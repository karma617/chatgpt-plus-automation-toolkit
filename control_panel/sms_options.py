from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from modules.fivesim_sms_provider import FiveSimProvider, configured_fivesim_countries
from modules.grizzly_sms_provider import GrizzlySMSProvider
from modules.hero_sms_provider import HeroSMSProvider, configured_country_catalog, enrich_countries_with_api
from modules.smsbower_provider import DEFAULT_ENDPOINT as SMSBOWER_DEFAULT_ENDPOINT
from modules.smsbower_provider import SmsBowerProvider, smsbower_country_catalog
from modules.sms_country_filter import allowed_sms_country_name, filter_allowed_sms_countries
from modules.auth_upload import join_url, normalize_base_url


@dataclass(frozen=True)
class OptionItem:
    value: str
    label: str

    def display(self) -> str:
        return f"{self.value} - {self.label}" if self.label and self.label != self.value else self.value


def dynamic_env_options(key: str, env: dict[str, str]) -> list[OptionItem]:
    if key == "HERO_SMS_SERVICE":
        return _handler_services(HeroSMSProvider(_api_key(env, "HERO_SMS_API_KEY"), base_url="https://hero-sms.com/stubs/handler_api.php"))
    if key == "GRIZZLY_SERVICE":
        return [OptionItem("auto", "自动识别 OpenAI/ChatGPT"), *_handler_services(GrizzlySMSProvider(_api_key(env, "GRIZZLY_API_KEY")))]
    if key == "FIVESIM_SERVICE":
        return [OptionItem("openai", "OpenAI / ChatGPT")]
    if key == "SMSBOWER_SERVICE":
        return [OptionItem("auto", "自动识别 OpenAI/ChatGPT"), *_handler_services(_smsbower(env))]

    if key == "HERO_SMS_COUNTRY_SELECT":
        return _handler_countries(HeroSMSProvider(_api_key(env, "HERO_SMS_API_KEY"), base_url="https://hero-sms.com/stubs/handler_api.php"), "HERO_SMS_SERVICE", env)
    if key == "GRIZZLY_COUNTRY_SELECT":
        return _handler_countries(GrizzlySMSProvider(_api_key(env, "GRIZZLY_API_KEY")), "GRIZZLY_SERVICE", env)
    if key == "FIVESIM_COUNTRY_SELECT":
        return _fivesim_countries(env)
    if key == "SMSBOWER_COUNTRY_SELECT":
        return _smsbower_countries(env)
    if key == "SUB2API_GROUP_IDS":
        return _sub2api_groups(env)

    return []


def _api_key(env: dict[str, str], key: str) -> str:
    return (env.get(key) or env.get("SMS_API_KEY") or "").strip()


def _smsbower(env: dict[str, str]) -> SmsBowerProvider:
    return SmsBowerProvider(
        _api_key(env, "SMSBOWER_API_KEY"),
        base_url=(env.get("SMSBOWER_API_URL") or SMSBOWER_DEFAULT_ENDPOINT).strip() or SMSBOWER_DEFAULT_ENDPOINT,
    )


def _handler_services(provider: Any) -> list[OptionItem]:
    if not getattr(provider, "api_key", ""):
        return []
    try:
        services = provider.get_services()
    except Exception:
        return []
    if isinstance(services, dict):
        return [OptionItem(str(code), str(name)) for code, name in sorted(services.items(), key=lambda item: str(item[1]).lower())]
    return []


def _handler_countries(provider: Any, service_key: str, env: dict[str, str]) -> list[OptionItem]:
    if not getattr(provider, "api_key", ""):
        return []
    service = (env.get(service_key) or "dr").strip()
    try:
        api_countries = provider.get_countries()
        countries = enrich_countries_with_api(configured_country_catalog(), api_countries) if api_countries else configured_country_catalog()
        priced = provider.list_country_prices(service, countries)
    except Exception:
        return []
    priced = filter_allowed_sms_countries(priced)
    return [
        OptionItem(str(country.hero_sms_country), _country_label(country))
        for country in priced
        if country.hero_sms_country > 0
    ]


def _fivesim_countries(env: dict[str, str]) -> list[OptionItem]:
    api_key = _api_key(env, "FIVESIM_API_KEY")
    if not api_key:
        return []
    provider = FiveSimProvider(api_key)
    service = (env.get("FIVESIM_SERVICE") or "openai").strip() or "openai"
    try:
        priced = provider.list_country_prices(service, configured_fivesim_countries())
    except Exception:
        return []
    priced = filter_allowed_sms_countries(priced)
    from modules.fivesim_sms_provider import FIVESIM_ISO_TO_COUNTRY

    return [
        OptionItem(FIVESIM_ISO_TO_COUNTRY.get(country.iso_code.upper(), country.iso_code.lower()), _country_label(country))
        for country in priced
    ]


