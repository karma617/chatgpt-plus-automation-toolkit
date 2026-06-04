from __future__ import annotations

import asyncio
import base64
import re
import uuid
from typing import Any
from urllib.parse import urljoin

import requests
from playwright.async_api import Page

from .utils import load_env

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
DEFAULT_EXTERNAL_CHECKOUT_TIMEOUT = 60
DEFAULT_EXTERNAL_CHECKOUT_API_URL = "https://payurl.ark2.cn/api/checkout"
DEFAULT_EXTERNAL_CHECKOUT_LINK_TYPE = "paypal"
DEFAULT_EXTERNAL_CHECKOUT_LANGUAGE = "en"
DEFAULT_EXTERNAL_CHECKOUT_COUNTRY = "US"
DEFAULT_EXTERNAL_CHECKOUT_CURRENCY = "USD"
DEFAULT_EXTERNAL_CHECKOUT_DEFAULT_PROXY_ID = "default"
DEFAULT_EXTERNAL_CHECKOUT_PROMO_CODE = "STRIPEATLASGPT4BIZ050126"
CHATGPT_CHECKOUT_URL = "https://chatgpt.com/backend-api/payments/checkout"
LOCAL_GENERATOR_COUNTRIES = {
    "US": "USD",
    "GB": "GBP",
    "CA": "CAD",
    "AU": "AUD",
    "JP": "JPY",
    "SG": "SGD",
    "HK": "HKD",
    "TW": "TWD",
    "KR": "KRW",
    "ID": "IDR",
    "MY": "MYR",
    "TH": "THB",
    "VN": "VND",
    "PH": "PHP",
    "IN": "INR",
    "DE": "EUR",
    "FR": "EUR",
    "IT": "EUR",
    "ES": "EUR",
    "NL": "EUR",
    "IE": "EUR",
    "PT": "EUR",
    "BE": "EUR",
    "FI": "EUR",
    "AT": "EUR",
    "CH": "CHF",
    "SE": "SEK",
    "NO": "NOK",
    "DK": "DKK",
    "PL": "PLN",
    "CZ": "CZK",
    "MX": "MXN",
    "BR": "BRL",
    "NZ": "NZD",
}
LOCAL_GENERATOR_LOCKED_COUNTRY_BY_TYPE = {"hosted": "US", "paypal": "JP", "gopay": "ID"}
CHECKOUT_LINK_TYPES = {"hosted", "paypal", "gopay"}
CHECKOUT_UI_MODES = {"hosted", "custom", "redirect"}
COUNTRY_ALIASES = {
    "us": "US",
    "usa": "US",
    "unitedstates": "US",
    "america": "US",
    "\u7f8e\u56fd": "US",
    "\u7f8e\u533a": "US",
    "jp": "JP",
    "jpn": "JP",
    "japan": "JP",
    "\u65e5\u672c": "JP",
    "\u65e5\u533a": "JP",
    "de": "DE",
    "deu": "DE",
    "germany": "DE",
    "\u5fb7\u56fd": "DE",
    "\u5fb7\u533a": "DE",
    "id": "ID",
    "idn": "ID",
    "indonesia": "ID",
    "\u5370\u5c3c": "ID",
    "\u5370\u5ea6\u5c3c\u897f\u4e9a": "ID",
    "gb": "GB",
    "uk": "GB",
    "unitedkingdom": "GB",
    "\u82f1\u56fd": "GB",
    "ca": "CA",
    "canada": "CA",
    "\u52a0\u62ff\u5927": "CA",
    "au": "AU",
    "australia": "AU",
    "\u6fb3\u5927\u5229\u4e9a": "AU",
    "sg": "SG",
    "singapore": "SG",
    "\u65b0\u52a0\u5761": "SG",
    "hk": "HK",
    "hongkong": "HK",
    "\u9999\u6e2f": "HK",
    "tw": "TW",
    "taiwan": "TW",
    "\u53f0\u6e7e": "TW",
    "kr": "KR",
    "korea": "KR",
    "\u97e9\u56fd": "KR",
    "my": "MY",
    "malaysia": "MY",
    "\u9a6c\u6765\u897f\u4e9a": "MY",
    "th": "TH",
    "thailand": "TH",
    "\u6cf0\u56fd": "TH",
}
CURRENCY_ALIASES = {
    "\u7f8e\u5143": "USD",
    "\u65e5\u5143": "JPY",
    "\u65e5\u5e01": "JPY",
    "\u6b27\u5143": "EUR",
    "\u5370\u5c3c\u76fe": "IDR",
    "\u82f1\u9551": "GBP",
    "\u52a0\u5143": "CAD",
    "\u6fb3\u5143": "AUD",
}
CHECKOUT_METHOD_AUTO = "auto"
CHECKOUT_METHOD_EXTERNAL_API = "external_api"
CHECKOUT_METHOD_LOCAL_SERVICE = "local_service"
CHECKOUT_METHOD_LOCAL_GENERATOR = "local_generator"
CHECKOUT_METHOD_HOSTED_URL_HELPER = "hosted_url_helper"
CHECKOUT_METHOD_BROWSER_CHECKOUT = "browser_checkout"
CHECKOUT_METHOD_ORDER = (
    CHECKOUT_METHOD_EXTERNAL_API,
    CHECKOUT_METHOD_LOCAL_SERVICE,
    CHECKOUT_METHOD_HOSTED_URL_HELPER,
    CHECKOUT_METHOD_LOCAL_GENERATOR,
    CHECKOUT_METHOD_BROWSER_CHECKOUT,
)


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


def _normalize_pay_url(url: str) -> str:
    value = str(url or "").strip()
    if "checkout.stripe.com" in value:
        return value.replace("checkout.stripe.com", "pay.openai.com")
    return value


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


