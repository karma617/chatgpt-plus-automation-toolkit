"""PayPal flow-1: register account and generate payment link."""
from __future__ import annotations

import asyncio
import re
import secrets
import shutil
import string
import traceback
from pathlib import Path
from typing import Any

import httpx

from .browser import BrowserSession
from .checkout import create_plus_checkout_link, get_chatgpt_session
from .free_browser_flow import FreeBrowserFlow
from .free_register import FreeProfile, FreeRegisterError, generate_free_profile, random_birth_date
from .mail_provider import MailProvider
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


def save_to_link_pool(email: str, query_code: str, payment_link: str, account_line: str | None = None) -> None:
    LINK_POOL_DIR.mkdir(parents=True, exist_ok=True)
    raw = str(account_line or "").strip()
    prefix = raw if raw and raw.split("----", 1)[0].strip().lower() == email.strip().lower() else f"{email}----{query_code}"
    with LINK_POOL_FILE.open("a", encoding="utf-8") as f:
        f.write(f"{prefix}----{payment_link}\n")
    paypal_flow_state.mark_link_ready(email, account_line=prefix, payment_link=payment_link)


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
        fingerprint_seed=email,
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
        chatgpt_session = await get_chatgpt_session(page)
        access_token = str(chatgpt_session.get("accessToken") or "")
        if not access_token:
            await page.wait_for_timeout(3000)
            chatgpt_session = await get_chatgpt_session(page)
            access_token = str(chatgpt_session.get("accessToken") or "")
        if not access_token:
            raise FreeRegisterError("failed to fetch accessToken")

        payment_link = ""
        if create_payment_link:
            env = load_env(".env")
            billing_country = env.get("PAYPAL_BILLING_COUNTRY") or "US"
            chatgpt_cfg = {**cfg["chatgpt"], "billing_country": billing_country, "currency": "USD"}
            payment_link = await create_plus_checkout_link(page, access_token, chatgpt_cfg)
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
            log(f"{prefix} link ok")
            return payment_link
        log(f"{prefix} session bootstrap ok")
        return ""

    except Exception as exc:
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


async def run_paypal_register(
    cfg: dict[str, Any],
    count: int = 1,
    workers: int = 1,
    selected_email: str | None = None,
) -> int:
    """Batch run flow-1 (register + payment link)."""
    reset_last_run_detail()
    env = load_env(".env")
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

    use_proxy = paypal_register_proxy_enabled(env)
    proxy_pool: ProxyPool | None = None
    fallback_proxy = ""
    if use_proxy:
        proxy_file = paypal_register_proxy_file(env)
        proxy_pool = ProxyPool(proxy_file)
        if proxy_pool.count() == 0:
            fallback_proxy = paypal_register_local_proxy_url(env)
            proxy_pool = None
            log(f"PayPal flow1: proxy pool empty, fallback local proxy: {proxy_file} -> {fallback_proxy or 'direct'}")
        if proxy_pool is not None:
            log(f"PayPal flow1: proxy enabled, pool size={proxy_pool.count()}, file={proxy_file}")
            # Preflight check first proxy to fail fast with actionable reason.
            first_proxy = proxy_pool.pick(1)
            if first_proxy:
                ok, reason = _probe_proxy(first_proxy)
                if not ok:
                    log(f"PayPal flow1: proxy precheck failed: {reason}")
                    log("PayPal flow1: cliproxy is required for JP region, stop this run")
                    _set_last_run_detail(f"proxy precheck failed: {reason}", path=str(proxy_file))
                    return 0
                else:
                    log("PayPal flow1: proxy precheck passed")
    else:
        fallback_proxy = paypal_register_local_proxy_url(env)
        if fallback_proxy:
            log(f"PayPal flow1: proxy disabled, using local proxy: {fallback_proxy}")

    target = min(count, len(pending_accounts))
    log(f"PayPal flow1: source={active_source}, pending={len(pending_accounts)}, target={target}, workers={workers}")

    success = 0
    sem = asyncio.Semaphore(workers)

    async def worker(index: int, account: MailAccount) -> None:
        nonlocal success
        async with sem:
            proxy = proxy_pool.pick(index) if proxy_pool else fallback_proxy or None
            if proxy:
                log(f"[paypal-reg-{index:02d}] using proxy: {proxy}")
            account_mail_source = _mail_source_for_registered_account(account, active_source)
            link = await register_one(account, account_mail_source, cfg, worker_id=index, proxy=proxy)
            if link:
                code_address = account.code_address or "mail"
                save_to_link_pool(account.email, code_address, link, account_line=account.raw)
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