def _smsbower_countries(env: dict[str, str]) -> list[OptionItem]:
    provider = _smsbower(env)
    if not provider.api_key:
        return []
    service = (env.get("SMSBOWER_SERVICE") or "dr").strip() or "dr"
    try:
        priced = provider.list_country_prices(service, smsbower_country_catalog(provider))
    except Exception:
        return []
    priced = filter_allowed_sms_countries(priced)
    return [
        OptionItem(str(country.hero_sms_country), _country_label(country))
        for country in priced
        if country.hero_sms_country > 0
    ]


def _sub2api_groups(env: dict[str, str]) -> list[OptionItem]:
    base_url = normalize_base_url(env.get("SUB2API_SERVER_URL") or env.get("SUB2API_API_URL") or "")
    api_key = (env.get("SUB2API_API_KEY") or env.get("SUB2API_API_TOKEN") or env.get("SUB2API_TOKEN") or "").strip()
    if not base_url or not api_key:
        return []
    for path in _sub2api_group_paths(env):
        url = join_url(base_url, path)
        params = _sub2api_group_params(path)
        for headers in _sub2api_header_candidates(env, api_key):
            try:
                import requests

                response = requests.get(url, headers=headers, params=params, timeout=15)
                if response.status_code < 200 or response.status_code >= 300:
                    continue
                groups = _extract_group_list(response.json())
                options = _group_options(groups)
                if options:
                    return options
            except Exception:
                continue
    return []


def _sub2api_group_paths(env: dict[str, str]) -> tuple[str, ...]:
    configured = str(env.get("SUB2API_GROUPS_PATH") or "").strip()
    paths = [configured] if configured else []
    paths.extend(("/api/v1/admin/groups", "/api/v1/admin/groups/all"))
    result: list[str] = []
    for path in paths:
        if path and path not in result:
            result.append(path)
    return tuple(result)


def _sub2api_group_params(path: str) -> dict[str, object]:
    if str(path or "").rstrip("/").endswith("/all"):
        return {"platform": "openai"}
    return {
        "page": 1,
        "page_size": 50,
        "status": "",
        "sort_by": "sort_order",
        "sort_order": "asc",
        "timezone": "Asia/Shanghai",
    }


def _sub2api_header_candidates(env: dict[str, str], api_key: str) -> tuple[dict[str, str], ...]:
    configured_header = str(env.get("SUB2API_API_KEY_HEADER") or "").strip()
    configured_scheme = str(env.get("SUB2API_AUTH_SCHEME") or "").strip()
    candidates: list[dict[str, str]] = []
    if configured_header:
        candidates.append(
            {
                "Accept": "application/json",
                configured_header: f"{configured_scheme} {api_key}".strip() if configured_scheme else api_key,
            }
        )
    candidates.extend(
        (
            {"Accept": "application/json", "authorization": f"Bearer {api_key}"},
            {"Accept": "application/json", "Authorization": f"Bearer {api_key}"},
            {"Accept": "application/json", "x-api-key": api_key},
        )
    )
    result: list[dict[str, str]] = []
    seen: set[tuple[tuple[str, str], ...]] = set()
    for headers in candidates:
        marker = tuple(sorted((key.lower(), value) for key, value in headers.items()))
        if marker in seen:
            continue
        seen.add(marker)
        result.append(headers)
    return tuple(result)


