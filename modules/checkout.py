from __future__ import annotations

import asyncio
import re
import uuid
from typing import Any

import requests
from playwright.async_api import Page

try:
    from curl_cffi.requests import Session as CffiSession
except Exception:  # pragma: no cover - optional runtime dependency
    CffiSession = None


CHECKOUT_REGION_BILLING: dict[str, tuple[str, str]] = {
    "us": ("US", "USD"),
    # JP PayPal is created as a US checkout link, then paid under JP IP/address in flow2.
    "jp": ("US", "USD"),
}

DEFAULT_STRIPE_PK = (
    "pk_live_51HOrSwC6h1nxGoI3lTAgRjYVrz4dU3fVOabyCcKR3pbEJguCVAlqCxdxCUvoRh1XWwRac"
    "ViovU3kLKvpkjh7IqkW00iXQsjo3n"
)
DEFAULT_STRIPE_VERSION = (
    "2025-03-31.basil; checkout_server_update_beta=v1; "
    "checkout_manual_approval_preview=v1"
)
DEFAULT_STRIPE_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
)
DEFAULT_STRIPE_FALLBACK_PROXY = "http://127.0.0.1:7897"


def normalize_checkout_region(value: str | None) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_")
    if normalized in {"jp", "japan", "jpn"}:
        return "jp"
    return "us"


def checkout_billing_for_region(value: str | None) -> dict[str, str]:
    country, currency = CHECKOUT_REGION_BILLING[normalize_checkout_region(value)]
    return {"country": country, "currency": currency}


def _log_checkout(message: str) -> None:
    print(f"[checkout] {message}", flush=True)


def _walk_values(value: Any):
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_values(item)
    else:
        yield value


def _extract_checkout_session_id(data: Any) -> str:
    if isinstance(data, dict):
        for key in ("checkout_session_id", "cs_id", "session_id", "id"):
            value = str(data.get(key) or "").strip()
            if value.startswith(("cs_live_", "cs_test_")):
                return value
        custom_session = data.get("custom_checkout_session")
        if custom_session:
            extracted = _extract_checkout_session_id(custom_session)
            if extracted:
                return extracted
    for value in _walk_values(data):
        if not isinstance(value, str):
            continue
        match = re.search(r"\bcs_(?:live|test)_[A-Za-z0-9]+", value)
        if match:
            return match.group(0)
    return ""


def _extract_publishable_key(data: Any) -> str:
    if isinstance(data, dict):
        for key in ("publishable_key", "stripe_publishable_key", "key"):
            value = str(data.get(key) or "").strip()
            if value.startswith(("pk_live_", "pk_test_")):
                return value
    for value in _walk_values(data):
        if not isinstance(value, str):
            continue
        match = re.search(r"\bpk_(?:live|test)_[A-Za-z0-9]+", value)
        if match:
            return match.group(0)
    return ""


def _new_http_session(proxy: str | None = None):
    if CffiSession is not None:
        session = CffiSession(impersonate="chrome136")
    else:
        session = requests.Session()
        session.headers["User-Agent"] = DEFAULT_STRIPE_UA
    if proxy:
        session.proxies = {"http": proxy, "https": proxy}
    return session


