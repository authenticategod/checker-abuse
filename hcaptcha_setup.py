#!/usr/bin/env python3
import sys
import time
import json
import random
import string
import re
import logging

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("hcaptcha_setup")

from hcaptcha_bypass import (
    extract_cookie_from_link,
    _load_cookie,
    _save_cookie,
)

MAIL_TM_BASE = "https://api.mail.tm"


def rand_str(n=10):
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=n))


def create_temp_inbox():
    import requests as req
    log.debug(f"GET -> {MAIL_TM_BASE}/domains")
    r = req.get(f"{MAIL_TM_BASE}/domains", timeout=10)
    domains = r.json().get("hydra:member", [])
    if not domains:
        return None, None, None, "No temp mail domains available"
    domain = domains[0]["domain"]
    log.debug(f"Available domain: {domain}")

    username = f"hcap{rand_str(8)}"
    email = f"{username}@{domain}"
    password = rand_str(16)

    log.debug(f"POST -> {MAIL_TM_BASE}/accounts  (creating {email})")
    acc = req.post(f"{MAIL_TM_BASE}/accounts", json={"address": email, "password": password}, timeout=10)
    if acc.status_code not in (200, 201):
        return None, None, None, f"Failed to create inbox: {acc.status_code} {acc.text[:100]}"
    log.info(f"Temp inbox created: {email}")

    log.debug(f"POST -> {MAIL_TM_BASE}/token  (getting JWT)")
    tok = req.post(f"{MAIL_TM_BASE}/token", json={"address": email, "password": password}, timeout=10)
    if tok.status_code != 200:
        return None, None, None, f"Failed to get token: {tok.status_code}"
    jwt = tok.json().get("token", "")
    log.debug(f"JWT obtained: {jwt[:20]}...")

    return email, password, jwt, None


def wait_for_hcaptcha_email(jwt, max_wait=120, poll_interval=5):
    import requests as req
    headers = {"Authorization": f"Bearer {jwt}"}
    start = time.time()
    attempt = 0

    while time.time() - start < max_wait:
        attempt += 1
        elapsed = int(time.time() - start)
        log.debug(f"Polling inbox... (attempt {attempt}, {elapsed}s elapsed)")

        r = req.get(f"{MAIL_TM_BASE}/messages", headers=headers, timeout=10)
        if r.status_code != 200:
            log.warning(f"Inbox poll failed: {r.status_code}")
            time.sleep(poll_interval)
            continue

        messages = r.json().get("hydra:member", [])
        if not messages:
            time.sleep(poll_interval)
            continue

        for msg_summary in messages:
            msg_id = msg_summary.get("id", "")
            subject = msg_summary.get("subject", "")
            sender = msg_summary.get("from", {}).get("address", "")
            log.info(f"Email received! From: {sender} | Subject: {subject}")

            log.debug(f"GET -> {MAIL_TM_BASE}/messages/{msg_id}")
            full = req.get(f"{MAIL_TM_BASE}/messages/{msg_id}", headers=headers, timeout=10)
            if full.status_code != 200:
                continue

            body = full.json()
            html_body = body.get("html", [""])[0] if body.get("html") else ""
            text_body = body.get("text", "")
            combined = html_body + " " + text_body

            links = re.findall(r'https://[^\s"<>\']+(?:accessibility|cookie|set-cookie|token|verify|confirm)[^\s"<>\']*', combined, re.IGNORECASE)
            if not links:
                links = re.findall(r'https://(?:accounts\.hcaptcha\.com|dashboard\.hcaptcha\.com)[^\s"<>\']+', combined)
            if not links:
                links = re.findall(r'href="(https://[^"]+)"', combined)

            if links:
                link = links[0].rstrip(".")
                log.info(f"Magic link found: {link[:80]}...")
                return link

            log.warning("Email found but no magic link detected in body")
            log.debug(f"Body preview: {combined[:300]}")

        time.sleep(poll_interval)

    return None


def signup_hcaptcha(email):
    from curl_cffi import requests as cffi
    sess = cffi.Session(impersonate="chrome")

    log.debug(f"POST -> https://accounts.hcaptcha.com/accessibility/signup")
    log.debug(f"Payload: email={email}")

    r = sess.post(
        "https://accounts.hcaptcha.com/accessibility/signup",
        json={"email": email, "language": "en"},
        headers={
            "Content-Type": "application/json",
            "Origin": "https://dashboard.hcaptcha.com",
            "Referer": "https://dashboard.hcaptcha.com/signup?type=accessibility",
        },
        timeout=15,
    )

    log.info(f"hCaptcha response: {r.status_code} | {r.text[:200]}")

    if r.status_code == 200:
        return True, "Signup successful"
    if r.status_code == 429:
        return False, "Rate limited — wait a few minutes and try again"
    if r.status_code == 401:
        try:
            msg = r.json().get("message", r.text[:200])
        except Exception:
            msg = r.text[:200]
        return False, f"Auth error: {msg}"
    return False, f"HTTP {r.status_code}: {r.text[:200]}"


