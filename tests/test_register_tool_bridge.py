import asyncio
from types import SimpleNamespace

import main
from modules import register_tool_bridge as bridge


class FakeProxyPool:
    def count(self) -> int:
        return 1

    def pick(self, worker_id: int) -> str:
        return f"http://proxy-{worker_id}"


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
    assert captured["kwargs"]["sms_selection"] is sms_selection
    assert captured["kwargs"]["create_payment_link"] is False
    assert captured["kwargs"]["session_cache_path"] == session_cache
    assert captured["kwargs"]["session_source"] == "register_only_gui"
    assert session_cache.exists()
    assert in_progress_file.exists()
    assert failed_file.exists()
    assert used_file.read_text(encoding="utf-8") == "user@example.com\n"