def _normalize_proxy(value: str | None) -> str:
    proxy = str(value or "").strip()
    if not proxy:
        return ""
    if "://" not in proxy:
        proxy = "http://" + proxy
    if not re.match(r"^(https?|socks4a?|socks5h?)://", proxy, re.I):
        raise ValueError(f"unsupported proxy scheme: {proxy}")
    return proxy


def _proxy_scheme_candidates(value: str | None) -> list[str]:
    proxy = _normalize_proxy(value)
    if not proxy:
        return []
    match = re.match(r"^([a-z0-9+.-]+)://(.+)$", proxy, re.I)
    if not match:
        return [proxy]
    first_scheme = match.group(1).lower()
    rest = match.group(2)
    schemes = [first_scheme]
    for scheme in ("socks5h", "socks5", "http", "https"):
        if scheme not in schemes:
            schemes.append(scheme)
    return [f"{scheme}://{rest}" for scheme in schemes]


def _local_service_proxy_candidates(proxy: str | None, env: dict[str, str]) -> list[str]:
    values: list[str] = []
    for raw in (
        proxy,
        env.get("PAYPAL_CHECKOUT_FALLBACK_PROXY"),
        DEFAULT_STRIPE_FALLBACK_PROXY,
    ):
        for candidate in _proxy_scheme_candidates(str(raw or "").strip()):
            if candidate and candidate not in values:
                values.append(candidate)
    return values


def _looks_like_cloudflare_challenge(text: str) -> bool:
    lowered = (text or "").lower()
    return (
        "_cf_chl_opt" in lowered
        or "enable javascript and cookies to continue" in lowered
        or "cf-chl" in lowered
    )


def _request_headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Origin": "https://chatgpt.com",
        "Referer": "https://chatgpt.com/",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "User-Agent": DEFAULT_STRIPE_UA,
    }


def _stripe_init_hosted_url(
    checkout_session_id: str,
    publishable_key: str,
    *,
    payment_locale: str = "en",
    user_agent: str = "",
    device_id: str = "",
    proxy: str | None = None,
) -> str:
    stripe_js_id = str(device_id or "").strip() or str(uuid.uuid4())
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
    return _normalize_pay_url(hosted_url)


def _external_checkout_api_url(env: dict[str, str]) -> str:
    raw = str(env.get("PAYPAL_CHECKOUT_FALLBACK_API_URL") or DEFAULT_EXTERNAL_CHECKOUT_API_URL).strip()
    if not raw or raw.lower() in {"0", "false", "no", "off", "disabled", "none"}:
        return ""
    if raw.rstrip("/").endswith("/api/checkout"):
        return raw
    return urljoin(raw.rstrip("/") + "/", "api/checkout")


def _normalize_checkout_method(value: str | None) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_")
    aliases = {
        "": CHECKOUT_METHOD_AUTO,
        "default": CHECKOUT_METHOD_AUTO,
        "fallback": CHECKOUT_METHOD_AUTO,
        "\u81ea\u52a8\u515c\u5e95": CHECKOUT_METHOD_AUTO,
        "external": CHECKOUT_METHOD_EXTERNAL_API,
        "cloud": CHECKOUT_METHOD_EXTERNAL_API,
        "api": CHECKOUT_METHOD_EXTERNAL_API,
        "xjm_cloud": CHECKOUT_METHOD_EXTERNAL_API,
        "\u5c0f\u9e21\u6bdb\u306e\u516c\u76ca\u4e91\u7aef": CHECKOUT_METHOD_EXTERNAL_API,
        "\u5c0f\u9e21\u6bdb\u516c\u76ca\u4e91\u7aef": CHECKOUT_METHOD_EXTERNAL_API,
        "local_service": CHECKOUT_METHOD_LOCAL_SERVICE,
        "service": CHECKOUT_METHOD_LOCAL_SERVICE,
        "xjm_local_service": CHECKOUT_METHOD_LOCAL_SERVICE,
        "\u5c0f\u9e21\u6bdb\u306e\u516c\u76ca\u672c\u5730\u670d\u52a1": CHECKOUT_METHOD_LOCAL_SERVICE,
        "\u5c0f\u9e21\u6bdb\u516c\u76ca\u672c\u5730\u670d\u52a1": CHECKOUT_METHOD_LOCAL_SERVICE,
        "local_generator": CHECKOUT_METHOD_LOCAL_GENERATOR,
        "oaipayy": CHECKOUT_METHOD_LOCAL_GENERATOR,
        "local_long_link": CHECKOUT_METHOD_LOCAL_GENERATOR,
        "\u672c\u5730\u652f\u4ed8\u957f\u94fe\u751f\u6210\u5668": CHECKOUT_METHOD_LOCAL_GENERATOR,
        "hosted_url_helper": CHECKOUT_METHOD_HOSTED_URL_HELPER,
        "hosted_helper": CHECKOUT_METHOD_HOSTED_URL_HELPER,
        "cpa_sub_hosted_url_helper": CHECKOUT_METHOD_HOSTED_URL_HELPER,
        "hosted-url-helper": CHECKOUT_METHOD_HOSTED_URL_HELPER,
        "\u672c\u5730 hosted-url-helper": CHECKOUT_METHOD_HOSTED_URL_HELPER,
        "\u672c\u5730hostedurlhelper": CHECKOUT_METHOD_HOSTED_URL_HELPER,
        "hostedurlhelper": CHECKOUT_METHOD_HOSTED_URL_HELPER,
        "browser": CHECKOUT_METHOD_BROWSER_CHECKOUT,
        "browser_checkout": CHECKOUT_METHOD_BROWSER_CHECKOUT,
        "original": CHECKOUT_METHOD_BROWSER_CHECKOUT,
        "builtin": CHECKOUT_METHOD_BROWSER_CHECKOUT,
        "\u672c\u9879\u76ee\u6700\u521d\u7684\u751f\u6210\u5668": CHECKOUT_METHOD_BROWSER_CHECKOUT,
    }
    return aliases.get(normalized, normalized if normalized in {CHECKOUT_METHOD_AUTO, *CHECKOUT_METHOD_ORDER} else CHECKOUT_METHOD_AUTO)


