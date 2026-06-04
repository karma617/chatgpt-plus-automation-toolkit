import asyncio

from modules import checkout, paypal_register
from modules import paypal_flow_state
from modules import proxy_pool as proxy_pool_module
from modules.paypal_register import filter_accounts_by_email
from modules.storage import MailAccount


def _isolate_flow_state(monkeypatch, tmp_path):
    state_file = tmp_path / "paypal_flow_state.json"
    discard_file = tmp_path / "paypal_flow_discarded_emails.txt"
    monkeypatch.setattr(paypal_flow_state, "PAYPAL_FLOW_STATE_FILE", state_file)
    monkeypatch.setattr(paypal_flow_state, "PAYPAL_FLOW_DISCARDED_FILE", discard_file)
    return state_file, discard_file


def test_filter_accounts_by_email_is_case_insensitive() -> None:
    accounts = [
        MailAccount(email="first@hotmail.com", raw="first@hotmail.com----x"),
        MailAccount(email="Target@Hotmail.com", raw="Target@Hotmail.com----x"),
    ]

    selected = filter_accounts_by_email(accounts, "target@hotmail.com")

    assert [account.email for account in selected] == ["Target@Hotmail.com"]


def test_filter_accounts_by_email_returns_all_when_empty() -> None:
    accounts = [MailAccount(email="first@hotmail.com", raw="first@hotmail.com----x")]

    assert filter_accounts_by_email(accounts, "") == accounts


def test_flow1_accounts_use_register_only_sessions_only(monkeypatch, tmp_path) -> None:
    accounts_file = tmp_path / "accounts.txt"
    register_only_file = tmp_path / "registered_sessions.txt"
    raw_pool_file = tmp_path / "mail_pool.txt"
    accounts_file.write_text("pool@hotmail.com----pw----client----rt\n", encoding="utf-8")
    register_only_file.write_text("registered@hotmail.com----pw----client----rt\n", encoding="utf-8")
    raw_pool_file.write_text("raw@hotmail.com----pw----client----rt\n", encoding="utf-8")
    monkeypatch.setattr(paypal_register, "REGISTER_ONLY_SUMMARY_FILE", register_only_file)

    accounts = paypal_register._load_registered_flow1_accounts(set())

    assert [account.email for account in accounts] == ["registered@hotmail.com"]
    assert accounts[0].raw == "registered@hotmail.com----pw----client----rt"


def test_remove_from_register_only_sessions_moves_index(tmp_path) -> None:
    register_only_file = tmp_path / "registered_sessions.txt"
    register_only_file.write_text(
        "first@hotmail.com----pw----client----rt\nsecond@hotmail.com----pw----client----rt\n",
        encoding="utf-8",
    )

    paypal_register._remove_from_account_file("first@hotmail.com", register_only_file)

    assert register_only_file.read_text(encoding="utf-8") == "second@hotmail.com----pw----client----rt\n"


