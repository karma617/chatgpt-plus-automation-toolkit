"""PayPal flow-1: register account and generate payment link."""
from __future__ import annotations

import asyncio
import re
import secrets
import shutil
import string
import time
import traceback
from pathlib import Path
from typing import Any

import httpx

from .browser import BrowserSession
from .checkout import checkout_billing_for_region, create_plus_checkout_link, get_chatgpt_session, normalize_checkout_region
from .free_browser_flow import FreeBrowserFlow
from .free_register import FreeProfile, FreeRegisterError, generate_free_profile, random_birth_date
from .chatgpt_register import ChatGPTRegister, is_signin_problem_retry_reason
from .mail_provider import MailCodeTimeoutError, MailProvider
from .proxy_config import paypal_register_local_proxy_url, paypal_register_proxy_enabled, paypal_register_proxy_file
from .proxy_pool import ProxyPool
from . import paypal_flow_state
from . import session_export
from .storage import MailAccount, parse_mail_line
from .utils import load_env, log, now_utc, resolve_path, safe_filename


PAYPAL_OUTPUT_ROOT = resolve_path("output/paypal注册")
LINK_POOL_DIR = PAYPAL_OUTPUT_ROOT / "长链接账号"
LINK_POOL_FILE = LINK_POOL_DIR / "account.txt"
DOMAIN163_USED_FILE = PAYPAL_OUTPUT_ROOT / "domain163_used.txt"
PAYPAL_SESSIOND_DIR = PAYPAL_OUTPUT_ROOT / "sessiond"
PAYPAL_SESSION_CACHE_FILE = PAYPAL_SESSIOND_DIR / "session_cache.jsonl"
ICLOUD_DEFAULT_FILE = resolve_path("data/paypal/icloud_accounts.txt")
EXTERNAL_MAIL_FETCH_MODE_IMAP163 = {"desktop_imap163", "external_imap163", "imap163"}
REGISTER_ONLY_SUMMARY_FILE = resolve_path("output/register_only/registered_sessions.txt")
REGISTER_ONLY_FLOW1_USED_FILE = resolve_path("output/register_only/paypal_flow1_used_emails.txt")
PAYPAL_PENDING_AUTH_FILE = PAYPAL_OUTPUT_ROOT / "\u5f85\u6388\u6743\u8d26\u53f7" / "account.txt"
_LAST_RUN_DETAIL: dict[str, str] = {"message": "", "account": "", "path": ""}
MAIL_CODE_TIMEOUT_DISCARD_THRESHOLD = 3
SIGNIN_PROBLEM_RETRY_CURRENT_FLOW_THRESHOLD = 3


def _set_last_run_detail(message: str = "", *, account: str = "", path: str = "") -> None:
    _LAST_RUN_DETAIL.clear()
    _LAST_RUN_DETAIL.update({"message": message, "account": account, "path": path})


def get_last_run_detail() -> dict[str, str]:
    return dict(_LAST_RUN_DETAIL)


def reset_last_run_detail() -> None:
    _set_last_run_detail()


def _is_network_navigation_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(
        key in text
        for key in (
            "err_connection_reset",
            "err_proxy_connection_failed",
            "err_connection_closed",
            "err_timed_out",
            "err_name_not_resolved",
            "net::err",
        )
    )


def _short_error(exc: Exception) -> str:
    text = str(exc).strip().replace("\r", "\n")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return (lines[0] if lines else exc.__class__.__name__)[:500]


def _display_proxy(proxy: str | None) -> str:
    if not proxy:
        return "direct"
    text = proxy.strip()
    if "@" not in text:
        return text
    prefix, suffix = text.rsplit("@", 1)
    scheme = prefix.split("://", 1)[0] + "://" if "://" in prefix else ""
    return f"{scheme}***:***@{suffix}"


def _proxy_attempts(proxy_pool: ProxyPool | None, fallback_proxy: str | None = "") -> list[str | None]:
    if proxy_pool:
        return proxy_pool.random_sequence()
    return [fallback_proxy or None]


def _is_mail_code_timeout_reason(reason: str | None) -> bool:
    text = str(reason or "").lower()
    return "mail_code_timeout" in text or "验证码等待超时" in text or "没有新验证码" in text


def _discard_flow1_account_for_mail_timeout(email: str, *, reason: str) -> None:
    safe_reason = str(reason or "mail_code_timeout").strip()[:500]
    discard_reason = f"mail_code_timeout_after_{MAIL_CODE_TIMEOUT_DISCARD_THRESHOLD}_attempts: {safe_reason}"
    paypal_flow_state.mark_discarded_many([email], reason=discard_reason)
    paypal_flow_state.append_discarded_emails([email], reason=discard_reason)


async def _fetch_chatgpt_session_with_retry(page, prefix: str, attempts: int = 4) -> dict[str, Any]:
    last_error = ""
    for attempt in range(1, max(1, attempts) + 1):
        try:
            chatgpt_session = await get_chatgpt_session(page)
            if str(chatgpt_session.get("accessToken") or ""):
                return chatgpt_session
            last_error = "session missing accessToken"
        except Exception as exc:  # noqa: BLE001
            last_error = _short_error(exc)
        if attempt < attempts:
            log(f"{prefix} accessToken/session fetch failed, retry after page settles ({attempt}/{attempts}): {last_error}")
            try:
                await page.wait_for_load_state("networkidle", timeout=6000)
            except Exception:
                pass
            await page.wait_for_timeout(2500)
    raise RuntimeError(last_error or "failed to fetch ChatGPT session")


