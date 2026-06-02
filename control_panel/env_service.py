from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


KNOWN_ENV_FIELDS = [
    "MOEMAIL_API_KEY",
    "AUTH_SERVER_API_KEY",
    "HERO_SMS_API_KEY",
    "GRIZZLY_API_KEY",
    "FIVESIM_API_KEY",
    "CAPSOLVER_API_KEY",
    "TWOCAPTCHA_API_KEY",
    "PAYPAL_CARD_REDEEM_API_KEY",
    "PAYPAL_CAPTCHA_MODE",
    "CAPTCHA_API_PROVIDER",
    "PAYPAL_USE_PROXY",
    "PAYPAL_REGISTER_USE_PROXY",
    "PAYPAL_PROXY_FILE",
    "PAYPAL_REGISTER_PROXY_FILE",
    "PAYPAL_CARD_REDEEM_ENABLED",
    "PAYPAL_CARD_REDEEM_API_URL",
    "PAYPAL_CARD_REDEEM_CODE_FIELD",
    "PAYPAL_CARD_REDEEM_TIMEOUT",
    "PAYPAL_CARD_REDEEM_MAX_AUTO_FETCH",
    "PAYPAL_CARD_CODES_FILE",
    "PAYPAL_CARD_CODES_USED_FILE",
    "PAYPAL_CARD_CODES_FAILED_FILE",
    "USE_PROXY",
    "PROXY_FILE",
    "MAIL_SOURCE",
    "FLOW1_MAIL_SOURCE",
    "FLOW3_MAIL_SOURCE",
    "FREE_MAIL_SOURCE",
    "SMS_ENABLED",
    "SMS_PROVIDER",
    "FLOW1_SMS_ENABLED",
    "FLOW1_SMS_PROVIDER",
    "FLOW3_SMS_ENABLED",
    "FLOW3_SMS_PROVIDER",
    "FREE_SMS_ENABLED",
    "FREE_SMS_PROVIDER",
    "HERO_SMS_SERVICE",
    "HERO_SMS_COUNTRY_SELECT",
    "HERO_SMS_PROMPT_COUNTRY_SELECTION",
    "GRIZZLY_SERVICE",
    "GRIZZLY_COUNTRY_SELECT",
    "GRIZZLY_PROMPT_COUNTRY_SELECTION",
    "FIVESIM_SERVICE",
    "FIVESIM_COUNTRY_SELECT",
    "FIVESIM_PROMPT_COUNTRY_SELECTION",
]


def _u(text: str) -> str:
    return text.encode("ascii").decode("unicode_escape")


