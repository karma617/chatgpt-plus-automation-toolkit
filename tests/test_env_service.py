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

    assert "HERO_SMS_API_KEY" in fields
    assert "PAYPAL_CARD_REDEEM_API_KEY" in fields
    assert "PAYPAL_USE_PROXY" in fields
    assert "PAYPAL_REGISTER_USE_PROXY" in fields
    assert "PAYPAL_REGISTER_PROXY_FILE" in fields
    assert "REGISTER_TOOL_ROOT" not in fields
    assert "REGISTER_TOOL_PROXY" not in fields


def test_known_env_fields_render_chinese_labels_with_env_keys() -> None:
    use_proxy = _field_by_key("PAYPAL_REGISTER_USE_PROXY")
    proxy_file = _field_by_key("PAYPAL_REGISTER_PROXY_FILE")

    assert _u(r"PayPal \u6ce8\u518c\u4f7f\u7528\u4ee3\u7406") in str(use_proxy)
    assert "PAYPAL_REGISTER_USE_PROXY" in str(use_proxy)
    assert _u(r"PayPal \u6ce8\u518c\u4ee3\u7406\u6587\u4ef6") in str(proxy_file)
    assert "PAYPAL_REGISTER_PROXY_FILE" in str(proxy_file)


def test_known_env_fields_keep_chinese_label_separate_from_env_key() -> None:
    field = _field_by_key("FLOW1_MAIL_SOURCE")

    assert field.key == "FLOW1_MAIL_SOURCE"
    assert field.label == _u(r"\u6d41\u7a0b1\u90ae\u7bb1\u6765\u6e90")
    assert str(field) == f"{field.label} (FLOW1_MAIL_SOURCE)"