def _probe_proxy(proxy: str, timeout_sec: int = 12) -> tuple[bool, str]:
    urls = (
        "https://api.ipify.org",
        "https://ifconfig.me/ip",
        "http://example.com",
    )
    errors: list[str] = []
    with httpx.Client(proxy=proxy, timeout=timeout_sec, follow_redirects=True) as c:
        for url in urls:
            try:
                r = c.get(url)
                body = (r.text or "").strip()
                if r.status_code == 200:
                    return True, "ok"
                if r.status_code == 403:
                    m = re.search(r"forbidden ip=([0-9.]+)", body, flags=re.I)
                    if m:
                        return False, f"403 forbidden: source ip {m.group(1)} not supported by proxy provider"
                errors.append(f"{url} -> http {r.status_code}: {body[:120]}")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{url} -> {type(exc).__name__}: {exc}")
    return False, " | ".join(errors[:3]) if errors else "unknown proxy precheck error"


def _country_matches(value: str, required_country_code: str) -> bool:
    text = str(value or "").strip().lower()
    required = str(required_country_code or "").strip().upper()
    if not required:
        return True
    if required == "JP":
        return text in {"jp", "jpn", "japan", "日本"} or "japan" in text or "日本" in text
    return text == required.lower()


def _probe_proxy_country(proxy: str, required_country_code: str, timeout_sec: int = 12) -> tuple[bool, str]:
    required = str(required_country_code or "").strip().upper()
    if not required:
        return _probe_proxy(proxy, timeout_sec=timeout_sec)
    urls = (
        "https://ipwho.is/",
        "https://ipapi.co/json/",
        "https://ipinfo.io/json",
    )
    errors: list[str] = []
    with httpx.Client(proxy=proxy, timeout=timeout_sec, follow_redirects=True) as c:
        for url in urls:
            try:
                r = c.get(url)
                body = (r.text or "").strip()
                if r.status_code != 200:
                    errors.append(f"{url} -> http {r.status_code}: {body[:120]}")
                    continue
                try:
                    data = r.json()
                except Exception:
                    errors.append(f"{url} -> non-json: {body[:120]}")
                    continue
                country_code = str(
                    data.get("country_code")
                    or data.get("countryCode")
                    or data.get("country")
                    or data.get("cc")
                    or ""
                ).strip()
                country_name = str(data.get("country_name") or data.get("country") or "").strip()
                ip = str(data.get("ip") or data.get("query") or "").strip()
                if _country_matches(country_code, required) or _country_matches(country_name, required):
                    return True, f"country={country_code or country_name or required} ip={ip}"
                if country_code or country_name:
                    return False, f"country mismatch: got={country_code or country_name} required={required} ip={ip}"
                errors.append(f"{url} -> no country field")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{url} -> {type(exc).__name__}: {exc}")
    return False, " | ".join(errors[:3]) if errors else "unknown proxy country precheck error"


def _precheck_proxy_pool(
    proxy_pool: ProxyPool,
    *,
    max_checks: int = 5,
    required_country_code: str = "",
) -> tuple[list[str], list[str]]:
    usable: list[str] = []
    errors: list[str] = []
    for proxy in proxy_pool.sequence(1)[: max(1, max_checks)]:
        if required_country_code:
            ok, reason = _probe_proxy_country(proxy, required_country_code)
        else:
            ok, reason = _probe_proxy(proxy)
        if ok:
            usable.append(proxy)
        else:
            errors.append(f"{proxy}: {reason}")
    return usable, errors


def generate_chatgpt_password() -> str:
    alphabet = string.ascii_letters + string.digits
    return "Lc" + "".join(secrets.choice(alphabet) for _ in range(16)) + "9!"


