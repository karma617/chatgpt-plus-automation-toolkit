from pathlib import Path

from control_panel.env_service import EnvField, get_known_env_fields, read_env, update_env


def _u(text: str) -> str:
    return text.encode("ascii").decode("unicode_escape")


def _field_by_key(key: str) -> EnvField:
    return next(field for field in get_known_env_fields() if field.key == key)


def test_read_env_parses_key_value_pairs(tmp_path: Path) -> None:
    target = tmp_path / ".env"
    target.write_text("# comment\nA=1\nB = two\nEMPTY=\n", encoding="utf-8")

    values = read_env(target)

    assert values == {"A": "1", "B": "two", "EMPTY": ""}


def test_read_missing_env_returns_empty_dict(tmp_path: Path) -> None:
    assert read_env(tmp_path / ".env") == {}


def test_update_env_preserves_comments_and_unknown_keys(tmp_path: Path) -> None:
    target = tmp_path / ".env"
    target.write_text("# keep\nA=1\nUNKNOWN=value\n", encoding="utf-8")

    update_env(target, {"A": "changed"})

    assert target.read_text(encoding="utf-8") == "# keep\nA=changed\nUNKNOWN=value\n"


def test_update_env_adds_missing_keys_at_end(tmp_path: Path) -> None:
    target = tmp_path / ".env"
    target.write_text("A=1\n", encoding="utf-8")

    update_env(target, {"NEW_KEY": "new-value"})

    assert target.read_text(encoding="utf-8") == "A=1\nNEW_KEY=new-value\n"


def test_update_env_keeps_env_key_when_field_has_chinese_label(tmp_path: Path) -> None:
    target = tmp_path / ".env"
    target.write_text("PAYPAL_REGISTER_PROXY_FILE=old\n", encoding="utf-8")
    field = _field_by_key("PAYPAL_REGISTER_PROXY_FILE")

    update_env(target, {field: "data/proxies/proxies_jp.txt"})

    assert target.read_text(encoding="utf-8") == "PAYPAL_REGISTER_PROXY_FILE=data/proxies/proxies_jp.txt\n"
    assert str(field) not in target.read_text(encoding="utf-8")


def test_known_env_fields_include_api_keys() -> None:
    fields = get_known_env_fields()

    assert "REGISTER_ONLY_MODE" in fields
    assert "MOEMAIL_BASE_URL" in fields
    assert "MAIL_ACCOUNT_MODE" in fields
    assert "HERO_SMS_POLL_INTERVAL" in fields
    assert "SMS_PHONE_RETRY_LIMIT" in fields
    assert "SMS_PHONE_RETRY_INTERVAL" in fields
    assert "GRIZZLY_PROMPT_PROVIDER_SELECTION" in fields
    assert "FIVESIM_OPERATOR_THRESHOLD" in fields
    assert "SMSBOWER_API_KEY" in fields
    assert "SMSBOWER_API_URL" in fields
    assert "SMSBOWER_SERVICE" in fields
    assert "SMSBOWER_COUNTRY_SELECT" in fields
    assert "YESCAPTCHA_API_KEY" in fields
    assert "HERO_SMS_API_KEY" in fields
    assert "PAYPAL_CARD_REDEEM_API_KEY" in fields
    assert "PAYPAL_USE_PROXY" in fields
    assert "PAYPAL_REGISTER_USE_PROXY" in fields
    assert "PAYPAL_REGISTER_LOCAL_PROXY_URL" in fields
    assert "LOCAL_PROXY_URL" in fields
    assert "PAYPAL_PROXY_FILE_US" in fields
    assert "PAYPAL_PROXY_FILE_JP" in fields
    assert "PAYPAL_REGISTER_PROXY_FILE" in fields
    assert "REGISTER_LOCAL_PROXY_URL" in fields
    assert "REGISTER_TOOL_ROOT" not in fields
    assert "REGISTER_TOOL_PROXY" not in fields


