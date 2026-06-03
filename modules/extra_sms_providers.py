from __future__ import annotations

import json
import re
import time
import zlib
from dataclasses import replace
from pathlib import Path
from typing import Any

import requests

from modules.hero_sms_provider import PhoneCountry, SmsActivation, parse_integer, parse_number
from modules.smsbower_provider import SmsBowerProvider


def _u(text: str) -> str:
    return text.encode("ascii").decode("unicode_escape")


SMS_VERIFICATION_NUMBER_ENDPOINT = "https://sms-verification-number.com/stubs/handler_api"
SMSPOOL_COMPAT_ENDPOINT = "https://api.smspool.net/stubs/handler_api.php?setting=smspool"
NEXSMS_ENDPOINT = "https://api.nexsms.net"


COUNTRY_BY_PHONE_PREFIX: tuple[tuple[str, str], ...] = (
    ("63", "PH"),
    ("254", "KE"),
    ("84", "VN"),
    ("48", "PL"),
    ("44", "GB"),
    ("40", "RO"),
    ("57", "CO"),
    ("62", "ID"),
    ("66", "TH"),
    ("49", "DE"),
    ("55", "BR"),
    ("33", "FR"),
    ("56", "CL"),
    ("81", "JP"),
    ("1", "US"),
)


def _code_from_text(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value or "")
    contextual = re.search(
        r"(?:verification\s*code|one[-\s]?time\s*(?:passcode|code)|passcode|otp|code|"
        r"\u9a8c\u8bc1\u7801|\u5b89\u5168\u7801)[\s\S]{0,80}?(\d[\s-]?\d[\s-]?\d[\s-]?\d[\s-]?\d[\s-]?\d)",
        text,
        re.I,
    )
    if contextual:
        return re.sub(r"\D+", "", contextual.group(1))
    match = re.search(r"\b(\d{4,8})\b", text)
    return match.group(1) if match else ""


def _activation_id_from_key(value: str) -> int:
    return int(zlib.crc32(str(value or "").encode("utf-8")) & 0x7FFFFFFF) or 1


def _normalized_phone(value: str) -> str:
    raw = str(value or "").strip()
    digits = re.sub(r"\D+", "", raw)
    if not digits:
        return raw
    return f"+{digits}" if not raw.startswith("+") else f"+{digits}"


def _infer_country_iso(phone_number: str) -> str:
    digits = re.sub(r"\D+", "", phone_number or "")
    for prefix, iso in COUNTRY_BY_PHONE_PREFIX:
        if digits.startswith(prefix):
            return iso
    return ""


class SmsVerificationNumberProvider(SmsBowerProvider):
    def __init__(self, api_key: str, *, base_url: str = SMS_VERIFICATION_NUMBER_ENDPOINT, timeout: int = 30) -> None:
        super().__init__(api_key, base_url=base_url or SMS_VERIFICATION_NUMBER_ENDPOINT, timeout=timeout)


class SmsPoolProvider(SmsBowerProvider):
    def __init__(self, api_key: str, *, base_url: str = SMSPOOL_COMPAT_ENDPOINT, timeout: int = 30) -> None:
        super().__init__(api_key, base_url=base_url or SMSPOOL_COMPAT_ENDPOINT, timeout=timeout)

    def get_services(self) -> dict[str, str]:
        services = super().get_services()
        return services or {"671": "OpenAI / ChatGPT"}