def load_icloud_accounts(path: Path | None = None) -> list[tuple[str, str]]:
    p = path or ICLOUD_DEFAULT_FILE
    if not p.exists():
        return []
    accounts: list[tuple[str, str]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("----", 1)
        if len(parts) == 2 and "@" in parts[0]:
            accounts.append((parts[0].strip(), parts[1].strip()))
    return accounts


def already_in_link_pool() -> set[str]:
    if not LINK_POOL_FILE.exists():
        return set()
    return {
        line.split("----", 1)[0].strip().lower()
        for line in LINK_POOL_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip() and "----" in line
    }


def _load_register_only_flow1_used_emails() -> set[str]:
    if not REGISTER_ONLY_FLOW1_USED_FILE.exists():
        return set()
    used: set[str] = set()
    for line in REGISTER_ONLY_FLOW1_USED_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
        email = line.split("\t", 1)[0].strip().lower()
        if email and "@" in email:
            used.add(email)
    return used


def _mark_register_only_flow1_used(email: str) -> None:
    value = (email or "").strip().lower()
    if not value or "@" not in value:
        return
    REGISTER_ONLY_FLOW1_USED_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing = _load_register_only_flow1_used_emails()
    if value in existing:
        return
    with REGISTER_ONLY_FLOW1_USED_FILE.open("a", encoding="utf-8") as fh:
        fh.write(value + "\n")


def _load_domain163_used_emails() -> set[str]:
    if not DOMAIN163_USED_FILE.exists():
        return set()
    used: set[str] = set()
    for line in DOMAIN163_USED_FILE.read_text(encoding="utf-8").splitlines():
        email = (line or "").strip().lower()
        if email and "@" in email:
            used.add(email)
    return used


def _mark_domain163_email_used(email: str) -> None:
    value = (email or "").strip().lower()
    if not value or "@" not in value:
        return
    DOMAIN163_USED_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing = _load_domain163_used_emails()
    if value in existing:
        return
    with DOMAIN163_USED_FILE.open("a", encoding="utf-8") as fh:
        fh.write(value + "\n")


def save_to_link_pool(
    email: str,
    query_code: str,
    payment_link: str,
    account_line: str | None = None,
    *,
    link_method: str = "",
) -> None:
    LINK_POOL_DIR.mkdir(parents=True, exist_ok=True)
    raw = str(account_line or "").strip()
    prefix = raw if raw and raw.split("----", 1)[0].strip().lower() == email.strip().lower() else f"{email}----{query_code}"
    with LINK_POOL_FILE.open("a", encoding="utf-8") as f:
        f.write(f"{prefix}----{payment_link}\n")
    paypal_flow_state.mark_link_ready(email, account_line=prefix, payment_link=payment_link, link_method=link_method)


def remove_from_icloud_file(email: str, path: Path | None = None) -> None:
    p = path or ICLOUD_DEFAULT_FILE
    if not p.exists():
        return
    lines = p.read_text(encoding="utf-8").splitlines()
    remaining = [l for l in lines if not l.strip().lower().startswith(email.lower())]
    p.write_text("\n".join(remaining) + ("\n" if remaining else ""), encoding="utf-8")


def _external_imap163_enabled(env: dict[str, str]) -> bool:
    mode = (env.get("MAIL_FETCH_SOURCE") or "").strip().lower()
    return mode in EXTERNAL_MAIL_FETCH_MODE_IMAP163


def _resolve_imap163_domain(env: dict[str, str]) -> str:
    merged = dict(env)
    ext_dir = (env.get("EXTERNAL_IMAP163_DIR") or "").strip()
    if ext_dir:
        ext_env_path = Path(ext_dir) / ".env"
        if ext_env_path.exists():
            for key, value in load_env(ext_env_path).items():
                merged.setdefault(key, value)
    raw_domain = (merged.get("IMAP163_FORWARD_DOMAIN") or merged.get("MAIL_DOMAIN") or "").strip().lower()
    return raw_domain.replace(",", ".")


def _domain163_fixed_domain() -> str:
    return "edu.hanyiz2.com"


def _generate_imap163_pending(count: int, done: set[str], domain: str) -> list[tuple[str, str]]:
    pending: list[tuple[str, str]] = []
    alphabet = string.ascii_lowercase + string.digits
    while len(pending) < max(1, count):
        email = f"{''.join(secrets.choice(alphabet) for _ in range(10))}@{domain}"
        if email.lower() in done:
            continue
        if any(email.lower() == item[0].lower() for item in pending):
            continue
        pending.append((email, "imap163"))
    return pending


def _normalize_mail_source(value: str) -> str:
    source = (value or "").strip().lower()
    aliases = {
        "hotmail": "hotmail",
        "icloud": "icloud_query",
        "icloud_query": "icloud_query",
        "moemail": "moemail",
        "domain163": "domain163",
        "domain": "domain163",
        "domain_mail": "domain163",
    }
    return aliases.get(source, source or "moemail")


def _is_domain163_account(account: MailAccount, domain: str) -> bool:
    email = (account.email or "").strip().lower()
    mail_url = (account.mail_url or "").strip().lower()
    if not email or not domain:
        return False
    return email.endswith(f"@{domain}") and mail_url == "imap163"


def _active_mail_source(cfg: dict[str, Any]) -> str:
    mail_cfg = cfg.get("mail", {})
    return _normalize_mail_source(str(mail_cfg.get("active_source") or mail_cfg.get("source") or "moemail"))


def _load_accounts_from_file(path: Path, done: set[str]) -> list[MailAccount]:
    if not path.exists():
        return []
    accounts: list[MailAccount] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        account = parse_mail_line(line)
        if not account:
            continue
        if account.email.lower() in done:
            continue
        accounts.append(account)
    return accounts


def _merge_accounts(*groups: list[MailAccount], done: set[str] | None = None) -> list[MailAccount]:
    blocked = done or set()
    seen: set[str] = set()
    merged: list[MailAccount] = []
    for group in groups:
        for account in group:
            email = account.email.strip().lower()
            if not email or email in blocked or email in seen:
                continue
            seen.add(email)
            merged.append(account)
    return merged


def _load_registered_flow1_accounts(done: set[str]) -> list[MailAccount]:
    return _load_accounts_from_file(REGISTER_ONLY_SUMMARY_FILE, done)


def _mail_source_for_registered_account(account: MailAccount, fallback: str) -> str:
    if account.client_id and account.refresh_token:
        return "hotmail"
    if str(account.mail_url or "").strip().lower() == "imap163":
        return "domain163"
    if account.email.strip().lower().endswith("@icloud.com"):
        return "icloud_query"
    return fallback


def _append_mail_accounts(path: Path, accounts: list[MailAccount]) -> int:
    if not accounts:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_lower: set[str] = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            account = parse_mail_line(line.strip()) if line.strip() and not line.strip().startswith("#") else None
            if account:
                existing_lower.add(account.email.strip().lower())
    written = 0
    with path.open("a", encoding="utf-8") as fh:
        for account in accounts:
            email = account.email.strip().lower()
            if not email or email in existing_lower:
                continue
            fh.write((account.raw or f"{account.email}----{account.mail_url}").rstrip() + "\n")
            existing_lower.add(email)
            written += 1
    return written


def _remove_from_account_file(email: str, path: Path) -> None:
    if not path.exists():
        return
    lines = path.read_text(encoding="utf-8").splitlines()
    remaining = []
    for line in lines:
        account = parse_mail_line(line) if line.strip() and not line.strip().startswith("#") else None
        if account and account.email.lower() == email.lower():
            continue
        remaining.append(line)
    path.write_text("\n".join(remaining) + ("\n" if remaining else ""), encoding="utf-8")


def filter_accounts_by_email(accounts: list[MailAccount], selected_email: str | None = None) -> list[MailAccount]:
    email = (selected_email or "").strip().lower()
    if not email:
        return accounts
    return [account for account in accounts if account.email.strip().lower() == email]


async def register_one(
    account: MailAccount,
    mail_source: str,
    cfg: dict[str, Any],
    worker_id: int = 1,
    proxy: str | None = None,
    create_payment_link: bool = True,
    session_cache_path: str | Path | None = None,
    session_source: str = "paypal_flow1",
    checkout_region: str = "us",
    last_error: dict[str, str] | None = None,
    checkout_skip_methods: set[str] | None = None,
    checkout_preferred_methods: list[str] | tuple[str, ...] | None = None,
    checkout_method_sink: dict[str, str] | None = None,
) -> str | None:
    email = account.email
    prefix = f"[paypal-reg-{worker_id:02d}][{email}]"
    chatgpt_password = generate_chatgpt_password()
    full_name, age = generate_free_profile(
        int(cfg.get("register_profile", {}).get("age_min", 21)),
        int(cfg.get("register_profile", {}).get("age_max", 45)),
    )
    profile = FreeProfile(
        full_name=full_name,
        age=age,
        password=chatgpt_password,
        birth_date=random_birth_date(int(age)),
    )

    mail_provider = MailProvider(
        source=mail_source,
        timeout_sec=150,
        poll_interval_sec=5,
        log_prefix=prefix,
    )

    browser_cfg = cfg.get("browser", {})
    profile_dir = resolve_path("profiles") / f"paypal_reg_{safe_filename(email)}"

    session_kwargs = dict(
        profile_dir=profile_dir,
        headless=bool(browser_cfg.get("headless", False)),
        slow_mo=int(browser_cfg.get("slow_mo", 80)),
        timeout_ms=int(browser_cfg.get("timeout_ms", 60000)),
        proxy=proxy,
        isolated=True,
        fingerprint_seed=f"{email}|paypal-register",
        account_id=email,
        log_prefix=prefix,
        browser_engine=browser_cfg.get("engine"),
        browser_locale=browser_cfg.get("locale"),
        camoufox_executable_path=browser_cfg.get("camoufox_executable_path"),
        camoufox_geoip=browser_cfg.get("camoufox_geoip"),
    )
    session = BrowserSession(**session_kwargs)

    page = None
    flow = None
    try:
        await session.__aenter__()
        page = await session.current_page()
        flow = FreeBrowserFlow(page, prefix)

        log(f"{prefix} start register")
        for nav_attempt in range(1, 4):
            try:
                await flow.navigate_to_signup_email(email)
                break
            except Exception as exc:
                if nav_attempt == 1 and "wait button timeout" in str(exc):
                    try:
                        body = await page.locator("body").inner_text(timeout=3000)
                    except Exception:
                        body = ""
                    if "ChatGPT" in body and ("历史聊天记录" in body or "新聊天" in body or "免费版" in body):
                        log(f"{prefix} detected existing ChatGPT login profile; clearing profile and retrying signup")
                        await session.__aexit__(None, None, None)
                        if not session_kwargs.get("isolated"):
                            shutil.rmtree(profile_dir, ignore_errors=True)
                        session = BrowserSession(**session_kwargs)
                        await session.__aenter__()
                        page = await session.current_page()
                        flow = FreeBrowserFlow(page, prefix)
                        continue
                if nav_attempt >= 3 or not _is_network_navigation_error(exc):
                    raise
                log(f"{prefix} open signup failed ({nav_attempt}/3): {exc}; retrying...")
                await page.wait_for_timeout(3000 * nav_attempt)

        # 显式分支：密码页先创建密码；验证码页直接走验证码流程。
        stage = await flow.detect_post_email_stage(timeout_ms=12_000)
        if stage == "password":
            log(f"{prefix} detected password stage first; creating password before waiting email code")
            await flow.fill_password_if_shown(chatgpt_password)
        elif stage == "email_code":
            log(f"{prefix} detected email-code stage; continue with code flow")
        else:
            # 保底：未知页面状态时，仍尝试处理一次密码页，避免漏掉“先密码”分支。
            await flow.fill_password_if_shown(chatgpt_password)

        since = now_utc()
        bad_codes: set[str] = set()

        log(f"{prefix} waiting email code")
        try:
            email_code = await mail_provider.wait_code(account, since, bad_codes)
        except TimeoutError:
            resent = await flow.click_resend_code()
            if not resent:
                raise
            since = now_utc()
            email_code = await mail_provider.wait_code(account, since, bad_codes)

        for attempt in range(1, 4):
            try:
                await flow.enter_email_verification_code(email_code)
                break
            except RuntimeError as exc:
                if attempt >= 3:
                    raise
                bad_codes.add(email_code)
                resent = await flow.click_resend_code()
                if resent:
                    since = now_utc()
                await page.wait_for_timeout(1200)
                email_code = await mail_provider.wait_code(account, since, bad_codes)

        await page.wait_for_timeout(700)
        await flow.fill_password_if_shown(chatgpt_password)
        await page.wait_for_timeout(700)
        await flow.fill_about_you_and_submit(
            profile.full_name,
            profile.age,
            profile.birth_date,
            "[PayPal-Reg]",
            verification_code=email_code,
        )
        await flow.wait_until_url_leaves("about-you", timeout_ms=15000)

        log(f"{prefix} fetch accessToken")
        access_page_opened = False
        for url in ("https://chatgpt.com/", "https://chat.openai.com/"):
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                access_page_opened = True
                break
            except Exception as exc:
                if not _is_network_navigation_error(exc):
                    raise
                log(f"{prefix} open {url} failed: {exc}")
                try:
                    await page.goto(url, wait_until="commit", timeout=20_000)
                    access_page_opened = True
                    log(f"{prefix} open {url} reached commit stage; continue fetching accessToken")
                    break
                except Exception as commit_exc:
                    log(f"{prefix} open {url} commit fallback failed: {commit_exc}")
                await page.wait_for_timeout(2000)
        if not access_page_opened:
            raise FreeRegisterError("failed to open ChatGPT page for accessToken")
        await page.wait_for_timeout(2000)
        chatgpt_session = await _fetch_chatgpt_session_with_retry(page, prefix)
        access_token = str(chatgpt_session.get("accessToken") or "")
        if not access_token:
            raise FreeRegisterError("failed to fetch accessToken")

        payment_link = ""
        if create_payment_link:
            region = normalize_checkout_region(checkout_region)
            billing = checkout_billing_for_region(region)
            chatgpt_cfg = {**cfg["chatgpt"], "billing_country": billing["country"], "currency": billing["currency"]}
            log(f"{prefix} create Plus checkout link: mode={region.upper()} billing={billing['country']}/{billing['currency']}")
            payment_link = await create_plus_checkout_link(
                page,
                access_token,
                chatgpt_cfg,
                checkout_region=region,
                proxy=proxy,
                skip_methods=checkout_skip_methods,
                preferred_methods=checkout_preferred_methods,
                method_sink=checkout_method_sink,
            )
        source_format = "hotmail" if account.client_id and account.refresh_token else ("icloud_query" if email.lower().endswith("@icloud.com") else "code_address")
        code_address = (account.code_address or account.mail_url or "").strip()
        session_record = session_export.extract_session_record(
            chatgpt_session,
            email=email,
            mail_source=mail_source,
            source_format=source_format,
            code_address=code_address,
            payment_link=payment_link,
            profile_dir=str(profile_dir),
            source=session_source,
        )
        cache_target = session_cache_path or PAYPAL_SESSION_CACHE_FILE
        cache_path = session_export.upsert_session_cache(session_record, path=cache_target)
        log(f"{prefix} session cached: {cache_path}")
        if create_payment_link:
            method = str((checkout_method_sink or {}).get("method") or "unknown").strip() or "unknown"
            log(f"{prefix} link ok method={method}")
            return payment_link
        log(f"{prefix} session bootstrap ok")
        return ""

    except Exception as exc:
        if last_error is not None:
            last_error["reason"] = str(exc)
            if is_signin_problem_retry_reason(str(exc)):
                last_error["kind"] = "retry_current_flow"
            elif isinstance(exc, MailCodeTimeoutError):
                last_error["kind"] = "mail_code_timeout"
        if page is not None:
            try:
                current_url = page.url
                title = await page.title()
                body = await page.evaluate("() => (document.body?.innerText || '').slice(0, 1200)")
                log(f"{prefix} debug page: url={current_url} title={title!r} body={body!r}")
            except Exception:
                pass
        if flow is not None:
            try:
                await flow.screenshot("paypal_reg_failed.png")
            except Exception:
                pass
        log(f"{prefix} failed: {exc}")
        traceback.print_exc()
        return None
    finally:
        await session.__aexit__(None, None, None)


async def login_existing_account_for_checkout(
    account: MailAccount,
    mail_source: str,
    cfg: dict[str, Any],
    worker_id: int = 1,
    proxy: str | None = None,
    create_payment_link: bool = True,
    session_cache_path: str | Path | None = None,
    session_source: str = "paypal_flow1_existing_login",
    checkout_region: str = "us",
    last_error: dict[str, str] | None = None,
    checkout_skip_methods: set[str] | None = None,
    checkout_preferred_methods: list[str] | tuple[str, ...] | None = None,
    checkout_method_sink: dict[str, str] | None = None,
) -> str | None:
    """Log in an already registered account and optionally create a checkout link."""
    email = account.email
    prefix = f"[paypal-login-{worker_id:02d}][{email}]"
    mail_provider = MailProvider(
        source=mail_source,
        timeout_sec=int(cfg.get("mail", {}).get("code_timeout_sec", 150)),
        poll_interval_sec=int(cfg.get("mail", {}).get("poll_interval_sec", 5)),
        log_prefix=prefix,
    )
    browser_cfg = cfg.get("browser", {})
    profile_dir = resolve_path("profiles") / f"paypal_login_{safe_filename(email)}"
    session = BrowserSession(
        profile_dir=profile_dir,
        headless=bool(browser_cfg.get("headless", False)),
        slow_mo=int(browser_cfg.get("slow_mo", 80)),
        timeout_ms=int(browser_cfg.get("timeout_ms", 60000)),
        proxy=proxy,
        isolated=True,
        fingerprint_seed=f"{email}|paypal-login",
        account_id=email,
        log_prefix=prefix,
        browser_engine=browser_cfg.get("engine"),
        browser_locale=browser_cfg.get("locale"),
        camoufox_executable_path=browser_cfg.get("camoufox_executable_path"),
        camoufox_geoip=browser_cfg.get("camoufox_geoip"),
    )
    page = None
    try:
        await session.__aenter__()
        page = await session.current_page()
        register = ChatGPTRegister(
            page=page,
            page_getter=session.current_page,
            start_url="https://chatgpt.com/auth/login",
            entry_action="login",
            mail_provider=mail_provider,
            age_min=int(cfg.get("register_profile", {}).get("age_min", 21)),
            age_max=int(cfg.get("register_profile", {}).get("age_max", 45)),
            sms_selection=None,
            log_prefix=prefix,
            proxy=proxy,
        )
        log(f"{prefix} login existing registered account")
        await register.run_until_logged_in(account, now_utc())
        page = await session.current_page()
        if "chatgpt.com" not in (page.url or ""):
            await page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=45_000)
        await page.wait_for_timeout(1500)
        chatgpt_session = await _fetch_chatgpt_session_with_retry(page, prefix)
        access_token = str(chatgpt_session.get("accessToken") or "")
        if not access_token:
            raise FreeRegisterError("failed to fetch accessToken")

        payment_link = ""
        if create_payment_link:
            region = normalize_checkout_region(checkout_region)
            billing = checkout_billing_for_region(region)
            chatgpt_cfg = {**cfg["chatgpt"], "billing_country": billing["country"], "currency": billing["currency"]}
            log(f"{prefix} create Plus checkout link from existing login: mode={region.upper()} billing={billing['country']}/{billing['currency']}")
            payment_link = await create_plus_checkout_link(
                page,
                access_token,
                chatgpt_cfg,
                checkout_region=region,
                proxy=proxy,
                skip_methods=checkout_skip_methods,
                preferred_methods=checkout_preferred_methods,
                method_sink=checkout_method_sink,
            )

        source_format = "hotmail" if account.client_id and account.refresh_token else ("icloud_query" if email.lower().endswith("@icloud.com") else "code_address")
        code_address = (account.code_address or account.mail_url or "").strip()
        session_record = session_export.extract_session_record(
            chatgpt_session,
            email=email,
            mail_source=mail_source,
            source_format=source_format,
            code_address=code_address,
            payment_link=payment_link,
            profile_dir=str(profile_dir),
            source=session_source,
        )
        cache_target = session_cache_path or PAYPAL_SESSION_CACHE_FILE
        cache_path = session_export.upsert_session_cache(session_record, path=cache_target)
        log(f"{prefix} session cached: {cache_path}")
        if create_payment_link:
            method = str((checkout_method_sink or {}).get("method") or "unknown").strip() or "unknown"
            log(f"{prefix} link ok method={method}")
            return payment_link
        log(f"{prefix} session bootstrap ok")
        return ""
    except Exception as exc:
        if last_error is not None:
            last_error["reason"] = str(exc)
            if isinstance(exc, MailCodeTimeoutError):
                last_error["kind"] = "mail_code_timeout"
        if page is not None:
            try:
                current_url = page.url
                title = await page.title()
                body = await page.evaluate("() => (document.body?.innerText || '').slice(0, 1200)")
                log(f"{prefix} debug page: url={current_url} title={title!r} body={body!r}")
            except Exception:
                pass
            try:
                out = resolve_path("output/paypal\u6ce8\u518c/debug")
                out.mkdir(parents=True, exist_ok=True)
                await page.screenshot(path=str(out / f"{safe_filename(email)}_login_failed.png"), full_page=True)
            except Exception:
                pass
        log(f"{prefix} failed: {exc}")
        traceback.print_exc()
        return None
    finally:
        await session.__aexit__(None, None, None)


