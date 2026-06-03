from __future__ import annotations

from pathlib import Path
from typing import Any

from modules.extra_sms_providers import (
    ChatGptApiSmsProvider,
    NexSmsProvider,
    SmsPoolProvider,
    SmsVerificationNumberProvider,
)
from modules.fivesim_sms_provider import FiveSimProvider
from modules.grizzly_sms_provider import GrizzlySMSProvider
from modules.hero_sms_provider import HeroSMSProvider
from modules.smsbower_provider import SmsBowerProvider


def _u(text: str) -> str:
    return text.encode("ascii").decode("unicode_escape")


SUPPORTED_SMS_PROVIDERS: tuple[str, ...] = (
    "herosms",
    "grizzly",
    "fivesim",
    "smsbower",
    "sms-verification-number",
    "nexsms",
    "smspool",
    "chatgpt-api",
)

SMS_PROVIDER_LABELS: dict[str, str] = {
    "herosms": "HeroSMS",
    "grizzly": "GrizzlySMS",
    "fivesim": "5sim",
    "smsbower": "SMSBower",
    "sms-verification-number": "SMS Verification Number",
    "nexsms": "NexSMS",
    "smspool": "SMSPool",
    "chatgpt-api": "ChatGPT API SMS",
}

SMS_PROVIDER_ENV_PREFIX: dict[str, str] = {
    "herosms": "HERO_SMS",
    "grizzly": "GRIZZLY",
    "fivesim": "FIVESIM",
    "smsbower": "SMSBOWER",
    "sms-verification-number": "SMS_VERIFICATION_NUMBER",
    "nexsms": "NEXSMS",
    "smspool": "SMSPOOL",
    "chatgpt-api": "CHATGPT_API_SMS",
}

SMS_PROVIDER_DEFAULT_SERVICE: dict[str, str] = {
    "herosms": "dr",
    "grizzly": "auto",
    "fivesim": "openai",
    "smsbower": "auto",
    "sms-verification-number": "auto",
    "nexsms": "ot",
    "smspool": "671",
    "chatgpt-api": "custom-api",
}


def normalize_sms_provider_name(name: str) -> str:
    value = str(name or "").strip().lower().replace("_", "-")
    aliases = {
        "hero": "herosms",
        "hero-sms": "herosms",
        "hero-smscom": "herosms",
        "grizzlysms": "grizzly",
        "grizzly-sms": "grizzly",
        "5sim": "fivesim",
        "5sims": "fivesim",
        "five-sim": "fivesim",
        "sms-bower": "smsbower",
        "smsverificationnumber": "sms-verification-number",
        "sms-verification": "sms-verification-number",
        "sms-number": "sms-verification-number",
        "sms-pool": "smspool",
        "chatgptapi": "chatgpt-api",
        "chatgpt-api-sms": "chatgpt-api",
        "chatgpt-api-phone": "chatgpt-api",
    }
    return aliases.get(value, value)


def sms_provider_label(provider_name: str) -> str:
    return SMS_PROVIDER_LABELS.get(normalize_sms_provider_name(provider_name), provider_name or "SMS")


def sms_provider_env_prefix(provider_name: str) -> str:
    return SMS_PROVIDER_ENV_PREFIX.get(normalize_sms_provider_name(provider_name), "")


def sms_provider_default_service(provider_name: str) -> str:
    return SMS_PROVIDER_DEFAULT_SERVICE.get(normalize_sms_provider_name(provider_name), "dr")


def sms_provider_uses_country_object(provider_name: str) -> bool:
    return normalize_sms_provider_name(provider_name) == "fivesim"


def sms_provider_api_key_name(provider_name: str) -> str:
    name = normalize_sms_provider_name(provider_name)
    if name == "chatgpt-api":
        return "CHATGPT_API_SMS_POOL_FILE"
    prefix = sms_provider_env_prefix(name)
    return f"{prefix}_API_KEY" if prefix else "SMS_API_KEY"


def sms_provider_api_key_from_env(provider_name: str, env: dict[str, str], fallback: str = "") -> str:
    name = normalize_sms_provider_name(provider_name)
    prefix = sms_provider_env_prefix(name)
    if name == "chatgpt-api":
        return (
            str(env.get("CHATGPT_API_SMS_POOL_FILE") or "").strip()
            or str(env.get("CHATGPT_API_SMS_POOL_TEXT") or "").strip()
            or fallback
        )
    keys = [f"{prefix}_API_KEY"] if prefix else []
    if name == "herosms":
        keys.append("HEROSMS_API_KEY")
    keys.append("SMS_API_KEY")
    for key in keys:
        value = str(env.get(key) or "").strip()
        if value:
            return value
    return fallback


def create_sms_provider(
    provider_name: str,
    api_key: str = "",
    *,
    base_url: str = "",
    pool_file: str | Path = "",
    pool_text: str = "",
):
    name = normalize_sms_provider_name(provider_name)
    if name == "grizzly":
        return GrizzlySMSProvider(api_key)
    if name == "fivesim":
        return FiveSimProvider(api_key)
    if name == "smsbower":
        return SmsBowerProvider(api_key)
    if name == "sms-verification-number":
        return SmsVerificationNumberProvider(api_key)
    if name == "nexsms":
        return NexSmsProvider(api_key, base_url=base_url) if base_url else NexSmsProvider(api_key)
    if name == "smspool":
        return SmsPoolProvider(api_key)
    if name == "chatgpt-api":
        return ChatGptApiSmsProvider(pool_text=pool_text or (api_key if "\n" in str(api_key or "") or "----" in str(api_key or "") else ""), pool_file=pool_file or (api_key if api_key and "\n" not in api_key and "----" not in api_key else ""))
    return HeroSMSProvider(api_key)


def provider_country_arg(provider_name: str, country: Any) -> Any:
    if sms_provider_uses_country_object(provider_name):
        return country
    return getattr(country, "hero_sms_country", country)

