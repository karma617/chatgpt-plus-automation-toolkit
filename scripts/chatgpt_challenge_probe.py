from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page, Response
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.browser import BrowserSession, prepare_proxy_for_playwright  # noqa: E402
from modules.chatgpt_register import (  # noqa: E402
    click_cloudflare_turnstile_widget,
    cloudflare_turnstile_state,
    sync_cloudflare_turnstile_token,
)
from modules.proxy_config import (  # noqa: E402
    local_proxy_url,
    normalize_proxy_url,
    paypal_flow2_proxy_file,
    paypal_register_local_proxy_url,
    paypal_register_proxy_file,
)
from modules.proxy_pool import ProxyPool  # noqa: E402
from modules.utils import load_env, resolve_path, safe_filename  # noqa: E402


DEFAULT_URL = "https://chatgpt.com/"
DEFAULT_FLARESOLVERR_URL = "http://127.0.0.1:8191/v1"
DEFAULT_OUTPUT_DIR = "output/challenge_probe"
SUCCESS_STABLE_SECONDS = 6.0


@dataclass
class AttemptResult:
    mode: str
    proxy: str
    status: str
    reason: str
    url: str = ""
    title: str = ""
    elapsed_seconds: float = 0.0
    http_status: int | None = None
    artifact_dir: str = ""
    cookies_count: int = 0
    user_agent: str = ""


def _now_tag() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def _log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def _mask_proxy(value: str | None) -> str:
    if not value:
        return ""
    parsed = urlparse(normalize_proxy_url(value))
    if parsed.username or parsed.password:
        auth = "***:***@"
    else:
        auth = ""
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{auth}{host}{port}"


def _load_proxy_values(args: argparse.Namespace, env: dict[str, str]) -> list[str | None]:
    if args.no_proxy:
        return [None]
    if args.proxy:
        return [normalize_proxy_url(args.proxy)]

    proxy_file = args.proxy_file.strip()
    if not proxy_file:
        if args.proxy_preset == "flow2-jp":
            proxy_file = paypal_flow2_proxy_file(env, "jp")
        elif args.proxy_preset == "register":
            proxy_file = paypal_register_proxy_file(env)
        else:
            proxy_file = env.get("PAYPAL_PROXY_FILE_JP") or env.get("PAYPAL_REGISTER_PROXY_FILE") or "data/proxies/proxies_jp.txt"

    values: list[str | None] = []
    try:
        pool = ProxyPool(proxy_file)
        values = [normalize_proxy_url(item) for item in pool.random_sequence() if normalize_proxy_url(item)]
    except Exception as exc:
        _log(f"proxy file load failed: {proxy_file}: {exc}")

    fallback = ""
    if args.local_proxy:
        fallback = args.local_proxy
    elif args.proxy_preset == "register":
        fallback = paypal_register_local_proxy_url(env)
    else:
        fallback = local_proxy_url(env)
    fallback = normalize_proxy_url(fallback)
    if fallback and fallback not in values:
        values.append(fallback)

    if not values and not args.require_proxy:
        values.append(None)
    return values


async def _page_summary(page: Page) -> dict[str, Any]:
    try:
        state = await cloudflare_turnstile_state(page)
    except Exception:
        state = {}
    try:
        title = await page.title()
    except Exception:
        title = ""
    try:
        text = await page.locator("body").inner_text(timeout=1500)
    except Exception:
        text = ""
    try:
        html = await page.content()
    except Exception:
        html = ""
    return {
        "url": page.url,
        "title": title,
        "text_sample": text[:2000],
        "html_sample": html[:12000],
        "cloudflare": state,
    }


def _looks_like_challenge(summary: dict[str, Any]) -> bool:
    state = summary.get("cloudflare") or {}
    if state.get("present"):
        return True
    haystack = " ".join(
        str(summary.get(key) or "").lower()
        for key in ("url", "title", "text_sample", "html_sample")
    )
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
    return any(marker in haystack for marker in markers)