async def run_paypal_register(
    cfg: dict[str, Any],
    count: int = 1,
    workers: int = 1,
    selected_email: str | None = None,
    checkout_region: str = "us",
) -> int:
    """Batch run flow-1 (register + payment link)."""
    reset_last_run_detail()
    env = load_env(".env")
    region = normalize_checkout_region(checkout_region)
    region_label = "JP-PAY-US-LINK" if region == "jp" else "US"
    active_source = _active_mail_source(cfg)
    mail_cfg = cfg.get("mail", {})
    accounts_file = resolve_path(str(mail_cfg.get("accounts_file") or ""))
    raw_pool_file = resolve_path(str(mail_cfg.get("raw_pool_file") or ""))
    icloud_file = resolve_path(env.get("PAYPAL_ICLOUD_FILE") or "data/paypal/icloud_accounts.txt")
    paypal_flow_state.sync_from_files(
        registered_file=REGISTER_ONLY_SUMMARY_FILE,
        link_file=LINK_POOL_FILE,
        pending_file=PAYPAL_PENDING_AUTH_FILE,
    )
    existing_links = paypal_flow_state.active_link_emails(LINK_POOL_FILE, selected_email=selected_email or "")
    done = paypal_flow_state.flow1_blocked_emails(link_file=LINK_POOL_FILE, pending_file=PAYPAL_PENDING_AUTH_FILE)
    if active_source == "domain163":
        done |= _load_domain163_used_emails()
    pending_accounts = _load_registered_flow1_accounts(done)

    if selected_email:
        before_count = len(pending_accounts)
        pending_accounts = filter_accounts_by_email(pending_accounts, selected_email)
        if not pending_accounts:
            if existing_links:
                reused = min(count, len(existing_links))
                message = (
                    f"selected email already has unfinished payment link; "
                    f"reused existing unfinished links={reused}/{len(existing_links)}; "
                    "next=paypal-flow2/paypal-auto"
                )
                log(
                    f"PayPal flow1: selected email already has unfinished payment link; "
                    f"reuse existing links {reused}/{len(existing_links)}"
                )
                _set_last_run_detail(message, account=selected_email, path=str(LINK_POOL_FILE))
                return reused
            message = (
                f"selected email not found or already used: "
                f"{selected_email} | source={active_source} | pool_count={before_count}"
            )
            log(
                f"PayPal flow1: {message}"
            )
            _set_last_run_detail(message, account=selected_email, path=str(REGISTER_ONLY_SUMMARY_FILE))
            return 0

    if not pending_accounts:
        if existing_links:
            reused = min(count, len(existing_links))
            message = (
                f"no new registered accounts; reused existing unfinished links="
                f"{reused}/{len(existing_links)}; next=paypal-flow2/paypal-auto"
            )
            log(
                f"PayPal flow1: no new registered accounts; reuse existing unfinished links "
                f"{reused}/{len(existing_links)} from {LINK_POOL_FILE}"
            )
            _set_last_run_detail(message, path=str(LINK_POOL_FILE))
            return reused
        message = f"no pending registered accounts in {REGISTER_ONLY_SUMMARY_FILE}"
        log(f"PayPal flow1: {message}")
        _set_last_run_detail(message, path=str(REGISTER_ONLY_SUMMARY_FILE))
        return 0

    required_proxy_country = "JP" if region == "jp" else ""
    use_proxy = paypal_register_proxy_enabled(env) or bool(required_proxy_country)
    proxy_pool: ProxyPool | None = None
    fallback_proxy = ""
    if use_proxy:
        proxy_file = paypal_register_proxy_file(env)
        proxy_pool = ProxyPool(proxy_file)
        if proxy_pool.count() == 0:
            fallback_proxy = paypal_register_local_proxy_url(env)
            if required_proxy_country and fallback_proxy:
                ok, reason = _probe_proxy_country(fallback_proxy, required_proxy_country)
                if ok:
                    proxy_pool = None
                    log(
                        f"PayPal flow1: proxy pool empty, fallback local proxy verified "
                        f"{required_proxy_country}: {proxy_file} -> {fallback_proxy}"
                    )
                else:
                    message = (
                        f"JP checkout link requires Japan IP; proxy pool empty and fallback is not JP: "
                        f"{proxy_file} -> {reason}"
                    )
                    log(f"PayPal flow1: {message}")
                    _set_last_run_detail(message, path=str(proxy_file))
                    return 0
            else:
                proxy_pool = None
                log(f"PayPal flow1: proxy pool empty, fallback local proxy: {proxy_file} -> {fallback_proxy or 'direct'}")
        if proxy_pool is not None:
            log(f"PayPal flow1: proxy enabled, pool size={proxy_pool.count()}, file={proxy_file}")
            usable_proxies, proxy_errors = _precheck_proxy_pool(
                proxy_pool,
                max_checks=proxy_pool.count() if required_proxy_country else 5,
                required_country_code=required_proxy_country,
            )
            if usable_proxies:
                proxy_pool.proxies = usable_proxies
                if proxy_errors:
                    suffix = f", required_country={required_proxy_country}" if required_proxy_country else ""
                    log(f"PayPal flow1: proxy precheck skipped bad proxies={len(proxy_errors)}, usable={len(usable_proxies)}{suffix}")
                else:
                    suffix = f" ({required_proxy_country})" if required_proxy_country else ""
                    log(f"PayPal flow1: proxy precheck passed{suffix}")
            else:
                reason = " | ".join(proxy_errors[:3]) if proxy_errors else "no usable proxy"
                fallback_proxy = paypal_register_local_proxy_url(env)
                if fallback_proxy and required_proxy_country:
                    ok, fallback_reason = _probe_proxy_country(fallback_proxy, required_proxy_country)
                    if ok:
                        proxy_pool = None
                        log(
                            f"PayPal flow1: proxy precheck found no usable pool proxy, "
                            f"fallback local proxy verified {required_proxy_country}: {reason} -> {fallback_proxy}"
                        )
                    else:
                        message = (
                            f"JP checkout link requires Japan IP; no usable JP proxy: "
                            f"{reason}; fallback_not_jp={fallback_reason}"
                        )
                        log(f"PayPal flow1: {message}")
                        _set_last_run_detail(message, path=str(proxy_file))
                        return 0
                elif fallback_proxy:
                    proxy_pool = None
                    log(f"PayPal flow1: proxy precheck found no usable pool proxy, fallback local proxy: {reason} -> {fallback_proxy}")
                else:
                    log(f"PayPal flow1: proxy precheck failed, no usable proxy: {reason}")
                    _set_last_run_detail(f"proxy precheck failed: {reason}", path=str(proxy_file))
                    return 0
    else:
        fallback_proxy = paypal_register_local_proxy_url(env)
        if fallback_proxy:
            log(f"PayPal flow1: proxy disabled, using local proxy: {fallback_proxy}")

    target = min(count, len(pending_accounts))
    log(
        f"PayPal flow1: checkout_region={region_label}, source={active_source}, "
        f"pending={len(pending_accounts)}, target={target}, workers={workers}"
    )

    success = 0
    sem = asyncio.Semaphore(workers)

    async def worker(index: int, account: MailAccount) -> None:
        nonlocal success
        async with sem:
            account_mail_source = _mail_source_for_registered_account(account, active_source)
            proxies = _proxy_attempts(proxy_pool, fallback_proxy)
            link = None
            link_method = ""
            bad_methods = paypal_flow_state.bad_link_methods(account.email)
            mail_code_timeout_count = 0
            last_mail_code_timeout_reason = ""
            signin_problem_retry_count = 0
            for proxy_attempt, proxy in enumerate(proxies, start=1):
                last_error: dict[str, str] = {}
                method_sink: dict[str, str] = {}
                if proxy:
                    log(
                        f"[paypal-reg-{index:02d}][{account.email}] "
                        f"bind proxy ({proxy_attempt}/{len(proxies)}): {_display_proxy(proxy)}"
                    )
                if bad_methods:
                    log(
                        f"[paypal-reg-{index:02d}][{account.email}] "
                        f"skip previously invalid checkout methods: {', '.join(sorted(bad_methods))}"
                    )
                link = await login_existing_account_for_checkout(
                    account,
                    account_mail_source,
                    cfg,
                    worker_id=index,
                    proxy=proxy,
                    checkout_region=region,
                    last_error=last_error,
                    checkout_skip_methods=bad_methods,
                    checkout_method_sink=method_sink,
                )
                if link:
                    link_method = method_sink.get("method", "")
                    break
                reason = last_error.get("reason") or "flow1 returned no payment link"
                if last_error.get("kind") == "retry_current_flow" or is_signin_problem_retry_reason(reason):
                    signin_problem_retry_count += 1
                    log(
                        f"[paypal-reg-{index:02d}][{account.email}] "
                        f"signin problem page retry current flow "
                        f"({signin_problem_retry_count}/{SIGNIN_PROBLEM_RETRY_CURRENT_FLOW_THRESHOLD}): "
                        f"{_short_error(RuntimeError(reason))}"
                    )
                    if signin_problem_retry_count < SIGNIN_PROBLEM_RETRY_CURRENT_FLOW_THRESHOLD:
                        proxies.insert(proxy_attempt, proxy)
                        continue
                if last_error.get("kind") == "mail_code_timeout" or _is_mail_code_timeout_reason(reason):
                    mail_code_timeout_count += 1
                    last_mail_code_timeout_reason = reason
                    log(
                        f"[paypal-reg-{index:02d}][{account.email}] "
                        f"mail code timeout ({mail_code_timeout_count}/{MAIL_CODE_TIMEOUT_DISCARD_THRESHOLD})"
                    )
                    if mail_code_timeout_count >= MAIL_CODE_TIMEOUT_DISCARD_THRESHOLD:
                        _discard_flow1_account_for_mail_timeout(
                            account.email,
                            reason=last_mail_code_timeout_reason,
                        )
                        log(
                            f"[paypal-reg-{index:02d}][{account.email}] "
                            "mail code timeout reached limit; account marked discarded"
                        )
                        return
                    if proxy_attempt >= len(proxies):
                        proxies.append(proxy)
                    log(
                        f"[paypal-reg-{index:02d}][{account.email}] "
                        "retry login for new email code after timeout"
                    )
                    continue
                if not proxy_pool or proxy_attempt >= len(proxies):
                    break
                log(
                    f"[paypal-reg-{index:02d}][{account.email}] "
                    f"flow failed, rebind random proxy ({proxy_attempt}/{len(proxies)}): "
                    f"{_short_error(RuntimeError(reason))}"
                )
            if link:
                code_address = account.code_address or "mail"
                save_to_link_pool(account.email, code_address, link, account_line=account.raw, link_method=link_method)
                if active_source == "domain163":
                    _mark_domain163_email_used(account.email)
                success += 1

    tasks = [
        asyncio.create_task(worker(i + 1, account))
        for i, account in enumerate(pending_accounts[:target])
    ]
    await asyncio.gather(*tasks)
    message = f"created payment links={success}/{target}; next=paypal-flow2/paypal-auto"
    log(f"PayPal flow1 done: success={success}/{target}")
    _set_last_run_detail(message, path=str(LINK_POOL_FILE))
    return success
