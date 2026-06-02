import asyncio

from modules import paypal_register
from modules import paypal_flow_state
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
