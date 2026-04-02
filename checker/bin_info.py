"""
Shared BIN info utility for card checkers.
Formats BIN/card metadata into a clean display block.
Uses CKO (Checkout.com) tokenization as primary BIN lookup,
then Braintree tokenization for richer prepaid/debit/commercial flags,
then free APIs (binlist.net, handyapi.com) as final fallback.
"""

import asyncio
import aiohttp
import json
import re
import base64
import random
import string
import time

CKO_PUBLIC_KEY = "pk_saijocqn2lu52prqeubhmwyhye5"
CKO_TOKENIZE_URL = "https://api.checkout.com/tokens"

BRAINTREE_GRAPHQL_URL = "https://payments.braintree-api.com/graphql"
BRAINTREE_DONATION_URLS = [
    "https://secure.aspca.org/donate/donate",
    "https://act.earthjustice.org/a/donate",
    "https://give.salvationarmy.org/give/164006/",
]
BRAINTREE_TOKEN_TTL = 1800

_braintree_auth_cache: dict = {
    "fingerprint": None,
    "expires_at": 0,
}


def _luhn_checkdigit(partial: str) -> str:
    digits = [int(d) for d in partial]
    total = 0
    for i, d in enumerate(digits):
        if i % 2 == 0:
            d2 = d * 2
            total += d2 - 9 if d2 > 9 else d2
        else:
            total += d
    return str((10 - (total % 10)) % 10)


def _generate_card_from_bin(bin6: str) -> str:
    padded = bin6.ljust(15, "0")
    return padded + _luhn_checkdigit(padded)


def format_bin_block(
    brand: str = None,
    funding: str = None,
    country: str = None,
    three_ds: str = None,
    checks: str = None,
    bank: str = None,
    extra_lines: list = None,
) -> str:
    def _val(v, fallback="?"):
        if v and str(v).strip().lower() not in ("", "unknown", "none", "null"):
            return str(v).strip()
        return fallback

    def _line(label, v, fallback="?"):
        val = _val(v, fallback)
        mark = "\u2713" if val != "?" and val != "unknown" else ""
        return f"{label} {val} {mark}".rstrip()

    lines = []
    lines.append(_line("Brand:  ", brand))
    lines.append(_line("Funding:", funding))
    lines.append(_line("Country:", country))
    lines.append(_line("3DS:    ", three_ds))
    if bank:
        lines.append(_line("Bank:   ", bank))
    lines.append(f"Checks:  {_val(checks, 'all None')}")
    if extra_lines:
        lines.extend(extra_lines)

    width = max(len(l) for l in lines) + 4
    border = "\u2500" * width
    result = f"\u250c{border}\u2510\n"
    for l in lines:
        result += f"\u2502 {l.ljust(width - 1)}\u2502\n"
    result += f"\u2514{border}\u2518"
    return result


def bin_block_from_cko(cko_token_data: dict, country_fallback: str = None) -> str:
    scheme = cko_token_data.get("scheme", "")
    card_type = cko_token_data.get("card_type", "")
    card_category = cko_token_data.get("card_category", "")
    issuer_country = cko_token_data.get("issuer_country", "") or country_fallback or ""
    issuer = cko_token_data.get("issuer", "")
    product_type = cko_token_data.get("product_type", "")

    funding = card_type or card_category or "?"
    three_ds = product_type if product_type else "unknown"

    return format_bin_block(
        brand=scheme,
        funding=funding,
        country=issuer_country,
        three_ds=three_ds,
        checks="all None",
        bank=issuer if issuer else None,
    )


