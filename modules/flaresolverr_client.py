from __future__ import annotations

import re
import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import requests

from .proxy_config import normalize_proxy_url
from .utils import load_env


DEFAULT_FLARESOLVERR_URL = "http://127.0.0.1:8191/v1"


@dataclass
class FlareSolverrResult:
    ok: bool
    reason: str
    cookies: list[dict[str, Any]]
    user_agent: str
    url: str
    status_code: int | None = None


def _env_bool(env: dict[str, str], key: str, default: bool = False) -> bool:
    raw = str(env.get(key) or "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on", "y"}:
        return True
    if raw in {"0", "false", "no", "off", "disabled", "none", "n"}:
        return False
    return default


def flaresolverr_enabled(env: dict[str, str] | None = None) -> bool:
    values = env if env is not None else load_env(".env")
    return _env_bool(values, "FLARESOLVERR_ENABLED", False)


def flaresolverr_api_url(env: dict[str, str] | None = None) -> str:
    values = env if env is not None else load_env(".env")
    raw = str(values.get("FLARESOLVERR_URL") or DEFAULT_FLARESOLVERR_URL).strip()
    if not raw:
        raw = DEFAULT_FLARESOLVERR_URL
    if not raw.rstrip("/").endswith("/v1"):
        raw = raw.rstrip("/") + "/v1"
    return raw


def flaresolverr_timeout_seconds(env: dict[str, str] | None = None) -> int:
    values = env if env is not None else load_env(".env")
    try:
        value = int(str(values.get("FLARESOLVERR_TIMEOUT_SECONDS") or "").strip())
        return max(15, value)
    except Exception:
        return 120


def flaresolverr_wait_seconds(env: dict[str, str] | None = None) -> int:
    values = env if env is not None else load_env(".env")
    try:
        value = int(str(values.get("FLARESOLVERR_WAIT_SECONDS") or "").strip())
        return max(1, value)
    except Exception:
        return 8


def split_flaresolverr_proxy(proxy: str | None) -> tuple[dict[str, str] | None, bool]:
    if not proxy:
        return None, False
    parsed = urlparse(normalize_proxy_url(proxy))
    scheme = parsed.scheme.lower()
    if scheme == "socks5h":
        scheme = "socks5"
    if not parsed.hostname or not parsed.port:
        return None, False
    result = {"url": f"{scheme}://{parsed.hostname}:{parsed.port}"}
    has_auth = bool(parsed.username or parsed.password)
    if parsed.username:
        result["username"] = parsed.username
    if parsed.password:
        result["password"] = parsed.password
    return result, has_auth


def _flare_post(api_url: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    response = requests.post(
        api_url,
        headers={"Content-Type": "application/json"},
        json=payload,
        timeout=timeout,
    )
    response.raise_for_status()
    return dict(response.json())


def _looks_like_challenge_html(text: str) -> bool:
    lowered = str(text or "").lower()
    markers = (
        "just a moment",
        "checking your browser",
        "verify you are human",
        "cf-turnstile",
        "__cf_chl",
        "cf_chl",
        "challenge-platform",
        "challenges.cloudflare.com",
    )
    return any(marker in lowered for marker in markers)


def _to_playwright_cookie(raw: dict[str, Any]) -> dict[str, Any] | None:
    name = str(raw.get("name") or "").strip()
    value = str(raw.get("value") or "")
    if not name:
        return None
    cookie: dict[str, Any] = {
        "name": name,
        "value": value,
        "domain": str(raw.get("domain") or "chatgpt.com"),
        "path": str(raw.get("path") or "/"),
    }
    if raw.get("expiry") is not None:
        try:
            cookie["expires"] = int(raw.get("expiry"))
        except Exception:
            pass
    if raw.get("httpOnly") is not None:
        cookie["httpOnly"] = bool(raw.get("httpOnly"))
    if raw.get("secure") is not None:
        cookie["secure"] = bool(raw.get("secure"))
    same_site = str(raw.get("sameSite") or "").strip()
    if same_site in {"Strict", "Lax", "None"}:
        cookie["sameSite"] = same_site
    return cookie


def solve_with_flaresolverr(url: str, *, proxy: str | None = None, env: dict[str, str] | None = None) -> FlareSolverrResult:
    values = env if env is not None else load_env(".env")
    api_url = flaresolverr_api_url(values)
    timeout = flaresolverr_timeout_seconds(values)
    wait_seconds = flaresolverr_wait_seconds(values)
    flare_proxy, needs_session = split_flaresolverr_proxy(proxy)
    payload: dict[str, Any] = {
        "cmd": "request.get",
        "url": url,
        "maxTimeout": timeout * 1000,
        "waitInSeconds": wait_seconds,
    }
    if flare_proxy and not needs_session:
        payload["proxy"] = flare_proxy
    session_id = ""
    started = time.time()
    try:
        if needs_session:
            session_id = f"chatgpt_runtime_{int(started)}"
            create_payload: dict[str, Any] = {"cmd": "sessions.create", "session": session_id}
            if flare_proxy:
                create_payload["proxy"] = flare_proxy
            _flare_post(api_url, create_payload, timeout + 15)
            payload["session"] = session_id
        data = _flare_post(api_url, payload, timeout + 15)
    except Exception as exc:
        return FlareSolverrResult(False, f"api_error:{exc}", [], "", url)
    finally:
        if session_id:
            try:
                _flare_post(api_url, {"cmd": "sessions.destroy", "session": session_id}, 15)
            except Exception:
                pass
    if str(data.get("status") or "").lower() != "ok":
        return FlareSolverrResult(False, f"api_status:{data.get('status')}:{data.get('message')}", [], "", url)
    solution = data.get("solution") if isinstance(data.get("solution"), dict) else {}
    html = str(solution.get("response") or "")
    solution_url = str(solution.get("url") or url)
    if _looks_like_challenge_html(html):
        return FlareSolverrResult(False, "challenge_html_returned", [], str(solution.get("userAgent") or ""), solution_url)
    cookies = []
    for raw_cookie in solution.get("cookies") or []:
        if isinstance(raw_cookie, dict):
            cookie = _to_playwright_cookie(raw_cookie)
            if cookie:
                cookies.append(cookie)
    if not cookies:
        return FlareSolverrResult(False, "no_cookies_returned", [], str(solution.get("userAgent") or ""), solution_url)
    status_code = None
    try:
        status_code = int(solution.get("status")) if solution.get("status") else None
    except Exception:
        status_code = None
    return FlareSolverrResult(
        True,
        "flaresolverr_ok",
        cookies,
        str(solution.get("userAgent") or ""),
        solution_url,
        status_code,
    )


async def inject_flaresolverr_solution(page: Any, result: FlareSolverrResult) -> None:
    if result.cookies:
        await page.context.add_cookies(result.cookies)
    if result.user_agent:
        user_agent = json.dumps(result.user_agent)
        await page.add_init_script(
            script=f"""(() => {{
                const ua = {user_agent};
                try {{
                    Object.defineProperty(navigator, 'userAgent', {{ get: () => ua, configurable: true }});
                }} catch {{}}
            }})();""",
        )