def _stripe_init_hosted_url(
    checkout_session_id: str,
    publishable_key: str,
    *,
    payment_locale: str = "en",
    user_agent: str = "",
    proxy: str | None = None,
) -> str:
    stripe_js_id = str(uuid.uuid4())
    body = {
        "browser_locale": "en-US",
        "browser_timezone": "Asia/Shanghai",
        "elements_session_client[client_betas][0]": "custom_checkout_server_updates_1",
        "elements_session_client[client_betas][1]": "custom_checkout_manual_approval_1",
        "elements_session_client[elements_init_source]": "custom_checkout",
        "elements_session_client[referrer_host]": "chatgpt.com",
        "elements_session_client[stripe_js_id]": stripe_js_id,
        "elements_session_client[locale]": payment_locale,
        "elements_session_client[is_aggregation_expected]": "false",
        "elements_options_client[saved_payment_method][enable_save]": "auto",
        "elements_options_client[saved_payment_method][enable_redisplay]": "auto",
        "key": publishable_key,
        "_stripe_version": DEFAULT_STRIPE_VERSION,
    }
    headers = {
        "Authorization": f"Bearer {publishable_key}",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": user_agent or DEFAULT_STRIPE_UA,
    }
    session = _new_http_session(proxy=proxy)
    response = session.post(
        f"https://api.stripe.com/v1/payment_pages/{checkout_session_id}/init",
        data=body,
        headers=headers,
        timeout=30,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Stripe init failed: HTTP {response.status_code} {response.text[:500]}")
    data = response.json()
    hosted_url = str(
        data.get("stripe_hosted_url")
        or data.get("hosted_url")
        or data.get("url")
        or ""
    ).strip()
    if not hosted_url:
        raise RuntimeError(f"Stripe init response has no hosted URL: {data}")
    if "checkout.stripe.com" in hosted_url:
        return hosted_url.replace("checkout.stripe.com", "pay.openai.com")
    return hosted_url


async def _create_plus_checkout_link_fallback(
    checkout_data: dict[str, Any],
    *,
    proxy: str | None = None,
) -> str:
    checkout_session_id = _extract_checkout_session_id(checkout_data)
    if not checkout_session_id:
        raise RuntimeError(f"fallback missing checkout session id: {checkout_data}")
    publishable_key = _extract_publishable_key(checkout_data) or DEFAULT_STRIPE_PK
    proxy_candidates: list[str | None] = []
    for candidate in (proxy, DEFAULT_STRIPE_FALLBACK_PROXY, None):
        normalized = str(candidate or "").strip() or None
        if normalized not in proxy_candidates:
            proxy_candidates.append(normalized)
    errors: list[str] = []
    for index, candidate in enumerate(proxy_candidates, start=1):
        label = candidate or "direct"
        try:
            _log_checkout(f"fallback stripe init attempt {index}/{len(proxy_candidates)}: cs={checkout_session_id}, proxy={label}")
            link = await asyncio.to_thread(
                _stripe_init_hosted_url,
                checkout_session_id,
                publishable_key,
                proxy=candidate,
            )
            _log_checkout(f"fallback stripe init ok: proxy={label}")
            return link
        except Exception as exc:
            error = f"proxy={label}: {exc}"
            errors.append(error)
            _log_checkout(f"fallback stripe init failed: {error}")
    raise RuntimeError("; ".join(errors))


async def get_chatgpt_session(page: Page) -> dict[str, Any]:
    data = await page.evaluate(
        """async () => {
            const r = await fetch('/api/auth/session', { credentials: 'include' });
            return await r.json();
        }"""
    )
    if not isinstance(data, dict):
        raise RuntimeError("无法获取 ChatGPT session，当前页面可能未登录 ChatGPT")
    return data


async def get_access_token(page: Page) -> str:
    data = await get_chatgpt_session(page)
    token = data.get("accessToken")
    if not token:
        raise RuntimeError("无法获取 accessToken，当前页面可能未登录 ChatGPT")
    return str(token)


async def create_plus_checkout_link(
    page: Page,
    access_token: str,
    cfg: dict[str, Any],
    *,
    checkout_region: str | None = None,
    proxy: str | None = None,
) -> str:
    if checkout_region is not None:
        billing_details = checkout_billing_for_region(checkout_region)
    else:
        billing_details = {
            "country": cfg["billing_country"],
            "currency": cfg["currency"],
        }
    payload = {
        "plan_name": cfg["plan_name"],
        "billing_details": billing_details,
        "cancel_url": cfg["cancel_url"],
        "promo_campaign": {
            "promo_campaign_id": cfg["promo_campaign_id"],
            "is_coupon_from_query_param": False,
        },
        "checkout_ui_mode": cfg["checkout_ui_mode"],
    }
    data = await page.evaluate(
        """async ({ accessToken, payload }) => {
            const r = await fetch('https://chatgpt.com/backend-api/payments/checkout', {
                method: 'POST',
                headers: {
                    Authorization: `Bearer ${accessToken}`,
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(payload)
            });
            const text = await r.text();
            let data = null;
            try { data = JSON.parse(text); } catch (e) { data = { raw: text }; }
            return { ok: r.ok, status: r.status, data };
        }""",
        {"accessToken": access_token, "payload": payload},
    )
    body = data.get("data") or {}
    if not data.get("ok"):
        try:
            return await _create_plus_checkout_link_fallback(body, proxy=proxy)
        except Exception as fallback_exc:
            raise RuntimeError(
                f"生成长链接失败: HTTP {data.get('status')} {body}; fallback failed: {fallback_exc}"
            ) from fallback_exc
    link = body.get("url") or body.get("stripe_hosted_url") or body.get("checkout_url")
    if not link:
        try:
            return await _create_plus_checkout_link_fallback(body, proxy=proxy)
        except Exception as fallback_exc:
            raise RuntimeError(f"生成长链接响应里没有 url: {body}; fallback failed: {fallback_exc}") from fallback_exc
    return link
