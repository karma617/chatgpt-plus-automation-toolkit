from __future__ import annotations

import json
import time
from dataclasses import replace
from typing import Any

import requests

from modules.hero_sms_provider import (
    OperatorQuote,
    PhoneCountry,
    SmsActivation,
    configured_country_catalog,
    enrich_countries_with_api,
    parse_countries_response,
    parse_integer,
    parse_number,
)
from modules.terminal_theme import install_print_theme


install_print_theme()


DEFAULT_ENDPOINT = "https://smsbower.page/stubs/handler_api.php"
DEFAULT_SERVICE = "dr"

SERVICE_ALIASES = {
    "auto": DEFAULT_SERVICE,
    "openai": DEFAULT_SERVICE,
    "chatgpt": DEFAULT_SERVICE,
    "openai(chatgpt)": DEFAULT_SERVICE,
    "openai (chatgpt)": DEFAULT_SERVICE,
}


class SmsBowerProvider:
    def __init__(self, api_key: str, *, base_url: str = DEFAULT_ENDPOINT, timeout: int = 30) -> None:
        self.api_key = (api_key or "").strip()
        self.base_url = (base_url or DEFAULT_ENDPOINT).strip() or DEFAULT_ENDPOINT
        self.timeout = timeout

    def request(self, action: str, **params: Any) -> Any:
        response = requests.get(
            self.base_url,
            params={"api_key": self.api_key, "action": action, **params},
            timeout=self.timeout,
        )
        response.raise_for_status()
        text = response.text.strip()
        try:
            return response.json()
        except ValueError:
            return text

    def get_services(self) -> dict[str, str]:
        data = self.request("getServicesList")
        if isinstance(data, str):
            data = json.loads(data)
        services = data.get("services", data) if isinstance(data, dict) else data
        result: dict[str, str] = {}
        if isinstance(services, dict):
            for code, item in services.items():
                key = str(code).strip()
                if not key:
                    continue
                if isinstance(item, dict):
                    name = str(item.get("name") or item.get("title") or item.get("label") or key).strip()
                else:
                    name = str(item).strip()
                result[key] = name or key
        elif isinstance(services, list):
            for item in services:
                if not isinstance(item, dict):
                    continue
                key = str(item.get("code") or item.get("activate_org_code") or item.get("service") or item.get("slug") or "").strip()
                name = str(item.get("name") or item.get("title") or item.get("label") or key).strip()
                if key:
                    result[key] = name or key
        return result

    def resolve_openai_service(self, configured: str = "") -> str:
        value = normalize_service(configured)
        if value != DEFAULT_SERVICE:
            return value
        try:
            services = self.get_services()
        except Exception:
            return value
        for code, name in services.items():
            haystack = f"{code} {name}".lower()
            if "openai" in haystack or "chatgpt" in haystack or "chat gpt" in haystack:
                print(f"[SMS] SMSBower 自动识别服务: {name} ({code})", flush=True)
                return code
        return value

    def get_countries(self) -> list[dict[str, Any]]:
        try:
            return parse_countries_response(self.request("getCountries"))
        except Exception:
            return []

    def get_price_matrix(self, service: str = DEFAULT_SERVICE) -> Any:
        last_error: Exception | None = None
        for action in ("getPricesV3", "getPricesV2", "getPrices"):
            try:
                return self.request(action, service=normalize_service(service))
            except Exception as exc:
                last_error = exc
        if last_error:
            raise last_error
        raise RuntimeError("未能获取 SMSBower 价格列表")

    def list_country_prices(self, service: str, countries: list[PhoneCountry]) -> list[PhoneCountry]:
        matrix = self.get_price_matrix(service)
        priced: list[PhoneCountry] = []
        known_ids = {country.hero_sms_country for country in countries if country.hero_sms_country > 0}
        by_id = {country.hero_sms_country: country for country in countries if country.hero_sms_country > 0}
        for country in countries:
            if country.hero_sms_country <= 0:
                continue
            parsed = _extract_smsbower_country_price(matrix, country.hero_sms_country, service)
            if not parsed or parsed.get("price") is None:
                continue
            priced.append(replace(country, price=parsed.get("price"), count=parsed.get("count")))

        for row in _extract_price_rows(matrix, service):
            country_id = parse_integer(row.get("country") or row.get("country_id") or row.get("countryId") or row.get("id"))
            price = parse_number(row.get("price") or row.get("cost") or row.get("activationCost"))
            count = parse_integer(row.get("count") or row.get("qty") or row.get("available") or row.get("stock"))
            if country_id is None or country_id in known_ids or price is None:
                continue
            base = by_id.get(country_id) or PhoneCountry(
                iso_code=str(row.get("isoCode") or row.get("iso") or "").strip().upper(),
                dial_code=str(row.get("dialCode") or row.get("phoneCode") or row.get("prefix") or "").strip().lstrip("+"),
                name=str(row.get("name") or row.get("countryName") or row.get("country") or country_id).strip(),
                hero_sms_country=country_id,
            )
            priced.append(replace(base, price=price, count=count))
            known_ids.add(country_id)

        return sorted(priced, key=lambda row: ((row.price if row.price is not None else 999999), -(row.count or 0)))

    def get_operator_quote_options(self, service: str, country: int) -> list[OperatorQuote]:
        rows: list[OperatorQuote] = []
        for action in ("getPricesV3", "getPricesV2", "getPrices"):
            try:
                data = self.request(action, service=normalize_service(service), country=country)
            except Exception:
                continue
            rows.extend(_extract_provider_quotes(data, service, country))
            if rows:
                break
        return rows

    def get_number(self, service: str = DEFAULT_SERVICE, country: int | str = "any", *, operator: str = "", max_retries: int = 5) -> SmsActivation:
        service_code = normalize_service(service)
        for attempt in range(1, max_retries + 1):
            params: dict[str, Any] = {"service": service_code}
            if str(country).strip():
                params["country"] = country
            if operator:
                params["providerIds"] = operator
            operator_text = f", providerIds={operator}" if operator else ", providerIds=自动"
            print(f"[SMS] 请求 SMSBower 号码: service={service_code}, country={country}{operator_text} ({attempt}/{max_retries})", flush=True)
            data = self.request("getNumberV2", **params)
            activation = _parse_activation(data)
            if activation:
                phone = activation.phone_number if activation.phone_number.startswith("+") else f"+{activation.phone_number}"
                print(
                    f"[SMS] 获取号码成功: {phone} (activation={activation.activation_id}, 费用=${activation.activation_cost if activation.activation_cost is not None else '-'})",
                    flush=True,
                )
                return SmsActivation(activation.activation_id, phone, activation.activation_cost)
            error = _error_text(data)
            if error in {"NO_NUMBERS", "SERVICE_UNAVAILABLE_REGION"} and attempt < max_retries:
                print(f"[SMS] {error}，3 秒后重试... ({attempt}/{max_retries})", flush=True)
                time.sleep(3)
                continue
            if error == "NO_BALANCE":
                raise RuntimeError("SMSBower 余额不足")
            if error == "BAD_KEY":
                raise RuntimeError("SMSBower API Key 无效")
            raise RuntimeError(f"获取号码失败: {data}")
        raise RuntimeError("获取号码失败")

    def mark_ready(self, activation_id: int) -> None:
        print(f"[SMS] 通知 SMSBower 准备接收短信: activation={activation_id}", flush=True)
        try:
            self.request("setStatus", id=activation_id, status=1)
        except Exception as exc:
            print(f"[SMS] 标记准备接收失败，继续等待验证码: {exc}", flush=True)

    def get_status(self, activation_id: int) -> tuple[bool, str]:
        data = self.request("getStatus", id=activation_id)
        if isinstance(data, str):
            if data.startswith("STATUS_OK:"):
                return True, data.split(":", 1)[1].strip().strip("'\"")
            if data == "STATUS_CANCEL":
                raise RuntimeError("激活已被取消")
            return False, ""
        code = _extract_sms_code(data)
        return bool(code), code

    def poll_for_code(self, activation_id: int, *, interval: float = 5.0, max_attempts: int = 60) -> str:
        for attempt in range(1, max_attempts + 1):
            print(f"[SMS] 拉取 SMSBower 验证码 activation={activation_id} ({attempt}/{max_attempts})", flush=True)
            received, code = self.get_status(activation_id)
            if received and code:
                print(f"[SMS] 拉取到短信验证码: {code}", flush=True)
                return code
            print(f"[SMS] 暂未收到验证码，{interval:g}s 后继续", flush=True)
            time.sleep(max(1.0, interval))
        self.cancel(activation_id)
        raise TimeoutError(f"SMSBower 验证码超时，已取消激活")

    def complete(self, activation_id: int) -> None:
        print(f"[SMS] 完成 SMSBower 激活: activation={activation_id}", flush=True)
        self.request("setStatus", id=activation_id, status=6)

    def cancel(self, activation_id: int) -> None:
        try:
            print(f"[SMS] 取消 SMSBower 激活: activation={activation_id}", flush=True)
            self.request("setStatus", id=activation_id, status=8)
        except Exception as exc:
            print(f"[SMS] 取消失败: {exc}，号码会在超时后自动释放", flush=True)


