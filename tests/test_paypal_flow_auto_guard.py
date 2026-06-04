from modules import paypal_flow
from modules import paypal_flow_state
from modules import proxy_pool as proxy_pool_module
from modules import session_export
from modules.storage import MailAccount


def _u(text: str) -> str:
    return text.encode("ascii").decode("unicode_escape")


class FakePhonePool:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def count(self) -> int:
        return 1


def _stub_menu(monkeypatch, choices: list[str], errors: list[str]) -> None:
    import main

    monkeypatch.setattr(main, "ui_header", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "ui_footer", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "ui_kv_row", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "ui_option", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "ui_error", lambda message: errors.append(message))
    monkeypatch.setattr(main, "ui_prompt", lambda prompt: choices.pop(0))
    monkeypatch.setattr(main, "ask_positive_int", lambda *args, **kwargs: 1)


def test_interactive_auto_stops_after_flow1_failure(monkeypatch) -> None:
    errors: list[str] = []
    calls: list[str] = []
    _stub_menu(monkeypatch, ["6", "7"], errors)

    async def fake_flow1(*args, **kwargs):
        calls.append("flow1")
        return 0

    async def fail_flow2(*args, **kwargs):
        calls.append("flow2")
        raise AssertionError("flow2 should not run after flow1 failure")

    def fail_authorize(**kwargs):
        calls.append("flow3")
        raise AssertionError("flow3 should not run after flow1 failure")

    monkeypatch.setattr(paypal_flow, "load_env", lambda path: {})
    monkeypatch.setattr(paypal_flow, "_count_accounts_file", lambda path: 1)
    monkeypatch.setattr(paypal_flow, "load_link_pool", lambda: [])
    monkeypatch.setattr(paypal_flow, "_count_authorizable_pending", lambda *args, **kwargs: 0)
    monkeypatch.setattr(paypal_flow, "PhonePool", FakePhonePool)
    monkeypatch.setattr(paypal_flow, "run_paypal_register", fake_flow1)
    monkeypatch.setattr(paypal_flow, "run_paypal_pay", fail_flow2)
    monkeypatch.setattr(paypal_flow, "_run_paypal_authorize", fail_authorize)

    result = paypal_flow.interactive_paypal(cfg={"mail": {"source": "hotmail"}, "browser": {}})

    assert result == 0
    assert calls == ["flow1"]
    assert any(_u(r"\u6d41\u7a0b1\u672a\u751f\u6210\u672c\u8f6e\u957f\u94fe\u63a5") in message for message in errors)


def test_interactive_auto_stops_after_flow2_failure(monkeypatch) -> None:
    errors: list[str] = []
    calls: list[str] = []
    _stub_menu(monkeypatch, ["6", "7"], errors)

    async def fake_flow1(*args, **kwargs):
        calls.append("flow1")
        return 1

    async def fake_flow2(*args, **kwargs):
        calls.append("flow2")
        return 0

    def fail_authorize(**kwargs):
        calls.append("flow3")
        raise AssertionError("flow3 should not run after flow2 failure")

    monkeypatch.setattr(paypal_flow, "load_env", lambda path: {})
    monkeypatch.setattr(paypal_flow, "_count_accounts_file", lambda path: 1)
    monkeypatch.setattr(
        paypal_flow,
        "load_link_pool",
        lambda: [{"email": "user@example.com", "payment_link": "https://pay.example"}],
    )
    monkeypatch.setattr(paypal_flow, "_count_authorizable_pending", lambda *args, **kwargs: 1)
    monkeypatch.setattr(paypal_flow, "PhonePool", FakePhonePool)
    monkeypatch.setattr(paypal_flow, "run_paypal_register", fake_flow1)
    monkeypatch.setattr(paypal_flow, "run_paypal_pay", fake_flow2)
    monkeypatch.setattr(paypal_flow, "_run_paypal_authorize", fail_authorize)

    result = paypal_flow.interactive_paypal(cfg={"mail": {"source": "hotmail"}, "browser": {}})

    assert result == 0
    assert calls == ["flow1", "flow2"]
    assert any(_u(r"\u6d41\u7a0b2\u672c\u8f6e\u672a\u4ea7\u751f\u5f85\u6388\u6743\u8d26\u53f7") in message for message in errors)


def test_session_export_binds_random_proxy_and_rebinds_after_hydrate_error(monkeypatch, tmp_path) -> None:
    proxy_file = tmp_path / "proxies.txt"
    cache_file = tmp_path / "session_cache.jsonl"
    proxy_file.write_text(
        "http://good-proxy.example.test:1080\nhttp://bad-proxy.example.test:1080\n",
        encoding="utf-8",
    )
    attempts: list[str | None] = []

    def fake_shuffle(values):
        values.reverse()

    async def fake_login_existing(account, mail_source, cfg, **kwargs):
        proxy = kwargs.get("proxy")
        attempts.append(proxy)
        if proxy and "bad-proxy" in proxy:
            kwargs["last_error"]["reason"] = "session endpoint returned 403"
            return None
        return ""

    monkeypatch.setattr(paypal_flow, "PAYPAL_SESSIOND_DIR", tmp_path)
    monkeypatch.setattr(paypal_flow, "PAYPAL_SESSION_CACHE_FILE", cache_file)
    monkeypatch.setattr(
        paypal_flow,
        "load_env",
        lambda path: {
            "PAYPAL_REGISTER_USE_PROXY": "true",
            "PAYPAL_REGISTER_PROXY_FILE": str(proxy_file),
        },
    )
    monkeypatch.setattr(
        paypal_flow,
        "_build_account_lookup",
        lambda cfg: {
            "user@example.com": MailAccount(
                email="user@example.com",
                password="pw",
                raw="user@example.com----pw",
            )
        },
    )
    monkeypatch.setattr(proxy_pool_module.random, "shuffle", fake_shuffle)
    monkeypatch.setattr(paypal_flow, "login_existing_account_for_checkout", fake_login_existing)
    monkeypatch.setattr(
        session_export,
        "read_paid_records",
        lambda path: [{"account": "user@example.com", "raw": "user@example.com----pw"}],
    )
    monkeypatch.setattr(session_export, "load_session_cache", lambda path: [])
    monkeypatch.setattr(session_export, "find_cache_record", lambda account, records: None)
    monkeypatch.setattr(
        session_export,
        "export_paid_sessions",
        lambda **kwargs: {
            "success": 1,
            "total": 1,
            "skipped": [],
            "outputs": [{"email": "user@example.com"}],
        },
    )
    monkeypatch.setattr(paypal_flow_state, "mark_completed_many", lambda emails: None)

    result = paypal_flow._run_paypal_session_export(cfg={"mail": {"source": "hotmail"}, "browser": {}})

    assert result == 0
    assert attempts == [
        "http://bad-proxy.example.test:1080",
        "http://good-proxy.example.test:1080",
    ]
