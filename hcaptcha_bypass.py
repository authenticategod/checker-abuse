import os
import re
import json
import time
import random
import string
import hashlib
import asyncio
import subprocess
import aiohttp
from curl_cffi import requests as cffi_requests

COOKIE_FILE = os.path.join(os.path.dirname(__file__), "hcaptcha_cookie.json")
HSW_SOLVER_PATH = os.path.join(os.path.dirname(__file__), "hsw_solver.js")

CHROME_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

SCREEN_DATA = {
    "width": 1920, "height": 1080,
    "availWidth": 1920, "availHeight": 1040,
    "colorDepth": 24, "pixelDepth": 24,
}

_hcaptcha_version_cache = {"version": None, "fetched": 0}


def _get_hcaptcha_version():
    cached = _hcaptcha_version_cache
    if cached["version"] and time.time() - cached["fetched"] < 3600:
        return cached["version"]
    try:
        sess = cffi_requests.Session(impersonate="chrome")
        resp = sess.get("https://hcaptcha.com/1/api.js", headers={"User-Agent": CHROME_UA}, timeout=10)
        match = re.search(r'v1/([a-f0-9]{7,10})/', resp.text)
        if not match:
            match = re.search(r'/([a-f0-9]{7,10})/', resp.text)
        if match:
            ver = match.group(1)
            _hcaptcha_version_cache["version"] = ver
            _hcaptcha_version_cache["fetched"] = time.time()
            return ver
    except Exception:
        pass
    return cached["version"] or "ac9b6e6"


def _rand_str(n=16):
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=n))


def _load_cookie():
    if os.path.exists(COOKIE_FILE):
        try:
            with open(COOKIE_FILE, 'r') as f:
                data = json.load(f)
            ts = data.get("timestamp", 0)
            if time.time() - ts < 82800:
                return data.get("cookie")
        except Exception:
            pass
    return None


def _save_cookie(cookie_value):
    with open(COOKIE_FILE, 'w') as f:
        json.dump({"cookie": cookie_value, "timestamp": time.time()}, f)


SSO_DOMAINS = {
    "gmail.com": "Google",
    "googlemail.com": "Google",
    "outlook.com": "Microsoft",
    "hotmail.com": "Microsoft",
    "live.com": "Microsoft",
    "msn.com": "Microsoft",
}


def request_accessibility_link(email):
    domain = email.split("@")[-1].lower()
    sso_provider = SSO_DOMAINS.get(domain)

    if sso_provider:
        return False, (
            f"SSO_REQUIRED:{sso_provider}|"
            f"{email} uses {sso_provider} SSO — hCaptcha requires OAuth login for this domain.\n"
            f"Sign up in browser:\n"
            f"1. Go to https://dashboard.hcaptcha.com/signup?type=accessibility\n"
            f"2. Click 'Sign up with {sso_provider}'\n"
            f"3. After login, open DevTools → Application → Cookies → hcaptcha.com\n"
            f"4. Copy 'hc_accessibility' cookie value"
        )

    sess = cffi_requests.Session(impersonate="chrome")

    resp = sess.post(
        "https://accounts.hcaptcha.com/accessibility/signup",
        json={"email": email, "language": "en"},
        headers={
            "User-Agent": CHROME_UA,
            "Content-Type": "application/json",
            "Origin": "https://dashboard.hcaptcha.com",
            "Referer": "https://dashboard.hcaptcha.com/signup?type=accessibility",
        },
    )

    if resp.status_code == 200:
        data = {}
        try:
            data = resp.json()
        except Exception:
            pass
        if data.get("success") or data.get("status") == "ok" or "pass" in resp.text.lower():
            return True, "Magic link sent — check your email (and spam folder)"
        return True, f"Signup submitted ({resp.status_code}) — check your email"

    if resp.status_code == 429:
        return False, "Rate limited by hCaptcha — wait a few minutes and try again"

    if resp.status_code == 401:
        try:
            msg = resp.json().get("message", "")
        except Exception:
            msg = resp.text[:200]
        if "SSO" in msg:
            provider = "Google" if "Google" in msg else "Microsoft" if "Microsoft" in msg else "SSO"
            return False, (
                f"SSO_REQUIRED:{provider}|"
                f"hCaptcha requires {provider} SSO for this email.\n"
                f"Sign up in browser at https://dashboard.hcaptcha.com/signup?type=accessibility"
            )
        return False, f"Auth error: {msg}"

    return False, f"Unexpected response ({resp.status_code}): {resp.text[:200]}"