def normalize_service(service: str) -> str:
    text = str(service or DEFAULT_SERVICE).strip()
    return SERVICE_ALIASES.get(text.lower(), text or DEFAULT_SERVICE)


def smsbower_country_catalog(provider: SmsBowerProvider | None = None) -> list[PhoneCountry]:
    catalog = configured_country_catalog()
    if provider is None:
        return catalog
    api_countries = provider.get_countries()
    return enrich_countries_with_api(catalog, api_countries) if api_countries else catalog


def _parse_activation(data: Any) -> SmsActivation | None:
    if isinstance(data, str):
        parts = data.strip().split(":")
        if len(parts) >= 3 and parts[0] == "ACCESS_NUMBER":
            activation_id = parse_integer(parts[1])
            phone = str(parts[2] or "").strip()
            if activation_id is not None and phone:
                return SmsActivation(activation_id, phone, None)
        return None
    if not isinstance(data, dict):
        return None
    activation_id = parse_integer(data.get("activationId") or data.get("activation_id") or data.get("id"))
    phone = str(data.get("phoneNumber") or data.get("phone") or data.get("number") or "").strip()
    if activation_id is None or not phone:
        return None
    return SmsActivation(activation_id, phone, parse_number(data.get("activationCost") or data.get("cost") or data.get("price")))


