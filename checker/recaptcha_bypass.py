import os
import asyncio
import aiohttp
import re
import base64

CAPSOLVER_API_KEY = os.environ.get("CAPSOLVER_API_KEY", "")
TWOCAPTCHA_API_KEY = os.environ.get("TWOCAPTCHA_API_KEY", "")


async def get_recaptcha_v3_token(
    sitekey: str,
    domain: str,
    action: str = "",
    session: aiohttp.ClientSession | None = None,
) -> str | None:
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    headers = {"User-Agent": ua}
    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=False, limit=5),
            timeout=aiohttp.ClientTimeout(total=20),
        )
    try:
        async with session.get(
            f"https://www.recaptcha.net/recaptcha/api.js?render={sitekey}",
            headers=headers,
        ) as resp:
            text = await resp.text()
        versions = re.findall(r"releases/([\w.-]+)/", text)
        if not versions:
            return None
        v = versions[0]

        co = base64.b64encode(f"https://{domain}:443".encode()).decode().rstrip("=")
        anchor_url = (
            f"https://www.recaptcha.net/recaptcha/api2/anchor"
            f"?ar=1&k={sitekey}&co={co}&hl=en&v={v}&size=invisible&cb=1"
        )
        async with session.get(anchor_url, headers=headers) as resp:
            anchor_html = await resp.text()
        anchor_match = re.findall(
            r'id="recaptcha-token"\s*value="([^"]+)"', anchor_html
        )
        if not anchor_match:
            return None

        reload_url = f"https://www.recaptcha.net/recaptcha/api2/reload?k={sitekey}"
        reload_data = {
            "v": v,
            "reason": "q",
            "k": sitekey,
            "c": anchor_match[0],
            "co": co,
            "hl": "en",
            "size": "invisible",
        }
        async with session.post(
            reload_url,
            data=reload_data,
            headers={**headers, "Referer": anchor_url},
        ) as resp:
            reload_text = await resp.text()

        token_match = re.findall(r'"rresp","([^"]+)"', reload_text)
        if token_match and token_match[0] != "null":
            return token_match[0]
        return None
    except Exception:
        return None
    finally:
        if own_session and session and not session.closed:
            await session.close()


async def solve_recaptcha_v2(
    sitekey: str,
    page_url: str,
    session: aiohttp.ClientSession | None = None,
) -> str | None:
    if CAPSOLVER_API_KEY:
        result = await _solve_v2_capsolver(sitekey, page_url, session)
        if result:
            return result
    if TWOCAPTCHA_API_KEY:
        result = await _solve_v2_twocaptcha(sitekey, page_url, session)
        if result:
            return result
    return None


def has_solver_key() -> bool:
    return bool(CAPSOLVER_API_KEY or TWOCAPTCHA_API_KEY)


async def _solve_v2_capsolver(
    sitekey: str,
    page_url: str,
    session: aiohttp.ClientSession | None = None,
) -> str | None:
    api_key = CAPSOLVER_API_KEY
    if not api_key:
        return None

    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=False, limit=5),
            timeout=aiohttp.ClientTimeout(total=120),
        )
    try:
        create_payload = {
            "clientKey": api_key,
            "task": {
                "type": "ReCaptchaV2TaskProxyLess",
                "websiteURL": page_url,
                "websiteKey": sitekey,
            },
        }
        async with session.post(
            "https://api.capsolver.com/createTask", json=create_payload
        ) as resp:
            data = await resp.json()
        if data.get("errorId", 1) != 0:
            return None
        task_id = data.get("taskId")
        if not task_id:
            return None

        for _ in range(60):
            await asyncio.sleep(3)
            async with session.post(
                "https://api.capsolver.com/getTaskResult",
                json={"clientKey": api_key, "taskId": task_id},
            ) as resp:
                result = await resp.json()
            status = result.get("status", "")
            if status == "ready":
                solution = result.get("solution", {})
                return solution.get("gRecaptchaResponse")
            if status == "failed":
                return None
        return None
    except Exception:
        return None
    finally:
        if own_session and session and not session.closed:
            await session.close()


async def _solve_v2_twocaptcha(
    sitekey: str,
    page_url: str,
    session: aiohttp.ClientSession | None = None,
) -> str | None:
    api_key = TWOCAPTCHA_API_KEY
    if not api_key:
        return None

    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=False, limit=5),
            timeout=aiohttp.ClientTimeout(total=120),
        )
    try:
        create_url = (
            f"https://2captcha.com/in.php"
            f"?key={api_key}&method=userrecaptcha"
            f"&googlekey={sitekey}&pageurl={page_url}&json=1"
        )
        async with session.get(create_url) as resp:
            data = await resp.json()
        if data.get("status") != 1:
            return None
        request_id = data.get("request")
        if not request_id:
            return None

        result_url = f"https://2captcha.com/res.php?key={api_key}&action=get&id={request_id}&json=1"
        for _ in range(60):
            await asyncio.sleep(5)
            async with session.get(result_url) as resp:
                result = await resp.json()
            if result.get("status") == 1:
                return result.get("request")
            if result.get("request") == "ERROR_CAPTCHA_UNSOLVABLE":
                return None
        return None
    except Exception:
        return None
    finally:
        if own_session and session and not session.closed:
            await session.close()
