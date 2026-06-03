from types import SimpleNamespace

from control_panel import sms_options
from control_panel.sms_options import OptionItem, _country_label, dynamic_env_options, parse_dynamic_display
from modules.hero_sms_provider import HeroSMSProvider, PhoneCountry, parse_services_response
from modules.sms_country_filter import filter_allowed_sms_countries
from modules.smsbower_provider import DEFAULT_ENDPOINT


def test_option_item_display_keeps_value_first() -> None:
    assert OptionItem("38", "Ghana / +233 / $0.054").display() == "38 - Ghana / +233 / $0.054"
    assert OptionItem("dr", "dr").display() == "dr"


def test_parse_dynamic_display_writes_only_config_value() -> None:
    assert parse_dynamic_display("38 - Ghana / +233 / $0.054") == "38"
    assert parse_dynamic_display("dr") == "dr"
    assert parse_dynamic_display("") == ""


def test_country_label_prefers_chinese_country_name() -> None:
    country = PhoneCountry("US", "1", "garbled-name", 12, price=0.12, count=34)

    label = _country_label(country)

    assert "\u7f8e\u56fd" in label
    assert "garbled-name" not in label
    assert "+1" in label


def test_country_label_uses_allowed_country_chinese_name() -> None:
    country = PhoneCountry("GH", "233", "Ghana", 38, price=0.09, count=8)

    label = _country_label(country)

    assert "\u52a0\u7eb3" in label
    assert "GH" in label


def test_allowed_sms_country_filter_excludes_non_whitelist_country() -> None:
    allowed = PhoneCountry("US", "1", "United States", 12)
    blocked = PhoneCountry("GB", "44", "United Kingdom", 16)

    assert filter_allowed_sms_countries([allowed, blocked]) == [allowed]


def test_smsbower_endpoint_is_code_default() -> None:
    assert DEFAULT_ENDPOINT == "https://smsbower.page/stubs/handler_api.php"


def test_herosms_provider_get_services_uses_services_list_action(monkeypatch) -> None:
    calls: list[str] = []

    def fake_request(self, action: str, **_params):
        calls.append(action)
        return {"services": {"dr": {"name": "OpenAI"}}}

    monkeypatch.setattr(HeroSMSProvider, "request", fake_request)

    assert HeroSMSProvider("key").get_services() == {"dr": "OpenAI"}
    assert calls == ["getServicesList"]


def test_herosms_parse_services_response_accepts_nested_payloads() -> None:
    payload = {"data": {"services": [{"code": "dr", "name": "OpenAI"}, {"activate_org_code": "tg", "title": "Telegram"}]}}

    assert parse_services_response(payload) == {"dr": "OpenAI", "tg": "Telegram"}


def test_herosms_service_dropdown_uses_provider_services(monkeypatch) -> None:
    class FakeHeroSMSProvider:
        def __init__(self, api_key: str, **_kwargs) -> None:
            self.api_key = api_key

        def get_services(self) -> dict[str, str]:
            return {"dr": "OpenAI", "tg": "Telegram"}

    monkeypatch.setattr(sms_options, "HeroSMSProvider", FakeHeroSMSProvider)

    options = dynamic_env_options("HERO_SMS_SERVICE", {"HERO_SMS_API_KEY": "hero-key"})

    assert [item.display() for item in options] == ["dr - OpenAI", "tg - Telegram"]


def test_smsbower_country_dropdown_parses_provider_price_map(monkeypatch) -> None:
    class FakeSmsBowerProvider:
        def __init__(self, api_key: str, **_kwargs) -> None:
            self.api_key = api_key

        def get_countries(self) -> list[dict]:
            return [{"id": "12", "eng": "United States", "prefix": "1"}]

        def list_country_prices(self, service: str, countries: list[PhoneCountry]) -> list[PhoneCountry]:
            assert service == "dr"
            return [PhoneCountry("US", "1", "United States", 12, price=0.02, count=320)]

    monkeypatch.setattr(sms_options, "SmsBowerProvider", FakeSmsBowerProvider)

    options = dynamic_env_options("SMSBOWER_COUNTRY_SELECT", {"SMSBOWER_API_KEY": "sms-key", "SMSBOWER_SERVICE": "dr"})

    assert [item.display() for item in options] == ["12 - \u7f8e\u56fd / +1 / US / $0.02 / \u5e93\u5b58 320"]


def test_smsbower_service_dropdown_uses_code_default_endpoint(monkeypatch) -> None:
    captured: list[dict] = []

    class FakeSmsBowerProvider:
        def __init__(self, api_key: str, **kwargs) -> None:
            self.api_key = api_key
            captured.append(kwargs)

        def get_services(self) -> dict[str, str]:
            return {"dr": "OpenAI"}

    monkeypatch.setattr(sms_options, "SmsBowerProvider", FakeSmsBowerProvider)

    options = dynamic_env_options(
        "SMSBOWER_SERVICE",
        {"SMSBOWER_API_KEY": "sms-key"},
    )

    assert "base_url" not in captured[0]
    assert [item.display() for item in options] == ["auto - \u81ea\u52a8\u8bc6\u522b OpenAI/ChatGPT", "dr - OpenAI"]