def _error_text(data: Any) -> str:
    if isinstance(data, str):
        return data.strip()
    if isinstance(data, dict):
        for key in ("error", "status", "message"):
            value = str(data.get(key) or "").strip()
            if value:
                return value
    return str(data)


def _extract_sms_code(data: Any) -> str:
    if isinstance(data, dict):
        for key in ("code", "smsCode"):
            value = str(data.get(key) or "").strip()
            if value:
                return value
        sms = data.get("sms")
        if isinstance(sms, dict):
            return _extract_sms_code(sms)
        if isinstance(sms, list):
            for item in reversed(sms):
                value = _extract_sms_code(item)
                if value:
                    return value
        for key in ("data", "result"):
            value = _extract_sms_code(data.get(key))
            if value:
                return value
    if isinstance(data, str):
        match = __import__("re").search(r"\d{4,8}", data)
        return match.group(0) if match else ""
    return ""


def _extract_smsbower_country_price(raw: Any, country_id: int, service: str) -> dict[str, Any] | None:
    from modules.hero_sms_provider import extract_country_price

    service_code = normalize_service(service)
    matrix = raw
    if isinstance(matrix, dict):
        for key in ("data", "result", "prices", "countries", "response"):
            nested = matrix.get(key)
            if isinstance(nested, dict):
                matrix = nested
                break
    if isinstance(matrix, dict):
        country_node = matrix.get(str(country_id))
        if isinstance(country_node, dict):
            service_node = country_node.get(service_code)
            parsed = _extract_provider_price_summary(service_node)
            if parsed:
                return parsed
            parsed = _extract_provider_price_summary(country_node)
            if parsed:
                return parsed
    return extract_country_price(raw, country_id, service_code)


