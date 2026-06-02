import importlib

import modules.utils as utils


def test_resolve_path_uses_frozen_exe_directory(monkeypatch, tmp_path) -> None:
    exe = tmp_path / "ChatGPTAssistantPanel.exe"
    exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(utils.sys, "frozen", True, raising=False)
    monkeypatch.setattr(utils.sys, "executable", str(exe))

    reloaded = importlib.reload(utils)
    try:
        assert reloaded.resolve_path("output/register_only/registered_sessions.txt") == (
            tmp_path / "output" / "register_only" / "registered_sessions.txt"
        )
    finally:
        monkeypatch.setattr(reloaded.sys, "frozen", False, raising=False)
        importlib.reload(reloaded)