def test_known_env_fields_render_chinese_labels_without_env_keys() -> None:
    use_proxy = _field_by_key("PAYPAL_REGISTER_USE_PROXY")
    proxy_file = _field_by_key("PAYPAL_REGISTER_PROXY_FILE")

    assert _u(r"PayPal \u6ce8\u518c\u4f7f\u7528\u4ee3\u7406") in str(use_proxy)
    assert "PAYPAL_REGISTER_USE_PROXY" not in str(use_proxy)
    assert _u(r"PayPal \u6ce8\u518c\u4ee3\u7406\u6587\u4ef6") in str(proxy_file)
    assert "PAYPAL_REGISTER_PROXY_FILE" not in str(proxy_file)


def test_proxy_fields_split_local_us_and_jp_labels() -> None:
    local_proxy = _field_by_key("LOCAL_PROXY_URL")
    register_local_proxy = _field_by_key("REGISTER_LOCAL_PROXY_URL")
    paypal_register_local_proxy = _field_by_key("PAYPAL_REGISTER_LOCAL_PROXY_URL")
    us_proxy = _field_by_key("PAYPAL_PROXY_FILE_US")
    jp_proxy = _field_by_key("PAYPAL_PROXY_FILE_JP")

    assert str(local_proxy) == _u(r"\u672c\u5730\u4ee3\u7406\u5730\u5740")
    assert str(register_local_proxy) == _u(r"\u6ce8\u518c\u6d41\u7a0b\u672c\u5730\u4ee3\u7406\u5730\u5740")
    assert str(paypal_register_local_proxy) == _u(r"PayPal \u6ce8\u518c\u672c\u5730\u4ee3\u7406\u5730\u5740")
    assert str(us_proxy) == _u(r"PayPal \u7f8e\u56fd\u4ee3\u7406\u6587\u4ef6")
    assert str(jp_proxy) == _u(r"PayPal \u65e5\u672c\u4ee3\u7406\u6587\u4ef6")
    assert _u(r"\u9ed8\u8ba4 http://127.0.0.1:7987") in local_proxy.tooltip
    assert _u(r"\u9ed8\u8ba4 http://127.0.0.1:7897") in register_local_proxy.tooltip
    assert _u(r"\u9ed8\u8ba4 http://127.0.0.1:7897") in paypal_register_local_proxy.tooltip
    assert _u(r"\u4e0d\u4f1a\u518d\u6df7\u7528\u7f8e\u56fd\u4ee3\u7406") in jp_proxy.tooltip


def test_known_env_fields_keep_chinese_label_separate_from_env_key() -> None:
    field = _field_by_key("FLOW1_MAIL_SOURCE")

    assert field.key == "FLOW1_MAIL_SOURCE"
    assert field.label == _u(r"\u6d41\u7a0b1\u90ae\u7bb1\u6765\u6e90")
    assert str(field) == field.label


def test_sms_fields_include_human_readable_tooltips() -> None:
    top_n = _field_by_key("SMSBOWER_COUNTRY_TOP_N")
    threshold = _field_by_key("GRIZZLY_PROVIDER_THRESHOLD")
    retry_limit = _field_by_key("SMS_PHONE_RETRY_LIMIT")

    assert _u(r"\u663e\u793a\u5ec9\u4ef7\u56fd\u5bb6\u6570") in top_n.label
    assert _u(r"\u53ea\u5f71\u54cd\u5019\u9009\u5217\u8868\u957f\u5ea6") in top_n.tooltip
    assert _u(r"\u5e93\u5b58\u4f4e\u4e8e\u6b64\u503c") in threshold.label
    assert _u(r"\u4e8c\u6b21\u9009\u62e9") in threshold.tooltip
    assert _u(r"\u540c\u56fd\u5bb6\u6362\u53f7\u91cd\u8bd5\u6b21\u6570") in retry_limit.label
    assert _u(r"\u624b\u673a\u53f7\u8868\u5355") in retry_limit.tooltip


