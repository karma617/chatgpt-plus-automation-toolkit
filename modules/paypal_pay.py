"""PayPal 流程2：从长链接池取账号 → Stripe → PayPal 注册绑卡 → 支付。

输入：output/paypal成品/长链接账号/account.txt + cards.txt + phones.txt
输出：output/paypal成品/待授权账号/account.txt
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import random
import re
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from .browser import BrowserSession
from .chatgpt_register import ChatGPTRegister, is_signin_problem_retry_reason
from .checkout import (
    CHECKOUT_METHOD_BROWSER_CHECKOUT,
    CHECKOUT_METHOD_EXTERNAL_API,
    CHECKOUT_METHOD_HOSTED_URL_HELPER,
    CHECKOUT_METHOD_LOCAL_GENERATOR,
    CHECKOUT_METHOD_LOCAL_SERVICE,
)
from .mail_provider import MailProvider
from . import paypal_flow_state
from .paypal_card_pool import CardInfo, CardPool
from .paypal_phone_pool import PhoneInfo, PhonePool
from .proxy_config import local_proxy_url, paypal_flow2_proxy_enabled, paypal_flow2_proxy_file
from .storage import MailAccount, parse_mail_line
from .utils import load_env, log, resolve_path, safe_filename
from .zenpic_short_link import create_zenpic_short_link, find_session_text_for_email


PAYPAL_OUTPUT_ROOT = resolve_path("output/paypal注册")
LINK_POOL_FILE = PAYPAL_OUTPUT_ROOT / "长链接账号" / "account.txt"
PENDING_AUTH_DIR = PAYPAL_OUTPUT_ROOT / "待授权账号"
PENDING_AUTH_FILE = PENDING_AUTH_DIR / "account.txt"
REGISTER_ONLY_SUMMARY_FILE = resolve_path("output/register_only/registered_sessions.txt")
PAYPAL_FLOW2_CODE_VERSION = "PAYPAL_ZENPIC_JP_NOCARD_2026-06-07_01"
PAYPAL_FLOW2_NONZERO_AMOUNT = "nonzero_checkout_amount"
PAYPAL_FLOW2_STRIPE_PAYPAL_TIMEOUT = "stripe_paypal_redirect_timeout"
PAYPAL_FLOW2_RECREATE_LINK = "generated_payment_link_invalid_recreate"
PAYPAL_FLOW2_NO_PAYPAL_OPTION = "short_offer_checkout_no_paypal_option"
PAYPAL_FLOW2_RECREATE_LINK_MAX = 3
PAYPAL_FLOW2_JP_PROXY_COUNTRY_MISMATCH = "jp_short_link_proxy_country_mismatch"
PAYPAL_PAYMENT_MODE_LONG_LINK = "long_link"
PAYPAL_PAYMENT_MODE_SHORT_LINK = "short_link"
PAYPAL_FLOW2_RECREATE_METHOD_ORDER = (
    CHECKOUT_METHOD_EXTERNAL_API,
    CHECKOUT_METHOD_LOCAL_SERVICE,
    CHECKOUT_METHOD_HOSTED_URL_HELPER,
    CHECKOUT_METHOD_LOCAL_GENERATOR,
    CHECKOUT_METHOD_BROWSER_CHECKOUT,
)
_CHATGPT_OFFER_SURFACE_MAX_ATTEMPTS = 36
_CHATGPT_OFFER_DEBUG_KEYS: set[str] = set()


def _zh(text: str) -> str:
    return text.encode("ascii").decode("unicode_escape")

_RANDOM_CARD_PROFILES: list[tuple[str, str, str, str, str]] = [
    ("New York", "NY", "10001", "W 34th St", "US"),
    ("Los Angeles", "CA", "90017", "S Grand Ave", "US"),
    ("Chicago", "IL", "60606", "N LaSalle St", "US"),
    ("Houston", "TX", "77002", "Louisiana St", "US"),
    ("Phoenix", "AZ", "85004", "E Washington St", "US"),
    ("Philadelphia", "PA", "19103", "Market St", "US"),
    ("San Antonio", "TX", "78205", "E Houston St", "US"),
    ("San Diego", "CA", "92101", "Broadway", "US"),
    ("Dallas", "TX", "75201", "Main St", "US"),
    ("San Jose", "CA", "95113", "Santa Clara St", "US"),
    ("Austin", "TX", "78701", "Congress Ave", "US"),
    ("Jacksonville", "FL", "32202", "Bay St", "US"),
    ("Fort Worth", "TX", "76102", "Houston St", "US"),
    ("Columbus", "OH", "43215", "High St", "US"),
    ("Charlotte", "NC", "28202", "Trade St", "US"),
    ("Indianapolis", "IN", "46204", "Meridian St", "US"),
    ("Seattle", "WA", "98101", "Pike St", "US"),
    ("Denver", "CO", "80202", "17th St", "US"),
    ("Boston", "MA", "02110", "Atlantic Ave", "US"),
    ("Nashville", "TN", "37219", "Church St", "US"),
]

_RANDOM_CARD_PROFILES_JP: list[tuple[str, str, str, str, str]] = [
    ("Tokyo", "Tokyo", "1000001", "Chiyoda 1-1", "JP"),
    ("Osaka", "Osaka", "5300001", "Kita Umeda 2-3", "JP"),
    ("Yokohama", "Kanagawa", "2200012", "Nishi Minatomirai 1-4", "JP"),
    ("Nagoya", "Aichi", "4600008", "Naka Sakae 3-5", "JP"),
    ("Sapporo", "Hokkaido", "0600001", "Chuo Odori 2-6", "JP"),
    ("Fukuoka", "Fukuoka", "8100001", "Chuo Tenjin 1-8", "JP"),
    ("Kyoto", "Kyoto", "6008001", "Shimogyo Shijo 4-2", "JP"),
    ("Kobe", "Hyogo", "6500001", "Chuo Sannomiya 2-9", "JP"),
    ("Sendai", "Miyagi", "9800004", "Aoba Ichibancho 1-7", "JP"),
    ("Hiroshima", "Hiroshima", "7300011", "Naka Motomachi 1-3", "JP"),
]

_STRIPE_STABLE_US_BILLING_PROFILES: list[tuple[str, str, str, str]] = [
    ("350 5th Ave", "New York", "NY", "10118"),
    ("11 Wall St", "New York", "NY", "10005"),
    ("1 Apple Park Way", "Cupertino", "CA", "95014"),
    ("1600 Amphitheatre Pkwy", "Mountain View", "CA", "94043"),
    ("500 Terry A Francois Blvd", "San Francisco", "CA", "94158"),
    ("1 Microsoft Way", "Redmond", "WA", "98052"),
    ("410 Terry Ave N", "Seattle", "WA", "98109"),
    ("600 Congress Ave", "Austin", "TX", "78701"),
    ("233 S Wacker Dr", "Chicago", "IL", "60606"),
    ("4059 Mt Lee Dr", "Los Angeles", "CA", "90068"),
]

_US_STATE_NAMES: dict[str, str] = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida",
    "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana",
    "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine",
    "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota",
    "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island",
    "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin",
    "WY": "Wyoming",
}

_MEIGUODIZHI_ADDRESS_URL = "https://www.meiguodizhi.com/api/v1/dz"
_BILLING_ADDRESS_REGION_PATHS = {
    "US": "/",
    "JP": "/jp-address",
}
_VISA_BIN_PREFIXES = (
    "4859",
    "424631",
    "414709",
)

_JP_PREFECTURE_LABELS: dict[str, str] = {
    "Tokyo": "東京都",
    "Osaka": "大阪府",
    "Kanagawa": "神奈川県",
    "Aichi": "愛知県",
    "Hokkaido": "北海道",
    "Aomori": "青森県",
    "Iwate": "岩手県",
    "Akita": "秋田県",
    "Yamagata": "山形県",
    "Fukushima": "福島県",
    "Fukuoka": "福岡県",
    "Kyoto": "京都府",
    "Hyogo": "兵庫県",
    "Miyagi": "宮城県",
    "Ibaraki": "茨城県",
    "Tochigi": "栃木県",
    "Gunma": "群馬県",
    "Saitama": "埼玉県",
    "Chiba": "千葉県",
    "Niigata": "新潟県",
    "Toyama": "富山県",
    "Ishikawa": "石川県",
    "Fukui": "福井県",
    "Yamanashi": "山梨県",
    "Nagano": "長野県",
    "Gifu": "岐阜県",
    "Shizuoka": "静岡県",
    "Mie": "三重県",
    "Shiga": "滋賀県",
    "Nara": "奈良県",
    "Wakayama": "和歌山県",
    "Tottori": "鳥取県",
    "Shimane": "島根県",
    "Okayama": "岡山県",
    "Hiroshima": "広島県",
    "Yamaguchi": "山口県",
    "Tokushima": "徳島県",
    "Kagawa": "香川県",
    "Ehime": "愛媛県",
    "Kochi": "高知県",
    "Saga": "佐賀県",
    "Nagasaki": "長崎県",
    "Kumamoto": "熊本県",
    "Oita": "大分県",
    "Miyazaki": "宮崎県",
    "Kagoshima": "鹿児島県",
    "Okinawa": "沖縄県",
}

_JP_PAYPAL_PREFECTURE_CODES: dict[str, str] = {
    "Tokyo": "TOKYO-TO",
    "Osaka": "OSAKA-FU",
    "Kanagawa": "KANAGAWA-KEN",
    "Aichi": "AICHI-KEN",
    "Hokkaido": "HOKKAIDO",
    "Aomori": "AOMORI-KEN",
    "Iwate": "IWATE-KEN",
    "Akita": "AKITA-KEN",
    "Yamagata": "YAMAGATA-KEN",
    "Fukushima": "FUKUSHIMA-KEN",
    "Fukuoka": "FUKUOKA-KEN",
    "Kyoto": "KYOTO-FU",
    "Hyogo": "HYOGO-KEN",
    "Miyagi": "MIYAGI-KEN",
    "Ibaraki": "IBARAKI-KEN",
    "Tochigi": "TOCHIGI-KEN",
    "Gunma": "GUNMA-KEN",
    "Saitama": "SAITAMA-KEN",
    "Chiba": "CHIBA-KEN",
    "Niigata": "NIIGATA-KEN",
    "Toyama": "TOYAMA-KEN",
    "Ishikawa": "ISHIKAWA-KEN",
    "Fukui": "FUKUI-KEN",
    "Yamanashi": "YAMANASHI-KEN",
    "Nagano": "NAGANO-KEN",
    "Gifu": "GIFU-KEN",
    "Shizuoka": "SHIZUOKA-KEN",
    "Mie": "MIE-KEN",
    "Shiga": "SHIGA-KEN",
    "Nara": "NARA-KEN",
    "Wakayama": "WAKAYAMA-KEN",
    "Tottori": "TOTTORI-KEN",
    "Shimane": "SHIMANE-KEN",
    "Okayama": "OKAYAMA-KEN",
    "Hiroshima": "HIROSHIMA-KEN",
    "Yamaguchi": "YAMAGUCHI-KEN",
    "Tokushima": "TOKUSHIMA-KEN",
    "Kagawa": "KAGAWA-KEN",
    "Ehime": "EHIME-KEN",
    "Kochi": "KOCHI-KEN",
    "Saga": "SAGA-KEN",
    "Nagasaki": "NAGASAKI-KEN",
    "Kumamoto": "KUMAMOTO-KEN",
    "Oita": "OITA-KEN",
    "Miyazaki": "MIYAZAKI-KEN",
    "Kagoshima": "KAGOSHIMA-KEN",
    "Okinawa": "OKINAWA-KEN",
}

_JP_FIRST_NAME_META: dict[str, tuple[str, str]] = {
    "Haruto": ("はると", "晴斗"),
    "Yui": ("ゆい", "結衣"),
    "Sota": ("そうた", "蒼太"),
    "Sakura": ("さくら", "桜"),
    "Ren": ("れん", "蓮"),
    "Yuna": ("ゆな", "優奈"),
    "Daiki": ("だいき", "大輝"),
    "Mio": ("みお", "美桜"),
}

_JP_LAST_NAME_META: dict[str, tuple[str, str]] = {
    "Sato": ("さとう", "佐藤"),
    "Suzuki": ("すずき", "鈴木"),
    "Takahashi": ("たかはし", "高橋"),
    "Tanaka": ("たなか", "田中"),
    "Watanabe": ("わたなべ", "渡辺"),
    "Ito": ("いとう", "伊藤"),
    "Yamamoto": ("やまもと", "山本"),
    "Nakamura": ("なかむら", "中村"),
}


def _normalize_flow2_region_mode(mode: str | None) -> str:
    value = (mode or "").strip().lower()
    if value in {"jp", "japan", "日本"}:
        return "jp"
    return "default"


def _billing_country_code(region_mode: str) -> str:
    return "JP" if _normalize_flow2_region_mode(region_mode) == "jp" else "US"


def _pick_stripe_stable_us_billing_profile(seed_key: str) -> tuple[str, str, str, str]:
    """为 Stripe hosted checkout 选稳定 US 地址，避免随机街道触发不可匹配的 Google 建议。"""
    seed = int(hashlib.sha256(str(seed_key or "").encode("utf-8")).hexdigest()[:8], 16)
    return _STRIPE_STABLE_US_BILLING_PROFILES[seed % len(_STRIPE_STABLE_US_BILLING_PROFILES)]


def _payment_form_country_code(region_mode: str, *, use_long_link: bool) -> str:
    # Short-link JP mode enters the official offer under a JP IP, then switches
    # the Stripe hosted checkout billing country to US before entering PayPal.
    if _normalize_flow2_region_mode(region_mode) == "jp" and not use_long_link:
        return "US"
    return _billing_country_code(region_mode)


def _paypal_form_country_code(region_mode: str, *, use_long_link: bool) -> str:
    # The browser/proxy remains JP in short-link JP mode, but hosted checkout
    # and PayPal billing fields are filled as US.
    return _payment_form_country_code(region_mode, use_long_link=use_long_link)


def _short_link_entry_uses_zenpic(region_mode: str, *, use_long_link: bool, card_source_mode: str | None) -> bool:
    return (
        _normalize_flow2_region_mode(region_mode) == "jp"
        and not use_long_link
        and str(card_source_mode or "").strip().lower() in {"local_random", "random_local", "local"}
    )


def _probe_flow2_proxy_country(proxy: str | None, required_country_code: str, timeout_sec: int = 12) -> tuple[bool, str]:
    from .paypal_register import _probe_proxy_country

    if not str(proxy or "").strip():
        return False, "missing proxy"
    return _probe_proxy_country(proxy, required_country_code, timeout_sec=timeout_sec)  # type: ignore[arg-type]


def _ensure_short_link_jp_proxy(proxy: str | None, *, env: dict[str, str], prefix: str) -> None:
    timeout_raw = str(env.get("PAYPAL_SHORT_LINK_JP_PROXY_CHECK_TIMEOUT") or "").strip()
    try:
        timeout_sec = max(3, int(timeout_raw or "12"))
    except ValueError:
        timeout_sec = 12
    ok, reason = _probe_flow2_proxy_country(proxy, "JP", timeout_sec=timeout_sec)
    if ok:
        log(
            f"{prefix} "
            + _zh(r"\u77ed\u94fe\u65e5\u533a\u5b98\u65b9\u8ba2\u9605\u5165\u53e3\u4ee3\u7406\u5df2\u786e\u8ba4\u4e3a\u65e5\u672c\u51fa\u53e3: ")
            + str(reason)
        )
        return
    raise RuntimeError(f"{PAYPAL_FLOW2_JP_PROXY_COUNTRY_MISMATCH}: {reason}")


def _with_billing_profile(base_card: CardInfo, billing_card: CardInfo) -> CardInfo:
    """保留原卡号/有效期/CVV，仅替换账单资料。"""
    return CardInfo(
        number=base_card.number,
        exp_month=base_card.exp_month,
        exp_year=base_card.exp_year,
        cvv=base_card.cvv,
        holder_name=billing_card.holder_name,
        first_name=billing_card.first_name,
        last_name=billing_card.last_name,
        street=billing_card.street,
        city=billing_card.city,
        state=billing_card.state,
        zip_code=billing_card.zip_code,
        country=billing_card.country,
        phone=base_card.phone,
        sms_api_url=base_card.sms_api_url,
        raw_line=base_card.raw_line,
    )


def _jp_birthdate_for_email(email: str) -> str:
    seed = int(hashlib.sha256((email or "").lower().encode("utf-8")).hexdigest()[:8], 16)
    year = 1986 + (seed % 14)  # 1986-1999
    month = 1 + ((seed >> 8) % 12)
    day = 1 + ((seed >> 16) % 28)
    return f"{year:04d}/{month:02d}/{day:02d}"


def _jp_identity_values(card: CardInfo, email: str) -> tuple[str, str, str, str, str]:
    first_key = (card.first_name or "").strip()
    last_key = (card.last_name or "").strip()
    first_kana, first_kanji = _JP_FIRST_NAME_META.get(first_key, ("だいき", "大輝"))
    last_kana, last_kanji = _JP_LAST_NAME_META.get(last_key, ("すずき", "鈴木"))
    birth = _jp_birthdate_for_email(email)
    return birth, first_kana, last_kana, first_kanji, last_kanji


def _hiragana_to_katakana(text: str) -> str:
    if not text:
        return ""
    out: list[str] = []
    for ch in text:
        code = ord(ch)
        # Hiragana range -> Katakana range
        if 0x3041 <= code <= 0x3096:
            out.append(chr(code + 0x60))
        else:
            out.append(ch)
    return "".join(out)


def is_local_random_card_mode(env: dict[str, str]) -> bool:
    source = (env.get("PAYPAL_CARD_SOURCE") or "").strip().lower()
    return source in {"local_random", "random_local", "local"}


def _luhn_check_digit(number_without_check: str) -> str:
    digits = [int(ch) for ch in number_without_check]
    total = 0
    parity = (len(digits) + 1) % 2
    for idx, value in enumerate(digits):
        if idx % 2 == parity:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return str((10 - (total % 10)) % 10)


def _build_luhn_card_number(prefix: str, random_part_length: int, *, rng: random.Random) -> str:
    body = prefix + "".join(str(rng.randint(0, 9)) for _ in range(random_part_length))
    return body + _luhn_check_digit(body)


def _normalize_card_expiry(value: str) -> tuple[str, str]:
    text = str(value or "").strip()
    parts = [p for p in re.split(r"\D+", text) if p]
    if len(parts) < 2:
        return "", ""
    first, second = parts[0], parts[1]
    try:
        first_num = int(first)
        second_num = int(second)
    except ValueError:
        return "", ""
    if first_num > 12 and 1 <= second_num <= 12:
        first, second = second, first
        first_num = second_num
    if not (1 <= first_num <= 12):
        return "", ""
    year = str(second)
    if len(year) == 2:
        year = "20" + year
    return str(first_num).zfill(2), year


def _generate_abai_visa_card(rng: random.Random) -> dict[str, str]:
    bin_prefix = _VISA_BIN_PREFIXES[rng.randrange(len(_VISA_BIN_PREFIXES))]
    random_len = 16 - len(bin_prefix) - 1
    year = time.gmtime().tm_year + rng.randint(2, 4)
    return {
        "card_number": _build_luhn_card_number(bin_prefix, random_len, rng=rng),
        "card_exp_month": str(rng.randint(1, 12)).zfill(2),
        "card_exp_year": str(year),
        "card_cvv": "".join(str(rng.randint(0, 9)) for _ in range(3)),
    }


def _normalize_meiguodizhi_billing_address(data: dict[str, Any], *, email: str, country: str) -> dict[str, str]:
    address = data.get("address") if isinstance(data, dict) else {}
    if not isinstance(address, dict):
        address = {}
    exp_month, exp_year = _normalize_card_expiry(
        str(address.get("Expires") or address.get("expires") or address.get("card_expiry") or "")
    )
    normalized = {
        "name": str(address.get("Full_Name") or address.get("name") or "").strip(),
        "line1": str(address.get("Address") or address.get("line1") or "").strip(),
        "city": str(address.get("City") or address.get("city") or "").strip(),
        "state": str(address.get("State") or address.get("state") or "").strip(),
        "postal_code": str(address.get("Zip_Code") or address.get("postal_code") or "").strip(),
        "phone": str(address.get("Telephone") or address.get("phone") or "").strip(),
        "country": str(country or "US").strip().upper() or "US",
        "email": str(email or address.get("Temporary_mail") or "").strip(),
    }
    card_number = str(
        address.get("Credit_Card_Number")
        or address.get("credit_card_number")
        or address.get("card_number")
        or ""
    ).strip()
    card_cvv = str(address.get("CVV2") or address.get("cvv") or address.get("card_cvv") or "").strip()
    if card_number:
        normalized["card_number"] = card_number
    if exp_month:
        normalized["card_exp_month"] = exp_month
    if exp_year:
        normalized["card_exp_year"] = exp_year
    if card_cvv:
        normalized["card_cvv"] = card_cvv
    return normalized


def _fetch_abai_billing_address(region: str, *, email: str, rng: random.Random) -> dict[str, str]:
    region_key = str(region or "").strip().upper()
    if region_key not in _BILLING_ADDRESS_REGION_PATHS:
        region_key = "US"
    path = _BILLING_ADDRESS_REGION_PATHS[region_key]
    last_exc: Exception | None = None
    for attempt in range(1, 4):
        try:
            resp = requests.post(
                _MEIGUODIZHI_ADDRESS_URL,
                json={"path": path, "method": "address"},
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
            address = _normalize_meiguodizhi_billing_address(
                data if isinstance(data, dict) else {},
                email=email,
                country=region_key,
            )
            missing = [key for key in ("name", "line1", "city", "state", "postal_code") if not address.get(key)]
            if missing:
                raise ValueError(f"{region_key} 地址接口返回字段不完整: {', '.join(missing)}")
            address.update(_generate_abai_visa_card(rng))
            return address
        except Exception as exc:
            last_exc = exc
            if attempt >= 3:
                break
            time.sleep(0.5 * (2 ** (attempt - 1)))
    raise RuntimeError(f"{region_key} 地址接口获取失败: {last_exc}")


def _generate_local_random_card(
    index: int,
    email: str,
    env: dict[str, str],
    *,
    region_mode: str = "default",
) -> CardInfo:
    seed_raw = f"{email.lower()}::{index}::{time.time_ns()}"
    seed = int(hashlib.sha256(seed_raw.encode("utf-8")).hexdigest()[:16], 16)
    rng = random.Random(seed)
    region_key = "JP" if _normalize_flow2_region_mode(region_mode) == "jp" else "US"

    if region_key == "JP":
        first_pool = ["Haruto", "Yui", "Sota", "Sakura", "Ren", "Yuna", "Daiki", "Mio"]
        last_pool = ["Sato", "Suzuki", "Takahashi", "Tanaka", "Watanabe", "Ito", "Yamamoto", "Nakamura"]
        profile = _RANDOM_CARD_PROFILES_JP[rng.randrange(len(_RANDOM_CARD_PROFILES_JP))]
    else:
        first_pool = ["James", "Mary", "John", "Patricia", "Robert", "Jennifer", "Michael", "Linda"]
        last_pool = ["Smith", "Johnson", "Williams", "Brown", "Davis", "Miller", "Wilson", "Moore"]
        profile = _RANDOM_CARD_PROFILES[rng.randrange(len(_RANDOM_CARD_PROFILES))]

    first_name = first_pool[rng.randrange(len(first_pool))]
    last_name = last_pool[rng.randrange(len(last_pool))]
    holder = f"{first_name} {last_name}"

    try:
        abai_address = _fetch_abai_billing_address(region_key, email=email, rng=rng)
        api_name = str(abai_address.get("name") or "").strip()
        name_parts = api_name.split()
        if len(name_parts) >= 2:
            first_name = name_parts[0]
            last_name = " ".join(name_parts[1:])
            holder = api_name
        elif api_name:
            holder = api_name
        log(
            f"PayPal flow2: 使用 aBaiAutoplus 账单生成逻辑: "
            f"region={region_key}, city={abai_address.get('city', '')}, "
            f"state={abai_address.get('state', '')}, zip={abai_address.get('postal_code', '')}"
        )
        return CardInfo(
            number=str(abai_address["card_number"]),
            exp_month=str(abai_address["card_exp_month"]).zfill(2),
            exp_year=str(abai_address["card_exp_year"])[-2:],
            cvv=str(abai_address["card_cvv"]),
            holder_name=holder,
            first_name=first_name,
            last_name=last_name,
            street=str(abai_address.get("line1") or ""),
            city=str(abai_address.get("city") or ""),
            state=str(abai_address.get("state") or ""),
            zip_code=str(abai_address.get("postal_code") or ""),
            country=str(abai_address.get("country") or region_key),
            phone=str(abai_address.get("phone") or ""),
            sms_api_url="",
            raw_line=f"ABAI_RANDOM::{region_key}::{email}::{index}",
        )
    except Exception as exc:
        log(f"PayPal flow2: aBaiAutoplus 账单生成失败，回退本地随机资料: {exc}")

    city, state, zip_code, street_base, country = profile
    street_no = rng.randint(10, 9999)
    street = f"{street_no} {street_base}"

    year = time.gmtime().tm_year + rng.randint(2, 5)
    exp_year = str(year)[-2:]
    exp_month = str(rng.randint(1, 12)).zfill(2)
    cvv = str(rng.randint(100, 999))

    custom_bin = re.sub(r"\D+", "", (env.get("PAYPAL_RANDOM_CARD_BIN") or ""))[:8]
    custom_bin_allowed = len(custom_bin) >= 6 and not custom_bin.startswith("5200")
    if custom_bin_allowed:
        bin_prefix = custom_bin
    else:
        # 固定本地随机卡头池（流程2）
        allowed_prefixes = ["485954", "490714", "491688"]
        bin_prefix = allowed_prefixes[rng.randrange(len(allowed_prefixes))]
    random_len = 15 - len(bin_prefix)
    number = _build_luhn_card_number(bin_prefix, random_len, rng=rng)

    return CardInfo(
        number=number,
        exp_month=exp_month,
        exp_year=exp_year,
        cvv=cvv,
        holder_name=holder,
        first_name=first_name,
        last_name=last_name,
        street=street,
        city=city,
        state=state,
        zip_code=zip_code,
        country=country,
        phone="",
        sms_api_url="",
        raw_line=f"LOCAL_RANDOM::{email}::{index}",
    )


def _display_proxy(proxy: str | None) -> str:
    """隐藏代理密码用于日志显示。"""
    if not proxy:
        return "无"
    text = proxy.strip()
    if "@" in text:
        prefix, suffix = text.rsplit("@", 1)
        scheme = prefix.split("://", 1)[0] + "://" if "://" in prefix else ""
        return f"{scheme}***:***@{suffix}"
    return text


def _env_bool(env: dict[str, str], key: str, default: bool = False) -> bool:
    raw = str(env.get(key) or "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on", "y"}:
        return True
    if raw in {"0", "false", "no", "off", "disabled", "none", "n"}:
        return False
    return default


def paypal_payment_mode(env: dict[str, str]) -> str:
    raw = str(env.get("PAYPAL_PAYMENT_MODE") or "").strip()
    if not raw:
        return PAYPAL_PAYMENT_MODE_LONG_LINK if _env_bool(env, "PAYPAL_USE_LONG_LINK", False) else PAYPAL_PAYMENT_MODE_SHORT_LINK
    normalized = raw.lower().replace("-", "_")
    compact = re.sub(r"[\s_]+", "", normalized)
    if compact in {
        "long",
        "link",
        "longlink",
        "longurl",
        "longpay",
        "\u957f\u94fe",
        "\u957f\u94fe\u63a5",
        "\u957f\u94fe\u652f\u4ed8",
        "\u957f\u94fe\u63a5\u652f\u4ed8",
        "\u9577\u93c8",
        "\u9577\u93c8\u652f\u4ed8",
    }:
        return PAYPAL_PAYMENT_MODE_LONG_LINK
    if compact in {
        "short",
        "shortlink",
        "shorturl",
        "direct",
        "browser",
        "offer",
        "shortpay",
        "\u77ed\u94fe",
        "\u77ed\u94fe\u63a5",
        "\u77ed\u94fe\u652f\u4ed8",
        "\u77ed\u94fe\u63a5\u652f\u4ed8",
        "\u76f4\u63a5\u652f\u4ed8",
    }:
        return PAYPAL_PAYMENT_MODE_SHORT_LINK
    if normalized in {PAYPAL_PAYMENT_MODE_LONG_LINK, "long_link_payment"}:
        return PAYPAL_PAYMENT_MODE_LONG_LINK
    if normalized in {PAYPAL_PAYMENT_MODE_SHORT_LINK, "short_link_payment"}:
        return PAYPAL_PAYMENT_MODE_SHORT_LINK
    return PAYPAL_PAYMENT_MODE_LONG_LINK if _env_bool(env, "PAYPAL_USE_LONG_LINK", False) else PAYPAL_PAYMENT_MODE_SHORT_LINK


def paypal_use_long_link(env: dict[str, str]) -> bool:
    return paypal_payment_mode(env) == PAYPAL_PAYMENT_MODE_LONG_LINK


def paypal_click_watcher_enabled(env: dict[str, str]) -> bool:
    return _env_bool(env, "PAYPAL_CLICK_WATCHER_ENABLED", False)


def paypal_direct_checkout_start_url(env: dict[str, str]) -> str:
    raw = str(env.get("PAYPAL_DIRECT_CHECKOUT_START_URL") or "").strip()
    return raw or "https://chatgpt.com/"


def _mail_source_for_account(account: MailAccount, fallback: str) -> str:
    if account.client_id and account.refresh_token:
        return "hotmail"
    if str(account.mail_url or "").strip().lower() == "imap163":
        return "domain163"
    if account.email.strip().lower().endswith("@icloud.com"):
        return "icloud_query"
    return fallback


def _strip_payment_link_from_account_line(line: str) -> str:
    parts = [part.strip() for part in str(line or "").strip().split("----")]
    if len(parts) >= 3 and parts[-1].startswith(("http://", "https://")):
        return "----".join(parts[:-1]).strip()
    return str(line or "").strip()


def _account_item_from_account(account: MailAccount, *, source: str = "registered") -> dict[str, str]:
    code_address = (account.code_address or account.mail_url or "mail").strip()
    account_line = _strip_payment_link_from_account_line(account.raw)
    return {
        "email": account.email,
        "query_code": code_address,
        "payment_link": "",
        "account_line": account_line or f"{account.email}----{code_address}",
        "source": source,
    }


def _load_direct_pay_accounts(selected_email: str = "") -> list[dict[str, str]]:
    selected = (selected_email or "").strip().lower()
    paypal_flow_state.sync_from_files(
        registered_file=REGISTER_ONLY_SUMMARY_FILE,
        link_file=LINK_POOL_FILE,
        pending_file=PENDING_AUTH_FILE,
    )
    blocked = paypal_flow_state.link_pool_blocked_emails(pending_file=PENDING_AUTH_FILE)
    blocked |= paypal_flow_state.load_manual_discarded_emails()
    state = paypal_flow_state.load_state()
    items: list[dict[str, str]] = []
    seen: set[str] = set()

    def add_account(
        account: MailAccount | None,
        *,
        source: str,
        payment_link: str = "",
        link_method: str = "",
    ) -> None:
        if not account:
            return
        email = account.email.strip().lower()
        if not email or email in seen or email in blocked:
            return
        if selected and email != selected:
            return
        seen.add(email)
        item = _account_item_from_account(account, source=source)
        if payment_link:
            item["payment_link"] = payment_link
        if link_method:
            item["link_method"] = link_method
        items.append(item)

    for path, source in ((LINK_POOL_FILE, "link_pool_account"), (REGISTER_ONLY_SUMMARY_FILE, "registered_file")):
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            add_account(parse_mail_line(line), source=source)

    for record in state.values():
        status = str(record.get("status") or "")
        if status not in {paypal_flow_state.STATUS_REGISTERED, paypal_flow_state.STATUS_LINK_READY}:
            continue
        add_account(parse_mail_line(str(record.get("account_line") or "")), source=f"state:{status}")

    return items


def _load_mail_pool_direct_accounts(cfg: dict[str, Any], selected_email: str = "") -> list[dict[str, str]]:
    """短链直付池为空时，从当前邮箱池取未处理账号继续登录/注册。"""
    selected = (selected_email or "").strip().lower()
    mail_cfg = cfg.get("mail", {}) if isinstance(cfg, dict) else {}
    accounts_path_raw = str(mail_cfg.get("accounts_file") or "").strip()
    if not accounts_path_raw:
        return []
    accounts_path = resolve_path(accounts_path_raw)
    if not accounts_path.exists():
        return []
    paypal_flow_state.sync_from_files(
        registered_file=REGISTER_ONLY_SUMMARY_FILE,
        link_file=LINK_POOL_FILE,
        pending_file=PENDING_AUTH_FILE,
    )
    blocked = paypal_flow_state.link_pool_blocked_emails(pending_file=PENDING_AUTH_FILE)
    blocked |= paypal_flow_state.flow1_blocked_emails(link_file=LINK_POOL_FILE, pending_file=PENDING_AUTH_FILE)
    blocked |= paypal_flow_state.load_manual_discarded_emails()
    state = paypal_flow_state.load_state()
    items: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in accounts_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        account = parse_mail_line(line)
        if not account:
            continue
        email = account.email.strip().lower()
        if not email or email in seen or email in blocked:
            continue
        # 已进入状态机的账号应由 registered/link_ready 入口接管；这里仅兜底全新邮箱池账号。
        if email in state:
            continue
        if selected and email != selected:
            continue
        seen.add(email)
        items.append(_account_item_from_account(account, source="mail_pool_direct"))
    return items


async def _install_click_watcher(page, email: str, *, enabled: bool, label: str = "flow2") -> Path | None:
    if not enabled:
        return None
    out_dir = resolve_path("output/paypal\u6ce8\u518c/debug/click_watcher")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{safe_filename(email)}_{int(time.time())}.jsonl"
    try:
        out_file.touch(exist_ok=True)
    except Exception:
        pass
    binding_name = "__paypalClickWatcherRecord_" + re.sub(r"[^A-Za-z0-9_]", "_", out_file.stem)

    async def _record_click(payload: dict[str, Any]) -> None:
        try:
            payload = dict(payload)
            payload.setdefault("email", email)
            payload.setdefault("label", label)
            payload.setdefault("ts", datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
            with out_file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
        except Exception:
            return

    try:
        await page.expose_function(binding_name, _record_click)
    except Exception:
        pass
    script = """(() => {
        const bindingName = __PAYPAL_CLICK_WATCHER_BINDING__;
        const installKey = "__paypalClickWatcherInstalled_" + bindingName;
        if (window[installKey]) return;
        window[installKey] = true;
        const cssPath = (el) => {
            if (!el || !el.tagName) return "";
            const parts = [];
            let node = el;
            while (node && node.nodeType === 1 && parts.length < 6) {
                let part = node.tagName.toLowerCase();
                if (node.id) {
                    part += "#" + CSS.escape(node.id);
                    parts.unshift(part);
                    break;
                }
                const cls = String(node.className || "").trim().split(/\\s+/).filter(Boolean).slice(0, 2);
                if (cls.length) part += "." + cls.map((x) => CSS.escape(x)).join(".");
                const parent = node.parentElement;
                if (parent) {
                    const same = Array.from(parent.children).filter((x) => x.tagName === node.tagName);
                    if (same.length > 1) part += `:nth-of-type(${same.indexOf(node) + 1})`;
                }
                parts.unshift(part);
                node = parent;
            }
            return parts.join(" > ");
        };
        const brief = (el) => {
            if (!el || !el.tagName) return null;
            const rect = el.getBoundingClientRect ? el.getBoundingClientRect() : {};
            return {
                tag: el.tagName || "",
                role: el.getAttribute ? (el.getAttribute("role") || "") : "",
                aria: el.getAttribute ? (el.getAttribute("aria-label") || "") : "",
                testid: el.getAttribute ? (el.getAttribute("data-testid") || "") : "",
                text: String(el.innerText || el.textContent || el.value || "").replace(/\\s+/g, " ").trim().slice(0, 140),
                selector: cssPath(el),
                x: Math.round(rect.left || 0),
                y: Math.round(rect.top || 0),
                w: Math.round(rect.width || 0),
                h: Math.round(rect.height || 0),
            };
        };
        document.addEventListener("click", (event) => {
            const target = event.target && event.target.closest
                ? event.target.closest("button, a, input, [role='button'], [role='option'], [role='menuitem'], [aria-haspopup], [data-testid], [tabindex], div, span")
                : event.target;
            if (!target) return;
            const rect = target.getBoundingClientRect ? target.getBoundingClientRect() : {};
            const path = (event.composedPath ? event.composedPath() : [])
                .filter((el) => el && el.nodeType === 1)
                .slice(0, 8)
                .map(brief)
                .filter(Boolean);
            const payload = {
                url: location.href,
                title: document.title || "",
                eventX: Math.round(event.clientX || 0),
                eventY: Math.round(event.clientY || 0),
                tag: target.tagName || "",
                role: target.getAttribute ? (target.getAttribute("role") || "") : "",
                type: target.getAttribute ? (target.getAttribute("type") || "") : "",
                text: String(target.innerText || target.textContent || target.value || "").replace(/\\s+/g, " ").trim().slice(0, 200),
                aria: target.getAttribute ? (target.getAttribute("aria-label") || "") : "",
                href: target.getAttribute ? (target.getAttribute("href") || "") : "",
                testid: target.getAttribute ? (target.getAttribute("data-testid") || "") : "",
                selector: cssPath(target),
                x: Math.round(rect.left || 0),
                y: Math.round(rect.top || 0),
                w: Math.round(rect.width || 0),
                h: Math.round(rect.height || 0),
                path,
            };
            try {
                const fn = window[bindingName];
                if (typeof fn === "function") Promise.resolve(fn(payload)).catch(() => {});
            } catch {}
        }, true);
    })()""".replace("__PAYPAL_CLICK_WATCHER_BINDING__", json.dumps(binding_name))
    try:
        await page.add_init_script(script)
        await page.evaluate(script)
        log(f"[ClickWatcher][{email}] " + _zh(r"\u5df2\u542f\u7528\u70b9\u51fb\u8bb0\u5f55: ") + str(out_file))
        return out_file
    except Exception as exc:
        log(f"[ClickWatcher][{email}] " + _zh(r"\u542f\u7528\u5931\u8d25: ") + str(exc))
        return out_file


def _is_proxy_failure(reason: str | None) -> bool:
    if is_signin_problem_retry_reason(reason):
        return True
    text = str(reason or "").lower()
    markers = (
        PAYPAL_FLOW2_STRIPE_PAYPAL_TIMEOUT,
        "30s no paypal redirect",
        "60s no paypal redirect",
        "未跳转 paypal",
        "err_socks_connection_failed",
        "err_timed_out",
        "err_tunnel_connection_failed",
        "err_no_supported_proxies",
        "err_proxy_connection_failed",
        "err_proxy_auth_unsupported",
        "err_proxy_auth_requested",
        "browser does not support socks5 proxy authentication",
        "upstream socks5",
        "proxy",
    )
    return any(marker in text for marker in markers)


def load_link_pool() -> list[dict[str, str]]:
    if not LINK_POOL_FILE.exists():
        return []
    blocked = paypal_flow_state.link_pool_blocked_emails(pending_file=PENDING_AUTH_FILE)
    state = paypal_flow_state.load_state()
    items = []
    for line in LINK_POOL_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("----")
        if len(parts) >= 3:
            payment_link = parts[-1].strip()
            account_line = "----".join(part.strip() for part in parts[:-1]).strip()
            query_code = parts[1].strip()
            if len(parts) >= 5:
                query_code = parts[0].strip()
            email = parts[0].strip()
            if email.lower() in blocked:
                continue
            item = {
                "email": email,
                "query_code": query_code,
                "payment_link": payment_link,
                "account_line": account_line,
            }
            link_method = str((state.get(email.lower()) or {}).get("link_method") or "").strip()
            if link_method:
                item["link_method"] = link_method
            items.append(item)
    return items


async def _checkout_surface_ready(page) -> bool:
    subscribe_cn = _zh(r"\u8ba2\u9605")
    pay_cn = _zh(r"\u652f\u4ed8")
    pay_jp = _zh(r"\u652f\u6255")
    paypal_continue_jp = _zh(r"\u540c\u610f\u3057\u3066\u7d9a\u884c")
    payment_method_cn = _zh(r"\u652f\u4ed8\u65b9\u5f0f")
    today_payment_jp_1 = _zh(r"\u4eca\u65e5\u306e\u304a\u652f\u6255\u3044")
    today_payment_jp_2 = _zh(r"\u672c\u65e5\u306e\u304a\u652f\u6255\u3044")
    selectors = (
        '#ProductSummary-totalAmount',
        '#OrderDetails-TotalAmount',
        '[data-testid="order-details-footer-total-amount"]',
        '[data-testid="order-details-footer-subtotal-amount"]',
        'input[autocomplete="cc-number"]',
        'input[name="cardnumber"]',
        'iframe[name*="__privateStripeFrame"]',
        f'button:has-text("{subscribe_cn}")',
        'button:has-text("Subscribe")',
        'button:has-text("Pay")',
        f'button:has-text("{pay_cn}")',
        f'button:has-text("{pay_jp}")',
        f'button:has-text("{paypal_continue_jp}")',
        f'text={payment_method_cn}',
        'text=Payment method',
        'text=Due today',
        f'text={today_payment_jp_1}',
        f'text={today_payment_jp_2}',
    )
    for selector in selectors:
        try:
            if await page.locator(selector).first.is_visible(timeout=500):
                return True
        except Exception:
            continue
    return False


async def _wait_checkout_surface_after_offer_submit(
    page,
    prefix: str,
    *,
    email: str | None = None,
    timeout_ms: int = 90_000,
) -> bool:
    """套餐提交后等待 ChatGPT 的“正在加载安全结账”过渡页跳到账单页。"""
    deadline = time.monotonic() + max(5.0, timeout_ms / 1000)
    saw_loading = False
    last_sample = ""
    last_url = ""
    while time.monotonic() < deadline:
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=1500)
        except Exception:
            pass
        if await _checkout_surface_ready(page):
            if saw_loading:
                log(f"{prefix} checkout loading finished, billing page ready: url={page.url}")
            return True
        try:
            state = await page.evaluate(
                r"""() => {
                    const text = String(document.body?.innerText || document.body?.textContent || '').replace(/\s+/g, ' ').trim();
                    const loading = /\u6b63\u5728\u52a0\u8f7d\u5b89\u5168\u7ed3\u8d26|\u65e0\u9700\u957f\u671f\u7ed1\u5b9a|\u96a8\u65f6\u53ef\u4ee5\u53d6\u6d88|loading\s+secure\s+checkout|secure\s+checkout|cancel\s+anytime/i.test(text);
                    const hasStripeShell = /OpenAI|Stripe|Payment method|Due today|\u652f\u4ed8\u65b9\u5f0f|\u4eca\u65e5\u5e94\u4ed8/.test(text);
                    return { loading, hasStripeShell, sample: text.slice(0, 220) };
                }"""
            )
            if isinstance(state, dict):
                last_sample = str(state.get("sample") or "")[:220]
                if state.get("loading"):
                    if not saw_loading:
                        log(f"{prefix} waiting checkout loading transition: {last_sample}")
                    saw_loading = True
                if state.get("hasStripeShell") and await _checkout_surface_ready(page):
                    return True
        except Exception:
            pass
        current_url = str(page.url or "")
        if current_url != last_url and ("pay.openai.com" in current_url or "checkout" in current_url):
            log(f"{prefix} checkout navigation in progress: url={current_url}")
            last_url = current_url
        await page.wait_for_timeout(1000)
    log(f"{prefix} checkout loading did not finish: url={page.url} sample={last_sample}")
    if email:
        await _save_chatgpt_offer_failure_debug_once(
            page,
            email,
            f"checkout loading did not finish after offer submit: {last_sample}",
        )
    return False


async def _detect_link_payment_invalid_long_link(page) -> dict[str, Any]:
    try:
        result = await page.evaluate(
            """() => {
                const visible = (el) => {
                    if (!el) return false;
                    const rect = el.getBoundingClientRect();
                    const style = getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const textOf = (el) => String(el?.innerText || el?.textContent || el?.getAttribute?.('aria-label') || '').replace(/\\s+/g, ' ').trim();
                const patterns = [
                    /pay\\s*with\\s*link/i,
                    /pay\\s*by\\s*link/i,
                    /\\u7528\\s*link\\s*\\u652f\\u4ed8/i,
                    /link\\s*\\u652f\\u4ed8/i,
                    /link\\s*\\u652f\\u6255/i,
                    /link\\s*\\u3067\\s*\\u652f\\u6255/i
                ];
                const actionNodes = Array.from(document.querySelectorAll('button, a, label, [role="button"], [role="radio"], [data-testid], [aria-label]'))
                    .filter(visible);
                for (const node of actionNodes) {
                    const text = textOf(node);
                    if (!text || text.length > 180) continue;
                    if (patterns.some((pattern) => pattern.test(text))) {
                        return { invalid: true, source: 'action', text: text.slice(0, 180) };
                    }
                }
                const body = textOf(document.body).slice(0, 6000);
                const hasDirectLinkPay = patterns.some((pattern) => pattern.test(body));
                const hasCheckoutContext = /payment|pay\\s|paypal|stripe|card|\\u652f\\u4ed8|\\u652f\\u6255|\\u30ab\\u30fc\\u30c9/i.test(body);
                if (hasDirectLinkPay && hasCheckoutContext) {
                    const hit = body.match(/.{0,80}(?:pay\\s*with\\s*link|pay\\s*by\\s*link|\\u7528\\s*link\\s*\\u652f\\u4ed8|link\\s*\\u652f\\u4ed8|link\\s*\\u652f\\u6255|link\\s*\\u3067\\s*\\u652f\\u6255).{0,80}/i);
                    return { invalid: true, source: 'body', text: (hit ? hit[0] : body).slice(0, 180) };
                }
                return { invalid: false, source: '', text: '' };
            }"""
        )
        return result if isinstance(result, dict) else {"invalid": False, "source": "", "text": ""}
    except Exception as exc:
        return {"invalid": False, "source": "probe_error", "text": str(exc)[:160]}


async def _dismiss_chatgpt_interstitials(page, prefix: str) -> None:
    try:
        if await page.locator('#modal-account-payment, [data-testid="modal-account-payment"]').first.is_visible(timeout=300):
            return
    except Exception:
        pass
    try:
        ready_result = await page.evaluate(
            r"""() => {
                const visible = (el) => {
                    if (!el) return false;
                    const rect = el.getBoundingClientRect();
                    const style = getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const textOf = (el) => String(el?.innerText || el?.textContent || '').replace(/\s+/g, ' ').trim();
                const body = textOf(document.body);
                const isReadyModal = /\u4f60\u5df2\u51c6\u5907\u5c31\u7eea|ChatGPT\s*\u53ef\u80fd\u4f1a\u51fa\u9519|\u7ee7\u7eed\u64cd\u4f5c\u5373\u8868\u793a\u4f60\u540c\u610f/.test(body);
                if (!isReadyModal) return { clicked: false, reason: 'no-ready-modal' };
                const blocked = document.querySelector('#modal-account-payment, [data-testid="modal-account-payment"]');
                if (blocked && visible(blocked)) return { clicked: false, reason: 'pricing-modal' };
                const buttons = Array.from(document.querySelectorAll('button, [role="button"]')).filter(visible);
                const btn = buttons.find((node) => {
                    const text = textOf(node);
                    const disabled = !!node.disabled || String(node.getAttribute?.('aria-disabled') || '').toLowerCase() === 'true';
                    return !disabled && /^(?:\u7ee7\u7eed|Continue)$/.test(text);
                });
                if (!btn) return { clicked: false, reason: 'no-continue-button' };
                try { btn.scrollIntoView({ block: 'center', inline: 'center' }); } catch (e) {}
                btn.click();
                return { clicked: true, reason: 'ready-continue', text: textOf(btn).slice(0, 80) };
            }"""
        )
        if isinstance(ready_result, dict) and ready_result.get("clicked"):
            log(f"{prefix} dismissed ChatGPT ready interstitial: {ready_result.get('text', '')}")
            await page.wait_for_timeout(900)
    except Exception:
        pass
    skip_cn = _zh(r"\u8df3\u8fc7")
    later_cn_1 = _zh(r"\u4ee5\u540e\u518d\u8bf4")
    later_cn_2 = _zh(r"\u7a0d\u540e")
    selectors = (
        f'button:has-text("{skip_cn}")',
        'button:has-text("Skip")',
        f'button:has-text("{later_cn_1}")',
        f'button:has-text("{later_cn_2}")',
        'button:has-text("Not now")',
        'button[aria-label="Close"]',
        '[role="button"][aria-label="Close"]',
    )
    for _ in range(3):
        clicked = False
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                if await locator.is_visible(timeout=500) and await locator.is_enabled(timeout=500):
                    await locator.click(timeout=1500, no_wait_after=True)
                    log(f"{prefix} dismissed ChatGPT interstitial: {selector}")
                    clicked = True
                    await page.wait_for_timeout(800)
                    break
            except Exception:
                continue
        if not clicked:
            return


async def _chatgpt_offer_modal_visible(page) -> bool:
    """判断当前是否已进入 ChatGPT 套餐选择弹层。"""
    try:
        return bool(
            await page.evaluate(
                r"""() => {
                    const visible = (el) => {
                        if (!el) return false;
                        const rect = el.getBoundingClientRect();
                        const style = getComputedStyle(el);
                        return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                    };
                    const textOf = (el) => String(el?.innerText || el?.textContent || '').replace(/\s+/g, ' ').trim();
                    const nodes = Array.from(document.querySelectorAll('#modal-account-payment, [data-testid="modal-account-payment"], [role="dialog"]'))
                        .filter(visible);
                    return nodes.some((node) => /plus|trial|free|country|currency|jpy|usd|套餐|試用|支払|支払い/i.test(textOf(node)));
                }"""
            )
        )
    except Exception:
        return False


async def _click_zero_trial_plus_option(page, prefix: str) -> bool:
    if not await _select_chatgpt_offer_region_us(page, prefix):
        log(f"{prefix} zero/free Plus trial click skipped before US region")
        return False
    if await _chatgpt_offer_modal_visible(page):
        log(f"{prefix} zero/free Plus trial click skipped inside pricing modal")
        return False
    free_trial_cn = _zh(r"\u514d\u8d39\u8bd5\u7528")
    free_trial_tw = _zh(r"\u514d\u8cbb\u8a66\u7528")
    free_trial_jp = _zh(r"\u7121\u6599\u30c8\u30e9\u30a4\u30a2\u30eb")
    trial_jp = _zh(r"\u304a\u8a66\u3057")
    zero_yen = _zh(r"0\u5186")
    zero_cn = _zh(r"0\u5143")
    selectors = (
        f'button:has-text("Plus"):has-text("{free_trial_cn}")',
        f'a:has-text("Plus"):has-text("{free_trial_cn}")',
        f'[role="button"]:has-text("Plus"):has-text("{free_trial_cn}")',
        f'button:has-text("Plus"):has-text("{free_trial_tw}")',
        f'a:has-text("Plus"):has-text("{free_trial_tw}")',
        f'button:has-text("Plus"):has-text("{free_trial_jp}")',
        f'a:has-text("Plus"):has-text("{free_trial_jp}")',
        f'button:has-text("Plus"):has-text("{trial_jp}")',
        f'a:has-text("Plus"):has-text("{trial_jp}")',
        f'button:has-text("Plus"):has-text("{zero_yen}")',
        f'a:has-text("Plus"):has-text("{zero_yen}")',
        f'button:has-text("Plus"):has-text("{zero_cn}")',
        f'a:has-text("Plus"):has-text("{zero_cn}")',
        'button:has-text("Plus"):has-text("Free trial")',
        'a:has-text("Plus"):has-text("Free trial")',
        '[role="button"]:has-text("Plus"):has-text("Free trial")',
        'button:has-text("Try Plus")',
        'a:has-text("Try Plus")',
        'button:has-text("Get Plus")',
        'a:has-text("Get Plus")',
    )
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if await locator.is_visible(timeout=600) and await locator.is_enabled(timeout=600):
                await locator.scroll_into_view_if_needed(timeout=1000)
                await locator.click(timeout=2500)
                log(f"{prefix} clicked zero/free Plus trial option: {selector}")
                await page.wait_for_timeout(1800)
                return True
        except Exception:
            continue
    try:
        clicked = await page.evaluate(
            """() => {
                const visible = (el) => {
                    if (!el) return false;
                    const rect = el.getBoundingClientRect();
                    const style = getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const textOf = (el) => String(el.innerText || el.textContent || el.getAttribute('aria-label') || '').replace(/\\s+/g, ' ').trim();
                const nodes = Array.from(document.querySelectorAll('button, a, [role="button"], [data-testid]')).filter(visible);
                const bad = /team|business|enterprise|workspace|education|\\u56e2\\u961f|\\u5718\\u968a|\\u4f01\\u4e1a|\\u4f01\\u696d|\\u30c1\\u30fc\\u30e0|\\u30d3\\u30b8\\u30cd\\u30b9|\\u30a8\\u30f3\\u30bf\\u30fc\\u30d7\\u30e9\\u30a4\\u30ba/i;
                const plus = /plus/i;
                const freeOrZero = /free\\s*trial|trial|try\\s*plus|get\\s*plus|\\$\\s*0|us\\$\\s*0|\\u00a5\\s*0|\\uffe5\\s*0|0\\s*\\u5143|0\\s*\\u5186|\\u514d\\u8d39|\\u514d\\u8cbb|\\u7121\\u6599|\\u304a\\u8a66\\u3057|\\u30c8\\u30e9\\u30a4\\u30a2\\u30eb/i;
                let target = nodes.find((node) => {
                    const text = textOf(node);
                    return text && !bad.test(text) && plus.test(text) && freeOrZero.test(text);
                });
                if (!target) {
                    target = nodes.find((node) => {
                        const text = textOf(node);
                        return text && !bad.test(text) && /try\\s*plus|get\\s*plus/i.test(text);
                    });
                }
                if (!target) return "";
                target.scrollIntoView({ block: 'center', inline: 'center' });
                target.click();
                return textOf(target).slice(0, 160);
            }"""
        )
        if clicked:
            log(f"{prefix} clicked zero/free Plus trial option by JS: {clicked}")
            await page.wait_for_timeout(1800)
            return True
    except Exception:
        pass
    return False


async def _chatgpt_offer_region_state(page) -> dict[str, Any]:
    """读取套餐弹窗右下角真实地区文本。"""
    try:
        state = await page.evaluate(
            r"""() => {
                const visible = (el) => {
                    if (!el) return false;
                    const rect = el.getBoundingClientRect();
                    const style = getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const textOf = (el) => String(el?.innerText || el?.textContent || el?.value || el?.getAttribute?.('aria-label') || '').replace(/\s+/g, ' ').trim();
                const usText = /united states|usa|\bus\b|usd|美国|美國|アメリカ|米国|estados unidos/i;
                const jpText = /japan|\bjp\b|jpy|日本|japon|japón/i;
                const node = Array.from(document.querySelectorAll('[data-testid="country-selector-in-pricing-modal"]')).filter(visible)[0] || null;
                const label = textOf(node).slice(0, 180);
                return {
                    label,
                    isUS: !!label && usText.test(label) && !jpText.test(label),
                    isJP: !!label && jpText.test(label) && !usText.test(label),
                };
            }"""
        )
        return state if isinstance(state, dict) else {"label": "", "isUS": False, "isJP": False}
    except Exception as exc:
        return {"label": f"probe-error:{exc}", "isUS": False, "isJP": False}


async def _click_chatgpt_offer_region_option_strict(page, prefix: str, target: str, mode: str) -> bool:
    """只在套餐页国家下拉真实 listbox/item 内点选，避免把父容器误当国家选项。"""
    normalized_target = str(target or "").strip().lower()
    if normalized_target not in {"us", "jp", "non_us"}:
        return False

    target_label = {"us": "US", "jp": "JP", "non_us": "non-US"}[normalized_target]
    typeahead_queries = {
        "us": ("美国", "美國", "米国", "United States", "United States of America", "US"),
        "jp": ("日本", "Japan", "JP"),
        "non_us": ("日本", "Japan"),
    }[normalized_target]
    trigger_selector = (
        '[data-testid="country-selector-in-pricing-modal"] [role="combobox"], '
        '[data-testid="country-selector-in-pricing-modal"] button'
    )

    async def _confirm_selected() -> bool:
        state = await _chatgpt_offer_region_state(page)
        if normalized_target == "us":
            return bool(state.get("isUS"))
        if normalized_target == "jp":
            return bool(state.get("isJP"))
        return bool(state.get("label")) and not bool(state.get("isUS"))

    async def _menu_state() -> dict[str, Any]:
        try:
            state = await page.evaluate(
                r"""() => {
                    const optionSelector = '[role="option"], [role="menuitem"], [data-radix-select-item], [data-radix-collection-item], [cmdk-item], [data-value]';
                    const visible = (el) => {
                        if (!el) return false;
                        const rect = el.getBoundingClientRect();
                        const style = getComputedStyle(el);
                        return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity || '1') > 0.03;
                    };
                    const textOf = (el) => String(el?.innerText || el?.textContent || el?.value || el?.getAttribute?.('aria-label') || '').replace(/\s+/g, ' ').trim();
                    const valueOf = (el) => String(el?.getAttribute?.('data-value') || el?.getAttribute?.('value') || el?.getAttribute?.('aria-label') || '').trim();
                    const trigger = document.querySelector('[data-testid="country-selector-in-pricing-modal"] [role="combobox"], [data-testid="country-selector-in-pricing-modal"] button');
                    const controlId = trigger?.getAttribute?.('aria-controls') || '';
                    const roots = [];
                    const pushRoot = (root, source) => {
                        if (!root || !visible(root) || roots.some((item) => item.root === root)) return;
                        const rows = Array.from(root.querySelectorAll(optionSelector)).filter(visible);
                        if (!rows.length) return;
                        roots.push({ root, source, rows });
                    };
                    if (controlId) pushRoot(document.getElementById(controlId), 'aria-controls');
                    const active = document.activeElement;
                    const activeDescendantId = active?.getAttribute?.('aria-activedescendant') || '';
                    if (activeDescendantId) {
                        pushRoot(document.getElementById(activeDescendantId)?.closest?.('[role="listbox"], [role="menu"], [data-radix-select-content], [data-radix-popper-content-wrapper]'), 'active-descendant');
                    }
                    pushRoot(active?.closest?.('[role="listbox"], [role="menu"], [data-radix-select-content], [data-radix-popper-content-wrapper]'), 'active-root');
                    for (const node of Array.from(document.querySelectorAll('[role="listbox"], [role="menu"], [data-radix-select-content], [data-radix-popper-content-wrapper], [data-state="open"]')).filter(visible)) {
                        pushRoot(node, 'open-node');
                    }
                    const rootInfo = roots[0] || null;
                    const activeRole = active?.getAttribute?.('role') || '';
                    const activeTag = String(active?.tagName || '').toLowerCase();
                    const activeEditable = !!(active && (active.isContentEditable || /^(input|textarea)$/.test(activeTag) || activeRole === 'textbox'));
                    return {
                        open: !!rootInfo,
                        controlId,
                        ariaExpanded: trigger?.getAttribute?.('aria-expanded') || '',
                        triggerText: textOf(trigger).slice(0, 140),
                        activeRole,
                        activeTag,
                        activeEditable,
                        activeText: textOf(active).slice(0, 120),
                        rootSource: rootInfo?.source || '',
                        rootRole: rootInfo?.root?.getAttribute?.('role') || '',
                        optionCount: rootInfo?.rows?.length || 0,
                        samples: rootInfo ? Array.from(new Set(rootInfo.rows.map(textOf).filter(Boolean))).slice(0, 10) : [],
                        valueSamples: rootInfo ? Array.from(new Set(rootInfo.rows.map(valueOf).filter(Boolean))).slice(0, 16) : []
                    };
                }"""
            )
            return state if isinstance(state, dict) else {"open": False, "reason": "invalid-menu-state"}
        except Exception as exc:
            return {"open": False, "reason": f"menu-state-error:{exc}"}

    async def _open_menu(open_mode: str) -> bool:
        try:
            before = await _menu_state()
            if before.get("open"):
                return True
            trigger = page.locator(trigger_selector).first
            if not await trigger.is_visible(timeout=900) or not await trigger.is_enabled(timeout=900):
                return False
            await trigger.scroll_into_view_if_needed(timeout=1000)
            if open_mode == "keyboard":
                await trigger.focus(timeout=1000)
                await page.keyboard.press("Enter")
            else:
                box = await trigger.bounding_box()
                if box:
                    await page.mouse.move(float(box["x"] + max(8, box["width"] - 12)), float(box["y"] + box["height"] / 2))
                    await page.mouse.down()
                    await page.wait_for_timeout(60)
                    await page.mouse.up()
                else:
                    await trigger.click(timeout=1800, force=True, no_wait_after=True)
            await page.wait_for_timeout(500)
            after = await _menu_state()
            if not after.get("open"):
                log(f"{prefix} offer region strict menu not open by {mode}/{open_mode}: {after}")
                return False
            return True
        except Exception:
            return False

    async def _find_point() -> dict[str, Any]:
        point = await page.evaluate(
            r"""(target) => {
                const optionSelector = '[role="option"], [role="menuitem"], [data-radix-select-item], [data-radix-collection-item], [cmdk-item], [data-value]';
                const visible = (el) => {
                    if (!el) return false;
                    const rect = el.getBoundingClientRect();
                    const style = getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity || '1') > 0.03;
                };
                const textOf = (el) => String(el?.innerText || el?.textContent || el?.value || el?.getAttribute?.('aria-label') || '').replace(/\s+/g, ' ').trim();
                const valueOf = (el) => String(el?.getAttribute?.('data-value') || el?.getAttribute?.('value') || el?.getAttribute?.('aria-label') || '').trim();
                const norm = (value) => String(value || '').replace(/\s+/g, ' ').trim().toLowerCase();
                const trigger = document.querySelector('[data-testid="country-selector-in-pricing-modal"] [role="combobox"], [data-testid="country-selector-in-pricing-modal"] button');
                const triggerRect = trigger?.getBoundingClientRect?.() || null;
                const controlId = trigger?.getAttribute?.('aria-controls') || '';
                const itemOf = (el) => el.closest?.(optionSelector) || el;
                const optionRows = (root) => {
                    const seen = new Set();
                    return Array.from(root.querySelectorAll(optionSelector))
                        .map(itemOf)
                        .filter((item) => {
                            if (!item || item === root || seen.has(item)) return false;
                            seen.add(item);
                            if (!visible(item)) return false;
                            const rect = item.getBoundingClientRect();
                            if (rect.width <= 0 || rect.height <= 0 || rect.width > Math.max(640, window.innerWidth * 0.55) || rect.height > 92) return false;
                            const text = textOf(item);
                            const value = valueOf(item);
                            return !!(text || value);
                        });
                };
                const roots = [];
                const pushRoot = (root, source) => {
                    if (!root || !visible(root) || roots.some((x) => x.root === root)) return;
                    const rows = optionRows(root);
                    if (!rows.length) return;
                    const labels = Array.from(new Set(rows.map((row) => norm(textOf(row) || valueOf(row))).filter(Boolean)));
                    const rect = root.getBoundingClientRect();
                    if (rect.width > window.innerWidth * 0.85 || rect.height > window.innerHeight * 0.85) return;
                    if (!labels.length) return;
                    let score = rows.length + labels.length * 3;
                    if (controlId && root.id === controlId) score += 100;
                    if (/listbox|menu/i.test(root.getAttribute?.('role') || '')) score += 30;
                    if (root.hasAttribute?.('data-radix-select-content') || /radix/i.test(String(root.className || ''))) score += 20;
                    if (triggerRect) {
                        score -= Math.abs(rect.right - triggerRect.right) / 80;
                        score -= Math.abs(rect.top - triggerRect.bottom) / 80;
                    }
                    roots.push({ root, rows, labels, score, source });
                };
                if (controlId) pushRoot(document.getElementById(controlId), 'aria-controls');
                const active = document.activeElement;
                const activeDescendantId = active?.getAttribute?.('aria-activedescendant') || '';
                if (activeDescendantId) {
                    pushRoot(document.getElementById(activeDescendantId)?.closest?.('[role="listbox"], [role="menu"], [data-radix-select-content], [data-radix-popper-content-wrapper]'), 'active-descendant');
                }
                pushRoot(active?.closest?.('[role="listbox"], [role="menu"], [data-radix-select-content], [data-radix-popper-content-wrapper]'), 'active-root');
                for (const node of Array.from(document.querySelectorAll('[role="listbox"], [role="menu"], [data-radix-select-content], [data-radix-popper-content-wrapper], [data-state="open"]')).filter(visible)) {
                    pushRoot(node, 'open-node');
                }
                roots.sort((a, b) => b.score - a.score);
                const rootInfo = roots[0] || null;
                if (!rootInfo) return { ok: false, reason: 'no-country-listbox', controlId };

                const usText = /united states|united states of america|usa|\bus\b|美国|美國|アメリカ|米国|estados unidos/i;
                const jpText = /japan|\bjp\b|日本|japon|japón/i;
                const badText = /country|currency|search|select|region|国家|地区|地域|货币|貨幣/i;
                const toOption = (item) => {
                    const rect = item.getBoundingClientRect();
                    const text = textOf(item);
                    const value = valueOf(item);
                    const label = text || value;
                    const sig = `${label} ${value}`;
                    const valueNorm = norm(value);
                    const textNorm = norm(label);
                    const isUS = valueNorm === 'us' || usText.test(sig);
                    const isJP = valueNorm === 'jp' || jpText.test(sig);
                    let matched = false;
                    if (target === 'us') matched = isUS && !isJP;
                    else if (target === 'jp') matched = isJP && !isUS;
                    else matched = !isUS && !badText.test(sig);
                    if (!matched) return null;
                    let score = 0;
                    if (/option|menuitem/i.test(item.getAttribute?.('role') || '')) score += 20;
                    if (item.hasAttribute?.('data-radix-collection-item')) score += 8;
                    if (valueNorm === target || (target === 'non_us' && isJP)) score += 12;
                    if (/^(united states|united states of america|usa|us|美国|美國|アメリカ|米国|日本|japan|jp|estados unidos)$/i.test(textNorm)) score += 10;
                    score -= rect.width * rect.height / 1000000;
                    return {
                        item,
                        label: label.slice(0, 120),
                        value,
                        score,
                        x: Math.round(rect.left + Math.max(6, Math.min(rect.width - 6, rect.width / 2))),
                        y: Math.round(rect.top + Math.max(6, Math.min(rect.height - 6, rect.height / 2)))
                    };
                };
                const matches = rootInfo.rows.map(toOption).filter(Boolean).sort((a, b) => b.score - a.score);
                const samples = Array.from(new Set(rootInfo.rows.map((row) => textOf(row)).filter(Boolean))).slice(0, 12);
                const valueSamples = Array.from(new Set(rootInfo.rows.map((row) => valueOf(row)).filter(Boolean))).slice(0, 24);
                const rowDetails = rootInfo.rows.slice(0, 18).map((row) => {
                    const rect = row.getBoundingClientRect();
                    return {
                        text: textOf(row).slice(0, 120),
                        value: valueOf(row).slice(0, 80),
                        role: row.getAttribute?.('role') || '',
                        tag: row.tagName || '',
                        x: Math.round(rect.left),
                        y: Math.round(rect.top),
                        w: Math.round(rect.width),
                        h: Math.round(rect.height)
                    };
                });
                const hit = matches[0] || null;
                if (!hit) {
                    return {
                        ok: false,
                        reason: 'target-not-visible',
                        target,
                        controlId,
                        rootSource: rootInfo.source,
                        optionCount: rootInfo.rows.length,
                        samples,
                        valueSamples,
                        rowDetails
                    };
                }
                try { hit.item.scrollIntoView({ block: 'center', inline: 'nearest' }); } catch {}
                const finalRect = hit.item.getBoundingClientRect();
                const finalX = Math.round(finalRect.left + Math.max(6, Math.min(finalRect.width - 6, finalRect.width / 2)));
                const finalY = Math.round(finalRect.top + Math.max(6, Math.min(finalRect.height - 6, finalRect.height / 2)));
                return {
                    ok: true,
                    target,
                    controlId,
                    rootSource: rootInfo.source,
                    label: hit.label,
                    value: hit.value,
                    optionCount: rootInfo.rows.length,
                    samples,
                    valueSamples,
                    rowDetails,
                    staleX: hit.x,
                    staleY: hit.y,
                    x: finalX,
                    y: finalY
                };
            }""",
            normalized_target,
        )
        return point if isinstance(point, dict) else {"ok": False, "reason": "invalid-point"}

    async def _scroll_menu() -> dict[str, Any]:
        scrolled = await page.evaluate(
            r"""() => {
                const optionSelector = '[role="option"], [role="menuitem"], [data-radix-select-item], [data-radix-collection-item], [cmdk-item], [data-value]';
                const visible = (el) => {
                    if (!el) return false;
                    const rect = el.getBoundingClientRect();
                    const style = getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const textOf = (el) => String(el?.innerText || el?.textContent || el?.value || el?.getAttribute?.('aria-label') || '').replace(/\s+/g, ' ').trim();
                const trigger = document.querySelector('[data-testid="country-selector-in-pricing-modal"] [role="combobox"], [data-testid="country-selector-in-pricing-modal"] button');
                const controlId = trigger?.getAttribute?.('aria-controls') || '';
                const roots = [];
                const rowsOf = (root) => Array.from(root.querySelectorAll(optionSelector)).filter(visible);
                const pushRoot = (root) => {
                    if (!root || !visible(root) || roots.includes(root) || !rowsOf(root).length) return;
                    roots.push(root);
                };
                if (controlId) pushRoot(document.getElementById(controlId));
                pushRoot(document.activeElement?.closest?.('[role="listbox"], [role="menu"], [data-radix-select-content], [data-radix-popper-content-wrapper]'));
                for (const node of Array.from(document.querySelectorAll('[role="listbox"], [role="menu"], [data-radix-select-content], [data-radix-popper-content-wrapper], [data-state="open"]')).filter(visible)) {
                    pushRoot(node);
                }
                const root = roots[0] || null;
                if (!root) return { ok: false, reason: 'no-root', controlId };
                const scrollables = Array.from(root.querySelectorAll('[data-radix-select-viewport], [data-radix-scroll-area-viewport], [style*="overflow"], div'))
                    .filter(visible)
                    .filter((el) => el.scrollHeight > el.clientHeight + 8);
                if (root.scrollHeight > root.clientHeight + 8) scrollables.push(root);
                const hit = scrollables.sort((a, b) => (b.scrollHeight - b.clientHeight) - (a.scrollHeight - a.clientHeight))[0] || null;
                if (!hit) return { ok: false, reason: 'no-scrollable', controlId, samples: rowsOf(root).map(textOf).filter(Boolean).slice(0, 8) };
                const before = hit.scrollTop;
                hit.scrollTop = Math.min(hit.scrollHeight, hit.scrollTop + Math.max(220, Math.floor(hit.clientHeight * 0.9)));
                hit.dispatchEvent(new Event('scroll', { bubbles: true }));
                return {
                    ok: hit.scrollTop !== before,
                    reason: hit.scrollTop === before ? 'end' : 'scrolled',
                    controlId,
                    before,
                    after: hit.scrollTop,
                    samples: rowsOf(root).map(textOf).filter(Boolean).slice(0, 8)
                };
            }"""
        )
        return scrolled if isinstance(scrolled, dict) else {"ok": False, "reason": "invalid-scroll"}

    async def _activate_matched_option_by_dom(open_mode: str) -> dict[str, Any]:
        """Radix Select 对鼠标坐标很敏感；命中选项后直接向真实 option 派发选择事件。"""
        try:
            result = await page.evaluate(
                r"""(target) => {
                    const optionSelector = '[role="option"], [role="menuitem"], [data-radix-select-item], [data-radix-collection-item], [cmdk-item], [data-value]';
                    const visible = (el) => {
                        if (!el) return false;
                        const rect = el.getBoundingClientRect();
                        const style = getComputedStyle(el);
                        return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity || '1') > 0.03;
                    };
                    const textOf = (el) => String(el?.innerText || el?.textContent || el?.value || el?.getAttribute?.('aria-label') || '').replace(/\s+/g, ' ').trim();
                    const valueOf = (el) => String(el?.getAttribute?.('data-value') || el?.getAttribute?.('value') || el?.getAttribute?.('aria-label') || '').trim();
                    const norm = (value) => String(value || '').replace(/\s+/g, ' ').trim().toLowerCase();
                    const trigger = document.querySelector('[data-testid="country-selector-in-pricing-modal"] [role="combobox"], [data-testid="country-selector-in-pricing-modal"] button');
                    const controlId = trigger?.getAttribute?.('aria-controls') || '';
                    const roots = [];
                    const pushRoot = (root, source) => {
                        if (!root || !visible(root) || roots.some((item) => item.root === root)) return;
                        const rows = Array.from(root.querySelectorAll(optionSelector)).filter(visible);
                        if (!rows.length) return;
                        let score = rows.length;
                        if (controlId && root.id === controlId) score += 100;
                        if (/listbox|menu/i.test(root.getAttribute?.('role') || '')) score += 30;
                        roots.push({ root, source, rows, score });
                    };
                    if (controlId) pushRoot(document.getElementById(controlId), 'aria-controls');
                    const active = document.activeElement;
                    const activeDescendantId = active?.getAttribute?.('aria-activedescendant') || '';
                    if (activeDescendantId) {
                        pushRoot(document.getElementById(activeDescendantId)?.closest?.('[role="listbox"], [role="menu"], [data-radix-select-content], [data-radix-popper-content-wrapper]'), 'active-descendant');
                    }
                    pushRoot(active?.closest?.('[role="listbox"], [role="menu"], [data-radix-select-content], [data-radix-popper-content-wrapper]'), 'active-root');
                    for (const node of Array.from(document.querySelectorAll('[role="listbox"], [role="menu"], [data-radix-select-content], [data-radix-popper-content-wrapper], [data-state="open"]')).filter(visible)) {
                        pushRoot(node, 'open-node');
                    }
                    roots.sort((a, b) => b.score - a.score);
                    const rootInfo = roots[0] || null;
                    if (!rootInfo) return { ok: false, reason: 'no-root', controlId };
                    const usText = /united states|united states of america|usa|\bus\b|美国|美國|アメリカ|米国|estados unidos/i;
                    const jpText = /japan|\bjp\b|日本|japon|japón/i;
                    const badText = /country|currency|search|select|region|国家|地区|地域|货币|貨幣/i;
                    const matchRow = () => {
                        const rows = rootInfo.rows.map((row) => row.closest?.(optionSelector) || row);
                        return rows.find((row) => {
                            const sig = `${textOf(row)} ${valueOf(row)}`;
                            const valueNorm = norm(valueOf(row));
                            const isUS = valueNorm === 'us' || usText.test(sig);
                            const isJP = valueNorm === 'jp' || jpText.test(sig);
                            if (target === 'us') return isUS && !isJP;
                            if (target === 'jp') return isJP && !isUS;
                            return !isUS && !badText.test(sig);
                        }) || null;
                    };
                    let hit = matchRow();
                    const rows = rootInfo.rows.map((row) => row.closest?.(optionSelector) || row);
                    if (!hit) {
                        return {
                            ok: false,
                            reason: 'target-not-found',
                            controlId,
                            optionCount: rows.length,
                            samples: Array.from(new Set(rows.map(textOf).filter(Boolean))).slice(0, 12),
                            valueSamples: Array.from(new Set(rows.map(valueOf).filter(Boolean))).slice(0, 16)
                        };
                    }
                    try { hit.scrollIntoView({ block: 'center', inline: 'nearest' }); } catch {}
                    hit = matchRow() || hit;
                    const rect = hit.getBoundingClientRect();
                    const x = rect.left + Math.max(6, Math.min(rect.width - 6, rect.width / 2));
                    const y = rect.top + Math.max(6, Math.min(rect.height - 6, rect.height / 2));
                    const pointerUp = { bubbles: true, cancelable: true, composed: true, view: window, clientX: x, clientY: y, button: 0, buttons: 0, pointerId: 1, pointerType: 'mouse', isPrimary: true };
                    const pointerDown = { ...pointerUp, buttons: 1 };
                    const mouseUp = { bubbles: true, cancelable: true, composed: true, view: window, clientX: x, clientY: y, button: 0, buttons: 0 };
                    const mouseDown = { ...mouseUp, buttons: 1 };
                    const keyInit = { bubbles: true, cancelable: true, composed: true, key: 'Enter', code: 'Enter', keyCode: 13, which: 13 };
                    const hoverTarget = document.elementFromPoint(x, y) || hit;
                    const targets = Array.from(new Set([hit, hoverTarget]));
                    for (const el of targets) {
                        try { el.focus?.({ preventScroll: true }); } catch {}
                        for (const type of ['pointerover', 'pointerenter', 'pointermove']) {
                            try { el.dispatchEvent(new PointerEvent(type, pointerUp)); } catch { el.dispatchEvent(new MouseEvent(type.replace('pointer', 'mouse'), mouseUp)); }
                        }
                        for (const type of ['mouseover', 'mouseenter', 'mousemove']) {
                            el.dispatchEvent(new MouseEvent(type, mouseUp));
                        }
                        try { el.dispatchEvent(new PointerEvent('pointerdown', pointerDown)); } catch { el.dispatchEvent(new MouseEvent('mousedown', mouseDown)); }
                        el.dispatchEvent(new MouseEvent('mousedown', mouseDown));
                        try { el.dispatchEvent(new PointerEvent('pointerup', pointerUp)); } catch { el.dispatchEvent(new MouseEvent('mouseup', mouseUp)); }
                        el.dispatchEvent(new MouseEvent('mouseup', mouseUp));
                        el.dispatchEvent(new MouseEvent('click', mouseUp));
                        el.dispatchEvent(new KeyboardEvent('keydown', keyInit));
                        el.dispatchEvent(new KeyboardEvent('keyup', keyInit));
                    }
                    try { hit.click(); } catch {}
                    return {
                        ok: true,
                        controlId,
                        rootSource: rootInfo.source,
                        label: textOf(hit).slice(0, 120),
                        value: valueOf(hit).slice(0, 80),
                        x: Math.round(x),
                        y: Math.round(y),
                        optionCount: rows.length
                    };
                }""",
                normalized_target,
            )
            return result if isinstance(result, dict) else {"ok": False, "reason": "invalid-dom-activate"}
        except Exception as exc:
            return {"ok": False, "reason": f"dom-activate-error:{exc}", "mode": open_mode}

    async def _click_matched_option_by_locator(open_mode: str) -> dict[str, Any]:
        """给已命中的国家 option 临时打标，再用 Playwright 真实点击，避免只点坐标不触发 Radix 选择。"""
        marker = f"paypal-region-{normalized_target}-{int(time.time() * 1000)}"
        try:
            marked = await page.evaluate(
                r"""({ target, marker }) => {
                    const optionSelector = '[role="option"], [role="menuitem"], [data-radix-select-item], [data-radix-collection-item], [cmdk-item], [data-value]';
                    const visible = (el) => {
                        if (!el) return false;
                        const rect = el.getBoundingClientRect();
                        const style = getComputedStyle(el);
                        return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity || '1') > 0.03;
                    };
                    const textOf = (el) => String(el?.innerText || el?.textContent || el?.value || el?.getAttribute?.('aria-label') || '').replace(/\s+/g, ' ').trim();
                    const valueOf = (el) => String(el?.getAttribute?.('data-value') || el?.getAttribute?.('value') || el?.getAttribute?.('aria-label') || '').trim();
                    const norm = (value) => String(value || '').replace(/\s+/g, ' ').trim().toLowerCase();
                    const trigger = document.querySelector('[data-testid="country-selector-in-pricing-modal"] [role="combobox"], [data-testid="country-selector-in-pricing-modal"] button');
                    const controlId = trigger?.getAttribute?.('aria-controls') || '';
                    const roots = [];
                    const pushRoot = (root, source) => {
                        if (!root || !visible(root) || roots.some((item) => item.root === root)) return;
                        const rows = Array.from(root.querySelectorAll(optionSelector)).filter(visible);
                        if (!rows.length) return;
                        let score = rows.length;
                        if (controlId && root.id === controlId) score += 100;
                        if (/listbox|menu/i.test(root.getAttribute?.('role') || '')) score += 30;
                        roots.push({ root, source, rows, score });
                    };
                    if (controlId) pushRoot(document.getElementById(controlId), 'aria-controls');
                    const active = document.activeElement;
                    const activeDescendantId = active?.getAttribute?.('aria-activedescendant') || '';
                    if (activeDescendantId) {
                        pushRoot(document.getElementById(activeDescendantId)?.closest?.('[role="listbox"], [role="menu"], [data-radix-select-content], [data-radix-popper-content-wrapper]'), 'active-descendant');
                    }
                    pushRoot(active?.closest?.('[role="listbox"], [role="menu"], [data-radix-select-content], [data-radix-popper-content-wrapper]'), 'active-root');
                    for (const node of Array.from(document.querySelectorAll('[role="listbox"], [role="menu"], [data-radix-select-content], [data-radix-popper-content-wrapper], [data-state="open"]')).filter(visible)) {
                        pushRoot(node, 'open-node');
                    }
                    roots.sort((a, b) => b.score - a.score);
                    const rootInfo = roots[0] || null;
                    if (!rootInfo) return { ok: false, reason: 'no-root', controlId };
                    const usText = /united states|united states of america|usa|\bus\b|美国|美國|アメリカ|米国|estados unidos/i;
                    const jpText = /japan|\bjp\b|日本|japon|japón/i;
                    const badText = /country|currency|search|select|region|国家|地区|地域|货币|貨幣/i;
                    const rowsOf = () => Array.from(rootInfo.root.querySelectorAll(optionSelector))
                        .filter(visible)
                        .map((row) => row.closest?.(optionSelector) || row);
                    const matchRow = () => rowsOf().find((row) => {
                        const sig = `${textOf(row)} ${valueOf(row)}`;
                        const valueNorm = norm(valueOf(row));
                        const isUS = valueNorm === 'us' || usText.test(sig);
                        const isJP = valueNorm === 'jp' || jpText.test(sig);
                        if (target === 'us') return isUS && !isJP;
                        if (target === 'jp') return isJP && !isUS;
                        return !isUS && !badText.test(sig);
                    }) || null;
                    let hit = matchRow();
                    if (!hit) {
                        const rows = rowsOf();
                        return {
                            ok: false,
                            reason: 'target-not-found',
                            controlId,
                            optionCount: rows.length,
                            samples: Array.from(new Set(rows.map(textOf).filter(Boolean))).slice(0, 12),
                            valueSamples: Array.from(new Set(rows.map(valueOf).filter(Boolean))).slice(0, 16)
                        };
                    }
                    try { hit.scrollIntoView({ block: 'center', inline: 'nearest' }); } catch {}
                    hit = matchRow() || hit;
                    for (const node of Array.from(document.querySelectorAll('[data-paypal-offer-region-target]'))) {
                        node.removeAttribute('data-paypal-offer-region-target');
                    }
                    hit.setAttribute('data-paypal-offer-region-target', marker);
                    const rect = hit.getBoundingClientRect();
                    return {
                        ok: true,
                        controlId,
                        rootSource: rootInfo.source,
                        label: textOf(hit).slice(0, 120),
                        value: valueOf(hit).slice(0, 80),
                        x: Math.round(rect.left + rect.width / 2),
                        y: Math.round(rect.top + rect.height / 2),
                        optionCount: rowsOf().length
                    };
                }""",
                {"target": normalized_target, "marker": marker},
            )
            if not isinstance(marked, dict) or not marked.get("ok"):
                return marked if isinstance(marked, dict) else {"ok": False, "reason": "invalid-marker"}
            option = page.locator(f'[data-paypal-offer-region-target="{marker}"]').first
            await option.hover(timeout=1200, force=True)
            await option.click(timeout=2600, force=True, no_wait_after=True)
            await page.wait_for_timeout(900)
            return {**marked, "ok": True, "mode": f"locator:{open_mode}"}
        except Exception as exc:
            return {"ok": False, "reason": f"locator-activate-error:{exc}", "mode": open_mode}

    async def _typeahead_confirm(query: str, open_mode: str) -> bool:
        if not query or not await _open_menu(open_mode):
            return False
        try:
            trigger = page.locator(trigger_selector).first
            if await trigger.is_visible(timeout=600):
                await trigger.focus(timeout=800)
        except Exception:
            pass
        safe_state = await page.evaluate(
            r"""(target) => {
                const optionSelector = '[role="option"], [role="menuitem"], [data-radix-select-item], [data-radix-collection-item], [cmdk-item], [data-value]';
                const visible = (el) => {
                    if (!el) return false;
                    const rect = el.getBoundingClientRect();
                    const style = getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity || '1') > 0.03;
                };
                const textOf = (el) => String(el?.innerText || el?.textContent || el?.value || el?.getAttribute?.('aria-label') || '').replace(/\s+/g, ' ').trim();
                const valueOf = (el) => String(el?.getAttribute?.('data-value') || el?.getAttribute?.('value') || el?.getAttribute?.('aria-label') || '').trim();
                const active = document.activeElement;
                const activeRole = active?.getAttribute?.('role') || '';
                const activeTag = String(active?.tagName || '').toLowerCase();
                const activeEditable = !!(active && (active.isContentEditable || /^(input|textarea)$/.test(activeTag) || activeRole === 'textbox'));
                const trigger = document.querySelector('[data-testid="country-selector-in-pricing-modal"] [role="combobox"], [data-testid="country-selector-in-pricing-modal"] button');
                const controlId = trigger?.getAttribute?.('aria-controls') || '';
                const roots = [];
                const pushRoot = (root, source) => {
                    if (!root || !visible(root) || roots.some((item) => item.root === root)) return;
                    const rows = Array.from(root.querySelectorAll(optionSelector)).filter(visible);
                    if (!rows.length) return;
                    roots.push({ root, source, rows });
                };
                if (controlId) pushRoot(document.getElementById(controlId), 'aria-controls');
                const activeDescendantId = active?.getAttribute?.('aria-activedescendant') || '';
                const activeDescendant = activeDescendantId ? document.getElementById(activeDescendantId) : null;
                if (activeDescendant) {
                    pushRoot(activeDescendant.closest?.('[role="listbox"], [role="menu"], [data-radix-select-content], [data-radix-popper-content-wrapper]'), 'active-descendant');
                }
                pushRoot(active?.closest?.('[role="listbox"], [role="menu"], [data-radix-select-content], [data-radix-popper-content-wrapper]'), 'active-root');
                for (const node of Array.from(document.querySelectorAll('[role="listbox"], [role="menu"], [data-radix-select-content], [data-radix-popper-content-wrapper], [data-state="open"]')).filter(visible)) {
                    pushRoot(node, 'open-node');
                }
                const rootInfo = roots[0] || null;
                const highlighted = rootInfo ? Array.from(rootInfo.root.querySelectorAll('[data-highlighted], [aria-selected="true"], [data-state="checked"], [aria-current="true"]')).filter(visible)[0] || null : null;
                const usText = /united states|united states of america|usa|\bus\b|美国|美國|アメリカ|米国|estados unidos/i;
                const jpText = /japan|\bjp\b|日本|japon|japón/i;
                const targetPattern = target === 'us' ? usText : jpText;
                const antiPattern = target === 'us' ? jpText : usText;
                const rows = rootInfo?.rows || [];
                const matched = rows.find((row) => targetPattern.test(`${textOf(row)} ${valueOf(row)}`) && !antiPattern.test(`${textOf(row)} ${valueOf(row)}`)) || null;
                const activeText = textOf(activeDescendant) || textOf(highlighted) || textOf(active);
                const highlightedText = textOf(highlighted);
                const activeSig = `${activeText} ${valueOf(activeDescendant || highlighted || active)}`;
                const safeFocus = !!rootInfo && !activeEditable && (
                    active === trigger ||
                    activeRole === 'combobox' ||
                    (active && rootInfo.root.contains(active)) ||
                    active === document.body
                );
                return {
                    ok: safeFocus,
                    controlId,
                    activeRole,
                    activeTag,
                    activeEditable,
                    activeText: activeText.slice(0, 120),
                    highlightedText: highlightedText.slice(0, 120),
                    rootSource: rootInfo?.source || '',
                    optionCount: rows.length,
                    matchedText: matched ? textOf(matched).slice(0, 120) : '',
                    matchedValue: matched ? valueOf(matched).slice(0, 80) : '',
                    targetActive: targetPattern.test(activeSig) && !antiPattern.test(activeSig),
                    samples: Array.from(new Set(rows.map(textOf).filter(Boolean))).slice(0, 10),
                    valueSamples: Array.from(new Set(rows.map(valueOf).filter(Boolean))).slice(0, 16)
                };
            }""",
            normalized_target,
        )
        if not isinstance(safe_state, dict) or not safe_state.get("ok"):
            log(f"{prefix} offer region strict typeahead skipped unsafe focus {target_label}: {safe_state}")
            return False
        try:
            await page.keyboard.type(query, delay=30)
            await page.wait_for_timeout(420)
            focus_state = await page.evaluate(
                r"""(target) => {
                    const optionSelector = '[role="option"], [role="menuitem"], [data-radix-select-item], [data-radix-collection-item], [cmdk-item], [data-value]';
                    const visible = (el) => {
                        if (!el) return false;
                        const rect = el.getBoundingClientRect();
                        const style = getComputedStyle(el);
                        return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity || '1') > 0.03;
                    };
                    const textOf = (el) => String(el?.innerText || el?.textContent || el?.value || el?.getAttribute?.('aria-label') || '').replace(/\s+/g, ' ').trim();
                    const valueOf = (el) => String(el?.getAttribute?.('data-value') || el?.getAttribute?.('value') || el?.getAttribute?.('aria-label') || '').trim();
                    const active = document.activeElement;
                    const trigger = document.querySelector('[data-testid="country-selector-in-pricing-modal"] [role="combobox"], [data-testid="country-selector-in-pricing-modal"] button');
                    const controlId = trigger?.getAttribute?.('aria-controls') || '';
                    const roots = [];
                    const pushRoot = (root, source) => {
                        if (!root || !visible(root) || roots.some((item) => item.root === root)) return;
                        const rows = Array.from(root.querySelectorAll(optionSelector)).filter(visible);
                        if (!rows.length) return;
                        roots.push({ root, source, rows });
                    };
                    if (controlId) pushRoot(document.getElementById(controlId), 'aria-controls');
                    const activeDescendantId = active?.getAttribute?.('aria-activedescendant') || '';
                    const activeDescendant = activeDescendantId ? document.getElementById(activeDescendantId) : null;
                    if (activeDescendant) {
                        pushRoot(activeDescendant.closest?.('[role="listbox"], [role="menu"], [data-radix-select-content], [data-radix-popper-content-wrapper]'), 'active-descendant');
                    }
                    pushRoot(active?.closest?.('[role="listbox"], [role="menu"], [data-radix-select-content], [data-radix-popper-content-wrapper]'), 'active-root');
                    for (const node of Array.from(document.querySelectorAll('[role="listbox"], [role="menu"], [data-radix-select-content], [data-radix-popper-content-wrapper], [data-state="open"]')).filter(visible)) {
                        pushRoot(node, 'open-node');
                    }
                    const rootInfo = roots[0] || null;
                    const rows = rootInfo?.rows || [];
                    const highlighted = rootInfo ? Array.from(rootInfo.root.querySelectorAll('[data-highlighted], [aria-selected="true"], [data-state="checked"], [aria-current="true"]')).filter(visible)[0] || null : null;
                    const usText = /united states|united states of america|usa|\bus\b|美国|美國|アメリカ|米国|estados unidos/i;
                    const jpText = /japan|\bjp\b|日本|japon|japón/i;
                    const targetPattern = target === 'us' ? usText : jpText;
                    const antiPattern = target === 'us' ? jpText : usText;
                    const activeText = textOf(activeDescendant) || textOf(highlighted) || textOf(active);
                    const activeValue = valueOf(activeDescendant || highlighted || active);
                    const matched = rows.find((row) => targetPattern.test(`${textOf(row)} ${valueOf(row)}`) && !antiPattern.test(`${textOf(row)} ${valueOf(row)}`)) || null;
                    return {
                        open: !!rootInfo,
                        controlId,
                        rootSource: rootInfo?.source || '',
                        optionCount: rows.length,
                        activeText: activeText.slice(0, 120),
                        activeValue: activeValue.slice(0, 80),
                        highlightedText: textOf(highlighted).slice(0, 120),
                        matchedText: matched ? textOf(matched).slice(0, 120) : '',
                        matchedValue: matched ? valueOf(matched).slice(0, 80) : '',
                        targetActive: targetPattern.test(`${activeText} ${activeValue}`) && !antiPattern.test(`${activeText} ${activeValue}`),
                        samples: Array.from(new Set(rows.map(textOf).filter(Boolean))).slice(0, 10),
                        valueSamples: Array.from(new Set(rows.map(valueOf).filter(Boolean))).slice(0, 16)
                    };
                }""",
                normalized_target,
            )
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(1200)
            if await _confirm_selected():
                state = await _chatgpt_offer_region_state(page)
                log(
                    f"{prefix} offer region strict selected {target_label} by typeahead {mode}/{open_mode}: "
                    f"query={query} -> {state.get('label', '')}"
                )
                return True
            after = await _chatgpt_offer_region_state(page)
            log(
                f"{prefix} offer region strict typeahead unconfirmed {target_label}: "
                f"query={query} state={focus_state} -> {after.get('label', '')}"
            )
        except Exception as exc:
            log(f"{prefix} offer region strict typeahead failed {target_label}: query={query} err={exc}")
        return False

    last_state: dict[str, Any] = {}
    for open_mode in ("keyboard", "mouse"):
        if not await _open_menu(open_mode):
            continue
        for query in ("", *typeahead_queries):
            if query:
                if await _typeahead_confirm(query, open_mode):
                    return True
                await _open_menu(open_mode)
            for _ in range(20):
                point = await _find_point()
                last_state = point
                if point.get("ok"):
                    locator_pick = await _click_matched_option_by_locator(open_mode)
                    if await _confirm_selected():
                        state = await _chatgpt_offer_region_state(page)
                        log(
                            f"{prefix} offer region strict selected {target_label} by {mode}/{open_mode}/locator: "
                            f"{locator_pick.get('label', '') or point.get('label', '')} -> {state.get('label', '')}"
                        )
                        return True
                    if locator_pick.get("ok"):
                        state = await _chatgpt_offer_region_state(page)
                        log(
                            f"{prefix} offer region strict locator pick unconfirmed {target_label}: "
                            f"{locator_pick.get('label', '')} -> {state.get('label', '')}"
                        )
                    dom_pick = await _activate_matched_option_by_dom(open_mode)
                    await page.wait_for_timeout(900)
                    if await _confirm_selected():
                        state = await _chatgpt_offer_region_state(page)
                        log(
                            f"{prefix} offer region strict selected {target_label} by {mode}/{open_mode}/dom: "
                            f"{dom_pick.get('label', '') or point.get('label', '')} -> {state.get('label', '')}"
                        )
                        return True
                    if dom_pick.get("ok"):
                        state = await _chatgpt_offer_region_state(page)
                        log(
                            f"{prefix} offer region strict DOM pick unconfirmed {target_label}: "
                            f"{dom_pick.get('label', '')} -> {state.get('label', '')}"
                        )
                    await page.mouse.click(float(point.get("x") or 0), float(point.get("y") or 0), delay=70)
                    await page.wait_for_timeout(1400)
                    if await _confirm_selected():
                        state = await _chatgpt_offer_region_state(page)
                        log(
                            f"{prefix} offer region strict selected {target_label} by {mode}/{open_mode}: "
                            f"{point.get('label', '')} -> {state.get('label', '')}"
                        )
                        return True
                    state = await _chatgpt_offer_region_state(page)
                    log(
                        f"{prefix} offer region strict click unconfirmed {target_label}: "
                        f"{point.get('label', '')} -> {state.get('label', '')}"
                    )
                    break
                scrolled = await _scroll_menu()
                if not scrolled.get("ok"):
                    last_state = {**point, "scroll": scrolled}
                    break
                await page.wait_for_timeout(180)
            if query != typeahead_queries[-1]:
                await _open_menu(open_mode)

    log(f"{prefix} offer region strict {target_label} not selected by {mode}: {last_state}")
    return False


async def _select_chatgpt_offer_region_us(page, prefix: str, *, force: bool = False) -> bool:
    async def _probe_region() -> dict[str, Any]:
        try:
            result = await page.evaluate(
                r"""() => {
                    const visible = (el) => {
                        if (!el) return false;
                        const rect = el.getBoundingClientRect();
                        const style = getComputedStyle(el);
                        return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                    };
                    const textOf = (el) => String(el?.innerText || el?.textContent || el?.value || el?.getAttribute?.('aria-label') || '').replace(/\s+/g, ' ').trim();
                    const usText = /united states|usa|\bus\b|usd|美国|美國|アメリカ|米国/i;
                    const jpText = /japan|\bjp\b|jpy|日本/i;
                    const candidates = [];
                    const modalVisible = Array.from(document.querySelectorAll('#modal-account-payment, [data-testid="modal-account-payment"], [role="dialog"]'))
                        .filter(visible)
                        .some((node) => /plus|trial|free|country|currency|jpy|usd|套餐|試用|支払|支払い/i.test(textOf(node)));
                    const exactRegion = Array.from(document.querySelectorAll('[data-testid="country-selector-in-pricing-modal"]'))
                        .filter(visible)
                        .map((el) => ({ label: textOf(el), source: 'country-selector-in-pricing-modal' }))
                        .find((item) => item.label);
                    if (exactRegion) {
                        const label = String(exactRegion.label || '').trim();
                        return {
                            ok: usText.test(label) && !jpText.test(label),
                            isJP: jpText.test(label) && !usText.test(label),
                            label: label.slice(0, 180),
                            source: exactRegion.source
                        };
                    }
                    if (!modalVisible) {
                        return { ok: false, isJP: false, label: '', source: 'missing-modal' };
                    }

                    for (const sel of Array.from(document.querySelectorAll('select')).filter(visible)) {
                        const opt = sel.options && sel.options[sel.selectedIndex >= 0 ? sel.selectedIndex : 0];
                        const label = [sel.value || '', opt?.text || opt?.label || '', sel.getAttribute('aria-label') || ''].join(' ').trim();
                        if (/country|region|currency|国家|地区|地域|国/i.test(label) || usText.test(label) || jpText.test(label)) {
                            candidates.push({ label, source: 'select' });
                        }
                    }

                    const nodes = Array.from(document.querySelectorAll('button, [role="button"], [role="combobox"], [aria-haspopup], [data-testid], div, span'))
                        .filter(visible)
                        .filter((el) => {
                            const rect = el.getBoundingClientRect();
                            if (rect.top < window.innerHeight * 0.38 || rect.left < window.innerWidth * 0.38) return false;
                            if (rect.width > window.innerWidth * 0.45 || rect.height > window.innerHeight * 0.22) return false;
                            const text = textOf(el);
                            if (!text || text.length > 80) return false;
                            if (/open image|chatgpt said|you said|chatgpt can make mistakes|ask anything/i.test(text)) return false;
                            return /country|currency|region|japan|united states|\bus\b|usa|usd|jpy|国家|地区|地域|国|日本|美国|美國|アメリカ|米国/i.test(text);
                        })
                        .sort((a, b) => {
                            const ar = a.getBoundingClientRect();
                            const br = b.getBoundingClientRect();
                            const aScore = (ar.top / Math.max(1, window.innerHeight)) + (ar.left / Math.max(1, window.innerWidth));
                            const bScore = (br.top / Math.max(1, window.innerHeight)) + (br.left / Math.max(1, window.innerWidth));
                            const aText = textOf(a);
                            const bText = textOf(b);
                            const aSpecific = /country|currency|国家|地区|地域|国/i.test(aText) ? 1 : 0;
                            const bSpecific = /country|currency|国家|地区|地域|国/i.test(bText) ? 1 : 0;
                            return (bSpecific - aSpecific) || (bScore - aScore);
                        });
                    if (nodes.length) candidates.push({ label: textOf(nodes[0]), source: 'bottom-right' });

                    const best = candidates[0] || { label: '', source: 'missing' };
                    const label = String(best.label || '').trim();
                    const isUS = usText.test(label) && !jpText.test(label);
                    const isJP = jpText.test(label) && !usText.test(label);
                    return { ok: isUS, isJP, label: label.slice(0, 180), source: best.source || 'missing' };
                }"""
            )
            return result if isinstance(result, dict) else {"ok": False, "label": "", "source": "invalid"}
        except Exception as exc:
            return {"ok": False, "label": f"probe-error:{exc}", "source": "exception"}

    before = await _probe_region()
    if before.get("ok") and not force:
        log(f"{prefix} offer region already US: {before.get('source', '')} {before.get('label', '')}")
        return True
    if before.get("ok") and force:
        log(f"{prefix} offer region US label found, force reselect before submit: {before.get('label', '')}")

    try:
        result = await page.evaluate(
            r"""() => {
                const visible = (el) => {
                    if (!el) return false;
                    const rect = el.getBoundingClientRect();
                    const style = getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const textOf = (el) => String(el?.innerText || el?.textContent || el?.value || el?.getAttribute?.('aria-label') || '').replace(/\s+/g, ' ').trim();
                const fire = (el) => {
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                };
                const selectTargets = Array.from(document.querySelectorAll('select')).filter((sel) => {
                    if (!visible(sel)) return false;
                    const sig = String(sel.name || '') + ' ' + String(sel.id || '') + ' ' + String(sel.getAttribute('aria-label') || '') + ' ' + textOf(sel.closest('label'));
                    return /country|region|location|billing|国家|地区|區域|地域|国|地域/i.test(sig) || (sel.options && sel.options.length > 10);
                });
                for (const sel of selectTargets) {
                    const opts = Array.from(sel.options || []);
                    const hit = opts.find((opt) => {
                        const text = String(opt.text || opt.label || '').trim();
                        const value = String(opt.value || '').trim();
                        return value.toUpperCase() === 'US' || /united states|usa|u\.s\.|美国|美國|アメリカ|米国/i.test(text);
                    });
                    if (!hit) continue;
                    if (String(sel.value || '').toUpperCase() === 'US' || /united states|usa|美国|美國|アメリカ|米国/i.test(textOf(sel))) {
                        return { ok: true, mode: 'native-already', label: textOf(hit) || hit.value || 'US' };
                    }
                    const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set;
                    if (setter) setter.call(sel, hit.value);
                    else sel.value = hit.value;
                    fire(sel);
                    return { ok: true, mode: 'native-select', label: textOf(hit) || hit.value || 'US' };
                }
                return { ok: false, mode: 'not-found', label: '' };
            }"""
        )
        if isinstance(result, dict) and result.get("ok"):
            await page.wait_for_timeout(1000)
            after_native = await _probe_region()
            if after_native.get("ok"):
                log(
                    f"{prefix} offer region selected US: {result.get('mode', '')} "
                    f"{after_native.get('label', '')}"
                )
                return True
            log(
                f"{prefix} offer region native set unconfirmed: "
                f"{result.get('mode', '')} -> {after_native.get('label', '')}"
            )
    except Exception:
        pass

    if await _click_chatgpt_offer_region_option_strict(page, prefix, "us", "select"):
        return True

    async def _click_open_us_region_option(mode: str) -> bool:
        option_clicked = ""
        try:
            scoped_point = await page.evaluate(
                r"""() => {
                    const visible = (el) => {
                        if (!el) return false;
                        const rect = el.getBoundingClientRect();
                        const style = getComputedStyle(el);
                        return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                    };
                    const textOf = (el) => String(el?.innerText || el?.textContent || el?.value || el?.getAttribute?.('aria-label') || '').replace(/\s+/g, ' ').trim();
                    const usText = /united states|united states of america|usa|\bus\b|美国|美國|アメリカ|米国/i;
                    const jpText = /japan|\bjp\b|jpy|日本/i;
                    const roots = [];
                    const pushRoot = (el) => {
                        if (el && visible(el) && !roots.includes(el)) roots.push(el);
                    };
                    const trigger = document.querySelector('[data-testid="country-selector-in-pricing-modal"] [role="combobox"], [data-testid="country-selector-in-pricing-modal"] button');
                    const controlId = trigger?.getAttribute?.('aria-controls') || '';
                    pushRoot(controlId ? document.getElementById(controlId) : null);
                    const active = document.activeElement;
                    const activeDescendantId = active?.getAttribute?.('aria-activedescendant') || '';
                    pushRoot(activeDescendantId ? document.getElementById(activeDescendantId)?.closest?.('[role="listbox"], [role="menu"], [data-radix-select-content], [data-radix-popper-content-wrapper]') : null);
                    pushRoot(active?.closest?.('[role="listbox"], [role="menu"], [data-radix-select-content], [data-radix-popper-content-wrapper]'));
                    for (const node of Array.from(document.querySelectorAll('[role="listbox"], [role="menu"], [data-radix-select-content], [data-radix-popper-content-wrapper]')).filter(visible)) {
                        const rect = node.getBoundingClientRect();
                        const text = textOf(node);
                        if (rect.left < window.innerWidth * 0.45 && rect.width > window.innerWidth * 0.35) continue;
                        if (/japan|andorra|angola|country|currency|united states|cape verde|djibouti/i.test(text)) pushRoot(node);
                    }
                    const root = roots[0] || null;
                    if (!root) return { ok: false, reason: 'no-scoped-root', controlId };
                    const itemOf = (el) => el.closest?.('[role="option"], [role="menuitem"], [data-radix-select-item], [data-radix-collection-item], [data-value], [tabindex]') || el;
                    const options = Array.from(root.querySelectorAll('[role="option"], [role="menuitem"], [data-radix-select-item], [data-radix-collection-item], [data-value], [tabindex], div, span'))
                        .filter(visible)
                        .map((el) => {
                            const item = itemOf(el);
                            const rect = item.getBoundingClientRect();
                            const text = textOf(item) || textOf(el);
                            const value = String(item.getAttribute?.('data-value') || item.getAttribute?.('value') || item.getAttribute?.('aria-label') || '');
                            const sig = [text, value, item.getAttribute?.('role') || ''].join(' ');
                            if (!text || text.length > 140) return null;
                            if (rect.width <= 0 || rect.height <= 0 || rect.width > Math.max(480, window.innerWidth * 0.45) || rect.height > 96) return null;
                            if (!usText.test(sig) || jpText.test(sig)) return null;
                            const exact = /^(united states|united states of america|usa|us|美国|美國|アメリカ|米国)$/i.test(text.toLowerCase()) ? 10 : 0;
                            const roleScore = /option|menuitem/i.test(item.getAttribute?.('role') || '') ? 5 : 0;
                            return { item, label: text.slice(0, 140), score: exact + roleScore - (rect.width * rect.height / 1000000) };
                        })
                        .filter(Boolean)
                        .sort((a, b) => b.score - a.score);
                    const hit = options[0] || null;
                    const optionTexts = Array.from(root.querySelectorAll('[role="option"], [role="menuitem"], [data-radix-select-item], [data-radix-collection-item], [data-value], [tabindex]'))
                        .filter(visible)
                        .map((el) => textOf(el))
                        .filter((text) => text && text.length <= 120);
                    if (!hit) {
                        return {
                            ok: false,
                            reason: 'us-not-visible',
                            controlId,
                            optionCount: optionTexts.length,
                            firstOption: optionTexts[0] || '',
                            lastOption: optionTexts[optionTexts.length - 1] || '',
                            rootText: textOf(root).slice(0, 160)
                        };
                    }
                    hit.item.scrollIntoView({ block: 'center', inline: 'nearest' });
                    const rect = hit.item.getBoundingClientRect();
                    return {
                        ok: true,
                        controlId,
                        label: hit.label,
                        optionCount: optionTexts.length,
                        x: Math.round(rect.left + Math.max(6, Math.min(rect.width - 6, rect.width / 2))),
                        y: Math.round(rect.top + Math.max(6, Math.min(rect.height - 6, rect.height / 2)))
                    };
                }"""
            )
            if isinstance(scoped_point, dict) and scoped_point.get("ok"):
                await page.mouse.move(float(scoped_point.get("x") or 0), float(scoped_point.get("y") or 0))
                await page.mouse.down()
                await page.wait_for_timeout(70)
                await page.mouse.up()
                option_clicked = f"scoped:{scoped_point.get('label', '')}"
                await page.wait_for_timeout(1500)
                after_scoped_pick = await _probe_region()
                if after_scoped_pick.get("ok"):
                    log(
                        f"{prefix} offer region selected US by exact combobox {mode}: "
                        f"{after_scoped_pick.get('label', '')}"
                    )
                    return True
        except Exception:
            pass
        for option_selector in (
            '[role="option"]:has-text("美国")',
            '[role="menuitem"]:has-text("美国")',
            '[data-radix-collection-item]:has-text("美国")',
            'li:has-text("美国")',
            'button:has-text("美国")',
            '[role="option"]:has-text("美國")',
            '[role="menuitem"]:has-text("美國")',
            '[role="option"]:has-text("米国")',
            '[role="menuitem"]:has-text("米国")',
            '[role="option"]:has-text("United States")',
            '[role="option"]:has-text("United States of America")',
            '[role="menuitem"]:has-text("United States")',
            '[data-radix-collection-item]:has-text("United States")',
            '[cmdk-item]:has-text("United States")',
            '[data-value="US"]',
            '[data-value="us"]',
            'li:has-text("United States")',
            'button:has-text("United States")',
        ):
            try:
                option = page.locator(option_selector).last
                if await option.is_visible(timeout=450):
                    await option.scroll_into_view_if_needed(timeout=1000)
                    box = await option.bounding_box()
                    if box:
                        x_offset = float(box["width"] / 2)
                        if box["width"] > 18:
                            x_offset = min(float(box["width"] - 6), 14.0)
                        await page.mouse.move(float(box["x"] + x_offset), float(box["y"] + box["height"] / 2))
                        await page.mouse.down()
                        await page.wait_for_timeout(70)
                        await page.mouse.up()
                        option_clicked = f"{option_selector}:mouse"
                        await page.wait_for_timeout(1600)
                        after_mouse_pick = await _probe_region()
                        if not after_mouse_pick.get("ok") and not after_mouse_pick.get("label"):
                            await page.wait_for_timeout(1000)
                            after_mouse_pick = await _probe_region()
                        if after_mouse_pick.get("ok"):
                            log(
                                f"{prefix} offer region selected US by exact combobox {mode}: "
                                f"{after_mouse_pick.get('label', '')}"
                            )
                            return True
                    await option.click(timeout=2500, force=True, no_wait_after=True)
                    option_clicked = option_selector
                    await page.wait_for_timeout(1800)
                    break
            except Exception:
                continue
        if not option_clicked:
            try:
                picked = await page.evaluate(
                    r"""() => {
                        const visible = (el) => {
                            if (!el) return false;
                            const rect = el.getBoundingClientRect();
                            const style = getComputedStyle(el);
                            return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                        };
                        const textOf = (el) => String(el?.innerText || el?.textContent || el?.value || el?.getAttribute?.('aria-label') || '').replace(/\s+/g, ' ').trim();
                        const clickLikeUser = (el) => {
                            const rect = el.getBoundingClientRect();
                            const x = rect.left + Math.max(6, Math.min(rect.width - 6, rect.width / 2));
                            const y = rect.top + Math.max(6, Math.min(rect.height - 6, rect.height / 2));
                            const pointerUpInit = { bubbles: true, cancelable: true, view: window, clientX: x, clientY: y, button: 0, buttons: 0, pointerId: 1, pointerType: 'mouse', isPrimary: true };
                            const pointerDownInit = { ...pointerUpInit, buttons: 1 };
                            const mouseUpInit = { bubbles: true, cancelable: true, view: window, clientX: x, clientY: y, button: 0, buttons: 0 };
                            const mouseDownInit = { ...mouseUpInit, buttons: 1 };
                            el.focus?.();
                            for (const type of ['pointerover', 'pointermove']) {
                                try { el.dispatchEvent(new PointerEvent(type, pointerUpInit)); } catch { el.dispatchEvent(new MouseEvent(type.replace('pointer', 'mouse'), mouseUpInit)); }
                            }
                            try { el.dispatchEvent(new PointerEvent('pointerdown', pointerDownInit)); } catch { el.dispatchEvent(new MouseEvent('mousedown', mouseDownInit)); }
                            el.dispatchEvent(new MouseEvent('mousedown', mouseDownInit));
                            try { el.dispatchEvent(new PointerEvent('pointerup', pointerUpInit)); } catch { el.dispatchEvent(new MouseEvent('mouseup', mouseUpInit)); }
                            el.dispatchEvent(new MouseEvent('mouseup', mouseUpInit));
                            el.dispatchEvent(new MouseEvent('click', mouseUpInit));
                        };
                        const targetOf = (el) => (
                            el.closest?.('[role="option"], [role="menuitem"], [data-radix-collection-item], [cmdk-item], [data-value], li, button, [tabindex]') ||
                            el
                        );
                        const options = Array.from(document.querySelectorAll('[role="option"], [role="menuitem"], [data-radix-collection-item], [cmdk-item], [data-value], [tabindex], li, button, div, span'))
                            .filter(visible)
                            .map((el) => {
                                const target = targetOf(el);
                                const rect = target.getBoundingClientRect();
                                const text = textOf(target) || textOf(el);
                                const value = String(target.getAttribute?.('data-value') || target.getAttribute?.('value') || target.getAttribute?.('aria-label') || '');
                                const sig = [text, value, target.getAttribute?.('role') || '', target.getAttribute?.('data-radix-collection-item') || ''].join(' ');
                                if (!text || text.length > 140) return null;
                                if (rect.width <= 0 || rect.height <= 0 || rect.width > window.innerWidth * 0.72 || rect.height > 90) return null;
                                if (/japan|日本|jpy/i.test(sig)) return null;
                                if (!/united states|united states of america|usa|\bus\b|美国|美國|アメリカ|米国/i.test(sig)) return null;
                                const exact = /^(united states|united states of america|usa|us|美国|美國|アメリカ|米国)$/i.test(text.toLowerCase()) ? 8 : 0;
                                const roleScore = /option|menuitem/i.test(target.getAttribute?.('role') || '') ? 5 : 0;
                                return { target, label: text.slice(0, 140), score: exact + roleScore - (rect.width * rect.height / 1000000) };
                            })
                            .filter(Boolean)
                            .sort((a, b) => b.score - a.score);
                        const hit = options[0];
                        if (!hit) return { ok: false, label: '' };
                        hit.target.scrollIntoView({ block: 'center', inline: 'center' });
                        clickLikeUser(hit.target);
                        return { ok: true, label: hit.label };
                    }"""
                )
                if isinstance(picked, dict) and picked.get("ok"):
                    option_clicked = f"js:{picked.get('label', '')}"
                    await page.wait_for_timeout(1300)
            except Exception:
                pass
        if not option_clicked:
            return False
        after_pick = await _probe_region()
        if not after_pick.get("ok") and not after_pick.get("label"):
            await page.wait_for_timeout(1000)
            after_pick = await _probe_region()
        if after_pick.get("ok"):
            log(f"{prefix} offer region selected US by exact combobox {mode}: {after_pick.get('label', '')}")
            return True
        log(
            f"{prefix} offer region exact combobox pick unconfirmed: "
            f"mode={mode} option={option_clicked} -> {after_pick.get('label', '')}"
        )
        return False

    async def _typeahead_us_from_open_region_menu(mode: str) -> bool:
        for query in ("美国", "美國", "米国", "United States", "United States of America", "US"):
            safe_state = await page.evaluate(
                r"""() => {
                    const visible = (el) => {
                        if (!el) return false;
                        const rect = el.getBoundingClientRect();
                        const style = getComputedStyle(el);
                        return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                    };
                    const textOf = (el) => String(el?.innerText || el?.textContent || '').replace(/\s+/g, ' ').trim();
                    const active = document.activeElement;
                    const activeRole = active?.getAttribute?.('role') || '';
                    const activeTag = String(active?.tagName || '').toLowerCase();
                    const activeEditable = !!(active && (active.isContentEditable || /^(input|textarea)$/.test(activeTag) || activeRole === 'textbox'));
                    const activeCombo = !!(active && activeRole === 'combobox' && active.closest?.('[data-testid="country-selector-in-pricing-modal"]'));
                    const roots = [];
                    const pushRoot = (el) => {
                        if (el && visible(el) && !roots.includes(el)) roots.push(el);
                    };
                    const trigger = document.querySelector('[data-testid="country-selector-in-pricing-modal"] [role="combobox"], [data-testid="country-selector-in-pricing-modal"] button');
                    const controlId = trigger?.getAttribute?.('aria-controls') || '';
                    pushRoot(controlId ? document.getElementById(controlId) : null);
                    const activeDescendantId = active?.getAttribute?.('aria-activedescendant') || '';
                    pushRoot(activeDescendantId ? document.getElementById(activeDescendantId)?.closest?.('[role="listbox"], [role="menu"], [data-radix-select-content], [data-radix-popper-content-wrapper]') : null);
                    pushRoot(active?.closest?.('[role="listbox"], [role="menu"], [data-radix-select-content], [data-radix-popper-content-wrapper]'));
                    for (const node of Array.from(document.querySelectorAll('[role="listbox"], [role="menu"], [data-radix-select-content], [data-radix-popper-content-wrapper]')).filter(visible)) {
                        const rect = node.getBoundingClientRect();
                        const text = textOf(node);
                        if (rect.left < window.innerWidth * 0.45 && rect.width > window.innerWidth * 0.35) continue;
                        if (/japan|andorra|angola|country|currency|united states|cape verde|djibouti/i.test(text)) pushRoot(node);
                    }
                    const root = roots[0] || null;
                    const activeOption = !!(active && root && /option|menuitem/i.test(activeRole) && root.contains(active));
                    const activeInScopedMenu = !!(active && root && root.contains(active));
                    return {
                        ok: !activeEditable && !!root && (activeCombo || activeOption || activeInScopedMenu),
                        controlId,
                        activeRole,
                        activeTag,
                        activeEditable,
                        activeText: textOf(active).slice(0, 100),
                        openNodes: roots.length
                    };
                }"""
            )
            if not isinstance(safe_state, dict) or not safe_state.get("ok"):
                log(f"{prefix} offer region typeahead skipped unsafe focus: mode={mode} state={safe_state}")
                return False
            await page.keyboard.type(query, delay=25)
            await page.wait_for_timeout(550)
            selected_state = await page.evaluate(
                r"""() => {
                    const visible = (el) => {
                        if (!el) return false;
                        const rect = el.getBoundingClientRect();
                        const style = getComputedStyle(el);
                        return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                    };
                    const textOf = (el) => String(el?.innerText || el?.textContent || el?.value || el?.getAttribute?.('aria-label') || '').replace(/\s+/g, ' ').trim();
                    const usText = /united states|united states of america|usa|\bus\b|美国|美國|アメリカ|米国/i;
                    const jpText = /japan|\bjp\b|jpy|日本/i;
                    const active = document.activeElement;
                    const activeRole = active?.getAttribute?.('role') || '';
                    const activeDescendantId = active?.getAttribute?.('aria-activedescendant') || '';
                    const activeDescendant = activeDescendantId ? document.getElementById(activeDescendantId) : null;
                    const roots = [];
                    const pushRoot = (el) => {
                        if (el && visible(el) && !roots.includes(el)) roots.push(el);
                    };
                    const trigger = document.querySelector('[data-testid="country-selector-in-pricing-modal"] [role="combobox"], [data-testid="country-selector-in-pricing-modal"] button');
                    const controlId = trigger?.getAttribute?.('aria-controls') || '';
                    pushRoot(controlId ? document.getElementById(controlId) : null);
                    pushRoot(activeDescendant?.closest?.('[role="listbox"], [role="menu"], [data-radix-select-content], [data-radix-popper-content-wrapper]'));
                    pushRoot(active?.closest?.('[role="listbox"], [role="menu"], [data-radix-select-content], [data-radix-popper-content-wrapper]'));
                    for (const node of Array.from(document.querySelectorAll('[role="listbox"], [role="menu"], [data-radix-select-content], [data-radix-popper-content-wrapper]')).filter(visible)) {
                        const rect = node.getBoundingClientRect();
                        const text = textOf(node);
                        if (rect.left < window.innerWidth * 0.45 && rect.width > window.innerWidth * 0.35) continue;
                        if (/japan|andorra|angola|country|currency|united states|cape verde|djibouti/i.test(text)) pushRoot(node);
                    }
                    const root = roots[0] || null;
                    const highlighted = root ? Array.from(root.querySelectorAll('[data-highlighted], [data-state="checked"], [aria-current="true"]')).filter(visible)[0] || null : null;
                    const activeText = textOf(activeDescendant) || textOf(highlighted) || textOf(active);
                    const highlightedText = textOf(highlighted);
                    return {
                        ok: !!root,
                        controlId,
                        activeRole,
                        activeText: activeText.slice(0, 120),
                        highlightedText: highlightedText.slice(0, 120),
                        usActive: usText.test(activeText) && !jpText.test(activeText),
                        usHighlighted: usText.test(highlightedText) && !jpText.test(highlightedText),
                        openNodes: roots.length
                    };
                }"""
            )
            if isinstance(selected_state, dict) and (selected_state.get("usActive") or selected_state.get("usHighlighted")):
                await page.keyboard.press("Enter")
                await page.wait_for_timeout(1300)
            else:
                log(
                    f"{prefix} offer region typeahead no US highlight: "
                    f"mode={mode} query={query} state={selected_state}"
                )
                await page.wait_for_timeout(1100)
                continue
            after_typeahead = await _probe_region()
            if after_typeahead.get("ok"):
                log(f"{prefix} offer region selected US by typeahead {mode}: {after_typeahead.get('label', '')}")
                return True
            log(
                f"{prefix} offer region typeahead unconfirmed: "
                f"mode={mode} query={query} state={safe_state} -> {after_typeahead.get('label', '')}"
            )
            await page.wait_for_timeout(1100)
        return False

    async def _keyboard_select_open_us_region_option(mode: str) -> bool:
        """只用地区弹层内键盘导航选美国，避免把 United States 输入聊天框。"""
        last_state: dict[str, Any] | None = None
        last_marker = ""
        stalled_steps = 0
        for step in range(0, 180):
            state = await page.evaluate(
                r"""() => {
                    const visible = (el) => {
                        if (!el) return false;
                        const rect = el.getBoundingClientRect();
                        const style = getComputedStyle(el);
                        return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                    };
                    const textOf = (el) => String(el?.innerText || el?.textContent || el?.value || el?.getAttribute?.('aria-label') || '').replace(/\s+/g, ' ').trim();
                    const usText = /united states|united states of america|usa|\bus\b|美国|美國|アメリカ|米国/i;
                    const jpText = /japan|\bjp\b|jpy|日本/i;
                    const active = document.activeElement;
                    const activeRole = active?.getAttribute?.('role') || '';
                    const activeTag = String(active?.tagName || '').toLowerCase();
                    const activeEditable = !!(active && (active.isContentEditable || /^(input|textarea)$/.test(activeTag) || activeRole === 'textbox'));
                    const activeCombo = !!(active && activeRole === 'combobox' && active.closest?.('[data-testid="country-selector-in-pricing-modal"]'));
                    const roots = [];
                    const pushRoot = (el) => {
                        if (el && visible(el) && !roots.includes(el)) roots.push(el);
                    };
                    const trigger = document.querySelector('[data-testid="country-selector-in-pricing-modal"] [role="combobox"], [data-testid="country-selector-in-pricing-modal"] button');
                    const controlId = trigger?.getAttribute?.('aria-controls') || '';
                    pushRoot(controlId ? document.getElementById(controlId) : null);
                    const activeDescendantId = active?.getAttribute?.('aria-activedescendant') || '';
                    const activeDescendant = activeDescendantId ? document.getElementById(activeDescendantId) : null;
                    pushRoot(activeDescendant?.closest?.('[role="listbox"], [role="menu"], [data-radix-select-content], [data-radix-popper-content-wrapper]'));
                    pushRoot(active?.closest?.('[role="listbox"], [role="menu"], [data-radix-select-content], [data-radix-popper-content-wrapper]'));
                    for (const node of Array.from(document.querySelectorAll('[role="listbox"], [role="menu"], [data-radix-select-content], [data-radix-popper-content-wrapper]')).filter(visible)) {
                        const rect = node.getBoundingClientRect();
                        const text = textOf(node);
                        if (rect.left < window.innerWidth * 0.45 && rect.width > window.innerWidth * 0.35) continue;
                        if (/japan|andorra|angola|country|currency|united states|cape verde|djibouti/i.test(text)) pushRoot(node);
                    }
                    const root = roots[0] || null;
                    const activeOption = !!(active && root && /option|menuitem/i.test(activeRole) && root.contains(active));
                    const activeInScopedMenu = !!(active && root && root.contains(active));
                    const highlighted = root ? Array.from(root.querySelectorAll('[data-highlighted], [data-state="checked"], [aria-current="true"]')).filter(visible)[0] || null : null;
                    const optionNodes = root ? Array.from(root.querySelectorAll('[role="option"], [role="menuitem"], [data-radix-select-item], [data-radix-collection-item], [data-value], [tabindex]'))
                        .filter(visible)
                        .filter((el) => {
                            const text = textOf(el);
                            const rect = el.getBoundingClientRect();
                            if (!text || text.length > 150) return false;
                            if (rect.width > Math.max(480, window.innerWidth * 0.45) || rect.height > 120) return false;
                            return true;
                        }) : [];
                    const activeText = textOf(activeDescendant) || textOf(highlighted) || textOf(active);
                    const usNode = optionNodes.find((el) => {
                        const sig = [textOf(el), el.getAttribute?.('data-value') || '', el.getAttribute?.('value') || '', el.getAttribute?.('aria-label') || ''].join(' ');
                        return usText.test(sig) && !jpText.test(sig);
                    });
                    const highlightedText = textOf(highlighted);
                    const safeFocus = !activeEditable && !!root && (activeCombo || activeOption || activeInScopedMenu);
                    return {
                        ok: safeFocus,
                        controlId,
                        activeRole,
                        activeTag,
                        activeEditable,
                        activeText: activeText.slice(0, 120),
                        highlightedText: highlightedText.slice(0, 120),
                        openNodes: roots.length,
                        optionCount: optionNodes.length,
                        usVisible: !!usNode,
                        usText: usNode ? textOf(usNode).slice(0, 120) : '',
                        usActive: usText.test(activeText) && !jpText.test(activeText),
                        usHighlighted: usText.test(highlightedText) && !jpText.test(highlightedText)
                    };
                }"""
            )
            if not isinstance(state, dict) or not state.get("ok"):
                log(f"{prefix} offer region keyboard skipped unsafe focus: mode={mode} step={step} state={state}")
                return False
            last_state = state
            marker = f"{state.get('activeText', '')}|{state.get('highlightedText', '')}"
            if marker == last_marker and step > 0:
                stalled_steps += 1
            else:
                stalled_steps = 0
                last_marker = marker
            if stalled_steps >= 18 and not state.get("usActive") and not state.get("usHighlighted"):
                log(f"{prefix} offer region keyboard stalled: mode={mode} step={step} state={state}")
                return False
            if state.get("usActive") or state.get("usHighlighted"):
                await page.keyboard.press("Enter")
                await page.wait_for_timeout(1400)
                after_enter = await _probe_region()
                if after_enter.get("ok"):
                    log(
                        f"{prefix} offer region selected US by keyboard {mode}: "
                        f"{after_enter.get('label', '')}"
                    )
                    return True
                log(
                    f"{prefix} offer region keyboard enter unconfirmed: "
                    f"mode={mode} step={step} state={state} -> {after_enter.get('label', '')}"
                )
                return False
            await page.keyboard.press("ArrowDown")
            await page.wait_for_timeout(85 if step < 80 else 55)
        log(f"{prefix} offer region keyboard exhausted: mode={mode} state={last_state}")
        return False

    async def _wheel_open_region_menu_for_us(mode: str) -> bool:
        """Radix 国家列表常用虚拟滚动；用鼠标滚轮逐屏找 United States。"""
        last_state: dict[str, Any] | None = None
        for step in range(1, 140):
            if await _click_open_us_region_option(f"{mode}:wheel-{step}"):
                return True
            menu_state = await page.evaluate(
                r"""() => {
                    const visible = (el) => {
                        if (!el) return false;
                        const rect = el.getBoundingClientRect();
                        const style = getComputedStyle(el);
                        return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                    };
                    const textOf = (el) => String(el?.innerText || el?.textContent || el?.value || el?.getAttribute?.('aria-label') || '').replace(/\s+/g, ' ').trim();
                    const active = document.activeElement;
                    const activeRole = active?.getAttribute?.('role') || '';
                    const activeTag = String(active?.tagName || '').toLowerCase();
                    const activeEditable = !!(active && (active.isContentEditable || /^(input|textarea)$/.test(activeTag) || activeRole === 'textbox'));
                    const roots = [];
                    const pushRoot = (el) => {
                        if (el && visible(el) && !roots.includes(el)) roots.push(el);
                    };
                    const trigger = document.querySelector('[data-testid="country-selector-in-pricing-modal"] [role="combobox"], [data-testid="country-selector-in-pricing-modal"] button');
                    const controlId = trigger?.getAttribute?.('aria-controls') || '';
                    pushRoot(controlId ? document.getElementById(controlId) : null);
                    const activeDescendantId = active?.getAttribute?.('aria-activedescendant') || '';
                    pushRoot(activeDescendantId ? document.getElementById(activeDescendantId)?.closest?.('[role="listbox"], [role="menu"], [data-radix-select-content], [data-radix-popper-content-wrapper]') : null);
                    pushRoot(active?.closest?.('[role="listbox"], [role="menu"], [data-radix-select-content], [data-radix-popper-content-wrapper]'));
                    for (const node of Array.from(document.querySelectorAll('[role="listbox"], [role="menu"], [data-radix-select-content], [data-radix-popper-content-wrapper]')).filter(visible)) {
                        const rect = node.getBoundingClientRect();
                        const text = textOf(node);
                        if (rect.left < window.innerWidth * 0.45 && rect.width > window.innerWidth * 0.35) continue;
                        if (/japan|andorra|angola|country|currency|united states|cape verde|djibouti/i.test(text)) pushRoot(node);
                    }
                    const root = roots[0] || null;
                    const optionNodes = root ? Array.from(root.querySelectorAll('[role="option"], [role="menuitem"], [data-radix-select-item], [data-radix-collection-item], [data-value], [tabindex]'))
                        .filter(visible)
                        .filter((el) => {
                            const text = textOf(el);
                            const rect = el.getBoundingClientRect();
                            if (!text || text.length > 160) return false;
                            if (rect.width > Math.max(520, window.innerWidth * 0.45) || rect.height > 140) return false;
                            return true;
                        }) : [];
                    const scrollTargets = root ? Array.from(root.querySelectorAll('[data-radix-select-viewport], [data-radix-scroll-area-viewport], [style*="overflow"], div'))
                        .filter(visible)
                        .filter((el) => el.scrollHeight > el.clientHeight + 8) : [];
                    if (root && root.scrollHeight > root.clientHeight + 8) scrollTargets.push(root);
                    const scroller = scrollTargets.sort((a, b) => (b.scrollHeight - b.clientHeight) - (a.scrollHeight - a.clientHeight))[0] || root;
                    const activeOptionForPoint = active && root && root.contains(active) && /option|menuitem/i.test(activeRole) && visible(active) ? active : null;
                    const optionForPoint = activeOptionForPoint || optionNodes[0] || null;
                    const pointRect = optionForPoint?.getBoundingClientRect?.() || root?.getBoundingClientRect?.() || null;
                    if (!root || !pointRect || activeEditable) {
                        return {
                            ok: false,
                            reason: activeEditable ? 'unsafe-focus' : 'no-menu-point',
                            controlId,
                            activeRole,
                            activeTag,
                            activeText: textOf(active).slice(0, 100),
                            optionCount: optionNodes.length,
                            openCount: roots.length
                        };
                    }
                    const x = Math.max(40, Math.min(window.innerWidth - 40, pointRect.left + pointRect.width / 2));
                    const y = Math.max(60, Math.min(window.innerHeight - 60, pointRect.top + pointRect.height / 2));
                    let before = 0;
                    let after = 0;
                    let scrollHeight = 0;
                    if (scroller && scroller.scrollHeight > scroller.clientHeight + 8) {
                        before = scroller.scrollTop;
                        scroller.scrollTop = Math.min(scroller.scrollHeight, scroller.scrollTop + Math.max(220, Math.floor(scroller.clientHeight * 0.9)));
                        scroller.dispatchEvent(new Event('scroll', { bubbles: true }));
                        after = scroller.scrollTop;
                        scrollHeight = scroller.scrollHeight;
                    }
                    return {
                        ok: true,
                        controlId,
                        x: Math.round(x),
                        y: Math.round(y),
                        activeRole,
                        activeTag,
                        activeText: textOf(active).slice(0, 100),
                        firstOption: optionNodes.length ? textOf(optionNodes[0]).slice(0, 80) : '',
                        lastOption: optionNodes.length ? textOf(optionNodes[optionNodes.length - 1]).slice(0, 80) : '',
                        optionCount: optionNodes.length,
                        openCount: roots.length,
                        before,
                        after,
                        scrollHeight
                    };
                }"""
            )
            if not isinstance(menu_state, dict) or not menu_state.get("ok"):
                log(f"{prefix} offer region wheel skipped: mode={mode} step={step} state={menu_state}")
                return False
            last_state = menu_state
            await page.mouse.move(float(menu_state.get("x") or 0), float(menu_state.get("y") or 0))
            await page.mouse.wheel(0, 520)
            await page.wait_for_timeout(140)
        log(f"{prefix} offer region wheel exhausted: mode={mode} state={last_state}")
        return False

    async def _scroll_open_region_menu_for_us(mode: str) -> bool:
        for step in range(1, 32):
            if await _click_open_us_region_option(f"{mode}:scroll-{step}"):
                return True
            scrolled = await page.evaluate(
                r"""() => {
                    const visible = (el) => {
                        if (!el) return false;
                        const rect = el.getBoundingClientRect();
                        const style = getComputedStyle(el);
                        return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                    };
                    const textOf = (el) => String(el?.innerText || el?.textContent || '').replace(/\s+/g, ' ').trim();
                    const roots = [];
                    const pushRoot = (el) => {
                        if (el && visible(el) && !roots.includes(el)) roots.push(el);
                    };
                    const active = document.activeElement;
                    const trigger = document.querySelector('[data-testid="country-selector-in-pricing-modal"] [role="combobox"], [data-testid="country-selector-in-pricing-modal"] button');
                    const controlId = trigger?.getAttribute?.('aria-controls') || '';
                    pushRoot(controlId ? document.getElementById(controlId) : null);
                    const activeDescendantId = active?.getAttribute?.('aria-activedescendant') || '';
                    pushRoot(activeDescendantId ? document.getElementById(activeDescendantId)?.closest?.('[role="listbox"], [role="menu"], [data-radix-select-content], [data-radix-popper-content-wrapper]') : null);
                    pushRoot(active?.closest?.('[role="listbox"], [role="menu"], [data-radix-select-content], [data-radix-popper-content-wrapper]'));
                    for (const node of Array.from(document.querySelectorAll('[role="listbox"], [role="menu"], [data-radix-select-content], [data-radix-popper-content-wrapper]')).filter(visible)) {
                        const rect = node.getBoundingClientRect();
                        const text = textOf(node);
                        if (rect.left < window.innerWidth * 0.45 && rect.width > window.innerWidth * 0.35) continue;
                        if (/japan|andorra|angola|country|currency|united states|cape verde|djibouti/i.test(text)) pushRoot(node);
                    }
                    const root = roots[0] || null;
                    if (!root) return { ok: false, reason: 'no-scoped-root', controlId };
                    const containers = Array.from(root.querySelectorAll('[data-radix-select-viewport], [data-radix-scroll-area-viewport], [style*="overflow"], div'))
                        .filter(visible)
                        .filter((el) => el.scrollHeight > el.clientHeight + 8)
                        .map((el) => {
                            const rect = el.getBoundingClientRect();
                            const text = textOf(el);
                            return { el, text, area: rect.width * rect.height, top: el.scrollTop, clientHeight: el.clientHeight, scrollHeight: el.scrollHeight };
                        })
                        .filter((item) => item.area > 80 && item.area < window.innerWidth * window.innerHeight * 0.35)
                        .sort((a, b) => (b.scrollHeight - b.clientHeight) - (a.scrollHeight - a.clientHeight));
                    if (root.scrollHeight > root.clientHeight + 8) {
                        const rect = root.getBoundingClientRect();
                        containers.push({ el: root, text: textOf(root), area: rect.width * rect.height, top: root.scrollTop, clientHeight: root.clientHeight, scrollHeight: root.scrollHeight });
                    }
                    const hit = containers[0];
                    if (!hit) return { ok: false, reason: 'no-scroll-container', controlId, rootText: textOf(root).slice(0, 120) };
                    const before = hit.el.scrollTop;
                    hit.el.scrollTop = Math.min(hit.el.scrollHeight, hit.el.scrollTop + Math.max(220, Math.floor(hit.el.clientHeight * 0.9)));
                    hit.el.dispatchEvent(new Event('scroll', { bubbles: true }));
                    const options = Array.from(root.querySelectorAll('[role="option"], [role="menuitem"], [data-radix-select-item], [data-radix-collection-item], [data-value], [tabindex]'))
                        .filter(visible)
                        .map((el) => textOf(el))
                        .filter((text) => text && text.length <= 120);
                    return {
                        ok: hit.el.scrollTop !== before,
                        reason: hit.el.scrollTop === before ? 'end' : 'scrolled',
                        controlId,
                        before,
                        after: hit.el.scrollTop,
                        scrollHeight: hit.el.scrollHeight,
                        text: hit.text.slice(0, 120),
                        firstOption: options[0] || '',
                        lastOption: options[options.length - 1] || ''
                    };
                }"""
            )
            if not isinstance(scrolled, dict) or not scrolled.get("ok"):
                if step == 1:
                    log(f"{prefix} offer region menu scroll skipped: {scrolled}")
                break
            await page.wait_for_timeout(300)
        return False

    try:
        opened_selector = ""
        opened_modes: list[str] = []
        for selector in (
            '[data-testid="country-selector-in-pricing-modal"] button[role="combobox"]',
            '[data-testid="country-selector-in-pricing-modal"] [role="combobox"]',
            'button[role="combobox"][aria-labelledby*="_r_"]:has-text("Japan")',
        ):
            try:
                region_button = page.locator(selector).first
                if not await region_button.is_visible(timeout=700) or not await region_button.is_enabled(timeout=700):
                    continue
                opened_selector = selector
                await region_button.scroll_into_view_if_needed(timeout=1000)
                box = await region_button.bounding_box()
                if box:
                    click_points = (
                        ("mouse-right", float(box["x"] + max(6, box["width"] - 12)), float(box["y"] + box["height"] / 2)),
                        ("mouse-center", float(box["x"] + box["width"] / 2), float(box["y"] + box["height"] / 2)),
                    )
                    for mode, x, y in click_points:
                        await page.mouse.move(x, y)
                        await page.mouse.down()
                        await page.wait_for_timeout(60)
                        await page.mouse.up()
                        opened_modes.append(mode)
                        await page.wait_for_timeout(650)
                        if await _click_open_us_region_option(mode):
                            return True
                        if await _typeahead_us_from_open_region_menu(mode):
                            return True
                        if await _wheel_open_region_menu_for_us(mode):
                            return True
                        if await _scroll_open_region_menu_for_us(mode):
                            return True
                        if await _keyboard_select_open_us_region_option(mode):
                            return True
                try:
                    await region_button.click(timeout=2500, force=True, no_wait_after=True)
                    opened_modes.append("locator-click")
                    await page.wait_for_timeout(700)
                    if await _click_open_us_region_option("locator-click"):
                        return True
                    if await _typeahead_us_from_open_region_menu("locator-click"):
                        return True
                    if await _wheel_open_region_menu_for_us("locator-click"):
                        return True
                    if await _scroll_open_region_menu_for_us("locator-click"):
                        return True
                    if await _keyboard_select_open_us_region_option("locator-click"):
                        return True
                except Exception:
                    pass

                # Radix Select 有时只响应键盘开关；仅当焦点确认为地区 combobox 时才发键，避免误输入聊天框。
                await region_button.focus(timeout=1000)
                active_safe = await region_button.evaluate(
                    r"""(el) => {
                        const active = document.activeElement;
                        return active === el && active.getAttribute('role') === 'combobox' && !!active.closest('[data-testid="country-selector-in-pricing-modal"]');
                    }"""
                )
                if active_safe:
                    for key in ("Enter", "Space", "ArrowDown", "Alt+ArrowDown"):
                        await page.keyboard.press(key)
                        opened_modes.append(f"key:{key}")
                        await page.wait_for_timeout(750)
                        if await _click_open_us_region_option(f"key:{key}"):
                            return True
                        if await _typeahead_us_from_open_region_menu(f"key:{key}"):
                            return True
                        if await _wheel_open_region_menu_for_us(f"key:{key}"):
                            return True
                        if await _scroll_open_region_menu_for_us(f"key:{key}"):
                            return True
                        if await _keyboard_select_open_us_region_option("focused-combobox"):
                            return True
                if await _click_open_us_region_option("open-menu"):
                    return True
                if await _typeahead_us_from_open_region_menu("open-menu"):
                    return True
                if await _wheel_open_region_menu_for_us("open-menu"):
                    return True
                if await _keyboard_select_open_us_region_option("open-menu"):
                    return True
                if await _scroll_open_region_menu_for_us("open-menu"):
                    return True
                after_exact = await _probe_region()
                menu_state = await page.evaluate(
                    r"""(sel) => {
                        const btn = document.querySelector(sel);
                        const visible = (el) => {
                            if (!el) return false;
                            const rect = el.getBoundingClientRect();
                            const style = getComputedStyle(el);
                            return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                        };
                        const openNodes = Array.from(document.querySelectorAll('[role="listbox"], [role="menu"], [data-radix-popper-content-wrapper], [data-state="open"]')).filter(visible).length;
                        return {
                            ariaExpanded: btn?.getAttribute?.('aria-expanded') || '',
                            dataState: btn?.getAttribute?.('data-state') || '',
                            openNodes,
                            activeRole: document.activeElement?.getAttribute?.('role') || '',
                            activeText: String(document.activeElement?.innerText || document.activeElement?.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 80)
                        };
                    }""",
                    selector,
                )
                log(
                    f"{prefix} offer region exact combobox not confirmed: "
                    f"opened={opened_selector} modes={opened_modes} state={menu_state} -> {after_exact.get('label', '')}"
                )
                break
            except Exception:
                continue
    except Exception as exc:
        log(f"{prefix} offer region exact combobox path skipped: {exc}")

    try:
        # ChatGPT 套餐页国家控件是自绘弹层，DOM click 常只点到 span；需向上找真正可交互父级。
        menu_point = await page.evaluate(
            r"""() => {
                const visible = (el) => {
                    if (!el) return false;
                    const rect = el.getBoundingClientRect();
                    const style = getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const textOf = (el) => String(el?.innerText || el?.textContent || el?.value || el?.getAttribute?.('aria-label') || '').replace(/\s+/g, ' ').trim();
                const interactiveOf = (el) => {
                    const wanted = /country|currency|region|japan|\bjp\b|jpy|国家|地区|地域|国|日本/i;
                    let best = null;
                    let bestScore = -9999;
                    let node = el;
                    for (let depth = 0; node && node.nodeType === 1 && depth < 9; depth++, node = node.parentElement) {
                        if (!visible(node)) continue;
                        const rect = node.getBoundingClientRect();
                        if (rect.width > window.innerWidth * 0.82 || rect.height > window.innerHeight * 0.45) continue;
                        const tag = String(node.tagName || '').toLowerCase();
                        const role = String(node.getAttribute?.('role') || '').toLowerCase();
                        const aria = String(node.getAttribute?.('aria-label') || '');
                        const testid = String(node.getAttribute?.('data-testid') || '');
                        const cls = String(node.className || '');
                        const style = getComputedStyle(node);
                        const text = textOf(node) || textOf(el);
                        let score = 0 - depth;
                        if (/^(button|select)$/.test(tag)) score += 12;
                        if (/button|combobox|menuitem|option/.test(role)) score += 10;
                        if (node.hasAttribute?.('aria-haspopup')) score += 9;
                        if (node.tabIndex >= 0) score += 6;
                        if (typeof node.onclick === 'function') score += 5;
                        if (style.cursor === 'pointer') score += 5;
                        if (/cursor-pointer|select|dropdown|menu|popover|country|currency/i.test(cls + ' ' + testid + ' ' + aria)) score += 4;
                        if (/country|currency|国家|地区|地域|国/i.test(text + ' ' + aria + ' ' + testid)) score += 4;
                        if (wanted.test(text + ' ' + aria + ' ' + testid)) score += 2;
                        if (score > bestScore) {
                            best = node;
                            bestScore = score;
                        }
                    }
                    return best || el;
                };
                const nodes = Array.from(document.querySelectorAll('button, [role="button"], [role="combobox"], [aria-haspopup], [data-testid], [tabindex], div, span'))
                    .filter(visible)
                    .filter((el) => {
                        const rect = el.getBoundingClientRect();
                        if (rect.top < window.innerHeight * 0.35 || rect.left < window.innerWidth * 0.35) return false;
                        if (rect.width > window.innerWidth * 0.65 || rect.height > window.innerHeight * 0.35) return false;
                        const text = textOf(el);
                        if (!text || text.length > 180) return false;
                        if (/open image|chatgpt said|you said|chatgpt can make mistakes|ask anything/i.test(text)) return false;
                        return /country|currency|region|japan|\bjp\b|jpy|国家|地区|地域|国|日本/i.test(text);
                    })
                    .map((el) => {
                        const target = interactiveOf(el);
                        const rect = target.getBoundingClientRect();
                        const text = textOf(target) || textOf(el);
                        const targetSig = [
                            target.tagName || '',
                            target.getAttribute?.('role') || '',
                            target.getAttribute?.('aria-label') || '',
                            target.getAttribute?.('data-testid') || '',
                            String(target.className || '').slice(0, 80)
                        ].join(' ');
                        const specific = /country|currency|国家|地区|地域|国/i.test(text) ? 2 : 0;
                        const jp = /japan|\bjp\b|jpy|日本/i.test(text) ? 1 : 0;
                        const interactive = /button|combobox|menuitem|option|cursor-pointer|select|dropdown|menu|popover/i.test(targetSig) || target.tabIndex >= 0 || target.hasAttribute?.('aria-haspopup') ? 3 : 0;
                        const score = specific + jp + (rect.top / Math.max(1, window.innerHeight)) + (rect.left / Math.max(1, window.innerWidth));
                        return {
                            ok: true,
                            label: text.slice(0, 180),
                            tag: target.tagName || '',
                            role: target.getAttribute?.('role') || '',
                            aria: target.getAttribute?.('aria-label') || '',
                            testid: target.getAttribute?.('data-testid') || '',
                            x: Math.round(rect.left + Math.max(6, Math.min(rect.width - 6, rect.width / 2))),
                            y: Math.round(rect.top + Math.max(6, Math.min(rect.height - 6, rect.height / 2))),
                            score: score + interactive
                        };
                    })
                    .sort((a, b) => b.score - a.score);
                return nodes[0] || { ok: false, label: '' };
            }"""
        )
        if isinstance(menu_point, dict) and menu_point.get("ok"):
            await page.mouse.move(float(menu_point.get("x") or 0), float(menu_point.get("y") or 0))
            await page.mouse.down()
            await page.wait_for_timeout(80)
            await page.mouse.up()
            await page.wait_for_timeout(1100)
            option_point = await page.evaluate(
                r"""() => {
                    const visible = (el) => {
                        if (!el) return false;
                        const rect = el.getBoundingClientRect();
                        const style = getComputedStyle(el);
                        return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                    };
                    const textOf = (el) => String(el?.innerText || el?.textContent || el?.value || el?.getAttribute?.('aria-label') || '').replace(/\s+/g, ' ').trim();
                    const norm = (s) => String(s || '').replace(/\s+/g, ' ').trim().toLowerCase();
                    const interactiveOf = (el) => (
                        el.closest?.('[role="option"], [role="menuitem"], button, [data-radix-collection-item], [cmdk-item], [data-value], [tabindex]') ||
                        el
                    );
                    const options = Array.from(document.querySelectorAll('[role="option"], [role="menuitem"], [data-radix-collection-item], [cmdk-item], [data-value], [tabindex], li, button, div, span'))
                        .filter(visible)
                        .filter((el) => {
                            const rect = el.getBoundingClientRect();
                            const text = textOf(el);
                            const value = String(el.getAttribute?.('data-value') || el.getAttribute?.('value') || el.getAttribute?.('aria-label') || '');
                            if (!text || text.length > 140) return false;
                            if (rect.width > window.innerWidth * 0.75 || rect.height > window.innerHeight * 0.35) return false;
                            if (/chatgpt said|you said|ask anything|new chat|search chats/i.test(text)) return false;
                            if (/japan|日本|jpy/i.test(text + ' ' + value)) return false;
                            return /united states|united states of america|usa|\bus\b|美国|美國|アメリカ|米国/i.test(text + ' ' + value);
                        })
                        .map((el) => {
                            const target = interactiveOf(el);
                            const rect = target.getBoundingClientRect();
                            const text = textOf(target) || textOf(el);
                            const exact = /^(united states|united states of america|usa|us|美国|美國|アメリカ|米国)$/i.test(norm(text)) ? 2 : 0;
                            const small = Math.max(1, rect.width * rect.height);
                            return {
                                ok: true,
                                label: text.slice(0, 140),
                                x: Math.round(rect.left + Math.max(6, Math.min(rect.width - 6, rect.width / 2))),
                                y: Math.round(rect.top + Math.max(6, Math.min(rect.height - 6, rect.height / 2))),
                                score: exact - (small / 1000000)
                            };
                        })
                        .sort((a, b) => b.score - a.score);
                    return options[0] || { ok: false, label: '' };
                }"""
            )
            if isinstance(option_point, dict) and option_point.get("ok"):
                await page.mouse.click(float(option_point.get("x") or 0), float(option_point.get("y") or 0))
                await page.wait_for_timeout(1400)
                after_mouse = await _probe_region()
                if after_mouse.get("ok"):
                    log(
                        f"{prefix} offer region selected US by mouse: "
                        f"{after_mouse.get('label', '')}"
                    )
                    return True
                log(
                    f"{prefix} offer region mouse pick unconfirmed: "
                    f"{option_point.get('label', '')} -> {after_mouse.get('label', '')}"
                )
            else:
                log(
                    f"{prefix} offer region mouse menu opened but US option not found: "
                    f"{menu_point.get('label', '')}"
                )
    except Exception as exc:
        log(f"{prefix} offer region mouse path skipped: {exc}")

    try:
        clicked = await page.evaluate(
            r"""() => {
                const visible = (el) => {
                    if (!el) return false;
                    const rect = el.getBoundingClientRect();
                    const style = getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const textOf = (el) => String(el?.innerText || el?.textContent || el?.getAttribute?.('aria-label') || '').replace(/\s+/g, ' ').trim();
                const interactiveOf = (el) => {
                    let best = el;
                    let bestScore = -9999;
                    let node = el;
                    for (let depth = 0; node && node.nodeType === 1 && depth < 9; depth++, node = node.parentElement) {
                        if (!visible(node)) continue;
                        const rect = node.getBoundingClientRect();
                        if (rect.width > window.innerWidth * 0.82 || rect.height > window.innerHeight * 0.45) continue;
                        const tag = String(node.tagName || '').toLowerCase();
                        const role = String(node.getAttribute?.('role') || '').toLowerCase();
                        const sig = [
                            node.getAttribute?.('aria-label') || '',
                            node.getAttribute?.('data-testid') || '',
                            String(node.className || ''),
                            textOf(node)
                        ].join(' ');
                        const style = getComputedStyle(node);
                        let score = 0 - depth;
                        if (/^(button|select)$/.test(tag)) score += 12;
                        if (/button|combobox|menuitem|option/.test(role)) score += 10;
                        if (node.hasAttribute?.('aria-haspopup')) score += 9;
                        if (node.tabIndex >= 0) score += 6;
                        if (typeof node.onclick === 'function') score += 5;
                        if (style.cursor === 'pointer') score += 5;
                        if (/cursor-pointer|select|dropdown|menu|popover|country|currency/i.test(sig)) score += 4;
                        if (/country|currency|region|japan|\bjp\b|日本|united states|\bus\b|usa|美国|美國|アメリカ|米国/i.test(sig)) score += 2;
                        if (score > bestScore) {
                            best = node;
                            bestScore = score;
                        }
                    }
                    return best || el;
                };
                const clickEl = (el) => {
                    const target = interactiveOf(el);
                    try { target.scrollIntoView({ block: 'center', inline: 'center' }); } catch {}
                    try {
                        const rect = target.getBoundingClientRect();
                        const x = rect.left + Math.max(4, Math.min(rect.width - 4, rect.width / 2));
                        const y = rect.top + Math.max(4, Math.min(rect.height - 4, rect.height / 2));
                        target.focus?.();
                        for (const type of ['pointerover', 'mouseover', 'pointermove', 'mousemove', 'pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                            target.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window, clientX: x, clientY: y }));
                        }
                        return { ok: true, label: textOf(target), tag: target.tagName, role: target.getAttribute?.('role') || '', aria: target.getAttribute?.('aria-label') || '' };
                    } catch {}
                    return { ok: false, label: textOf(el), tag: el.tagName, role: el.getAttribute?.('role') || '', aria: el.getAttribute?.('aria-label') || '' };
                };
                const nodes = Array.from(document.querySelectorAll('button, [role="button"], [role="combobox"], [aria-haspopup], [data-testid], div, span'))
                    .filter(visible)
                    .filter((el) => {
                        const rect = el.getBoundingClientRect();
                        if (rect.top < window.innerHeight * 0.38 || rect.left < window.innerWidth * 0.38) return false;
                        if (rect.width > window.innerWidth * 0.65 || rect.height > window.innerHeight * 0.35) return false;
                        return true;
                    });
                const countryLike = nodes.filter((el) => {
                    const text = textOf(el);
                    if (!text || text.length > 180) return false;
                    return /country|currency|region|japan|\bjp\b|日本|united states|\bus\b|usa|美国|美國|アメリカ|米国/i.test(text);
                }).sort((a, b) => {
                    const ar = a.getBoundingClientRect();
                    const br = b.getBoundingClientRect();
                    const aText = textOf(a);
                    const bText = textOf(b);
                    const aSpecific = /country|currency|国家|地区|地域|国/i.test(aText) ? 1 : 0;
                    const bSpecific = /country|currency|国家|地区|地域|国/i.test(bText) ? 1 : 0;
                    return (bSpecific - aSpecific) || ((br.top + br.left) - (ar.top + ar.left));
                });
                const target = countryLike[0];
                if (!target) return { ok: false, label: '' };
                return clickEl(target);
            }"""
        )
        clicked_ok = bool(clicked) if not isinstance(clicked, dict) else bool(clicked.get("ok"))
        if clicked_ok:
            await page.wait_for_timeout(600)
            picked = await page.evaluate(
                r"""() => {
                    const visible = (el) => {
                        if (!el) return false;
                        const rect = el.getBoundingClientRect();
                        const style = getComputedStyle(el);
                        return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                    };
                    const textOf = (el) => String(el?.innerText || el?.textContent || el?.value || el?.getAttribute?.('aria-label') || '').replace(/\s+/g, ' ').trim();
                    const norm = (s) => String(s || '').replace(/\s+/g, ' ').trim().toLowerCase();
                    const exactUS = (el) => {
                        const text = norm(textOf(el));
                        const value = norm(el.getAttribute?.('data-value') || el.getAttribute?.('value') || '');
                        return (
                            text === 'united states' || text === 'united states of america' || text === 'usa' || text === 'us' ||
                            value === 'us' || text === '美国' || text === '美國' || text === 'アメリカ' || text === '米国'
                        );
                    };
                    const likelyUS = (el) => {
                        const text = textOf(el);
                        const value = String(el.getAttribute?.('data-value') || el.getAttribute?.('value') || '');
                        if (/japan|日本|jpy/i.test(text + ' ' + value)) return false;
                        return /united states|usa|\bus\b|美国|美國|アメリカ|米国/i.test(text + ' ' + value);
                    };
                    const selectors = [
                        '[role="option"]',
                        '[role="menuitem"]',
                        '[role="listbox"] [role="option"]',
                        '[data-radix-collection-item]',
                        '[cmdk-item]',
                        '[data-value]',
                        'li',
                        'button',
                        'div',
                        'span'
                    ];
                    const options = [];
                    for (const sel of selectors) {
                        for (const el of Array.from(document.querySelectorAll(sel))) {
                            if (!visible(el)) continue;
                            const text = textOf(el);
                            if (!text || text.length > 120) continue;
                            if (!likelyUS(el)) continue;
                            options.push(el);
                        }
                    }
                    const uniq = Array.from(new Set(options));
                    const target = uniq.find(exactUS) || uniq[0];
                    if (!target) return { ok: false, label: '' };
                    target.scrollIntoView({ block: 'center', inline: 'center' });
                    target.click();
                    return { ok: true, label: textOf(target).slice(0, 120) };
                }"""
            )
            if isinstance(picked, dict) and picked.get("ok"):
                await page.wait_for_timeout(1200)
                after_menu = await _probe_region()
                if after_menu.get("ok"):
                    log(f"{prefix} offer region selected US by menu: {after_menu.get('label', '')}")
                    return True
                log(
                    f"{prefix} offer region menu pick unconfirmed: "
                    f"{picked.get('label', '')} -> {after_menu.get('label', '')}"
                )
            try:
                candidates = await page.evaluate(
                    r"""() => {
                        const visible = (el) => {
                            if (!el) return false;
                            const rect = el.getBoundingClientRect();
                            const style = getComputedStyle(el);
                            return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                        };
                        const textOf = (el) => String(el?.innerText || el?.textContent || el?.value || el?.getAttribute?.('aria-label') || '').replace(/\s+/g, ' ').trim();
                        return Array.from(document.querySelectorAll('button, [role], [aria-haspopup], [data-testid], li, option, div, span'))
                            .filter(visible)
                            .map((el) => {
                                const rect = el.getBoundingClientRect();
                                return {
                                    tag: el.tagName,
                                    role: el.getAttribute?.('role') || '',
                                    aria: el.getAttribute?.('aria-label') || '',
                                    testid: el.getAttribute?.('data-testid') || '',
                                    text: textOf(el).slice(0, 120),
                                    x: Math.round(rect.left),
                                    y: Math.round(rect.top),
                                    w: Math.round(rect.width),
                                    h: Math.round(rect.height)
                                };
                            })
                            .filter((x) => x.text && x.text.length <= 140 && /country|currency|region|japan|united states|\bus\b|usa|usd|jpy|国家|地区|地域|日本|美国|美國|アメリカ|米国/i.test([x.text, x.aria, x.testid].join(' ')))
                            .slice(0, 12);
                    }"""
                )
                log(f"{prefix} offer region menu opened but US option not found: clicked={clicked} candidates={candidates}")
            except Exception:
                log(f"{prefix} offer region menu opened but US option not found: {clicked}")
    except Exception:
        pass
    final_probe = await _probe_region()
    if final_probe.get("label"):
        log(f"{prefix} offer region still not US: {final_probe.get('label', '')}")
    return False


async def _wait_chatgpt_offer_us_pricing(page, prefix: str, *, timeout_ms: int = 10_000) -> bool:
    """提交套餐前确认弹窗价格已随美国地区刷新，避免生成 JPY checkout。"""
    deadline = time.monotonic() + max(1.0, timeout_ms / 1000)
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        try:
            status = await page.evaluate(
                r"""() => {
                    const visible = (el) => {
                        if (!el) return false;
                        const rect = el.getBoundingClientRect();
                        const style = getComputedStyle(el);
                        return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                    };
                    const textOf = (el) => String(el?.innerText || el?.textContent || el?.value || el?.getAttribute?.('aria-label') || '').replace(/\s+/g, ' ').trim();
                    const scopes = Array.from(document.querySelectorAll('#modal-account-payment, [data-testid="modal-account-payment"], [role="dialog"]'))
                        .filter(visible);
                    const scope = scopes.find((el) => /plus|trial|free|country|currency|jpy|usd|\$|¥|套餐|試用|支払|支払い/i.test(textOf(el))) || null;
                    if (!scope) return { ok: true, reason: 'missing-modal' };
                    const text = textOf(scope).slice(0, 3000);
                    const exactRegion = Array.from(scope.querySelectorAll('[data-testid="country-selector-in-pricing-modal"]'))
                        .filter(visible)
                        .map((el) => textOf(el))
                        .find(Boolean) || '';
                    const fallbackRegion = Array.from(scope.querySelectorAll('[role="combobox"], button'))
                        .filter(visible)
                        .map((el) => textOf(el))
                        .find((value) => /country|currency|japan|united states|usa|\bus\b|usd|jpy|美国|美國|日本|アメリカ|米国/i.test(value)) || '';
                    const region = exactRegion || fallbackRegion;
                    const countryUS = /united states|usa|\bus\b|usd|美国|美國|アメリカ|米国/i.test(region);
                    const countryJP = /japan|\bjp\b|jpy|日本/i.test(region);
                    const hasJPY = /jpy|¥|￥|円/i.test(text);
                    const hasUSD = /usd|us\$|\$|united states|usa|\bus\b/i.test(text + ' ' + region);
                    return {
                        ok: (countryUS && !countryJP && !hasJPY) || (!hasJPY && hasUSD),
                        reason: 'modal-pricing',
                        countryUS,
                        countryJP,
                        hasJPY,
                        hasUSD,
                        region: region.slice(0, 160),
                        sample: text.slice(0, 220)
                    };
                }"""
            )
            if isinstance(status, dict):
                last = status
                if status.get("ok"):
                    return True
        except Exception as exc:
            last = {"reason": f"probe-error:{exc}"}
        await page.wait_for_timeout(500)
    log(
        f"{prefix} offer US pricing not ready before submit: "
        f"region={last.get('region', '')} hasJPY={last.get('hasJPY', '')} "
        f"hasUSD={last.get('hasUSD', '')} sample={last.get('sample', '')}"
    )
    return False


async def _typeahead_chatgpt_offer_region(page, prefix: str, query: str, expected: str, label: str) -> bool:
    """用地区下拉自身 typeahead 选择国家，避免把文本输入聊天框。"""
    try:
        trigger = page.locator(
            '[data-testid="country-selector-in-pricing-modal"] [role="combobox"], '
            '[data-testid="country-selector-in-pricing-modal"] button'
        ).first
        if not await trigger.is_visible(timeout=900) or not await trigger.is_enabled(timeout=900):
            return False
        await trigger.scroll_into_view_if_needed(timeout=1000)
        await trigger.click(timeout=1800)
        await page.wait_for_timeout(450)
        safe_state = await page.evaluate(
            r"""() => {
                const active = document.activeElement;
                const activeRole = active?.getAttribute?.('role') || '';
                const activeTag = String(active?.tagName || '').toLowerCase();
                const activeEditable = !!(active && (active.isContentEditable || /^(input|textarea)$/.test(activeTag) || activeRole === 'textbox'));
                const activeCombo = !!(active && activeRole === 'combobox' && active.closest?.('[data-testid="country-selector-in-pricing-modal"]'));
                const openMenu = !!document.querySelector('[role="listbox"], [role="menu"], [data-radix-select-content], [data-radix-popper-content-wrapper]');
                return { ok: !activeEditable && (activeCombo || openMenu), activeRole, activeTag, activeEditable, openMenu };
            }"""
        )
        if not isinstance(safe_state, dict) or not safe_state.get("ok"):
            log(f"{prefix} offer region refresh typeahead skipped unsafe focus: {label} state={safe_state}")
            return False
        await page.keyboard.type(query, delay=30)
        await page.wait_for_timeout(650)
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(1700)
        selected = await page.evaluate(
            r"""() => {
                const visible = (el) => {
                    if (!el) return false;
                    const rect = el.getBoundingClientRect();
                    const style = getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const textOf = (el) => String(el?.innerText || el?.textContent || el?.value || el?.getAttribute?.('aria-label') || '').replace(/\s+/g, ' ').trim();
                const node = Array.from(document.querySelectorAll('[data-testid="country-selector-in-pricing-modal"]')).filter(visible)[0] || null;
                return textOf(node).slice(0, 180);
            }"""
        )
        if re.search(expected, str(selected or ""), re.I):
            log(f"{prefix} offer region refresh selected {label}: {selected}")
            return True
        log(f"{prefix} offer region refresh unconfirmed {label}: {selected}")
    except Exception as exc:
        log(f"{prefix} offer region refresh failed {label}: {exc}")
    return False


async def _click_visible_us_chatgpt_offer_region_option(page, prefix: str) -> bool:
    """右下角已显示 US 时，仍打开菜单点一次 United States 选项，确保前端提交真实变更。"""
    if await _click_chatgpt_offer_region_option_strict(page, prefix, "us", "force"):
        return True
    try:
        trigger = page.locator(
            '[data-testid="country-selector-in-pricing-modal"] [role="combobox"], '
            '[data-testid="country-selector-in-pricing-modal"] button'
        ).first
        if not await trigger.is_visible(timeout=900) or not await trigger.is_enabled(timeout=900):
            return False
        await trigger.scroll_into_view_if_needed(timeout=1000)
        await trigger.click(timeout=1800, force=True)
        await page.wait_for_timeout(650)
        point = await page.evaluate(
            r"""() => {
                const visible = (el) => {
                    if (!el) return false;
                    const rect = el.getBoundingClientRect();
                    const style = getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const textOf = (el) => String(el?.innerText || el?.textContent || el?.value || el?.getAttribute?.('aria-label') || '').replace(/\s+/g, ' ').trim();
                const roots = [];
                const pushRoot = (el) => {
                    if (el && visible(el) && !roots.includes(el)) roots.push(el);
                };
                const trigger = document.querySelector('[data-testid="country-selector-in-pricing-modal"] [role="combobox"], [data-testid="country-selector-in-pricing-modal"] button');
                const controlId = trigger?.getAttribute?.('aria-controls') || '';
                pushRoot(controlId ? document.getElementById(controlId) : null);
                pushRoot(document.activeElement?.closest?.('[role="listbox"], [role="menu"], [data-radix-select-content], [data-radix-popper-content-wrapper]'));
                for (const node of Array.from(document.querySelectorAll('[role="listbox"], [role="menu"], [data-radix-select-content], [data-radix-popper-content-wrapper]')).filter(visible)) {
                    const text = textOf(node);
                    if (/united states|usa|\bus\b|usd|美国|美國|アメリカ|米国/i.test(text)) pushRoot(node);
                }
                const root = roots[0] || null;
                if (!root) return { ok: false, reason: 'no-root', controlId };
                const usText = /united states|usa|\bus\b|usd|美国|美國|アメリカ|米国/i;
                const badText = /country|currency|search|select|region|国家|地区|地域/i;
                const options = Array.from(root.querySelectorAll('[role="option"], [role="menuitem"], [data-radix-select-item], [data-radix-collection-item], [data-value], [tabindex], div, span, button'))
                    .filter(visible)
                    .map((el) => {
                        const item = el.closest?.('[role="option"], [role="menuitem"], [data-radix-select-item], [data-radix-collection-item], [data-value], [tabindex], button') || el;
                        const rect = item.getBoundingClientRect();
                        const text = textOf(item) || textOf(el);
                        const sig = [text, item.getAttribute?.('data-value') || '', item.getAttribute?.('value') || '', item.getAttribute?.('aria-label') || ''].join(' ');
                        if (!text || text.length > 120) return null;
                        if (!usText.test(sig) || badText.test(sig)) return null;
                        if (rect.width <= 0 || rect.height <= 0 || rect.width > Math.max(560, window.innerWidth * 0.48) || rect.height > 100) return null;
                        return {
                            item,
                            label: text.slice(0, 120),
                            x: Math.round(rect.left + Math.max(6, Math.min(rect.width - 6, rect.width / 2))),
                            y: Math.round(rect.top + Math.max(6, Math.min(rect.height - 6, rect.height / 2)))
                        };
                    })
                    .filter(Boolean);
                const hit = options.find((x) => /^united states$/i.test(x.label)) || options[0] || null;
                if (!hit) {
                    const samples = Array.from(root.querySelectorAll('[role="option"], [role="menuitem"], [data-radix-select-item], [data-radix-collection-item], [data-value], [tabindex], button'))
                        .filter(visible)
                        .map((el) => textOf(el))
                        .filter((text) => text && text.length <= 120)
                        .slice(0, 12);
                    return { ok: false, reason: 'no-us-option', samples };
                }
                hit.item.scrollIntoView({ block: 'center', inline: 'nearest' });
                return { ok: true, label: hit.label, x: hit.x, y: hit.y };
            }"""
        )
        if not isinstance(point, dict) or not point.get("ok"):
            log(f"{prefix} offer region force US option not found: {point}")
            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass
            return False
        await page.mouse.move(float(point.get("x") or 0), float(point.get("y") or 0))
        await page.mouse.down()
        await page.wait_for_timeout(80)
        await page.mouse.up()
        await page.wait_for_timeout(1800)
        selected = await page.evaluate(
            r"""() => {
                const visible = (el) => {
                    if (!el) return false;
                    const rect = el.getBoundingClientRect();
                    const style = getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const textOf = (el) => String(el?.innerText || el?.textContent || el?.value || el?.getAttribute?.('aria-label') || '').replace(/\s+/g, ' ').trim();
                const node = Array.from(document.querySelectorAll('[data-testid="country-selector-in-pricing-modal"]')).filter(visible)[0] || null;
                return textOf(node).slice(0, 180);
            }"""
        )
        log(f"{prefix} offer region force clicked US option: {point.get('label', '')} -> {selected}")
        return True
    except Exception as exc:
        log(f"{prefix} offer region force US option failed: {exc}")
        try:
            await page.keyboard.press("Escape")
        except Exception:
            pass
    return False


async def _click_visible_non_us_chatgpt_offer_region(page, prefix: str) -> bool:
    """地区已显示 US 但价格未刷新时，先点任一非 US 选项制造真实变更。"""
    if await _click_chatgpt_offer_region_option_strict(page, prefix, "jp", "refresh"):
        return True
    if await _click_chatgpt_offer_region_option_strict(page, prefix, "non_us", "refresh"):
        return True
    try:
        trigger = page.locator(
            '[data-testid="country-selector-in-pricing-modal"] [role="combobox"], '
            '[data-testid="country-selector-in-pricing-modal"] button'
        ).first
        if not await trigger.is_visible(timeout=900) or not await trigger.is_enabled(timeout=900):
            return False
        await trigger.scroll_into_view_if_needed(timeout=1000)
        await trigger.click(timeout=1800)
        await page.wait_for_timeout(600)
        point = await page.evaluate(
            r"""() => {
                const visible = (el) => {
                    if (!el) return false;
                    const rect = el.getBoundingClientRect();
                    const style = getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const textOf = (el) => String(el?.innerText || el?.textContent || el?.value || el?.getAttribute?.('aria-label') || '').replace(/\s+/g, ' ').trim();
                const active = document.activeElement;
                const roots = [];
                const pushRoot = (el) => {
                    if (el && visible(el) && !roots.includes(el)) roots.push(el);
                };
                const trigger = document.querySelector('[data-testid="country-selector-in-pricing-modal"] [role="combobox"], [data-testid="country-selector-in-pricing-modal"] button');
                const controlId = trigger?.getAttribute?.('aria-controls') || '';
                pushRoot(controlId ? document.getElementById(controlId) : null);
                pushRoot(active?.closest?.('[role="listbox"], [role="menu"], [data-radix-select-content], [data-radix-popper-content-wrapper]'));
                for (const node of Array.from(document.querySelectorAll('[role="listbox"], [role="menu"], [data-radix-select-content], [data-radix-popper-content-wrapper]')).filter(visible)) {
                    const rect = node.getBoundingClientRect();
                    const text = textOf(node);
                    if (rect.left < window.innerWidth * 0.35 && rect.width > window.innerWidth * 0.35) continue;
                    if (/japan|andorra|angola|country|currency|united states|united kingdom|canada|australia|euro|cape verde|djibouti/i.test(text)) pushRoot(node);
                }
                const root = roots[0] || null;
                if (!root) return { ok: false, reason: 'no-root', controlId };
                const usText = /united states|usa|\bus\b|usd|美国|美國|アメリカ|米国/i;
                const badText = /country|currency|search|select|region|国家|地区|地域/i;
                const options = Array.from(root.querySelectorAll('[role="option"], [role="menuitem"], [data-radix-select-item], [data-radix-collection-item], [data-value], [tabindex], div, span'))
                    .filter(visible)
                    .map((el) => {
                        const item = el.closest?.('[role="option"], [role="menuitem"], [data-radix-select-item], [data-radix-collection-item], [data-value], [tabindex]') || el;
                        const rect = item.getBoundingClientRect();
                        const text = textOf(item) || textOf(el);
                        const sig = [text, item.getAttribute?.('data-value') || '', item.getAttribute?.('value') || '', item.getAttribute?.('aria-label') || ''].join(' ');
                        if (!text || text.length > 120) return null;
                        if (rect.width <= 0 || rect.height <= 0 || rect.width > Math.max(520, window.innerWidth * 0.45) || rect.height > 96) return null;
                        if (usText.test(sig) || badText.test(sig)) return null;
                        return { item, label: text.slice(0, 120), x: Math.round(rect.left + Math.max(6, Math.min(rect.width - 6, rect.width / 2))), y: Math.round(rect.top + Math.max(6, Math.min(rect.height - 6, rect.height / 2))) };
                    })
                    .filter(Boolean);
                const hit = options[0] || null;
                if (!hit) {
                    const samples = Array.from(root.querySelectorAll('[role="option"], [role="menuitem"], [data-radix-select-item], [data-radix-collection-item], [data-value], [tabindex]'))
                        .filter(visible)
                        .map((el) => textOf(el))
                        .filter((text) => text && text.length <= 120)
                        .slice(0, 12);
                    return { ok: false, reason: 'no-non-us-option', samples };
                }
                hit.item.scrollIntoView({ block: 'center', inline: 'nearest' });
                return { ok: true, label: hit.label, x: hit.x, y: hit.y };
            }"""
        )
        if not isinstance(point, dict) or not point.get("ok"):
            log(f"{prefix} offer region refresh non-US option not found: {point}")
            return False
        await page.mouse.move(float(point.get("x") or 0), float(point.get("y") or 0))
        await page.mouse.down()
        await page.wait_for_timeout(80)
        await page.mouse.up()
        await page.wait_for_timeout(1800)
        selected = await page.evaluate(
            r"""() => {
                const visible = (el) => {
                    if (!el) return false;
                    const rect = el.getBoundingClientRect();
                    const style = getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const textOf = (el) => String(el?.innerText || el?.textContent || el?.value || el?.getAttribute?.('aria-label') || '').replace(/\s+/g, ' ').trim();
                const node = Array.from(document.querySelectorAll('[data-testid="country-selector-in-pricing-modal"]')).filter(visible)[0] || null;
                return textOf(node).slice(0, 180);
            }"""
        )
        log(f"{prefix} offer region refresh selected non-US option: {point.get('label', '')} -> {selected}")
        return True
    except Exception as exc:
        log(f"{prefix} offer region refresh non-US option failed: {exc}")
    return False


async def _refresh_chatgpt_offer_region_us(page, prefix: str) -> bool:
    """价格仍 JPY 时先切 Japan 再切回 US，强制触发价格刷新。"""
    if not await _click_visible_non_us_chatgpt_offer_region(page, prefix):
        await _typeahead_chatgpt_offer_region(page, prefix, "Japan", r"japan|\bjp\b|jpy|日本", "Japan")
    if await _typeahead_chatgpt_offer_region(
        page,
        prefix,
        "美国",
        r"united states|usa|\bus\b|usd|美国|美國|アメリカ|米国",
        "美国",
    ):
        return True
    return await _typeahead_chatgpt_offer_region(
        page,
        prefix,
        "United States",
        r"united states|usa|\bus\b|usd|美国|美國|アメリカ|米国",
        "United States",
    )


async def _ensure_chatgpt_offer_region_jp_before_us(page, prefix: str, email: str | None = None) -> bool:
    """日区短链优惠页必须先呈现日本地区，再由自动化切到美国。"""
    state = await _chatgpt_offer_region_state(page)

    label = str(state.get("label", "") if isinstance(state, dict) else "")
    if isinstance(state, dict) and state.get("isJP"):
        log(f"{prefix} offer region default JP before US switch: {label}")
        return True
    if not isinstance(state, dict) or not state.get("isUS"):
        log(f"{prefix} offer region JP precheck unknown; continue switch path: {label}")
        return True

    log(f"{prefix} offer region unexpectedly US before manual switch; reset to Japan first: {label}")
    if await _click_chatgpt_offer_region_option_strict(page, prefix, "jp", "precheck-reset"):
        await page.wait_for_timeout(800)
        return True
    for query, name in (("日本", "日本"), ("Japan", "Japan"), ("JP", "JP")):
        if await _typeahead_chatgpt_offer_region(page, prefix, query, r"japan|\bjp\b|jpy|日本", name):
            await page.wait_for_timeout(800)
            return True

    log(f"{prefix} offer plan submit skipped: cannot reset region to Japan before US switch")
    if email:
        await _save_chatgpt_offer_failure_debug_once(page, email, "cannot reset region to Japan before US switch")
    return False


async def _click_chatgpt_offer_plan_submit(page, prefix: str, email: str | None = None) -> bool:
    """套餐确认页：先把右下角地区切到美国，再点击继续/提交。"""
    current_region = await _chatgpt_offer_region_state(page)
    if isinstance(current_region, dict) and current_region.get("isUS"):
        log(f"{prefix} offer region already US before submit; skip JP precheck: {current_region.get('label', '')}")
    elif not await _ensure_chatgpt_offer_region_jp_before_us(page, prefix, email):
        return False
    if not await _select_chatgpt_offer_region_us(page, prefix):
        log(f"{prefix} offer plan submit skipped before US region: region switch failed")
        if email:
            await _save_chatgpt_offer_failure_debug_once(page, email, "region switch failed before offer submit")
        return False
    await _click_visible_us_chatgpt_offer_region_option(page, prefix)
    if not await _wait_chatgpt_offer_us_pricing(page, prefix, timeout_ms=4500):
        log(f"{prefix} offer plan submit skipped before US pricing: region/currency did not refresh to US")
        if email:
            await _save_chatgpt_offer_failure_debug_once(page, email, "region/currency did not refresh to US before offer submit")
        return False
    try:
        result = await page.evaluate(
            r"""() => {
                const visible = (el) => {
                    if (!el) return false;
                    const rect = el.getBoundingClientRect();
                    const style = getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const enabled = (el) => !el.disabled && el.getAttribute('aria-disabled') !== 'true';
                const textOf = (el) => String(el?.innerText || el?.textContent || el?.value || el?.getAttribute?.('aria-label') || '').replace(/\s+/g, ' ').trim();
                const signatureOf = (el) => [
                    textOf(el),
                    el?.getAttribute?.('aria-label') || '',
                    el?.getAttribute?.('data-testid') || '',
                    el?.getAttribute?.('title') || '',
                    el?.getAttribute?.('name') || ''
                ].join(' ');
                const usText = /united states|usa|\bus\b|usd|美国|美國|アメリカ|米国/i;
                const jpText = /japan|\bjp\b|jpy|日本/i;
                const exactRegionNode = Array.from(document.querySelectorAll('[data-testid="country-selector-in-pricing-modal"]')).filter(visible)[0] || null;
                const exactRegionText = textOf(exactRegionNode);
                const exactRegionUS = !!exactRegionText && usText.test(exactRegionText) && !jpText.test(exactRegionText);
                if (exactRegionText && jpText.test(exactRegionText) && !usText.test(exactRegionText)) {
                    return { ok: false, reason: 'region-not-us', label: exactRegionText };
                }
                const bottomRightNodes = Array.from(document.querySelectorAll('button, [role="button"], [role="combobox"], select, [data-testid], div, span'))
                    .filter(visible)
                    .filter((el) => {
                        const rect = el.getBoundingClientRect();
                        return rect.top > window.innerHeight * 0.45 && rect.left > window.innerWidth * 0.45;
                    });
                const countryNode = bottomRightNodes.find((el) => {
                    const text = textOf(el);
                    return text && text.length <= 80 && /japan|jp|日本|united states|usa|\bus\b|美国|美國|アメリカ|米国/i.test(text);
                });
                const countryText = countryNode ? textOf(countryNode) : '';
                if (!exactRegionUS && countryText && /japan|jp|日本/i.test(countryText) && !/united states|usa|\bus\b|美国|美國|アメリカ|米国/i.test(countryText)) {
                    return { ok: false, reason: 'region-not-us', label: countryText };
                }

                const scopes = Array.from(document.querySelectorAll('#modal-account-payment, [data-testid="modal-account-payment"], [role="dialog"], main, body'))
                    .filter(visible);
                const scope = scopes[0] || document.body;
                const bodyText = textOf(scope).slice(0, 4000);
                if (!/plus|trial|free|subscribe|payment|套餐|免费|免費|試用|支払|支払い/i.test(bodyText)) {
                    return { ok: false, reason: 'no-plan-surface', label: bodyText.slice(0, 120) };
                }
                const bad = /team|business|enterprise|workspace|education|country|region|japan|united states|paypal|card|close|cancel|dismiss|esc|back|团队|企業|地域|地区|国|国家|关闭|關閉|取消|閉じる|キャンセル|カード/i;
                const action = /claim\s+(?:plus\s+)?(?:free\s+)?offer|claim\s+offer|get\s+plus|upgrade|continue|next|start(?:\s+free)?\s+trial|try\s+plus|subscribe|confirm|checkout|check\s*out|proceed|领取\s*(?:plus\s*)?免费优惠|领取\s*plus|领取优惠|免费试用|开始.*试用|继续|下一步|订阅|訂閱|确认|確認|続行|次へ|申し込む|始める|支払いへ/i;
                const targets = Array.from(scope.querySelectorAll('button, [role="button"], a, input[type="submit"]'))
                    .filter((el) => visible(el) && enabled(el))
                    .map((el) => {
                        const text = textOf(el);
                        const rect = el.getBoundingClientRect();
                        const aria = el.getAttribute?.('aria-label') || '';
                        const testid = el.getAttribute?.('data-testid') || '';
                        return { el, text, aria, testid, signature: signatureOf(el), bottom: rect.top / Math.max(1, window.innerHeight), right: rect.left / Math.max(1, window.innerWidth) };
                    })
                    .filter((item) => item.text && item.text.length <= 180);
                const direct = targets.find((item) => (
                    !bad.test(item.signature) && (
                        /select-plan-button-plus-upgrade/i.test(item.testid) ||
                        /claim\s+plus\s+free\s+offer|领取\s*Plus\s*免费优惠|领取\s*免费优惠/i.test(item.signature)
                    )
                ));
                if (direct) {
                    direct.el.scrollIntoView({ block: 'center', inline: 'center' });
                    direct.el.click();
                    return { ok: true, reason: 'clicked-direct', label: direct.text.slice(0, 160) };
                }
                const filteredTargets = targets
                    .filter((item) => action.test(item.signature) && !bad.test(item.signature))
                    .sort((a, b) => ((b.bottom + b.right) - (a.bottom + a.right)));
                const target = filteredTargets[0];
                if (!target) {
                    return {
                        ok: false,
                        reason: 'submit-not-found',
                        label: bodyText.slice(0, 160),
                        candidates: targets.map((item) => ({ text: item.text.slice(0, 120), aria: item.aria.slice(0, 120), testid: item.testid })).slice(0, 12)
                    };
                }
                target.el.scrollIntoView({ block: 'center', inline: 'center' });
                target.el.click();
                return { ok: true, reason: 'clicked', label: target.text.slice(0, 160) };
            }"""
        )
        if isinstance(result, dict) and result.get("ok"):
            log(f"{prefix} clicked offer plan submit after US region: {result.get('label', '')}")
            return await _wait_checkout_surface_after_offer_submit(page, prefix, email=email)
        if isinstance(result, dict) and result.get("reason") == "region-not-us":
            log(f"{prefix} offer plan submit skipped before US region: {result.get('label', '')}")
            if email:
                await _save_chatgpt_offer_failure_debug_once(page, email, "region still not US before offer submit")
        elif isinstance(result, dict):
            log(
                f"{prefix} offer plan submit not found after US region: "
                f"reason={result.get('reason', '')} label={result.get('label', '')}"
            )
    except Exception:
        pass
    return False


async def _click_visible_offer_entry(page, prefix: str) -> bool:
    try:
        url = str(page.url or "").lower()
        if "/checkout/" in url or "checkout.openai" in url:
            log(f"{prefix} offer entry click skipped on checkout URL")
            return False
    except Exception:
        pass
    if await _chatgpt_offer_modal_visible(page):
        log(f"{prefix} offer entry click skipped inside pricing modal")
        return False
    claim_offer = _zh(r"\u9886\u53d6\u4f18\u60e0")
    free_trial = _zh(r"\u514d\u8d39\u8bd5\u7528")
    claim_offer_jp = _zh(r"\u30aa\u30d5\u30a1\u30fc\u3092\u53d7\u3051\u53d6\u308b")
    free_trial_jp = _zh(r"\u7121\u6599\u30c8\u30e9\u30a4\u30a2\u30eb")
    try_plus_jp = _zh(r"Plus\u3092\u8a66\u3059")
    get_plus_jp = _zh(r"Plus\u3092\u5165\u624b")
    upgrade_cn = _zh(r"\u5347\u7ea7")
    upgrade_jp = _zh(r"\u30a2\u30c3\u30d7\u30b0\u30ec\u30fc\u30c9")
    selectors = (
        'button:has-text("Claim offer")',
        'a:has-text("Claim offer")',
        '[role="button"]:has-text("Claim offer")',
        'button[aria-label*="Claim offer" i]',
        'a[aria-label*="Claim offer" i]',
        f'button:has-text("{claim_offer}")',
        f'a:has-text("{claim_offer}")',
        f'[role="button"]:has-text("{claim_offer}")',
        f'button:has-text("{claim_offer_jp}")',
        f'a:has-text("{claim_offer_jp}")',
        f'[role="button"]:has-text("{claim_offer_jp}")',
        f'button:has-text("{free_trial}")',
        f'a:has-text("{free_trial}")',
        f'button:has-text("{free_trial_jp}")',
        f'a:has-text("{free_trial_jp}")',
        f'button:has-text("{upgrade_cn}")',
        f'a:has-text("{upgrade_cn}")',
        f'button:has-text("{upgrade_jp}")',
        f'a:has-text("{upgrade_jp}")',
        f'button:has-text("{try_plus_jp}")',
        f'a:has-text("{try_plus_jp}")',
        f'button:has-text("{get_plus_jp}")',
        f'a:has-text("{get_plus_jp}")',
        'button:has-text("Upgrade")',
        'a:has-text("Upgrade")',
        'button:has-text("Try Plus")',
        'a:has-text("Try Plus")',
        'button:has-text("Get Plus")',
        'a:has-text("Get Plus")',
        'button:has-text("Free offer")',
        'a:has-text("Free offer")',
        '[role="button"]:has-text("Free offer")',
        'button[aria-label*="Free offer" i]',
        'a[aria-label*="Free offer" i]',
    )
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if await locator.is_visible(timeout=700) and await locator.is_enabled(timeout=700):
                in_pricing_modal = await locator.evaluate(
                    r"""(el) => {
                        const visible = (node) => {
                            if (!node) return false;
                            const rect = node.getBoundingClientRect();
                            const style = getComputedStyle(node);
                            return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                        };
                        const textOf = (node) => String(node?.innerText || node?.textContent || node?.getAttribute?.('aria-label') || '').replace(/\s+/g, ' ').trim();
                        const modal = el.closest('#modal-account-payment, [data-testid="modal-account-payment"]');
                        if (modal && visible(modal)) return true;
                        const dialog = el.closest('[role="dialog"]');
                        return !!(dialog && visible(dialog) && /plus|trial|free|country|currency|jpy|usd|套餐|試用|支払|支払い/i.test(textOf(dialog)));
                    }"""
                )
                if in_pricing_modal:
                    log(f"{prefix} offer modal plan button skipped by entry click: {selector}")
                    return False
                await locator.scroll_into_view_if_needed(timeout=1000)
                if await _chatgpt_offer_modal_visible(page):
                    log(f"{prefix} offer entry click skipped inside pricing modal")
                    return False
                await locator.click(timeout=2500)
                log(f"{prefix} clicked offer entry: {selector}")
                await page.wait_for_timeout(1800)
                return True
        except Exception:
            continue

    try:
        clicked = await page.evaluate(
            """() => {
                const visible = (el) => {
                    if (!el) return false;
                    const rect = el.getBoundingClientRect();
                    const style = getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const textOf = (el) => String(el.innerText || el.textContent || el.getAttribute('aria-label') || '').replace(/\\s+/g, ' ').trim();
                const modal = Array.from(document.querySelectorAll('#modal-account-payment, [data-testid="modal-account-payment"], [role="dialog"]'))
                    .filter(visible)
                    .find((el) => /plus|trial|free|country|currency|jpy|usd|套餐|試用|支払|支払い/i.test(textOf(el)));
                if (modal) return "";
                const nodes = Array.from(document.querySelectorAll('button, a, [role="button"]')).filter(visible);
                const bad = /team|business|enterprise|workspace|education|close|cancel|dismiss|\\u56e2\\u961f|\\u5718\\u968a|\\u4f01\\u4e1a|\\u4f01\\u696d|\\u5173\\u95ed|\\u95dc\\u9589|\\u53d6\\u6d88|\\u9589\\u3058\\u308b|\\u30ad\\u30e3\\u30f3\\u30bb\\u30eb|\\u30c1\\u30fc\\u30e0|\\u30d3\\u30b8\\u30cd\\u30b9|\\u30a8\\u30f3\\u30bf\\u30fc\\u30d7\\u30e9\\u30a4\\u30ba/i;
                const patterns = [
                    /free\\s*offer/i,
                    /claim\\s*offer/i,
                    /\u9886\u53d6\u4f18\u60e0/,
                    /\u514d\u8d39\u8bd5\u7528/,
                    /\u5347\u7ea7/,
                    /\u30aa\u30d5\u30a1\u30fc\u3092\u53d7\u3051\u53d6\u308b/,
                    /\u7121\u6599\u30c8\u30e9\u30a4\u30a2\u30eb/,
                    /\u30a2\u30c3\u30d7\u30b0\u30ec\u30fc\u30c9/,
                    /plus\u3092\u8a66\u3059/i,
                    /plus\u3092\u5165\u624b/i,
                    /upgrade/i,
                    /try\\s*plus/i,
                    /get\\s*plus/i,
                    /trial/i
                ];
                const target = nodes.find((node) => {
                    const text = textOf(node);
                    const signature = [text, node.getAttribute('aria-label') || '', node.getAttribute('data-testid') || '', node.getAttribute('href') || ''].join(' ');
                    return text && !bad.test(signature) && patterns.some((pattern) => pattern.test(signature));
                });
                if (!target) return "";
                target.scrollIntoView({ block: 'center', inline: 'center' });
                target.click();
                return textOf(target).slice(0, 120);
            }"""
        )
        if clicked:
            log(f"{prefix} clicked offer entry by JS: {clicked}")
            await page.wait_for_timeout(1800)
            return True
    except Exception:
        pass
    return False


async def _save_chatgpt_offer_failure_debug(page, email: str, reason: str) -> Path | None:
    """ChatGPT 优惠入口失败时保存当前页，定位真实按钮/地区控件。"""
    try:
        out_dir = PAYPAL_OUTPUT_ROOT / "debug" / "chatgpt_offer_failure" / f"{safe_filename(email)}_{int(time.time())}"
        out_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        log(f"[ChatGPTOffer] debug dir create failed: {exc}")
        return None
    try:
        (out_dir / "page.html").write_text(await page.content(), encoding="utf-8")
    except Exception as exc:
        log(f"[ChatGPTOffer] debug html save failed: {exc}")
    try:
        body_text = await page.evaluate("() => document.body?.innerText || ''")
        (out_dir / "body.txt").write_text(str(body_text or ""), encoding="utf-8")
    except Exception as exc:
        log(f"[ChatGPTOffer] debug body save failed: {exc}")
    try:
        state = await page.evaluate(
            r"""(reason) => {
                const visible = (el) => {
                    if (!el) return false;
                    const rect = el.getBoundingClientRect();
                    const style = getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const textOf = (el) => String(el?.innerText || el?.textContent || el?.value || el?.getAttribute?.('aria-label') || '').replace(/\s+/g, ' ').trim();
                const itemOf = (el) => {
                    const rect = el.getBoundingClientRect();
                    return {
                        tag: el.tagName,
                        id: el.getAttribute?.('id') || '',
                        role: el.getAttribute?.('role') || '',
                        aria: el.getAttribute?.('aria-label') || '',
                        ariaControls: el.getAttribute?.('aria-controls') || '',
                        ariaExpanded: el.getAttribute?.('aria-expanded') || '',
                        dataState: el.getAttribute?.('data-state') || '',
                        testid: el.getAttribute?.('data-testid') || '',
                        value: el.getAttribute?.('value') || '',
                        dataValue: el.getAttribute?.('data-value') || '',
                        className: String(el.className || '').slice(0, 180),
                        href: el.getAttribute?.('href') || '',
                        text: textOf(el).slice(0, 220),
                        x: Math.round(rect.left),
                        y: Math.round(rect.top),
                        w: Math.round(rect.width),
                        h: Math.round(rect.height)
                    };
                };
                const ancestorsOf = (el) => {
                    const items = [];
                    let node = el;
                    for (let depth = 0; node && node.nodeType === 1 && depth < 9; depth++, node = node.parentElement) {
                        const item = itemOf(node);
                        item.depth = depth;
                        item.className = String(node.className || '').slice(0, 180);
                        item.tabIndex = node.tabIndex;
                        item.cursor = getComputedStyle(node).cursor || '';
                        item.hasPopup = node.hasAttribute?.('aria-haspopup') || false;
                        items.push(item);
                    }
                    return items;
                };
                const rowOf = (el) => {
                    const rect = el.getBoundingClientRect();
                    return {
                        tag: el.tagName,
                        id: el.getAttribute?.('id') || '',
                        role: el.getAttribute?.('role') || '',
                        text: textOf(el).slice(0, 160),
                        value: el.getAttribute?.('value') || '',
                        dataValue: el.getAttribute?.('data-value') || '',
                        aria: el.getAttribute?.('aria-label') || '',
                        dataState: el.getAttribute?.('data-state') || '',
                        highlighted: el.hasAttribute?.('data-highlighted') || el.getAttribute?.('aria-selected') === 'true',
                        x: Math.round(rect.left),
                        y: Math.round(rect.top),
                        w: Math.round(rect.width),
                        h: Math.round(rect.height)
                    };
                };
                const trigger = document.querySelector('[data-testid="country-selector-in-pricing-modal"] [role="combobox"], [data-testid="country-selector-in-pricing-modal"] button');
                const controlId = trigger?.getAttribute?.('aria-controls') || '';
                const optionSelector = '[role="option"], [role="menuitem"], [data-radix-select-item], [data-radix-collection-item], [cmdk-item], [data-value], [tabindex]';
                const listRoots = [];
                const pushRoot = (root, source) => {
                    if (!root || !visible(root) || listRoots.some((item) => item.root === root)) return;
                    const rows = Array.from(root.querySelectorAll(optionSelector)).filter(visible);
                    if (!rows.length && !/listbox|menu/i.test(root.getAttribute?.('role') || '')) return;
                    const rect = root.getBoundingClientRect();
                    const scrollables = Array.from(root.querySelectorAll('[data-radix-select-viewport], [data-radix-scroll-area-viewport], [style*="overflow"], div'))
                        .filter(visible)
                        .filter((el) => el.scrollHeight > el.clientHeight + 8)
                        .map((el) => {
                            const sr = el.getBoundingClientRect();
                            return {
                                tag: el.tagName,
                                role: el.getAttribute?.('role') || '',
                                className: String(el.className || '').slice(0, 120),
                                scrollTop: el.scrollTop,
                                clientHeight: el.clientHeight,
                                scrollHeight: el.scrollHeight,
                                x: Math.round(sr.left),
                                y: Math.round(sr.top),
                                w: Math.round(sr.width),
                                h: Math.round(sr.height)
                            };
                        })
                        .slice(0, 8);
                    listRoots.push({
                        source,
                        id: root.getAttribute?.('id') || '',
                        role: root.getAttribute?.('role') || '',
                        dataState: root.getAttribute?.('data-state') || '',
                        text: textOf(root).slice(0, 260),
                        x: Math.round(rect.left),
                        y: Math.round(rect.top),
                        w: Math.round(rect.width),
                        h: Math.round(rect.height),
                        optionCount: rows.length,
                        rows: rows.slice(0, 80).map(rowOf),
                        scrollables,
                        html: String(root.outerHTML || '').slice(0, 12000)
                    });
                };
                if (controlId) pushRoot(document.getElementById(controlId), 'aria-controls');
                const active = document.activeElement;
                const activeDescendantId = active?.getAttribute?.('aria-activedescendant') || '';
                if (activeDescendantId) {
                    pushRoot(document.getElementById(activeDescendantId)?.closest?.('[role="listbox"], [role="menu"], [data-radix-select-content], [data-radix-popper-content-wrapper]'), 'active-descendant');
                }
                pushRoot(active?.closest?.('[role="listbox"], [role="menu"], [data-radix-select-content], [data-radix-popper-content-wrapper]'), 'active-root');
                for (const root of Array.from(document.querySelectorAll('[role="listbox"], [role="menu"], [role="dialog"], [data-radix-select-content], [data-radix-popper-content-wrapper], [cmdk-list], [data-side], [data-state="open"], [popover]')).filter(visible)) {
                    pushRoot(root, 'open-node');
                }
                const perfResources = Array.from(performance.getEntriesByType?.('resource') || [])
                    .map((entry) => ({
                        name: String(entry.name || '').slice(0, 360),
                        initiatorType: String(entry.initiatorType || ''),
                        duration: Math.round(Number(entry.duration || 0)),
                        transferSize: Number(entry.transferSize || 0),
                    }))
                    .filter((entry) => /chatgpt|openai|payment|billing|subscription|pricing|country|locale|backend-api|checkout|offer/i.test(entry.name))
                    .slice(-160);
                const regionNodes = Array.from(document.querySelectorAll('button, [role], [aria-haspopup], [data-testid], [tabindex], div, span'))
                    .filter(visible)
                    .filter((el) => /country|currency|region|japan|united states|\bus\b|usa|usd|jpy|国家|地区|地域|日本|美国|美國|アメリカ|米国/i.test([textOf(el), el.getAttribute?.('aria-label') || '', el.getAttribute?.('data-testid') || ''].join(' ')));
                return {
                    reason,
                    url: location.href,
                    title: document.title || '',
                    activeElement: document.activeElement ? itemOf(document.activeElement) : null,
                    regionControl: trigger ? itemOf(trigger) : null,
                    regionControlId: controlId,
                    listboxDetails: listRoots,
                    buttons: Array.from(document.querySelectorAll('button, a, [role="button"], [role="link"], [data-testid]'))
                        .filter(visible)
                        .map(itemOf)
                        .slice(0, 80),
                    regionCandidates: regionNodes
                        .map(itemOf)
                        .slice(0, 40),
                    regionAncestors: regionNodes
                        .slice(0, 12)
                        .map((node) => ({ text: textOf(node).slice(0, 180), chain: ancestorsOf(node) })),
                    popoverCandidates: Array.from(document.querySelectorAll('[role="listbox"], [role="menu"], [role="dialog"], [data-radix-popper-content-wrapper], [cmdk-list], [data-side], [data-state="open"], [popover]'))
                        .filter(visible)
                        .map(itemOf)
                        .slice(0, 40),
                    perfResources,
                    bodyHead: textOf(document.body).slice(0, 3000)
                };
            }""",
            reason,
        )
        if isinstance(state, dict):
            state["offerNetworkEvents"] = _get_chatgpt_offer_network_events(page)[-240:]
        (out_dir / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        log(f"[ChatGPTOffer] debug state save failed: {exc}")
    try:
        await page.screenshot(path=str(out_dir / "screenshot.png"), full_page=True)
    except Exception as exc:
        log(f"[ChatGPTOffer] debug screenshot save failed: {exc}")
    log(f"[ChatGPTOffer] failure debug saved: {out_dir}")
    return out_dir


async def _save_chatgpt_offer_failure_debug_once(page, email: str, reason: str) -> Path | None:
    """同一页面同一原因只保存一次，避免轮询阶段反复截屏。"""
    key = f"{id(page)}:{email.lower()}:{reason}"
    if key in _CHATGPT_OFFER_DEBUG_KEYS:
        return None
    _CHATGPT_OFFER_DEBUG_KEYS.add(key)
    return await _save_chatgpt_offer_failure_debug(page, email, reason)


def _chatgpt_offer_probe_url(url: str) -> bool:
    """记录 ChatGPT 优惠页关键请求，辅助判断地区列表是否由网络返回异常。"""
    value = str(url or "").lower()
    if not any(host in value for host in ("chatgpt.com", "openai.com", "pay.openai.com", "stripe.com")):
        return False
    return any(
        marker in value
        for marker in (
            "backend-api",
            "payments",
            "payment",
            "billing",
            "subscription",
            "subscriptions",
            "checkout",
            "pricing",
            "country",
            "locale",
            "region",
            "currency",
            "offer",
            "trial",
            "plans",
            "accounts",
        )
    )


def _chatgpt_offer_short_url(url: str, limit: int = 420) -> str:
    text = str(url or "")
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _get_chatgpt_offer_network_events(page) -> list[dict[str, Any]]:
    try:
        events = getattr(page, "_flow2_chatgpt_offer_network_events", None)
        if isinstance(events, list):
            return list(events)
    except Exception:
        pass
    return []


def _append_chatgpt_offer_network_event(page, item: dict[str, Any]) -> None:
    try:
        events = getattr(page, "_flow2_chatgpt_offer_network_events", None)
        if not isinstance(events, list):
            events = []
            setattr(page, "_flow2_chatgpt_offer_network_events", events)
        events.append(item)
        if len(events) > 900:
            events[:] = events[-700:]
    except Exception:
        pass


def _attach_chatgpt_offer_network_probe(page) -> None:
    """登录后进入优惠页前挂网络探针；失败现场写入 state.json。"""
    try:
        if getattr(page, "_flow2_chatgpt_offer_network_probe_attached", False):
            return
        setattr(page, "_flow2_chatgpt_offer_network_probe_attached", True)
        setattr(page, "_flow2_chatgpt_offer_network_events", [])
    except Exception:
        return

    def on_request(request) -> None:
        try:
            url = str(getattr(request, "url", "") or "")
            if not _chatgpt_offer_probe_url(url):
                return
            _append_chatgpt_offer_network_event(
                page,
                {
                    "kind": "request",
                    "method": str(getattr(request, "method", "") or ""),
                    "resource": str(getattr(request, "resource_type", "") or ""),
                    "url": _chatgpt_offer_short_url(url),
                    "ts": round(time.time(), 3),
                },
            )
        except Exception:
            pass

    def on_response(response) -> None:
        try:
            url = str(getattr(response, "url", "") or "")
            if not _chatgpt_offer_probe_url(url):
                return
            request = getattr(response, "request", None)
            _append_chatgpt_offer_network_event(
                page,
                {
                    "kind": "response",
                    "status": int(getattr(response, "status", 0) or 0),
                    "ok": bool(getattr(response, "ok", False)),
                    "method": str(getattr(request, "method", "") or ""),
                    "resource": str(getattr(request, "resource_type", "") or ""),
                    "url": _chatgpt_offer_short_url(url),
                    "ts": round(time.time(), 3),
                },
            )
        except Exception:
            pass

    def on_request_failed(request) -> None:
        try:
            url = str(getattr(request, "url", "") or "")
            if not _chatgpt_offer_probe_url(url):
                return
            failure = getattr(request, "failure", None)
            _append_chatgpt_offer_network_event(
                page,
                {
                    "kind": "requestfailed",
                    "method": str(getattr(request, "method", "") or ""),
                    "resource": str(getattr(request, "resource_type", "") or ""),
                    "error": str(failure or "")[:500],
                    "url": _chatgpt_offer_short_url(url),
                    "ts": round(time.time(), 3),
                },
            )
        except Exception:
            pass

    try:
        page.on("request", on_request)
        page.on("response", on_response)
        page.on("requestfailed", on_request_failed)
    except Exception:
        pass


async def _prepare_checkout_from_chatgpt_offer(
    page,
    item: dict[str, str],
    cfg: dict[str, Any],
    *,
    proxy: str | None,
    prefix: str,
    watcher_enabled: bool,
    flow_env: dict[str, str] | None = None,
) -> None:
    account = parse_mail_line(item.get("account_line", "")) or MailAccount(
        email=item["email"],
        mail_url=item.get("query_code") or None,
        raw=item.get("account_line", "") or item["email"],
    )
    mail_cfg = cfg.get("mail", {})
    fallback_source = str(mail_cfg.get("active_source") or mail_cfg.get("source") or "").strip() or "hotmail"
    mail_provider = MailProvider(
        source=_mail_source_for_account(account, fallback_source),
        timeout_sec=int(mail_cfg.get("code_timeout_sec", 150)),
        poll_interval_sec=int(mail_cfg.get("poll_interval_sec", 5)),
        log_prefix=prefix,
    )
    register = ChatGPTRegister(
        page=page,
        page_getter=None,
        start_url="https://chatgpt.com/auth/login",
        entry_action="login",
        mail_provider=mail_provider,
        age_min=21,
        age_max=45,
        sms_selection=None,
        log_prefix=prefix,
        proxy=proxy,
    )
    await _install_click_watcher(page, item["email"], enabled=watcher_enabled, label="flow2_direct_offer")
    _attach_chatgpt_offer_network_probe(page)
    log(f"{prefix} long link disabled; login ChatGPT and open offer checkout")
    await register.run_until_logged_in(account, datetime.now(timezone.utc))
    start_url = paypal_direct_checkout_start_url(flow_env or load_env(".env"))
    await page.goto(start_url, wait_until="domcontentloaded", timeout=45_000)
    await page.wait_for_timeout(2500)
    await _install_click_watcher(page, item["email"], enabled=watcher_enabled, label="flow2_direct_offer")
    await _dismiss_chatgpt_interstitials(page, prefix)
    if await _checkout_surface_ready(page):
        return
    region_failures = 0
    for attempt in range(1, _CHATGPT_OFFER_SURFACE_MAX_ATTEMPTS + 1):
        if attempt in {1, 4, 8, 12, 16, 20, 24, 30, _CHATGPT_OFFER_SURFACE_MAX_ATTEMPTS}:
            log(
                f"{prefix} waiting direct checkout surface via ChatGPT offer "
                f"({attempt}/{_CHATGPT_OFFER_SURFACE_MAX_ATTEMPTS}), url={page.url}"
            )
        if await _checkout_surface_ready(page):
            return
        await _dismiss_chatgpt_interstitials(page, prefix)
        region_ready = await _select_chatgpt_offer_region_us(page, prefix)
        if await _chatgpt_offer_modal_visible(page) and not region_ready:
            region_failures += 1
            if region_failures >= 3:
                await _save_chatgpt_offer_failure_debug(
                    page,
                    item["email"],
                    "offer region failed to switch US after 3 attempts",
                )
                raise RuntimeError("套餐页右下角地区未能自动切换到美国")
            await page.wait_for_timeout(1200)
            continue
        submit_attempted = False
        submit_ok = False
        clicked_trial = await _click_zero_trial_plus_option(page, prefix)
        if clicked_trial:
            submit_attempted = True
            submit_ok = await _click_chatgpt_offer_plan_submit(page, prefix, email=item["email"])
        elif await _chatgpt_offer_modal_visible(page):
            submit_attempted = True
            submit_ok = await _click_chatgpt_offer_plan_submit(page, prefix, email=item["email"])
        if submit_attempted and not submit_ok and await _chatgpt_offer_modal_visible(page):
            region_failures += 1
            if region_failures >= 3:
                await _save_chatgpt_offer_failure_debug(
                    page,
                    item["email"],
                    "offer region/currency failed before submit after 3 attempts",
                )
                raise RuntimeError("套餐页地区/货币未能切换到美国")
            await page.wait_for_timeout(1200)
            continue
        if region_ready or submit_ok:
            region_failures = 0
        if await _checkout_surface_ready(page):
            return
        clicked = await _click_visible_offer_entry(page, prefix)
        if clicked:
            submit_attempted = True
            submit_ok = await _click_chatgpt_offer_plan_submit(page, prefix, email=item["email"])
            if not submit_ok and await _chatgpt_offer_modal_visible(page):
                region_failures += 1
                if region_failures >= 3:
                    await _save_chatgpt_offer_failure_debug(
                        page,
                        item["email"],
                        "offer region/currency failed after entry click",
                    )
                    raise RuntimeError("套餐页地区/货币未能切换到美国")
                await page.wait_for_timeout(1200)
                continue
        if await _checkout_surface_ready(page):
            return
        if clicked:
            await _select_chatgpt_offer_region_us(page, prefix)
            clicked_trial_after_offer = await _click_zero_trial_plus_option(page, prefix)
            if clicked_trial_after_offer:
                submit_attempted = True
                submit_ok = await _click_chatgpt_offer_plan_submit(page, prefix, email=item["email"])
            elif await _chatgpt_offer_modal_visible(page):
                submit_attempted = True
                submit_ok = await _click_chatgpt_offer_plan_submit(page, prefix, email=item["email"])
            if submit_attempted and not submit_ok and await _chatgpt_offer_modal_visible(page):
                region_failures += 1
                if region_failures >= 3:
                    await _save_chatgpt_offer_failure_debug(
                        page,
                        item["email"],
                        "offer region/currency failed after offer entry retry",
                    )
                    raise RuntimeError("套餐页地区/货币未能切换到美国")
                await page.wait_for_timeout(1200)
                continue
            if await _checkout_surface_ready(page):
                return
        if not clicked and not clicked_trial and attempt in {8, 16, 24, 30}:
            try:
                await page.goto("https://chatgpt.com/#pricing", wait_until="domcontentloaded", timeout=30_000)
                await page.wait_for_timeout(1800)
            except Exception:
                pass
        await page.wait_for_timeout(1500)
    await _save_chatgpt_offer_failure_debug(
        page,
        item["email"],
        "direct offer checkout surface not found after region US",
    )
    raise RuntimeError(
        _zh(
            r"\u5df2\u5173\u95ed\u957f\u94fe\uff0c\u4f46\u767b\u5f55\u540e\u672a\u80fd\u81ea\u52a8\u627e\u5230\u9886\u53d6\u4f18\u60e0/\u5347\u7ea7\u5165\u53e3\u6216\u652f\u4ed8\u9875\u5143\u7d20"
        )
    )


def save_pending_auth(email: str, query_code: str, account_line: str | None = None) -> None:
    PENDING_AUTH_DIR.mkdir(parents=True, exist_ok=True)
    raw = str(account_line or "").strip()
    line = raw if raw and raw.split("----", 1)[0].strip().lower() == email.strip().lower() else f"{email}----{query_code}"
    with PENDING_AUTH_FILE.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    paypal_flow_state.mark_paid_pending_auth(email, account_line=line)


def remove_from_link_pool(email: str) -> None:
    if not LINK_POOL_FILE.exists():
        return
    lines = LINK_POOL_FILE.read_text(encoding="utf-8").splitlines()
    remaining = [l for l in lines if not l.strip().lower().startswith(email.lower())]
    LINK_POOL_FILE.write_text("\n".join(remaining) + ("\n" if remaining else ""), encoding="utf-8")


def upsert_link_pool_item(email: str, account_line: str, payment_link: str) -> None:
    normalized = (email or "").strip().lower()
    if not normalized:
        return
    prefix = _strip_payment_link_from_account_line(account_line) or email.strip()
    LINK_POOL_FILE.parent.mkdir(parents=True, exist_ok=True)
    lines = LINK_POOL_FILE.read_text(encoding="utf-8").splitlines() if LINK_POOL_FILE.exists() else []
    remaining = [line for line in lines if not line.strip().lower().startswith(normalized)]
    remaining.append(f"{prefix}----{payment_link}")
    LINK_POOL_FILE.write_text("\n".join(remaining) + "\n", encoding="utf-8")


def discard_flow2_link(email: str, *, reason: str) -> None:
    remove_from_link_pool(email)
    paypal_flow_state.append_discarded_emails([email], reason=reason)
    paypal_flow_state.mark_discarded_many([email], reason=reason)


def mark_link_for_regeneration(email: str, *, account_line: str = "", reason: str, failed_link_method: str = "") -> None:
    remove_from_link_pool(email)
    paypal_flow_state.mark_needs_link(
        email,
        account_line=account_line,
        reason=reason,
        failed_link_method=failed_link_method,
    )


def _is_recreate_link_reason(reason: str | None) -> bool:
    return str(reason or "").startswith(PAYPAL_FLOW2_RECREATE_LINK)


def _is_no_paypal_option_reason(reason: str | None) -> bool:
    return str(reason or "").startswith(PAYPAL_FLOW2_NO_PAYPAL_OPTION)


async def regenerate_flow2_payment_link(
    item: dict[str, str],
    cfg: dict[str, Any],
    *,
    worker_id: int,
    proxy: str | None,
    flow2_region_mode: str,
    mail_source: str = "",
) -> tuple[str, str]:
    from .paypal_register import login_existing_account_for_checkout

    account_line = item.get("account_line", "") or _strip_payment_link_from_account_line(item.get("raw", ""))
    account = parse_mail_line(account_line)
    if not account:
        raise RuntimeError(f"cannot recreate payment link: invalid account line for {item.get('email')}")
    resolved_mail_source = mail_source or _mail_source_for_account(account, "hotmail")
    region = "jp" if _normalize_flow2_region_mode(flow2_region_mode) == "jp" else "us"
    method_sink: dict[str, str] = {}
    failed_method = str(item.get("link_method") or "").strip()
    preferred_methods = tuple(method for method in PAYPAL_FLOW2_RECREATE_METHOD_ORDER if method != failed_method)
    if not preferred_methods:
        preferred_methods = PAYPAL_FLOW2_RECREATE_METHOD_ORDER
    log(
        f"[paypal-pay-{worker_id:02d}][{account.email}] "
        + _zh(r"\u91cd\u65b0\u751f\u6210\u652f\u4ed8\u957f\u94fe: ")
        + " -> ".join(preferred_methods)
        + (f" (skip={failed_method})" if failed_method else "")
    )
    link = await login_existing_account_for_checkout(
        account,
        resolved_mail_source,
        cfg,
        worker_id=worker_id,
        proxy=proxy,
        create_payment_link=True,
        session_source="paypal_flow2_recreate_link",
        checkout_region=region,
        checkout_skip_methods={failed_method} if failed_method else set(),
        checkout_preferred_methods=preferred_methods,
        checkout_method_sink=method_sink,
    )
    if not link:
        raise RuntimeError("cannot recreate payment link: generator returned empty link")
    method = str(method_sink.get("method") or "").strip()
    item["payment_link"] = str(link)
    item["link_method"] = method
    upsert_link_pool_item(account.email, account_line, str(link))
    paypal_flow_state.mark_link_ready(account.email, account_line=account_line, payment_link=str(link), link_method=method)
    log(
        f"[paypal-pay-{worker_id:02d}][{account.email}] "
        + _zh(r"\u65b0\u652f\u4ed8\u957f\u94fe\u5df2\u751f\u6210\uff0cmethod=")
        + (method or "unknown")
    )
    return str(link), method


_CHECKOUT_AMOUNT_KEYWORDS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (
        0,
        (
            "due today",
            "total due",
            "amount due",
            "due now",
            "today's total",
            "today total",
            _zh(r"\u672c\u65e5"),
            _zh(r"\u4eca\u65e5"),
            _zh(r"\u304a\u652f\u6255\u3044"),
            _zh(r"\u652f\u6255\u3044"),
            _zh(r"\u652f\u6255"),
            _zh(r"\u8acb\u6c42"),
            _zh(r"\u5e94\u4ed8"),
            _zh(r"\u61c9\u4ed8"),
            _zh(r"\u652f\u4ed8"),
        ),
    ),
    (
        1,
        (
            "total",
            "amount",
            _zh(r"\u5408\u8a08"),
            _zh(r"\u603b\u8ba1"),
            _zh(r"\u7e3d\u8a08"),
            _zh(r"\u5408\u8ba1"),
            _zh(r"\u91d1\u989d"),
            _zh(r"\u91d1\u984d"),
        ),
    ),
    (
        2,
        (
            "pay now",
            "pay ",
            "pay\n",
        ),
    ),
)

_MONEY_RE = re.compile(
    r"(?i)(?:US\$|CA\$|AU\$|[$\uFFE5\u00A5]|USD|JPY|EUR|GBP)\s*[-+]?\d[\d,]*(?:\.\d{1,2})?"
    r"|[-+]?\d[\d,]*(?:\.\d{1,2})?\s*(?:USD|JPY|EUR|GBP|\u5186)"
)


def _money_value(raw: str) -> float | None:
    text = str(raw or "").strip()
    number = re.sub(r"(?i)(US\$|CA\$|AU\$|USD|JPY|EUR|GBP|\u5186|[$\uFFE5\u00A5]|\s)", "", text)
    number = re.sub(r"[^0-9,.\-+]", "", number)
    if not number or not re.search(r"\d", number):
        return None
    if "," in number and "." not in number:
        last = number.rsplit(",", 1)[-1]
        if len(last) in {1, 2}:
            number = number.replace(".", "").replace(",", ".")
        else:
            number = number.replace(",", "")
    else:
        number = number.replace(",", "")
    try:
        return float(number)
    except ValueError:
        return None


def _money_candidates(text: str, *, limit: int = 8) -> list[str]:
    seen: set[str] = set()
    values: list[str] = []
    for match in _MONEY_RE.finditer(str(text or "")):
        item = re.sub(r"\s+", " ", match.group(0)).strip()
        if item and item not in seen:
            seen.add(item)
            values.append(item)
        if len(values) >= limit:
            break
    return values


def classify_checkout_amount_candidates(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    parsed: list[dict[str, Any]] = []
    raw_candidates: list[str] = []
    for idx, candidate in enumerate(candidates or []):
        text = str(candidate.get("text") or "").strip()
        selector = str(candidate.get("selector") or "").strip()
        if not text:
            continue
        for raw_amount in _money_candidates(text, limit=3):
            value = _money_value(raw_amount)
            if value is None:
                continue
            priority_value = candidate.get("priority")
            priority = priority_value if isinstance(priority_value, int) else 50
            if raw_amount not in raw_candidates:
                raw_candidates.append(raw_amount)
            parsed.append(
                {
                    "priority": priority,
                    "index": idx,
                    "amount_text": raw_amount,
                    "amount_value": value,
                    "selector": selector,
                    "source_text": text[:500],
                }
            )
            break

    if not parsed:
        return {
            "status": "unknown",
            "reason": "no_dom_amount_candidate",
            "amount_candidates": raw_candidates[:8],
        }

    parsed.sort(key=lambda item: (int(item["priority"]), int(item["index"])))
    selected = parsed[0]
    status = "nonzero" if abs(float(selected["amount_value"])) > 0.000001 else "zero"
    return {
        "status": status,
        "amount_text": selected["amount_text"],
        "amount_value": selected["amount_value"],
        "source_text": selected["source_text"],
        "selector": selected["selector"],
        "amount_candidates": raw_candidates[:8],
    }


def classify_checkout_due_amount(text: str) -> dict[str, Any]:
    lines = [re.sub(r"\s+", " ", line).strip() for line in str(text or "").splitlines()]
    lines = [line for line in lines if line]
    candidates: list[dict[str, Any]] = []
    for idx, line in enumerate(lines):
        lower = line.lower()
        priority = None
        for candidate_priority, keywords in _CHECKOUT_AMOUNT_KEYWORDS:
            if any(keyword in lower or keyword in line for keyword in keywords):
                priority = candidate_priority
                break
        if priority is None:
            continue
        for amount_idx in range(idx, min(idx + 4, len(lines))):
            matches = list(_MONEY_RE.finditer(lines[amount_idx]))
            if not matches:
                continue
            raw_amount = matches[0].group(0)
            value = _money_value(raw_amount)
            if value is None:
                continue
            candidates.append(
                {
                    "priority": priority,
                    "line": idx,
                    "amount_line": amount_idx,
                    "amount_text": raw_amount,
                    "amount_value": value,
                    "source_text": " | ".join(lines[idx : amount_idx + 1])[:500],
                }
            )
            break

    if not candidates:
        return {
            "status": "unknown",
            "reason": "no_due_amount_candidate",
            "amount_candidates": _money_candidates(text),
        }

    candidates.sort(key=lambda item: (int(item["priority"]), int(item["line"]), int(item["amount_line"])))
    selected = candidates[0]
    status = "nonzero" if abs(float(selected["amount_value"])) > 0.000001 else "zero"
    return {
        "status": status,
        "amount_text": selected["amount_text"],
        "amount_value": selected["amount_value"],
        "source_text": selected["source_text"],
    }


def _checkout_due_amount_looks_jpy(due_amount: dict[str, Any]) -> bool:
    """短链日区必须在套餐页切美国；若 checkout 仍是 JPY，则不会出现 PayPal。"""
    text = " ".join(
        str(due_amount.get(key) or "")
        for key in ("amount_text", "source_text", "selector", "amount_candidates")
    )
    return bool(re.search(r"(?i)(?:JPY|\u5186|[\u00a5\uffe5])", text))


async def inspect_checkout_due_amount(page) -> dict[str, Any]:
    wait_error = ""
    try:
        await page.wait_for_selector(
            "#OrderDetails-TotalAmount .CurrencyAmount, "
            "#OrderDetails-TotalAmount, "
            "#ProductSummary-totalAmount .CurrencyAmount, "
            "[data-testid=\"product-summary-total-amount\"] .CurrencyAmount, "
            ".CurrencyAmount",
            state="attached",
            timeout=10000,
        )
    except Exception as exc:
        wait_error = str(exc)[:160]

    try:
        dom_candidates = await page.evaluate(
            """() => {
                const textOf = (el) => (el && (el.innerText || el.textContent) || '').replace(/\\s+/g, ' ').trim();
                const visible = (el) => {
                    if (!el) return false;
                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width >= 0 && rect.height >= 0;
                };
                const out = [];
                const push = (selector, priority, label) => {
                    for (const el of document.querySelectorAll(selector)) {
                        const text = textOf(el);
                        if (!text || !visible(el)) continue;
                        out.push({ selector, priority, label, text });
                    }
                };
                push('#OrderDetails-TotalAmount .CurrencyAmount, #OrderDetails-TotalAmount', 0, 'order_total');
                push('[data-testid="order-details-total-amount"] .CurrencyAmount, [data-testid="order-details-total-amount"]', 0, 'order_total_testid');
                push('.OrderDetails-total .CurrencyAmount, .OrderDetails-total', 0, 'order_total_class');
                push('button[type="submit"] .CurrencyAmount, button[type="submit"], [data-testid*="submit"] .CurrencyAmount', 1, 'submit_amount');
                push('[data-testid="order-details-footer-subtotal-amount"] .CurrencyAmount, [data-testid="order-details-footer-subtotal-amount"]', 4, 'subtotal');
                push('#ProductSummary-totalAmount .CurrencyAmount, #ProductSummary-totalAmount', 6, 'product_summary');
                push('[data-testid="product-summary-total-amount"] .CurrencyAmount, [data-testid="product-summary-total-amount"]', 6, 'product_summary_testid');
                push('[data-testid="line-item-total-amount"] .CurrencyAmount, [data-testid="line-item-total-amount"]', 8, 'line_item');
                for (const el of document.querySelectorAll('.CurrencyAmount, [class*="CurrencyAmount"]')) {
                    const text = textOf(el);
                    if (!text || !visible(el)) continue;
                    const ctx = el.closest('[id], [data-testid], .OrderDetails-total, .LineItem, .ProductSummary, .YGErOEoF__Subtotal');
                    const ctxText = textOf(ctx).slice(0, 500);
                    let priority = 20;
                    const hay = `${ctx?.id || ''} ${ctx?.getAttribute('data-testid') || ''} ${ctx?.className || ''} ${ctxText}`.toLowerCase();
                    if (/orderdetails-totalamount|orderdetails-total|order-details-total/.test(hay)) priority = 0;
                    else if (/subtotal|tax|fee/.test(hay)) priority = 7;
                    else if (/productsummary|product-summary|line-item/.test(hay)) priority = 8;
                    out.push({ selector: 'currency_amount_scan', priority, label: 'scan', text: ctxText || text });
                }
                return out.slice(0, 80);
            }"""
        )
        dom_result = classify_checkout_amount_candidates(dom_candidates if isinstance(dom_candidates, list) else [])
        if dom_result.get("status") != "unknown":
            dom_result["source"] = "dom"
            return dom_result
        if wait_error:
            dom_result["wait_error"] = wait_error
    except Exception:
        dom_result = {
            "status": "unknown",
            "reason": "dom_amount_probe_failed",
            "wait_error": wait_error,
            "amount_candidates": [],
        }

    try:
        text = await page.locator("body").inner_text(timeout=5000)
    except Exception as exc:
        return {"status": "unknown", "reason": f"read_body_failed:{exc}"}
    text_result = classify_checkout_due_amount(text)
    if text_result.get("status") == "unknown" and dom_result.get("amount_candidates"):
        text_result["amount_candidates"] = dom_result.get("amount_candidates")
    if text_result.get("status") == "unknown" and dom_result.get("wait_error"):
        text_result["wait_error"] = dom_result.get("wait_error")
    return text_result


def generate_paypal_password(email: str) -> str:
    local = email.split("@")[0] if "@" in email else email
    prefix_raw = re.sub(r"[^a-zA-Z0-9]", "", local)
    prefix = (prefix_raw[:3] or "usr").lower()

    upper_pool = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    lower_pool = "abcdefghjkmnpqrstuvwxyz"
    digit_pool = "246789"
    seed = hashlib.sha256(email.lower().encode("utf-8")).digest()

    def pick(pool: str, idx: int) -> str:
        return pool[seed[idx] % len(pool)]

    candidate = (
        f"{prefix}{pick(upper_pool, 0)}{pick(lower_pool, 1)}"
        f"#{pick(digit_pool, 2)}{pick(upper_pool, 3)}{pick(digit_pool, 4)}"
        f"!{pick(lower_pool, 5)}{pick(upper_pool, 6)}"
    )

    def has_4_key_sequence(s: str) -> bool:
        t = s.lower()
        rows = ("0123456789", "qwertyuiop", "asdfghjkl", "zxcvbnm")
        for row in rows:
            for i in range(len(row) - 3):
                seq = row[i : i + 4]
                if seq in t or seq[::-1] in t:
                    return True
        for i in range(len(t) - 3):
            chunk = t[i : i + 4]
            if chunk.isdigit():
                vals = [ord(c) for c in chunk]
                if all(vals[j + 1] - vals[j] == 1 for j in range(3)) or all(
                    vals[j + 1] - vals[j] == -1 for j in range(3)
                ):
                    return True
            if chunk.isalpha():
                vals = [ord(c) for c in chunk]
                if all(vals[j + 1] - vals[j] == 1 for j in range(3)) or all(
                    vals[j + 1] - vals[j] == -1 for j in range(3)
                ):
                    return True
        return False

    if has_4_key_sequence(candidate):
        candidate = (
            f"{prefix}{pick(upper_pool, 7)}{pick(lower_pool, 8)}"
            f"#{pick(digit_pool, 9)}{pick(upper_pool, 10)}{pick(digit_pool, 11)}"
            f"!{pick(lower_pool, 12)}{pick(upper_pool, 13)}"
        )
    return candidate


def poll_sms_code(api_url: str, *, timeout: int = 120, interval: int = 5) -> str:
    """轮询手机 API 获取验证码。"""
    def _extract_code(text: str) -> str | None:
        raw = (text or "").strip()
        if not raw:
            return None

        # 1) 兼容历史 "ok|短信内容" / "no|暂无验证码" 风格
        parts = raw.split("|", 2)
        status = parts[0].lower() if parts else ""
        content = parts[1] if len(parts) > 1 else ""
        if status and status != "no" and content and content != "暂无验证码":
            m = re.search(r"\b(\d{4,8})\b", content)
            if m:
                return m.group(1)

        # 2) 兼容 JSON 风格（常见字段：SmsCode / code / smsCode / SmsContent / message）
        if raw.startswith("{") or raw.startswith("["):
            try:
                data = json.loads(raw)
            except Exception:
                data = None
            if data is None:
                return None
            rows = data if isinstance(data, list) else [data]
            for row in rows:
                if not isinstance(row, dict):
                    continue
                for key in ("SmsCode", "smsCode", "code", "Code", "otp", "Otp"):
                    val = str(row.get(key, "")).strip()
                    if re.fullmatch(r"\d{4,8}", val):
                        return val
                merged = " ".join(
                    str(row.get(k, "")).strip()
                    for k in ("SmsContent", "smsContent", "content", "message", "msg", "body")
                ).strip()
                if merged:
                    m = re.search(r"\b(\d{4,8})\b", merged)
                    if m:
                        return m.group(1)

        # 3) 兜底：直接从全文本提取 4-8 位数字
        m = re.search(r"\b(\d{4,8})\b", raw)
        if m:
            return m.group(1)
        return None

    start = time.time()
    while time.time() - start < timeout:
        try:
            resp = requests.get(api_url, timeout=10)
            text = resp.text.strip()
            code = _extract_code(text)
            if code:
                return code
        except Exception:
            pass
        time.sleep(interval)
    raise TimeoutError(f"手机验证码超时 ({timeout}s)")


async def _fill_paypal_jp_identity(page, *, email: str, card: CardInfo) -> None:
    birth, first_kana, last_kana, first_kanji, last_kanji = _jp_identity_values(card, email)

    # 1) 生日
    dob_selectors = [
        'input[name*="birth" i]',
        'input[id*="birth" i]',
        'input[placeholder*="年/月/日"]',
        'input[aria-label*="生年月日" i]',
    ]
    for sel in dob_selectors:
        try:
            loc = page.locator(f"{sel}:visible").first
            if await loc.is_visible(timeout=800):
                await loc.fill("", timeout=1500)
                await loc.fill(birth, timeout=3000)
                break
        except Exception:
            continue

    # 2) 假名 + 汉字（兜底用 JS，按字段语义和空值状态填充）
    try:
        result = await page.evaluate(
            """(payload) => {
                const isVisible = (el) => {
                    if (!el) return false;
                    const r = el.getBoundingClientRect();
                    if (r.width < 8 || r.height < 8) return false;
                    const st = window.getComputedStyle(el);
                    if (!st) return false;
                    if (st.display === 'none' || st.visibility === 'hidden') return false;
                    if (Number(st.opacity || '1') < 0.05) return false;
                    return !el.disabled && !el.readOnly;
                };
                const setVal = (el, v) => {
                    const proto = HTMLInputElement.prototype;
                    const desc = Object.getOwnPropertyDescriptor(proto, 'value');
                    if (desc && typeof desc.set === 'function') desc.set.call(el, v);
                    else el.value = v;
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    el.dispatchEvent(new Event('blur', { bubbles: true }));
                };
                const sig = (el) => {
                    const p = String(el.placeholder || '').toLowerCase();
                    const a = String(el.getAttribute('aria-label') || '').toLowerCase();
                    const n = String(el.name || '').toLowerCase();
                    const i = String(el.id || '').toLowerCase();
                    const t = (el.closest('label')?.innerText || el.parentElement?.innerText || '').toLowerCase();
                    return `${p} ${a} ${n} ${i} ${t}`;
                };
                const isExcluded = (s) => /email|mail|phone|zip|postal|address|city|street|state|country|card|cvv|exp|password/.test(s);
                const inputs = Array.from(document.querySelectorAll('input'))
                    .filter(isVisible)
                    .filter((el) => {
                        const type = String(el.type || 'text').toLowerCase();
                        return ['text', 'search', 'tel', ''].includes(type);
                    });

                const used = new Set();

                // DOB
                for (const el of inputs) {
                    const s = sig(el);
                    if (s.includes('生年月日') || s.includes('birth') || s.includes('年/月/日')) {
                        if (!String(el.value || '').trim()) setVal(el, payload.birth);
                        used.add(el);
                        break;
                    }
                }

                // Kana fields
                const kanaCandidates = inputs.filter((el) => {
                    const s = sig(el);
                    return s.includes('かな') || s.includes('カナ') || s.includes('furigana') || s.includes('phonetic') || s.includes('kana');
                }).filter((el) => !used.has(el));
                const setIfEmpty = (el, v) => {
                    if (!el) return false;
                    if (String(el.value || '').trim()) return false;
                    setVal(el, v);
                    return true;
                };
                let kanaFirst = false;
                let kanaLast = false;
                for (const el of kanaCandidates) {
                    const s = sig(el);
                    if (!kanaFirst && /(^|\\s)(名|first|given)($|\\s)/.test(s)) kanaFirst = setIfEmpty(el, payload.firstKana) || kanaFirst;
                    if (!kanaLast && /(^|\\s)(姓|last|family)($|\\s)/.test(s)) kanaLast = setIfEmpty(el, payload.lastKana) || kanaLast;
                }
                const kanaByLeft = kanaCandidates.slice().sort((a, b) => a.getBoundingClientRect().left - b.getBoundingClientRect().left);
                if (!kanaFirst && kanaByLeft[0]) kanaFirst = setIfEmpty(kanaByLeft[0], payload.firstKana) || kanaFirst;
                if (!kanaLast && kanaByLeft[1]) kanaLast = setIfEmpty(kanaByLeft[1], payload.lastKana) || kanaLast;
                kanaCandidates.forEach((el) => used.add(el));

                // Kanji fields: 优先“漢字 区域 + 名/姓语义 + 非地址邮箱类字段”
                const kanjiCandidates = inputs.filter((el) => {
                    if (used.has(el)) return false;
                    const s = sig(el);
                    if (isExcluded(s)) return false;
                    if (s.includes('かな') || s.includes('カナ') || s.includes('furigana') || s.includes('phonetic') || s.includes('kana')) return false;
                    if (s.includes('漢字')) return true;
                    return s.includes('名') || s.includes('姓') || s.includes('name') || s.includes('first') || s.includes('last');
                });
                let kanjiFirst = false;
                let kanjiLast = false;
                for (const el of kanjiCandidates) {
                    const s = sig(el);
                    if (!kanjiFirst && /(^|\\s)(名|first|given)($|\\s)/.test(s)) kanjiFirst = setIfEmpty(el, payload.firstKanji) || kanjiFirst;
                    if (!kanjiLast && /(^|\\s)(姓|last|family)($|\\s)/.test(s)) kanjiLast = setIfEmpty(el, payload.lastKanji) || kanjiLast;
                }
                const kanjiByLeft = kanjiCandidates.slice().sort((a, b) => a.getBoundingClientRect().left - b.getBoundingClientRect().left);
                if (!kanjiFirst && kanjiByLeft[0]) kanjiFirst = setIfEmpty(kanjiByLeft[0], payload.firstKanji) || kanjiFirst;
                if (!kanjiLast && kanjiByLeft[1]) kanjiLast = setIfEmpty(kanjiByLeft[1], payload.lastKanji) || kanjiLast;

                // 必填兜底：如果还有“名/姓”空框，按语义再补一次（避免只填了一个）
                const empties = inputs.filter((el) => !String(el.value || '').trim());
                for (const el of empties) {
                    const s = sig(el);
                    if (isExcluded(s)) continue;
                    if (s.includes('かな') || s.includes('カナ') || s.includes('furigana') || s.includes('phonetic') || s.includes('kana')) continue;
                    if (!kanjiFirst && /(^|\\s)(名|first|given)($|\\s)/.test(s)) kanjiFirst = setIfEmpty(el, payload.firstKanji) || kanjiFirst;
                    if (!kanjiLast && /(^|\\s)(姓|last|family)($|\\s)/.test(s)) kanjiLast = setIfEmpty(el, payload.lastKanji) || kanjiLast;
                }

                return {
                    kanaCount: kanaCandidates.length,
                    kanjiCount: kanjiCandidates.length,
                    kanaFirst,
                    kanaLast,
                    kanjiFirst,
                    kanjiLast,
                };
            }""",
            {
                "birth": birth,
                "firstKana": first_kana,
                "lastKana": last_kana,
                "firstKanji": first_kanji,
                "lastKanji": last_kanji,
            },
        )
        log(
            f"[PayPal][JP] 已尝试填充生日/假名/汉字: dob={birth}, "
            f"kana_candidates={(result or {}).get('kanaCount', 0)}, "
            f"kanji_candidates={(result or {}).get('kanjiCount', 0)}, "
            f"kana_ok=({(result or {}).get('kanaFirst', False)},{(result or {}).get('kanaLast', False)}), "
            f"kanji_ok=({(result or {}).get('kanjiFirst', False)},{(result or {}).get('kanjiLast', False)})"
        )
    except Exception as exc:
        log(f"[PayPal][JP] 日本实名字段填充异常: {exc}")


async def _ensure_stripe_paypal_selected(page, prefix: str = "[Stripe]") -> bool:
    """Stripe/Checkout 页：确认 PayPal 支付方式可见并重新点选，防止切国家后被重置。"""
    async def _click_paypal_in_target(target, target_name: str) -> str:
        try:
            result = await target.evaluate(
                r"""() => {
                    const visible = (el) => {
                        if (!el) return false;
                        const rect = el.getBoundingClientRect();
                        const style = getComputedStyle(el);
                        return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity || '1') > 0.02;
                    };
                    const metaOf = (el) => [
                        el?.innerText || '',
                        el?.textContent || '',
                        el?.getAttribute?.('aria-label') || '',
                        el?.getAttribute?.('title') || '',
                        el?.getAttribute?.('alt') || '',
                        el?.getAttribute?.('data-testid') || '',
                        el?.getAttribute?.('id') || '',
                        el?.getAttribute?.('name') || '',
                        el?.getAttribute?.('value') || '',
                        el?.getAttribute?.('src') || ''
                    ].join(' ').replace(/\s+/g, ' ').trim();
                    const selectedState = () => {
                        const radio = document.querySelector(
                            '#payment-method-accordion-item-title-paypal, input[type="radio"][value="paypal"], input[name="payment-method-accordion-item-title"][value="paypal"]'
                        );
                        const item = document.querySelector('[data-testid="paypal-accordion-item"]');
                        const button = document.querySelector('[data-testid="paypal-accordion-item-button"]');
                        const values = [
                            radio && ('checked' in radio ? String(!!radio.checked) : ''),
                            radio?.getAttribute?.('aria-checked') || '',
                            item?.getAttribute?.('aria-checked') || '',
                            item?.getAttribute?.('aria-expanded') || '',
                            item?.getAttribute?.('data-state') || '',
                            button?.getAttribute?.('aria-checked') || '',
                            button?.getAttribute?.('aria-expanded') || '',
                            button?.getAttribute?.('data-state') || '',
                            String(radio?.className || ''),
                            String(item?.className || ''),
                            String(button?.className || '')
                        ].join(' ').toLowerCase();
                        const selectedByText = /已选择\s*PayPal|PayPal\s*已选择|PayPal[\s\S]{0,30}selected|selected[\s\S]{0,30}PayPal/i.test(
                            [
                                item && metaOf(item),
                                button && metaOf(button),
                                document.body?.innerText || document.body?.textContent || ''
                            ].join(' ').replace(/\s+/g, ' ')
                        );
                        return /true|open|checked|selected|active/.test(values) || selectedByText;
                    };
                    const firePointerClick = (node) => {
                        if (!node) return false;
                        try { node.scrollIntoView({ block: 'center', inline: 'center' }); } catch (e) {}
                        for (const [name, init] of [
                            ['pointerdown', { bubbles: true, cancelable: true, pointerType: 'mouse' }],
                            ['mousedown', { bubbles: true, cancelable: true }],
                            ['pointerup', { bubbles: true, cancelable: true, pointerType: 'mouse' }],
                            ['mouseup', { bubbles: true, cancelable: true }],
                            ['click', { bubbles: true, cancelable: true }]
                        ]) {
                            try {
                                const ctor = name.startsWith('pointer') && window.PointerEvent ? PointerEvent : MouseEvent;
                                node.dispatchEvent(new ctor(name, init));
                            } catch (e) {}
                        }
                        try { node.click(); } catch (e) {}
                        return true;
                    };
                    const clickAt = (node, xRatio, yRatio) => {
                        if (!node || !visible(node)) return false;
                        const rect = node.getBoundingClientRect();
                        const x = Math.max(rect.left + 4, Math.min(rect.right - 4, rect.left + rect.width * xRatio));
                        const y = Math.max(rect.top + 4, Math.min(rect.bottom - 4, rect.top + rect.height * yRatio));
                        let hit = document.elementFromPoint(x, y) || node;
                        for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                            try {
                                const init = {
                                    bubbles: true,
                                    cancelable: true,
                                    view: window,
                                    clientX: x,
                                    clientY: y,
                                    pointerType: 'mouse',
                                };
                                const ctor = name.startsWith('pointer') && window.PointerEvent ? PointerEvent : MouseEvent;
                                hit.dispatchEvent(new ctor(name, init));
                            } catch (e) {}
                        }
                        return true;
                    };
                    const nativeCheckRadio = (radio) => {
                        if (!radio) return false;
                        try {
                            const desc = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'checked');
                            if (desc && typeof desc.set === 'function') desc.set.call(radio, true);
                            else radio.checked = true;
                            radio.setAttribute('aria-checked', 'true');
                            radio.dispatchEvent(new Event('input', { bubbles: true }));
                            radio.dispatchEvent(new Event('change', { bubbles: true }));
                            return true;
                        } catch (e) {
                            return false;
                        }
                    };
                    const tryCandidate = (node, mode) => {
                        if (!node || !visible(node)) return null;
                        const item = node.closest?.('[data-testid="paypal-accordion-item"], .PaymentMethodFormAccordionItem, [role="listitem"], label') || node;
                        // 优先点真实 radio 与其左侧位置；避免点到包含“银行卡 PayPal 电话号码”的大表单容器。
                        const radio = item.querySelector?.('input[type="radio"][value="paypal"], input[name="payment-method-accordion-item-title"][value="paypal"]')
                            || (String(node.matches?.('input[type="radio"]')) === 'true' ? node : null);
                        firePointerClick(radio || node);
                        clickAt(radio || item, 0.06, 0.5);
                        clickAt(item, 0.08, 0.5);
                        clickAt(item, 0.5, 0.5);
                        if (selectedState()) {
                            return { clicked: true, selected: true, mode, label: metaOf(item).slice(0, 180) || metaOf(node).slice(0, 180) };
                        }
                        nativeCheckRadio(radio);
                        if (selectedState()) {
                            return { clicked: true, selected: true, mode: `${mode}:native-radio`, label: metaOf(item).slice(0, 180) || metaOf(node).slice(0, 180) };
                        }
                        return { clicked: true, selected: false, mode, label: metaOf(item).slice(0, 180) || metaOf(node).slice(0, 180) };
                    };

                    const directSelectors = [
                        '#payment-method-accordion-item-title-paypal',
                        'input[type="radio"][value="paypal"]',
                        'input[name="payment-method-accordion-item-title"][value="paypal"]',
                        '[aria-labelledby="payment-method-label-paypal"]',
                        '[data-testid="paypal-accordion-item"]',
                        '[data-testid="paypal-accordion-item-button"]'
                    ];
                    for (const selector of directSelectors) {
                        const node = document.querySelector(selector);
                        const result = tryCandidate(node, `direct:${selector}`);
                        if (result?.selected) return result;
                    }
                    const label = document.querySelector('#payment-method-label-paypal');
                    const labelResult = tryCandidate(label, 'label-id');
                    if (labelResult?.selected) return labelResult;

                    const nodes = Array.from(document.querySelectorAll('button, [role="button"], [role="radio"], label, [data-testid], [aria-label], div, span'))
                        .filter(visible)
                        .map((el) => {
                            const rect = el.getBoundingClientRect();
                            const text = metaOf(el);
                            const hasPayPal = /paypal|pay\s*pal/i.test(text);
                            const badLarge = rect.width > Math.max(620, window.innerWidth * 0.55)
                                || rect.height > 180
                                || /银行卡|card|电话号码|phone|联系信息|subscription|订阅|subscribe/i.test(text.replace(/pay\s*pal/ig, ''));
                            return { el, rect, text, hasPayPal, badLarge };
                        })
                        .filter((item) => item.hasPayPal && !item.badLarge && !/powered\s+by/i.test(item.text))
                        .sort((a, b) => (a.rect.width * a.rect.height) - (b.rect.width * b.rect.height));
                    for (const item of nodes.slice(0, 8)) {
                        const result = tryCandidate(item.el, 'scoped-text-paypal');
                        if (result?.selected) return result;
                    }
                    return { clicked: false, selected: selectedState(), mode: '', label: '' };
                }"""
            )
            if isinstance(result, dict) and result.get("clicked") and result.get("selected"):
                label = str(result.get("label") or "")
                mode = str(result.get("mode") or "direct-paypal")
                return f"{target_name}:{mode}:{label[:80]}"
        except Exception:
            pass
        selectors = (
            '#payment-method-accordion-item-title-paypal',
            'input[type="radio"][value="paypal"]',
            'input[name="payment-method-accordion-item-title"][value="paypal"]',
            '[aria-labelledby="payment-method-label-paypal"]',
            '[data-testid="paypal-accordion-item"]',
            '[data-testid="paypal-accordion-item-button"]',
            'button[aria-label*="PayPal" i]',
            '[role="button"][aria-label*="PayPal" i]',
            '[role="radio"][aria-label*="PayPal" i]',
            'label[aria-label*="PayPal" i]',
            'button:has-text("PayPal")',
            '[role="button"]:has-text("PayPal")',
            '[role="radio"]:has-text("PayPal")',
            'label:has-text("PayPal")',
            'img[alt*="PayPal" i]',
            'img[src*="paypal" i]',
        )
        for selector in selectors:
            try:
                locator = target.locator(selector).first
                if await locator.is_visible(timeout=500):
                    await locator.scroll_into_view_if_needed(timeout=1000)
                    await locator.click(timeout=2500, force=True)
                    await page.wait_for_timeout(350)
                    snapshot = await _paypal_snapshot(target, target_name)
                    if snapshot.get("selected"):
                        return f"{target_name}:{selector}"
            except Exception:
                continue
        return ""

    async def _paypal_snapshot(target, target_name: str) -> dict[str, Any]:
        try:
            result = await target.evaluate(
                r"""() => {
                    const visible = (el) => {
                        if (!el) return false;
                        const rect = el.getBoundingClientRect();
                        const style = getComputedStyle(el);
                        return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                    };
                    const metaOf = (el) => [
                        el?.innerText || '',
                        el?.textContent || '',
                        el?.getAttribute?.('aria-label') || '',
                        el?.getAttribute?.('title') || '',
                        el?.getAttribute?.('alt') || '',
                        el?.getAttribute?.('data-testid') || '',
                        el?.getAttribute?.('id') || '',
                        el?.getAttribute?.('name') || '',
                        el?.getAttribute?.('value') || '',
                        el?.getAttribute?.('src') || ''
                    ].join(' ').replace(/\s+/g, ' ').trim();
                    const radio = document.querySelector(
                        '#payment-method-accordion-item-title-paypal, input[type="radio"][value="paypal"], input[name="payment-method-accordion-item-title"][value="paypal"]'
                    );
                    const item = document.querySelector('[data-testid="paypal-accordion-item"]');
                    const button = document.querySelector('[data-testid="paypal-accordion-item-button"]');
                    const nodes = Array.from(document.querySelectorAll(
                        'button, [role="button"], [role="radio"], label, a, [tabindex], [data-testid], [aria-label], img, svg, div, span, input[type="radio"]'
                    ))
                        .filter(visible)
                        .filter((el) => /paypal|pay\s*pal/i.test(metaOf(el)) && !/powered\s+by/i.test(metaOf(el)));
                    const selectedText = [
                        radio && ('checked' in radio ? String(!!radio.checked) : ''),
                        radio?.getAttribute?.('aria-checked') || '',
                        item?.getAttribute?.('aria-checked') || '',
                        item?.getAttribute?.('aria-expanded') || '',
                        item?.getAttribute?.('data-state') || '',
                        button?.getAttribute?.('aria-checked') || '',
                        button?.getAttribute?.('aria-expanded') || '',
                        button?.getAttribute?.('data-state') || '',
                        String(radio?.className || ''),
                        String(item?.className || ''),
                        String(button?.className || '')
                    ].join(' ').toLowerCase();
                    const body = String(document.body?.innerText || document.body?.textContent || '').replace(/\s+/g, ' ').trim();
                    const selectedByText = /已选择\s*PayPal|PayPal\s*已选择|PayPal[\s\S]{0,30}selected|selected[\s\S]{0,30}PayPal/i.test(
                        [metaOf(radio), metaOf(item), metaOf(button), body].filter(Boolean).join(' ')
                    );
                    const selected = /true|open|checked|selected|active/.test(selectedText) || selectedByText;
                    return {
                        hasPayPal: nodes.length > 0 || !!radio || !!item || !!button,
                        selected,
                        label: [metaOf(radio), metaOf(item), metaOf(button), ...nodes.map(metaOf)]
                            .filter(Boolean).slice(0, 5).join(' | ').slice(0, 260),
                        radioChecked: radio && ('checked' in radio ? !!radio.checked : null),
                        radioAria: radio?.getAttribute?.('aria-checked') || '',
                        selectedByText,
                        body: body.slice(0, 220)
                    };
                }"""
            )
            if isinstance(result, dict):
                result["target"] = target_name
                return result
        except Exception as exc:
            return {"target": target_name, "error": str(exc)[:160], "hasPayPal": False, "selected": False, "label": "", "body": ""}
        return {"target": target_name, "hasPayPal": False, "selected": False, "label": "", "body": ""}

    async def _targets() -> list[tuple[str, Any]]:
        targets: list[tuple[str, Any]] = [("main", page)]
        for frame in page.frames:
            if frame is page.main_frame:
                continue
            url = str(getattr(frame, "url", "") or "").lower()
            if "stripe" not in url and "private" not in url and "payment" not in url:
                continue
            targets.append((f"iframe-paypal:{len(targets)}", frame))
        return targets

    click_mode = ""
    try:
        for attempt in range(3):
            for name, target in await _targets():
                click_mode = await _click_paypal_in_target(target, name)
                if click_mode:
                    await page.wait_for_timeout(700)
                    snapshot = await _paypal_snapshot(target, name)
                    if snapshot.get("selected"):
                        break
                    log(
                        f"{prefix} PayPal click did not select yet "
                        f"(attempt={attempt + 1}/3, mode={click_mode}, "
                        f"radio_checked={snapshot.get('radioChecked')}, label={snapshot.get('label', '')})"
                    )
                    click_mode = ""
            if click_mode:
                await page.wait_for_timeout(800)
                break
            await page.wait_for_timeout(500)
    except Exception as exc:
        log(f"{prefix} PayPal payment method click probe failed: {exc}")
    clicked = bool(click_mode)
    snapshots: list[dict[str, Any]] = []
    try:
        for name, target in await _targets():
            snapshot = await _paypal_snapshot(target, name)
            snapshots.append(snapshot)
            if snapshot.get("hasPayPal") and snapshot.get("selected"):
                log(
                    f"{prefix} PayPal payment method confirmed "
                    f"(clicked={clicked}, mode={click_mode}, target={snapshot.get('target')}, "
                    f"selected={snapshot.get('selected')}, label={snapshot.get('label', '')})"
                )
                return True
        summary = [
            {
                "target": item.get("target", ""),
                "hasPayPal": item.get("hasPayPal", False),
                "selected": item.get("selected", False),
                "label": str(item.get("label", ""))[:120],
                "body": str(item.get("body", ""))[:120],
                "error": item.get("error", ""),
            }
            for item in snapshots
        ][:8]
        log(f"{prefix} PayPal payment method not visible before submit: {summary}")
    except Exception as exc:
        log(f"{prefix} PayPal payment method probe failed: {exc}")
    return False


async def _ensure_stripe_required_checkboxes(page, prefix: str = "[Stripe]") -> dict[str, Any]:
    """Stripe 页：只勾选必要条款类 checkbox，避开 AI-agent / Link CLI 引导控件。"""
    try:
        result = await page.evaluate(
            r"""() => {
                const visible = (el) => {
                    if (!el) return false;
                    const rect = el.getBoundingClientRect();
                    const style = getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const textOf = (el) => String(el?.innerText || el?.textContent || el?.value || el?.getAttribute?.('aria-label') || '').replace(/\s+/g, ' ').trim();
                const labelOf = (el) => {
                    const labels = [];
                    if (el.id) {
                        const byFor = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
                        if (byFor) labels.push(textOf(byFor));
                    }
                    const label = el.closest && el.closest('label');
                    if (label) labels.push(textOf(label));
                    let parent = el.parentElement;
                    for (let i = 0; parent && i < 3; i += 1, parent = parent.parentElement) {
                        const text = textOf(parent);
                        if (text) labels.push(text);
                    }
                    return labels.find(Boolean)?.slice(0, 180) || textOf(el).slice(0, 180) || el.getAttribute?.('name') || el.getAttribute?.('id') || '';
                };
                const checkedOf = (el) => {
                    if ('checked' in el) return !!el.checked;
                    return String(el.getAttribute('aria-checked') || '').toLowerCase() === 'true';
                };
                const setUnchecked = (el) => {
                    if (!checkedOf(el)) return;
                    try { el.scrollIntoView({ block: 'center', inline: 'center' }); } catch {}
                    try { el.click(); } catch {}
                    if ('checked' in el && el.checked) {
                        el.checked = false;
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                    if (!('checked' in el)) {
                        el.setAttribute('aria-checked', 'false');
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                };
                const setChecked = (el) => {
                    try { el.scrollIntoView({ block: 'center', inline: 'center' }); } catch {}
                    try { el.click(); } catch {}
                    if ('checked' in el && !el.checked) {
                        el.checked = true;
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                    if (!('checked' in el)) {
                        el.setAttribute('aria-checked', 'true');
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                };
                const allBoxes = Array.from(document.querySelectorAll('input[type="checkbox"], [role="checkbox"]'))
                    .filter(visible);
                const agentPattern = /ai\s*agent|link\s*cli|agent_identity_token|agent\s*identity|underlying payment credentials/i;
                const boxes = [];
                const skippedAgent = [];
                const uncheckedAgent = [];
                for (const box of allBoxes) {
                    const label = labelOf(box);
                    const agentHost = box.closest && box.closest('.AiAgentPaymentSteering, [class*="AiAgentPaymentSteering"]');
                    const agentText = [
                        label,
                        textOf(box.closest && box.closest('label')),
                        box.getAttribute?.('name') || '',
                        box.getAttribute?.('id') || '',
                        box.getAttribute?.('aria-label') || ''
                    ].join(' ');
                    if (agentHost || agentPattern.test(agentText)) {
                        const before = checkedOf(box);
                        setUnchecked(box);
                        const after = checkedOf(box);
                        if (before && !after) uncheckedAgent.push(label);
                        else skippedAgent.push(label);
                        continue;
                    }
                    boxes.push({ box, label });
                }
                const touched = [];
                for (const item of boxes) {
                    const box = item.box;
                    if (checkedOf(box)) continue;
                    const before = checkedOf(box);
                    setChecked(box);
                    const after = checkedOf(box);
                    touched.push({ label: item.label, before, after });
                }
                return {
                    total: boxes.length,
                    totalAll: allBoxes.length,
                    touched: touched.length,
                    labels: touched.map((x) => `${x.after ? 'checked' : 'forced'}:${x.label}`).slice(0, 8),
                    checkedAfter: boxes.map((x) => x.box).filter(checkedOf).length,
                    skippedAgent: skippedAgent.length,
                    uncheckedAgent: uncheckedAgent.length,
                    agentLabels: [...uncheckedAgent, ...skippedAgent].slice(0, 4)
                };
            }"""
        )
        if isinstance(result, dict):
            log(
                f"{prefix} checkout checkbox sweep: total={result.get('total', 0)} "
                f"all={result.get('totalAll', 0)} "
                f"touched={result.get('touched', 0)} checked={result.get('checkedAfter', 0)} "
                f"skipped_agent={result.get('skippedAgent', 0)} "
                f"unchecked_agent={result.get('uncheckedAgent', 0)} "
                f"labels={result.get('labels', [])}"
            )
            return result
    except Exception as exc:
        log(f"{prefix} checkout checkbox sweep skipped: {exc}")
    return {"total": 0, "touched": 0, "checkedAfter": 0, "labels": []}


def _stripe_probe_url(url: str) -> bool:
    """仅记录 Stripe/PayPal 关键请求，避免日志膨胀。"""
    value = str(url or "").lower()
    if not any(host in value for host in ("stripe.com", "pay.openai.com", "paypal.com")):
        return False
    return any(
        marker in value
        for marker in (
            "payment_pages",
            "confirm",
            "sessions",
            "checkout",
            "redirect",
            "paypal",
            "hcaptcha",
            "human-security",
            "pay.openai.com/c/pay",
        )
    )


def _stripe_short_url(url: str, limit: int = 360) -> str:
    text = str(url or "")
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


_STRIPE_CONFIRM_BODY_CAPTURE_LIMIT = 120_000
_STRIPE_CONFIRM_REQUEST_CAPTURE_LIMIT = 12_000


def _get_stripe_network_events(page) -> list[dict[str, Any]]:
    try:
        events = getattr(page, "_flow2_stripe_network_events", None)
        if isinstance(events, list):
            return list(events)
    except Exception:
        pass
    return []


def _append_stripe_network_event(page, item: dict[str, Any]) -> None:
    try:
        events = getattr(page, "_flow2_stripe_network_events", None)
        if not isinstance(events, list):
            events = []
            setattr(page, "_flow2_stripe_network_events", events)
        events.append(item)
        if len(events) > 1200:
            important = [
                event for event in events
                if "/poll?" not in str(event.get("url") or "").lower()
            ][-400:]
            recent = events[-700:]
            merged: list[dict[str, Any]] = []
            seen: set[tuple[str, str, str, str]] = set()
            for event in [*important, *recent]:
                key = (
                    str(event.get("kind") or ""),
                    str(event.get("method") or ""),
                    str(event.get("status") or event.get("error") or ""),
                    str(event.get("url") or ""),
                )
                if key in seen:
                    continue
                seen.add(key)
                merged.append(event)
            events[:] = merged[-1000:]
    except Exception:
        pass


def _attach_stripe_network_probe(page) -> None:
    """提交 Stripe 前挂网络探针，失败现场可见真实请求状态。"""
    try:
        if getattr(page, "_flow2_stripe_network_probe_attached", False):
            return
        setattr(page, "_flow2_stripe_network_probe_attached", True)
        setattr(page, "_flow2_stripe_network_events", [])
    except Exception:
        return

    def on_request(request) -> None:
        try:
            url = str(getattr(request, "url", "") or "")
            if not _stripe_probe_url(url):
                return
            body = ""
            body_length = 0
            body_truncated = False
            if "/confirm" in url.lower():
                try:
                    raw_body = getattr(request, "post_data", "")
                    if callable(raw_body):
                        raw_body = raw_body()
                    body = str(raw_body or "")
                    body_length = len(body)
                    body_truncated = body_length > _STRIPE_CONFIRM_REQUEST_CAPTURE_LIMIT
                    body = body[:_STRIPE_CONFIRM_REQUEST_CAPTURE_LIMIT]
                except Exception as exc:
                    body = f"<post_data_error:{exc}>"
            _append_stripe_network_event(
                page,
                {
                    "kind": "request",
                    "method": str(getattr(request, "method", "") or ""),
                    "resource": str(getattr(request, "resource_type", "") or ""),
                    "url": _stripe_short_url(url),
                    "body": body,
                    "body_length": body_length,
                    "body_truncated": body_truncated,
                    "ts": round(time.time(), 3),
                },
            )
        except Exception:
            pass

    def on_response(response) -> None:
        try:
            url = str(getattr(response, "url", "") or "")
            if not _stripe_probe_url(url):
                return
            request = getattr(response, "request", None)
            _append_stripe_network_event(
                page,
                {
                    "kind": "response",
                    "status": int(getattr(response, "status", 0) or 0),
                    "ok": bool(getattr(response, "ok", False)),
                    "method": str(getattr(request, "method", "") or ""),
                    "resource": str(getattr(request, "resource_type", "") or ""),
                    "url": _stripe_short_url(url),
                    "ts": round(time.time(), 3),
                },
            )
            if "/confirm" in url.lower():
                try:
                    loop = asyncio.get_running_loop()

                    async def capture_confirm_body() -> None:
                        try:
                            body = await response.text()
                            body_text = str(body or "")
                            _append_stripe_network_event(
                                page,
                                {
                                    "kind": "response_body",
                                    "status": int(getattr(response, "status", 0) or 0),
                                    "method": str(getattr(request, "method", "") or ""),
                                    "url": _stripe_short_url(url),
                                    "body": body_text[:_STRIPE_CONFIRM_BODY_CAPTURE_LIMIT],
                                    "body_length": len(body_text),
                                    "body_truncated": len(body_text) > _STRIPE_CONFIRM_BODY_CAPTURE_LIMIT,
                                    "ts": round(time.time(), 3),
                                },
                            )
                        except Exception as exc:
                            _append_stripe_network_event(
                                page,
                                {
                                    "kind": "response_body_error",
                                    "status": int(getattr(response, "status", 0) or 0),
                                    "method": str(getattr(request, "method", "") or ""),
                                    "url": _stripe_short_url(url),
                                    "error": str(exc)[:500],
                                    "ts": round(time.time(), 3),
                                },
                            )

                    loop.create_task(capture_confirm_body())
                except Exception:
                    pass
        except Exception:
            pass

    def on_request_failed(request) -> None:
        try:
            url = str(getattr(request, "url", "") or "")
            if not _stripe_probe_url(url):
                return
            failure = getattr(request, "failure", None)
            _append_stripe_network_event(
                page,
                {
                    "kind": "requestfailed",
                    "method": str(getattr(request, "method", "") or ""),
                    "resource": str(getattr(request, "resource_type", "") or ""),
                    "error": str(failure or ""),
                    "url": _stripe_short_url(url),
                    "ts": round(time.time(), 3),
                },
            )
        except Exception:
            pass

    try:
        page.on("request", on_request)
        page.on("response", on_response)
        page.on("requestfailed", on_request_failed)
    except Exception:
        pass


def _summarize_stripe_network_events(page) -> str:
    events = _get_stripe_network_events(page)
    if not events:
        return "none"
    parts: list[str] = []
    for item in events[-10:]:
        kind = str(item.get("kind") or "")
        status = str(item.get("status") or item.get("error") or "")
        method = str(item.get("method") or "")
        url = str(item.get("url") or "")
        parts.append(f"{kind}:{status}:{method}:{url[:120]}")
    return " || ".join(parts)


def _walk_json_values(value: Any) -> list[Any]:
    stack: list[Any] = [value]
    values: list[Any] = []
    while stack:
        current = stack.pop()
        values.append(current)
        if isinstance(current, dict):
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    return values


def _is_paypal_redirect_url(value: str) -> bool:
    """只接受真实 PayPal 域名跳转，避免把 Stripe 的 PayPal 图标资源当成跳转地址。"""
    cleaned = str(value or "").replace("\\/", "/").strip()
    match = re.match(r"^https://([^/?#]+)([^?#]*)", cleaned, re.I)
    if not match:
        return False
    host = match.group(1).split("@")[-1].split(":", 1)[0].lower()
    path = match.group(2) or ""
    if host != "paypal.com" and not host.endswith(".paypal.com"):
        return False
    if re.search(r"\.(?:png|jpe?g|gif|svg|webp|ico)(?:$|[?#])", path, re.I):
        return False
    return True


def _first_paypal_redirect_in_text(text: str) -> str:
    for match in re.finditer(r"https://[^\"'\\\s<>]+paypal[^\"'\\\s<>]+", str(text or ""), re.I):
        candidate = match.group(0).replace("\\/", "/")
        if _is_paypal_redirect_url(candidate):
            return candidate
    return ""


def _find_paypal_redirect_in_value(value: Any) -> str:
    for current in _walk_json_values(value):
        if isinstance(current, str):
            cleaned = current.replace("\\/", "/")
            if _is_paypal_redirect_url(cleaned):
                return cleaned
    return ""


def _stripe_confirm_response_snapshot(page) -> dict[str, Any]:
    """提取最近一次 Stripe confirm 响应摘要，失败现场可快速判断是否有 PayPal action。"""
    for item in reversed(_get_stripe_network_events(page)):
        if item.get("kind") != "response_body":
            continue
        body = str(item.get("body") or "")
        snapshot: dict[str, Any] = {
            "http_status": item.get("status"),
            "method": item.get("method"),
            "url": item.get("url"),
            "body_length": item.get("body_length", len(body)),
            "body_truncated": bool(item.get("body_truncated")),
            "paypal_url": "",
            "parse_ok": False,
        }
        if not body:
            return snapshot
        text = body.replace("\\/", "/")
        direct = _first_paypal_redirect_in_text(text)
        if direct:
            snapshot["paypal_url"] = direct
        try:
            data = json.loads(body)
        except Exception as exc:
            snapshot["parse_error"] = str(exc)[:220]
            return snapshot
        snapshot["parse_ok"] = True
        if isinstance(data, dict):
            elements_options = data.get("elements_options") if isinstance(data.get("elements_options"), dict) else {}
            for key in (
                "id",
                "object",
                "mode",
                "status",
                "payment_status",
                "currency",
                "amount_total",
                "payment_intent",
                "setup_intent",
                "subscription",
                "success_url",
                "cancel_url",
                "url",
                "approval_method",
            ):
                if key in data:
                    snapshot[key] = data.get(key)
            snapshot["payment_method_types"] = data.get("payment_method_types") or elements_options.get("payment_method_types")
            if not snapshot.get("paypal_url"):
                snapshot["paypal_url"] = _find_paypal_redirect_in_value(data)
        return snapshot
    return {}


def _summarize_stripe_confirm_response(page) -> str:
    snapshot = _stripe_confirm_response_snapshot(page)
    if not snapshot:
        return "none"
    parts = [
        f"http={snapshot.get('http_status')}",
        f"len={snapshot.get('body_length')}",
        f"trunc={snapshot.get('body_truncated')}",
        f"parse={snapshot.get('parse_ok')}",
    ]
    for key in ("object", "mode", "status", "payment_status", "amount_total", "payment_method_types"):
        value = snapshot.get(key)
        if value not in (None, "", []):
            parts.append(f"{key}={value}")
    if snapshot.get("paypal_url"):
        parts.append("paypal_url=yes")
    if snapshot.get("parse_error"):
        parts.append(f"parse_error={snapshot.get('parse_error')}")
    return "; ".join(str(item) for item in parts)


def _extract_stripe_confirm_redirect_url(page) -> str:
    """从 Stripe confirm 响应体里提取 PayPal 跳转地址，前端卡住时手动接管跳转。"""
    for item in reversed(_get_stripe_network_events(page)):
        if item.get("kind") != "response_body":
            continue
        body = str(item.get("body") or "")
        if not body:
            continue
        text = body.replace("\\/", "/")
        direct = _first_paypal_redirect_in_text(text)
        if direct:
            return direct
        try:
            data = json.loads(body)
        except Exception:
            data = None
        redirect = _find_paypal_redirect_in_value(data) if isinstance(data, (dict, list)) else ""
        if redirect:
            return redirect
    return ""


async def _accept_stripe_address_suggestion(
    page,
    card: CardInfo,
    city_value: str,
    zip_value: str,
    prefix: str = "[Stripe]",
) -> bool:
    """选择 Stripe/Google 地址建议中匹配账单城市的首条，避免未提交规范化地址导致 PayPal 跳转卡死。"""
    payload = {
        "street": (card.street or "").strip(),
        "city": (city_value or card.city or "").strip(),
        "state": (card.state or "").strip(),
        "zip": (zip_value or card.zip_code or "").strip(),
    }
    for _ in range(5):
        try:
            point = await page.evaluate(
                r"""(payload) => {
                const visible = (el) => {
                    if (!el) return false;
                    const rect = el.getBoundingClientRect();
                    const style = getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const textOf = (el) => String(el?.innerText || el?.textContent || el?.value || el?.getAttribute?.('aria-label') || '').replace(/\s+/g, ' ').trim();
                const compact = (s) => String(s || '').toLowerCase().replace(/[^a-z0-9]+/g, '');
                const street = compact(payload?.street || '');
                const city = compact(payload?.city || '');
                const state = compact(payload?.state || '');
                const zip = compact(payload?.zip || '');
                const stateNames = {
                    al: 'alabama', ak: 'alaska', az: 'arizona', ar: 'arkansas', ca: 'california',
                    co: 'colorado', ct: 'connecticut', de: 'delaware', dc: 'districtofcolumbia',
                    fl: 'florida', ga: 'georgia', hi: 'hawaii', id: 'idaho', il: 'illinois',
                    in: 'indiana', ia: 'iowa', ks: 'kansas', ky: 'kentucky', la: 'louisiana',
                    me: 'maine', md: 'maryland', ma: 'massachusetts', mi: 'michigan', mn: 'minnesota',
                    ms: 'mississippi', mo: 'missouri', mt: 'montana', ne: 'nebraska', nv: 'nevada',
                    nh: 'newhampshire', nj: 'newjersey', nm: 'newmexico', ny: 'newyork',
                    nc: 'northcarolina', nd: 'northdakota', oh: 'ohio', ok: 'oklahoma',
                    or: 'oregon', pa: 'pennsylvania', ri: 'rhodeisland', sc: 'southcarolina',
                    sd: 'southdakota', tn: 'tennessee', tx: 'texas', ut: 'utah', vt: 'vermont',
                    va: 'virginia', wa: 'washington', wv: 'westvirginia', wi: 'wisconsin', wy: 'wyoming'
                };
                const stateFull = stateNames[state] || state;
                const streetNumber = String(payload?.street || '').match(/\d+/)?.[0] || '';
                const streetWord = compact(String(payload?.street || '').replace(/^\d+\s*/, '').split(/\s+/).slice(0, 2).join(' '));
                const bodyText = textOf(document.body);
                if (!/results available|建议|suggest|google/i.test(bodyText)) {
                    return { ok: false, reason: 'no-suggestion-overlay', label: '' };
                }
                const matchesAddress = (text) => {
                    const key = compact(text);
                    if (!key) return false;
                    const streetHit = (
                        (street && key.includes(street)) ||
                        (streetNumber && streetWord && key.includes(streetNumber) && key.includes(streetWord))
                    );
                    const localityHit = (
                        (city && key.includes(city)) ||
                        (zip && key.includes(zip)) ||
                        (state && key.includes(state)) ||
                        (stateFull && key.includes(stateFull))
                    );
                    return streetHit && localityHit;
                };
                const nodes = Array.from(document.querySelectorAll('[role="option"], li, button, div, span'))
                    .filter(visible)
                    .map((el) => {
                        const rect = el.getBoundingClientRect();
                        const text = textOf(el);
                        return { el, rect, text };
                    })
                    .filter((item) => {
                        if (!item.text || item.text.length > 220) return false;
                        if (item.rect.width > window.innerWidth * 0.65 || item.rect.height > window.innerHeight * 0.22) return false;
                        return matchesAddress(item.text);
                    })
                    .sort((a, b) => {
                        const aExact = compact(a.text).includes(street) && city && compact(a.text).includes(city) ? 1 : 0;
                        const bExact = compact(b.text).includes(street) && city && compact(b.text).includes(city) ? 1 : 0;
                        const aArea = a.rect.width * a.rect.height;
                        const bArea = b.rect.width * b.rect.height;
                        return (bExact - aExact) || (aArea - bArea) || (a.text.length - b.text.length);
                    });
                let targetItem = nodes[0] || null;
                if (!targetItem) {
                    const overlays = Array.from(document.querySelectorAll('div, ul, [role="listbox"]'))
                        .filter(visible)
                        .map((el) => ({ el, rect: el.getBoundingClientRect(), text: textOf(el) }))
                        .filter((item) => /results available|建议|suggest/i.test(item.text) && matchesAddress(item.text))
                        .sort((a, b) => (a.rect.width * a.rect.height) - (b.rect.width * b.rect.height));
                    const overlay = overlays[0];
                    if (!overlay) return { ok: false, reason: 'candidate-not-found', label: '' };
                    return {
                        ok: true,
                        mode: 'overlay-fallback',
                        label: overlay.text.slice(0, 180),
                        x: Math.round(overlay.rect.left + Math.max(18, Math.min(overlay.rect.width - 18, overlay.rect.width * 0.35))),
                        y: Math.round(overlay.rect.top + Math.max(48, Math.min(overlay.rect.height - 12, 58)))
                    };
                }
                const rect = targetItem.rect;
                return {
                    ok: true,
                    mode: 'node',
                    label: targetItem.text.slice(0, 180),
                    x: Math.round(rect.left + Math.max(8, Math.min(rect.width - 8, rect.width / 2))),
                    y: Math.round(rect.top + Math.max(8, Math.min(rect.height - 8, rect.height / 2)))
                };
            }""",
                payload,
            )
            if isinstance(point, dict) and point.get("ok"):
                await page.mouse.click(float(point.get("x") or 0), float(point.get("y") or 0))
                await page.wait_for_timeout(900)
                log(
                    f"{prefix} accepted address suggestion: "
                    f"mode={point.get('mode', '')} label={point.get('label', '')}"
                )
                return True
        except Exception as exc:
            log(f"{prefix} address suggestion accept skipped: {exc}")
            return False
        await page.wait_for_timeout(450)
    return False


async def _dismiss_stripe_address_suggestions(page, prefix: str = "[Stripe]") -> None:
    """Stripe 地址输入后可能出现 Google 建议浮层；提交前强制失焦并关闭，避免挡住 PayPal 跳转。"""
    try:
        for selector in (
            "button.AddressAutocomplete--clear-dropdown-button",
            "[class*='AddressAutocomplete--clear-dropdown-button']",
            "#billing-address-autocomplete-results button",
        ):
            try:
                button = page.locator(selector).first
                if await button.is_visible(timeout=500):
                    await button.click(timeout=1000, force=True)
                    await page.wait_for_timeout(250)
                    break
            except Exception:
                continue
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(250)
        await page.evaluate(
            r"""() => {
                if (document.activeElement && document.activeElement.blur) {
                    document.activeElement.blur();
                }
                const visible = (el) => {
                    if (!el) return false;
                    const rect = el.getBoundingClientRect();
                    const style = getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const textOf = (el) => String(el?.innerText || el?.textContent || el?.getAttribute?.('aria-label') || '').replace(/\s+/g, ' ').trim();
                const overlays = Array.from(document.querySelectorAll('div, ul, li, [role="listbox"], [role="option"]'))
                    .filter(visible)
                    .filter((el) => /results available|建议|suggest/i.test(textOf(el)) && textOf(el).length < 1200);
                for (const overlay of overlays) {
                    const close = Array.from(overlay.querySelectorAll('button, [role="button"], svg, span, div'))
                        .filter(visible)
                        .find((el) => /^(×|x|close|关闭|關閉)$/i.test(textOf(el)) || /close|关闭|關閉/i.test(el.getAttribute?.('aria-label') || ''));
                    if (close) {
                        try { close.click(); return; } catch {}
                    }
                }
                const input = document.querySelector('#billingAddressLine1');
                if (input) {
                    input.setAttribute('aria-expanded', 'false');
                    input.removeAttribute('aria-activedescendant');
                    try { input.blur(); } catch {}
                }
                for (const sel of [
                    '#billing-address-autocomplete-results',
                    '.AddressAutocomplete-results',
                    '.AutocompleteInput-dropdown-container',
                    '.AddressAutocomplete-suggestions-container'
                ]) {
                    for (const node of document.querySelectorAll(sel)) {
                        node.setAttribute('aria-hidden', 'true');
                        node.style.display = 'none';
                        node.style.visibility = 'hidden';
                        node.style.opacity = '0';
                        node.style.pointerEvents = 'none';
                    }
                }
            }"""
        )
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(250)
        await page.locator("body").click(position={"x": 8, "y": 8}, force=True)
        await page.wait_for_timeout(350)
    except Exception:
        pass

    try:
        snapshot = await page.evaluate(
            r"""() => {
                const visible = (el) => {
                    if (!el) return false;
                    const rect = el.getBoundingClientRect();
                    const style = getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const textOf = (el) => String(el?.innerText || el?.textContent || '').replace(/\s+/g, ' ').trim();
                const nodes = Array.from(document.querySelectorAll('[role="listbox"], [role="option"], [aria-label*="suggest" i], [id*="autocomplete" i], [class*="autocomplete" i], [class*="pac-container" i], div, ul, li'))
                    .filter(visible)
                    .filter((el) => {
                        const text = textOf(el);
                        return text && text.length <= 800 && /suggest|results available|建议|候補|候选|address/i.test(text);
                    });
                return {
                    count: nodes.length,
                    labels: nodes.slice(0, 3).map((el) => textOf(el).slice(0, 180))
                };
            }"""
        )
        if isinstance(snapshot, dict) and int(snapshot.get("count") or 0) > 0:
            log(f"{prefix} address suggestion overlay still visible: {snapshot.get('labels', [])}")
    except Exception:
        pass


async def _save_stripe_failure_debug(page, email: str, reason: str) -> Path | None:
    """Stripe 失败时落 HTML/截图/表单快照，便于复盘真实页面状态。"""
    try:
        out_dir = PAYPAL_OUTPUT_ROOT / "debug" / "stripe_failure" / f"{safe_filename(email)}_{int(time.time())}"
        out_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        log(f"[Stripe] debug dir create failed: {exc}")
        return None

    try:
        (out_dir / "page.html").write_text(await page.content(), encoding="utf-8")
    except Exception as exc:
        log(f"[Stripe] debug html save failed: {exc}")
    try:
        body_text = await page.evaluate("() => document.body?.innerText || ''")
        (out_dir / "body.txt").write_text(str(body_text or ""), encoding="utf-8")
    except Exception as exc:
        log(f"[Stripe] debug body save failed: {exc}")
    try:
        state = await page.evaluate(
            r"""(reason) => {
                const visible = (el) => {
                    if (!el) return false;
                    const rect = el.getBoundingClientRect();
                    const style = getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const textOf = (el) => String(el?.innerText || el?.textContent || el?.value || el?.getAttribute?.('aria-label') || '').replace(/\s+/g, ' ').trim();
                const fieldOf = (el) => ({
                    tag: el.tagName,
                    id: el.id || '',
                    name: el.getAttribute('name') || '',
                    type: el.getAttribute('type') || '',
                    aria: el.getAttribute('aria-label') || '',
                    value: String(el.value || '').slice(0, 180),
                    text: textOf(el).slice(0, 180),
                    checked: ('checked' in el) ? !!el.checked : String(el.getAttribute('aria-checked') || ''),
                    invalid: el.getAttribute('aria-invalid') || '',
                    testid: el.getAttribute('data-testid') || ''
                });
                const errorNodes = Array.from(document.querySelectorAll('[role="alert"], .Error, .ErrorMessage, [class*="error" i], [aria-invalid="true"]'))
                    .filter(visible)
                    .map((el) => textOf(el).slice(0, 240))
                    .filter(Boolean);
                return {
                    reason,
                    url: location.href,
                    title: document.title || '',
                    paymentMethods: Array.from(document.querySelectorAll('[data-testid*="accordion-item-button"], button, [role="button"], label'))
                        .filter(visible)
                        .map(fieldOf)
                        .filter((x) => /paypal|card|link|支付|付款|銀行|银行卡/i.test([x.text, x.aria, x.testid].join(' ')))
                        .slice(0, 20),
                    checkboxes: Array.from(document.querySelectorAll('input[type="checkbox"], [role="checkbox"]')).filter(visible).map(fieldOf).slice(0, 20),
                    selects: Array.from(document.querySelectorAll('select')).filter(visible).map(fieldOf).slice(0, 20),
                    inputs: Array.from(document.querySelectorAll('input, textarea')).filter(visible).map(fieldOf).slice(0, 40),
                    errors: errorNodes.slice(0, 20),
                    submitButtons: Array.from(document.querySelectorAll('button, input[type="submit"], [data-testid="hosted-payment-submit-button"]'))
                        .filter(visible)
                        .map(fieldOf)
                        .filter((x) => /subscribe|processing|submit|订阅|購読|正在处理|処理/i.test([x.text, x.aria, x.testid, x.type].join(' ')))
                        .slice(0, 10),
                    iframes: Array.from(document.querySelectorAll('iframe'))
                        .map((el) => ({
                            name: el.getAttribute('name') || '',
                            title: el.getAttribute('title') || '',
                            src: String(el.getAttribute('src') || '').slice(0, 260)
                        }))
                        .slice(0, 20),
                    resources: performance.getEntriesByType('resource')
                        .map((entry) => ({
                            name: String(entry.name || '').slice(0, 260),
                            type: entry.initiatorType || '',
                            duration: Math.round(entry.duration || 0),
                            transferSize: entry.transferSize || 0
                        }))
                        .filter((entry) => /stripe|paypal|openai|confirm|payment|checkout|hcaptcha|human-security/i.test(entry.name))
                        .slice(-80),
                    bodyHead: textOf(document.body).slice(0, 2000)
                };
            }""",
            reason,
        )
        if isinstance(state, dict):
            state["networkEvents"] = _get_stripe_network_events(page)
            state["confirmSummary"] = _summarize_stripe_confirm_response(page)
            state["confirmResponse"] = _stripe_confirm_response_snapshot(page)
            frame_states = []
            for frame in page.frames:
                if frame is page.main_frame:
                    continue
                url = str(getattr(frame, "url", "") or "")
                if "stripe" not in url and "private" not in url and "payment" not in url and "address" not in url:
                    continue
                try:
                    frame_states.append(
                        await frame.evaluate(
                            r"""() => {
                                const visible = (el) => {
                                    if (!el) return false;
                                    const rect = el.getBoundingClientRect();
                                    const style = getComputedStyle(el);
                                    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                                };
                                const textOf = (el) => String(el?.innerText || el?.textContent || el?.value || el?.getAttribute?.('aria-label') || '').replace(/\s+/g, ' ').trim();
                                const fieldOf = (el) => ({
                                    tag: el.tagName,
                                    id: el.id || '',
                                    name: el.getAttribute('name') || '',
                                    type: el.getAttribute('type') || '',
                                    aria: el.getAttribute('aria-label') || '',
                                    autocomplete: el.getAttribute('autocomplete') || '',
                                    placeholder: el.getAttribute('placeholder') || '',
                                    value: String(el.value || '').slice(0, 180),
                                    text: textOf(el).slice(0, 180),
                                    invalid: el.getAttribute('aria-invalid') || '',
                                    checked: ('checked' in el) ? !!el.checked : String(el.getAttribute('aria-checked') || ''),
                                });
                                return {
                                    url: location.href,
                                    title: document.title || '',
                                    body: textOf(document.body).slice(0, 500),
                                    selects: Array.from(document.querySelectorAll('select')).filter(visible).map(fieldOf).slice(0, 30),
                                    inputs: Array.from(document.querySelectorAll('input, textarea')).filter(visible).map(fieldOf).slice(0, 50),
                                    errors: Array.from(document.querySelectorAll('[role="alert"], [aria-invalid="true"], [class*="error" i]')).filter(visible).map((el) => textOf(el).slice(0, 240)).filter(Boolean).slice(0, 20),
                                };
                            }"""
                        )
                    )
                except Exception as exc:
                    frame_states.append({"url": url[:260], "error": str(exc)[:200]})
            state["frameStates"] = frame_states
        (out_dir / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            (out_dir / "network.json").write_text(
                json.dumps(_get_stripe_network_events(page), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass
    except Exception as exc:
        log(f"[Stripe] debug state save failed: {exc}")
    try:
        await page.screenshot(path=str(out_dir / "screenshot.png"), full_page=True)
    except Exception as exc:
        log(f"[Stripe] debug screenshot save failed: {exc}")
    log(f"[Stripe] failure debug saved: {out_dir}")
    return out_dir


async def fill_stripe(
    page,
    email: str,
    card: CardInfo,
    *,
    country_code: str = "US",
    recreate_on_missing_paypal: bool = True,
):
    """Stripe 页面：选 PayPal + 填地址 + Subscribe。"""
    stripe_started_at = time.perf_counter()
    _attach_stripe_network_probe(page)
    await page.wait_for_load_state("domcontentloaded", timeout=30000)
    try:
        await page.locator(
            '[data-testid="paypal-accordion-item-button"], #billingCountry, '
            'select[name*="country" i], select[autocomplete="country"]'
        ).first.wait_for(state="visible", timeout=5000)
    except Exception:
        await page.wait_for_timeout(1200)

    # 防御性修正：部分卡源会把 "CITY ZIP" 合并到 city 字段
    city_value = (card.city or "").strip()
    zip_value = (card.zip_code or "").strip()
    m_city_zip = re.search(r"^(?P<city>.*)\s+(?P<zip>\d{5}(?:-\d{4})?)$", city_value)
    if m_city_zip:
        city_value = (m_city_zip.group("city") or "").strip()
        if not zip_value:
            zip_value = (m_city_zip.group("zip") or "").strip()

    await _ensure_stripe_paypal_selected(page)

    desired_country = "JP" if str(country_code or "").upper() == "JP" else "US"
    desired_labels = ["Japan", "日本"] if desired_country == "JP" else ["United States", "美国"]
    if desired_country == "US":
        stable_street, stable_city, stable_state, stable_zip = _pick_stripe_stable_us_billing_profile(
            f"{email}:{card.raw_line}:{card.street}:{card.city}:{card.zip_code}"
        )
        if (
            stable_street != (card.street or "").strip()
            or stable_city != (card.city or "").strip()
            or stable_state != (card.state or "").strip()
            or stable_zip != (card.zip_code or "").strip()
        ):
            log(
                f"[Stripe] using stable US billing address: "
                f"city={stable_city}, state={stable_state}, zip={stable_zip}"
            )
        # Stripe 的 Google 地址建议对随机街道很敏感；此处同步 card，后续 PayPal 地址也保持一致。
        card.street = stable_street
        card.city = stable_city
        card.state = stable_state
        card.zip_code = stable_zip
        city_value = stable_city
        zip_value = stable_zip
    manual_address_jp = _zh(r"\u4f4f\u6240\u3092\u624b\u52d5\u3067\u5165\u529b")
    subscribe_jp = _zh(r"\u8cfc\u8aad")
    subscribe_jp_alt = _zh(r"\u30b5\u30d6\u30b9\u30af\u30e9\u30a4\u30d6")
    apply_jp = _zh(r"\u7533\u3057\u8fbc\u3080")
    continue_jp = _zh(r"\u7d9a\u884c")

    async def _stripe_address_targets() -> list[Any]:
        targets: list[Any] = [page]
        for frame in page.frames:
            if frame is page.main_frame:
                continue
            url = str(getattr(frame, "url", "") or "").lower()
            title = ""
            try:
                frame_el = await frame.frame_element()
                title = str(await frame_el.get_attribute("title") or "").lower()
            except Exception:
                pass
            if (
                "elements-inner-address" in url
                or "componentname=address" in url
                or "安全地址输入框" in title
                or "address" in title
            ):
                targets.append(frame)
        return targets

    async def _select_first_visible(
        target,
        selector: str,
        value: str,
        labels: list[str] | None = None,
        timeout: int = 1800,
    ) -> bool:
        for loc_selector in selector.split(","):
            loc_selector = loc_selector.strip()
            if not loc_selector:
                continue
            try:
                loc = target.locator(loc_selector).first
                if not await loc.is_visible(timeout=500):
                    continue
                try:
                    await loc.select_option(value, timeout=timeout)
                    return True
                except Exception:
                    pass
                for label in labels or []:
                    try:
                        await loc.select_option(label=label, timeout=timeout)
                        return True
                    except Exception:
                        continue
            except Exception:
                continue
        return False

    # 国家选择 - 先等待下拉框可交互
    country_select = page.locator('#billingCountry, select[name*="country" i], select[autocomplete="country"]').first
    country_started_at = time.perf_counter()
    country_changed = False
    try:
        await country_select.wait_for(state="visible", timeout=3500)
        await country_select.select_option(desired_country, timeout=2500)
        country_changed = True
    except Exception:
        for lbl in desired_labels:
            try:
                await country_select.select_option(label=lbl, timeout=1500)
                country_changed = True
                break
            except Exception:
                continue
    if not country_changed:
        for target in await _stripe_address_targets():
            if await _select_first_visible(
                target,
                '#billingCountry, select[name*="country" i], select[autocomplete="country"], select[autocomplete="country-name"]',
                desired_country,
                desired_labels,
            ):
                country_changed = True
                break

    # 等待国家切换后页面重新渲染地址字段
    await page.wait_for_timeout(1200 if country_changed else 500)

    # 验证国家是否选中目标国家
    try:
        current_val = await country_select.input_value()
        if current_val != desired_country:
            log(f"[Stripe] 国家仍为 {current_val}，再次尝试切到 {desired_country}...")
            await country_select.select_option(desired_country, timeout=1800)
            await page.wait_for_timeout(900)
        else:
            log(f"[Stripe] billing country confirmed: {current_val}")
    except Exception:
        pass
    country_finished_at = time.perf_counter()

    await _ensure_stripe_paypal_selected(page)

    # 手动输入地址
    try:
        manual = page.locator(
            f'text=手动输入地址, text=Enter address manually, text="{manual_address_jp}", '
            f'a:has-text("手动"), a:has-text("manually"), a:has-text("{manual_address_jp}"), '
            f'button:has-text("{manual_address_jp}")'
        ).first
        if await manual.is_visible(timeout=1200):
            await manual.click(timeout=1500)
            await page.wait_for_timeout(600)
    except Exception:
        pass

    async def _safe_fill(selector: str, value: str, timeout: int = 2500) -> bool:
        if not str(value or "").strip():
            return False
        val = str(value).strip()
        for target in await _stripe_address_targets():
            try:
                loc = target.locator(f"{selector}:visible").first
                if await loc.is_visible(timeout=500):
                    await loc.fill("", timeout=1000)
                    await loc.fill(val, timeout=timeout)
                    read_back = (await loc.input_value()).strip()
                    if read_back:
                        return True
            except Exception:
                pass
            try:
                ok = await target.evaluate(
                    """(selector, value) => {
                        const isVisible = (el) => {
                            if (!el) return false;
                            const r = el.getBoundingClientRect();
                            if (r.width < 6 || r.height < 6) return false;
                            const st = window.getComputedStyle(el);
                            if (!st) return false;
                            if (st.display === 'none' || st.visibility === 'hidden') return false;
                            if (Number(st.opacity || '1') < 0.05) return false;
                            return !el.disabled && !el.readOnly;
                        };
                        const nodes = Array.from(document.querySelectorAll(selector)).filter(isVisible);
                        if (!nodes.length) return false;
                        const input = nodes[0];
                        const proto = input.tagName.toLowerCase() === 'textarea'
                            ? HTMLTextAreaElement.prototype
                            : HTMLInputElement.prototype;
                        const desc = Object.getOwnPropertyDescriptor(proto, 'value');
                        if (desc && typeof desc.set === 'function') desc.set.call(input, value);
                        else input.value = value;
                        input.focus();
                        input.dispatchEvent(new Event('input', { bubbles: true }));
                        input.dispatchEvent(new Event('change', { bubbles: true }));
                        input.dispatchEvent(new Event('blur', { bubbles: true }));
                        return String(input.value || '').trim().length > 0;
                    }""",
                    selector,
                    val,
                )
                if ok:
                    return True
            except Exception:
                pass

        # 先尝试可见输入框
        try:
            loc = page.locator(f"{selector}:visible").first
            if await loc.is_visible(timeout=600):
                await loc.fill("", timeout=1000)
                await loc.fill(val, timeout=timeout)
                read_back = (await loc.input_value()).strip()
                if read_back:
                    return True
        except Exception:
            pass

        # 兜底：遍历可见节点，使用原生 setter 写入并触发事件链
        try:
            ok = await page.evaluate(
                """(selector, value) => {
                    const isVisible = (el) => {
                        if (!el) return false;
                        const r = el.getBoundingClientRect();
                        if (r.width < 6 || r.height < 6) return false;
                        const st = window.getComputedStyle(el);
                        if (!st) return false;
                        if (st.display === 'none' || st.visibility === 'hidden') return false;
                        if (Number(st.opacity || '1') < 0.05) return false;
                        return !el.disabled && !el.readOnly;
                    };
                    const nodes = Array.from(document.querySelectorAll(selector)).filter(isVisible);
                    if (!nodes.length) return false;
                    const input = nodes[0];
                    const proto = input.tagName.toLowerCase() === 'textarea'
                        ? HTMLTextAreaElement.prototype
                        : HTMLInputElement.prototype;
                    const desc = Object.getOwnPropertyDescriptor(proto, 'value');
                    if (desc && typeof desc.set === 'function') desc.set.call(input, value);
                    else input.value = value;
                    input.dispatchEvent(new Event('input', { bubbles: true }));
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                    input.dispatchEvent(new Event('blur', { bubbles: true }));
                    return String(input.value || '').trim().length > 0;
                }""",
                selector,
                val,
            )
            return bool(ok)
        except Exception:
            return False

    async def _fill_stripe_address_element() -> dict[str, bool]:
        """Stripe 地址 Element 常在 iframe 内，且字段名随版本变化；按语义一次性补齐。"""
        state_code = str(card.state or "").strip().upper()
        state_full = _US_STATE_NAMES.get(state_code, str(card.state or "").strip())
        payload = {
            "name": card.holder_name or f"{card.first_name} {card.last_name}".strip() or email,
            "street": card.street,
            "city": city_value,
            "state": state_code or card.state,
            "stateFull": state_full,
            "stateLabels": [item for item in (state_code, state_full, card.state) if str(item or "").strip()],
            "zip": zip_value,
            "country": desired_country,
            "countryLabels": desired_labels,
        }
        result: dict[str, bool] = {}

        async def _verify_select_committed(
            loc,
            *,
            values: tuple[str, ...],
            labels: tuple[str, ...],
        ) -> bool:
            try:
                info = await loc.evaluate(
                    r"""(el, payload) => {
                        const opt = el && el.options ? el.options[el.selectedIndex] : null;
                        return {
                            value: String(el?.value || '').trim(),
                            text: String((opt && (opt.text || opt.label)) || el?.innerText || el?.textContent || '').trim(),
                            invalid: String(el?.getAttribute('aria-invalid') || '').toLowerCase(),
                        };
                    }""",
                    {
                        "values": [str(item or "") for item in values],
                        "labels": [str(item or "") for item in labels],
                    },
                )
            except Exception:
                return False
            value = str((info or {}).get("value") or "").strip()
            text = str((info or {}).get("text") or "").strip()
            invalid = str((info or {}).get("invalid") or "").lower() == "true"
            if invalid or not value:
                return False
            value_keys = {str(item or "").strip().lower() for item in values if str(item or "").strip()}
            label_keys = {str(item or "").strip().lower() for item in labels if str(item or "").strip()}
            value_lower = value.lower()
            text_lower = text.lower()
            return value_lower in value_keys or any(key and key in text_lower for key in label_keys)

        async def _native_select_stripe_option(
            selectors: tuple[str, ...],
            *,
            values: tuple[str, ...],
            labels: tuple[str, ...],
            field_name: str,
        ) -> bool:
            """优先用 Playwright 真实 select 操作；避免 JS 强设 value 后 React 状态未提交。"""
            for target in await _stripe_address_targets():
                for selector in selectors:
                    try:
                        loc = target.locator(selector).first
                        if not await loc.is_visible(timeout=550):
                            continue
                        with contextlib.suppress(Exception):
                            await loc.scroll_into_view_if_needed(timeout=800)
                        for value in [item for item in values if str(item or "").strip()]:
                            try:
                                await loc.select_option(value=value, timeout=2200)
                                await page.wait_for_timeout(350)
                                if await _verify_select_committed(loc, values=values, labels=labels):
                                    committed_label = (
                                        "billing country dropdown committed"
                                        if field_name == "country"
                                        else "billing state dropdown committed"
                                    )
                                    log(f"[Stripe] {committed_label}: {value}")
                                    return True
                            except Exception:
                                pass
                        for label in [item for item in labels if str(item or "").strip()]:
                            try:
                                await loc.select_option(label=label, timeout=2200)
                                await page.wait_for_timeout(350)
                                if await _verify_select_committed(loc, values=values, labels=labels):
                                    committed_label = (
                                        "billing country dropdown committed"
                                        if field_name == "country"
                                        else "billing state dropdown committed"
                                    )
                                    log(f"[Stripe] {committed_label}: {label}")
                                    return True
                            except Exception:
                                pass
                    except Exception:
                        continue
            return False

        country_committed = await _native_select_stripe_option(
            (
                '#billingAddress-countryInput',
                'select[name="country"]',
                'select[autocomplete*="country" i]',
                'select[id*="country" i]',
            ),
            values=(desired_country,),
            labels=tuple(desired_labels),
            field_name="country",
        )
        result["country"] = bool(result.get("country")) or bool(country_committed)
        if desired_country == "US":
            state_committed = await _native_select_stripe_option(
                (
                    '#billingAddress-administrativeAreaInput',
                    'select[name="administrativeArea"]',
                    'select[autocomplete*="address-level1" i]',
                    'select[id*="administrative" i]',
                    'select[id*="state" i]',
                    'select[name*="state" i]',
                ),
                values=tuple(item for item in (state_code, state_full, card.state) if str(item or "").strip()),
                labels=tuple(item for item in (state_full, state_code, card.state) if str(item or "").strip()),
                field_name="state",
            )
            result["state"] = bool(result.get("state")) or bool(state_committed)
        for target in await _stripe_address_targets():
            try:
                filled = await target.evaluate(
                    r"""(payload) => {
                        const out = {};
                        const visible = (el) => {
                            if (!el) return false;
                            const r = el.getBoundingClientRect();
                            if (r.width < 6 || r.height < 6) return false;
                            const st = getComputedStyle(el);
                            return st.display !== 'none' && st.visibility !== 'hidden' && Number(st.opacity || '1') > 0.05 && !el.disabled && !el.readOnly;
                        };
                        const meta = (el) => [
                            el.id || '',
                            el.name || '',
                            el.autocomplete || '',
                            el.placeholder || '',
                            el.getAttribute('aria-label') || '',
                            el.getAttribute('data-testid') || '',
                            el.labels ? Array.from(el.labels).map(x => x.innerText || x.textContent || '').join(' ') : ''
                        ].join(' ').toLowerCase();
                        const setVal = (el, value) => {
                            if (!el || !String(value || '').trim()) return false;
                            const proto = el.tagName.toLowerCase() === 'textarea' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
                            const desc = Object.getOwnPropertyDescriptor(proto, 'value');
                            if (desc && typeof desc.set === 'function') desc.set.call(el, String(value));
                            else el.value = String(value);
                            try { el.focus(); } catch {}
                            el.dispatchEvent(new Event('input', { bubbles: true }));
                            el.dispatchEvent(new Event('change', { bubbles: true }));
                            el.dispatchEvent(new Event('blur', { bubbles: true }));
                            return String(el.value || '').trim().length > 0;
                        };
                        const clickEl = (el) => {
                            if (!el) return false;
                            try { el.scrollIntoView({block: 'center', inline: 'center'}); } catch {}
                            try { el.click(); } catch {}
                            try {
                                const r = el.getBoundingClientRect();
                                for (const name of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                                    const init = {bubbles: true, cancelable: true, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2, pointerType: 'mouse'};
                                    const ctor = name.startsWith('pointer') && window.PointerEvent ? PointerEvent : MouseEvent;
                                    el.dispatchEvent(new ctor(name, init));
                                }
                            } catch {}
                            return true;
                        };
                        const norm = (s) => String(s || '').toLowerCase().replace(/[\s\-ー—‐－_]/g, '');
                        const stateKeys = (payload.stateLabels || []).map(norm).filter(Boolean);
                        const matchesState = (s) => {
                            const t = norm(s);
                            return stateKeys.some(k => t === k || t.includes(k) || k.includes(t));
                        };
                        const selects = Array.from(document.querySelectorAll('select')).filter(visible);
                        for (const sel of selects) {
                            const m = meta(sel);
                            if (!/country|国家|地区|國家|地域/.test(m) && !(sel.options && sel.options.length > 100)) continue;
                            const opts = Array.from(sel.options || []);
                            const labels = (payload.countryLabels || []).map(x => String(x || '').toLowerCase());
                            const hit = opts.find(o => String(o.value || '').toUpperCase() === String(payload.country || '').toUpperCase())
                                || opts.find(o => labels.some(k => String(o.text || o.label || '').toLowerCase().includes(k)));
                            if (!hit) continue;
                            sel.value = String(hit.value || '');
                            sel.dispatchEvent(new Event('input', { bubbles: true }));
                            sel.dispatchEvent(new Event('change', { bubbles: true }));
                            const selected = sel.options && sel.options[sel.selectedIndex];
                            const selectedText = String((selected && (selected.text || selected.label)) || '');
                            const countryValue = String(sel.value || '').toUpperCase();
                            out.country = countryValue === String(payload.country || '').toUpperCase()
                                || labels.some(k => selectedText.toLowerCase().includes(k));
                            break;
                        }
                        const selectState = () => {
                            for (const sel of selects) {
                                const opts = Array.from(sel.options || []);
                                const hit = opts.find(o => matchesState(o.value) || matchesState(o.text || o.label));
                                if (!hit) continue;
                                sel.value = String(hit.value || '');
                                sel.dispatchEvent(new Event('input', { bubbles: true }));
                                sel.dispatchEvent(new Event('change', { bubbles: true }));
                                const selected = sel.options && sel.options[sel.selectedIndex];
                                out.state = matchesState(sel.value) || matchesState((selected && (selected.text || selected.label)) || '');
                                return true;
                            }
                            const controls = Array.from(document.querySelectorAll('[role="combobox"], button, [aria-haspopup="listbox"], [tabindex]'))
                                .filter(visible)
                                .filter(el => {
                                    const hint = [meta(el), el.innerText || el.textContent || ''].join(' ');
                                    return /(^|\s)(州|state|province|region|administrative)($|\s)/i.test(hint) || /^州$/.test(String(el.innerText || el.textContent || '').trim());
                                });
                            for (const ctl of controls) {
                                clickEl(ctl);
                                const options = Array.from(document.querySelectorAll('[role="option"], [role="menuitem"], li, div, span'))
                                    .filter(visible)
                                    .filter(el => matchesState(el.innerText || el.textContent || el.getAttribute('aria-label') || ''));
                                const opt = options[0];
                                if (!opt) continue;
                                clickEl(opt);
                                out.state = true;
                                return true;
                            }
                            return false;
                        };
                        selectState();
                        const inputs = Array.from(document.querySelectorAll('input, textarea')).filter(visible);
                        const used = new Set();
                        const fillBy = (key, value, patterns) => {
                            const el = inputs.find((node) => !used.has(node) && patterns.some((re) => re.test(meta(node))));
                            if (!el) return false;
                            used.add(el);
                            out[key] = setVal(el, value);
                            return out[key];
                        };
                        fillBy('name', payload.name, [/name|fullname|full name|姓名|全名/]);
                        fillBy('street', payload.street, [/address-line1|line1|address1|address|street|住所|地址/]);
                        fillBy('city', payload.city, [/address-level2|locality|city|市区町村|城市|市/]);
                        if (!out.state) fillBy('state', payload.state, [/address-level1|administrative|state|province|region|都道府県|州|省/]);
                        fillBy('zip', payload.zip, [/postal|zip|postcode|邮编|郵便/]);
                        return out;
                    }""",
                    payload,
                )
                if isinstance(filled, dict):
                    for key, value in filled.items():
                        result[key] = bool(value) or result.get(key, False)
            except Exception:
                continue
        if result:
            log(f"[Stripe] billing address iframe fill: {result}")
        return result

    async def _verify_stripe_billing_address_complete() -> dict[str, Any]:
        """提交前核验 Stripe 地址 Element 的真实提交值，防止下拉框视觉占位仍被误判成功。"""
        state_code = str(card.state or "").strip().upper()
        state_full = _US_STATE_NAMES.get(state_code, str(card.state or "").strip())
        payload = {
            "country": desired_country,
            "countryLabels": desired_labels,
            "stateLabels": [item for item in (state_code, state_full, card.state) if str(item or "").strip()],
            "expected": {
                "name": card.holder_name or f"{card.first_name} {card.last_name}".strip() or email,
                "street": card.street,
                "city": city_value,
                "zip": zip_value,
            },
        }
        snapshots: list[dict[str, Any]] = []
        for target in await _stripe_address_targets():
            try:
                status = await target.evaluate(
                    r"""(payload) => {
                        const visible = (el) => {
                            if (!el) return false;
                            const rect = el.getBoundingClientRect();
                            if (rect.width < 6 || rect.height < 6) return false;
                            const style = window.getComputedStyle(el);
                            return style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity || '1') > 0.05;
                        };
                        const textOf = (el) => String(
                            el?.innerText || el?.textContent || el?.value || el?.getAttribute?.('aria-label') || ''
                        ).replace(/\s+/g, ' ').trim();
                        const compact = (s) => String(s || '').toLowerCase().replace(/[^a-z0-9\u4e00-\u9fff]+/g, '');
                        const meta = (el) => [
                            el.id || '',
                            el.name || '',
                            el.autocomplete || '',
                            el.placeholder || '',
                            el.getAttribute('aria-label') || '',
                            el.getAttribute('data-testid') || '',
                            el.labels ? Array.from(el.labels).map(x => x.innerText || x.textContent || '').join(' ') : ''
                        ].join(' ').toLowerCase();
                        const selectInfo = (sel) => {
                            if (!sel) return {present: false, ok: false, value: '', text: '', invalid: false};
                            const opt = sel.options && sel.options[sel.selectedIndex];
                            return {
                                present: true,
                                value: String(sel.value || '').trim(),
                                text: String((opt && (opt.text || opt.label)) || '').trim(),
                                invalid: String(sel.getAttribute('aria-invalid') || '').toLowerCase() === 'true',
                            };
                        };
                        const inputInfo = (input) => {
                            if (!input) return {present: false, ok: false, value: '', invalid: false};
                            return {
                                present: true,
                                value: String(input.value || '').trim(),
                                invalid: String(input.getAttribute('aria-invalid') || '').toLowerCase() === 'true',
                            };
                        };
                        const hasExpectedText = (actual, expected) => {
                            const a = compact(actual);
                            const e = compact(expected);
                            if (!e) return !!a;
                            return !!a && (a.includes(e) || e.includes(a));
                        };
                        const allSelects = Array.from(document.querySelectorAll('select')).filter(visible);
                        const allInputs = Array.from(document.querySelectorAll('input, textarea')).filter(visible);
                        const findSelect = (patterns, manyOptions = false) => allSelects.find((el) => {
                            const m = meta(el);
                            return patterns.some((re) => re.test(m)) || (manyOptions && (el.options || []).length > 80);
                        }) || null;
                        const findInput = (patterns) => allInputs.find((el) => patterns.some((re) => re.test(meta(el)))) || null;
                        const country = selectInfo(findSelect([/country|国家|地区|國家|地域/], true));
                        const state = selectInfo(findSelect([/administrative|address-level1|state|province|region|州|省/], false));
                        const fields = {
                            name: inputInfo(findInput([/name|fullname|full name|姓名|全名/])),
                            street: inputInfo(findInput([/address-line1|line1|address1|street|住所|地址/])),
                            city: inputInfo(findInput([/address-level2|locality|city|市区町村|城市|市/])),
                            zip: inputInfo(findInput([/postal|zip|postcode|邮编|郵便/])),
                        };
                        const countryLabels = (payload.countryLabels || []).map(x => String(x || '').toLowerCase()).filter(Boolean);
                        country.ok = country.present
                            && !country.invalid
                            && !!country.value
                            && (
                                country.value.toUpperCase() === String(payload.country || '').toUpperCase()
                                || countryLabels.some(k => country.text.toLowerCase().includes(k))
                            );
                        const stateLabels = (payload.stateLabels || []).map(compact).filter(Boolean);
                        state.ok = state.present
                            && !state.invalid
                            && !!state.value
                            && stateLabels.some((key) => compact(state.value) === key || compact(state.text).includes(key));
                        for (const [key, info] of Object.entries(fields)) {
                            info.ok = info.present
                                && !info.invalid
                                && hasExpectedText(info.value, payload.expected?.[key] || '');
                        }
                        const relevant = (
                            country.present || state.present || Object.values(fields).some(x => x.present)
                            || /国家或地区|全名|billingaddress|address element|地址第 1 行/i.test(textOf(document.body))
                        );
                        const missing = [];
                        if (relevant) {
                            if (!country.ok) missing.push('country');
                            if (String(payload.country || '').toUpperCase() === 'US' && !state.ok) missing.push('state');
                            for (const [key, info] of Object.entries(fields)) {
                                if (!info.ok) missing.push(key);
                            }
                        }
                        const invalidFields = Array.from(document.querySelectorAll('[aria-invalid="true"]'))
                            .filter(visible)
                            .map((el) => {
                                const m = meta(el);
                                const v = String(el.value || '').trim();
                                const t = textOf(el);
                                return {meta: m.slice(0, 120), value: v.slice(0, 80), text: t.slice(0, 120)};
                            });
                        return {
                            relevant,
                            ok: relevant && missing.length === 0 && invalidFields.length === 0,
                            missing,
                            country,
                            state,
                            fields,
                            invalidFields,
                            body: textOf(document.body).slice(0, 220),
                        };
                    }""",
                    payload,
                )
                if isinstance(status, dict) and status.get("relevant"):
                    snapshots.append(status)
            except Exception as exc:
                snapshots.append({"relevant": False, "ok": False, "error": str(exc)[:180]})
        if not snapshots:
            return {"ok": False, "missing": ["address_frame"], "snapshots": []}
        best = next((item for item in snapshots if item.get("ok")), snapshots[-1])
        return {"ok": bool(best.get("ok")), "missing": best.get("missing") or [], "snapshots": snapshots}

    # 日本地址表单通常需要“邮编 -> 都道府县 -> 城市 -> 地址”顺序
    if desired_country == "JP":
        await _safe_fill(
            '#billingPostalCode, input[name*="postalCode" i], input[name*="zip" i], input[placeholder*="邮编" i], input[placeholder*="ZIP" i]',
            zip_value,
        )

        # 选择辖区（都道府县）
        pref_label = _JP_PREFECTURE_LABELS.get(card.state, card.state)
        pref_keys = [
            pref_label,
            card.state,
            f"{pref_label} {card.state}",
            f"{pref_label} - {card.state}",
            f"{pref_label} — {card.state}",
        ]
        pref_select_selector = (
            "#billingAdministrativeArea, #billingRegion, "
            'select[name*="administrative" i], select[name*="state" i], '
            'select[name*="region" i], select[name*="province" i], '
            'select[id*="administrative" i], select[id*="state" i], '
            'select[id*="region" i], select[id*="province" i], '
            'select[autocomplete="address-level1"]'
        )
        pref_any_selector = (
            f"{pref_select_selector}, "
            'input[name*="administrative" i], input[name*="state" i], '
            'input[name*="region" i], input[name*="province" i], '
            'input[id*="administrative" i], input[id*="state" i], '
            'input[id*="region" i], input[id*="province" i], '
            'input[autocomplete="address-level1"], '
            '[role="combobox"][aria-label*="都道府県"], '
            '[role="combobox"][aria-label*="prefecture" i], '
            '[role="combobox"][aria-label*="state" i], '
            '[role="combobox"][name*="administrative" i], '
            '[role="combobox"][name*="state" i], '
            '[role="combobox"][name*="region" i], '
            '[aria-label*="都道府県"], [aria-label*="prefecture" i], [aria-label*="state" i]'
        )

        async def _switch_stripe_prefecture_like_country(labels: list[str]) -> bool:
            # Fast path: scan native select options once instead of timing out per candidate.
            try:
                result = await page.evaluate(
                    """(labels) => {
                        const keys = (labels || []).map(x => String(x || '').toLowerCase()).filter(Boolean);
                        const selects = Array.from(document.querySelectorAll('select'));
                        const candidates = selects.filter((sel) => {
                            const name = String(sel.name || '').toLowerCase();
                            const id = String(sel.id || '').toLowerCase();
                            const ac = String(sel.getAttribute('autocomplete') || '').toLowerCase();
                            return (
                                name.includes('administrative') || name.includes('state') || name.includes('region') || name.includes('province') ||
                                id.includes('administrative') || id.includes('state') || id.includes('region') || id.includes('province') ||
                                ac.includes('address-level1')
                            );
                        });
                        for (const sel of candidates) {
                            const opts = Array.from(sel.options || []);
                            let hit = opts.find(o => keys.some(k => String(o.value || '').toLowerCase() === k));
                            if (!hit) {
                                hit = opts.find(o => {
                                    const t = String(o.text || o.label || '').toLowerCase();
                                    return keys.some(k => t.includes(k));
                                });
                            }
                            if (!hit) continue;
                            sel.value = String(hit.value || '');
                            sel.dispatchEvent(new Event('input', {bubbles: true}));
                            sel.dispatchEvent(new Event('change', {bubbles: true}));
                            return {ok: true, value: sel.value || '', text: String(hit.text || hit.label || '')};
                        }
                        return {ok: false, value: '', text: ''};
                    }""",
                    labels,
                )
                return bool((result or {}).get("ok"))
            except Exception:
                return False

        async def _verify_prefecture_selected(pref_label_value: str, state_key: str) -> tuple[bool, str]:
            try:
                result = await page.evaluate(
                    """(payload) => {
                        const prefLabel = String((payload && payload.prefLabel) || '');
                        const stateKey = String((payload && payload.stateKey) || '');
                        const isVisible = (el) => {
                            if (!el) return false;
                            const r = el.getBoundingClientRect();
                            if (r.width < 8 || r.height < 8) return false;
                            const st = window.getComputedStyle(el);
                            if (!st) return false;
                            if (st.display === 'none' || st.visibility === 'hidden') return false;
                            if (Number(st.opacity || '1') < 0.05) return false;
                            return true;
                        };
                        const keyA = String(prefLabel || '').toLowerCase();
                        const keyB = String(stateKey || '').toLowerCase();
                        const hasKey = (s) => {
                            const t = String(s || '').toLowerCase();
                            return (!!keyA && t.includes(keyA)) || (!!keyB && t.includes(keyB));
                        };
                        const textOf = (el) => String(el?.innerText || el?.textContent || '').trim();
                        let candidates = Array.from(document.querySelectorAll(
                            String((payload && payload.selector) || '')
                        )).filter(isVisible);
                        if (!candidates.length) {
                            // 文本反查：从“都道府県”标签附近找真实控件
                            const labels = Array.from(document.querySelectorAll('*'))
                                .filter(isVisible)
                                .filter(el => {
                                    const t = String(el.innerText || el.textContent || '').trim();
                                    return /都道府県/.test(t) && t.length <= 30;
                                });
                            if (labels.length) {
                                const anchor = labels[0];
                                const host = anchor.closest('label,div,section,fieldset,li') || anchor.parentElement || anchor;
                                const scoped = Array.from(
                                    (host.parentElement || host).querySelectorAll('select,input,[role="combobox"],[aria-haspopup="listbox"],button')
                                ).filter(isVisible);
                                if (scoped.length) {
                                    candidates = scoped;
                                } else {
                                    const globalCands = Array.from(
                                        document.querySelectorAll('select,input,[role="combobox"],button,[aria-haspopup="listbox"]')
                                    ).filter(isVisible).filter(el => {
                                        const hint = [
                                            el.id || '', el.name || '', el.getAttribute('aria-label') || '',
                                            el.getAttribute('placeholder') || '', el.getAttribute('data-testid') || '',
                                            textOf(el),
                                        ].join(' ').toLowerCase();
                                        return /都道府県|prefecture|state|administrative|province|region/.test(hint);
                                    });
                                    if (globalCands.length) candidates = globalCands;
                                    else return { ok: false, reason: 'no_candidate_with_label' };
                                }
                            } else {
                                const globalCands = Array.from(
                                    document.querySelectorAll('select,input,[role="combobox"],button,[aria-haspopup="listbox"]')
                                ).filter(isVisible).filter(el => {
                                    const hint = [
                                        el.id || '', el.name || '', el.getAttribute('aria-label') || '',
                                        el.getAttribute('placeholder') || '', el.getAttribute('data-testid') || '',
                                        textOf(el),
                                    ].join(' ').toLowerCase();
                                    return /prefecture|state|administrative|province|region/.test(hint);
                                });
                                if (globalCands.length) candidates = globalCands;
                                else return { ok: false, reason: 'no_candidate_no_label' };
                            }
                        }
                        const el = candidates[0];
                        const tag = el.tagName.toLowerCase();
                        const ariaInvalid = String(el.getAttribute('aria-invalid') || '').toLowerCase();
                        const rawValue = String(el.value || '').trim();
                        const rawText = String(el.innerText || el.textContent || '').trim();
                        const placeholderLike = /都道府県/.test(rawText) && !hasKey(rawText);
                        let selected = false;
                        if (tag === 'select') {
                            const opt = el.options && el.options[el.selectedIndex || 0];
                            const optText = String((opt && (opt.text || opt.label)) || '').trim();
                            const optVal = String((opt && opt.value) || '').trim();
                            selected = (ariaInvalid !== 'true') && (hasKey(optText) || hasKey(optVal));
                            return {
                                ok: !!selected,
                                reason: selected ? 'select_ok' : `select_miss text=${optText} value=${optVal} ariaInvalid=${ariaInvalid}`,
                            };
                        }
                        selected = (ariaInvalid !== 'true') && !placeholderLike && (hasKey(rawValue) || hasKey(rawText));
                        return {
                            ok: !!selected,
                            reason: selected ? 'combo_ok' : `combo_miss value=${rawValue} text=${rawText} ariaInvalid=${ariaInvalid}`,
                        };
                    }""",
                    {"prefLabel": pref_label_value, "stateKey": state_key, "selector": pref_any_selector},
                )
                return bool((result or {}).get("ok")), str((result or {}).get("reason") or "")
            except Exception as exc:
                return False, f"verify_exception:{exc}"

        async def _pick_prefecture_by_role() -> bool:
            names = [
                f"{pref_label} — {card.state}",
                f"{pref_label} - {card.state}",
                f"{pref_label} {card.state}",
                pref_label,
                card.state,
            ]
            # 先用页面脚本做一次“打开下拉 + 匹配点击”，兼容自定义组件
            try:
                picked_js = await page.evaluate(
                    """(labels) => {
                        const norm = (s) => String(s || '')
                            .toLowerCase()
                            .replace(/[\\s\\-ー—‐－]/g, '')
                            .replace(/[()（）]/g, '');
                        const keys = (labels || []).map(norm).filter(Boolean);
                        if (!keys.length) return false;
                        const isVisible = (el) => {
                            if (!el) return false;
                            const r = el.getBoundingClientRect();
                            if (r.width < 8 || r.height < 8) return false;
                            const st = window.getComputedStyle(el);
                            if (!st) return false;
                            if (st.display === 'none' || st.visibility === 'hidden') return false;
                            if (Number(st.opacity || '1') < 0.05) return false;
                            return true;
                        };
                        const textOf = (el) => String(el?.innerText || el?.textContent || '').trim();
                        const clickEl = (el) => {
                            if (!el) return false;
                            try { el.click(); return true; } catch {}
                            try {
                                const r = el.getBoundingClientRect();
                                el.dispatchEvent(new MouseEvent('mousedown', {bubbles:true, clientX:r.left+8, clientY:r.top+8}));
                                el.dispatchEvent(new MouseEvent('mouseup', {bubbles:true, clientX:r.left+8, clientY:r.top+8}));
                                el.dispatchEvent(new MouseEvent('click', {bubbles:true, clientX:r.left+8, clientY:r.top+8}));
                                return true;
                            } catch {}
                            return false;
                        };

                        // 1) 打开下拉
                        const triggerSelectors = [
                            '[role="combobox"][aria-label*="都道府県"]',
                            '[role="combobox"][name*="administrative" i]',
                            '[role="combobox"]',
                            'button[aria-haspopup="listbox"]',
                        ];
                        let trigger = null;
                        for (const sel of triggerSelectors) {
                            const cands = Array.from(document.querySelectorAll(sel)).filter(isVisible);
                            const hit = cands.find(el => /都道府県|prefecture|state/i.test(textOf(el) + ' ' + (el.getAttribute('aria-label') || '') + ' ' + (el.getAttribute('name') || '')));
                            if (hit) { trigger = hit; break; }
                            if (!trigger && cands.length) trigger = cands[0];
                        }
                        if (!trigger) {
                            const labelsText = Array.from(document.querySelectorAll('*'))
                                .filter(isVisible)
                                .find(el => /都道府県/.test(textOf(el)) && textOf(el).length < 20);
                            if (labelsText) {
                                const near = labelsText.closest('label,div,section') || labelsText.parentElement;
                                if (near) {
                                    trigger = near.querySelector('[role="combobox"],button[aria-haspopup="listbox"],input,select') || near;
                                }
                            }
                        }
                        if (trigger) clickEl(trigger);

                        // 2) 匹配选项（role=option/listbox/menuitem/li）
                        const optionSelectors = [
                            '[role="option"]',
                            '[role="listbox"] [role="option"]',
                            'li[role="option"]',
                            'div[role="option"]',
                            '[role="menu"] [role="menuitem"]',
                            'ul li',
                        ];
                        const options = [];
                        for (const sel of optionSelectors) {
                            for (const el of Array.from(document.querySelectorAll(sel))) {
                                if (!isVisible(el)) continue;
                                const t = textOf(el);
                                if (!t) continue;
                                options.push(el);
                            }
                        }
                        const uniq = Array.from(new Set(options));
                        const best = uniq.find(el => {
                            const t = norm(textOf(el));
                            return keys.some(k => t.includes(k));
                        });
                        if (best) return clickEl(best);

                        // 3) 未命中时，选第一个可见候选（JP专用兜底，宁可先选上）
                        if (uniq.length) return clickEl(uniq[0]);
                        return false;
                    }""",
                    names,
                )
                if picked_js:
                    return True
            except Exception:
                pass

            # 打开都道府县下拉
            opened = False
            open_selectors = [
                '[role="combobox"][aria-label*="都道府県"]',
                '[role="combobox"][name*="administrative" i]',
                '[role="combobox"][name*="region" i]',
                '[role="combobox"][name*="province" i]',
                '[role="combobox"]:has-text("都道府県")',
                'button[aria-haspopup="listbox"]:has-text("都道府県")',
                'button[aria-haspopup="listbox"][aria-label*="都道府県"]',
                'button[aria-haspopup="listbox"][aria-label*="prefecture" i]',
                'button[aria-haspopup="listbox"][aria-label*="state" i]',
            ]
            for sel in open_selectors:
                try:
                    trigger = page.locator(sel).first
                    if await trigger.is_visible(timeout=1200):
                        await trigger.click(timeout=2000)
                        opened = True
                        break
                except Exception:
                    continue
            if not opened:
                try:
                    # 点击标签附近触发
                    lbl = page.locator('text=都道府県').first
                    if await lbl.is_visible(timeout=1200):
                        await lbl.click(timeout=2000)
                        opened = True
                except Exception:
                    pass
            if not opened:
                return False

            await page.wait_for_timeout(350)

            # 优先 role=option
            for name in names:
                if not name:
                    continue
                try:
                    opt = page.get_by_role("option", name=name).first
                    if await opt.is_visible(timeout=1200):
                        await opt.click(timeout=2000)
                        return True
                except Exception:
                    pass

            # 兜底：listbox 内文本匹配
            for name in names:
                if not name:
                    continue
                listbox_selectors = [
                    f'[role="listbox"] >> text={name}',
                    f'li[role="option"]:has-text("{name}")',
                    f'div[role="option"]:has-text("{name}")',
                ]
                for sel in listbox_selectors:
                    try:
                        opt2 = page.locator(sel).first
                        if await opt2.is_visible(timeout=1000):
                            await opt2.click(timeout=2000)
                            return True
                    except Exception:
                        continue

            # 最后一招：方向键+回车
            try:
                await page.keyboard.press("ArrowDown")
                await page.wait_for_timeout(180)
                await page.keyboard.press("Enter")
                return True
            except Exception:
                return False

        async def _pick_prefecture_by_tab_fallback() -> bool:
            """不依赖下拉 DOM：从邮编框 Tab 到都道府県并键盘选中。"""
            zip_sel = '#billingPostalCode, input[name*="postalCode" i], input[name*="zip" i], input[placeholder*="邮编" i], input[placeholder*="ZIP" i]'
            try:
                zip_input = page.locator(f"{zip_sel}:visible").first
                if await zip_input.is_visible(timeout=1200):
                    await zip_input.click(timeout=1500)
                    await page.wait_for_timeout(120)
                    await page.keyboard.press("Tab")
                    await page.wait_for_timeout(150)
                    await page.keyboard.press("Control+A")
                    await page.keyboard.type(pref_label, delay=35)
                    await page.wait_for_timeout(250)
                    await page.keyboard.press("ArrowDown")
                    await page.wait_for_timeout(150)
                    await page.keyboard.press("Enter")
                    await page.wait_for_timeout(250)
                    return True
            except Exception:
                return False
            return False

        async def _pick_prefecture_by_label_api() -> bool:
            """Playwright 标签定位兜底：直接按 label/aria-label 定位都道府県控件。"""
            patterns = [r"都道府県", r"prefecture", r"state", r"province", r"region"]
            for pattern in patterns:
                try:
                    field = page.get_by_label(re.compile(pattern, re.I)).first
                    if not await field.is_visible(timeout=900):
                        continue
                    tag = await field.evaluate("el => el.tagName.toLowerCase()")
                    if tag == "select":
                        for key in [pref_label, card.state, f"{pref_label} {card.state}"]:
                            if not key:
                                continue
                            try:
                                await field.select_option(label=key, timeout=1400)
                                return True
                            except Exception:
                                try:
                                    await field.select_option(value=key, timeout=1400)
                                    return True
                                except Exception:
                                    continue
                    else:
                        await field.click(timeout=1200)
                        try:
                            await field.fill("", timeout=1200)
                        except Exception:
                            pass
                        await field.type(pref_label, delay=30)
                        await page.wait_for_timeout(180)
                        for key in [pref_label, card.state]:
                            try:
                                opt = page.locator(f'[role="option"]:has-text("{key}")').first
                                if await opt.is_visible(timeout=800):
                                    await opt.click(timeout=1200)
                                    return True
                            except Exception:
                                continue
                        try:
                            await page.keyboard.press("Enter")
                            return True
                        except Exception:
                            pass
                except Exception:
                    continue
            return False

        await page.wait_for_timeout(300)
        await _safe_fill(
            '#billingAddressLine1, input[name*="addressLine1" i], input[name*="address" i], input[placeholder*="地址" i], input[placeholder*="Address" i]',
            card.street,
        )
        # Stripe 日本表单在地址输入后可能重置“城市”，最后再回填一次并校验
        city_selector = '#billingLocality, input[name*="locality" i], input[name*="city" i], input[placeholder*="城市" i], input[placeholder*="City" i]'
        city_ok = await _safe_fill(city_selector, city_value)
        if not city_ok:
            await page.wait_for_timeout(300)
            city_ok = await _safe_fill(city_selector, city_value)
        if not city_ok:
            log(f"[Stripe] ⚠️ 日本地址城市填充失败: city={city_value}")

        # 地址阶段再统一执行都道府县选择与校验
        pref_ok = await _switch_stripe_prefecture_like_country(pref_keys)
        v_ok, v_reason = await _verify_prefecture_selected(pref_label, card.state)
        if desired_country == "JP":
            now = time.perf_counter()
            log(
                f"[Stripe][JP] address prep timing: country={country_finished_at - country_started_at:.1f}s "
                f"fields={now - country_finished_at:.1f}s total={now - stripe_started_at:.1f}s"
            )
        log(
            f"[Stripe][JP] pref step1 like-country: attempted={pref_ok} "
            f"committed={v_ok} reason={v_reason}"
        )

        try:
            state_el = page.locator(pref_any_selector).first
            if await state_el.is_visible(timeout=3000):
                tag = await state_el.evaluate("el => el.tagName.toLowerCase()")
                if tag == "select":
                    picked = False
                    for v in [pref_label, card.state, f"{pref_label} — {card.state}", f"{pref_label} {card.state}"]:
                        if not v:
                            continue
                        try:
                            await state_el.select_option(value=v, timeout=2500)
                            picked = True
                            break
                        except Exception:
                            try:
                                await state_el.select_option(label=v, timeout=2500)
                                picked = True
                                break
                            except Exception:
                                continue
                    if not picked:
                        await page.evaluate(
                            """(targetKeys) => {
                                const keys = (targetKeys || []).map(x => String(x || '').toLowerCase()).filter(Boolean);
                                const selectors = [
                                    '#billingAdministrativeArea',
                                    '#billingRegion',
                                    'select[name*="administrative" i]',
                                    'select[name*="state" i]',
                                    'select[name*="region" i]',
                                    'select[name*="province" i]',
                                    'select[id*="administrative" i]',
                                    'select[id*="state" i]',
                                    'select[id*="region" i]',
                                    'select[id*="province" i]',
                                    'select[autocomplete="address-level1"]'
                                ];
                                const sel = selectors.map(s => document.querySelector(s)).find(Boolean);
                                if (!sel || sel.tagName.toLowerCase() !== 'select') return false;
                                const opts = Array.from(sel.options || []);
                                const hit = opts.find(o => {
                                    const v = String(o.value || '').toLowerCase();
                                    const t = String(o.text || o.label || '').toLowerCase();
                                    return keys.some(k => v === k || t.includes(k));
                                });
                                if (!hit) return false;
                                sel.value = String(hit.value || '');
                                sel.dispatchEvent(new Event('input', {bubbles: true}));
                                sel.dispatchEvent(new Event('change', {bubbles: true}));
                                return true;
                            }""",
                            [pref_label, card.state, f"{pref_label} {card.state}", f"{pref_label} — {card.state}"],
                        )
                else:
                    try:
                        await state_el.click(timeout=1500)
                    except Exception:
                        pass
                    try:
                        await state_el.fill("", timeout=1500)
                        await state_el.fill(pref_label, timeout=3000)
                    except Exception:
                        try:
                            await page.keyboard.type(pref_label, delay=30)
                        except Exception:
                            pass
                    picked_combo = False
                    for key in [pref_label, card.state]:
                        if not key:
                            continue
                        option_selectors = [
                            f'[role="option"]:has-text("{key}")',
                            f'li:has-text("{key}")',
                            f'div[role="option"]:has-text("{key}")',
                        ]
                        for sel in option_selectors:
                            try:
                                opt = page.locator(sel).first
                                if await opt.is_visible(timeout=1200):
                                    await opt.click(timeout=2000)
                                    picked_combo = True
                                    break
                            except Exception:
                                continue
                        if picked_combo:
                            break
                    try:
                        await page.keyboard.press("Enter")
                    except Exception:
                        pass
        except Exception:
            pass

        try:
            pref_verified, verify_reason = await _verify_prefecture_selected(pref_label, card.state)
            if not pref_verified:
                pref_verified = await _pick_prefecture_by_role()
                if pref_verified:
                    await page.wait_for_timeout(300)
                pref_verified2, verify_reason2 = await _verify_prefecture_selected(pref_label, card.state)
                log(
                    f"[Stripe][JP] pref step2 role: attempted={pref_verified} "
                    f"committed={pref_verified2} reason={verify_reason2}"
                )
                pref_verified = pref_verified2
                verify_reason = verify_reason2
            if not pref_verified:
                pref_verified = await _pick_prefecture_by_label_api()
                if pref_verified:
                    await page.wait_for_timeout(280)
                pref_verified2, verify_reason2 = await _verify_prefecture_selected(pref_label, card.state)
                log(
                    f"[Stripe][JP] pref step2b label: attempted={pref_verified} "
                    f"committed={pref_verified2} reason={verify_reason2}"
                )
                pref_verified = pref_verified2
                verify_reason = verify_reason2
            if not pref_verified:
                pref_verified = await _pick_prefecture_by_tab_fallback()
                if pref_verified:
                    await page.wait_for_timeout(300)
                pref_verified2, verify_reason2 = await _verify_prefecture_selected(pref_label, card.state)
                log(
                    f"[Stripe][JP] pref step3 tab: attempted={pref_verified} "
                    f"committed={pref_verified2} reason={verify_reason2}"
                )
                pref_verified = pref_verified2
                verify_reason = verify_reason2
            if not pref_verified:
                log(
                    f"[Stripe] ⚠️ 日本地址都道府县未命中: state={card.state}, label={pref_label}, "
                    f"verify_reason={verify_reason}"
                )
        except Exception:
            pass

    else:
        # 美区等旧流程
        await _fill_stripe_address_element()
        for selector, value in [
            ('#billingName, input[name*="name" i], input[autocomplete="name"], input[placeholder*="姓名" i], input[placeholder*="Name" i]', card.holder_name or f"{card.first_name} {card.last_name}".strip() or email),
            ('#billingAddressLine1, input[name*="addressLine1" i], input[name*="address" i], input[placeholder*="地址" i], input[placeholder*="Address" i]', card.street),
            ('#billingLocality, input[name*="locality" i], input[name*="city" i], input[placeholder*="城市" i], input[placeholder*="City" i]', city_value),
            ('#billingPostalCode, input[name*="postalCode" i], input[name*="zip" i], input[placeholder*="邮编" i], input[placeholder*="ZIP" i]', zip_value),
        ]:
            await _safe_fill(selector, value)

        # State - 尝试多种方式匹配
        try:
            state_el = page.locator('#billingAdministrativeArea, select[name*="state" i]').first
            if await state_el.is_visible(timeout=3000):
                tag = await state_el.evaluate("el => el.tagName.toLowerCase()")
                if tag == "select":
                    try:
                        await state_el.select_option(value=card.state, timeout=3000)
                    except Exception:
                        # 尝试用全称
                        full_name = _US_STATE_NAMES.get(str(card.state or "").strip().upper(), card.state)
                        try:
                            await state_el.select_option(label=full_name, timeout=3000)
                        except Exception:
                            pass
                else:
                    await state_el.fill(card.state, timeout=3000)
        except Exception:
            pass

    if desired_country == "US":
        await _accept_stripe_address_suggestion(page, card, city_value, zip_value)
        # 地址建议可能重绘 Stripe Address Element；接受建议后必须再次补齐下拉框。
        await _fill_stripe_address_element()

    # 勾选条款/代理声明。Stripe 会按地区动态增减 checkbox，须扫全量，不可只点第一个。
    checkbox_state = await _ensure_stripe_required_checkboxes(page)
    await page.wait_for_timeout(700)
    checkbox_state_second = await _ensure_stripe_required_checkboxes(page)
    if int(checkbox_state_second.get("total") or 0) > int(checkbox_state.get("total") or 0):
        checkbox_state = checkbox_state_second
    if not checkbox_state.get("total"):
        log("[Stripe] checkout agreement checkbox not detected; keep as diagnostic only")
    elif checkbox_state.get("touched"):
        log("[Stripe] checkout agreement checkbox JS fallback sweep applied")
    else:
        log("[Stripe] checkout agreement checkbox detected")

    if desired_country == "US":
        await _accept_stripe_address_suggestion(page, card, city_value, zip_value)

    await _dismiss_stripe_address_suggestions(page)

    if desired_country == "US":
        billing_verify: dict[str, Any] = {"ok": False, "missing": ["not_checked"]}
        for attempt in range(3):
            await _fill_stripe_address_element()
            await _dismiss_stripe_address_suggestions(page)
            billing_verify = await _verify_stripe_billing_address_complete()
            log(
                f"[Stripe] billing address verify attempt {attempt + 1}/3: "
                f"ok={billing_verify.get('ok')} missing={billing_verify.get('missing')}"
            )
            if billing_verify.get("ok"):
                break
            await page.wait_for_timeout(600)
        if not billing_verify.get("ok"):
            await _save_stripe_failure_debug(
                page,
                email,
                f"stripe billing address incomplete before subscribe: missing={billing_verify.get('missing')}",
            )
            raise RuntimeError(
                f"stripe_billing_address_incomplete: missing={billing_verify.get('missing')}"
            )

    if not await _ensure_stripe_paypal_selected(page):
        await _save_stripe_failure_debug(page, email, "paypal payment option not available before subscribe")
        reason = (
            PAYPAL_FLOW2_RECREATE_LINK
            if recreate_on_missing_paypal
            else PAYPAL_FLOW2_NO_PAYPAL_OPTION
        )
        raise RuntimeError(f"{reason}: paypal payment option not available before subscribe")
    await _dismiss_stripe_address_suggestions(page)
    await _ensure_stripe_required_checkboxes(page)

    # Subscribe / 订阅 - 多种选择器兜底
    log("[Stripe] 点击订阅按钮...")
    subscribe_clicked = False
    subscribe_attempted = False
    subscribe_selectors = [
        'button.SubmitButton',
        'button.SubmitButton--complete',
        '[data-testid="hosted-payment-submit-button"]',
        'button[type="submit"]',
        'input[type="submit"]',
        'button:has-text("Subscribe")',
        'button:has-text("订阅")',
        f'button:has-text("{subscribe_jp}")',
        f'button:has-text("{subscribe_jp_alt}")',
        f'button:has-text("{apply_jp}")',
        f'button:has-text("{continue_jp}")',
    ]

    async def _wait_stripe_subscribe_processing(timeout_ms: int = 4500) -> tuple[bool, str]:
        deadline = time.perf_counter() + (timeout_ms / 1000)
        last_reason = "not_checked"
        while time.perf_counter() < deadline:
            if "paypal.com" in page.url:
                return True, "redirected"
            try:
                state = await page.evaluate(
                    r"""() => {
                        const processingText = /processing|loading|please\s*wait|\u6b63\u5728\u5904\u7406|\u5904\u7406\u4e2d|\u51e6\u7406\u4e2d|\u8bf7\u7a0d\u5019|\u304a\u5f85\u3061\u304f\u3060\u3055\u3044|\u8aad\u307f\u8fbc\u307f\u4e2d/i;
                        const isVisible = (el) => {
                            if (!el) return false;
                            const rect = el.getBoundingClientRect();
                            if (rect.width < 8 || rect.height < 8) return false;
                            const style = window.getComputedStyle(el);
                            return style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity || '1') > 0.05;
                        };
                        const nodes = Array.from(document.querySelectorAll(
                            'button, [role="button"], input[type="submit"], [data-testid="hosted-payment-submit-button"]'
                        )).filter(isVisible);
                        const texts = [];
                        for (const el of nodes) {
                            const text = String(el.innerText || el.textContent || el.value || el.getAttribute('aria-label') || '').trim();
                            const busy = String(el.getAttribute('aria-busy') || '').toLowerCase() === 'true';
                            const disabled = !!el.disabled || String(el.getAttribute('aria-disabled') || '').toLowerCase() === 'true';
                            texts.push(text || el.tagName);
                            if (processingText.test(text)) {
                                return { ok: true, reason: `processing_text:${text}` };
                            }
                            if (busy) {
                                return { ok: true, reason: 'aria_busy' };
                            }
                            if (disabled && /subscribe|\u8ba2\u9605|\u8cfc\u8aad|\u30b5\u30d6\u30b9\u30af\u30e9\u30a4\u30d6|\u7533\u3057\u8fbc\u3080|submit/i.test(text + ' ' + (el.type || ''))) {
                                return { ok: true, reason: 'disabled_after_click' };
                            }
                        }
                        return { ok: false, reason: `visible_buttons:${texts.slice(0, 4).join('|')}` };
                    }"""
                )
                if bool((state or {}).get("ok")):
                    return True, str((state or {}).get("reason") or "processing")
                last_reason = str((state or {}).get("reason") or last_reason)
            except Exception as exc:
                last_reason = f"state_error:{exc}"
            await page.wait_for_timeout(250)
        return False, last_reason

    async def _retry_click_stripe_submit(attempt_no: int) -> bool:
        for sel in subscribe_selectors:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=1200):
                    await btn.click(timeout=10000)
                    processing_ok, processing_reason = await _wait_stripe_subscribe_processing()
                    log(
                        f"[Stripe] retry submit attempt {attempt_no}/3 "
                        f"selector={sel} processing={processing_ok} reason={processing_reason}"
                    )
                    return True
            except Exception:
                continue
        try:
            js_clicked = bool(await page.evaluate(r"""() => {
                const buttons = Array.from(document.querySelectorAll('button'));
                for (const btn of buttons.reverse()) {
                    const text = (btn.textContent || '').trim();
                    const rect = btn.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0 && /Subscribe|\u8ba2\u9605|\u8cfc\u8aad|\u30b5\u30d6\u30b9\u30af\u30e9\u30a4\u30d6|\u7533\u3057\u8fbc\u3080|\u7d9a\u884c|submit/i.test(text + btn.type)) {
                        btn.click();
                        return true;
                    }
                }
                return false;
            }"""))
            if js_clicked:
                processing_ok, processing_reason = await _wait_stripe_subscribe_processing()
                log(
                    f"[Stripe] retry submit attempt {attempt_no}/3 "
                    f"selector=js_fallback processing={processing_ok} reason={processing_reason}"
                )
                return True
        except Exception:
            pass
        return False

    for sel in subscribe_selectors:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=2000):
                # 确保按钮没被禁用
                is_disabled = await btn.is_disabled()
                if is_disabled:
                    log(f"[Stripe] 按钮被禁用: {sel}，等待...")
                    await page.wait_for_timeout(3000)
                for click_attempt in range(2):
                    await btn.click(timeout=10000)
                    subscribe_attempted = True
                    processing_ok, processing_reason = await _wait_stripe_subscribe_processing()
                    if processing_ok:
                        subscribe_clicked = True
                        log(f"[Stripe] 订阅按钮已点击并进入处理状态 (选择器: {sel}, reason={processing_reason})")
                        break
                    if click_attempt == 0:
                        log(
                            f"[Stripe] Subscribe button did not enter processing state, retry click "
                            f"(selector={sel}, reason={processing_reason})"
                        )
                    else:
                        log(
                            f"[Stripe] Subscribe button click state still not observed "
                            f"(selector={sel}, reason={processing_reason})"
                        )
                if subscribe_clicked:
                    break
        except Exception:
            continue

    if not subscribe_clicked:
        # 终极兜底：点击页面上最后一个可见的 submit 按钮
        log("[Stripe] 常规选择器未命中，尝试 JS 点击...")
        js_subscribe_attempted = bool(await page.evaluate(r"""() => {
            const buttons = Array.from(document.querySelectorAll('button'));
            for (const btn of buttons.reverse()) {
                const text = (btn.textContent || '').trim();
                const rect = btn.getBoundingClientRect();
                if (rect.width > 0 && rect.height > 0 && /Subscribe|\u8ba2\u9605|\u8cfc\u8aad|\u30b5\u30d6\u30b9\u30af\u30e9\u30a4\u30d6|\u7533\u3057\u8fbc\u3080|\u7d9a\u884c|submit/i.test(text + btn.type)) {
                    btn.click();
                    return true;
                }
            }
            return false;
        }"""))
        subscribe_attempted = subscribe_attempted or js_subscribe_attempted
        if js_subscribe_attempted:
            processing_ok, processing_reason = await _wait_stripe_subscribe_processing()
            if processing_ok:
                subscribe_clicked = True
                log(f"[Stripe] JS fallback submit entered processing state: {processing_reason}")
            else:
                log(f"[Stripe] JS fallback submit did not enter processing state: {processing_reason}")

    if not subscribe_attempted:
        raise RuntimeError("Stripe subscribe button was not clicked")

    async def _paypal_redirect_page():
        """Stripe 可能在当前页或新页打开 PayPal；两者都要监听。"""
        try:
            pages = list(page.context.pages)
        except Exception:
            pages = [page]
        for candidate in [page, *[item for item in pages if item is not page]]:
            try:
                if "paypal.com" in str(candidate.url or "").lower():
                    return candidate
            except Exception:
                continue
        return None

    # 等跳转 PayPal；若提交已进入处理态但长时间不跳转，不再做长链兜底。
    jumped = False
    redirect_page = None
    for second in range(180):
        redirect_page = await _paypal_redirect_page()
        if redirect_page:
            jumped = True
            break
        manual_redirect = _extract_stripe_confirm_redirect_url(page)
        if manual_redirect:
            log(f"[Stripe] PayPal redirect found in confirm response, navigating manually: {manual_redirect[:180]}")
            try:
                await page.goto(manual_redirect, wait_until="domcontentloaded", timeout=60000)
                redirect_page = page
                jumped = True
                break
            except Exception as exc:
                log(f"[Stripe] manual PayPal redirect failed: {exc}")
        if second in {5, 10, 30, 60, 120}:
            log(
                f"[Stripe] waiting PayPal redirect... {second}s url={page.url} "
                f"confirm={_summarize_stripe_confirm_response(page)} "
                f"network={_summarize_stripe_network_events(page)}"
            )
        await page.wait_for_timeout(1000)
    if not jumped:
        log(
            _zh(r"[Stripe] 180s \u672a\u8df3\u8f6c PayPal\uff0c\u68c0\u67e5\u662f\u5426\u6709\u8868\u5355\u9519\u8bef...")
        )
        has_error = await page.evaluate("""() => {
            const text = (document.body?.innerText || '');
            return /This is required|必填|invalid|错误|error/i.test(text);
        }""")
        if has_error:
            log("[Stripe] 检测到表单错误，按 Stripe/PayPal 跳转超时处理，不再做长链兜底")
        log(f"[Stripe] recent network events: {_summarize_stripe_network_events(page)}")
        await _save_stripe_failure_debug(
            page,
            email,
            f"submit entered processing but did not redirect PayPal in 180s; form_error={bool(has_error)}",
        )
        raise RuntimeError(
            f"{PAYPAL_FLOW2_STRIPE_PAYPAL_TIMEOUT}: submit entered processing but did not redirect PayPal in 180s; "
            f"form_error={bool(has_error)} url={page.url}"
        )

    if redirect_page is not None and redirect_page is not page:
        log(f"[Stripe] PayPal opened in new page: {redirect_page.url}")
        page = redirect_page
    await page.wait_for_timeout(3000)
    return page


async def fill_paypal(
    page,
    email: str,
    card: CardInfo,
    phone: PhoneInfo,
    paypal_password: str,
    proxy: str | None = None,
    *,
    country_code: str = "US",
) -> None:
    """PayPal 页面：注册 + 绑卡。"""
    await page.wait_for_timeout(5000)
    mail_jp = _zh(r"\u30e1\u30fc\u30eb")
    continue_jp = _zh(r"\u7d9a\u884c")
    next_jp = _zh(r"\u6b21\u3078")
    confirm_jp = _zh(r"\u78ba\u8a8d")
    pay_continue_jp = _zh(r"\u652f\u6255\u3044\u3092\u7d9a\u884c")
    agree_create_jp = _zh(r"\u540c\u610f\u3057\u3066\u4f5c\u6210")
    create_account_jp = _zh(r"\u30a2\u30ab\u30a6\u30f3\u30c8\u3092\u4f5c\u6210")
    agree_continue_jp = _zh(r"\u540c\u610f\u3057\u3066\u7d9a\u884c")
    phone_jp = _zh(r"\u96fb\u8a71")
    address_jp = _zh(r"\u4f4f\u6240")
    city_jp = _zh(r"\u5e02\u533a\u753a\u6751")
    postal_jp = _zh(r"\u90f5\u4fbf\u756a\u53f7")

    # 第一步：填邮箱（确保所有可见的 email 字段都被填写）
    log("[PayPal] 填写邮箱...")
    email_filled = False
    # PayPal 中文页面的 placeholder 是 "电子邮箱地址或手机号码"
    email_selectors = (
        'input[name="email"], input[type="email"], input[autocomplete="email"], input[autocomplete="username"], '
        'input[name*="login" i], input[id*="email" i], input[id*="login" i], '
        'input[placeholder*="邮箱" i], input[placeholder*="email" i], input[placeholder*="手机号" i], '
        f'input[placeholder*="{mail_jp}"], input[aria-label*="email" i], input[aria-label*="邮箱" i], input[aria-label*="{mail_jp}"]'
    )
    email_fields = page.locator(email_selectors)
    count = await email_fields.count()
    for i in range(count):
        field = email_fields.nth(i)
        try:
            if await field.is_visible(timeout=2000):
                await field.fill("", timeout=2000)
                await field.fill(email, timeout=3000)
                email_filled = True
                break
        except Exception:
            pass
    if not email_filled:
        log("[PayPal] ⚠️ 未找到可见的邮箱输入框，尝试备用选择器")
        try:
            await page.locator(f'input[aria-label*="email" i], input[aria-label*="邮箱" i], input[aria-label*="{mail_jp}"]').first.fill(email, timeout=5000)
        except Exception:
            pass

    # 点击"继续付款"优先（PayPal 注册页面的实际按钮文字），然后是其他变体
    clicked_next = False
    next_selectors = [
        'button[id="btnNext"]',
        'button[type="submit"]',
        'input[type="submit"]',
        '[data-atomic-wait-task]',
        'button:has-text("继续付款")',
        'button:has-text("Continue")',
        'button:has-text("继续")',
        'button:has-text("Next")',
        'button:has-text("下一页")',
        f'button:has-text("{next_jp}")',
        f'button:has-text("{continue_jp}")',
        f'button:has-text("{pay_continue_jp}")',
        f'button:has-text("{confirm_jp}")',
    ]
    for sel in next_selectors:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=2000):
                await btn.click(timeout=5000)
                clicked_next = True
                log(f"[PayPal] 点击了按钮 (选择器: {sel})")
                break
        except Exception:
            continue

    if not clicked_next:
        log("[PayPal] ⚠️ 未找到可点击的下一步按钮")

    # 等待 PayPal 页面加载完成（按钮转圈结束，新页面元素出现）
    log("[PayPal] 等待页面跳转/加载...")
    for _ in range(30):
        await page.wait_for_timeout(2000)
        # 检查是否已经进入了注册表单（有国家选择框或手机号输入框）
        try:
            has_form = await page.evaluate("""() => {
                const selects = document.querySelectorAll('select');
                const hasCountrySelect = Array.from(selects).some(s => s.options.length > 50 || /country/i.test(s.name + s.id));
                const hasPhoneInput = !!document.querySelector(
                    'input[name*="phone" i], input[name*="telephone" i], input[id*="phone" i], input[id*="tel" i], ' +
                    'input[type="tel"], input[autocomplete="tel"], input[placeholder*="Phone" i], input[placeholder*="手机" i], input[placeholder*="\\u96fb\\u8a71"]'
                );
                const hasCardInput = !!document.querySelector('input[name*="card" i], input[id*="card" i], input[placeholder*="Card" i], input[placeholder*="卡号" i]');
                return hasCountrySelect || hasPhoneInput || hasCardInput;
            }""")
        except Exception:
            # 页面正在导航中，等一下再试
            continue
        if has_form:
            log("[PayPal] 注册表单已加载")
            break
    else:
        log("[PayPal] ⚠️ 等待 60 秒后仍未检测到注册表单，继续尝试...")

    await page.wait_for_timeout(2000)

    desired_country = "JP" if str(country_code or "").upper() == "JP" else "US"
    desired_labels = ["Japan", "日本"] if desired_country == "JP" else ["United States", "美国"]

    # 第二步：进入注册表单后，先切国家
    # 等待国家下拉框出现并可交互
    log("[PayPal] 等待国家选择框加载...")
    try:
        await page.locator('select').first.wait_for(state="attached", timeout=10000)
    except Exception:
        pass
    await page.wait_for_timeout(2000)

    async def _switch_paypal_country(target_country: str, labels: list[str]) -> bool:
        # 先尝试 select_option（值 / 文本）
        try:
            country_sel = page.locator('select[name*="country" i], select[id*="country" i]').first
            if await country_sel.is_visible(timeout=2000):
                try:
                    await country_sel.select_option(target_country, timeout=2500)
                    return True
                except Exception:
                    pass
                for lbl in labels:
                    try:
                        await country_sel.select_option(label=lbl, timeout=2500)
                        return True
                    except Exception:
                        continue
        except Exception:
            pass

        # 兜底：遍历所有 select，按 value 或 option 文本匹配国家
        result = await page.evaluate("""(targetCountry, labels) => {
            const keys = (labels || []).map(x => String(x || '').toLowerCase()).filter(Boolean);
            const selects = Array.from(document.querySelectorAll('select'));
            const candidates = selects.filter((sel) => {
                const name = String(sel.name || '').toLowerCase();
                const id = String(sel.id || '').toLowerCase();
                return name.includes('country') || id.includes('country') || sel.options.length > 50;
            });
            for (const sel of candidates) {
                const opts = Array.from(sel.options || []);
                let hit = opts.find(o => String(o.value || '').toUpperCase() === String(targetCountry || '').toUpperCase());
                if (!hit) {
                    hit = opts.find(o => {
                        const t = String(o.text || o.label || '').toLowerCase();
                        return keys.some(k => t.includes(k));
                    });
                }
                if (!hit) continue;
                sel.value = String(hit.value || '');
                sel.dispatchEvent(new Event('input', {bubbles: true}));
                sel.dispatchEvent(new Event('change', {bubbles: true}));
                const text = String(hit.text || hit.label || '').trim();
                return {ok: true, value: sel.value || '', text};
            }
            return {ok: false, value: '', text: ''};
        }""", target_country, labels)
        return bool((result or {}).get("ok"))

    log(f"[PayPal] 切换国家到 {desired_country} ...")
    country_switched = await _switch_paypal_country(desired_country, desired_labels)
    if not country_switched:
        log("[PayPal] ⚠️ 国家切换首轮未命中，将继续进入复检重试")

    # 国家切换后页面会重新渲染表单字段，必须等待足够时间
    log("[PayPal] 等待页面根据新国家重新加载表单...")
    await page.wait_for_timeout(5000)

    # 等待表单字段重新出现（国家切换后地址字段会重新渲染）
    try:
        await page.locator(
            'input[name*="phone" i], input[name*="telephone" i], input[id*="phone" i], input[id*="tel" i], '
            f'input[type="tel"], input[autocomplete="tel"], input[placeholder*="Phone" i], input[placeholder*="{phone_jp}"]'
        ).first.wait_for(state="visible", timeout=10000)
    except Exception:
        await page.wait_for_timeout(3000)

    # 验证国家是否切换成功
    current_country = await page.evaluate("""() => {
        const selects = Array.from(document.querySelectorAll('select'));
        for (const sel of selects) {
            if (sel.name.toLowerCase().includes('country') || sel.id.toLowerCase().includes('country') || sel.options.length > 50) {
                const idx = sel.selectedIndex || 0;
                const opt = sel.options && sel.options[idx];
                const text = opt ? String(opt.text || opt.label || '').trim() : '';
                return { value: sel.value || '', text };
            }
        }
        return { value: '', text: '' };
    }""")
    current_value = str((current_country or {}).get("value") or "")
    current_text = str((current_country or {}).get("text") or "").strip().lower()
    if desired_country == "JP":
        country_ok = current_value.upper() == "JP" or ("japan" in current_text) or ("日本" in current_text)
    else:
        country_ok = current_value.upper() == "US" or ("united states" in current_text) or ("美国" in current_text)

    if not country_ok:
        log(f"[PayPal] ⚠️ 国家仍为 value={current_value}, text={current_text}，再次尝试切换到 {desired_country}...")
        await _switch_paypal_country(desired_country, desired_labels)
        await page.wait_for_timeout(5000)
    else:
        log(f"[PayPal] ✓ 国家已确认切换为 {desired_country} (value={current_value}, text={current_text})")

    # 再次确认邮箱（国家切换后页面可能重置了邮箱字段）
    try:
        email_fields_after = page.locator(email_selectors)
        count_after = await email_fields_after.count()
        for i in range(count_after):
            field = email_fields_after.nth(i)
            if await field.is_visible(timeout=1000):
                val = (await field.input_value()).strip()
                if not val:
                    log("[PayPal] 邮箱字段为空，重新填写...")
                    await field.fill(email, timeout=3000)
                break
    except Exception:
        pass

    # 手机号
    phone_local = phone.number.lstrip("+1") if phone.number.startswith("+1") else phone.number.lstrip("+")
    try:
        phone_input = page.locator(
            'input[name*="phone" i], input[name*="telephone" i], input[id*="phone" i], input[id*="tel" i], '
            f'input[type="tel"], input[autocomplete="tel"], input[placeholder*="Phone" i], input[placeholder*="{phone_jp}"]'
        ).first
        await phone_input.fill("", timeout=2000)
        await phone_input.fill(phone_local, timeout=5000)
    except Exception:
        pass
    await page.wait_for_timeout(500)

    # 卡号
    try:
        card_input = page.locator('input[name="cardnumber"], input[id*="card" i], input[placeholder*="Card" i]').first
        await card_input.fill("", timeout=2000)
        await card_input.fill(card.number, timeout=5000)
    except Exception:
        pass
    await page.wait_for_timeout(500)

    # 有效期
    try:
        exp_input = page.locator('input[name="exp-date"], input[id*="exp" i], input[placeholder*="Expir" i]').first
        await exp_input.fill("", timeout=2000)
        await exp_input.fill(f"{card.exp_month}/{card.exp_year}", timeout=5000)
    except Exception:
        pass
    await page.wait_for_timeout(500)

    # CVV
    try:
        cvv_input = page.locator('input[name="cvv"], input[id*="cvv" i], input[placeholder*="CVV" i]').first
        await cvv_input.fill("", timeout=2000)
        await cvv_input.fill(card.cvv, timeout=5000)
    except Exception:
        pass
    await page.wait_for_timeout(500)

    # 地址（先清空再填写，避免残留旧值）
    # PayPal 页面字段可能用 placeholder 而非 name 属性，需要多种选择器兜底
    log("[PayPal] 填写地址信息...")
    jp_birth, jp_first_kana, jp_last_kana, jp_first_kanji, jp_last_kanji = _jp_identity_values(card, email)
    jp_first_kata = _hiragana_to_katakana(jp_first_kana)
    jp_last_kata = _hiragana_to_katakana(jp_last_kana)
    first_name_value = jp_first_kata if desired_country == "JP" else card.first_name
    last_name_value = jp_last_kata if desired_country == "JP" else card.last_name

    # First name
    try:
        loc = page.locator('input[name="fname"], input[id*="first" i], input[placeholder*="First" i], input[autocomplete="given-name"]').first
        if await loc.is_visible(timeout=2000):
            await loc.fill("", timeout=2000)
            await loc.fill(first_name_value, timeout=5000)
    except Exception:
        pass
    await page.wait_for_timeout(300)

    # Last name
    try:
        loc = page.locator('input[name="lname"], input[id*="last" i], input[placeholder*="Last" i], input[autocomplete="family-name"]').first
        if await loc.is_visible(timeout=2000):
            await loc.fill("", timeout=2000)
            await loc.fill(last_name_value, timeout=5000)
    except Exception:
        pass
    await page.wait_for_timeout(300)

    # Street address - PayPal 页面可能用浮动 label 而非 placeholder
    # 尝试多种方式定位 street 字段
    street_filled = False
    street_selectors = [
        'input[name*="street" i]',
        'input[name*="address" i]:not([name*="email" i])',
        'input[autocomplete="address-line1"]',
        'input[placeholder*="Street" i]',
        'input[placeholder*="地址" i]',
        'input[placeholder*="Address" i]',
        f'input[placeholder*="{address_jp}" i]',
        'input[aria-label*="Street" i]',
        'input[aria-label*="address" i]:not([aria-label*="email" i])',
        f'input[aria-label*="{address_jp}" i]',
        'input[id*="street" i]',
        'input[id*="address" i]:not([id*="email" i])',
    ]
    for sel in street_selectors:
        try:
            loc = page.locator(sel).first
            if await loc.is_visible(timeout=1500):
                await loc.fill("", timeout=2000)
                await loc.fill(card.street, timeout=5000)
                street_filled = True
                log(f"[PayPal] Street 已填 (选择器: {sel})")
                break
        except Exception:
            continue

    if not street_filled:
        # 最后兜底：通过 label 文本找到对应的 input
        log("[PayPal] Street 常规选择器均未命中，尝试通过 label 文本定位...")
        try:
            loc = page.get_by_label("Street address", exact=False).first
            if await loc.is_visible(timeout=2000):
                await loc.fill(card.street, timeout=5000)
                street_filled = True
        except Exception:
            pass
        if not street_filled:
            try:
                loc = page.get_by_label(re.compile(r"地址|\u4f4f\u6240|address|street", re.I)).first
                if await loc.is_visible(timeout=2000):
                    await loc.fill(card.street, timeout=5000)
                    street_filled = True
            except Exception:
                pass
        if not street_filled:
            # 终极兜底：找 Billing address 区域下第一个空的 text input（排除 first/last name）
            try:
                street_filled = await page.evaluate("""(street) => {
                    const inputs = Array.from(document.querySelectorAll('input[type="text"], input:not([type])'));
                    for (const inp of inputs) {
                        const rect = inp.getBoundingClientRect();
                        if (rect.width <= 0 || rect.height <= 0) continue;
                        const label = (inp.placeholder || inp.getAttribute('aria-label') || inp.name || inp.id || '').toLowerCase();
                        // 跳过已知字段
                        if (/first|last|city|zip|postal|phone|email|apt|suite|bldg/i.test(label)) continue;
                        // 跳过已有值的
                        if ((inp.value || '').trim()) continue;
                        // 这个可能就是 street
                        const proto = HTMLInputElement.prototype;
                        const desc = Object.getOwnPropertyDescriptor(proto, 'value');
                        desc?.set?.call(inp, street);
                        inp.dispatchEvent(new Event('input', {bubbles: true}));
                        inp.dispatchEvent(new Event('change', {bubbles: true}));
                        return true;
                    }
                    return false;
                }""", card.street)
            except Exception:
                pass
    if not street_filled:
        log("[PayPal] ⚠️ Street address 填写失败，所有选择器均未命中")
    await page.wait_for_timeout(300)

    # City
    try:
        loc = page.locator(
            'input[name="city"], input[name*="city" i], input[name*="locality" i], input[autocomplete="address-level2"], '
            f'input[placeholder*="City" i], input[placeholder*="城市" i], input[placeholder*="{city_jp}" i], '
            f'input[aria-label*="City" i], input[aria-label*="{city_jp}" i]'
        ).first
        if await loc.is_visible(timeout=3000):
            await loc.fill("", timeout=2000)
            await loc.fill(card.city, timeout=5000)
        else:
            raise Exception("not visible")
    except Exception:
        try:
            loc = page.get_by_placeholder("City").first
            await loc.fill(card.city, timeout=5000)
        except Exception:
            pass
    await page.wait_for_timeout(300)

    # ZIP code
    try:
        loc = page.locator(
            'input[name*="zip" i], input[name*="postal" i], input[autocomplete="postal-code"], '
            'input[placeholder*="ZIP" i], input[placeholder*="邮编" i], input[placeholder*="Postal" i], '
            f'input[placeholder*="{postal_jp}" i], input[aria-label*="{postal_jp}" i]'
        ).first
        if await loc.is_visible(timeout=2000):
            await loc.fill("", timeout=2000)
            await loc.fill(card.zip_code, timeout=5000)
    except Exception:
        pass
    await page.wait_for_timeout(300)

    async def _fill_paypal_jp_prefecture() -> tuple[bool, str]:
        pref_label = _JP_PREFECTURE_LABELS.get(card.state, card.state)
        pref_code = _JP_PAYPAL_PREFECTURE_CODES.get(card.state, "")
        pref_code_base = pref_code.rsplit("-", 1)[0] if "-" in pref_code else pref_code
        pref_keys = [pref_label, card.state, pref_code, pref_code_base, card.state.upper()]
        pref_keys = [x for x in pref_keys if x]

        # 1) 原生 select_option
        try:
            sel = page.locator(
                '#state, #province, '
                'select[name="state"], select[name*="state" i], select[name*="prefecture" i], '
                'select[name*="region" i], select[name*="province" i], '
                'select[id*="state" i], select[id*="prefecture" i], select[id*="region" i], select[id*="province" i], '
                'select[autocomplete="address-level1"], select[aria-label*="都道府県"], select[aria-label*="Prefecture" i]'
            ).first
            if await sel.is_visible(timeout=1400):
                for key in pref_keys:
                    try:
                        await sel.select_option(value=key, timeout=1500)
                        return True, f"select:value:{key}"
                    except Exception:
                        pass
                    try:
                        await sel.select_option(label=key, timeout=1500)
                        return True, f"select:label:{key}"
                    except Exception:
                        continue
        except Exception:
            pass

        # 2) 组合下拉（都道府県）
        try:
            result = await page.evaluate(
                """(payload) => {
                    const keys = (payload.keys || []).map(x => String(x || '').trim()).filter(Boolean);
                    const keysN = keys.map(x => x.toLowerCase().replace(/[\\s\\-ー—‐－]/g, ''));
                    const isVisible = (el) => {
                        if (!el) return false;
                        const r = el.getBoundingClientRect();
                        if (r.width < 8 || r.height < 8) return false;
                        const st = window.getComputedStyle(el);
                        if (!st) return false;
                        return st.display !== 'none' && st.visibility !== 'hidden' && Number(st.opacity || '1') > 0.05;
                    };
                    const norm = (s) => String(s || '').toLowerCase().replace(/[\\s\\-ー—‐－]/g, '');
                    const textOf = (el) => String(el?.innerText || el?.textContent || '').trim();
                    const hasKey = (s) => {
                        const t = norm(s);
                        return keysN.some(k => t.includes(k));
                    };
                    const clickEl = (el) => {
                        if (!el) return false;
                        try { el.click(); return true; } catch {}
                        return false;
                    };

                    const controls = Array.from(document.querySelectorAll(
                        'select,input,button,[role="combobox"],[aria-haspopup="listbox"],div'
                    )).filter(isVisible).filter(el => {
                        const hint = [
                            el.id || '', el.name || '', el.getAttribute('aria-label') || '',
                            el.getAttribute('placeholder') || '', el.getAttribute('data-testid') || '', textOf(el),
                        ].join(' ').toLowerCase();
                        return /都道府県|prefecture|state|province|region|administrative/.test(hint);
                    });
                    if (!controls.length) return { ok: false, reason: 'no_control' };

                    for (const ctl of controls) {
                        const tag = String(ctl.tagName || '').toLowerCase();
                        if (tag === 'select') {
                            const opts = Array.from(ctl.options || []);
                            let hit = opts.find(o => hasKey(o.value || ''));
                            if (!hit) hit = opts.find(o => hasKey(o.text || o.label || ''));
                            if (!hit) continue;
                            ctl.value = String(hit.value || '');
                            ctl.dispatchEvent(new Event('input', { bubbles: true }));
                            ctl.dispatchEvent(new Event('change', { bubbles: true }));
                            return { ok: true, reason: 'select_set' };
                        }

                        clickEl(ctl);
                        const options = Array.from(document.querySelectorAll(
                            '[role="option"], [role="listbox"] li, li[role="option"], div[role="option"], ul li, button'
                        )).filter(isVisible).filter(el => {
                            const t = textOf(el);
                            if (!t || t.length > 40) return false;
                            return hasKey(t);
                        });
                        if (options.length) {
                            clickEl(options[0]);
                            return { ok: true, reason: 'option_click' };
                        }
                    }
                    return { ok: false, reason: 'no_option' };
                }""",
                {"keys": pref_keys},
            )
            if bool((result or {}).get("ok")):
                return True, str((result or {}).get("reason") or "combo_ok")
            return False, str((result or {}).get("reason") or "combo_fail")
        except Exception as exc:
            return False, f"exception:{exc}"

    # State/Prefecture（JP 下优先填都道府県）
    if desired_country == "JP":
        ok, why = await _fill_paypal_jp_prefecture()
        log(f"[PayPal][JP] 都道府県填充: ok={ok} reason={why} target={_JP_PREFECTURE_LABELS.get(card.state, card.state)}")
    else:
        try:
            state_sel = page.locator('select[name="state"], select[name*="state" i], select[autocomplete="address-level1"], select[aria-label*="State" i]').first
            if await state_sel.is_visible(timeout=3000):
                try:
                    await state_sel.select_option(value=card.state, timeout=5000)
                except Exception:
                    try:
                        await state_sel.select_option(label=card.state, timeout=3000)
                    except Exception:
                        pass
            else:
                state_input = page.locator('input[name*="state" i], input[placeholder*="State" i], input[autocomplete="address-level1"]').first
                if await state_input.is_visible(timeout=2000):
                    await state_input.fill("", timeout=2000)
                    await state_input.fill(card.state, timeout=5000)
        except Exception:
            pass
    await page.wait_for_timeout(500)

    # 密码
    try:
        pwd_input = page.locator('input[name="password"], input[type="password"]').first
        if await pwd_input.is_visible(timeout=3000):
            await pwd_input.fill("", timeout=2000)
            await pwd_input.fill(paypal_password, timeout=5000)
    except Exception:
        pass
    await page.wait_for_timeout(1000)

    # 日本实名页会额外要求生日 + かな + 漢字姓名
    if desired_country == "JP":
        await _fill_paypal_jp_identity(page, email=email, card=card)
        await page.wait_for_timeout(600)
        # 日本页经常要求“名/姓”为假名；若页面报错则强制改写一次
        try:
            kana_name_invalid = await page.evaluate("""() => {
                const text = (document.body?.innerText || '');
                return /ひらがなまたはカタカナ/.test(text);
            }""")
            if kana_name_invalid:
                log("[PayPal][JP] 检测到姓名假名校验错误，强制重填 名/姓 为假名")
                await page.evaluate(
                    """(payload) => {
                        const first = String(payload.first || '');
                        const last = String(payload.last || '');
                        const isVisible = (el) => {
                            if (!el) return false;
                            const r = el.getBoundingClientRect();
                            if (r.width < 8 || r.height < 8) return false;
                            const st = window.getComputedStyle(el);
                            if (!st) return false;
                            return st.display !== 'none' && st.visibility !== 'hidden' && Number(st.opacity || '1') > 0.05;
                        };
                        const setVal = (el, v) => {
                            if (!el) return false;
                            const proto = HTMLInputElement.prototype;
                            const desc = Object.getOwnPropertyDescriptor(proto, 'value');
                            if (desc && typeof desc.set === 'function') desc.set.call(el, v);
                            else el.value = v;
                            el.dispatchEvent(new Event('input', { bubbles: true }));
                            el.dispatchEvent(new Event('change', { bubbles: true }));
                            el.dispatchEvent(new Event('blur', { bubbles: true }));
                            return true;
                        };
                        const sig = (el) => {
                            const p = String(el.placeholder || '').toLowerCase();
                            const a = String(el.getAttribute('aria-label') || '').toLowerCase();
                            const n = String(el.name || '').toLowerCase();
                            const i = String(el.id || '').toLowerCase();
                            const t = String(el.closest('label')?.innerText || el.parentElement?.innerText || '').toLowerCase();
                            return `${p} ${a} ${n} ${i} ${t}`;
                        };
                        const inputs = Array.from(document.querySelectorAll('input')).filter(isVisible);
                        let firstDone = false;
                        let lastDone = false;
                        for (const el of inputs) {
                            const s = sig(el);
                            if (!firstDone && /(\\b名\\b|first|given)/.test(s)) firstDone = setVal(el, first) || firstDone;
                            if (!lastDone && /(\\b姓\\b|last|family)/.test(s)) lastDone = setVal(el, last) || lastDone;
                        }
                        return { firstDone, lastDone };
                    }""",
                    {"first": jp_first_kata, "last": jp_last_kata},
                )
                await page.wait_for_timeout(500)
        except Exception:
            pass

    # 最终检查：确认所有关键字段已填写
    log("[PayPal] 检查表单完整性...")
    empty_fields = await page.evaluate("""() => {
        const checks = [
            {name: 'email', selectors: 'input[name="email"], input[type="email"]'},
            {name: 'phone', selectors: 'input[name*="phone" i], input[id*="phone" i]'},
            {name: 'card', selectors: 'input[name="cardnumber"], input[id*="card" i]'},
            {name: 'firstName', selectors: 'input[name="fname"], input[id*="first" i], input[autocomplete="given-name"], input[placeholder*="First" i]'},
            {name: 'lastName', selectors: 'input[name="lname"], input[id*="last" i], input[autocomplete="family-name"], input[placeholder*="Last" i]'},
            {name: 'street', selectors: 'input[name*="street" i], input[name*="address" i], input[autocomplete="address-line1"], input[placeholder*="Street" i]'},
            {name: 'city', selectors: 'input[name="city"], input[name*="city" i], input[autocomplete="address-level2"], input[placeholder*="City" i]'},
            {name: 'zip', selectors: 'input[name*="zip" i], input[name*="postal" i], input[autocomplete="postal-code"], input[placeholder*="ZIP" i]'},
            {name: 'prefecture', selectors: 'select[name*="state" i], select[name*="prefecture" i], select[name*="region" i], input[name*="state" i], input[aria-label*="都道府県"], [role="combobox"][aria-label*="都道府県"]'},
        ];
        const empty = [];
        for (const {name, selectors} of checks) {
            const els = document.querySelectorAll(selectors);
            let found = false;
            for (const el of els) {
                const rect = el.getBoundingClientRect();
                if (rect.width > 0 && rect.height > 0) {
                    if ((el.value || '').trim()) {
                        found = true;
                    }
                    break;
                }
            }
            if (!found) empty.push(name);
        }
        return empty;
    }""")
    if empty_fields:
        log(f"[PayPal] ⚠️ 以下字段仍为空: {empty_fields}，尝试补填...")
        # 补填邮箱
        if "email" in empty_fields:
            try:
                await page.locator('input[name="email"], input[type="email"]').first.fill(email, timeout=3000)
            except Exception:
                pass
        # 补填手机号
        if "phone" in empty_fields:
            try:
                await page.locator('input[name*="phone" i], input[id*="phone" i]').first.fill(phone_local, timeout=3000)
            except Exception:
                pass
        # 补填 street
        if "street" in empty_fields:
            try:
                loc = page.locator('input[placeholder*="Street" i], input[name*="street" i], input[name*="address" i]:not([name*="email" i]), input[aria-label*="Street" i], input[id*="street" i], input[id*="address" i]:not([id*="email" i])').first
                if await loc.is_visible(timeout=2000):
                    await loc.fill(card.street, timeout=3000)
                else:
                    loc = page.get_by_label("Street address", exact=False).first
                    await loc.fill(card.street, timeout=3000)
            except Exception:
                # 终极兜底
                await page.evaluate("""(street) => {
                    const inputs = Array.from(document.querySelectorAll('input[type="text"], input:not([type])'));
                    for (const inp of inputs) {
                        const rect = inp.getBoundingClientRect();
                        if (rect.width <= 0 || rect.height <= 0) continue;
                        const label = (inp.placeholder || inp.getAttribute('aria-label') || inp.name || inp.id || '').toLowerCase();
                        if (/first|last|city|zip|postal|phone|email|apt|suite|bldg/i.test(label)) continue;
                        if ((inp.value || '').trim()) continue;
                        const proto = HTMLInputElement.prototype;
                        const desc = Object.getOwnPropertyDescriptor(proto, 'value');
                        desc?.set?.call(inp, street);
                        inp.dispatchEvent(new Event('input', {bubbles: true}));
                        inp.dispatchEvent(new Event('change', {bubbles: true}));
                        return;
                    }
                }""", card.street)
        # 补填 city
        if "city" in empty_fields:
            try:
                loc = page.locator('input[placeholder*="City" i], input[name*="city" i]').first
                await loc.fill(card.city, timeout=3000)
            except Exception:
                pass
        if "prefecture" in empty_fields and desired_country == "JP":
            try:
                ok, why = await _fill_paypal_jp_prefecture()
                log(f"[PayPal][JP] 都道府県补填: ok={ok} reason={why}")
            except Exception:
                pass
        await page.wait_for_timeout(1000)

    async def _refill_card_fields(c: CardInfo) -> None:
        try:
            card_input = page.locator('input[name="cardnumber"], input[id*="card" i], input[placeholder*="Card" i]').first
            await card_input.fill("", timeout=2000)
            await card_input.fill(c.number, timeout=5000)
        except Exception:
            pass
        await page.wait_for_timeout(300)
        try:
            exp_input = page.locator('input[name="exp-date"], input[id*="exp" i], input[placeholder*="Expir" i]').first
            await exp_input.fill("", timeout=2000)
            await exp_input.fill(f"{c.exp_month}/{c.exp_year}", timeout=5000)
        except Exception:
            pass
        await page.wait_for_timeout(300)
        try:
            cvv_input = page.locator('input[name="cvv"], input[id*="cvv" i], input[placeholder*="CVV" i]').first
            await cvv_input.fill("", timeout=2000)
            await cvv_input.fill(c.cvv, timeout=5000)
        except Exception:
            pass
        await page.wait_for_timeout(300)

    async def _is_card_rejected() -> bool:
        try:
            body_text = ""
            try:
                body_text = await page.locator("body").inner_text(timeout=2500)
            except Exception:
                body_text = ""
            normalized = (body_text or "").replace("’", "'").lower()
            if any(
                k in normalized
                for k in (
                    "we weren't able to add this card",
                    "check all the details are correct",
                    "try a different card",
                    "unable to add this card",
                    "无法添加此卡",
                    "添加此卡失败",
                    "请尝试其他卡",
                )
            ):
                return True

            # 文本抓取失败或页面分段渲染时，补一层可见错误提示定位。
            err = page.locator(
                "text=We weren’t able to add this card, "
                "text=We weren't able to add this card, "
                "text=try a different card, "
                "text=无法添加此卡, "
                "text=请尝试其他卡"
            ).first
            if await err.is_visible(timeout=600):
                return True
            return False
        except Exception:
            return False

    # Agree & Create Account；如遇卡被拒，自动生成新卡并重试一次
    env = load_env(".env")
    max_regen = 1
    try:
        max_regen = max(0, int((env.get("PAYPAL_CARD_REGEN_ON_DECLINE") or "1").strip() or "1"))
    except Exception:
        max_regen = 1
    working_card = card
    for regen_idx in range(max_regen + 1):
        create_btn = page.locator(
            'button[type="submit"], input[type="submit"], button:has-text("Agree"), button:has-text("Create Account"), '
            f'button:has-text("Continue"), button:has-text("{agree_create_jp}"), button:has-text("{create_account_jp}"), '
            f'button:has-text("{agree_continue_jp}"), button:has-text("{continue_jp}")'
        ).first
        try:
            await create_btn.click(timeout=10000)
        except Exception as exc:
            if _looks_like_captcha_pointer_block(exc):
                log("[PayPal] 创建账号按钮被 CAPTCHA 遮挡，处理后重试点击")
                removed = await _cleanup_hosted_captcha_artifacts(page, timeout_ms=2500)
                if removed:
                    log(f"[PayPal] 已快速清理 hosted captcha 遮挡元素 {removed} 个，立即重试点击")
                try:
                    await create_btn.click(timeout=3000)
                except Exception as retry_exc:
                    if not _looks_like_captcha_pointer_block(retry_exc):
                        raise
                    await handle_paypal_captcha(page, solver_proxy=proxy, force=True)
                    await _wait_captcha_cleared(page, timeout_seconds=10)
                    await create_btn.click(timeout=5000)
            else:
                raise
        await page.wait_for_timeout(2500)

        # 检测并处理人机验证码（PayPal 安全问题 / CAPTCHA）
        await handle_paypal_captcha(page, solver_proxy=proxy)
        await page.wait_for_timeout(1200)

        rejected = False
        for _ in range(6):
            if await _is_paypal_verification_stage(page):
                break
            if await _is_card_rejected():
                rejected = True
                break
            await page.wait_for_timeout(800)
        if rejected:
            if regen_idx >= max_regen:
                raise RuntimeError("银行卡被拒（We weren’t able to add this card），且已达到自动换卡上限")
            new_card = _generate_local_random_card(
                int(time.time() * 1000) + regen_idx + random.randint(1, 9999),
                email,
                env,
                region_mode="jp" if str(country_code or "").upper() == "JP" else "default",
            )
            log(f"[PayPal] 检测到卡被拒，自动生成新卡重试 ({regen_idx + 1}/{max_regen})")
            working_card = new_card
            await _refill_card_fields(working_card)
            continue
        break


async def handle_paypal_captcha(page, timeout_seconds: int = 180, solver_proxy: str | None = None, force: bool = False) -> None:
    """检测 PayPal 人机验证码并处理。

    检测到 hosted checkout 的遮挡层时，先清理页面上的 captcha artifact。
    PAYPAL_CAPTCHA_MODE=api 时优先走打码平台，失败后回退人工完成。
    """
    await _remove_hosted_captcha_artifacts(page)
    # 检测是否有验证码弹窗（避免 v3 eval 误判）
    has_captcha, reason = await _detect_captcha_signal(page)
    if not has_captcha and not force:
        return
    if not has_captcha and force:
        has_any_frame = await _has_any_captcha_frame(page)
        if not has_any_frame:
            return
        reason = "force_by_timeout"
        has_captcha = True
    # 连续两次确认，避免“挑战未完全加载”就误触发打码
    await page.wait_for_timeout(1200)
    has_captcha_2, reason_2 = await _detect_captcha_signal(page)
    if not has_captcha_2:
        # force 模式下，如果 iframe 仍在，继续解码而不是直接跳过
        if force and await _has_any_captcha_frame(page):
            has_captcha_2 = True
            reason_2 = "force_frame_present"
        else:
            log(f"[PayPal] CAPTCHA 预检未稳定（首次={reason}, 二次={reason_2}），跳过本次自动解码")
            return

    log(f"[PayPal] ⚠️ 检测到人机验证码（CAPTCHA），需要处理... reason={reason_2 or reason}")

    removed = await _cleanup_hosted_captcha_artifacts(page, timeout_ms=15000)
    if removed:
        log(f"[PayPal] 已清理 hosted captcha 遮挡元素 {removed} 个，继续检测页面状态")
        await page.wait_for_timeout(800)
        if not await _detect_captcha(page):
            log("[PayPal] CAPTCHA 遮挡元素已移除，继续流程")
            return

    env = load_env(".env")
    timeout_value = int(env.get("PAYPAL_CAPTCHA_TIMEOUT") or timeout_seconds or 180)
    captcha_mode = (env.get("PAYPAL_CAPTCHA_MODE") or "manual").strip().lower()
    if captcha_mode == "api":
        try:
            await _solve_captcha_via_api(page, env, solver_proxy=solver_proxy)
            if await _wait_captcha_cleared(page, timeout_seconds=30):
                log("[PayPal] CAPTCHA 自动处理完成")
                return
            log("[PayPal] CAPTCHA 自动处理后仍未清除，回退人工处理")
        except Exception as exc:
            log(f"[PayPal] CAPTCHA 自动处理失败，回退人工处理: {exc}")

    await _wait_captcha_manual(page, timeout_value)


async def _detect_captcha(page) -> bool:
    """检测页面是否出现了验证码弹窗。"""
    await _remove_hosted_captcha_artifacts(page)
    ok, _ = await _detect_captcha_signal(page)
    return ok


async def _remove_hosted_captcha_artifacts(page) -> int:
    """移除 PayPal hosted checkout 上会遮挡按钮的 captcha 容器。"""
    script = """() => {
        let removed = 0;
        const selectors = [
            '#captcha-standalone',
            '.captcha-overlay',
            '.captcha-container',
        ];
        for (const selector of selectors) {
            for (const node of Array.from(document.querySelectorAll(selector))) {
                try {
                    node.remove();
                    removed += 1;
                } catch {}
            }
        }
        return removed;
    }"""
    total = 0
    targets = [page]
    try:
        targets.extend([fr for fr in page.frames if fr is not page.main_frame])
    except Exception:
        pass
    for target in targets:
        try:
            total += int(await target.evaluate(script) or 0)
        except Exception:
            continue
    return total


async def _cleanup_hosted_captcha_artifacts(page, timeout_ms: int = 15000) -> int:
    """短时间持续清理新插入的 hosted captcha artifact。"""
    deadline = time.monotonic() + max(1.0, timeout_ms / 1000)
    total = 0
    while time.monotonic() < deadline:
        total += await _remove_hosted_captcha_artifacts(page)
        await page.wait_for_timeout(300)
        if not await _has_hosted_captcha_artifact(page):
            break
    return total


async def _has_hosted_captcha_artifact(page) -> bool:
    script = """() => !!document.querySelector('#captcha-standalone, .captcha-overlay, .captcha-container')"""
    targets = [page]
    try:
        targets.extend([fr for fr in page.frames if fr is not page.main_frame])
    except Exception:
        pass
    for target in targets:
        try:
            if await target.evaluate(script):
                return True
        except Exception:
            continue
    return False


async def _detect_captcha_signal(page) -> tuple[bool, str]:
    """返回 (是否应触发解码, 原因)。"""
    try:
        result = await page.evaluate("""() => {
            const isVisible = (el) => {
                if (!el) return false;
                const rect = el.getBoundingClientRect();
                if (rect.width < 20 || rect.height < 20) return false;
                const style = window.getComputedStyle(el);
                if (!style) return false;
                if (style.display === 'none' || style.visibility === 'hidden') return false;
                if (Number(style.opacity || '1') < 0.05) return false;
                return true;
            };

            const text = (document.body?.innerText || '').replace(/\\s+/g, ' ');
            // PayPal 验证码特征
            const hasCaptchaText = /安全问题|security challenge|请选择包含|select all images|人行横道|crosswalk|traffic light|bus|bicycle|i'?m not a robot|robot check/i.test(text);

            const frames = Array.from(document.querySelectorAll('iframe'));
            const frameInfos = frames.map((f) => {
                const src = (f.getAttribute('src') || '').toLowerCase();
                const title = (f.getAttribute('title') || '').toLowerCase();
                const rect = f.getBoundingClientRect();
                return { src, title, w: rect.width, h: rect.height, visible: isVisible(f) };
            });

            const hasVisibleV2OrChallengeFrame = frameInfos.some((x) => {
                if (!x.visible) return false;
                const isV3Eval = /recaptcha_v3|source=recaptchav3eval/.test(x.src);
                if (isV3Eval) return false;
                return /api2\\/bframe|api2\\/anchor|recaptcha_v2|hcaptcha|challenge/.test(x.src + ' ' + x.title) && x.w >= 140 && x.h >= 60;
            });

            const hasVisiblePaypalChallenge = Array.from(
                document.querySelectorAll('[data-testid="captcha"], .captcha-container, #captcha, [class*="challenge" i], [class*="captcha" i]')
            ).some((el) => isVisible(el));

            const recaptchaFrames = frameInfos.filter((x) => /recaptcha/.test(x.src));
            const onlyV3EvalFrames =
                recaptchaFrames.length > 0 &&
                recaptchaFrames.every((x) => /recaptcha_v3|source=recaptchav3eval/.test(x.src));

            let reason = 'none';
            if (hasVisibleV2OrChallengeFrame) reason = 'visible_challenge_frame';
            else if (hasVisiblePaypalChallenge) reason = 'visible_paypal_challenge';
            else if (hasCaptchaText && !onlyV3EvalFrames) reason = 'captcha_text';
            else if (onlyV3EvalFrames) reason = 'v3_eval_only';

            const shouldSolve =
                hasVisibleV2OrChallengeFrame ||
                hasVisiblePaypalChallenge ||
                (hasCaptchaText && !onlyV3EvalFrames);

            return { shouldSolve, reason };
        }""")
        if isinstance(result, dict):
            return bool(result.get("shouldSolve")), str(result.get("reason") or "")
        return bool(result), ""
    except Exception:
        return False, "detect_error"


async def _has_any_captcha_frame(page) -> bool:
    """宽松检测：只要页面存在 captcha 相关 iframe 就返回 True（用于超时兜底）。"""
    try:
        return bool(await page.evaluate("""() => {
            return !!document.querySelector('iframe[src*="recaptcha"], iframe[src*="hcaptcha"], iframe[src*="captcha"], iframe[title*="challenge" i]');
        }"""))
    except Exception:
        return False


def _looks_like_captcha_pointer_block(exc: Exception | str) -> bool:
    text = str(exc or "").lower()
    return ("intercepts pointer events" in text) and (
        "recaptcha" in text or "hcaptcha" in text or "captcha" in text
    )


async def _fill_visible_tel_inputs_direct(target, code: str) -> bool:
    """不依赖 click，直接向可见 tel 输入框写入验证码。target 可是 page 或 frame。"""
    try:
        mode = await target.evaluate(
            """(code) => {
                const isVisible = (el) => {
                    if (!el) return false;
                    const r = el.getBoundingClientRect();
                    if (r.width < 8 || r.height < 8) return false;
                    const st = window.getComputedStyle(el);
                    if (!st) return false;
                    if (st.display === 'none' || st.visibility === 'hidden') return false;
                    if (Number(st.opacity || '1') < 0.05) return false;
                    return true;
                };
                const setVal = (el, v) => {
                    const proto = HTMLInputElement.prototype;
                    const desc = Object.getOwnPropertyDescriptor(proto, 'value');
                    if (desc && typeof desc.set === 'function') desc.set.call(el, v);
                    else el.value = v;
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                };
                const inputs = Array.from(document.querySelectorAll('input[type="tel"]')).filter(isVisible);
                if (!inputs.length) return "";

                // 单输入框：整串写入
                if (inputs.length === 1) {
                    setVal(inputs[0], String(code));
                    inputs[0].focus();
                    return "single_direct";
                }
                // 多输入格：逐位写入
                const digits = String(code).replace(/\\D/g, "").slice(0, inputs.length).split("");
                if (!digits.length) return "";
                for (let i = 0; i < digits.length; i++) {
                    setVal(inputs[i], digits[i]);
                }
                inputs[Math.min(digits.length - 1, inputs.length - 1)].focus();
                return "multi_direct";
            }""",
            str(code),
        )
        return bool(mode)
    except Exception:
        return False


async def _fill_paypal_otp_inputs_direct(target, code: str) -> bool:
    """匹配 PayPal OTP 输入框并直填；支持 ciBasic 与几何分组兜底。"""
    try:
        mode = await target.evaluate(
            """(rawCode) => {
                const code = String(rawCode || '').replace(/\\D/g, '');
                if (!code) return '';
                const isVisible = (el) => {
                    if (!el) return false;
                    const r = el.getBoundingClientRect();
                    if (r.width < 8 || r.height < 8) return false;
                    const st = window.getComputedStyle(el);
                    if (!st) return false;
                    if (st.display === 'none' || st.visibility === 'hidden') return false;
                    if (Number(st.opacity || '1') < 0.05) return false;
                    return true;
                };
                const setVal = (el, v) => {
                    const proto = HTMLInputElement.prototype;
                    const desc = Object.getOwnPropertyDescriptor(proto, 'value');
                    if (desc && typeof desc.set === 'function') desc.set.call(el, v);
                    else el.value = v;
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    el.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: v }));
                };
                const idx = (el) => {
                    const id = String(el.id || '');
                    const name = String(el.name || '');
                    const m = id.match(/ciBasic-(\\d+)$/i) || name.match(/ciBasic-(\\d+)$/i);
                    return m ? Number(m[1]) : 9999;
                };
                const looksOtp = (el) => {
                    const id = String(el.id || '').toLowerCase();
                    const name = String(el.name || '').toLowerCase();
                    const cls = String(el.className || '').toLowerCase();
                    const aria = String(el.getAttribute('aria-label') || '').toLowerCase();
                    const ac = String(el.getAttribute('autocomplete') || '').toLowerCase();
                    return id.includes('cibasic') || name.includes('cibasic') ||
                        cls.includes('code_input') || ac.includes('one-time-code') ||
                        /\\b1\\s*-\\s*6\\b/.test(aria) || aria.includes('code');
                };
                let inputs = Array.from(
                    document.querySelectorAll('input[type="tel"][id*="ciBasic" i], input[type="tel"][name*="ciBasic" i]')
                ).filter(isVisible);
                if (inputs.length < 6) {
                    const all = Array.from(document.querySelectorAll('input[type="tel"], input[inputmode="numeric"], input[autocomplete="one-time-code"]'))
                        .filter(isVisible)
                        .map((el) => ({ el, r: el.getBoundingClientRect(), otp: looksOtp(el) }))
                        .filter((x) => x.r.width >= 20 && x.r.width <= 120 && x.r.height >= 25 && x.r.height <= 90);

                    const groups = new Map();
                    for (const item of all) {
                        const key = String(Math.round(item.r.top / 12) * 12);
                        if (!groups.has(key)) groups.set(key, []);
                        groups.get(key).push(item);
                    }

                    let best = [];
                    for (const group of groups.values()) {
                        const sorted = group.sort((a, b) => a.r.left - b.r.left);
                        const otpScore = sorted.filter((x) => x.otp).length;
                        if (sorted.length >= 6 && (otpScore >= 2 || sorted.every((x) => x.r.width <= 80))) {
                            if (sorted.length > best.length || otpScore > best.filter((x) => x.otp).length) {
                                best = sorted;
                            }
                        }
                    }
                    inputs = best.slice(0, 6).map((x) => x.el);
                }
                inputs.sort((a, b) => idx(a) - idx(b));
                // 只在 OTP 输入框数量足够时启用，避免误命中少量非 OTP tel 框导致“部分填入”
                if (inputs.length < 6) return '';
                if (idx(inputs[0]) === 9999) {
                    inputs.sort((a, b) => a.getBoundingClientRect().left - b.getBoundingClientRect().left);
                }
                const digits = code.slice(0, 6).split('');
                if (digits.length < 6) return '';
                for (let i = 0; i < 6; i++) setVal(inputs[i], digits[i]);

                // 回读校验：必须 6 位都写入，才算成功
                const compact = inputs.slice(0, 6).map((el) => String(el.value || '')).join('');
                if (compact.length < 6) return '';
                return `paypal_otp_6`;
            }""",
            str(code),
        )
        return bool(mode)
    except Exception:
        return False


async def _wait_captcha_manual(page, timeout_seconds: int = 180) -> None:
    """方案1：暂停等待手动处理验证码。

    检测到验证码后每 3 秒检查一次是否已消失，最多等待 timeout_seconds 秒。
    用户手动完成验证码后脚本自动继续。
    """
    log(f"[PayPal] 🖐️ 请手动完成验证码！等待最多 {timeout_seconds} 秒...")
    log("[PayPal] 完成验证码后脚本会自动继续")

    start = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start < timeout_seconds:
        removed = await _remove_hosted_captcha_artifacts(page)
        if removed:
            log(f"[PayPal] 人工等待期间已清理 hosted captcha 遮挡元素 {removed} 个")
        await page.wait_for_timeout(3000)
        # 检查验证码是否已消失
        still_has = await _detect_captcha(page)
        if not still_has:
            log("[PayPal] ✓ 验证码已完成，继续流程")
            await page.wait_for_timeout(2000)
            return
        # 检查是否已经跳转到下一页（验证码通过后可能直接跳转）
        try:
            body_text = await page.evaluate("() => (document.body?.innerText || '').slice(0, 500)")
            if "code" in body_text.lower() or "验证码" in body_text or "verify" in body_text.lower():
                log("[PayPal] ✓ 页面已跳转到验证码/下一步页面，继续流程")
                return
        except Exception:
            # 页面可能在导航
            await page.wait_for_timeout(2000)
            return

    log("[PayPal] ⚠️ 验证码等待超时，继续尝试...")


async def _inject_recaptcha_token(page, token: str) -> None:
    """向页面注入 reCAPTCHA token，并尽可能触发回调。"""
    inject_js = """(token) => {
        const touch = (el) => {
            if (!el) return;
            el.value = token;
            el.innerHTML = token;
            try { el.dispatchEvent(new Event('input', { bubbles: true })); } catch {}
            try { el.dispatchEvent(new Event('change', { bubbles: true })); } catch {}
        };

        const ensureField = (selector, id, name) => {
            let el = document.querySelector(selector);
            if (!el) {
                el = document.createElement('textarea');
                if (id) el.id = id;
                if (name) el.name = name;
                el.style.display = 'none';
                document.body.appendChild(el);
            }
            touch(el);
            return el;
        };

        ensureField('#g-recaptcha-response', 'g-recaptcha-response', 'g-recaptcha-response');
        ensureField('textarea[name="g-recaptcha-response"]', '', 'g-recaptcha-response');
        ensureField('textarea[name="g-recaptcha-response-100000"]', '', 'g-recaptcha-response-100000');

        for (const el of Array.from(document.querySelectorAll('[name*="captcha-response" i], textarea[id*="captcha" i]'))) {
            touch(el);
        }

        for (const cbEl of Array.from(document.querySelectorAll('[data-callback]'))) {
            const cb = cbEl.getAttribute('data-callback');
            if (!cb) continue;
            const fn = window[cb];
            if (typeof fn === 'function') {
                try { fn(token); } catch {}
            }
        }

        if (window.___grecaptcha_cfg && window.___grecaptcha_cfg.clients) {
            const clients = window.___grecaptcha_cfg.clients;
            for (const cid of Object.keys(clients)) {
                const stack = [clients[cid]];
                while (stack.length) {
                    const obj = stack.pop();
                    if (!obj || typeof obj !== 'object') continue;
                    for (const k of Object.keys(obj)) {
                        const v = obj[k];
                        if (typeof v === 'function' && /callback/i.test(k)) {
                            try { v(token); } catch {}
                        } else if (v && typeof v === 'object') {
                            stack.push(v);
                        }
                    }
                }
            }
        }
    }"""

    await page.evaluate(inject_js, token)
    for frame in page.frames:
        try:
            u = (frame.url or "").lower()
            if "recaptcha" in u or "paypal.com" in u:
                await frame.evaluate(inject_js, token)
        except Exception:
            pass


async def _inject_hcaptcha_token(page, token: str) -> None:
    """向页面注入 hCaptcha token，并尽可能触发回调。"""
    inject_js = """(token) => {
        const touch = (el) => {
            if (!el) return;
            el.value = token;
            el.innerHTML = token;
            try { el.dispatchEvent(new Event('input', { bubbles: true })); } catch {}
            try { el.dispatchEvent(new Event('change', { bubbles: true })); } catch {}
        };

        const ensureField = (selector, name) => {
            let el = document.querySelector(selector);
            if (!el) {
                el = document.createElement('textarea');
                if (name) el.name = name;
                el.style.display = 'none';
                document.body.appendChild(el);
            }
            touch(el);
        };

        ensureField('textarea[name="h-captcha-response"]', 'h-captcha-response');
        ensureField('textarea[name="g-recaptcha-response"]', 'g-recaptcha-response');
        for (const el of Array.from(document.querySelectorAll('[name*="captcha-response" i], textarea[id*="captcha" i]'))) {
            touch(el);
        }
        for (const cbEl of Array.from(document.querySelectorAll('[data-callback]'))) {
            const cb = cbEl.getAttribute('data-callback');
            if (!cb) continue;
            const fn = window[cb];
            if (typeof fn === 'function') {
                try { fn(token); } catch {}
            }
        }
        if (window.hcaptcha && typeof window.hcaptcha.execute === 'function') {
            try { window.hcaptcha.execute(); } catch {}
        }
    }"""

    await page.evaluate(inject_js, token)
    for frame in page.frames:
        try:
            u = (frame.url or "").lower()
            if "hcaptcha" in u or "paypal.com" in u:
                await frame.evaluate(inject_js, token)
        except Exception:
            pass


async def _wait_captcha_cleared(page, timeout_seconds: int = 45) -> bool:
    """等待验证码真正消失，或页面进入下一步（短信/验证页）。"""
    start = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start < timeout_seconds:
        await _remove_hosted_captcha_artifacts(page)
        await page.wait_for_timeout(1500)
        if not await _detect_captcha(page):
            return True
        try:
            body = await page.locator("body").inner_text(timeout=1500)
            low = body.lower()
            if ("verification code" in low) or ("enter code" in low) or ("验证码" in body):
                return True
        except Exception:
            # 页面导航瞬间读取失败也视作可能前进，继续下一轮
            pass
    return False


async def _is_paypal_verification_stage(page) -> bool:
    """判断是否已进入 PayPal 短信验证码阶段（用于避免残留 iframe 误判）。"""
    try:
        body = await page.locator("body").inner_text(timeout=1500)
        low = body.lower()
        markers = (
            "verification code",
            "enter code",
            "security code",
            "one-time code",
            "text message",
            "sms code",
            "验证码",
            "認証コード",
            "コードを入力",
            "コードを入力する",
            "送信しました",
            "再送",
        )
        if any(m in low for m in markers):
            return True
    except Exception:
        pass

    try:
        if await page.locator('input[type="tel"]:visible').count() > 0:
            return True
    except Exception:
        pass

    try:
        for fr in page.frames:
            if fr is page.main_frame:
                continue
            try:
                if await fr.locator('input[type="tel"]:visible').count() > 0:
                    return True
            except Exception:
                continue
    except Exception:
        pass

    return False


async def _post_captcha_nudge(page) -> None:
    """token 注入后，尝试触发页面继续动作（部分站点需要 callback 后再点按钮）。"""
    selectors = [
        'button:has-text("Continue")',
        'button:has-text("Verify")',
        'button:has-text("Submit")',
        'button:has-text("Next")',
        'button:has-text("下一步")',
        'button:has-text("继续")',
        'button[type="submit"]',
    ]
    for sel in selectors:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=1200):
                await btn.click(timeout=2000)
                log(f"[PayPal] 验证码后触发按钮: {sel}")
                await page.wait_for_timeout(800)
                return
        except Exception:
            continue


async def _extract_captcha_info(page) -> dict[str, Any]:
    """提取当前页面可交给打码平台处理的 CAPTCHA 信息。"""
    script = """() => {
        const decode = (value) => {
            try { return decodeURIComponent(value || ''); } catch { return value || ''; }
        };
        const visible = (el) => {
            if (!el) return false;
            const rect = el.getBoundingClientRect();
            if (rect.width < 20 || rect.height < 20) return false;
            const style = window.getComputedStyle(el);
            if (!style) return false;
            return style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity || '1') > 0.05;
        };
        const fromUrl = (raw, keys) => {
            const src = String(raw || '');
            for (const key of keys) {
                const m = src.match(new RegExp('[?&]' + key + '=([^&]+)', 'i'));
                if (m) return decode(m[1]);
            }
            return '';
        };
        const dataNode = Array.from(document.querySelectorAll('[data-sitekey], [data-site-key]'))
            .find((el) => visible(el));
        if (dataNode) {
            const sitekey = dataNode.getAttribute('data-sitekey') || dataNode.getAttribute('data-site-key') || '';
            const cls = String(dataNode.className || '').toLowerCase();
            const id = String(dataNode.id || '').toLowerCase();
            const provider = (cls.includes('hcaptcha') || id.includes('hcaptcha')) ? 'hcaptcha' : 'recaptcha';
            return {
                provider,
                sitekey,
                pageUrl: location.href,
                enterprise: /enterprise/i.test(document.documentElement.outerHTML.slice(0, 250000)),
                invisible: dataNode.getAttribute('data-size') === 'invisible',
            };
        }
        for (const frame of Array.from(document.querySelectorAll('iframe'))) {
            const src = frame.getAttribute('src') || '';
            const low = src.toLowerCase();
            if (!visible(frame) && !/recaptcha|hcaptcha|captcha|challenge/.test(low)) continue;
            if (low.includes('hcaptcha')) {
                const sitekey = fromUrl(src, ['sitekey', 'siteKey', 'k']);
                if (sitekey) {
                    return { provider: 'hcaptcha', sitekey, pageUrl: location.href, enterprise: true, invisible: false };
                }
            }
            if (low.includes('recaptcha')) {
                const sitekey = fromUrl(src, ['k', 'sitekey', 'siteKey']);
                if (sitekey) {
                    return {
                        provider: 'recaptcha',
                        sitekey,
                        pageUrl: location.href,
                        enterprise: low.includes('enterprise'),
                        invisible: low.includes('size=invisible'),
                        recaptchaVersion: low.includes('recaptcha_v3') ? 'v3' : 'v2',
                        action: fromUrl(src, ['action']) || 'verify',
                    };
                }
            }
        }
        return {};
    }"""
    targets = [page]
    try:
        targets.extend([fr for fr in page.frames if fr is not page.main_frame])
    except Exception:
        pass
    for target in targets:
        try:
            info = await target.evaluate(script)
        except Exception:
            continue
        if isinstance(info, dict) and info.get("provider") and info.get("sitekey"):
            if not info.get("pageUrl"):
                info["pageUrl"] = page.url
            return info
    return {}


async def _solve_captcha_via_api(page, env: dict[str, str], solver_proxy: str | None = None) -> None:
    """使用配置的打码平台处理 PayPal CAPTCHA。"""
    await _cleanup_hosted_captcha_artifacts(page, timeout_ms=15000)
    info = await _extract_captcha_info(page)
    provider = str(info.get("provider") or "").strip().lower()
    if provider not in {"recaptcha", "hcaptcha"}:
        raise RuntimeError(f"未识别可自动处理的 CAPTCHA 类型: {provider or 'unknown'}")
    site_key = str(info.get("sitekey") or "").strip()
    if not site_key:
        raise RuntimeError("未提取到 CAPTCHA sitekey")

    api_provider = (env.get("CAPTCHA_API_PROVIDER") or "capsolver").strip().lower()
    timeout = int(env.get("PAYPAL_CAPTCHA_TIMEOUT") or 180)
    log(f"[PayPal] CAPTCHA 自动处理: platform={api_provider}, type={provider}, sitekey={site_key[:16]}...")

    if provider == "hcaptcha":
        page_url = page.url
    else:
        page_url = str(info.get("pageUrl") or page.url)

    if api_provider == "yescaptcha":
        from .yescaptcha_solver import YesCaptchaSolver

        solver = YesCaptchaSolver(env.get("YESCAPTCHA_API_KEY", ""), timeout=timeout)
        if provider == "hcaptcha":
            token = await asyncio.to_thread(
                solver.solve_hcaptcha,
                page_url,
                site_key,
                enterprise=bool(info.get("enterprise", True)),
            )
            await _inject_hcaptcha_token(page, token)
        else:
            recaptcha_version = str(info.get("recaptchaVersion") or "v2").lower()
            if recaptcha_version == "v3":
                raise RuntimeError("YesCaptcha 当前 PayPal 自动接入仅处理 reCAPTCHA v2 / hCaptcha")
            token = await asyncio.to_thread(solver.solve_recaptcha_v2, page_url, site_key)
            await _inject_recaptcha_token(page, token)
    elif api_provider in {"capsolver", "twocaptcha", "captchaai"}:
        import recaptcha_solver

        api_key = (
            env.get("CAPSOLVER_API_KEY")
            if api_provider == "capsolver"
            else env.get("TWOCAPTCHA_API_KEY")
        )
        if api_provider == "captchaai":
            api_key = env.get("CAPTCHAAI_KEY") or env.get("CAPTCHA_API_KEY")
        if not api_key:
            raise RuntimeError(f"{api_provider} API key 未配置")
        if provider == "hcaptcha":
            token = await asyncio.to_thread(
                recaptcha_solver.solve_hcaptcha,
                api_key,
                site_key,
                page_url,
                timeout,
                20,
                5,
                bool(info.get("invisible", False)),
                solver_proxy or "",
            )
            await _inject_hcaptcha_token(page, token)
        else:
            token = await asyncio.to_thread(
                recaptcha_solver.solve_recaptcha_v2,
                api_key,
                site_key,
                page_url,
                bool(info.get("invisible", False)),
                bool(info.get("enterprise", False)),
                timeout,
                20,
                5,
                "",
                solver_proxy or "",
            )
            await _inject_recaptcha_token(page, token)
    else:
        raise RuntimeError(f"不支持的验证码服务商: {api_provider}")

    await page.wait_for_timeout(800)
    await _post_captcha_nudge(page)

async def fill_sms_code(
    page,
    api_url: str,
    solver_proxy: str | None = None,
    *,
    prefix: str = "[PayPal]",
) -> bool:
    """等待并填入 PayPal 手机验证码（复刻 source4 逻辑）。"""
    stage_started_at = time.perf_counter()
    await page.wait_for_timeout(800)
    if not await _is_paypal_verification_stage(page):
        log(f"{prefix} 未检测到短信验证码页，跳过自动填码")
        return False

    # 二次风控常发生在短信页刚加载时，先做一次预处理。
    has_captcha_pre, reason_pre = await _detect_captcha_signal(page)
    if has_captcha_pre:
        log(f"[PayPal] 短信验证码页检测到 CAPTCHA，先处理... reason={reason_pre}")
        await handle_paypal_captcha(page, solver_proxy=solver_proxy)
        await _wait_captcha_cleared(page, timeout_seconds=30)

    code = poll_sms_code(api_url, timeout=120, interval=5)
    code_received_at = time.perf_counter()
    log(f"[PayPal] 验证码: {code} (wait={code_received_at - stage_started_at:.1f}s)")

    # 优先使用直填，避免 click 被遮挡导致输入中断。
    typed = False
    if await _fill_paypal_otp_inputs_direct(page, code):
        typed = True

    if not typed:
        all_tel = page.locator('input[type="tel"]:visible')
        tel_count = await all_tel.count()
        for i in range(tel_count):
            val = await all_tel.nth(i).input_value()
            if not val:
                try:
                    await all_tel.nth(i).click(timeout=2000)
                except Exception as exc:
                    if _looks_like_captcha_pointer_block(exc):
                        log("[PayPal] 验证码输入框被 CAPTCHA 遮挡，处理后重试点击")
                        await handle_paypal_captcha(page, solver_proxy=solver_proxy, force=True)
                        await _wait_captcha_cleared(page, timeout_seconds=30)
                        await all_tel.nth(i).click(timeout=3000)
                    else:
                        raise
                await page.wait_for_timeout(300)
                for digit in code:
                    await page.keyboard.press(digit)
                    await page.wait_for_timeout(200)
                typed = True
                break
    if not typed:
        raise RuntimeError("短信验证码输入失败：未找到可填写的 OTP 输入框")
    typed_at = time.perf_counter()
    log(f"{prefix} PayPal OTP filled (elapsed={typed_at - code_received_at:.1f}s)")

    # 快速路径：验证码写入后先短暂等待，尽快进入提交/确认点击
    await page.wait_for_timeout(500)
    # 输入后再次出现验证码时，先处理再点提交。
    has_captcha_post, reason_post = await _detect_captcha_signal(page)
    if has_captcha_post:
        log(f"[PayPal] 验证码输入后检测到 CAPTCHA，处理后再提交... reason={reason_post}")
        await handle_paypal_captcha(page, solver_proxy=solver_proxy)
        await _wait_captcha_cleared(page, timeout_seconds=30)

    try:
        confirm_jp = _zh(r"\u78ba\u8a8d")
        submit_jp = _zh(r"\u9001\u4fe1")
        continue_jp = _zh(r"\u7d9a\u884c")
        next_jp = _zh(r"\u6b21\u3078")
        btn = page.locator(
            'button[type="submit"], input[type="submit"], '
            'button:has-text("Confirm"), button:has-text("Submit"), button:has-text("Verify"), button:has-text("Continue"), '
            f'button:has-text("{confirm_jp}"), button:has-text("{submit_jp}"), button:has-text("{continue_jp}"), button:has-text("{next_jp}")'
        ).first
        if await btn.is_visible(timeout=800):
            try:
                await btn.click(timeout=2500, no_wait_after=True)
            except Exception as exc:
                if _looks_like_captcha_pointer_block(exc):
                    log("[PayPal] 提交按钮被 CAPTCHA 遮挡，处理后重试提交")
                    await handle_paypal_captcha(page, solver_proxy=solver_proxy, force=True)
                    await _wait_captcha_cleared(page, timeout_seconds=10)
                    await btn.click(timeout=2500, no_wait_after=True)
                else:
                    raise
        else:
            await _click_paypal_otp_submit_by_dom(page)
    except Exception:
        pass

    submitted_at = time.perf_counter()
    log(f"{prefix} PayPal OTP submit checked (elapsed={submitted_at - typed_at:.1f}s)")

    # 提交后用短间隔轮询 review 页按钮，避免外层等待 60s 后才点到 Agree。
    agree_clicked = await _wait_and_click_paypal_agree_after_sms(
        page,
        solver_proxy=solver_proxy,
        prefix=prefix,
        timeout_seconds=12.0,
    )
    if agree_clicked:
        log(f"{prefix} PayPal agree clicked after OTP (elapsed={time.perf_counter() - submitted_at:.1f}s)")
        return True

    await page.wait_for_timeout(500)
    still_verify = await _is_paypal_verification_stage(page)
    if still_verify:
        log(f"{prefix} 短信验证码阶段仍在，判定未完成提交")
        return False
    log(f"{prefix} 短信验证码已提交并通过")
    return True


async def _click_paypal_otp_submit_by_dom(page) -> bool:
    script = r"""() => {
        const labels = /confirm|submit|verify|continue|确认|提交|验证|继续|\u78ba\u8a8d|\u9001\u4fe1|\u7d9a\u884c|\u6b21\u3078/i;
        const isVisible = (el) => {
            if (!el) return false;
            const rect = el.getBoundingClientRect();
            if (rect.width < 10 || rect.height < 10) return false;
            const style = window.getComputedStyle(el);
            return style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity || '1') > 0.05;
        };
        const candidates = Array.from(document.querySelectorAll('button, input[type="submit"], div[role="button"], a[role="button"]'));
        for (const el of candidates) {
            const text = String(el.innerText || el.textContent || el.value || el.getAttribute('aria-label') || '').trim();
            if (!isVisible(el)) continue;
            if (el.disabled || el.getAttribute('aria-disabled') === 'true') continue;
            if (!labels.test(text) && !el.matches('button[type="submit"], input[type="submit"]')) continue;
            try { el.scrollIntoView({ block: 'center' }); } catch {}
            for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
            }
            return true;
        }
        return false;
    }"""
    try:
        return bool(await page.evaluate(script))
    except Exception:
        return False


async def check_phone_rejected(page) -> bool:
    """检查是否出现 'Try a different phone number' 弹窗。"""
    try:
        body = await page.locator("body").inner_text(timeout=2000)
        if "different phone" in body.lower() or "Try a different" in body:
            # 点 OK 关闭弹窗
            try:
                await page.locator('button:has-text("OK")').first.click(timeout=3000)
            except Exception:
                pass
            await page.wait_for_timeout(1000)
            return True
    except Exception:
        pass
    return False


async def _wait_and_click_paypal_agree_after_sms(
    page,
    *,
    solver_proxy: str | None = None,
    prefix: str = "[PayPal]",
    timeout_seconds: float = 12.0,
) -> bool:
    """Poll the PayPal review button shortly after OTP submit."""
    deadline = asyncio.get_event_loop().time() + max(1.0, timeout_seconds)
    attempt = 0
    while asyncio.get_event_loop().time() < deadline:
        attempt += 1
        if await _click_paypal_agree_and_continue_if_present(
            page,
            solver_proxy=solver_proxy,
            prefix=prefix,
            visible_timeout_ms=350,
        ):
            return True
        if attempt == 1 or attempt % 6 == 0:
            log(f"{prefix} PayPal agree button not visible yet after OTP (attempt={attempt})")
        await page.wait_for_timeout(500)
    return False


async def _click_paypal_agree_and_continue_if_present(
    page,
    *,
    solver_proxy: str | None = None,
    prefix: str = "[PayPal]",
    visible_timeout_ms: int = 1200,
) -> bool:
    """若出现 PayPal review 页的 Agree and Continue，则自动点击。"""
    agree_continue_jp = _zh(r"\u540c\u610f\u3057\u3066\u7d9a\u884c")
    agree_continue_jp_alt = _zh(r"\u540c\u610f\u3057\u3066\u7d9a\u3051\u308b")
    continue_jp = _zh(r"\u7d9a\u884c")
    selectors = [
        f'button:has-text("{agree_continue_jp}")',
        f'button:has-text("{agree_continue_jp_alt}")',
        f'button:has-text("{continue_jp}")',
        'button:has-text("Agree and Continue")',
        'button:has-text("Agree & Continue")',
        'button:has-text("同意并继续")',
        'button:has-text("继续并同意")',
        'button:has-text("继续")',
    ]
    for sel in selectors:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=visible_timeout_ms):
                # 页面常在底部，先滚动到按钮再点击
                try:
                    await btn.scroll_into_view_if_needed(timeout=800)
                except Exception:
                    pass
                try:
                    await btn.click(timeout=5000)
                except Exception as exc:
                    if _looks_like_captcha_pointer_block(exc):
                        log(f"{prefix} Agree and Continue 按钮被 CAPTCHA 遮挡，处理后重试")
                        await handle_paypal_captcha(page, solver_proxy=solver_proxy, force=True)
                        await _wait_captcha_cleared(page, timeout_seconds=30)
                        await btn.click(timeout=5000)
                    else:
                        raise
                log(f"{prefix} 已点击支付确认按钮: {sel}")
                await page.wait_for_timeout(1500)
                return True
        except Exception:
            continue
    # JS 文本兜底：处理包裹在 span/div 的日语大蓝按钮
    try:
        js_clicked = await page.evaluate(
            r"""() => {
                const isVisible = (el) => {
                    if (!el) return false;
                    const r = el.getBoundingClientRect();
                    if (r.width < 20 || r.height < 20) return false;
                    const st = window.getComputedStyle(el);
                    if (!st) return false;
                    return st.display !== 'none' && st.visibility !== 'hidden' && Number(st.opacity || '1') > 0.05;
                };
                const textOf = (el) => String(el?.innerText || el?.textContent || '').trim();
                const targets = Array.from(document.querySelectorAll('button, a, div[role="button"], span'))
                    .filter(isVisible);
                const hit = targets.find(el => /\u540c\u610f\u3057\u3066\u7d9a\u884c|\u540c\u610f\u3057\u3066\u7d9a\u3051\u308b|Agree\s*&?\s*Continue/i.test(textOf(el)));
                if (!hit) return false;
                const btn = hit.closest('button, a, div[role="button"]') || hit;
                btn.scrollIntoView({block: 'center'});
                btn.click();
                return true;
            }"""
        )
        if js_clicked:
            log(f"{prefix} 已点击支付确认按钮: js_fallback_jp_agree")
            await page.wait_for_timeout(1500)
            return True
    except Exception:
        pass
    return False


async def pay_one(
    item: dict[str, str],
    card: CardInfo,
    phone_pool: PhonePool,
    cfg: dict[str, Any],
    worker_id: int = 1,
    max_phone_retries: int = 3,
    proxy: str | None = None,
    flow2_region_mode: str = "default",
    last_error: dict[str, str] | None = None,
    proxy_attempt: int = 1,
    us_proxy: str | None = None,
) -> bool:
    """执行一次 PayPal 支付。"""
    email = item["email"]
    query_code = item["query_code"]
    payment_link = item["payment_link"]
    account_line = item.get("account_line", "")
    prefix = f"[paypal-pay-{worker_id:02d}][{email}]"
    paypal_password = generate_paypal_password(email)
    region_mode = _normalize_flow2_region_mode(flow2_region_mode)

    browser_cfg = cfg.get("browser", {})
    profile_dir = resolve_path("profiles") / f"paypal_pay_{safe_filename(email)}"

    if proxy:
        log(f"{prefix} 使用代理: {_display_proxy(proxy)}")

    session = BrowserSession(
        profile_dir=profile_dir,
        headless=bool(browser_cfg.get("headless", False)),
        slow_mo=int(browser_cfg.get("slow_mo", 80)),
        timeout_ms=int(browser_cfg.get("timeout_ms", 60000)),
        proxy=proxy,
        isolated=True,
        fingerprint_seed=f"{email}|flow2",
        account_id=email,
        log_prefix=prefix,
        browser_engine=browser_cfg.get("engine"),
        browser_locale=browser_cfg.get("locale"),
        camoufox_executable_path=browser_cfg.get("camoufox_executable_path"),
        camoufox_geoip=browser_cfg.get("camoufox_geoip"),
    )

    phone: PhoneInfo | None = None
    try:
        flow_env = load_env(".env")
        use_long_link = paypal_use_long_link(flow_env)
        open_payment_link = use_long_link
        watcher_enabled = paypal_click_watcher_enabled(flow_env)
        stripe_country = _payment_form_country_code(region_mode, use_long_link=use_long_link)
        paypal_country = _paypal_form_country_code(region_mode, use_long_link=use_long_link)
        working_card = card
        if region_mode == "jp":
            if use_long_link:
                jp_billing = _generate_local_random_card(worker_id, email, flow_env, region_mode="jp")
                working_card = _with_billing_profile(card, jp_billing)
                log(
                    f"{prefix} "
                    + _zh(r"\u65e5\u672c\u4ee3\u7406\u957f\u94fe\u6a21\u5f0f: \u4f7f\u7528\u65e5\u672c\u8d26\u5355\u5730\u5740 ")
                    + f"{working_card.city}, {working_card.state}, {working_card.zip_code}"
                )
            else:
                if payment_link.startswith("https://www.paypal.com/agreements/approve?ba_token=BA-"):
                    if not us_proxy:
                        raise RuntimeError("zenpic PayPal BA 短链必须绑定 US 代理打开")
                    working_card = card
                    paypal_country = "JP"
                    log(
                        f"{prefix} "
                        + _zh(
                            r"\u65e5\u533a\u77ed\u94fe\u65b0\u6a21\u5f0f: "
                            r"\u4f7f\u7528 US \u4ee3\u7406\u6253\u5f00 PayPal BA \u77ed\u94fe\uff0c"
                            r"\u8fdb\u5165\u6ce8\u518c\u8868\u5355\u540e\u5207\u6362\u65e5\u672c\u5730\u533a"
                        )
                    )
                else:
                    _ensure_short_link_jp_proxy(proxy, env=flow_env, prefix=prefix)
                    us_billing = _generate_local_random_card(worker_id, email, flow_env, region_mode="default")
                    working_card = _with_billing_profile(card, us_billing)
                    log(
                        f"{prefix} "
                        + _zh(
                            r"\u77ed\u94fe\u5b98\u65b9\u8ba2\u9605\u6a21\u5f0f: \u4f7f\u7528\u65e5\u672c IP \u83b7\u53d6 0 \u5143\u8bd5\u7528\uff0c"
                            r"\u5168\u7a0b\u4fdd\u6301\u540c\u4e00\u65e5\u672c\u4ee3\u7406\uff0c\u652f\u4ed8\u9875/PayPal \u8d26\u5355\u6539\u7528\u7f8e\u56fd "
                        )
                        + f"{working_card.city}, {working_card.state}, {working_card.zip_code}"
                    )

        if region_mode == "jp" and not use_long_link and payment_link.startswith("https://www.paypal.com/agreements/approve?ba_token=BA-"):
            session = BrowserSession(
                profile_dir=profile_dir,
                headless=bool(browser_cfg.get("headless", False)),
                slow_mo=int(browser_cfg.get("slow_mo", 80)),
                timeout_ms=int(browser_cfg.get("timeout_ms", 60000)),
                proxy=us_proxy,
                isolated=True,
                fingerprint_seed=f"{email}|flow2|paypal-ba-us",
                account_id=email,
                log_prefix=prefix,
                browser_engine=browser_cfg.get("engine"),
                browser_locale=browser_cfg.get("locale"),
                camoufox_executable_path=browser_cfg.get("camoufox_executable_path"),
                camoufox_geoip=browser_cfg.get("camoufox_geoip"),
            )
            log(f"{prefix} 打开 PayPal BA 短链代理(US): {_display_proxy(us_proxy)}")

        await session.__aenter__()
        page = await session.current_page()
        await _install_click_watcher(page, email, enabled=watcher_enabled, label="flow2")

        # 长链仅在显式长链模式下打开；短链模式不再接受任何长链兜底。
        if open_payment_link:
            log(f"{prefix} 打开支付链接...")
            await page.goto(payment_link, wait_until="domcontentloaded")
            await page.wait_for_timeout(1500)
            link_payment = await _detect_link_payment_invalid_long_link(page)
            if link_payment.get("invalid"):
                log(
                    f"{prefix} "
                    + _zh(r"\u957f\u94fe\u6253\u5f00\u540e\u51fa\u73b0 Link \u652f\u4ed8\u5165\u53e3\uff0c\u5224\u5b9a\u957f\u94fe\u751f\u6210\u9519\u8bef\uff0c\u9700\u6362\u65b9\u6cd5\u91cd\u65b0\u751f\u6210: ")
                    + str(link_payment.get("text") or link_payment.get("source") or "")
                )
                raise RuntimeError(
                    f"{PAYPAL_FLOW2_RECREATE_LINK}: opened long link shows Link payment; generated link is invalid"
                )
        elif payment_link.startswith("https://www.paypal.com/agreements/approve?ba_token=BA-"):
            log(f"{prefix} 打开 PayPal BA 短链...")
            await page.goto(payment_link, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)
        else:
            await _prepare_checkout_from_chatgpt_offer(
                page,
                item,
                cfg,
                proxy=proxy,
                prefix=prefix,
                watcher_enabled=watcher_enabled,
                flow_env=flow_env,
            )
        if payment_link.startswith("https://www.paypal.com/agreements/approve?ba_token=BA-"):
            due_amount = {"status": "zero", "amount_text": "BA 短链", "source": "zenpic"}
        else:
            try:
                await page.locator(
                    f'input[autocomplete="cc-number"], iframe[name*="__privateStripeFrame"], text={_zh(r"\u652f\u4ed8\u65b9\u5f0f")}'
                ).first.wait_for(timeout=2500)
            except Exception:
                pass
            await page.wait_for_timeout(1200)
            due_amount = await inspect_checkout_due_amount(page)
        if due_amount.get("status") == "nonzero":
            reason = (
                f"{PAYPAL_FLOW2_NONZERO_AMOUNT}: "
                f"{due_amount.get('amount_text') or due_amount.get('amount_value')} | "
                f"{due_amount.get('source_text') or ''}"
            )
            if use_long_link:
                log(
                    f"{prefix} "
                    + _zh(r"\u68c0\u6d4b\u5230\u652f\u4ed8\u9875\u5e94\u4ed8\u91d1\u989d\u975e 0\uff0c\u4f5c\u5e9f\u8be5\u8d26\u53f7\u5e76\u8df3\u5230\u4e0b\u4e00\u4e2a: ")
                    + f"{due_amount.get('amount_text') or due_amount.get('amount_value')}"
                )
                discard_flow2_link(email, reason=reason)
            else:
                log(
                    f"{prefix} "
                    + _zh(r"\u77ed\u94fe\u6a21\u5f0f\u68c0\u6d4b\u5230\u652f\u4ed8\u9875\u975e 0\uff0c\u7ec8\u6b62\u5f53\u524d\u8d26\u53f7\uff0c\u4e0d\u505a\u957f\u94fe\u515c\u5e95: ")
                    + f"{due_amount.get('amount_text') or due_amount.get('amount_value')}"
                )
                discard_flow2_link(email, reason=reason)
            if last_error is not None:
                last_error["reason"] = PAYPAL_FLOW2_NONZERO_AMOUNT
            return False
        if (
            due_amount.get("status") == "zero"
            and region_mode == "jp"
            and not use_long_link
            and _checkout_due_amount_looks_jpy(due_amount)
        ):
            reason = (
                "checkout still JPY after US offer region: "
                f"{due_amount.get('amount_text') or due_amount.get('amount_value')} | "
                f"{due_amount.get('source_text') or ''}"
            )
            log(
                f"{prefix} "
                + _zh(r"\u652f\u4ed8\u9875\u4ecd\u663e\u793a\u65e5\u5143\u96f6\u91d1\u989d\uff0c\u5730\u533a/\u8d27\u5e01\u672a\u6309\u7f8e\u56fd\u5237\u65b0\uff0c\u7ec8\u6b62\u5f53\u524d\u4efb\u52a1: ")
                + f"{due_amount.get('amount_text') or due_amount.get('amount_value')}"
            )
            await _save_chatgpt_offer_failure_debug_once(page, email, reason)
            if last_error is not None:
                last_error["reason"] = f"{PAYPAL_FLOW2_NO_PAYPAL_OPTION}: checkout still JPY after US offer region"
            return False
        if due_amount.get("status") == "zero":
            log(
                f"{prefix} "
                + _zh(r"\u652f\u4ed8\u9875\u5e94\u4ed8\u91d1\u989d\u786e\u8ba4\u4e3a 0\uff0c\u7ee7\u7eed\u6d41\u7a0b: ")
                + f"{due_amount.get('amount_text') or due_amount.get('amount_value')}"
                + f" source={due_amount.get('source') or 'text'} selector={due_amount.get('selector') or ''}"
            )
        else:
            candidates_text = ", ".join(str(item) for item in (due_amount.get("amount_candidates") or [])[:8])
            log(
                f"{prefix} "
                + _zh(r"\u672a\u8bc6\u522b\u652f\u4ed8\u9875\u5e94\u4ed8\u91d1\u989d\uff0c\u6309\u539f\u6d41\u7a0b\u7ee7\u7eed: ")
                + str(due_amount.get("reason") or "unknown")
                + (f" wait={due_amount.get('wait_error')}" if due_amount.get("wait_error") else "")
                + (f" candidates={candidates_text}" if candidates_text else "")
            )

        if not payment_link.startswith("https://www.paypal.com/agreements/approve?ba_token=BA-"):
            # Stripe
            log(f"{prefix} Stripe 填充...")
            stripe_page = await fill_stripe(
                page,
                email,
                working_card,
                country_code=stripe_country,
                recreate_on_missing_paypal=False,
            )
            if stripe_page is not None:
                page = stripe_page

        # PayPal - 可能需要换手机号重试
        for attempt in range(1, max_phone_retries + 1):
            phone = phone_pool.acquire(worker_id)
            if not phone:
                raise RuntimeError("手机号池已耗尽")
            log(f"{prefix} PayPal 注册 (手机: {phone.number}, 尝试 {attempt}/{max_phone_retries})...")

            await fill_paypal(
                page,
                email,
                working_card,
                phone,
                paypal_password,
                proxy=proxy,
                country_code=paypal_country,
            )

            # 检查手机号是否被拒
            await page.wait_for_timeout(1200)
            # CAPTCHA 未通过时，不应误判手机号拒绝；先做一次有 reason 的复检与兜底处理
            has_captcha_after, reason_after = await _detect_captcha_signal(page)
            if has_captcha_after:
                log(f"{prefix} 检测到 CAPTCHA 残留（reason={reason_after}），尝试再处理一次...")
                await handle_paypal_captcha(page, solver_proxy=proxy)
                # 关键修复：不要仅凭 challenge iframe 残留直接判失败；
                # 若页面已推进到短信验证码阶段，也视为 CAPTCHA 已通过。
                cleared = await _wait_captcha_cleared(page, timeout_seconds=20)
                if not cleared:
                    has_captcha_after2, reason_after2 = await _detect_captcha_signal(page)
                    if has_captcha_after2:
                        # 兼容场景：CAPTCHA 已通过但 challenge iframe 短暂残留。
                        if reason_after2 == "visible_challenge_frame" and await _is_paypal_verification_stage(page):
                            log(f"{prefix} CAPTCHA 残留 iframe，但已进入短信验证码阶段，继续流程")
                        else:
                            raise RuntimeError(f"CAPTCHA 未通过（reason={reason_after2}），需人工处理或更换打码策略")
            if await check_phone_rejected(page):
                log(f"{prefix} 手机号 {phone.number} 被拒，换号重试...")
                phone_pool.mark_failed(phone.number)
                phone = None
                continue

            # 验证码
            log(f"{prefix} 等待验证码...")
            sms_done = await fill_sms_code(page, phone.api_url, solver_proxy=proxy, prefix=prefix)
            if not sms_done:
                if await _is_paypal_verification_stage(page):
                    raise RuntimeError("短信验证码未完成，停止后续支付等待")
                log(f"{prefix} 当前未处于短信验证码页，继续后续流程")
            await _click_paypal_agree_and_continue_if_present(page, solver_proxy=proxy, prefix=prefix)
            phone_pool.release(phone.number, success=True)
            break
        else:
            raise RuntimeError(f"手机号重试 {max_phone_retries} 次均被拒")

        # 等待回到 chatgpt.com
        log(f"{prefix} 等待支付完成...")
        for i in range(60):
            if "chatgpt.com" in page.url or "success" in page.url.lower():
                break
            if "paypal.com" in page.url and i % 2 == 0:
                await _click_paypal_agree_and_continue_if_present(page, solver_proxy=proxy, prefix=prefix)
            # review 页额外强制点击一次（该页常停留在“同意して続行”）
            if "paypal.com/webapps/hermes" in page.url and "billingweb/review" in page.url:
                await _click_paypal_agree_and_continue_if_present(page, solver_proxy=proxy, prefix=prefix)
            await page.wait_for_timeout(1000)

        final_url = page.url
        if "chatgpt.com" in final_url or "success" in final_url.lower():
            log(f"{prefix} ✅ 支付成功！")
            save_pending_auth(email, query_code, account_line=account_line)
            remove_from_link_pool(email)
            return True
        else:
            log(f"{prefix} ⚠️ 最终 URL: {final_url}")
            if "paypal.com/webapps/hermes" in final_url and "billingweb/review" in final_url:
                raise RuntimeError("仍停留在 PayPal review 页（同意并继续未完成）")
            raise RuntimeError("支付流程未返回成功页面")

    except Exception as exc:
        if last_error is not None:
            last_error["reason"] = str(exc)
        log(f"{prefix} ❌ 失败: {exc}")
        traceback.print_exc()
        paypal_flow_state.mark_stage_failure(email, stage="flow2", reason=str(exc))
        if phone:
            phone_pool.release(phone.number, success=False)
        return False
    finally:
        await session.__aexit__(None, None, None)


async def run_paypal_pay(
    cfg: dict[str, Any],
    count: int = 1,
    workers: int = 1,
    card_source_mode: str | None = None,
    flow2_region_mode: str | None = None,
    selected_email: str | None = None,
) -> int:
    """批量执行流程2。返回成功数。"""
    log(f"PayPal flow2 code version: {PAYPAL_FLOW2_CODE_VERSION} file={Path(__file__).resolve()}")
    env = load_env(".env")
    resolved_region_mode = _normalize_flow2_region_mode(flow2_region_mode)
    use_long_link = paypal_use_long_link(env)
    paypal_flow_state.sync_from_files(link_file=LINK_POOL_FILE, pending_file=PENDING_AUTH_FILE)
    pool = load_link_pool() if use_long_link else _load_direct_pay_accounts(selected_email or "")
    selected = (selected_email or "").strip().lower()
    if selected and use_long_link:
        pool = [item for item in pool if str(item.get("email") or "").strip().lower() == selected]
    if not pool and not use_long_link:
        fallback_pool = _load_mail_pool_direct_accounts(cfg, selected_email or "")
        if fallback_pool:
            pool = fallback_pool
            log(f"PayPal 流程2：短链直付池为空，改从当前邮箱池取未处理账号 {len(pool)} 个继续")
    if not pool:
        detail = f" (selected={selected_email})" if selected_email else ""
        if use_long_link:
            log(f"PayPal 流程2：长链接池为空，请先运行流程1{detail}")
        else:
            log(f"PayPal 流程2：短链支付模式下没有可直接登录的注册账号{detail}")
        return 0

    cards_file = env.get("PAYPAL_CARDS_FILE") or "data/paypal/cards.txt"
    phones_file = env.get("PAYPAL_PHONES_FILE") or "data/paypal/phones.txt"
    max_uses = int(env.get("PAYPAL_PHONE_MAX_USES") or 5)
    max_retries = int(env.get("PAYPAL_PHONE_RETRY_ON_REJECT") or 3)

    card_pool = CardPool(cards_file)
    phone_pool = PhonePool(phones_file, max_uses=max_uses)
    if card_source_mode:
        local_random_mode = card_source_mode.strip().lower() in {"local_random", "random_local", "local"}
    else:
        local_random_mode = is_local_random_card_mode(env)

    # 代理池（US/JP 分开配置）；未启用或无可用池时回退本地代理。
    from .proxy_pool import ProxyPool
    use_proxy = paypal_flow2_proxy_enabled(env)
    proxy_pool: ProxyPool | None = None
    us_proxy_pool: ProxyPool | None = None
    fallback_proxy = ""
    fallback_us_proxy = ""
    if use_proxy:
        proxy_file = paypal_flow2_proxy_file(env, resolved_region_mode)
        proxy_pool = ProxyPool(proxy_file)
        if proxy_pool.count() <= 0:
            fallback_proxy = local_proxy_url(env)
            proxy_pool = None
            log(f"PayPal 流程2：代理池为空，改用本地代理: {proxy_file} -> {_display_proxy(fallback_proxy)}")
        if resolved_region_mode == "jp":
            count_text = proxy_pool.count() if proxy_pool else 0
            log(f"PayPal 流程2：日本代理模式已启用，代理池={proxy_file}，代理数={count_text}")
            us_proxy_file = paypal_flow2_proxy_file(env, "default")
            us_proxy_pool = ProxyPool(us_proxy_file)
            if us_proxy_pool.count() <= 0:
                fallback_us_proxy = local_proxy_url(env)
                us_proxy_pool = None
                log(f"PayPal 流程2：US 代理池为空，改用本地代理: {us_proxy_file} -> {_display_proxy(fallback_us_proxy)}")
            else:
                log(f"PayPal 流程2：US 代理池已启用，代理池={us_proxy_file}，代理数={us_proxy_pool.count()}")
        else:
            count_text = proxy_pool.count() if proxy_pool else 0
            log(f"PayPal 流程2：美国支付代理已启用，代理池={proxy_file}，代理数={count_text}")
    else:
        fallback_proxy = local_proxy_url(env)
        fallback_us_proxy = fallback_proxy
        if fallback_proxy:
            log(f"PayPal 流程2：代理池关闭，使用本地代理: {_display_proxy(fallback_proxy)}")

    if not local_random_mode:
        desired_cards = min(count, len(pool), phone_pool.count())
        if desired_cards > 0 and card_pool.count() < desired_cards:
            from .paypal_card_redeem import ensure_card_supply

            ensure_card_supply(env, desired_cards, log_prefix="PayPal 流程2")

        if card_pool.count() <= 0:
            log("PayPal 流程2：卡池为空")
            return 0
    else:
        mode_label = card_source_mode or "PAYPAL_CARD_SOURCE=local_random"
        log(f"PayPal 流程2：已启用本地随机卡资料模式（{mode_label}）")
    if phone_pool.count() <= 0:
        log("PayPal 流程2：手机号池为空")
        return 0

    capacity = min(len(pool), phone_pool.count()) if local_random_mode else min(len(pool), card_pool.count())
    target = min(count, capacity)
    card_desc = "本地随机" if local_random_mode else str(card_pool.count())
    source_desc = "长链接" if use_long_link else "短链支付账号"
    log(f"PayPal 流程2：{source_desc} {len(pool)} 个，卡 {card_desc} 张，手机号 {phone_pool.count()} 个，本次目标 {target}，并发 {workers}")

    success = 0
    skipped_nonzero = 0
    attempted = 0
    active_slots = 0
    failed_slots = 0
    next_index = 0
    sem = asyncio.Semaphore(workers)
    queue_lock = asyncio.Lock()

    async def next_item() -> tuple[int, dict[str, str]] | None:
        nonlocal active_slots, next_index
        async with queue_lock:
            if success + active_slots >= target:
                return None
            if next_index >= len(pool):
                return None
            index = next_index
            next_index += 1
            active_slots += 1
            return index + 1, pool[index]

    async def worker(index: int) -> None:
        nonlocal active_slots, attempted, failed_slots, skipped_nonzero, success
        async with sem:
            while True:
                picked = await next_item()
                if not picked:
                    return
                item_number, item = picked
                attempted += 1
                if local_random_mode:
                    card = _generate_local_random_card(item_number, item["email"], env, region_mode=resolved_region_mode)
                else:
                    card = card_pool.take_one()
                    if not card:
                        log(f"[paypal-pay-{index:02d}] 卡池已空")
                        async with queue_lock:
                            active_slots = max(0, active_slots - 1)
                            failed_slots += 1
                        return
                proxies = proxy_pool.random_sequence() if proxy_pool else [fallback_proxy or None]
                us_proxies = us_proxy_pool.random_sequence() if us_proxy_pool else [fallback_us_proxy or None]
                zenpic_mode = _short_link_entry_uses_zenpic(
                    resolved_region_mode,
                    use_long_link=use_long_link,
                    card_source_mode=card_source_mode,
                )
                ok = False
                flow2_discarded = False
                flow2_recreate_link = False
                flow2_no_paypal_stop = False
                stripe_timeout_stop = False
                for proxy_attempt, proxy in enumerate(proxies, start=1):
                    us_proxy = us_proxies[(proxy_attempt - 1) % len(us_proxies)] if us_proxies else None
                    link_attempt = 1
                    while True:
                        last_error: dict[str, str] = {}
                        if zenpic_mode:
                            session_text = find_session_text_for_email(item["email"], env)
                            if not session_text:
                                last_error["reason"] = "zenpic session cache missing; run register-only with successful session first"
                                log(f"[paypal-pay-{index:02d}][{item['email']}] {last_error['reason']}")
                                ok = False
                                break
                            try:
                                result = create_zenpic_short_link(
                                    email=item["email"],
                                    session_text=session_text,
                                    us_proxy=us_proxy or "",
                                    jp_proxy=proxy or "",
                                    env=env,
                                )
                            except Exception as exc:  # noqa: BLE001
                                last_error["reason"] = str(exc)
                                log(f"[paypal-pay-{index:02d}][{item['email']}] zenpic 短链生成失败: {exc}")
                                ok = False
                                break
                            item["payment_link"] = result.long_url
                            item["link_method"] = "zenpic_stage2_ba"
                            log(f"[paypal-pay-{index:02d}][{item['email']}] zenpic 阶段二 BA 短链: {result.long_url}")
                        ok = await pay_one(
                            item,
                            card,
                            phone_pool,
                            cfg,
                            worker_id=index,
                            max_phone_retries=max_retries,
                            proxy=proxy,
                            flow2_region_mode=resolved_region_mode,
                            last_error=last_error,
                            proxy_attempt=proxy_attempt,
                            us_proxy=us_proxy,
                        )
                        reason = last_error.get("reason", "")
                        if ok:
                            break
                        if reason == PAYPAL_FLOW2_NONZERO_AMOUNT:
                            skipped_nonzero += 1
                            flow2_discarded = True
                            async with queue_lock:
                                active_slots = max(0, active_slots - 1)
                            break
                        if _is_no_paypal_option_reason(reason):
                            flow2_no_paypal_stop = True
                            discard_flow2_link(item["email"], reason=reason or PAYPAL_FLOW2_NO_PAYPAL_OPTION)
                            log(
                                f"[paypal-pay-{index:02d}][{item['email']}] "
                                + _zh(r"\u5b98\u65b9\u4f18\u60e0\u8d26\u5355\u9875\u672a\u51fa\u73b0 PayPal \u652f\u4ed8\u9009\u9879\uff0c\u6309\u89c4\u5219\u7ec8\u6b62\u5f53\u524d\u4efb\u52a1")
                            )
                            break
                        if _is_recreate_link_reason(reason):
                            flow2_recreate_link = True
                            discard_flow2_link(item["email"], reason=reason)
                            log(
                                f"[paypal-pay-{index:02d}][{item['email']}] "
                                + _zh(r"\u957f\u94fe/\u91cd\u5efa\u957f\u94fe\u515c\u5e95\u5df2\u7981\u7528\uff0c\u5f53\u524d\u8d26\u53f7\u76f4\u63a5\u7ec8\u6b62")
                            )
                            break
                        if not proxy_pool or proxy_attempt >= len(proxies):
                            break
                        if reason == PAYPAL_FLOW2_STRIPE_PAYPAL_TIMEOUT and proxy_attempt >= 4:
                            stripe_timeout_stop = True
                            log(
                                f"[paypal-pay-{index:02d}][{item['email']}] "
                                f"Stripe 60s no PayPal redirect, switched proxy {proxy_attempt - 1}/3 times; stop current link"
                            )
                            break
                        retry_label = f"{proxy_attempt}/3" if reason == PAYPAL_FLOW2_STRIPE_PAYPAL_TIMEOUT else f"{proxy_attempt}/{len(proxies)}"
                        log(
                            f"[paypal-pay-{index:02d}][{item['email']}] "
                            f"流程失败，随机切换代理 ({retry_label}): {_display_proxy(proxy)}"
                        )
                        break
                    if ok or flow2_discarded or flow2_recreate_link or flow2_no_paypal_stop or stripe_timeout_stop:
                        break
                if ok and not local_random_mode:
                    card_pool.remove(card)
                if ok:
                    async with queue_lock:
                        active_slots = max(0, active_slots - 1)
                        success += 1
                if not ok and not flow2_discarded:
                    async with queue_lock:
                        active_slots = max(0, active_slots - 1)
                        failed_slots += 1
                    if flow2_recreate_link:
                        log(
                            f"[paypal-pay-{index:02d}][{item['email']}] "
                            + _zh(r"\u957f\u94fe\u6d41\u7a0b\u5df2\u505c\u7528\uff0c\u672c\u6b21\u4e0d\u518d\u91cd\u5efa\u6216\u590d\u7528\u957f\u94fe")
                        )
                    continue

    tasks = [asyncio.create_task(worker(i + 1)) for i in range(max(1, workers))]
    await asyncio.gather(*tasks)
    if skipped_nonzero:
        log(
            f"PayPal 流程2 完成：成功 {success}/{target}，"
            + _zh(r"\u975e 0 \u91d1\u989d\u4f5c\u5e9f ")
            + f"{skipped_nonzero}，attempted={attempted}/{len(pool)}"
        )
    else:
        log(f"PayPal 流程2 完成：成功 {success}/{target}")
    return success