def checkout_method_from_env(env: dict[str, str]) -> str:
    return _normalize_checkout_method(
        env.get("PAYPAL_CHECKOUT_METHOD")
        or env.get("PAYPAL_CHECKOUT_LINK_METHOD")
        or env.get("PAYPAL_CHECKOUT_GENERATOR")
    )


def _checkout_method_plan(env: dict[str, str], skip_methods: set[str]) -> list[str]:
    selected = checkout_method_from_env(env)
    if selected == CHECKOUT_METHOD_AUTO:
        plan = list(CHECKOUT_METHOD_ORDER)
        return [method for method in plan if method not in skip_methods]
    else:
        # An explicit env/UI selection should be honored even if this account
        # previously marked the same method as bad.
        return [selected]


def _checkout_method_label(method: str) -> str:
    return {
        CHECKOUT_METHOD_EXTERNAL_API: "xjm_cloud",
        CHECKOUT_METHOD_LOCAL_SERVICE: "xjm_local_service",
        CHECKOUT_METHOD_LOCAL_GENERATOR: "local_generator",
        CHECKOUT_METHOD_HOSTED_URL_HELPER: "hosted_url_helper",
        CHECKOUT_METHOD_BROWSER_CHECKOUT: "browser_checkout",
    }.get(method, method)


def _env_int(env: dict[str, str], key: str, default: int) -> int:
    try:
        value = int(str(env.get(key) or "").strip())
        return value if value > 0 else default
    except Exception:
        return default


def _compact_key(value: str | None) -> str:
    return re.sub(r"[\s_\-()/（）【】\[\]:：]+", "", str(value or "").strip().lower())


def _normalize_checkout_plan(value: str | None, default: str = "plus") -> str:
    normalized = _compact_key(value)
    aliases = {
        "": default,
        "auto": default,
        "default": default,
        "plus": "plus",
        "chatgptplus": "plus",
        "chatgptplusplan": "plus",
        "\u4e2a\u4ebaplus": "plus",
        "\u4e2a\u4eba\u7248": "plus",
        "\u4e2a\u4eba": "plus",
        "team": "team",
        "business": "team",
        "chatgptteam": "team",
        "chatgptteamplan": "team",
        "\u56e2\u961f": "team",
        "\u56e2\u961f\u7248": "team",
    }
    return aliases.get(normalized, normalized if normalized in {"plus", "team"} else default)


def _normalize_link_type(value: str | None, default: str) -> str:
    normalized = _compact_key(value)
    aliases = {
        "": default,
        "auto": default,
        "default": default,
        "hosted": "hosted",
        "stripe": "hosted",
        "stripehosted": "hosted",
        "\u652f\u4ed8\u957f\u94fe": "hosted",
        "\u9ed8\u8ba4\u7f8e\u56fd": "hosted",
        "\u652f\u4ed8\u957f\u94fe\u9ed8\u8ba4\u7f8e\u56fd": "hosted",
        "paypal": "paypal",
        "pp": "paypal",
        "pplink": "paypal",
        "\u65e5\u672c\u5730\u5740": "paypal",
        "pp\u94fe\u63d0\u53d6\u65e5\u672c\u5730\u5740": "paypal",
        "paypal\u94fe\u63d0\u53d6\u65e5\u672c\u5730\u5740": "paypal",
        "gopay": "gopay",
        "\u5370\u5c3c\u5730\u5740": "gopay",
        "gopay\u94fe\u63d0\u53d6\u5370\u5c3c\u5730\u5740": "gopay",
    }
    return aliases.get(normalized, normalized if normalized in CHECKOUT_LINK_TYPES else default)


def _normalize_payment_locale(value: str | None, default: str = DEFAULT_EXTERNAL_CHECKOUT_LANGUAGE) -> str:
    normalized = _compact_key(value)
    aliases = {
        "": default,
        "auto": default,
        "default": default,
        "en": "en",
        "english": "en",
        "\u82f1\u6587": "en",
        "zh": "zh",
        "zhcn": "zh-CN",
        "zh_cn": "zh-CN",
        "zh-cn": "zh-CN",
        "\u7b80\u4f53\u4e2d\u6587": "zh-CN",
        "\u4e2d\u6587\u7b80\u4f53": "zh-CN",
        "zhtw": "zh-TW",
        "zh_tw": "zh-TW",
        "zh-tw": "zh-TW",
        "\u7e41\u4f53\u4e2d\u6587": "zh-TW",
        "\u4e2d\u6587\u7e41\u4f53": "zh-TW",
        "ja": "ja",
        "jp": "ja",
        "japanese": "ja",
        "\u65e5\u6587": "ja",
        "ko": "ko",
        "korean": "ko",
        "\u97e9\u6587": "ko",
        "de": "de",
        "german": "de",
        "\u5fb7\u6587": "de",
        "fr": "fr",
        "french": "fr",
        "\u6cd5\u6587": "fr",
        "es": "es",
        "spanish": "es",
        "\u897f\u73ed\u7259\u6587": "es",
        "id": "id",
        "indonesian": "id",
        "\u5370\u5c3c\u6587": "id",
        "pt": "pt",
        "portuguese": "pt",
        "\u8461\u8404\u7259\u6587": "pt",
    }
    return aliases.get(normalized, str(value or default).strip() or default)