class NexSmsProvider:
    def __init__(self, api_key: str, *, base_url: str = NEXSMS_ENDPOINT, timeout: int = 30) -> None:
        self.api_key = (api_key or "").strip()
        self.base_url = (base_url or NEXSMS_ENDPOINT).strip().rstrip("/") or NEXSMS_ENDPOINT
        self.timeout = timeout
        self._phones_by_activation_id: dict[int, str] = {}

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        if not self.api_key:
            raise RuntimeError("NexSMS API Key " + _u(r"\u7f3a\u5931"))
        url = f"{self.base_url}/{str(path or '').lstrip('/')}"
        params: dict[str, Any] = {"apiKey": self.api_key}
        for key, value in (query or {}).items():
            if value not in (None, ""):
                params[key] = value
        if method.upper() == "POST":
            response = requests.post(url, params=params, json=body or {}, timeout=self.timeout)
        else:
            response = requests.get(url, params=params, timeout=self.timeout)
        response.raise_for_status()
        text = response.text.strip()
        try:
            return response.json()
        except ValueError:
            return text

    def get_services(self) -> dict[str, str]:
        return {"ot": "OpenAI / ChatGPT"}

    def get_countries(self) -> list[dict[str, Any]]:
        return []

    def list_country_prices(self, service: str, countries: list[PhoneCountry]) -> list[PhoneCountry]:
        result: list[PhoneCountry] = []
        service_code = str(service or "ot").strip() or "ot"
        for country in countries:
            if country.hero_sms_country <= 0:
                continue
            try:
                payload = self.request(
                    "/api/getCountryByService",
                    query={"serviceCode": service_code, "countryId": country.hero_sms_country},
                )
            except Exception:
                continue
            data = payload.get("data") if isinstance(payload, dict) else {}
            if not isinstance(data, dict):
                continue
            price = parse_number(data.get("minPrice") or data.get("price") or data.get("medianPrice"))
            count = parse_integer(data.get("count") or data.get("stock") or data.get("available"))
            prices = data.get("priceMap") if isinstance(data.get("priceMap"), dict) else {}
            if price is None and prices:
                price_values = [parse_number(key) for key, value in prices.items() if parse_integer(value) not in (None, 0)]
                price_values = [item for item in price_values if item is not None]
                price = min(price_values) if price_values else None
            if count is None and prices:
                count = sum(parse_integer(value) or 0 for value in prices.values())
            if price is not None:
                result.append(replace(country, price=price, count=count))
        return sorted(result, key=lambda row: ((row.price if row.price is not None else 999999), -(row.count or 0)))

    def get_operator_quote_options(self, service: str, country: int) -> list[Any]:
        return []

    def get_number(self, service: str = "ot", country: int | str = 1, *, operator: str = "", max_retries: int = 5) -> SmsActivation:
        country_id = int(country or 1)
        service_code = str(service or "ot").strip() or "ot"
        last_payload: Any = None
        prices = self._price_candidates(service_code, country_id)
        if not prices:
            prices = [None]
        for attempt in range(1, max(1, max_retries) + 1):
            for price in prices:
                print(
                    f"[SMS] {_u(r'\u8bf7\u6c42')} NexSMS {_u(r'\u53f7\u7801')}: "
                    f"service={service_code}, country={country_id}, price={price or '-'} ({attempt}/{max_retries})",
                    flush=True,
                )
                body: dict[str, Any] = {"serviceCode": service_code, "countryId": country_id, "quantity": 1}
                if price is not None:
                    body["price"] = price
                last_payload = self.request("/api/order/purchase", method="POST", body=body)
                activation = self._parse_activation(last_payload)
                if activation:
                    self._phones_by_activation_id[activation.activation_id] = activation.phone_number
                    print(
                        f"[SMS] {_u(r'\u83b7\u53d6\u53f7\u7801\u6210\u529f')}: "
                        f"{activation.phone_number} (activation={activation.activation_id}, "
                        f"{_u(r'\u8d39\u7528')}=${activation.activation_cost or '-'})",
                        flush=True,
                    )
                    return activation
            if attempt < max_retries:
                time.sleep(2)
        raise RuntimeError(f"NexSMS {_u(r'\u83b7\u53d6\u53f7\u7801\u5931\u8d25')}: {last_payload}")

    def _price_candidates(self, service: str, country: int) -> list[float]:
        try:
            payload = self.request("/api/getCountryByService", query={"serviceCode": service, "countryId": country})
        except Exception:
            return []
        data = payload.get("data") if isinstance(payload, dict) else {}
        if not isinstance(data, dict):
            return []
        values: list[float] = []
        price_map = data.get("priceMap") if isinstance(data.get("priceMap"), dict) else {}
        for key, count in price_map.items():
            numeric_count = parse_integer(count)
            price = parse_number(key)
            if price is not None and (numeric_count is None or numeric_count > 0):
                values.append(price)
        for key in ("minPrice", "medianPrice", "price"):
            price = parse_number(data.get(key))
            if price is not None:
                values.append(price)
        return sorted(set(values))

    def _parse_activation(self, payload: Any) -> SmsActivation | None:
        if not isinstance(payload, dict):
            return None
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        phones = data.get("phoneNumbers") if isinstance(data.get("phoneNumbers"), list) else []
        phone = str(data.get("phoneNumber") or data.get("phone") or (phones[0] if phones else "")).strip()
        if not phone:
            return None
        phone = _normalized_phone(phone)
        activation_id = _activation_id_from_key(phone)
        return SmsActivation(
            activation_id=activation_id,
            phone_number=phone,
            activation_cost=parse_number(data.get("price") or data.get("cost")),
        )

    def mark_ready(self, activation_id: int) -> None:
        return None

    def get_status(self, activation_id: int) -> tuple[bool, str]:
        phone = self._phones_by_activation_id.get(int(activation_id or 0), "")
        if not phone:
            return False, ""
        payload = self.request("/api/sms/messages", query={"phoneNumber": phone, "format": "json_latest"})
        code = _code_from_text(payload)
        return (bool(code), code)

    def poll_for_code(self, activation_id: int, *, interval: float = 5.0, max_attempts: int = 60) -> str:
        for attempt in range(1, max(1, max_attempts) + 1):
            print(f"[SMS] {_u(r'\u62c9\u53d6')} NexSMS {_u(r'\u9a8c\u8bc1\u7801')}: activation={activation_id} ({attempt}/{max_attempts})", flush=True)
            received, code = self.get_status(activation_id)
            if received and code:
                print(f"[SMS] {_u(r'\u62c9\u53d6\u5230\u77ed\u4fe1\u9a8c\u8bc1\u7801')}: {code}", flush=True)
                return code
            time.sleep(max(1.0, interval))
        raise TimeoutError("NexSMS " + _u(r"\u9a8c\u8bc1\u7801\u8d85\u65f6"))

    def poll_for_phone_code(self, phone_number: str, *, interval: float = 5.0, max_attempts: int = 60) -> str:
        activation_id = _activation_id_from_key(_normalized_phone(phone_number))
        self._phones_by_activation_id[activation_id] = _normalized_phone(phone_number)
        return self.poll_for_code(activation_id, interval=interval, max_attempts=max_attempts)

    def complete(self, activation_id: int) -> None:
        self._phones_by_activation_id.pop(int(activation_id or 0), None)

    def cancel(self, activation_id: int) -> None:
        phone = self._phones_by_activation_id.pop(int(activation_id or 0), "")
        if not phone:
            return
        try:
            self.request("/api/close/activation", method="POST", body={"phoneNumber": phone})
        except Exception:
            return


