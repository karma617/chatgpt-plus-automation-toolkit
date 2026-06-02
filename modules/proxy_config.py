from __future__ import annotations

from typing import Mapping


DEFAULT_LOCAL_PROXY_URL = "http://127.0.0.1:7987"
DEFAULT_REGISTER_LOCAL_PROXY_URL = "http://127.0.0.1:7897"
DEFAULT_PAYPAL_REGISTER_LOCAL_PROXY_URL = "http://127.0.0.1:7897"


def env_bool(value: str | None, *, default: bool = False) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return default
    return text in {"1", "true", "yes", "on", "enabled"}


def normalize_proxy_url(value: str | None) -> str:
    raw = str(value or "").strip().lstrip("\ufeff\u200b\u2060")
    if not raw or raw.lower() in {"0", "false", "off", "none", "direct"}:
        return ""
    if "://" in raw:
        return raw
    return f"http://{raw}"


def local_proxy_url(env: Mapping[str, str] | None = None) -> str:
    values = env or {}
    return normalize_proxy_url(
        values.get("LOCAL_PROXY_URL")
        or values.get("LOCAL_PROXY_ADDRESS")
        or DEFAULT_LOCAL_PROXY_URL
    )


def register_local_proxy_url(env: Mapping[str, str] | None = None) -> str:
    values = env or {}
    return normalize_proxy_url(
        values.get("REGISTER_LOCAL_PROXY_URL")
        or DEFAULT_REGISTER_LOCAL_PROXY_URL
    )


def paypal_flow2_proxy_file(env: Mapping[str, str], region_mode: str) -> str:
    if str(region_mode or "").strip().lower() == "jp":
        return env.get("PAYPAL_PROXY_FILE_JP") or "data/proxies/proxies_jp.txt"
    return (
        env.get("PAYPAL_PROXY_FILE_US")
        or env.get("PAYPAL_PROXY_FILE")
        or "data/proxies/proxies_us.txt"
    )


def paypal_register_proxy_file(env: Mapping[str, str]) -> str:
    return (
        env.get("PAYPAL_REGISTER_PROXY_FILE")
        or env.get("PAYPAL_PROXY_FILE_JP")
        or "data/proxies/proxies_jp.txt"
    )


def paypal_register_local_proxy_url(env: Mapping[str, str] | None = None) -> str:
    values = env or {}
    return normalize_proxy_url(
        values.get("PAYPAL_REGISTER_LOCAL_PROXY_URL")
        or values.get("PAYPAL_REGISTER_FALLBACK_PROXY_URL")
        or DEFAULT_PAYPAL_REGISTER_LOCAL_PROXY_URL
    )


def paypal_flow2_proxy_enabled(env: Mapping[str, str]) -> bool:
    return env_bool(env.get("PAYPAL_USE_PROXY"), default=False)


def paypal_register_proxy_enabled(env: Mapping[str, str]) -> bool:
    return env_bool(
        env.get("PAYPAL_REGISTER_USE_PROXY"),
        default=env_bool(env.get("PAYPAL_USE_PROXY"), default=False),
    )