def run_auto_flow():
    log.info("=" * 55)
    log.info("  FULLY AUTOMATED hCaptcha Accessibility Cookie Setup")
    log.info("=" * 55)
    log.info("")

    existing = _load_cookie()
    if existing:
        log.info(f"Existing cookie found: {existing[:25]}...")
        log.info("Run with 'force' argument to get a new one")
        if len(sys.argv) < 3 or sys.argv[2] != "force":
            return

    log.info("Step 1/4: Creating temporary email inbox...")
    email, password, jwt, err = create_temp_inbox()
    if err:
        log.error(f"Failed: {err}")
        return

    log.info(f"Step 2/4: Signing up on hCaptcha with {email}...")
    ok, msg = signup_hcaptcha(email)
    if not ok:
        log.error(f"Signup failed: {msg}")
        if "rate limit" in msg.lower() or "429" in msg:
            log.info("Waiting 60 seconds for rate limit to clear...")
            time.sleep(60)
            ok, msg = signup_hcaptcha(email)
            if not ok:
                log.error(f"Still failing: {msg}")
                return
        else:
            return

    log.info(f"Step 3/4: Waiting for magic link email (up to 2 minutes)...")
    magic_link = wait_for_hcaptcha_email(jwt, max_wait=120, poll_interval=5)

    if not magic_link:
        log.error("No magic link received within 2 minutes")
        log.info("This can happen if hCaptcha blocked the temp email domain")
        log.info("")
        log.info("Manual fallback:")
        log.info("  1. Go to https://dashboard.hcaptcha.com/signup?type=accessibility")
        log.info("  2. Sign up with any email")
        log.info("  3. Get the cookie from browser DevTools")
        log.info("  4. Run: python hcaptcha_setup.py set <cookie_value>")
        return

    log.info(f"Step 4/4: Extracting cookie from magic link...")
    log.debug(f"GET -> {magic_link[:80]}...")

    ok, result = extract_cookie_from_link(magic_link)
    if ok:
        log.info(f"Cookie extracted and saved: {result[:25]}...")
        log.info("")
        log.info("=" * 55)
        log.info("  DONE! hCaptcha bypass cookie is active.")
        log.info("  Shopify sites with hCaptcha will auto-solve now.")
        log.info("  Cookie expires in ~24 hours — re-run to refresh.")
        log.info("=" * 55)
    else:
        log.warning(f"Auto-extract failed: {result}")
        log.info("")
        log.info("Try opening the magic link in your browser:")
        log.info(f"  {magic_link[:100]}")
        log.info("Then copy hc_accessibility cookie and run:")
        log.info("  python hcaptcha_setup.py set <cookie_value>")


def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python hcaptcha_setup.py auto           — Fully automated (temp email)")
        print("  python hcaptcha_setup.py auto force      — Force refresh even if cookie exists")
        print("  python hcaptcha_setup.py <email>         — Manual flow with your email")
        print("  python hcaptcha_setup.py status           — Check if cookie is active")
        print("  python hcaptcha_setup.py set <value>      — Set cookie value directly")
        sys.exit(1)

    arg = sys.argv[1].strip()

    if arg == "status":
        cookie = _load_cookie()
        if cookie:
            log.info(f"Cookie is ACTIVE: {cookie[:30]}...")
        else:
            log.warning("No cookie saved or cookie expired.")
        return

    if arg == "set":
        if len(sys.argv) < 3:
            log.error("Usage: python hcaptcha_setup.py set <cookie_value>")
            sys.exit(1)
        val = sys.argv[2].strip()
        _save_cookie(val)
        log.info(f"Cookie saved: {val[:30]}...")
        return

    if arg == "auto":
        run_auto_flow()
        return

    email = arg
    if "@" not in email:
        log.error(f"Invalid email: {email}")
        sys.exit(1)

    from hcaptcha_bypass import SSO_DOMAINS
    domain = email.split("@")[-1].lower()
    sso_provider = SSO_DOMAINS.get(domain)

    log.info(f"Email: {email}")
    log.info(f"Domain: {domain}")

    if sso_provider:
        log.warning(f"{domain} requires {sso_provider} SSO — cannot automate")
        log.info("")
        log.info("Two options:")
        log.info("")
        log.info("  OPTION A (recommended): Run fully automated with temp email:")
        log.info("    python hcaptcha_setup.py auto")
        log.info("")
        log.info("  OPTION B: Do it manually in browser:")
        log.info(f"    1. Open https://dashboard.hcaptcha.com/signup?type=accessibility")
        log.info(f"    2. Click 'Sign up with {sso_provider}'")
        log.info(f"    3. Log in with {email}")
        log.info(f"    4. DevTools (F12) -> Application -> Cookies -> hcaptcha.com")
        log.info(f"    5. Copy 'hc_accessibility' cookie value")
        log.info(f"    6. Run: python hcaptcha_setup.py set <cookie_value>")
        return

    log.info(f"Step 1: Requesting accessibility signup for {email}")
    from hcaptcha_bypass import request_accessibility_link
    ok, msg = request_accessibility_link(email)

    if "SSO_REQUIRED" in str(msg):
        log.warning("SSO required — use 'auto' mode instead:")
        log.info("  python hcaptcha_setup.py auto")
        return

    if ok:
        log.info(f"Response: {msg}")
    else:
        log.warning(f"Response: {msg}")

    print()
    log.info("Step 2: Check your email for the hCaptcha magic link")
    print()
    magic_link = input("Paste the magic link URL here: ").strip()

    if not magic_link:
        cookie_val = input("Or paste cookie value directly (Enter to quit): ").strip()
        if cookie_val:
            _save_cookie(cookie_val)
            log.info(f"Cookie saved: {cookie_val[:30]}...")
        else:
            log.warning("Aborted.")
        return

    log.info(f"Step 3: Extracting cookie from magic link...")
    ok, result = extract_cookie_from_link(magic_link)
    if ok:
        log.info(f"Cookie extracted and saved: {result[:30]}...")
        log.info("Done! Shopify hCaptcha will auto-solve.")
    else:
        log.warning(f"Auto-extract failed: {result}")
        cookie_val = input("Paste cookie value manually: ").strip()
        if cookie_val:
            _save_cookie(cookie_val)
            log.info(f"Cookie saved: {cookie_val[:30]}...")


if __name__ == "__main__":
    main()