def test_flow1_ignores_legacy_used_file_without_deleting_register_only_sessions(monkeypatch, tmp_path) -> None:
    _isolate_flow_state(monkeypatch, tmp_path)
    register_only_file = tmp_path / "registered_sessions.txt"
    used_file = tmp_path / "paypal_flow1_used_emails.txt"
    link_file = tmp_path / "links.txt"
    pending_file = tmp_path / "pending.txt"
    register_only_file.write_text(
        "first@hotmail.com----pw----client----rt\nsecond@hotmail.com----pw----client----rt\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(paypal_register, "REGISTER_ONLY_SUMMARY_FILE", register_only_file)
    monkeypatch.setattr(paypal_register, "REGISTER_ONLY_FLOW1_USED_FILE", used_file)
    monkeypatch.setattr(paypal_register, "LINK_POOL_FILE", link_file)
    monkeypatch.setattr(paypal_register, "PAYPAL_PENDING_AUTH_FILE", pending_file)

    paypal_register._mark_register_only_flow1_used("first@hotmail.com")
    accounts = paypal_register._load_registered_flow1_accounts(
        paypal_flow_state.flow1_blocked_emails(link_file=link_file, pending_file=pending_file)
    )

    assert register_only_file.read_text(encoding="utf-8") == (
        "first@hotmail.com----pw----client----rt\nsecond@hotmail.com----pw----client----rt\n"
    )
    assert used_file.read_text(encoding="utf-8") == "first@hotmail.com\n"
    assert [account.email for account in accounts] == ["first@hotmail.com", "second@hotmail.com"]


def test_save_to_link_pool_keeps_full_mail_account_line(monkeypatch, tmp_path) -> None:
    state_file, _discard_file = _isolate_flow_state(monkeypatch, tmp_path)
    link_file = tmp_path / "account.txt"
    monkeypatch.setattr(paypal_register, "LINK_POOL_DIR", tmp_path)
    monkeypatch.setattr(paypal_register, "LINK_POOL_FILE", link_file)

    paypal_register.save_to_link_pool(
        "user@hotmail.com",
        "user@hotmail.com",
        "https://pay.example/checkout",
        account_line="user@hotmail.com----pw----client----rt",
    )

    assert link_file.read_text(encoding="utf-8") == "user@hotmail.com----pw----client----rt----https://pay.example/checkout\n"
    state = paypal_flow_state.load_state(state_file)
    assert state["user@hotmail.com"]["status"] == paypal_flow_state.STATUS_LINK_READY


def test_save_to_link_pool_records_checkout_method(monkeypatch, tmp_path) -> None:
    state_file, _discard_file = _isolate_flow_state(monkeypatch, tmp_path)
    link_file = tmp_path / "account.txt"
    monkeypatch.setattr(paypal_register, "LINK_POOL_DIR", tmp_path)
    monkeypatch.setattr(paypal_register, "LINK_POOL_FILE", link_file)

    paypal_register.save_to_link_pool(
        "user@hotmail.com",
        "user@hotmail.com",
        "https://pay.example/checkout",
        account_line="user@hotmail.com----pw----client----rt",
        link_method=checkout.CHECKOUT_METHOD_LOCAL_SERVICE,
    )

    state = paypal_flow_state.load_state(state_file)
    assert state["user@hotmail.com"]["link_method"] == checkout.CHECKOUT_METHOD_LOCAL_SERVICE


def test_flow1_reuses_existing_unfinished_link_instead_of_failing(monkeypatch, tmp_path) -> None:
    _isolate_flow_state(monkeypatch, tmp_path)
    register_only_file = tmp_path / "registered_sessions.txt"
    link_file = tmp_path / "account.txt"
    pending_file = tmp_path / "pending.txt"
    register_only_file.write_text("first@hotmail.com----pw----client----rt\n", encoding="utf-8")
    link_file.write_text(
        "first@hotmail.com----pw----client----rt----https://pay.example/checkout\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(paypal_register, "REGISTER_ONLY_SUMMARY_FILE", register_only_file)
    monkeypatch.setattr(paypal_register, "LINK_POOL_FILE", link_file)
    monkeypatch.setattr(paypal_register, "PAYPAL_PENDING_AUTH_FILE", pending_file)
    monkeypatch.setattr(paypal_register, "load_env", lambda path: {})

    result = asyncio.run(
        paypal_register.run_paypal_register(
            {"mail": {"active_source": "hotmail"}, "browser": {"headless": True}},
            count=1,
            workers=1,
        )
    )

    assert result == 1


def test_flow1_logs_in_registered_account_instead_of_registering_again(monkeypatch, tmp_path) -> None:
    _isolate_flow_state(monkeypatch, tmp_path)
    register_only_file = tmp_path / "registered_sessions.txt"
    link_file = tmp_path / "account.txt"
    pending_file = tmp_path / "pending.txt"
    register_only_file.write_text("first@hotmail.com----pw----client----rt\n", encoding="utf-8")
    monkeypatch.setattr(paypal_register, "REGISTER_ONLY_SUMMARY_FILE", register_only_file)
    monkeypatch.setattr(paypal_register, "LINK_POOL_DIR", tmp_path)
    monkeypatch.setattr(paypal_register, "LINK_POOL_FILE", link_file)
    monkeypatch.setattr(paypal_register, "PAYPAL_PENDING_AUTH_FILE", pending_file)
    monkeypatch.setattr(paypal_register, "load_env", lambda path: {})
    calls = []

    async def fake_login(account, mail_source, cfg, **kwargs):
        calls.append((account.email, mail_source, kwargs))
        return "https://pay.example/checkout"

    async def fail_register_again(*args, **kwargs):
        raise AssertionError("registered_sessions accounts must not run signup again")

    monkeypatch.setattr(paypal_register, "login_existing_account_for_checkout", fake_login)
    monkeypatch.setattr(paypal_register, "register_one", fail_register_again)

    result = asyncio.run(
        paypal_register.run_paypal_register(
            {"mail": {"active_source": "hotmail"}, "browser": {"headless": True}},
            count=1,
            workers=1,
        )
    )

    assert result == 1
    assert len(calls) == 1
    email, mail_source, kwargs = calls[0]
    assert (email, mail_source) == ("first@hotmail.com", "hotmail")
    assert kwargs["worker_id"] == 1
    assert kwargs["proxy"] == "http://127.0.0.1:7897"
    assert kwargs["checkout_region"] == "us"
    assert "last_error" in kwargs
    assert link_file.read_text(encoding="utf-8") == (
        "first@hotmail.com----pw----client----rt----https://pay.example/checkout\n"
    )


def test_flow1_proxy_precheck_skips_bad_proxy_and_uses_next(monkeypatch, tmp_path) -> None:
    _isolate_flow_state(monkeypatch, tmp_path)
    register_only_file = tmp_path / "registered_sessions.txt"
    link_file = tmp_path / "account.txt"
    pending_file = tmp_path / "pending.txt"
    proxy_file = tmp_path / "proxies.txt"
    register_only_file.write_text("first@hotmail.com----pw----client----rt\n", encoding="utf-8")
    proxy_file.write_text("http://bad.proxy:1080\nhttp://good.proxy:1080\n", encoding="utf-8")
    monkeypatch.setattr(paypal_register, "REGISTER_ONLY_SUMMARY_FILE", register_only_file)
    monkeypatch.setattr(paypal_register, "LINK_POOL_DIR", tmp_path)
    monkeypatch.setattr(paypal_register, "LINK_POOL_FILE", link_file)
    monkeypatch.setattr(paypal_register, "PAYPAL_PENDING_AUTH_FILE", pending_file)
    monkeypatch.setattr(
        paypal_register,
        "load_env",
        lambda path: {
            "PAYPAL_REGISTER_USE_PROXY": "true",
            "PAYPAL_REGISTER_PROXY_FILE": str(proxy_file),
        },
    )
    used_proxies: list[str | None] = []

    def fake_probe(proxy, timeout_sec=12):
        return (proxy == "http://good.proxy:1080", "bad proxy")

    async def fake_login(account, mail_source, cfg, **kwargs):
        used_proxies.append(kwargs.get("proxy"))
        return "https://pay.example/checkout"

    monkeypatch.setattr(paypal_register, "_probe_proxy", fake_probe)
    monkeypatch.setattr(paypal_register, "login_existing_account_for_checkout", fake_login)

    result = asyncio.run(
        paypal_register.run_paypal_register(
            {"mail": {"active_source": "hotmail"}, "browser": {"headless": True}},
            count=1,
            workers=1,
        )
    )

    assert result == 1
    assert used_proxies == ["http://good.proxy:1080"]


def test_flow1_binds_random_proxy_and_rebinds_after_flow_error(monkeypatch, tmp_path) -> None:
    _isolate_flow_state(monkeypatch, tmp_path)
    register_only_file = tmp_path / "registered_sessions.txt"
    link_file = tmp_path / "account.txt"
    pending_file = tmp_path / "pending.txt"
    proxy_file = tmp_path / "proxies.txt"
    register_only_file.write_text("first@hotmail.com----pw----client----rt\n", encoding="utf-8")
    proxy_file.write_text(
        "http://good-proxy.example.test:1080\nhttp://bad-proxy.example.test:1080\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(paypal_register, "REGISTER_ONLY_SUMMARY_FILE", register_only_file)
    monkeypatch.setattr(paypal_register, "LINK_POOL_DIR", tmp_path)
    monkeypatch.setattr(paypal_register, "LINK_POOL_FILE", link_file)
    monkeypatch.setattr(paypal_register, "PAYPAL_PENDING_AUTH_FILE", pending_file)
    monkeypatch.setattr(
        paypal_register,
        "load_env",
        lambda path: {
            "PAYPAL_REGISTER_USE_PROXY": "true",
            "PAYPAL_REGISTER_PROXY_FILE": str(proxy_file),
        },
    )
    attempts: list[str | None] = []
    shuffled: list[list[str]] = []

    def fake_shuffle(values):
        values.reverse()
        shuffled.append(values[:])

    async def fake_login(account, mail_source, cfg, **kwargs):
        proxy = kwargs.get("proxy")
        attempts.append(proxy)
        if proxy and "bad-proxy" in proxy:
            kwargs["last_error"]["reason"] = "flow failed before checkout"
            return None
        return "https://pay.example/checkout"

    monkeypatch.setattr(paypal_register, "_probe_proxy", lambda proxy, timeout_sec=12: (True, "ok"))
    monkeypatch.setattr(proxy_pool_module.random, "shuffle", fake_shuffle)
    monkeypatch.setattr(paypal_register, "login_existing_account_for_checkout", fake_login)

    result = asyncio.run(
        paypal_register.run_paypal_register(
            {"mail": {"active_source": "hotmail"}, "browser": {"headless": True}},
            count=1,
            workers=1,
        )
    )

    assert result == 1
    assert shuffled == [
        [
            "http://bad-proxy.example.test:1080",
            "http://good-proxy.example.test:1080",
        ]
    ]
    assert attempts == [
        "http://bad-proxy.example.test:1080",
        "http://good-proxy.example.test:1080",
    ]


def test_flow1_discards_account_after_three_mail_code_timeouts(monkeypatch, tmp_path) -> None:
    state_file, discard_file = _isolate_flow_state(monkeypatch, tmp_path)
    register_only_file = tmp_path / "registered_sessions.txt"
    link_file = tmp_path / "account.txt"
    pending_file = tmp_path / "pending.txt"
    account_line = "timeout@hotmail.com----pw----client----rt"
    register_only_file.write_text(account_line + "\n", encoding="utf-8")
    monkeypatch.setattr(paypal_register, "REGISTER_ONLY_SUMMARY_FILE", register_only_file)
    monkeypatch.setattr(paypal_register, "LINK_POOL_DIR", tmp_path)
    monkeypatch.setattr(paypal_register, "LINK_POOL_FILE", link_file)
    monkeypatch.setattr(paypal_register, "PAYPAL_PENDING_AUTH_FILE", pending_file)
    monkeypatch.setattr(paypal_register, "load_env", lambda path: {})
    monkeypatch.setattr(paypal_register, "paypal_register_local_proxy_url", lambda env: "")
    attempts: list[str | None] = []

    async def fake_login(account, mail_source, cfg, **kwargs):
        attempts.append(kwargs.get("proxy"))
        kwargs["last_error"]["kind"] = "mail_code_timeout"
        kwargs["last_error"]["reason"] = "MAIL_CODE_TIMEOUT: no new verification code"
        return None

    monkeypatch.setattr(paypal_register, "login_existing_account_for_checkout", fake_login)

    result = asyncio.run(
        paypal_register.run_paypal_register(
            {"mail": {"active_source": "hotmail"}, "browser": {"headless": True}},
            count=1,
            workers=1,
        )
    )

    assert result == 0
    assert attempts == [None, None, None]
    state = paypal_flow_state.load_state(state_file)
    assert state["timeout@hotmail.com"]["status"] == paypal_flow_state.STATUS_DISCARDED
    assert "mail_code_timeout_after_3_attempts" in state["timeout@hotmail.com"]["reason"]
    assert "timeout@hotmail.com" in discard_file.read_text(encoding="utf-8")


def test_flow1_jp_forces_japan_proxy_and_skips_bad_checkout_methods(monkeypatch, tmp_path) -> None:
    state_file, _discard_file = _isolate_flow_state(monkeypatch, tmp_path)
    register_only_file = tmp_path / "registered_sessions.txt"
    link_file = tmp_path / "account.txt"
    pending_file = tmp_path / "pending.txt"
    proxy_file = tmp_path / "proxies_jp.txt"
    account_line = "jp@example.com----pw----client----rt"
    bad_proxy = "http://bad-us-proxy.example.test:1080"
    good_proxy = "http://good-jp-proxy.example.test:1080"
    register_only_file.write_text(account_line + "\n", encoding="utf-8")
    proxy_file.write_text(f"{bad_proxy}\n{good_proxy}\n", encoding="utf-8")
    monkeypatch.setattr(paypal_register, "REGISTER_ONLY_SUMMARY_FILE", register_only_file)
    monkeypatch.setattr(paypal_register, "LINK_POOL_DIR", tmp_path)
    monkeypatch.setattr(paypal_register, "LINK_POOL_FILE", link_file)
    monkeypatch.setattr(paypal_register, "PAYPAL_PENDING_AUTH_FILE", pending_file)
    monkeypatch.setattr(
        paypal_register,
        "load_env",
        lambda path: {
            "PAYPAL_REGISTER_USE_PROXY": "false",
            "PAYPAL_PROXY_FILE_JP": str(proxy_file),
        },
    )
    paypal_flow_state.mark_needs_link(
        "jp@example.com",
        account_line=account_line,
        reason="previous bad link",
        failed_link_method=checkout.CHECKOUT_METHOD_EXTERNAL_API,
    )
    checks: list[tuple[str, str]] = []
    calls = []

    def fake_probe_country(proxy, required_country_code, timeout_sec=12):
        checks.append((proxy, required_country_code))
        return (proxy == good_proxy, "country=JP" if proxy == good_proxy else "country mismatch: got=US required=JP")

    async def fake_login(account, mail_source, cfg, **kwargs):
        calls.append(kwargs)
        kwargs["checkout_method_sink"]["method"] = checkout.CHECKOUT_METHOD_LOCAL_SERVICE
        return "https://pay.example/checkout"

    monkeypatch.setattr(paypal_register, "_probe_proxy_country", fake_probe_country)
    monkeypatch.setattr(paypal_register, "login_existing_account_for_checkout", fake_login)

    result = asyncio.run(
        paypal_register.run_paypal_register(
            {"mail": {"active_source": "hotmail"}, "browser": {"headless": True}},
            count=1,
            workers=1,
            checkout_region="jp",
        )
    )

    assert result == 1
    assert checks == [(bad_proxy, "JP"), (good_proxy, "JP")]
    assert len(calls) == 1
    assert calls[0]["proxy"] == good_proxy
    assert calls[0]["checkout_region"] == "jp"
    assert calls[0]["checkout_skip_methods"] == {checkout.CHECKOUT_METHOD_EXTERNAL_API}
    state = paypal_flow_state.load_state(state_file)
    assert state["jp@example.com"]["status"] == paypal_flow_state.STATUS_LINK_READY
    assert state["jp@example.com"]["link_method"] == checkout.CHECKOUT_METHOD_LOCAL_SERVICE
    assert state["jp@example.com"]["bad_link_methods"] == [checkout.CHECKOUT_METHOD_EXTERNAL_API]


def test_sync_from_registered_file_reopens_non_manual_discarded_accounts(monkeypatch, tmp_path) -> None:
    state_file, discard_file = _isolate_flow_state(monkeypatch, tmp_path)
    registered_file = tmp_path / "registered_sessions.txt"
    registered_file.write_text(
        "manual@example.com----pw\nold@example.com----pw\n",
        encoding="utf-8",
    )
    discard_file.write_text("manual@example.com\tmanual\n", encoding="utf-8")
    paypal_flow_state.mark_discarded_many(["manual@example.com"], reason="manual_gui")
    paypal_flow_state.mark_discarded_many(["old@example.com"], reason="old_failure")

    paypal_flow_state.sync_from_files(registered_file=registered_file)

    state = paypal_flow_state.load_state(state_file)
    assert state["manual@example.com"]["status"] == paypal_flow_state.STATUS_DISCARDED
    assert state["old@example.com"]["status"] == paypal_flow_state.STATUS_REGISTERED
    assert state["old@example.com"]["account_line"] == "old@example.com----pw"