async def bin_block_from_braintree(credit_card: dict, bin_data: dict, cc: str = "", session: aiohttp.ClientSession = None) -> str:
    brand = credit_card.get("brandCode", "")

    prepaid = (bin_data.get("prepaid") or "").lower()
    debit = (bin_data.get("debit") or "").lower()
    commercial = (bin_data.get("commercial") or "").lower()

    if prepaid == "yes":
        funding = "prepaid"
    elif debit == "yes":
        funding = "debit"
    elif commercial == "yes":
        funding = "commercial"
    else:
        funding = "credit"

    country = bin_data.get("countryOfIssuance", "") or ""
    bank = bin_data.get("issuingBank", "") or ""

    checks_parts = []
    for field in ("prepaid", "healthcare", "debit", "durbinRegulated", "commercial", "payroll"):
        v = bin_data.get(field, "")
        if v and v.lower() not in ("unknown", ""):
            checks_parts.append(f"{field}={v}")
    checks_str = ", ".join(checks_parts) if checks_parts else "all None"

    product = bin_data.get("productId", "") or ""

    needs_fallback = not country or not bank or (country.upper() == "UNKNOWN") or (bank.upper() == "UNKNOWN")
    fallback = {}
    if needs_fallback and cc:
        try:
            fallback = await fetch_bin_info(cc[:6], session=session)
        except Exception:
            pass

    final_brand = brand or fallback.get("scheme") or fallback.get("brand") or "?"
    final_funding = funding
    final_country = country if country and country.upper() != "UNKNOWN" else (fallback.get("country_alpha2") or "?")
    final_bank = bank if bank and bank.upper() != "UNKNOWN" else (fallback.get("bank_name") or None)
    final_3ds = product if product else (fallback.get("product_type") or "unknown")

    return format_bin_block(
        brand=final_brand,
        funding=final_funding,
        country=final_country,
        three_ds=final_3ds,
        checks=checks_str,
        bank=final_bank,
    )


def _extract_fingerprint_from_html(page_html: str) -> str | None:
    """
    Extract a Braintree authorization fingerprint from page HTML.
    Tries named clientToken patterns first, then scans all base64 strings
    for a decoded Braintree token containing authorizationFingerprint.
    """
    token_match = (
        re.search(r'clientToken["\']?\s*[:=]\s*["\']([A-Za-z0-9+/=]{80,})["\']', page_html) or
        re.search(r'client_token["\']?\s*[:=]\s*["\']([A-Za-z0-9+/=]{80,})["\']', page_html) or
        re.search(r'data-braintree[^>]*token["\']?\s*[:=]\s*["\']([^"\']+)["\']', page_html)
    )

    candidates = []
    if token_match:
        candidates.append(token_match.group(1))

    for b64_match in re.finditer(r'["\']([A-Za-z0-9+/]{150,}={0,3})["\']', page_html):
        candidates.append(b64_match.group(1))

    for raw in candidates:
        try:
            padding = 4 - len(raw) % 4
            if padding != 4:
                raw += "=" * padding
            decoded = base64.b64decode(raw).decode("utf-8", errors="ignore")
            auth_match = re.search(r'"authorizationFingerprint":"([^"]+)"', decoded)
            if auth_match:
                return auth_match.group(1)
        except Exception:
            continue

    return None