def test_sms_provider_fields_are_grouped_by_platform() -> None:
    assert _field_by_key("HERO_SMS_API_KEY").group == "Hero SMS"
    assert _field_by_key("HERO_SMS_SERVICE").group == "Hero SMS"
    assert _field_by_key("GRIZZLY_API_KEY").group == "Grizzly SMS"
    assert _field_by_key("GRIZZLY_COUNTRY_SELECT").group == "Grizzly SMS"
    assert _field_by_key("FIVESIM_API_KEY").group == "5sim"
    assert _field_by_key("SMSBOWER_API_KEY").group == "SMSBower"
    assert _field_by_key("SMSBOWER_COUNTRY_SELECT").group == "SMSBower"


def test_known_env_fields_include_dropdown_choices_for_enum_values() -> None:
    assert _field_by_key("REGISTER_ONLY_MODE").choices == ("email", "phone")
    assert _field_by_key("MAIL_ACCOUNT_MODE").choices == ("pool", "api")
    assert _field_by_key("MOEMAIL_DOMAIN_MODE").choices == ("random", "fixed", "rotate")
    assert _field_by_key("MOEMAIL_CREATE_MODE").choices == ("human", "random")
    assert _field_by_key("PAYPAL_CAPTCHA_MODE").choices == ("manual", "api")
    assert _field_by_key("CAPTCHA_API_PROVIDER").choices == ("capsolver", "twocaptcha", "yescaptcha")
    assert _field_by_key("PAYPAL_USE_PROXY").choices == ("true", "false")
    assert _field_by_key("SMS_PROVIDER").choices == ("herosms", "grizzly", "fivesim", "smsbower")
    assert _field_by_key("FLOW1_MAIL_SOURCE").choices == ("moemail", "hotmail", "icloud_query", "domain163")
    assert _field_by_key("GRIZZLY_PROMPT_PROVIDER_SELECTION").choices == ("true", "false")
    assert _field_by_key("SMSBOWER_PROMPT_PROVIDER_SELECTION").choices == ("true", "false")


def test_known_env_fields_keep_freeform_inputs_without_choices() -> None:
    assert _field_by_key("HERO_SMS_API_KEY").choices == ()
    assert _field_by_key("YESCAPTCHA_API_KEY").choices == ()
    assert _field_by_key("PROXY_FILE").choices == ()
    assert _field_by_key("HERO_SMS_SERVICE").choices == ()
    assert _field_by_key("FIVESIM_COUNTRY_SELECT").choices == ()


def test_all_known_env_fields_have_tooltips() -> None:
    missing = [field.key for field in get_known_env_fields() if not field.tooltip]

    assert missing == []


def test_upload_config_only_exposes_url_and_key_fields() -> None:
    keys = {field.key for field in get_known_env_fields()}

    assert {"CPA_SERVER_URL", "CPA_SERVER_API_KEY", "SUB2API_SERVER_URL", "SUB2API_API_KEY", "SUB2API_GROUP_IDS"} <= keys
    assert {
        "CPA_SERVER_UPSERT_PATH",
        "CPA_SERVER_API_KEY_HEADER",
        "CPA_SERVER_AUTH_SCHEME",
        "CPA_SERVER_TIMEOUT",
        "SUB2API_IMPORT_PATH",
        "SUB2API_API_KEY_HEADER",
        "SUB2API_AUTH_SCHEME",
        "SUB2API_PROXY_ID",
        "SUB2API_PRIORITY",
        "SUB2API_CONCURRENCY",
        "SUB2API_AUTO_PAUSE_ON_EXPIRED",
        "SUB2API_UPDATE_EXISTING",
        "SUB2API_TIMEOUT",
        "AUTH_SERVER_URL",
        "AUTH_SERVER_UPSERT_PATH",
        "AUTH_SERVER_API_KEY",
        "AUTH_SERVER_API_KEY_HEADER",
        "AUTH_SERVER_AUTH_SCHEME",
        "AUTH_SERVER_TIMEOUT",
    }.isdisjoint(keys)