class ChatGptApiSmsProvider:
    def __init__(self, pool_text: str = "", *, pool_file: str | Path = "", timeout: int = 30) -> None:
        self.pool_text = str(pool_text or "")
        self.pool_file = Path(pool_file) if str(pool_file or "").strip() else None
        self.timeout = timeout
        self.api_key = "local-pool" if self._entries() else ""
        self._entries_by_activation_id: dict[int, tuple[str, str]] = {}

    def get_services(self) -> dict[str, str]:
        return {"custom-api": "ChatGPT API SMS"}

    def get_countries(self) -> list[dict[str, Any]]:
        return []

    def list_country_prices(self, service: str, countries: list[PhoneCountry]) -> list[PhoneCountry]:
        entries = self._entries()
        if not entries:
            return []
        counts: dict[str, int] = {}
        for phone, _url in entries:
            iso = _infer_country_iso(phone)
            if iso:
                counts[iso] = counts.get(iso, 0) + 1
        result: list[PhoneCountry] = []
        for country in countries:
            count = counts.get(country.iso_code.upper(), 0)
            if count:
                result.append(replace(country, price=0.0, count=count))
        return result or ([replace(countries[0], price=0.0, count=len(entries))] if countries else [])

    def get_operator_quote_options(self, service: str, country: int) -> list[Any]:
        return []

    def get_number(self, service: str = "custom-api", country: int | str = 0, *, operator: str = "", max_retries: int = 1) -> SmsActivation:
        entries = self._entries()
        if not entries:
            raise RuntimeError("ChatGPT API " + _u(r"\u63a5\u7801\u53f7\u6c60\u4e3a\u7a7a"))
        phone, url = entries[0]
        activation_id = _activation_id_from_key(f"{phone}----{url}")
        self._entries_by_activation_id[activation_id] = (phone, url)
        print(f"[SMS] {_u(r'\u4f7f\u7528')} ChatGPT API {_u(r'\u63a5\u7801\u53f7\u7801')}: {phone}", flush=True)
        return SmsActivation(activation_id=activation_id, phone_number=_normalized_phone(phone), activation_cost=0.0)

    def mark_ready(self, activation_id: int) -> None:
        return None

    def get_status(self, activation_id: int) -> tuple[bool, str]:
        entry = self._entries_by_activation_id.get(int(activation_id or 0))
        if not entry:
            for phone, url in self._entries():
                candidate_id = _activation_id_from_key(f"{phone}----{url}")
                if candidate_id == int(activation_id or 0):
                    entry = (phone, url)
                    self._entries_by_activation_id[candidate_id] = entry
                    break
        if not entry:
            return False, ""
        _phone, url = entry
        response = requests.get(url, timeout=self.timeout)
        text = response.text.strip()
        code = _code_from_text(text)
        return (bool(code), code)

    def poll_for_code(self, activation_id: int, *, interval: float = 5.0, max_attempts: int = 60) -> str:
        for attempt in range(1, max(1, max_attempts) + 1):
            print(f"[SMS] {_u(r'\u62c9\u53d6')} ChatGPT API {_u(r'\u9a8c\u8bc1\u7801')} ({attempt}/{max_attempts})", flush=True)
            received, code = self.get_status(activation_id)
            if received and code:
                print(f"[SMS] {_u(r'\u62c9\u53d6\u5230\u77ed\u4fe1\u9a8c\u8bc1\u7801')}: {code}", flush=True)
                return code
            time.sleep(max(1.0, interval))
        raise TimeoutError("ChatGPT API " + _u(r"\u63a5\u7801\u9a8c\u8bc1\u7801\u8d85\u65f6"))

    def complete(self, activation_id: int) -> None:
        self._entries_by_activation_id.pop(int(activation_id or 0), None)

    def cancel(self, activation_id: int) -> None:
        self._entries_by_activation_id.pop(int(activation_id or 0), None)

    def _entries(self) -> list[tuple[str, str]]:
        text = self.pool_text
        if self.pool_file and self.pool_file.exists():
            try:
                text = self.pool_file.read_text(encoding="utf-8")
            except Exception:
                text = self.pool_text
        result: list[tuple[str, str]] = []
        seen: set[str] = set()
        lines = [line.strip() for line in str(text or "").replace("\r", "").split("\n") if line.strip()]
        index = 0
        while index < len(lines):
            line = lines[index]
            if "----" in line:
                phone, url = line.split("----", 1)
            else:
                phone = line
                index += 1
                url = lines[index] if index < len(lines) else ""
            phone = _normalized_phone(phone.strip())
            url = url.strip()
            key = f"{phone}----{url}"
            if phone and url and key not in seen:
                seen.add(key)
                result.append((phone, url))
            index += 1
        return result
