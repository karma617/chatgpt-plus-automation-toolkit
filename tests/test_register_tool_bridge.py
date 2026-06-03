import asyncio
from types import SimpleNamespace

import main
from modules import paypal_flow_state
from modules import register_tool_bridge as bridge


class FakeProxyPool:
    def count(self) -> int:
        return 1

    def pick(self, worker_id: int) -> str:
        return f"http://proxy-{worker_id}"


class FakeStore:
    def __init__(self) -> None:
        self.blocked_emails: set[str] = set()

    def pending_count(self) -> int:
        return 1


def test_run_register_only_many_uses_register_only_outputs_and_disables_payment_link(monkeypatch, tmp_path) -> None:
    output_dir = tmp_path / "output" / "register_only"
    session_dir = output_dir / "sessiond"
    session_cache = session_dir / "session_cache.jsonl"
    summary_file = output_dir / "registered_sessions.txt"
    used_file = output_dir / "used_emails.txt"
    in_progress_file = output_dir / "in_progress.txt"
    failed_file = output_dir / "failed_accounts.txt"
    sms_selection = {"provider_label": "fake-sms"}
    created_store = SimpleNamespace(name="store")
    captured = {}

    async def fake_ensure_register_accounts(cfg, store, desired_count: int) -> int:
        captured["ensure_cfg"] = cfg
        captured["ensure_store"] = store
        captured["ensure_count"] = desired_count
        return desired_count

    async def fake_run_account(cfg, store, worker_id: int, **kwargs):
        captured["run_cfg"] = cfg
        captured["run_store"] = store
        captured["worker_id"] = worker_id
        captured["kwargs"] = kwargs
        summary_file.write_text("user@example.com----session\n", encoding="utf-8")
        return True

    def fake_create_store(cfg):
        captured["store_cfg"] = cfg
        return created_store

    monkeypatch.setattr(bridge, "REGISTER_ONLY_OUTPUT_DIR", output_dir)
    monkeypatch.setattr(bridge, "REGISTER_ONLY_SESSION_DIR", session_dir)
    monkeypatch.setattr(bridge, "REGISTER_ONLY_SESSION_CACHE_FILE", session_cache)
    monkeypatch.setattr(bridge, "REGISTER_ONLY_SUMMARY_FILE", summary_file)
    monkeypatch.setattr(bridge, "REGISTER_ONLY_USED_FILE", used_file)
    monkeypatch.setattr(bridge, "REGISTER_ONLY_IN_PROGRESS_FILE", in_progress_file)
    monkeypatch.setattr(bridge, "REGISTER_ONLY_FAILED_FILE", failed_file)
    monkeypatch.setattr(bridge, "register_only_mode", lambda: "email")
    monkeypatch.setattr(main, "create_store", fake_create_store)
    monkeypatch.setattr(main, "ensure_register_accounts", fake_ensure_register_accounts)
    monkeypatch.setattr(main, "create_proxy_pool", lambda cfg: FakeProxyPool())
    monkeypatch.setattr(main, "resolve_flow1_sms_selection", lambda: sms_selection)
    monkeypatch.setattr(main, "run_account", fake_run_account)
    monkeypatch.setattr(main, "log", lambda message: None)
    monkeypatch.setattr(main, "worker_log", lambda worker_id, message: None)

    result = asyncio.run(
        bridge.run_register_only_many(
            {
                "mail": {"source": "hotmail"},
                "output": {"success_file": "old-success.txt", "failed_file": "old-failed.txt"},
            },
            count=1,
            workers=1,
        )
    )

    assert result.returncode == 0
    assert result.success_count == 1
    assert result.target_count == 1
    assert result.output_dir == session_dir
    assert result.summary_file == summary_file
    assert captured["store_cfg"]["output"]["success_file"] == str(summary_file)
    assert captured["store_cfg"]["output"]["failed_file"] == str(failed_file)
    assert captured["store_cfg"]["output"]["in_progress_file"] == str(in_progress_file)
    assert captured["ensure_store"] is created_store
    assert captured["ensure_count"] == 1
    assert captured["run_store"] is created_store
    assert captured["worker_id"] == 1
    assert captured["kwargs"]["proxy"] == "http://proxy-1"
    assert captured["kwargs"]["sms_selection"] is None
    assert captured["kwargs"]["create_payment_link"] is False
    assert captured["kwargs"]["session_cache_path"] == session_cache
    assert captured["kwargs"]["session_source"] == "register_only_gui"
    assert session_cache.exists()
    assert in_progress_file.exists()
    assert failed_file.exists()
    assert used_file.read_text(encoding="utf-8") == "user@example.com\n"


def test_register_only_mode_defaults_to_email() -> None:
    assert bridge.register_only_mode({}) == "email"
    assert bridge.register_only_mode({"REGISTER_ONLY_MODE": "phone"}) == "phone"
    assert bridge.register_only_mode({"REGISTER_ONLY_MODE": "手机号注册"}) == "phone"
    assert bridge.register_only_mode({"REGISTER_ONLY_MODE": "bad-value"}) == "email"


