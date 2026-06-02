from __future__ import annotations

from typing import Any


def _u(text: str) -> str:
    return text.encode("ascii").decode("unicode_escape")


ALLOWED_SMS_COUNTRY_NAMES: dict[str, str] = {
    "AF": _u(r"\u963f\u5bcc\u6c57"),
    "AM": _u(r"\u4e9a\u7f8e\u5c3c\u4e9a"),
    "AN": _u(r"\u8377\u5c5e\u5b89\u7684\u5217\u65af"),
    "AO": _u(r"\u5b89\u54e5\u62c9"),
    "AX": _u(r"\u5965\u5170\u7fa4\u5c9b"),
    "BD": _u(r"\u5b5f\u52a0\u62c9\u56fd"),
    "BF": _u(r"\u5e03\u57fa\u7eb3\u6cd5\u7d22"),
    "BG": _u(r"\u4fdd\u52a0\u5229\u4e9a"),
    "BI": _u(r"\u5e03\u9686\u8fea"),
    "BL": _u(r"\u5723\u5df4\u6cf0\u52d2\u7c73"),
    "BO": _u(r"\u73bb\u5229\u7ef4\u4e9a"),
    "CA": _u(r"\u52a0\u62ff\u5927"),
    "CC": _u(r"\u79d1\u79d1\u65af\u7fa4\u5c9b"),
    "CF": _u(r"\u4e2d\u975e\u5171\u548c\u56fd"),
    "CM": _u(r"\u5580\u9ea6\u9686"),
    "CX": _u(r"\u5723\u8bde\u5c9b"),
    "DZ": _u(r"\u963f\u5c14\u53ca\u5229\u4e9a"),
    "EC": _u(r"\u5384\u74dc\u591a\u5c14"),
    "EG": _u(r"\u57c3\u53ca"),
    "EH": _u(r"\u897f\u6492\u54c8\u62c9"),
    "ET": _u(r"\u57c3\u585e\u4fc4\u6bd4\u4e9a"),
    "FK": _u(r"\u798f\u514b\u5170\u7fa4\u5c9b"),
    "FR": _u(r"\u6cd5\u56fd"),
    "GH": _u(r"\u52a0\u7eb3"),
    "GN": _u(r"\u51e0\u5185\u4e9a"),
    "GW": _u(r"\u51e0\u5185\u4e9a\u6bd4\u7ecd"),
    "HN": _u(r"\u6d2a\u90fd\u62c9\u65af"),
    "HT": _u(r"\u6d77\u5730"),
    "ID": _u(r"\u5370\u5ea6\u5c3c\u897f\u4e9a"),
    "IO": _u(r"\u82f1\u5c5e\u5370\u5ea6\u6d0b\u9886\u5730"),
    "JM": _u(r"\u7259\u4e70\u52a0"),
    "JO": _u(r"\u7ea6\u65e6"),
    "JP": _u(r"\u65e5\u672c"),
    "KG": _u(r"\u5409\u5c14\u5409\u65af\u65af\u5766"),
    "KH": _u(r"\u67ec\u57d4\u5be8"),
    "KM": _u(r"\u79d1\u6469\u7f57"),
    "KR": _u(r"\u97e9\u56fd"),
    "LB": _u(r"\u9ece\u5df4\u5ae9"),
    "LK": _u(r"\u65af\u91cc\u5170\u5361"),
    "LY": _u(r"\u5229\u6bd4\u4e9a"),
    "ME": _u(r"\u9ed1\u5c71"),
    "MF": _u(r"\u6cd5\u5c5e\u5723\u9a6c\u4e01"),
    "MG": _u(r"\u9a6c\u8fbe\u52a0\u65af\u52a0"),
    "ML": _u(r"\u9a6c\u91cc"),
    "MN": _u(r"\u8499\u53e4"),
    "MP": _u(r"\u5317\u9a6c\u91cc\u4e9a\u7eb3\u7fa4\u5c9b"),
    "MU": _u(r"\u6bdb\u91cc\u6c42\u65af"),
    "MY": _u(r"\u9a6c\u6765\u897f\u4e9a"),
    "MZ": _u(r"\u83ab\u6851\u6bd4\u514b"),
    "NG": _u(r"\u5c3c\u65e5\u5229\u4e9a"),
    "NU": _u(r"\u7ebd\u57c3"),
    "PE": _u(r"\u79d8\u9c81"),
    "PK": _u(r"\u5df4\u57fa\u65af\u5766"),
    "PN": _u(r"\u76ae\u7279\u51ef\u6069\u7fa4\u5c9b"),
    "PS": _u(r"\u5df4\u52d2\u65af\u5766\u9886\u571f"),
    "SA": _u(r"\u6c99\u7279\u963f\u62c9\u4f2f"),
    "SD": _u(r"\u82cf\u4e39"),
    "SH": _u(r"\u5723\u8d6b\u52d2\u62ff"),
    "SI": _u(r"\u65af\u6d1b\u6587\u5c3c\u4e9a"),
    "SJ": _u(r"\u65af\u74e6\u5c14\u5df4\u548c\u626c\u9a6c\u5ef6"),
    "SL": _u(r"\u585e\u62c9\u5229\u6602"),
    "SM": _u(r"\u5723\u9a6c\u529b\u8bfa"),
    "SN": _u(r"\u585e\u5185\u52a0\u5c14"),
    "TG": _u(r"\u591a\u54e5"),
    "TH": _u(r"\u6cf0\u56fd"),
    "TJ": _u(r"\u5854\u5409\u514b\u65af\u5766"),
    "TK": _u(r"\u6258\u514b\u52b3"),
    "TL": _u(r"\u4e1c\u5e1d\u6c76"),
    "TM": _u(r"\u571f\u5e93\u66fc\u65af\u5766"),
    "TW": _u(r"\u53f0\u6e7e"),
    "UG": _u(r"\u4e4c\u5e72\u8fbe"),
    "UM": _u(r"\u7f8e\u56fd\u672c\u571f\u5916\u5c0f\u5c9b\u5c7f"),
    "US": _u(r"\u7f8e\u56fd"),
    "UZ": _u(r"\u4e4c\u5179\u522b\u514b\u65af\u5766"),
    "VA": _u(r"\u68b5\u8482\u5188"),
    "VN": _u(r"\u8d8a\u5357"),
    "VU": _u(r"\u74e6\u52aa\u963f\u56fe"),
    "YU": _u(r"\u5357\u65af\u62c9\u592b"),
    "ZM": _u(r"\u8d5e\u6bd4\u4e9a"),
    "ZW": _u(r"\u6d25\u5df4\u5e03\u97e6"),
}

ALLOWED_SMS_COUNTRY_CODES = frozenset(ALLOWED_SMS_COUNTRY_NAMES)


def sms_country_iso(country: Any) -> str:
    return str(getattr(country, "iso_code", "") or "").strip().upper()


def is_allowed_sms_country(country: Any) -> bool:
    return sms_country_iso(country) in ALLOWED_SMS_COUNTRY_CODES


def filter_allowed_sms_countries(countries: list[Any]) -> list[Any]:
    return [country for country in countries if is_allowed_sms_country(country)]


def allowed_sms_country_name(iso_code: str) -> str:
    return ALLOWED_SMS_COUNTRY_NAMES.get(str(iso_code or "").strip().upper(), "")