ENV_FIELD_LABELS = {
    "MOEMAIL_API_KEY": _u(r"\u90ae\u7bb1\u670d\u52a1 MoEmail API Key"),
    "AUTH_SERVER_API_KEY": _u(r"\u6388\u6743\u670d\u52a1 API Key"),
    "HERO_SMS_API_KEY": _u(r"Hero SMS API Key"),
    "GRIZZLY_API_KEY": _u(r"Grizzly SMS API Key"),
    "FIVESIM_API_KEY": _u(r"5sim API Key"),
    "CAPSOLVER_API_KEY": _u(r"Capsolver \u9a8c\u8bc1\u7801 API Key"),
    "TWOCAPTCHA_API_KEY": _u(r"2Captcha \u9a8c\u8bc1\u7801 API Key"),
    "PAYPAL_CARD_REDEEM_API_KEY": _u(r"PayPal \u793c\u54c1\u5361\u5151\u6362 API Key"),
    "PAYPAL_CAPTCHA_MODE": _u(r"PayPal \u9a8c\u8bc1\u7801\u6a21\u5f0f"),
    "CAPTCHA_API_PROVIDER": _u(r"\u9a8c\u8bc1\u7801\u670d\u52a1\u5546"),
    "PAYPAL_USE_PROXY": _u(r"PayPal \u652f\u4ed8\u4f7f\u7528\u4ee3\u7406"),
    "PAYPAL_REGISTER_USE_PROXY": _u(r"PayPal \u6ce8\u518c\u4f7f\u7528\u4ee3\u7406"),
    "PAYPAL_PROXY_FILE": _u(r"PayPal \u652f\u4ed8\u4ee3\u7406\u6587\u4ef6"),
    "PAYPAL_REGISTER_PROXY_FILE": _u(r"PayPal \u6ce8\u518c\u4ee3\u7406\u6587\u4ef6"),
    "PAYPAL_CARD_REDEEM_ENABLED": _u(r"\u542f\u7528 PayPal \u793c\u54c1\u5361\u5151\u6362"),
    "PAYPAL_CARD_REDEEM_API_URL": _u(r"PayPal \u793c\u54c1\u5361\u5151\u6362 API \u5730\u5740"),
    "PAYPAL_CARD_REDEEM_CODE_FIELD": _u(r"PayPal \u793c\u54c1\u5361\u5151\u6362\u5361\u5bc6\u5b57\u6bb5"),
    "PAYPAL_CARD_REDEEM_TIMEOUT": _u(r"PayPal \u793c\u54c1\u5361\u5151\u6362\u8d85\u65f6\u79d2\u6570"),
    "PAYPAL_CARD_REDEEM_MAX_AUTO_FETCH": _u(r"PayPal \u793c\u54c1\u5361\u6700\u5927\u81ea\u52a8\u53d6\u7801\u6570"),
    "PAYPAL_CARD_CODES_FILE": _u(r"PayPal \u793c\u54c1\u5361\u5e93\u5b58\u6587\u4ef6"),
    "PAYPAL_CARD_CODES_USED_FILE": _u(r"PayPal \u793c\u54c1\u5361\u5df2\u7528\u8bb0\u5f55\u6587\u4ef6"),
    "PAYPAL_CARD_CODES_FAILED_FILE": _u(r"PayPal \u793c\u54c1\u5361\u5931\u8d25\u8bb0\u5f55\u6587\u4ef6"),
    "USE_PROXY": _u(r"\u6ce8\u518c\u6d41\u7a0b\u4f7f\u7528\u4ee3\u7406"),
    "PROXY_FILE": _u(r"\u6ce8\u518c\u6d41\u7a0b\u4ee3\u7406\u6587\u4ef6"),
    "MAIL_SOURCE": _u(r"\u9ed8\u8ba4\u90ae\u7bb1\u6765\u6e90"),
    "FLOW1_MAIL_SOURCE": _u(r"\u6d41\u7a0b1\u90ae\u7bb1\u6765\u6e90"),
    "FLOW3_MAIL_SOURCE": _u(r"\u6d41\u7a0b3\u90ae\u7bb1\u6765\u6e90"),
    "FREE_MAIL_SOURCE": _u(r"\u514d\u8d39\u6ce8\u518c\u90ae\u7bb1\u6765\u6e90"),
    "SMS_ENABLED": _u(r"\u9ed8\u8ba4\u624b\u673a\u63a5\u7801\u5f00\u5173"),
    "SMS_PROVIDER": _u(r"\u9ed8\u8ba4\u63a5\u7801\u5e73\u53f0"),
    "FLOW1_SMS_ENABLED": _u(r"\u6d41\u7a0b1\u6ce8\u518c\u63a5\u7801\u5f00\u5173"),
    "FLOW1_SMS_PROVIDER": _u(r"\u6d41\u7a0b1\u6ce8\u518c\u63a5\u7801\u5e73\u53f0"),
    "FLOW3_SMS_ENABLED": _u(r"\u6d41\u7a0b3\u6388\u6743\u63a5\u7801\u5f00\u5173"),
    "FLOW3_SMS_PROVIDER": _u(r"\u6d41\u7a0b3\u6388\u6743\u63a5\u7801\u5e73\u53f0"),
    "FREE_SMS_ENABLED": _u(r"\u514d\u8d39\u6ce8\u518c\u63a5\u7801\u5f00\u5173"),
    "FREE_SMS_PROVIDER": _u(r"\u514d\u8d39\u6ce8\u518c\u63a5\u7801\u5e73\u53f0"),
    "HERO_SMS_SERVICE": _u(r"Hero SMS \u670d\u52a1\u4ee3\u7801"),
    "HERO_SMS_COUNTRY_SELECT": _u(r"Hero SMS \u6307\u5b9a\u56fd\u5bb6"),
    "HERO_SMS_PROMPT_COUNTRY_SELECTION": _u(r"Hero SMS \u5f39\u51fa\u56fd\u5bb6\u9009\u62e9"),
    "GRIZZLY_SERVICE": _u(r"Grizzly SMS \u670d\u52a1\u4ee3\u7801"),
    "GRIZZLY_COUNTRY_SELECT": _u(r"Grizzly SMS \u6307\u5b9a\u56fd\u5bb6"),
    "GRIZZLY_PROMPT_COUNTRY_SELECTION": _u(r"Grizzly SMS \u5f39\u51fa\u56fd\u5bb6\u9009\u62e9"),
    "FIVESIM_SERVICE": _u(r"5sim \u670d\u52a1\u4ee3\u7801"),
    "FIVESIM_COUNTRY_SELECT": _u(r"5sim \u6307\u5b9a\u56fd\u5bb6"),
    "FIVESIM_PROMPT_COUNTRY_SELECTION": _u(r"5sim \u5f39\u51fa\u56fd\u5bb6\u9009\u62e9"),
}


@dataclass(frozen=True)
class EnvField:
    key: str
    label: str

    def __str__(self) -> str:
        return f"{self.label} ({self.key})"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, EnvField):
            return self.key == other.key
        if isinstance(other, str):
            return self.key == other
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.key)


def _env_key(key: object) -> str:
    raw = key.key if isinstance(key, EnvField) else key
    return str(raw).strip()


def get_known_env_fields() -> list[EnvField]:
    return [EnvField(key, ENV_FIELD_LABELS.get(key, key)) for key in KNOWN_ENV_FIELDS]


def _parse_env_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in line:
        return None
    key, value = line.split("=", 1)
    key = key.strip()
    if not key:
        return None
    return key, value.strip()


def read_env(path: Path | str) -> dict[str, str]:
    file_path = Path(path)
    if not file_path.exists():
        return {}
    values: dict[str, str] = {}
    for line in file_path.read_text(encoding="utf-8-sig").splitlines():
        parsed = _parse_env_line(line)
        if parsed:
            key, value = parsed
            values[key] = value
    return values


def update_env(path: Path | str, updates: dict[object, str]) -> None:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    lines = file_path.read_text(encoding="utf-8-sig").splitlines() if file_path.exists() else []
    remaining = {_env_key(key): str(value) for key, value in updates.items()}
    output: list[str] = []

    for line in lines:
        parsed = _parse_env_line(line)
        if not parsed:
            output.append(line)
            continue
        key, _old_value = parsed
        if key in remaining:
            output.append(f"{key}={remaining.pop(key)}")
        else:
            output.append(line)

    for key, value in remaining.items():
        output.append(f"{key}={value}")

    file_path.write_text("\n".join(output) + ("\n" if output else ""), encoding="utf-8")