def test_run_register_only_many_uses_sms_only_in_phone_mode(monkeypatch, tmp_path) -> None:
    output_dir = tmp_path / "output" / "register_only"
    session_dir = output_dir / "sessiond"
    session_cache = session_dir / "session_cache.jsonl"
    summary_file = output_dir / "registered_sessions.txt"
    used_file = output_dir / "used_emails.txt"
    in_progress_file = output_dir / "in_progress.txt"
    failed_file = output_dir / "failed_accounts.txt"
    sms_selection = {"provider_label": "fake-sms"}
    created_store = SimpleNamespace(name="store")
    captured = {}

    async def fake_ensure_register_accounts(cfg, store, desired_count: int) -> int:
        return desired_count

    async def fake_run_account(cfg, store, worker_id: int, **kwargs):
        captured["kwargs"] = kwargs
        summary_file.write_text("phone@example.com----session\n", encoding="utf-8")
        return True

    monkeypatch.setattr(bridge, "REGISTER_ONLY_OUTPUT_DIR", output_dir)
    monkeypatch.setattr(bridge, "REGISTER_ONLY_SESSION_DIR", session_dir)
    monkeypatch.setattr(bridge, "REGISTER_ONLY_SESSION_CACHE_FILE", session_cache)
    monkeypatch.setattr(bridge, "REGISTER_ONLY_SUMMARY_FILE", summary_file)
    monkeypatch.setattr(bridge, "REGISTER_ONLY_USED_FILE", used_file)
    monkeypatch.setattr(bridge, "REGISTER_ONLY_IN_PROGRESS_FILE", in_progress_file)
    monkeypatch.setattr(bridge, "REGISTER_ONLY_FAILED_FILE", failed_file)
    monkeypatch.setattr(bridge, "register_only_mode", lambda: "phone")
    monkeypatch.setattr(main, "create_store", lambda cfg: created_store)
    monkeypatch.setattr(main, "ensure_register_accounts", fake_ensure_register_accounts)
    monkeypatch.setattr(main, "create_proxy_pool", lambda cfg: None)
    monkeypatch.setattr(main, "resolve_flow1_sms_selection", lambda: sms_selection)
    monkeypatch.setattr(main, "run_account", fake_run_account)
    monkeypatch.setattr(main, "log", lambda message: None)
    monkeypatch.setattr(main, "worker_log", lambda worker_id, message: None)

    result = asyncio.run(
        bridge.run_register_only_many(
            {
                "mail": {"source": "hotmail"},
                "output": {"success_file": "old-success.txt", "failed_file": "old-failed.txt"},
            },
            count=1,
            workers=1,
        )
    )

    assert result.returncode == 0
    assert captured["kwargs"]["sms_selection"] is sms_selection


def test_apply_paypal_blocked_emails_blocks_discarded_registered_and_linked(monkeypatch, tmp_path) -> None:
    output_dir = tmp_path / "output" / "register_only"
    summary_file = output_dir / "registered_sessions.txt"
    used_file = output_dir / "used_emails.txt"
    state_file = output_dir / "paypal_flow_state.json"
    discard_file = output_dir / "paypal_flow_discarded_emails.txt"
    link_file = tmp_path / "output" / "paypal" / "links" / "account.txt"
    pending_file = tmp_path / "output" / "paypal" / "pending" / "account.txt"
    summary_file.parent.mkdir(parents=True)
    summary_file.write_text("registered@example.com----pw\n", encoding="utf-8")
    used_file.write_text("used@example.com\n", encoding="utf-8")
    discard_file.write_text("discarded@example.com\tmanual\n", encoding="utf-8")
    link_file.parent.mkdir(parents=True)
    link_file.write_text("linked@example.com----pw----https://pay.example\n", encoding="utf-8")
    pending_file.parent.mkdir(parents=True)
    pending_file.write_text("pending@example.com----pw\n", encoding="utf-8")

    monkeypatch.setattr(bridge, "REGISTER_ONLY_SUMMARY_FILE", summary_file)
    monkeypatch.setattr(bridge, "REGISTER_ONLY_USED_FILE", used_file)
    monkeypatch.setattr(bridge, "PAYPAL_LINK_POOL_FILE", link_file)
    monkeypatch.setattr(bridge, "PAYPAL_PENDING_AUTH_FILE", pending_file)
    monkeypatch.setattr(paypal_flow_state, "PAYPAL_FLOW_STATE_FILE", state_file)
    monkeypatch.setattr(paypal_flow_state, "PAYPAL_FLOW_DISCARDED_FILE", discard_file)
    store = FakeStore()

    blocked = bridge.apply_paypal_blocked_emails(store)

    assert {
        "registered@example.com",
        "used@example.com",
        "discarded@example.com",
        "linked@example.com",
        "pending@example.com",
    }.issubset(blocked)
    assert blocked.issubset(store.blocked_emails)