def _extract_group_list(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("data", "groups", "items", "results", "list", "rows", "records", "result"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = _extract_group_list(value)
            if nested:
                return nested
    return []


def _group_options(groups: list[Any]) -> list[OptionItem]:
    options: list[OptionItem] = []
    for item in groups:
        if not isinstance(item, dict):
            continue
        group_id = _first_present(item, "id", "group_id", "groupId", "gid", "value")
        if group_id is None or str(group_id).strip() == "":
            continue
        if not _group_supports_openai(item):
            continue
        name = str(_first_present(item, "name", "display_name", "group_name", "title", "label") or group_id).strip()
        platform = _platform_label(item)
        label = f"{name} / {platform or 'openai'}"
        options.append(OptionItem(str(group_id), label))
    return sorted(options, key=lambda option: option.label.lower())


def _first_present(item: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = item.get(key)
        if value is not None and str(value).strip() != "":
            return value
    return None


def _group_supports_openai(item: dict[str, Any]) -> bool:
    platforms = _platform_tokens(item)
    if not platforms:
        return True
    return "openai" in platforms


def _platform_label(item: dict[str, Any]) -> str:
    platforms = _platform_tokens(item)
    return ",".join(sorted(platforms))


def _platform_tokens(item: dict[str, Any]) -> set[str]:
    raw_values: list[Any] = []
    for key in ("platform", "platforms", "allowed_platforms", "provider", "providers"):
        value = item.get(key)
        if value is not None:
            raw_values.append(value)
    tokens: set[str] = set()
    for value in raw_values:
        if isinstance(value, list):
            iterable = value
        elif isinstance(value, tuple):
            iterable = list(value)
        elif isinstance(value, dict):
            iterable = list(value.values()) + list(value.keys())
        else:
            iterable = re.split(r"[,/| ]+", str(value))
        for part in iterable:
            text = str(part or "").strip().lower()
            if text:
                tokens.add(text)
    return tokens


def _country_label(country: Any) -> str:
    price = getattr(country, "price", None)
    count = getattr(country, "count", None)
    iso = str(getattr(country, "iso_code", "") or "").strip()
    name = allowed_sms_country_name(iso) or _country_zh_name(iso) or str(getattr(country, "name", "") or iso or "")
    parts = [name]
    dial = str(getattr(country, "dial_code", "") or "").strip()
    if dial:
        parts.append(f"+{dial}")
    if iso:
        parts.append(iso)
    if price is not None:
        parts.append(f"${price}")
    if count is not None:
        parts.append(f"库存 {count}")
    return " / ".join(part for part in parts if part)


def _country_zh_name(iso_code: str) -> str:
    names = {
        "AR": "\u963f\u6839\u5ef7",
        "AU": "\u6fb3\u5927\u5229\u4e9a",
        "BE": "\u6bd4\u5229\u65f6",
        "BR": "\u5df4\u897f",
        "CA": "\u52a0\u62ff\u5927",
        "CH": "\u745e\u58eb",
        "CL": "\u667a\u5229",
        "CO": "\u54e5\u4f26\u6bd4\u4e9a",
        "DE": "\u5fb7\u56fd",
        "DK": "\u4e39\u9ea6",
        "EG": "\u57c3\u53ca",
        "ES": "\u897f\u73ed\u7259",
        "FI": "\u82ac\u5170",
        "FR": "\u6cd5\u56fd",
        "GB": "\u82f1\u56fd",
        "GR": "\u5e0c\u814a",
        "HK": "\u4e2d\u56fd\u9999\u6e2f",
        "HU": "\u5308\u7259\u5229",
        "ID": "\u5370\u5ea6\u5c3c\u897f\u4e9a",
        "IE": "\u7231\u5c14\u5170",
        "IL": "\u4ee5\u8272\u5217",
        "IN": "\u5370\u5ea6",
        "IT": "\u610f\u5927\u5229",
        "JP": "\u65e5\u672c",
        "KR": "\u97e9\u56fd",
        "MX": "\u58a8\u897f\u54e5",
        "MY": "\u9a6c\u6765\u897f\u4e9a",
        "NG": "\u5c3c\u65e5\u5229\u4e9a",
        "NL": "\u8377\u5170",
        "NO": "\u632a\u5a01",
        "NZ": "\u65b0\u897f\u5170",
        "PE": "\u79d8\u9c81",
        "PH": "\u83f2\u5f8b\u5bbe",
        "PL": "\u6ce2\u5170",
        "PT": "\u8461\u8404\u7259",
        "RO": "\u7f57\u9a6c\u5c3c\u4e9a",
        "SE": "\u745e\u5178",
        "SG": "\u65b0\u52a0\u5761",
        "TH": "\u6cf0\u56fd",
        "TR": "\u571f\u8033\u5176",
        "TW": "\u4e2d\u56fd\u53f0\u6e7e",
        "US": "\u7f8e\u56fd",
        "VN": "\u8d8a\u5357",
        "ZA": "\u5357\u975e",
    }
    return names.get(str(iso_code or "").strip().upper(), "")


def parse_dynamic_display(value: str) -> str:
    text = str(value or "").strip()
    if " - " in text:
        return text.split(" - ", 1)[0].strip()
    return text