def _normalize_country(value: str | None, default: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return default.upper()
    compact = _compact_key(raw)
    if compact in COUNTRY_ALIASES:
        return COUNTRY_ALIASES[compact]
    letters = re.sub(r"[^A-Za-z]", "", raw).upper()
    if len(letters) == 2:
        return letters
    return default.upper()


def _normalize_currency(value: str | None, default: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return default.upper()
    compact = _compact_key(raw)
    if compact in CURRENCY_ALIASES:
        return CURRENCY_ALIASES[compact]
    letters = re.sub(r"[^A-Za-z]", "", raw).upper()
    if len(letters) == 3:
        return letters
    return default.upper()


def _normalize_checkout_ui_mode(value: str | None, default: str = "hosted") -> str:
    normalized = _compact_key(value)
    aliases = {
        "": default,
        "auto": default,
        "default": default,
        "hosted": "hosted",
        "\u6258\u7ba1\u9875": "hosted",
        "\u6258\u7ba1": "hosted",
        "custom": "custom",
        "\u81ea\u5b9a\u4e49": "custom",
        "\u5d4c\u5165": "custom",
        "redirect": "redirect",
        "\u8df3\u8f6c": "redirect",
    }
    return aliases.get(normalized, normalized if normalized in CHECKOUT_UI_MODES else default)


def _plan_for_external_checkout(cfg: dict[str, Any]) -> str:
    plan_name = str(cfg.get("plan_name") or "").lower()
    return "team" if "team" in plan_name else "plus"


def _external_checkout_plan(env: dict[str, str], cfg: dict[str, Any]) -> str:
    return _normalize_checkout_plan(env.get("PAYPAL_CHECKOUT_FALLBACK_PLAN"), _plan_for_external_checkout(cfg))


def _external_checkout_provider_error(data: dict[str, Any]) -> str:
    parts = [
        str(data.get("provider_error") or ""),
        str(data.get("extract_status") or ""),
        str(data.get("message") or ""),
    ]
    text = " ".join(part.strip() for part in parts if part.strip()).lower()
    provider_redirect = str(data.get("provider_redirect_url") or "").strip()
    if (
        bool(data.get("fallback"))
        and not provider_redirect
        and (
            "provider redirect" in text
            or "provider_redirect" in text
            or "no provider" in text
            or "\u6ca1\u6709 provider redirect" in text
        )
    ):
        return str(data.get("provider_error") or data.get("extract_status") or "external checkout provider redirect missing")
    if (
        str(data.get("skip_stripe_page") or "").strip().lower() in {"1", "true", "yes", "on"}
        and str(data.get("link_type") or "").strip().lower() in {"paypal", "gopay"}
        and str(data.get("payment_method_type") or "").strip().lower() in {"paypal", "gopay"}
        and not provider_redirect
        and text
        and ("confirm" in text or "redirect" in text)
    ):
        return str(data.get("provider_error") or data.get("extract_status") or "external checkout provider redirect missing")
    return ""


def _extract_external_checkout_link(data: Any) -> str:
    if isinstance(data, dict):
        provider_error = _external_checkout_provider_error(data)
        if provider_error:
            raise RuntimeError(f"external checkout provider redirect missing: {provider_error}")
        for key in (
            "preferredCheckoutUrl",
            "hostedCheckoutUrl",
            "convertedCheckoutUrl",
            "chatgptCheckoutUrl",
            "checkoutUrl",
            "provider_redirect_url",
            "long_url",
            "url",
            "openai_payurl",
            "stripe_hosted_url",
            "checkout_url",
        ):
            value = _normalize_pay_url(str(data.get(key) or "").strip())
            if value.startswith("http"):
                return value
    for value in _walk_values(data):
        if not isinstance(value, str):
            continue
        normalized = _normalize_pay_url(value)
        if normalized.startswith("http") and ("pay.openai.com" in normalized or "checkout.stripe.com" in normalized):
            return normalized
    return ""


def _local_service_checkout_payload(
    cfg: dict[str, Any],
    billing_details: dict[str, str],
    env: dict[str, str],
) -> dict[str, Any]:
    plan = _plan_for_external_checkout(cfg)
    payload: dict[str, Any] = {
        "plan_name": "chatgptteamplan" if plan == "team" else "chatgptplusplan",
        "billing_details": {
            "country": str(billing_details.get("country") or DEFAULT_EXTERNAL_CHECKOUT_COUNTRY).upper(),
            "currency": str(billing_details.get("currency") or DEFAULT_EXTERNAL_CHECKOUT_CURRENCY).upper(),
        },
        "checkout_ui_mode": str(cfg.get("checkout_ui_mode") or "hosted").strip() or "hosted",
    }
    if plan == "team":
        promo_code = str(env.get("PAYPAL_CHECKOUT_FALLBACK_PROMO_CODE") or DEFAULT_EXTERNAL_CHECKOUT_PROMO_CODE).strip()
        if promo_code:
            payload["cancel_url"] = f"https://chatgpt.com/?promoCode={promo_code}"
            payload["promo_code"] = promo_code
        else:
            payload["cancel_url"] = str(cfg.get("cancel_url") or "https://chatgpt.com/#pricing")
        payload["team_plan_data"] = {
            "workspace_name": str(env.get("PAYPAL_CHECKOUT_FALLBACK_WORKSPACE_NAME") or "linux-do").strip() or "linux-do",
            "price_interval": "month",
            "seat_quantity": max(2, _env_int(env, "PAYPAL_CHECKOUT_FALLBACK_SEAT_QUANTITY", 2)),
        }
    else:
        payload["cancel_url"] = str(cfg.get("cancel_url") or "https://chatgpt.com/#pricing")
        payload["promo_campaign"] = {
            "promo_campaign_id": str(cfg.get("promo_campaign_id") or "plus-1-month-free"),
            "is_coupon_from_query_param": True,
        }
    return payload


def _local_generator_link_type(env: dict[str, str], checkout_region: str | None = None) -> str:
    raw = str(env.get("PAYPAL_CHECKOUT_LOCAL_GENERATOR_LINK_TYPE") or "").strip().lower()
    if raw in {"hosted", "paypal", "gopay"}:
        return raw
    if normalize_checkout_region(checkout_region) == "jp":
        return "paypal"
    return "hosted"


def _local_generator_billing_details(
    billing_details: dict[str, str],
    *,
    link_type: str,
) -> dict[str, str]:
    if link_type != "hosted":
        country = LOCAL_GENERATOR_LOCKED_COUNTRY_BY_TYPE.get(link_type, "US")
    else:
        country = str(billing_details.get("country") or "US").upper()
    return {"country": country, "currency": LOCAL_GENERATOR_COUNTRIES.get(country, "USD")}


def _local_generator_checkout_payload(
    cfg: dict[str, Any],
    billing_details: dict[str, str],
    env: dict[str, str],
    *,
    checkout_region: str | None = None,
) -> tuple[dict[str, Any], dict[str, str], str]:
    link_type = _local_generator_link_type(env, checkout_region)
    local_billing = _local_generator_billing_details(billing_details, link_type=link_type)
    payload: dict[str, Any] = {
        "plan_name": "chatgptplusplan",
        "billing_details": local_billing,
        "cancel_url": str(cfg.get("cancel_url") or "https://chatgpt.com/#pricing"),
        "checkout_ui_mode": str(env.get("PAYPAL_CHECKOUT_LOCAL_GENERATOR_UI_MODE") or cfg.get("checkout_ui_mode") or "hosted").strip()
        or "hosted",
    }
    if link_type == "gopay":
        payload["promo_campaign"] = {
            "promo_campaign_id": str(cfg.get("promo_campaign_id") or "plus-1-month-free"),
            "is_coupon_from_query_param": False,
        }
    return payload, local_billing, link_type


def _call_chatgpt_checkout_sync(
    access_token: str,
    payload: dict[str, Any],
    *,
    proxy: str,
) -> dict[str, Any]:
    session = _new_http_session(proxy=proxy)
    response = session.post(
        CHATGPT_CHECKOUT_URL,
        json=payload,
        headers=_request_headers(access_token),
        timeout=30,
    )
    text = response.text or ""
    if _looks_like_cloudflare_challenge(text):
        raise RuntimeError("local service checkout blocked by Cloudflare challenge")
    try:
        data = response.json()
    except Exception:
        data = {"raw": text[:1000]}
    if response.status_code < 200 or response.status_code >= 300:
        raise RuntimeError(f"local service checkout HTTP {response.status_code}: {text[:500]}")
    if isinstance(data, dict):
        data["proxy_used"] = proxy
    return data if isinstance(data, dict) else {"raw": data}


def _create_local_service_checkout_link_sync(
    access_token: str,
    cfg: dict[str, Any],
    billing_details: dict[str, str],
    *,
    env: dict[str, str],
    proxy: str | None = None,
) -> str:
    payload = _local_service_checkout_payload(cfg, billing_details, env)
    proxy_candidates = _local_service_proxy_candidates(proxy, env)
    if not proxy_candidates:
        raise RuntimeError("local service checkout has no proxy candidate")
    errors: list[str] = []
    for index, candidate in enumerate(proxy_candidates, start=1):
        label = candidate or "direct"
        try:
            _log_checkout(f"local service checkout attempt {index}/{len(proxy_candidates)}: proxy={label}")
            data = _call_chatgpt_checkout_sync(access_token, payload, proxy=candidate)
            link = _extract_external_checkout_link(data)
            if link:
                _log_checkout(f"local service checkout ok: proxy={label}")
                return link
            checkout_session_id = _extract_checkout_session_id(data)
            if not checkout_session_id:
                raise RuntimeError(f"local service checkout response has no link/session: {data}")
            publishable_key = _extract_publishable_key(data) or DEFAULT_STRIPE_PK
            _log_checkout(f"local service stripe init attempt: cs={checkout_session_id}, proxy={label}")
            link = _stripe_init_hosted_url(checkout_session_id, publishable_key, proxy=candidate)
            _log_checkout(f"local service stripe init ok: proxy={label}")
            return link
        except Exception as exc:
            error = f"proxy={label}: {exc}"
            errors.append(error)
            _log_checkout(f"local service checkout failed: {error}")
    raise RuntimeError("; ".join(errors))


async def _create_local_service_checkout_link(
    access_token: str,
    cfg: dict[str, Any],
    billing_details: dict[str, str],
    *,
    proxy: str | None = None,
    env: dict[str, str] | None = None,
) -> str:
    actual_env = env if env is not None else load_env(".env")
    return await asyncio.to_thread(
        _create_local_service_checkout_link_sync,
        access_token,
        cfg,
        billing_details,
        env=actual_env,
        proxy=proxy,
    )


def _create_local_generator_checkout_link_sync(
    access_token: str,
    cfg: dict[str, Any],
    billing_details: dict[str, str],
    *,
    env: dict[str, str],
    proxy: str | None = None,
    checkout_region: str | None = None,
) -> str:
    payload, local_billing, link_type = _local_generator_checkout_payload(
        cfg,
        billing_details,
        env,
        checkout_region=checkout_region,
    )
    proxy_candidates = _local_service_proxy_candidates(proxy, env)
    if not proxy_candidates:
        raise RuntimeError("local generator checkout has no proxy candidate")
    payment_locale = str(env.get("PAYPAL_CHECKOUT_LOCAL_GENERATOR_PAYMENT_LOCALE") or DEFAULT_EXTERNAL_CHECKOUT_LANGUAGE).strip() or DEFAULT_EXTERNAL_CHECKOUT_LANGUAGE
    user_agent = str(env.get("PAYPAL_CHECKOUT_LOCAL_GENERATOR_USER_AGENT") or "").strip()
    publishable_key = str(env.get("PAYPAL_CHECKOUT_LOCAL_GENERATOR_STRIPE_PK") or "").strip()
    errors: list[str] = []
    for index, candidate in enumerate(proxy_candidates, start=1):
        label = candidate or "direct"
        try:
            _log_checkout(
                f"local generator checkout attempt {index}/{len(proxy_candidates)}: "
                f"link_type={link_type} billing={local_billing['country']}/{local_billing['currency']} proxy={label}"
            )
            data = _call_chatgpt_checkout_sync(access_token, payload, proxy=candidate)
            checkout_session_id = _extract_checkout_session_id(data)
            if not checkout_session_id:
                raise RuntimeError(f"local generator response has no checkout_session_id: {data}")
            pk = _extract_publishable_key(data) or publishable_key or DEFAULT_STRIPE_PK
            link = _stripe_init_hosted_url(
                checkout_session_id,
                pk,
                payment_locale=payment_locale,
                user_agent=user_agent,
                proxy=candidate,
            )
            _log_checkout(f"local generator checkout ok: proxy={label}")
            return link
        except Exception as exc:
            error = f"proxy={label}: {exc}"
            errors.append(error)
            _log_checkout(f"local generator checkout failed: {error}")
    raise RuntimeError("; ".join(errors))


async def _create_local_generator_checkout_link(
    access_token: str,
    cfg: dict[str, Any],
    billing_details: dict[str, str],
    *,
    proxy: str | None = None,
    env: dict[str, str] | None = None,
    checkout_region: str | None = None,
) -> str:
    actual_env = env if env is not None else load_env(".env")
    return await asyncio.to_thread(
        _create_local_generator_checkout_link_sync,
        access_token,
        cfg,
        billing_details,
        env=actual_env,
        proxy=proxy,
        checkout_region=checkout_region,
    )


def encode_hosted_url_helper_fragment(value: dict[str, Any]) -> str:
    """Match hosted-url-helper service-worker encodeStripeCheckoutState()."""
    import json

    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    xored = "".join(chr(5 ^ ord(char)) for char in raw)
    return requests.utils.quote(base64.b64encode(xored.encode("latin-1")).decode("ascii"), safe="")


def build_hosted_url_helper_pay_url(checkout_session_id: str, publishable_key: str, *, locale: str = "zh") -> str:
    session_id = str(checkout_session_id or "").strip()
    pk = str(publishable_key or "").strip()
    if not re.match(r"^cs_(?:test|live)_[A-Za-z0-9_-]+$", session_id):
        raise RuntimeError(f"hosted-url-helper invalid checkout_session_id: {session_id}")
    if not pk.startswith(("pk_live_", "pk_test_")):
        raise RuntimeError("hosted-url-helper missing publishable_key")
    fragment = encode_hosted_url_helper_fragment(
        {
            "borderStyle": "default",
            "locale": locale,
            "subscriptionUniquenessEnabled": False,
            "guacamoleVariant": "control",
            "apiKey": pk,
            "fromServer": True,
            "backgroundColor": "#ffffff",
            "layoutType": "single_item",
            "enablePlaceholders": True,
        }
    )
    return f"https://pay.openai.com/c/pay/{session_id}#{fragment}"


def _hosted_url_helper_checkout_payload(cfg: dict[str, Any]) -> dict[str, Any]:
    return {
        "plan_name": "chatgptplusplan",
        "billing_details": {
            "country": "US",
            "currency": "USD",
        },
        "cancel_url": str(cfg.get("cancel_url") or "https://chatgpt.com/#pricing"),
        "promo_campaign": {
            "promo_campaign_id": "plus-1-month-free",
            "is_coupon_from_query_param": False,
        },
        "checkout_ui_mode": "hosted",
    }


def _hosted_url_helper_locale(env: dict[str, str]) -> str:
    return _normalize_payment_locale(env.get("PAYPAL_CHECKOUT_HOSTED_HELPER_LOCALE"), "zh")


def _create_hosted_url_helper_checkout_link_sync(
    access_token: str,
    cfg: dict[str, Any],
    *,
    env: dict[str, str],
    proxy: str | None = None,
) -> str:
    proxy_candidates = _local_service_proxy_candidates(proxy, env)
    if not proxy_candidates:
        raise RuntimeError("hosted-url-helper checkout has no proxy candidate")
    payload = _hosted_url_helper_checkout_payload(cfg)
    locale = _hosted_url_helper_locale(env)
    errors: list[str] = []
    for index, candidate in enumerate(proxy_candidates, start=1):
        label = candidate or "direct"
        try:
            _log_checkout(f"hosted-url-helper checkout attempt {index}/{len(proxy_candidates)}: proxy={label}")
            data = _call_chatgpt_checkout_sync(access_token, payload, proxy=candidate)
            checkout_session_id = _extract_checkout_session_id(data)
            publishable_key = _extract_publishable_key(data) or DEFAULT_STRIPE_PK
            if not checkout_session_id:
                raise RuntimeError(f"hosted-url-helper response has no checkout_session_id: {data}")
            link = build_hosted_url_helper_pay_url(checkout_session_id, publishable_key, locale=locale)
            _log_checkout(f"hosted-url-helper checkout ok: proxy={label}")
            return link
        except Exception as exc:
            error = f"proxy={label}: {exc}"
            errors.append(error)
            _log_checkout(f"hosted-url-helper checkout failed: {error}")
    raise RuntimeError("; ".join(errors))


async def _create_hosted_url_helper_checkout_link(
    access_token: str,
    cfg: dict[str, Any],
    *,
    proxy: str | None = None,
    env: dict[str, str] | None = None,
) -> str:
    actual_env = env if env is not None else load_env(".env")
    return await asyncio.to_thread(
        _create_hosted_url_helper_checkout_link_sync,
        access_token,
        cfg,
        env=actual_env,
        proxy=proxy,
    )


def _create_external_checkout_link_sync(
    access_token: str,
    cfg: dict[str, Any],
    billing_details: dict[str, str],
    *,
    env: dict[str, str],
) -> str:
    endpoint = _external_checkout_api_url(env)
    if not endpoint:
        raise RuntimeError("external checkout fallback disabled: PAYPAL_CHECKOUT_FALLBACK_API_URL is empty")
    timeout = _env_int(env, "PAYPAL_CHECKOUT_FALLBACK_TIMEOUT", DEFAULT_EXTERNAL_CHECKOUT_TIMEOUT)
    headers = {
        "Accept": "*/*",
        "Content-Type": "application/json",
        "Origin": "https://payurl.ark2.cn",
        "Referer": "https://payurl.ark2.cn/",
        "User-Agent": DEFAULT_STRIPE_UA,
    }
    api_key = str(env.get("PAYPAL_CHECKOUT_FALLBACK_API_KEY") or "").strip()
    if api_key:
        headers["X-API-Key"] = api_key
        headers["Authorization"] = f"Bearer {api_key}"
    request_id = str(uuid.uuid4())
    country = str(billing_details.get("country") or DEFAULT_EXTERNAL_CHECKOUT_COUNTRY).upper()
    currency = str(billing_details.get("currency") or DEFAULT_EXTERNAL_CHECKOUT_CURRENCY).upper()
    proxy_url = str(env.get("PAYPAL_CHECKOUT_FALLBACK_PROXY") or "").strip()
    body = {
        "accessToken": access_token,
        "paymentMethod": DEFAULT_EXTERNAL_CHECKOUT_LINK_TYPE,
        "processorEntity": "openai_llc",
        "requestId": request_id,
        "job_id": request_id,
        "token": access_token,
        "plan": _plan_for_external_checkout(cfg),
        "link_type": DEFAULT_EXTERNAL_CHECKOUT_LINK_TYPE,
        "ui_language": DEFAULT_EXTERNAL_CHECKOUT_LANGUAGE,
        "country": country,
        "currency": currency,
        "proxy": proxy_url,
        "proxyUrl": proxy_url,
        "default_proxy_id": str(env.get("PAYPAL_CHECKOUT_FALLBACK_DEFAULT_PROXY_ID") or DEFAULT_EXTERNAL_CHECKOUT_DEFAULT_PROXY_ID).strip()
        or DEFAULT_EXTERNAL_CHECKOUT_DEFAULT_PROXY_ID,
        "stripe_proxy_id": str(env.get("PAYPAL_CHECKOUT_FALLBACK_STRIPE_PROXY_ID") or "").strip(),
        "stripe_proxy": str(env.get("PAYPAL_CHECKOUT_FALLBACK_STRIPE_PROXY") or "").strip(),
        "use_promo": True,
        "skip_stripe_page": True,
        "promo_code": str(env.get("PAYPAL_CHECKOUT_FALLBACK_PROMO_CODE") or DEFAULT_EXTERNAL_CHECKOUT_PROMO_CODE).strip()
        or DEFAULT_EXTERNAL_CHECKOUT_PROMO_CODE,
        "workspace_name": str(env.get("PAYPAL_CHECKOUT_FALLBACK_WORKSPACE_NAME") or "linux-do").strip() or "linux-do",
        "seat_quantity": _env_int(env, "PAYPAL_CHECKOUT_FALLBACK_SEAT_QUANTITY", 2),
    }
    session = _new_http_session()
    response = session.post(endpoint, json=body, headers=headers, timeout=timeout)
    text = response.text or ""
    try:
        data = response.json()
    except Exception:
        data = {"raw": text[:1000]}
    if response.status_code < 200 or response.status_code >= 300:
        raise RuntimeError(f"external checkout fallback HTTP {response.status_code}: {text[:500]}")
    if isinstance(data, dict) and (data.get("error") or data.get("detail") or data.get("message")) and not data.get("ok", True):
        raise RuntimeError(f"external checkout fallback error: {data.get('error') or data.get('detail') or data.get('message')}")
    link = _extract_external_checkout_link(data)
    if not link:
        raise RuntimeError(f"external checkout fallback response has no link: {data}")
    return link


async def _create_external_checkout_link(
    access_token: str,
    cfg: dict[str, Any],
    billing_details: dict[str, str],
    *,
    env: dict[str, str] | None = None,
) -> str:
    actual_env = env if env is not None else load_env(".env")
    endpoint = _external_checkout_api_url(actual_env)
    if not endpoint:
        raise RuntimeError("external checkout fallback disabled: PAYPAL_CHECKOUT_FALLBACK_API_URL is empty")
    _log_checkout(f"external checkout fallback attempt: endpoint={endpoint}")
    link = await asyncio.to_thread(
        _create_external_checkout_link_sync,
        access_token,
        cfg,
        billing_details,
        env=actual_env,
    )
    _log_checkout("external checkout fallback ok")
    return link


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
    for candidate in (proxy, DEFAULT_STRIPE_FALLBACK_PROXY):
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


async def _create_plus_checkout_link_with_fallbacks(
    checkout_data: dict[str, Any],
    access_token: str,
    cfg: dict[str, Any],
    billing_details: dict[str, str],
    *,
    proxy: str | None = None,
    env: dict[str, str] | None = None,
    checkout_region: str | None = None,
    skip_methods: set[str] | list[str] | tuple[str, ...] | None = None,
    preferred_methods: list[str] | tuple[str, ...] | None = None,
    method_sink: dict[str, str] | None = None,
) -> str:
    skipped = {str(item or "").strip() for item in (skip_methods or []) if str(item or "").strip()}
    actual_env = env if env is not None else load_env(".env")
    methods = list(preferred_methods or _checkout_method_plan(actual_env, skipped))
    errors: list[str] = []
    for method in methods:
        try:
            if method == CHECKOUT_METHOD_BROWSER_CHECKOUT:
                link = await _create_plus_checkout_link_fallback(checkout_data, proxy=proxy)
            elif method == CHECKOUT_METHOD_EXTERNAL_API:
                link = await _create_external_checkout_link(access_token, cfg, billing_details, env=actual_env)
            elif method == CHECKOUT_METHOD_LOCAL_SERVICE:
                link = await _create_local_service_checkout_link(
                    access_token,
                    cfg,
                    billing_details,
                    proxy=proxy,
                    env=actual_env,
                )
            elif method == CHECKOUT_METHOD_LOCAL_GENERATOR:
                link = await _create_local_generator_checkout_link(
                    access_token,
                    cfg,
                    billing_details,
                    proxy=proxy,
                    env=actual_env,
                    checkout_region=checkout_region,
                )
            elif method == CHECKOUT_METHOD_HOSTED_URL_HELPER:
                link = await _create_hosted_url_helper_checkout_link(
                    access_token,
                    cfg,
                    proxy=proxy,
                    env=actual_env,
                )
            else:
                continue
            if method_sink is not None:
                method_sink["method"] = method
            _log_checkout(f"checkout method selected: {_checkout_method_label(method)}")
            return link
        except Exception as exc:
            errors.append(f"{method}: {exc}")
            _log_checkout(f"{_checkout_method_label(method)} checkout attempt failed: {exc}")
    raise RuntimeError("; ".join(errors))


async def get_chatgpt_session(page: Page) -> dict[str, Any]:
    data = await page.evaluate(
        """async () => {
            const r = await fetch('/api/auth/session', { credentials: 'include' });
            const text = await r.text();
            let data = null;
            try { data = JSON.parse(text); } catch (e) {
                return { __session_error: true, status: r.status, contentType: r.headers.get('content-type') || '', text: text.slice(0, 500) };
            }
            if (!r.ok) {
                return { __session_error: true, status: r.status, data };
            }
            return data;
        }"""
    )
    if not isinstance(data, dict):
        raise RuntimeError("无法获取 ChatGPT session，当前页面可能未登录 ChatGPT")
    if data.get("__session_error"):
        status = data.get("status")
        content_type = data.get("contentType") or ""
        text = str(data.get("text") or data.get("data") or "")[:220]
        raise RuntimeError(f"无法获取 ChatGPT session: HTTP {status} content_type={content_type} body={text!r}")
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
    skip_methods: set[str] | list[str] | tuple[str, ...] | None = None,
    preferred_methods: list[str] | tuple[str, ...] | None = None,
    method_sink: dict[str, str] | None = None,
) -> str:
    skipped = {str(item or "").strip() for item in (skip_methods or []) if str(item or "").strip()}
    if set(CHECKOUT_METHOD_ORDER).issubset(skipped):
        skipped.clear()

    def _mark_method(method: str) -> None:
        if method_sink is not None:
            method_sink["method"] = method

    if checkout_region is not None:
        billing_details = checkout_billing_for_region(checkout_region)
    else:
        billing_details = {
            "country": cfg["billing_country"],
            "currency": cfg["currency"],
        }
    env = load_env(".env")
    method_plan = list(preferred_methods or _checkout_method_plan(env, skipped))
    if not method_plan:
        raise RuntimeError(f"all checkout methods skipped for this account: {sorted(skipped)}")
    _log_checkout(
        "checkout method plan: "
        + " -> ".join(_checkout_method_label(method) for method in method_plan)
    )
    browser_enabled = CHECKOUT_METHOD_BROWSER_CHECKOUT in method_plan
    pre_browser_methods = [method for method in method_plan if method != CHECKOUT_METHOD_BROWSER_CHECKOUT]
    errors: list[str] = []
    for method in pre_browser_methods:
        try:
            if method == CHECKOUT_METHOD_EXTERNAL_API:
                link = await _create_external_checkout_link(access_token, cfg, billing_details, env=env)
            elif method == CHECKOUT_METHOD_LOCAL_SERVICE:
                link = await _create_local_service_checkout_link(
                    access_token,
                    cfg,
                    billing_details,
                    proxy=proxy,
                    env=env,
                )
            elif method == CHECKOUT_METHOD_LOCAL_GENERATOR:
                link = await _create_local_generator_checkout_link(
                    access_token,
                    cfg,
                    billing_details,
                    proxy=proxy,
                    env=env,
                    checkout_region=checkout_region,
                )
            elif method == CHECKOUT_METHOD_HOSTED_URL_HELPER:
                link = await _create_hosted_url_helper_checkout_link(
                    access_token,
                    cfg,
                    proxy=proxy,
                    env=env,
                )
            else:
                continue
            _mark_method(method)
            _log_checkout(f"checkout method selected: {_checkout_method_label(method)}")
            return link
        except Exception as exc:
            errors.append(f"{method}: {exc}")
            _log_checkout(f"{_checkout_method_label(method)} checkout attempt failed: {exc}")
    if not browser_enabled:
        raise RuntimeError("; ".join(errors) or f"checkout method plan has no usable method: {method_plan}")
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
            link = await _create_plus_checkout_link_with_fallbacks(
                body,
                access_token,
                cfg,
                billing_details,
                proxy=proxy,
                env=env,
                checkout_region=checkout_region,
                skip_methods=skipped,
                preferred_methods=method_plan,
                method_sink=method_sink,
            )
            return link
        except Exception as fallback_exc:
            raise RuntimeError(
                f"生成长链接失败: HTTP {data.get('status')} {body}; fallback failed: {fallback_exc}"
            ) from fallback_exc
    link = body.get("url") or body.get("stripe_hosted_url") or body.get("checkout_url")
    if not link:
        try:
            link = await _create_plus_checkout_link_with_fallbacks(
                body,
                access_token,
                cfg,
                billing_details,
                proxy=proxy,
                env=env,
                checkout_region=checkout_region,
                skip_methods=skipped,
                preferred_methods=method_plan,
                method_sink=method_sink,
            )
            return link
        except Exception as fallback_exc:
            raise RuntimeError(f"生成长链接响应里没有 url: {body}; fallback failed: {fallback_exc}") from fallback_exc
    _mark_method(CHECKOUT_METHOD_BROWSER_CHECKOUT)
    _log_checkout(f"checkout method selected: {_checkout_method_label(CHECKOUT_METHOD_BROWSER_CHECKOUT)}")
    return _normalize_pay_url(link)