def test_sub2api_groups_use_bearer_paginated_endpoint_and_keep_openai_groups(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_get(url: str, headers: dict, params: dict, timeout: int):
        calls.append({"url": url, "headers": headers, "params": params, "timeout": timeout})
        return SimpleNamespace(
            status_code=200,
            json=lambda: {
                "data": {"items": [
                    {"id": 5, "name": "codex", "platform": "openai"},
                    {"id": 6, "name": "claude", "platform": "anthropic"},
                    {"groupId": 8, "group_name": "no-platform"},
                ]}
            },
        )

    monkeypatch.setattr(sms_options, "requests", SimpleNamespace(get=fake_get), raising=False)
    monkeypatch.setitem(__import__("sys").modules, "requests", SimpleNamespace(get=fake_get))

    options = dynamic_env_options(
        "SUB2API_GROUP_IDS",
        {"SUB2API_SERVER_URL": "https://sub.example/api/v1", "SUB2API_API_KEY": "sub-key"},
    )

    assert calls[0]["url"] == "https://sub.example/api/v1/admin/groups"
    assert calls[0]["headers"]["authorization"] == "Bearer sub-key"
    assert calls[0]["params"] == {
        "page": 1,
        "page_size": 50,
        "status": "",
        "sort_by": "sort_order",
        "sort_order": "asc",
        "timezone": "Asia/Shanghai",
    }
    assert calls[0]["timeout"] == 15
    assert [item.display() for item in options] == ["5 - codex / openai", "8 - no-platform / openai"]


def test_sub2api_groups_parse_real_admin_groups_payload(monkeypatch) -> None:
    def fake_get(url: str, headers: dict, params: dict, timeout: int):
        assert url == "https://sub.example/api/v1/admin/groups"
        assert headers["authorization"] == "Bearer sub-key"
        assert params["page_size"] == 50
        return SimpleNamespace(
            status_code=200,
            json=lambda: {
                "code": 0,
                "message": "success",
                "data": {
                    "items": [
                        {"id": 2, "name": "codex", "platform": "openai", "status": "active"},
                        {"id": 3, "name": "image2", "platform": "openai", "status": "active"},
                        {"id": 5, "name": "openai-plus", "platform": "openai", "status": "active"},
                        {"id": 7, "name": "fofa\u4e2d\u8f6c\u7684\u4e2d\u8f6c", "platform": "openai", "status": "active"},
                    ],
                    "total": 7,
                    "page": 1,
                    "page_size": 50,
                    "pages": 1,
                },
            },
        )

    monkeypatch.setitem(__import__("sys").modules, "requests", SimpleNamespace(get=fake_get))

    options = dynamic_env_options(
        "SUB2API_GROUP_IDS",
        {"SUB2API_SERVER_URL": "https://sub.example", "SUB2API_API_KEY": "sub-key"},
    )

    assert [item.value for item in options] == ["2", "7", "3", "5"]


def test_sub2api_groups_fallback_to_x_api_key(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_get(_url: str, headers: dict, params: dict, timeout: int):
        calls.append(headers)
        if "authorization" in headers:
            return SimpleNamespace(status_code=401, json=lambda: {})
        return SimpleNamespace(status_code=200, json=lambda: [{"id": 7, "name": "codex", "platform": "openai"}])

    monkeypatch.setitem(__import__("sys").modules, "requests", SimpleNamespace(get=fake_get))

    options = dynamic_env_options(
        "SUB2API_GROUP_IDS",
        {"SUB2API_SERVER_URL": "https://sub.example", "SUB2API_API_KEY": "sub-key"},
    )

    assert calls[1]["x-api-key"] == "sub-key"
    assert [item.value for item in options] == ["7"]


def test_sub2api_groups_fallback_to_groups_all_endpoint(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_get(url: str, headers: dict, params: dict, timeout: int):
        calls.append({"url": url, "headers": headers, "params": params, "timeout": timeout})
        if url.endswith("/groups"):
            return SimpleNamespace(status_code=404, json=lambda: {})
        return SimpleNamespace(status_code=200, json=lambda: {"data": {"items": [{"id": 9, "name": "codex"}], "total": 1}})

    monkeypatch.setitem(__import__("sys").modules, "requests", SimpleNamespace(get=fake_get))

    options = dynamic_env_options(
        "SUB2API_GROUP_IDS",
        {"SUB2API_SERVER_URL": "https://sub.example", "SUB2API_API_KEY": "sub-key"},
    )

    assert calls[-1]["url"] == "https://sub.example/api/v1/admin/groups/all"
    assert calls[-1]["params"] == {"platform": "openai"}
    assert [item.display() for item in options] == ["9 - codex / openai"]