def extract_cookie_from_link(magic_link):
    sess = cffi_requests.Session(impersonate="chrome")
    resp = sess.get(
        magic_link,
        headers={
            "User-Agent": CHROME_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
        allow_redirects=True,
    )

    cookie_val = None
    for cookie in sess.cookies:
        if cookie.name == "hc_accessibility":
            cookie_val = cookie.value
            break

    if not cookie_val:
        match = re.search(r'hc_accessibility["\s:=]+([a-zA-Z0-9_/+=\-]+)', resp.text)
        if match:
            cookie_val = match.group(1)

    if not cookie_val:
        set_cookie_header = resp.headers.get("set-cookie", "")
        match = re.search(r'hc_accessibility=([^;]+)', set_cookie_header)
        if match:
            cookie_val = match.group(1)

    if cookie_val:
        _save_cookie(cookie_val)
        return True, cookie_val
    return False, f"Cookie not found in response. Status: {resp.status_code}, URL: {resp.url}"


def get_saved_cookie():
    return _load_cookie()


def _build_motion_data(start_time=None):
    if not start_time:
        start_time = time.time()

    st = int(start_time * 1000)
    mm = []
    md = []
    mu = []
    km = []

    x, y = random.randint(100, 400), random.randint(100, 400)
    t = st + random.randint(200, 500)

    steps = random.randint(15, 40)
    for i in range(steps):
        dx = random.randint(-8, 8)
        dy = random.randint(-8, 8)
        x = max(0, min(1920, x + dx))
        y = max(0, min(1080, y + dy))
        dt = random.randint(5, 30)
        t += dt
        mm.append([x, y, t])

    md.append([x, y, t + random.randint(10, 50)])
    mu.append([x, y, t + random.randint(80, 200)])

    return {
        "st": st,
        "dct": st,
        "mm": mm,
        "md": md,
        "mu": mu,
        "km": km,
        "kd": [],
        "ku": [],
        "topLevel": {
            "st": st - random.randint(1000, 3000),
            "sc": {"availWidth": 1920, "availHeight": 1040, "width": 1920, "height": 1080,
                   "colorDepth": 24, "pixelDepth": 24, "availTop": 0, "availLeft": 0,
                   "isExtended": False},
            "nv": {
                "vendorSub": "",
                "productSub": "20030107",
                "vendor": "Google Inc.",
                "maxTouchPoints": 0,
                "scheduling": {},
                "userActivation": {},
                "doNotTrack": None,
                "geolocation": {},
                "connection": {},
                "pdfViewerEnabled": True,
                "webkitTemporaryStorage": {},
                "hardwareConcurrency": 8,
                "cookieEnabled": True,
                "appCodeName": "Mozilla",
                "appName": "Netscape",
                "appVersion": "5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "platform": "Win32",
                "product": "Gecko",
                "userAgent": CHROME_UA,
                "language": "en-US",
                "languages": ["en-US", "en"],
                "onLine": True,
                "webdriver": False,
                "deviceMemory": 8,
            },
            "dr": "",
            "inv": False,
            "exec": False,
        },
        "v": 1,
    }


def _build_widget_id():
    return _rand_str(10) + _rand_str(10)


def _solve_text_puzzle(text):
    import re
    text = text.strip().rstrip(".")

    m = re.search(r"[Ee]rase (?:each|every|all) (\w) in (\w+)", text)
    if not m:
        m = re.search(r"[Dd]elete (?:each|every occurrence of|every|all) (\w) in (\w+)", text)
    if not m:
        m = re.search(r"[Rr]emove (?:each|every occurrence of|every|all) (\w) in (\w+)", text)
    if m:
        char, num = m.group(1), m.group(2)
        return num.replace(char, "")

    m = re.search(r"[Rr]eplace (?:the )?last character with (\w+).*?(?:ending[^)]*?is|ends with|final[^)]*?is) (\w+) in (\w+)", text)
    if not m:
        m = re.search(r"(?:ending[^)]*?is|ends with|final[^)]*?is) (\w+),?\s*(?:change|replace)[^)]*?(?:last character|final \w+)[^)]*?(?:to|with) (\w+) in (\w+)", text)
        if m:
            cond, repl, num = m.group(1), m.group(2), m.group(3)
            if num.endswith(cond):
                return num[:-len(cond)] + repl
            return num
    if m:
        repl, cond, num = m.group(1), m.group(2), m.group(3)
        if num.endswith(cond):
            return num[:-len(cond)] + repl
        return num

    m = re.search(r"(?:If|When|Only if)[^)]*?(?:end|final|last)[^)]*?(\w),?\s*(?:change|replace)[^)]*?(?:last|final)[^)]*?(?:to|with) (\w+) in (\w+)", text, re.IGNORECASE)
    if m:
        cond, repl, num = m.group(1), m.group(2), m.group(3)
        if num.endswith(cond):
            return num[:-1] + repl
        return num

    m = re.search(r"[Cc]hange[^)]*?(?:last|final)[^)]*?(?:to|with) (\w+) in (\w+)", text)
    if m:
        repl, num = m.group(1), m.group(2)
        return num[:-1] + repl

    m = re.search(r"[Rr]eplace[^)]*?last[^)]*?(?:to|with) (\w+) in (\w+)", text)
    if m:
        repl, num = m.group(1), m.group(2)
        return num[:-1] + repl

    return text.split()[-1] if text.split() else ""


def _solve_hsw(req_jwt):
    import tempfile
    tmp_in = None
    tmp_out = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", prefix="hsw_in_", delete=False) as f:
            json.dump({"req": req_jwt}, f)
            tmp_in = f.name
        tmp_out = tmp_in.replace("hsw_in_", "hsw_out_")
        result = subprocess.run(
            ["node", HSW_SOLVER_PATH, "full", tmp_in, tmp_out],
            capture_output=True, text=True, timeout=45,
        )
        if result.returncode == 0 and os.path.exists(tmp_out):
            with open(tmp_out) as f:
                data = json.load(f)
            if data.get("token"):
                return data["token"]
        if result.stderr:
            return ""
    except Exception:
        pass
    finally:
        for p in [tmp_in, tmp_out]:
            if p and os.path.exists(p):
                try:
                    os.unlink(p)
                except OSError:
                    pass
    try:
        result = subprocess.run(
            ["node", HSW_SOLVER_PATH, "solve", req_jwt],
            capture_output=True, text=True, timeout=45,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return ""


async def _solve_hsw_async(req_jwt):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _solve_hsw, req_jwt)


async def solve_hcaptcha(sitekey, host, accessibility_cookie=None, proxy=None):
    if not accessibility_cookie:
        accessibility_cookie = _load_cookie()
    if not accessibility_cookie:
        return None, "No accessibility cookie — run setup first"

    headers = {
        "User-Agent": CHROME_UA,
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://newassets.hcaptcha.com",
        "Referer": "https://newassets.hcaptcha.com/",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
        "Sec-Ch-Ua": '"Chromium";v="131", "Not_A Brand";v="24"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
    }

    cookies = {
        "hc_accessibility": accessibility_cookie,
    }

    version = _get_hcaptcha_version()

    try:
        async with aiohttp.ClientSession() as session:
            config_url = (
                f"https://api2.hcaptcha.com/checksiteconfig"
                f"?v={version}&host={host}&sitekey={sitekey}&sc=1&swa=1"
            )
            async with session.get(config_url, headers=headers, cookies=cookies, proxy=proxy) as resp:
                config_data = await resp.json(content_type=None)

            c_data = config_data.get("c", {})
            c_req = c_data.get("req", "")

            pass_status = config_data.get("pass")
            if pass_status is True:
                uuid = config_data.get("generated_pass_UUID")
                if uuid:
                    return uuid, None

            hsw_proof = await _solve_hsw_async(c_req) if c_req else ""

            motion = _build_motion_data()

            getcaptcha_data = {
                "v": version,
                "sitekey": sitekey,
                "host": host,
                "hl": "en",
                "motionData": json.dumps(motion),
                "n": hsw_proof,
                "c": json.dumps(c_data),
            }

            getcaptcha_url = f"https://api2.hcaptcha.com/getcaptcha/{sitekey}"
            async with session.post(
                getcaptcha_url,
                data=getcaptcha_data,
                headers=headers,
                cookies=cookies,
                proxy=proxy,
            ) as resp:
                captcha_data = await resp.json(content_type=None)

            if captcha_data.get("pass"):
                uuid = captcha_data.get("generated_pass_UUID", "")
                if uuid:
                    return uuid, None

            captcha_key = captcha_data.get("key")
            tasklist = captcha_data.get("tasklist", [])
            request_type = captcha_data.get("request_type", "")

            if not captcha_key:
                err_codes = captcha_data.get("error-codes", [])
                error_msg = captcha_data.get("error", captcha_data.get("message", ""))
                if not error_msg and err_codes:
                    error_msg = ", ".join(str(e) for e in err_codes)
                return None, f"getcaptcha failed: {error_msg or 'Unknown'}"

            if not tasklist:
                uuid = captcha_data.get("generated_pass_UUID", "")
                if uuid:
                    return uuid, None
                return None, "getcaptcha returned no tasks and no token"

            answers = {}
            for task in tasklist:
                task_key = task.get("task_key", "")
                if request_type == "text_free_entry":
                    puzzle_text = task.get("datapoint_text", {}).get("en", "")
                    answers[task_key] = _solve_text_puzzle(puzzle_text)
                else:
                    answers[task_key] = "true"

            c2_data = captcha_data.get("c", c_data)
            c2_req = c2_data.get("req", "")
            hsw_proof2 = await _solve_hsw_async(c2_req) if c2_req else hsw_proof

            check_motion = _build_motion_data()

            checkcaptcha_data = {
                "v": version,
                "job_mode": request_type or "image_label_binary",
                "answers": json.dumps(answers),
                "serverdomain": host,
                "sitekey": sitekey,
                "motionData": json.dumps(check_motion),
                "n": hsw_proof2,
                "c": json.dumps(c2_data),
            }

            check_url = f"https://api2.hcaptcha.com/checkcaptcha/{sitekey}/{captcha_key}"
            async with session.post(
                check_url,
                data=checkcaptcha_data,
                headers=headers,
                cookies=cookies,
                proxy=proxy,
            ) as resp:
                check_data = await resp.json(content_type=None)

            if check_data.get("pass"):
                return check_data.get("generated_pass_UUID", ""), None

            error_text = check_data.get("error", check_data.get("message", ""))
            return None, f"checkcaptcha rejected: {error_text or json.dumps(check_data)[:200]}"

    except Exception as e:
        return None, f"solve_hcaptcha error: {str(e)}"


async def solve_hcaptcha_with_retry(sitekey, host, accessibility_cookie=None, proxy=None, max_retries=3):
    for attempt in range(max_retries):
        token, error = await solve_hcaptcha(sitekey, host, accessibility_cookie, proxy)
        if token:
            return token, None
        if "No accessibility cookie" in (error or ""):
            return None, error
        if attempt < max_retries - 1:
            await asyncio.sleep(random.uniform(1, 3))
    return None, error


def setup_interactive():
    print("=" * 60)
    print("  hCaptcha Accessibility Cookie Setup")
    print("=" * 60)

    cookie = _load_cookie()
    if cookie:
        print(f"\n[OK] Valid cookie found: {cookie[:20]}...")
        choice = input("\nRefresh cookie? (y/n): ").strip().lower()
        if choice != 'y':
            print("Using existing cookie.")
            return cookie

    print("\n--- Step 1: Enter your email ---")
    email = input("Email: ").strip()
    if not email or '@' not in email:
        print("Invalid email")
        return None

    print(f"\nSending magic link to {email}...")
    ok, msg = request_accessibility_link(email)
    print(f"Result: {msg}")

    if not ok:
        print("\nAlternative: go to https://accounts.hcaptcha.com/accessibility manually")
        print("Enter your email there and proceed to Step 2 below.\n")

    print("\n--- Step 2: Paste the magic link from your email ---")
    print("(Check inbox/spam for email from hCaptcha, copy the link)\n")
    magic_link = input("Magic link URL: ").strip()

    if not magic_link:
        print("\nNo link provided. You can also paste the cookie value directly.")
        cookie_val = input("Paste hc_accessibility cookie value (or press Enter to quit): ").strip()
        if cookie_val:
            _save_cookie(cookie_val)
            print(f"\n[OK] Cookie saved: {cookie_val[:20]}...")
            return cookie_val
        return None

    print(f"\nExtracting cookie from magic link...")
    ok, result = extract_cookie_from_link(magic_link)
    if ok:
        print(f"\n[OK] Cookie extracted and saved: {result[:20]}...")
        return result
    else:
        print(f"\n[WARN] Auto-extract failed: {result}")
        print("\nManual fallback:")
        print("1. Open the magic link in your browser")
        print("2. Open DevTools → Application → Cookies → hcaptcha.com")
        print("3. Copy the 'hc_accessibility' cookie value")
        cookie_val = input("\nPaste cookie value: ").strip()
        if cookie_val:
            _save_cookie(cookie_val)
            print(f"\n[OK] Cookie saved: {cookie_val[:20]}...")
            return cookie_val
        return None


async def test_solve(sitekey=None, host=None):
    if not sitekey:
        sitekey = "a5f74b19-9e45-40e0-b45d-47ff91b7a6c2"
    if not host:
        host = "accounts.hcaptcha.com"

    cookie = _load_cookie()
    if not cookie:
        print("No cookie found. Run setup first.")
        return

    print(f"Testing solve with cookie: {cookie[:20]}...")
    print(f"Sitekey: {sitekey}")
    print(f"Host: {host}")

    token, error = await solve_hcaptcha(sitekey, host, cookie)
    if token:
        print(f"\n[OK] Solved! Token: {token[:50]}...")
    else:
        print(f"\n[FAIL] {error}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        asyncio.run(test_solve())
    else:
        cookie = setup_interactive()
        if cookie:
            print("\n" + "=" * 60)
            print("  Cookie is ready! Testing solve...")
            print("=" * 60)
            asyncio.run(test_solve())