def _looks_like_success(summary: dict[str, Any]) -> tuple[bool, str]:
    if _looks_like_challenge(summary):
        return False, "challenge_present"
    url = str(summary.get("url") or "").lower()
    title = str(summary.get("title") or "").lower()
    text = str(summary.get("text_sample") or "").lower()
    if "/api/auth/error" in url:
        return False, "auth_error_page"
    if "chatgpt.com" not in url:
        return False, "unexpected_host"
    if any(marker in text for marker in ("chatgpt", "log in", "sign up", "new chat", "upgrade", "start now")):
        return True, "chatgpt_landing_marker"
    if "chatgpt" in title:
        return True, "chatgpt_title"
    return True, "challenge_absent"


async def _save_playwright_artifacts(page: Page, attempt_dir: Path, result: AttemptResult) -> None:
    attempt_dir.mkdir(parents=True, exist_ok=True)
    summary = await _page_summary(page)
    (attempt_dir / "state.json").write_text(
        json.dumps({"result": asdict(result), "summary": summary}, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )
    html = str(summary.get("html_sample") or "")
    if html:
        (attempt_dir / "page_sample.html").write_text(html, encoding="utf-8")
    try:
        await page.screenshot(path=str(attempt_dir / "screenshot.png"), full_page=True)
    except Exception:
        pass
    try:
        cookies = await page.context.cookies()
        (attempt_dir / "cookies.json").write_text(json.dumps(cookies, ensure_ascii=True, indent=2), encoding="utf-8")
    except Exception:
        pass


async def _human_like_pause(page: Page, min_ms: int = 700, max_ms: int = 1800) -> None:
    try:
        viewport = page.viewport_size or {"width": 1366, "height": 768}
        x = random.randint(80, max(120, int(viewport["width"]) - 80))
        y = random.randint(80, max(120, int(viewport["height"]) - 80))
        await page.mouse.move(x, y, steps=random.randint(8, 24))
        if random.random() < 0.35:
            await page.mouse.wheel(0, random.randint(80, 260))
    except Exception:
        pass
    await page.wait_for_timeout(random.randint(min_ms, max_ms))


async def run_playwright_attempt(
    *,
    proxy: str | None,
    args: argparse.Namespace,
    attempt_dir: Path,
) -> AttemptResult:
    started = time.monotonic()
    masked_proxy = _mask_proxy(proxy)
    result = AttemptResult(mode="playwright", proxy=masked_proxy, status="failure", reason="not_started")
    profile_dir = resolve_path(args.profile_root) / safe_filename(f"{_now_tag()}_{random.randint(1000, 9999)}")
    response_status: int | None = None
    async with BrowserSession(
        profile_dir=profile_dir,
        headless=args.headless,
        slow_mo=args.slow_mo,
        timeout_ms=args.timeout_seconds * 1000,
        proxy=proxy,
        isolated=not args.persistent_profile,
        fingerprint_seed=f"challenge-probe|{proxy or 'direct'}|{time.time_ns()}",
    ) as session:
        page = await session.current_page()
        result.user_agent = str(session.fingerprint.get("user_agent") or "")
        _log(f"playwright opening {args.url} proxy={masked_proxy or 'direct'}")
        response: Response | None = await page.goto(args.url, wait_until="domcontentloaded", timeout=args.timeout_seconds * 1000)
        response_status = response.status if response else None
        deadline = time.monotonic() + args.timeout_seconds
        stable_since: float | None = None
        click_count = 0
        last_click = 0.0
        last_reason = "unknown"

        while time.monotonic() < deadline:
            page = await session.current_page()
            summary = await _page_summary(page)
            ok, reason = _looks_like_success(summary)
            last_reason = reason
            if ok:
                if stable_since is None:
                    stable_since = time.monotonic()
                if time.monotonic() - stable_since >= SUCCESS_STABLE_SECONDS:
                    cookies = await page.context.cookies()
                    result = AttemptResult(
                        mode="playwright",
                        proxy=masked_proxy,
                        status="success",
                        reason=reason,
                        url=page.url,
                        title=str(summary.get("title") or ""),
                        elapsed_seconds=round(time.monotonic() - started, 2),
                        http_status=response_status,
                        artifact_dir=str(attempt_dir),
                        cookies_count=len(cookies),
                        user_agent=result.user_agent,
                    )
                    await _save_playwright_artifacts(page, attempt_dir, result)
                    return result
            else:
                stable_since = None

            cf_state = summary.get("cloudflare") or {}
            token_len = int(cf_state.get("tokenLength") or 0)
            if token_len >= 80:
                synced = await sync_cloudflare_turnstile_token(page)
                _log(f"turnstile token synced len={synced}")
                await _human_like_pause(page, 1000, 2500)
                continue

            now = time.monotonic()
            if click_count < args.max_clicks and now - last_click >= args.click_interval_seconds:
                last_click = now
                clicked = await click_cloudflare_turnstile_widget(page)
                click_count += 1 if clicked else 0
                _log(
                    "turnstile click "
                    f"{'sent' if clicked else 'not_found'} "
                    f"count={click_count}/{args.max_clicks} token_len={token_len} reason={last_reason}"
                )
            await _human_like_pause(page, 1200, 2600)

        summary = await _page_summary(page)
        result = AttemptResult(
            mode="playwright",
            proxy=masked_proxy,
            status="failure",
            reason=f"timeout:{last_reason}",
            url=page.url,
            title=str(summary.get("title") or ""),
            elapsed_seconds=round(time.monotonic() - started, 2),
            http_status=response_status,
            artifact_dir=str(attempt_dir),
            cookies_count=0,
            user_agent=result.user_agent,
        )
        await _save_playwright_artifacts(page, attempt_dir, result)
        if args.keep_open_on_failure:
            _log("keep-open-on-failure enabled; close the browser window to continue")
            while True:
                try:
                    if page.is_closed():
                        break
                    await page.wait_for_timeout(1000)
                except Exception:
                    break
        return result


def _split_flaresolverr_proxy(proxy: str | None) -> tuple[dict[str, str] | None, bool]:
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


def _flare_payload(args: argparse.Namespace, proxy: str | None) -> tuple[dict[str, Any], dict[str, str] | None, bool]:
    flare_proxy, needs_session = _split_flaresolverr_proxy(proxy)
    payload: dict[str, Any] = {
        "cmd": "request.get",
        "url": args.url,
        "maxTimeout": args.timeout_seconds * 1000,
        "waitInSeconds": args.flare_wait_seconds,
    }
    if flare_proxy and not needs_session:
        payload["proxy"] = flare_proxy
    return payload, flare_proxy, needs_session


def _flare_post(api_url: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    response = requests.post(
        api_url,
        headers={"Content-Type": "application/json"},
        json=payload,
        timeout=timeout,
    )
    response.raise_for_status()
    return dict(response.json())


def _flare_solution_summary(data: dict[str, Any]) -> dict[str, Any]:
    solution = data.get("solution") if isinstance(data.get("solution"), dict) else {}
    html = str(solution.get("response") or "")
    return {
        "status": data.get("status"),
        "message": data.get("message"),
        "startTimestamp": data.get("startTimestamp"),
        "endTimestamp": data.get("endTimestamp"),
        "solution_url": solution.get("url"),
        "solution_status": solution.get("status"),
        "cookies_count": len(solution.get("cookies") or []),
        "user_agent": solution.get("userAgent") or "",
        "html_sample": html[:12000],
    }


def _flare_success(summary: dict[str, Any]) -> tuple[bool, str]:
    if str(summary.get("status") or "").lower() != "ok":
        return False, f"api_status:{summary.get('status')}"
    pseudo = {
        "url": summary.get("solution_url") or "",
        "title": "",
        "text_sample": summary.get("html_sample") or "",
        "html_sample": summary.get("html_sample") or "",
        "cloudflare": {"present": False},
    }
    if _looks_like_challenge(pseudo):
        return False, "challenge_html_returned"
    if not str(summary.get("solution_url") or "").lower().startswith("https://chatgpt.com"):
        return False, "unexpected_solution_url"
    return True, "flaresolverr_ok"


def _to_playwright_cookie(raw: dict[str, Any]) -> dict[str, Any]:
    cookie: dict[str, Any] = {
        "name": str(raw.get("name") or ""),
        "value": str(raw.get("value") or ""),
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


async def verify_flaresolverr_solution_with_playwright(
    *,
    proxy: str | None,
    args: argparse.Namespace,
    attempt_dir: Path,
) -> AttemptResult:
    started = time.monotonic()
    masked_proxy = _mask_proxy(proxy)
    verify_dir = attempt_dir / "playwright_verify"
    verify_dir.mkdir(parents=True, exist_ok=True)
    try:
        payload = json.loads((attempt_dir / "result.json").read_text(encoding="utf-8"))
        raw = payload.get("raw") if isinstance(payload.get("raw"), dict) else {}
        solution = raw.get("solution") if isinstance(raw.get("solution"), dict) else {}
        cookies = [_to_playwright_cookie(item) for item in (solution.get("cookies") or []) if item.get("name")]
        user_agent = str(solution.get("userAgent") or "")
    except Exception as exc:
        result = AttemptResult(
            mode="playwright_verify",
            proxy=masked_proxy,
            status="failure",
            reason=f"load_flaresolverr_result_failed:{exc}",
            artifact_dir=str(verify_dir),
        )
        (verify_dir / "state.json").write_text(json.dumps(asdict(result), ensure_ascii=True, indent=2), encoding="utf-8")
        return result

    bridge = None
    browser = None
    playwright = None
    page: Page | None = None
    try:
        proxy_config, bridge = await prepare_proxy_for_playwright(proxy)
        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(
            headless=args.headless,
            slow_mo=args.slow_mo,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-gpu",
            ],
            proxy=proxy_config,
        )
        context = await browser.new_context(
            viewport={"width": 1366, "height": 768},
            screen={"width": 1366, "height": 768},
            user_agent=user_agent or None,
            locale="en-US",
            timezone_id="Asia/Tokyo",
        )
        await context.add_init_script(
            script="""
            (() => {
                try { Object.defineProperty(navigator, 'webdriver', { get: () => undefined, configurable: true }); } catch {}
                window.chrome = window.chrome || { runtime: {} };
            })();
            """
        )
        if cookies:
            await context.add_cookies(cookies)
        page = await context.new_page()
        _log(f"playwright verify FlareSolverr cookies proxy={masked_proxy or 'direct'} cookies={len(cookies)}")
        response = await page.goto(args.url, wait_until="domcontentloaded", timeout=args.timeout_seconds * 1000)
        await page.wait_for_timeout(max(1500, min(8000, args.flare_wait_seconds * 1000)))
        summary = await _page_summary(page)
        ok, reason = _looks_like_success(summary)
        result = AttemptResult(
            mode="playwright_verify",
            proxy=masked_proxy,
            status="success" if ok else "failure",
            reason=reason,
            url=page.url,
            title=str(summary.get("title") or ""),
            elapsed_seconds=round(time.monotonic() - started, 2),
            http_status=response.status if response else None,
            artifact_dir=str(verify_dir),
            cookies_count=len(await context.cookies()),
            user_agent=user_agent,
        )
        await _save_playwright_artifacts(page, verify_dir, result)
        return result
    except Exception as exc:
        result = AttemptResult(
            mode="playwright_verify",
            proxy=masked_proxy,
            status="failure",
            reason=f"exception:{type(exc).__name__}:{exc}",
            elapsed_seconds=round(time.monotonic() - started, 2),
            artifact_dir=str(verify_dir),
            user_agent=user_agent if "user_agent" in locals() else "",
        )
        (verify_dir / "state.json").write_text(json.dumps(asdict(result), ensure_ascii=True, indent=2), encoding="utf-8")
        return result
    finally:
        if browser:
            try:
                await browser.close()
            except Exception:
                pass
        if playwright:
            try:
                await playwright.stop()
            except Exception:
                pass
        if bridge:
            try:
                await bridge.close()
            except Exception:
                pass


def run_flaresolverr_attempt(
    *,
    proxy: str | None,
    args: argparse.Namespace,
    attempt_dir: Path,
) -> AttemptResult:
    started = time.monotonic()
    masked_proxy = _mask_proxy(proxy)
    attempt_dir.mkdir(parents=True, exist_ok=True)
    payload, flare_proxy, needs_session = _flare_payload(args, proxy)
    safe_payload = dict(payload)
    if safe_payload.get("proxy"):
        safe_payload["proxy"] = {"url": _mask_proxy(proxy)}
    (attempt_dir / "request.json").write_text(
        json.dumps(safe_payload, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )
    _log(f"flaresolverr request.get {args.url} proxy={masked_proxy or 'direct'} api={args.flaresolverr_url}")
    session_id = ""
    try:
        if needs_session:
            session_id = f"chatgpt_probe_{int(time.time())}_{random.randint(1000, 9999)}"
            create_payload: dict[str, Any] = {"cmd": "sessions.create", "session": session_id}
            if flare_proxy:
                create_payload["proxy"] = flare_proxy
            _flare_post(args.flaresolverr_url, create_payload, args.timeout_seconds + 15)
            payload["session"] = session_id
        data = _flare_post(args.flaresolverr_url, payload, args.timeout_seconds + 15)
    except Exception as exc:
        result = AttemptResult(
            mode="flaresolverr",
            proxy=masked_proxy,
            status="failure",
            reason=f"api_error:{exc}",
            elapsed_seconds=round(time.monotonic() - started, 2),
            artifact_dir=str(attempt_dir),
        )
        (attempt_dir / "result.json").write_text(json.dumps(asdict(result), ensure_ascii=True, indent=2), encoding="utf-8")
        return result
    finally:
        if session_id:
            try:
                _flare_post(args.flaresolverr_url, {"cmd": "sessions.destroy", "session": session_id}, 15)
            except Exception:
                pass

    summary = _flare_solution_summary(data)
    ok, reason = _flare_success(summary)
    solution = data.get("solution") if isinstance(data.get("solution"), dict) else {}
    cookies = solution.get("cookies") or []
    result = AttemptResult(
        mode="flaresolverr",
        proxy=masked_proxy,
        status="success" if ok else "failure",
        reason=reason,
        url=str(summary.get("solution_url") or ""),
        title="",
        elapsed_seconds=round(time.monotonic() - started, 2),
        http_status=int(summary["solution_status"]) if summary.get("solution_status") else None,
        artifact_dir=str(attempt_dir),
        cookies_count=len(cookies),
        user_agent=str(summary.get("user_agent") or ""),
    )
    (attempt_dir / "result.json").write_text(
        json.dumps({"result": asdict(result), "raw": data, "summary": summary}, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )
    html = str(summary.get("html_sample") or "")
    if html:
        (attempt_dir / "page_sample.html").write_text(html, encoding="utf-8")
    if cookies:
        (attempt_dir / "cookies.json").write_text(json.dumps(cookies, ensure_ascii=True, indent=2), encoding="utf-8")
    return result


async def run_probe(args: argparse.Namespace) -> int:
    env = load_env(args.env)
    output_root = resolve_path(args.output_dir) / _now_tag()
    output_root.mkdir(parents=True, exist_ok=True)
    proxies = _load_proxy_values(args, env)
    if not proxies:
        _log("no proxy available; pass --proxy, configure proxy file, or use --no-proxy")
        return 2

    if args.max_proxy_attempts > 0:
        proxies = proxies[: args.max_proxy_attempts]
    _log(f"loaded proxy attempts={len(proxies)} mode={args.mode} output={output_root}")

    all_results: list[AttemptResult] = []
    modes = ["flaresolverr", "playwright"] if args.mode == "both" else [args.mode]
    for proxy_index, proxy in enumerate(proxies, start=1):
        for mode in modes:
            attempt_dir = output_root / f"{proxy_index:02d}_{mode}_{safe_filename(_mask_proxy(proxy) or 'direct')}"
            try:
                if mode == "flaresolverr":
                    result = await asyncio.to_thread(run_flaresolverr_attempt, proxy=proxy, args=args, attempt_dir=attempt_dir)
                else:
                    result = await run_playwright_attempt(proxy=proxy, args=args, attempt_dir=attempt_dir)
            except (PlaywrightError, Exception) as exc:
                result = AttemptResult(
                    mode=mode,
                    proxy=_mask_proxy(proxy),
                    status="failure",
                    reason=f"exception:{type(exc).__name__}:{exc}",
                    elapsed_seconds=0,
                    artifact_dir=str(attempt_dir),
                )
                attempt_dir.mkdir(parents=True, exist_ok=True)
                (attempt_dir / "result.json").write_text(json.dumps(asdict(result), ensure_ascii=True, indent=2), encoding="utf-8")
            all_results.append(result)
            _log(
                f"attempt result mode={result.mode} status={result.status} "
                f"reason={result.reason} proxy={result.proxy or 'direct'} elapsed={result.elapsed_seconds}s"
            )
            if result.status == "success" and mode == "flaresolverr" and args.verify_flaresolverr_with_playwright:
                verify_result = await verify_flaresolverr_solution_with_playwright(
                    proxy=proxy,
                    args=args,
                    attempt_dir=attempt_dir,
                )
                all_results.append(verify_result)
                _log(
                    f"attempt result mode={verify_result.mode} status={verify_result.status} "
                    f"reason={verify_result.reason} proxy={verify_result.proxy or 'direct'} "
                    f"elapsed={verify_result.elapsed_seconds}s"
                )
                if verify_result.status != "success":
                    continue
            if result.status == "success":
                (output_root / "summary.json").write_text(
                    json.dumps([asdict(item) for item in all_results], ensure_ascii=True, indent=2),
                    encoding="utf-8",
                )
                _log(f"success artifacts={result.artifact_dir}")
                return 0
            if args.stop_after_first_mode and args.mode == "both":
                break

    (output_root / "summary.json").write_text(
        json.dumps([asdict(item) for item in all_results], ensure_ascii=True, indent=2),
        encoding="utf-8",
    )
    _log(f"all attempts failed summary={output_root / 'summary.json'}")
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Probe ChatGPT Cloudflare challenge handling with proxy rotation.")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--env", default=".env")
    parser.add_argument("--mode", choices=("playwright", "flaresolverr", "both"), default="both")
    parser.add_argument("--flaresolverr-url", default=DEFAULT_FLARESOLVERR_URL)
    parser.add_argument("--flare-wait-seconds", type=int, default=5)
    parser.add_argument("--verify-flaresolverr-with-playwright", action="store_true")
    parser.add_argument("--proxy", default="")
    parser.add_argument("--proxy-file", default="")
    parser.add_argument("--proxy-preset", choices=("flow2-jp", "register", "auto"), default="flow2-jp")
    parser.add_argument("--local-proxy", default="")
    parser.add_argument("--no-proxy", action="store_true")
    parser.add_argument("--require-proxy", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--max-proxy-attempts", type=int, default=5)
    parser.add_argument("--timeout-seconds", type=int, default=90)
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--slow-mo", type=int, default=80)
    parser.add_argument("--max-clicks", type=int, default=3)
    parser.add_argument("--click-interval-seconds", type=int, default=14)
    parser.add_argument("--persistent-profile", action="store_true")
    parser.add_argument("--profile-root", default="profiles/challenge_probe")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--keep-open-on-failure", action="store_true")
    parser.add_argument("--stop-after-first-mode", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.flaresolverr_url and not args.flaresolverr_url.rstrip("/").endswith("/v1"):
        args.flaresolverr_url = args.flaresolverr_url.rstrip("/") + "/v1"
    return asyncio.run(run_probe(args))


if __name__ == "__main__":
    raise SystemExit(main())
