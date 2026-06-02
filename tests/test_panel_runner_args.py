import json
from types import SimpleNamespace

import panel_runner


def test_default_config_resolves_next_to_frozen_exe(monkeypatch, tmp_path) -> None:
    exe = tmp_path / "ChatGPTAssistantPanel.exe"
    exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(panel_runner.sys, "frozen", True, raising=False)
    monkeypatch.setattr(panel_runner.sys, "executable", str(exe))

    assert panel_runner.resolve_config_path("config.yaml") == tmp_path / "config.yaml"


def test_default_env_resolves_next_to_frozen_exe(monkeypatch, tmp_path) -> None:
    exe = tmp_path / "ChatGPTAssistantPanel.exe"
    exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(panel_runner.sys, "frozen", True, raising=False)
    monkeypatch.setattr(panel_runner.sys, "executable", str(exe))

    assert panel_runner.resolve_env_path(".env") == tmp_path / ".env"


def test_flow_key_for_actions() -> None:
    assert panel_runner.flow_key_for_action("register-only") == "flow1"
    assert panel_runner.flow_key_for_action("paypal-flow1") == "flow1"
    assert panel_runner.flow_key_for_action("paypal-auto") == "flow1"
    assert panel_runner.flow_key_for_action("paypal-flow2-nocard") == "flow1"
    assert panel_runner.flow_key_for_action("paypal-flow2-jp") == "flow1"
    assert panel_runner.flow_key_for_action("paypal-flow2-jp-nocard") == "flow1"
    assert panel_runner.flow_key_for_action("paypal-flow2-filler") == "flow1"
    assert panel_runner.flow_key_for_action("paypal-auto-nocard") == "flow1"
    assert panel_runner.flow_key_for_action("paypal-auto-filler") == "flow1"
    assert panel_runner.flow_key_for_action("paypal-flow3") == "flow3"


def test_parse_paypal_flow1_args() -> None:
    args = panel_runner.parse_args(["paypal-flow1", "--count", "2", "--workers", "1"])

    assert args.action == "paypal-flow1"
    assert args.count == 2
    assert args.workers == 1


def test_parse_mail_source_and_selected_email_args() -> None:
    args = panel_runner.parse_args(
        [
            "paypal-auto",
            "--mail-source",
            "hotmail",
            "--email",
            "User@Hotmail.com",
        ]
    )

    assert args.mail_source == "hotmail"
    assert args.email == "User@Hotmail.com"


def test_parse_all_supported_actions() -> None:
    for action in panel_runner.VALID_ACTIONS:
        args = panel_runner.parse_args([action])
        assert args.action == action
        assert args.count == 1
        assert args.workers == 1


def test_parse_register_only_args() -> None:
    args = panel_runner.parse_args(
        [
            "register-only",
            "--count",
            "3",
            "--workers",
            "2",
            "--mail-source",
            "hotmail",
            "--email",
            "user@example.com",
        ]
    )

    assert args.action == "register-only"
    assert args.count == 3
    assert args.workers == 2
    assert args.mail_source == "hotmail"
    assert args.email == "user@example.com"


def test_result_event_is_json_line() -> None:
    event = panel_runner.result_event("paypal-flow2", "success", "done", account="a@example.com", path="out.txt")

    assert '"type":"result"' in event
    assert '"flow":"paypal-flow2"' in event
    assert '"status":"success"' in event
    assert '"account":"a@example.com"' in event


def test_ignores_benign_playwright_navigation_future_noise() -> None:
    context = {
        "message": "Future exception was never retrieved",
        "exception": RuntimeError("Execution context was destroyed, most likely because of a navigation"),
    }

    assert panel_runner.is_ignorable_playwright_future_noise(context)


def test_does_not_ignore_general_asyncio_errors() -> None:
    context = {
        "message": "Future exception was never retrieved",
        "exception": RuntimeError("real failure"),
    }

    assert not panel_runner.is_ignorable_playwright_future_noise(context)


def test_paypal_flow3_treats_zero_auth_return_code_as_success(monkeypatch, capsys) -> None:
    args = panel_runner.parse_args(["paypal-flow3", "--count", "1", "--workers", "1"])
    monkeypatch.setattr(panel_runner, "_load_panel_config", lambda *args, **kwargs: {})
    monkeypatch.setattr(panel_runner, "_run_paypal_authorize", lambda **kwargs: 0)

    exit_code = panel_runner.run_action(args)

    assert exit_code == 0
    assert '"status":"success"' in capsys.readouterr().out


def test_paypal_flow3_treats_nonzero_auth_return_code_as_failure(monkeypatch, capsys) -> None:
    args = panel_runner.parse_args(["paypal-flow3", "--count", "1", "--workers", "1"])
    monkeypatch.setattr(panel_runner, "_load_panel_config", lambda *args, **kwargs: {})
    monkeypatch.setattr(panel_runner, "_run_paypal_authorize", lambda **kwargs: 1)

    exit_code = panel_runner.run_action(args)

    assert exit_code == 1
    output = capsys.readouterr().out
    assert '"status":"failure"' in output
    assert "flow3 failed code=1" in output


def test_register_only_run_action_delegates_to_bridge(monkeypatch, tmp_path, capsys) -> None:
    cfg = {
        "mail": {"source": "default"},
        "mail_sources": {"hotmail": {"source": "hotmail", "accounts_file": "hotmail.txt"}},
    }
    captured = {}
    summary_file = tmp_path / "output" / "register_only" / "registered_sessions.txt"

    def fake_run_register_tool_only(
        received_cfg,
        *,
        count: int,
        workers: int,
        mail_source: str,
        selected_email: str,
    ):
        captured.update(
            {
                "cfg": received_cfg,
                "count": count,
                "workers": workers,
                "mail_source": mail_source,
                "selected_email": selected_email,
            }
        )
        return SimpleNamespace(
            ok=True,
            returncode=0,
            success_count=2,
            target_count=2,
            summary_file=summary_file,
        )

    args = panel_runner.parse_args(
        [
            "register-only",
            "--count",
            "2",
            "--workers",
            "3",
            "--mail-source",
            "hotmail",
            "--email",
            "user@example.com",
        ]
    )
    monkeypatch.setattr(panel_runner, "_load_panel_config", lambda *args, **kwargs: cfg)
    monkeypatch.setattr(panel_runner, "run_register_tool_only", fake_run_register_tool_only)

    exit_code = panel_runner.run_action(args)

    assert exit_code == 0
    assert captured["count"] == 2
    assert captured["workers"] == 3
    assert captured["mail_source"] == "hotmail"
    assert captured["selected_email"] == "user@example.com"
    assert captured["cfg"]["mail"]["active_source"] == "hotmail"
    output = capsys.readouterr().out
    event = json.loads(output)
    assert event["flow"] == "register-only"
    assert event["status"] == "success"
    assert event["account"] == "user@example.com"
    assert event["path"] == str(summary_file)