async def _fetch_braintree_authorization(session: aiohttp.ClientSession) -> str | None:
    """
    Scrape a Braintree authorization fingerprint from donation pages.
    Tries multiple sites in order. Caches the result for BRAINTREE_TOKEN_TTL seconds.
    """
    now = time.monotonic()
    cached = _braintree_auth_cache
    if cached["fingerprint"] and now < cached["expires_at"]:
        return cached["fingerprint"]

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }

    for donation_url in BRAINTREE_DONATION_URLS:
        try:
            async with session.get(donation_url, headers=headers,
                                   timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    continue
                page_html = await resp.text()

            fingerprint = _extract_fingerprint_from_html(page_html)
            if fingerprint:
                cached["fingerprint"] = fingerprint
                cached["expires_at"] = now + BRAINTREE_TOKEN_TTL
                return fingerprint
        except Exception:
            continue

    return None


async def fetch_bin_info_braintree(bin_number: str, session: aiohttp.ClientSession = None) -> dict:
    """
    Fetch BIN info by tokenizing a dummy card through Braintree's GraphQL API.
    Returns rich metadata including prepaid, debit, commercial, healthcare, payroll,
    durbinRegulated, issuingBank, countryOfIssuance, productId flags.
    Caches the authorization fingerprint for BRAINTREE_TOKEN_TTL seconds.
    """
    bin6 = str(bin_number).strip()[:6]
    if not bin6.isdigit() or len(bin6) < 6:
        return {}

    cc = _generate_card_from_bin(bin6)

    close_session = False
    if session is None:
        session = aiohttp.ClientSession()
        close_session = True

    try:
        authorization = await _fetch_braintree_authorization(session)
        if not authorization:
            return {}

        session_id = "".join(random.choices(string.ascii_letters + string.digits, k=36))
        headers = {
            "authorization": f"Bearer {authorization}",
            "braintree-version": "2018-05-10",
            "content-type": "application/json",
            "origin": "https://assets.braintreegateway.com",
            "referer": "https://assets.braintreegateway.com/",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }
        json_data = {
            "clientSdkMetadata": {
                "source": "client",
                "integration": "custom",
                "sessionId": session_id,
            },
            "query": (
                "mutation TokenizeCreditCard($input: TokenizeCreditCardInput!) {"
                " tokenizeCreditCard(input: $input) {"
                " token creditCard { bin brandCode last4"
                " binData { prepaid healthcare debit durbinRegulated commercial"
                " payroll issuingBank countryOfIssuance productId } } } }"
            ),
            "variables": {
                "input": {
                    "creditCard": {
                        "number": cc,
                        "expirationMonth": "12",
                        "expirationYear": "2028",
                        "cvv": "123",
                    },
                    "options": {"validate": False},
                }
            },
            "operationName": "TokenizeCreditCard",
        }

        async with session.post(
            BRAINTREE_GRAPHQL_URL,
            headers=headers,
            json=json_data,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                return {}
            data = await resp.json(content_type=None)

        tc = (data.get("data") or {}).get("tokenizeCreditCard")
        if not tc:
            return {}

        credit_card = tc.get("creditCard") or {}
        bin_data = credit_card.get("binData") or {}
        brand_code = credit_card.get("brandCode", "")

        def _yn(val):
            v = (val or "").strip().lower()
            if v in ("yes", "no"):
                return v.capitalize()
            return ""

        prepaid = _yn(bin_data.get("prepaid"))
        debit = _yn(bin_data.get("debit"))
        commercial = _yn(bin_data.get("commercial"))
        healthcare = _yn(bin_data.get("healthcare"))
        payroll = _yn(bin_data.get("payroll"))
        durbin = _yn(bin_data.get("durbinRegulated"))
        issuing_bank = (bin_data.get("issuingBank") or "").strip()
        country = (bin_data.get("countryOfIssuance") or "").strip()
        product_id = (bin_data.get("productId") or "").strip()

        if issuing_bank.upper() == "UNKNOWN":
            issuing_bank = ""
        if country.upper() == "UNKNOWN":
            country = ""

        if prepaid.lower() == "yes":
            funding = "prepaid"
        elif debit.lower() == "yes":
            funding = "debit"
        elif commercial.lower() == "yes":
            funding = "commercial"
        else:
            funding = "credit"

        result = {
            "scheme": brand_code,
            "brand": brand_code,
            "type": funding,
            "country_alpha2": country,
            "bank_name": issuing_bank,
            "product_type": product_id,
            "prepaid": prepaid,
            "debit": debit,
            "commercial": commercial,
            "healthcare": healthcare,
            "payroll": payroll,
            "durbin_regulated": durbin,
            "_source": "braintree",
        }
        return result

    except Exception:
        return {}
    finally:
        if close_session:
            await session.close()


async def fetch_bin_info_cko(bin_number: str, session: aiohttp.ClientSession = None) -> dict:
    """
    Fetch BIN info by tokenizing a dummy card through CKO (Checkout.com).
    Returns rich metadata: scheme, card_type, card_category, issuer_country, issuer, product_type.
    """
    bin6 = str(bin_number).strip()[:6]
    if not bin6.isdigit() or len(bin6) < 6:
        return {}

    cc = _generate_card_from_bin(bin6)

    close_session = False
    if session is None:
        session = aiohttp.ClientSession()
        close_session = True

    try:
        headers = {
            "Authorization": CKO_PUBLIC_KEY,
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }
        body = {"type": "card", "number": cc, "expiry_month": 12, "expiry_year": 2028}
        async with session.post(CKO_TOKENIZE_URL, headers=headers, json=body,
                                timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 201:
                data = await resp.json()
                return {
                    "scheme": data.get("scheme", ""),
                    "type": data.get("card_type", ""),
                    "card_category": data.get("card_category", ""),
                    "country_alpha2": data.get("issuer_country", ""),
                    "bank_name": data.get("issuer", ""),
                    "product_type": data.get("product_type", ""),
                    "product_id": data.get("product_id", ""),
                    "brand": data.get("scheme", ""),
                }
            elif resp.status == 422:
                text = await resp.text()
                try:
                    err = json.loads(text)
                    codes = err.get("error_codes", [])
                    if "card_number_invalid" in codes:
                        return {"_error": "invalid_bin"}
                except Exception:
                    pass
    except Exception:
        pass
    finally:
        if close_session:
            await session.close()

    return {}


def _is_useful(result: dict) -> bool:
    """Return True if result has at least one meaningful non-error value."""
    if not result or result.get("_error"):
        return False
    return any(v for k, v in result.items() if not k.startswith("_") and v)


def _merge_results(base: dict, supplement: dict) -> dict:
    """
    Merge supplement fields into base where base fields are missing/empty.
    Supplement fields are only used to fill gaps.
    """
    merged = dict(base)
    for k, v in supplement.items():
        if k.startswith("_"):
            continue
        if not merged.get(k) and v:
            merged[k] = v
    return merged


async def fetch_bin_info(bin_number: str, session: aiohttp.ClientSession = None) -> dict:
    """
    Fetch BIN info using a priority chain:
      1. CKO tokenization (primary — scheme, type, country, bank, product_type)
      2. Braintree tokenization (supplement — prepaid/debit/commercial flags + gap-fill)
      3. binlist.net (free API fallback)
      4. handyapi.com (free API last resort)
    CKO result is enriched with Braintree flags when available.
    """
    bin6 = str(bin_number).strip()[:6]
    if not bin6.isdigit() or len(bin6) < 6:
        return {}

    close_session = False
    if session is None:
        session = aiohttp.ClientSession()
        close_session = True

    try:
        cko_result = await fetch_bin_info_cko(bin6, session=session)
        bt_result = await fetch_bin_info_braintree(bin6, session=session)

        if cko_result and cko_result.get("_error") == "invalid_bin":
            if _is_useful(bt_result):
                return bt_result
            return cko_result

        if _is_useful(cko_result):
            if _is_useful(bt_result):
                merged = _merge_results(cko_result, bt_result)
                for flag_key in ("prepaid", "debit", "commercial", "healthcare", "payroll", "durbin_regulated"):
                    if bt_result.get(flag_key):
                        merged[flag_key] = bt_result[flag_key]
                merged["_source"] = "cko+braintree"
                return merged
            cko_result.setdefault("_source", "cko")
            return cko_result

        if _is_useful(bt_result):
            return bt_result

        try:
            url = f"https://lookup.binlist.net/{bin6}"
            headers = {"Accept-Version": "3"}
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    fallback = {
                        "scheme": data.get("scheme", ""),
                        "type": data.get("type", ""),
                        "brand": data.get("brand", ""),
                        "country_alpha2": (data.get("country") or {}).get("alpha2", ""),
                        "bank_name": (data.get("bank") or {}).get("name", ""),
                    }
                    if any(fallback.values()):
                        return fallback
        except Exception:
            pass

        try:
            url2 = f"https://data.handyapi.com/bin/{bin6}"
            async with session.get(url2, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    scheme = data.get("Scheme", "")
                    if scheme and scheme.upper() not in ("", "UNKNOWN"):
                        issuer = data.get("Issuer", "")
                        if issuer and issuer.upper() == "UNKNOWN":
                            issuer = ""
                        fallback = {
                            "scheme": scheme,
                            "type": data.get("Type", ""),
                            "brand": scheme,
                            "country_alpha2": data.get("Country", {}).get("A2", "") if isinstance(data.get("Country"), dict) else "",
                            "bank_name": issuer,
                        }
                        if any(fallback.values()):
                            return fallback
        except Exception:
            pass

    finally:
        if close_session:
            await session.close()

    return {}


async def bin_block_from_cko_with_fallback(
    cko_token_data: dict,
    session: aiohttp.ClientSession = None,
) -> str:
    scheme = cko_token_data.get("scheme", "")
    card_type = cko_token_data.get("card_type", "")
    card_category = cko_token_data.get("card_category", "")
    issuer_country = cko_token_data.get("issuer_country", "")
    issuer = cko_token_data.get("issuer", "")
    product_type = cko_token_data.get("product_type", "")
    bin_val = cko_token_data.get("bin", "")

    needs_fallback = not issuer_country or not scheme or not card_type

    fallback = {}
    if needs_fallback and bin_val:
        fallback = await fetch_bin_info(bin_val, session=session)

    final_brand = scheme or fallback.get("scheme") or fallback.get("brand") or "?"
    final_funding = card_type or card_category or fallback.get("type") or "?"
    final_country = issuer_country or fallback.get("country_alpha2") or "?"
    final_bank = issuer or fallback.get("bank_name") or None
    final_3ds = product_type if product_type else (fallback.get("product_type") or "unknown")

    return format_bin_block(
        brand=final_brand,
        funding=final_funding,
        country=final_country,
        three_ds=final_3ds,
        checks="all None",
        bank=final_bank,
    )


async def lookup_and_display(bin_number: str) -> None:
    print(f"Looking up BIN: {bin_number[:6]}...\n")
    info = await fetch_bin_info(bin_number)
    if not info or info.get("_error") or not any(v for k, v in info.items() if not k.startswith("_") and v):
        print(f"No data found for BIN {bin_number[:6]}")
        return

    brand = info.get("scheme") or info.get("brand") or "?"
    funding = info.get("type") or "?"
    country = info.get("country_alpha2") or "?"
    bank = info.get("bank_name") or None
    product_type = info.get("product_type") or ""
    card_category = info.get("card_category") or ""

    three_ds = product_type if product_type else "unknown"

    extra = []
    if card_category:
        extra.append(f"Class:   {card_category}")

    flag_fields = [
        ("prepaid", "Prepaid"),
        ("debit", "Debit"),
        ("commercial", "Commercial"),
        ("healthcare", "Healthcare"),
        ("payroll", "Payroll"),
        ("durbin_regulated", "DurbinReg"),
    ]
    flag_parts = []
    for key, label in flag_fields:
        val = info.get(key, "")
        if val:
            flag_parts.append(f"{label}={val}")
    if flag_parts:
        extra.append(f"Flags:   {', '.join(flag_parts)}")

    source = info.get("_source", "")
    if source:
        extra.append(f"Source:  {source}")

    block = format_bin_block(
        brand=brand,
        funding=funding,
        country=country,
        three_ds=three_ds,
        checks="all None",
        bank=bank,
        extra_lines=extra if extra else None,
    )
    print(block)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python bin_info.py <BIN> [BIN2] [BIN3] ...")
        print("Example: python bin_info.py 430576")
        sys.exit(1)

    async def _main():
        for b in sys.argv[1:]:
            await lookup_and_display(b.strip())
            if b != sys.argv[-1]:
                print()

    asyncio.run(_main())
