import authorization_flow
from error_classifier import classify_error


ROOT = authorization_flow.AUTH_ROOT


def test_build_auth_command_uses_longer_account_timeout(tmp_path) -> None:
    command = authorization_flow.build_auth_command(
        {"account": "user@example.com", "code_address": "user@example.com"},
        tmp_path / "account.txt",
        tmp_path / "out",
    )

    index = command.index("--account-timeout-seconds")
    assert command[index + 1] == "360"


def test_no_valid_organizations_accounts_are_not_removed(monkeypatch, tmp_path) -> None:
    del monkeypatch, tmp_path
    source = (ROOT / "authorization_flow.py").read_text(encoding="utf-8")

    assert "no_valid_organizations 账号暂不移除，保留在待授权池等待下次重跑" in source
    assert "removed_no_valid_org" not in source
    assert "auth_no_valid_organizations" not in source


def test_sms_country_attempt_plan_uses_selected_then_next_low_price() -> None:
    selected = authorization_flow.PhoneCountry("ID", "62", "Indonesia", 6, price=0.045, count=100)
    fallback = authorization_flow.PhoneCountry("TH", "66", "Thailand", 50, price=0.075, count=80)
    third = authorization_flow.PhoneCountry("FR", "33", "France", 56, price=0.1, count=10)

    plan = authorization_flow.sms_country_attempt_plan(
        {"country": selected, "countries": [selected, fallback, third]},
        limit=2,
    )

    assert [item["country"] for item in plan] == [selected, fallback]


def test_build_auth_command_passes_sms_rescue_controls(tmp_path) -> None:
    country = authorization_flow.PhoneCountry("ID", "62", "Indonesia", 6, price=0.045, count=100)
    command = authorization_flow.build_auth_command(
        {"account": "user@example.com", "code_address": "user@example.com"},
        tmp_path / "account.txt",
        tmp_path / "out",
        {
            "provider": "herosms",
            "api_key": "sms-key",
            "service": "dr",
            "country": country,
            "operator": authorization_flow.OperatorQuote("", "any", None, None),
            "poll_interval": 5,
            "max_attempts": 60,
            "phone_retry_limit": 50,
            "phone_retry_interval": 5,
            "code_timeout": 60,
            "code_page_retry_limit": 5,
            "reuse_ttl_seconds": 1200,
        },
        auth_sms_state_file=tmp_path / "sms_state.json",
    )

    assert "--save-store" in command
    assert command[command.index("--auth-sms-state-file") + 1] == str(tmp_path / "sms_state.json")
    assert command[command.index("--sms-phone-retry-limit") + 1] == "50"
    assert command[command.index("--sms-phone-retry-interval") + 1] == "5"
    assert command[command.index("--sms-code-timeout") + 1] == "60"
    assert command[command.index("--sms-code-page-retry-limit") + 1] == "5"
    assert command[command.index("--auth-sms-reuse-ttl-seconds") + 1] == "1200"


def test_sms_rescue_error_classification() -> None:
    assert classify_error("AUTH_SMS_COUNTRY_NO_NUMBER: country=ID") == "auth_sms_country_exhausted"
    assert classify_error("AUTH_SMS_CODE_PAGE_RETRY_EXHAUSTED: limit=5") == "phone_required"
