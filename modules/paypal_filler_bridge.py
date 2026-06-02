from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from typing import Any

from . import paypal_flow_state
from .paypal_pay import LINK_POOL_FILE, PENDING_AUTH_FILE, load_link_pool, remove_from_link_pool, save_pending_auth
from .proxy_config import local_proxy_url, paypal_flow2_proxy_enabled, paypal_flow2_proxy_file
from .proxy_pool import ProxyPool
from .utils import load_env, log


def _normalize_proxy(proxy: str | None) -> str:
    raw = (proxy or "").strip().lstrip("\ufeff\u200b\u2060")
    if not raw:
        return ""
    if "://" in raw:
        return raw
    if raw.count(":") == 3 and "@" not in raw:
        host, port, user, password = raw.split(":", 3)
        return f"http://{user}:{password}@{host}:{port}"
    if raw.count(":") == 1 and "@" not in raw:
        return f"http://{raw}"
    return raw


def _flow2_proxy_pool(env: dict[str, str]) -> ProxyPool | None:
    use_proxy = paypal_flow2_proxy_enabled(env)
    if not use_proxy:
        return None
    proxy_file = paypal_flow2_proxy_file(env, "us")
    pool = ProxyPool(proxy_file)
    if pool.count() <= 0:
        log(f"[PayPal-Filler] PAYPAL_USE_PROXY 已开启但代理池为空: {proxy_file}")
        return None
    log(f"[PayPal-Filler] 使用流程2代理池: {proxy_file} (count={pool.count()})")
    return pool


def _run_one(link: str, proxy: str, *, headless: bool) -> int:
    from . import paypal_auto_filler as filler_module

    filler = importlib.reload(filler_module)
    filler.CONFIG["target_url"] = link
    if proxy:
        filler.CONFIG["proxy"] = proxy
    filler.PLAYWRIGHT_PROXY, filler.REQUESTS_PROXIES = filler.parse_proxy_str(filler.CONFIG.get("proxy", ""))

    old_argv = list(sys.argv)
    old_headless = os.environ.get("HEADLESS")
    try:
        argv = [str(Path(filler.__file__).resolve()), link]
        if proxy:
            argv.extend(["--proxy", proxy])
        if headless:
            argv.append("--headless")
        sys.argv = argv
        filler.main()
        return 0
    except SystemExit as exc:
        code = exc.code
        if isinstance(code, int):
            return code
        return 0 if code in (None, "") else 1
    finally:
        sys.argv = old_argv
        if old_headless is None:
            os.environ.pop("HEADLESS", None)
        else:
            os.environ["HEADLESS"] = old_headless


def run_paypal_filler_flow2(
    cfg: dict[str, Any],
    *,
    count: int,
    workers: int = 1,
    selected_email: str = "",
) -> int:
    paypal_flow_state.sync_from_files(link_file=LINK_POOL_FILE, pending_file=PENDING_AUTH_FILE)
    items = load_link_pool()
    selected = (selected_email or "").strip().lower()
    if selected:
        items = [item for item in items if str(item.get("email") or "").strip().lower() == selected]
    if not items:
        detail = f" (selected={selected_email})" if selected_email else ""
        log(f"[PayPal-Filler] 长链接池为空{detail}")
        return 0

    target = max(1, min(int(count or 1), len(items)))
    env = load_env(".env")
    proxy_pool = _flow2_proxy_pool(env)
    fallback_proxy = "" if proxy_pool else local_proxy_url(env)
    if fallback_proxy:
        log(f"[PayPal-Filler] 使用本地代理: {_normalize_proxy(fallback_proxy)}")
    worker_count = max(1, int(workers or 1))
    headless = bool((cfg.get("browser") or {}).get("headless"))
    success = 0

    for index, item in enumerate(items[:target], start=1):
        email = str(item.get("email") or "").strip()
        query_code = str(item.get("query_code") or "").strip()
        account_line = str(item.get("account_line") or "").strip()
        link = str(item.get("payment_link") or "").strip()
        if not link:
            log(f"[PayPal-Filler][{email}] 跳过：空 payment_link")
            continue
        worker_id = ((index - 1) % worker_count) + 1
        raw_proxy = proxy_pool.pick(worker_id) if proxy_pool else fallback_proxy
        proxy = _normalize_proxy(raw_proxy)
        log(f"[PayPal-Filler][{email}] 启动 ({index}/{target}) | worker={worker_id} | proxy={'on' if proxy else 'off'}")
        code = _run_one(link, proxy, headless=headless)
        if code == 0:
            save_pending_auth(email, query_code, account_line=account_line)
            remove_from_link_pool(email)
            success += 1
            log(f"[PayPal-Filler][{email}] 支付成功，已写入待授权账号")
        else:
            paypal_flow_state.mark_stage_failure(email, stage="flow2_filler", reason=f"exit_code={code}")
            log(f"[PayPal-Filler][{email}] 失败，exit_code={code}")
    return success