def _extract_provider_price_summary(node: Any) -> dict[str, Any] | None:
    if not isinstance(node, dict):
        return None
    direct_price = parse_number(node.get("cost") or node.get("price") or node.get("activationCost") or node.get("amount") or node.get("rate"))
    direct_count = parse_integer(node.get("count") or node.get("qty") or node.get("available") or node.get("stock") or node.get("total"))
    if direct_price is not None or direct_count is not None:
        return {"price": direct_price, "count": direct_count}

    provider_nodes: list[dict[str, Any]] = []
    for key, value in node.items():
        if str(key).isdigit() and isinstance(value, dict):
            provider_nodes.append(value)
    if not provider_nodes:
        for key in ("providers", "providerMap", "providerPrices", "provider"):
            value = node.get(key)
            if isinstance(value, dict):
                provider_nodes.extend(item for item in value.values() if isinstance(item, dict))
            elif isinstance(value, list):
                provider_nodes.extend(item for item in value if isinstance(item, dict))

    candidates: list[tuple[float, int | None]] = []
    for item in provider_nodes:
        price = parse_number(item.get("price") or item.get("cost") or item.get("activationCost") or item.get("amount") or item.get("rate"))
        count = parse_integer(item.get("count") or item.get("qty") or item.get("available") or item.get("stock") or item.get("total"))
        if price is not None:
            candidates.append((price, count))
    if not candidates:
        return None

    min_price = min(price for price, _count in candidates)
    min_price_count = sum((count or 0) for price, count in candidates if price == min_price)
    total_count = sum((count or 0) for _price, count in candidates)
    return {"price": min_price, "count": min_price_count or total_count or None}


def _extract_price_rows(data: Any, service: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return rows
    for key, value in data.items():
        if str(key).isdigit() and isinstance(value, dict):
            row = dict(value)
            row.setdefault("country", key)
            service_node = value.get(normalize_service(service))
            if isinstance(service_node, dict):
                providers = {
                    str(provider_id): payload
                    for provider_id, payload in service_node.items()
                    if str(provider_id).isdigit() and isinstance(payload, dict)
                }
                if providers:
                    row["providers"] = providers
                    summary = _extract_provider_price_summary(service_node)
                    if summary:
                        row.update(summary)
                else:
                    row.update(service_node)
            rows.append(row)
    for key in ("data", "result", "prices", "countries"):
        nested = data.get(key)
        if isinstance(nested, (dict, list)):
            rows.extend(_extract_price_rows(nested, service))
    return rows


def _extract_provider_quotes(data: Any, service: str, country: int) -> list[OperatorQuote]:
    quotes: list[OperatorQuote] = []
    for row in _extract_price_rows(data, service):
        row_country = parse_integer(row.get("country") or row.get("countryId") or row.get("country_id") or row.get("id"))
        if row_country is not None and row_country != int(country):
            continue
        providers = row.get("providers") or row.get("providerMap") or row.get("providerPrices") or row.get("provider")
        if isinstance(providers, dict):
            iterable = providers.items()
        elif isinstance(providers, list):
            iterable = [(item.get("id") if isinstance(item, dict) else "", item) for item in providers]
        else:
            iterable = []
        for provider_id, payload in iterable:
            if not isinstance(payload, dict):
                payload = {"count": payload}
            pid = str(payload.get("provider_id") or payload.get("providerId") or payload.get("id") or provider_id).strip()
            if not pid:
                continue
            quotes.append(
                OperatorQuote(
                    operator=pid,
                    label=str(payload.get("name") or payload.get("providerName") or f"服务商 {pid}").strip(),
                    price=parse_number(payload.get("price") or payload.get("cost")),
                    count=parse_integer(payload.get("count") or payload.get("qty") or payload.get("available") or payload.get("stock")),
                )
            )
    return sorted(quotes, key=lambda row: ((row.price if row.price is not None else 999999), -(row.count or 0)))
