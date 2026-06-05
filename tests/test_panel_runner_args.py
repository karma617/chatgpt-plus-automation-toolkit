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
    assert panel_runner.flow_key_for_action("paypal-flow1-jp") == "flow1"
    assert panel_runner.flow_key_for_action("paypal-auto") == "flow1"
    assert panel_runner.flow_key_for_action("paypal-flow2-nocard") == "flow1"
    assert panel_runner.flow_key_for_action("paypal-flow2-jp") == "flow1"
    assert panel_runner.flow_key_for_action("paypal-flow2-jp-nocard") == "flow1"
    assert panel_runner.flow_key_for_action("paypal-flow2-filler") == "flow1"
    assert panel_runner.flow_key_for_action("paypal-auto-nocard") == "flow1"
    assert panel_runner.flow_key_for_action("paypal-auto-jp-nocard") == "flow1"
    assert panel_runner.flow_key_for_action("paypal-auto-filler") == "flow1"
    assert panel_runner.flow_key_for_action("paypal-flow3") == "flow3"


def test_parse_paypal_flow1_args() -> None:
    args = panel_runner.parse_args(["paypal-flow1", "--count", "2", "--workers", "1"])

    assert args.action == "paypal-flow1"
    assert args.count == 2
    assert args.workers == 1


def test_parse_paypal_flow1_jp_args() -> None:
    args = panel_runner.parse_args(["paypal-flow1-jp", "--count", "2", "--workers", "1"])

    assert args.action == "paypal-flow1-jp"
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


def test_mail_source_choices_do_not_expose_hotmail_graph() -> None:
    try:
        panel_runner.parse_args(["paypal-flow1", "--mail-source", "hotmail_graph"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("hotmail_graph should not be accepted as a panel mail source")


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


def test_paypal_flow1_result_uses_last_run_detail(monkeypatch, tmp_path, capsys) -> None:
    args = panel_runner.parse_args(["paypal-flow1", "--count", "1", "--workers", "1"])
    link_file = tmp_path / "account.txt"
    link_file.write_text("user@example.com----pw----client----rt----https://pay.example\n", encoding="utf-8")

    async def fake_run_paypal_register(*_args, **_kwargs):
        panel_runner.get_last_run_detail()
        import modules.paypal_register as paypal_register

        paypal_register._set_last_run_detail(
            "no new registered accounts; reused existing unfinished links=1/4; next=paypal-flow2/paypal-auto",
            path=str(link_file),
        )
        return 1

    monkeypatch.setattr(panel_runner, "_load_panel_config", lambda *args, **kwargs: {})
    monkeypatch.setattr(panel_runner, "run_paypal_register", fake_run_paypal_register)

    exit_code = panel_runner.run_action(args)

    assert exit_code == 0
    event = json.loads(capsys.readouterr().out)
    assert event["flow"] == "paypal-flow1"
    assert event["status"] == "success"
    assert event["message"] == "no new registered accounts; reused existing unfinished links=1/4; next=paypal-flow2/paypal-auto"
    assert event["path"] == str(link_file)


def test_paypal_flow1_jp_delegates_to_register_with_jp_region(monkeypatch, capsys) -> None:
    args = panel_runner.parse_args(["paypal-flow1-jp", "--count", "1", "--workers", "1", "--email", "user@example.com"])
    captured = {}

    async def fake_run_paypal_register(*args, **kwargs):
        captured.update(kwargs)
        return 1

    monkeypatch.setattr(panel_runner, "_load_panel_config", lambda *args, **kwargs: {})
    monkeypatch.setattr(panel_runner, "load_env", lambda path: {"PAYPAL_PAYMENT_MODE": "long_link"})
    monkeypatch.setattr(panel_runner, "run_paypal_register", fake_run_paypal_register)

    exit_code = panel_runner.run_action(args)

    assert exit_code == 0
    assert captured["checkout_region"] == "jp"
    assert captured["selected_email"] == "user@example.com"
    event = json.loads(capsys.readouterr().out)
    assert event["flow"] == "paypal-flow1-jp"
    assert event["status"] == "success"


def test_paypal_flow1_jp_short_link_mode_returns_hint_without_register(monkeypatch, capsys) -> None:
    args = panel_runner.parse_args(["paypal-flow1-jp", "--count", "1", "--workers", "1"])

    async def fail_run_paypal_register(*args, **kwargs):
        raise AssertionError("flow1 should be skipped when PAYPAL_PAYMENT_MODE=short_link")

    monkeypatch.setattr(panel_runner, "_load_panel_config", lambda *args, **kwargs: {})
    monkeypatch.setattr(panel_runner, "load_env", lambda path: {"PAYPAL_PAYMENT_MODE": "short_link"})
    monkeypatch.setattr(panel_runner, "run_paypal_register", fail_run_paypal_register)

    exit_code = panel_runner.run_action(args)

    assert exit_code == 0
    event = json.loads(capsys.readouterr().out)
    assert event["flow"] == "paypal-flow1-jp"
    assert event["status"] == "success"
    assert panel_runner._u(r"\u77ed\u94fe\u652f\u4ed8") in event["message"]
    assert panel_runner._u(r"\u6d41\u7a0b2 \u65e5\u672c\u4ee3\u7406(\u65e0\u5361)") in event["message"]


def test_paypal_auto_reuses_existing_link_when_flow1_reports_reused_link(monkeypatch, capsys) -> None:
    args = panel_runner.parse_args(["paypal-auto-nocard", "--count", "1", "--workers", "1"])
    captured = {}
    pending_calls = {"count": 0}

    async def fake_register(*args, **kwargs):
        return 1

    async def fake_pay(*args, **kwargs):
        captured.update(kwargs)
        return 1

    def fake_pending_count(*args, **kwargs):
        pending_calls["count"] += 1
        return 1 if pending_calls["count"] >= 2 else 0

    monkeypatch.setattr(panel_runner, "_load_panel_config", lambda *args, **kwargs: {})
    monkeypatch.setattr(panel_runner, "run_paypal_register", fake_register)
    monkeypatch.setattr(panel_runner, "run_paypal_pay", fake_pay)
    monkeypatch.setattr(panel_runner, "_run_paypal_authorize", lambda **kwargs: 0)
    monkeypatch.setattr(panel_runner, "_count_payment_links", lambda selected_email="": 1)
    monkeypatch.setattr(panel_runner, "_count_pending_auth", fake_pending_count)

    exit_code = panel_runner.run_action(args)

    assert exit_code == 0
    assert captured["card_source_mode"] == "local_random"
    assert captured["count"] == 1
    assert '"status":"success"' in capsys.readouterr().out


def test_paypal_auto_stops_when_flow1_fails_even_if_old_pending_exists(monkeypatch, capsys) -> None:
    args = panel_runner.parse_args(["paypal-auto-jp-nocard", "--count", "1", "--workers", "1"])
    calls = []

    async def fake_flow1(*args, **kwargs):
        calls.append(("flow1", kwargs))
        return 0

    async def fail_flow2(*args, **kwargs):
        calls.append(("flow2", kwargs))
        raise AssertionError("flow2 should not run after flow1 failure")

    def fail_authorize(**kwargs):
        calls.append(("flow3", kwargs))
        raise AssertionError("flow3 should not run after flow1 failure")

    monkeypatch.setattr(panel_runner, "_load_panel_config", lambda *args, **kwargs: {})
    monkeypatch.setattr(panel_runner, "load_env", lambda path: {"PAYPAL_PAYMENT_MODE": "long_link"})
    monkeypatch.setattr(panel_runner, "run_register_tool_only", lambda *args, **kwargs: SimpleNamespace(ok=True, returncode=0, success_count=1, target_count=1, summary_file="registered.txt"))
    monkeypatch.setattr(panel_runner, "run_paypal_register", fake_flow1)
    monkeypatch.setattr(panel_runner, "run_paypal_pay", fail_flow2)
    monkeypatch.setattr(panel_runner, "_run_paypal_authorize", fail_authorize)
    monkeypatch.setattr(panel_runner, "_count_flow1_ready_registered", lambda selected_email="": 0)
    monkeypatch.setattr(panel_runner, "_count_payment_links", lambda selected_email="": 0)
    monkeypatch.setattr(panel_runner, "_count_pending_auth", lambda selected_email="": 1)

    exit_code = panel_runner.run_action(args)

    assert exit_code == 1
    assert [name for name, _kwargs in calls] == ["flow1"]
    event = json.loads(capsys.readouterr().out)
    assert event["status"] == "failure"
    assert "flow1 failed" in event["message"]


def test_paypal_auto_stops_when_flow2_fails_even_if_old_pending_exists(monkeypatch, capsys) -> None:
    args = panel_runner.parse_args(["paypal-auto-jp-nocard", "--count", "1", "--workers", "1"])
    calls = []

    async def fake_flow1(*args, **kwargs):
        calls.append(("flow1", kwargs))
        return 1

    async def fake_flow2(*args, **kwargs):
        calls.append(("flow2", kwargs))
        return 0

    def fail_authorize(**kwargs):
        calls.append(("flow3", kwargs))
        raise AssertionError("flow3 should not run after flow2 failure")

    monkeypatch.setattr(panel_runner, "_load_panel_config", lambda *args, **kwargs: {})
    monkeypatch.setattr(panel_runner, "load_env", lambda path: {"PAYPAL_PAYMENT_MODE": "long_link"})
    monkeypatch.setattr(panel_runner, "run_register_tool_only", lambda *args, **kwargs: SimpleNamespace(ok=True, returncode=0, success_count=1, target_count=1, summary_file="registered.txt"))
    monkeypatch.setattr(panel_runner, "run_paypal_register", fake_flow1)
    monkeypatch.setattr(panel_runner, "run_paypal_pay", fake_flow2)
    monkeypatch.setattr(panel_runner, "_run_paypal_authorize", fail_authorize)
    monkeypatch.setattr(panel_runner, "_count_flow1_ready_registered", lambda selected_email="": 0)
    monkeypatch.setattr(panel_runner, "_count_payment_links", lambda selected_email="": 1)
    monkeypatch.setattr(panel_runner, "_count_pending_auth", lambda selected_email="": 1)

    exit_code = panel_runner.run_action(args)

    assert exit_code == 1
    assert [name for name, _kwargs in calls] == ["flow1", "flow2"]
    event = json.loads(capsys.readouterr().out)
    assert event["status"] == "failure"
    assert "flow2 failed" in event["message"]


def test_paypal_auto_jp_nocard_runs_full_jp_chain(monkeypatch, tmp_path, capsys) -> None:
    args = panel_runner.parse_args(
        [
            "paypal-auto-jp-nocard",
            "--count",
            "1",
            "--workers",
            "1",
            "--mail-source",
            "hotmail",
            "--email",
            "user@example.com",
        ]
    )
    calls = []
    cfg = {
        "mail": {"source": "default"},
        "mail_sources": {"hotmail": {"source": "hotmail", "accounts_file": "hotmail.txt"}},
    }
    summary_file = tmp_path / "registered_sessions.txt"
    pending_calls = {"count": 0}
    link_calls = {"count": 0}

    def fake_register_only(*args, **kwargs):
        calls.append(("register-only", kwargs))
        return SimpleNamespace(
            ok=True,
            returncode=0,
            success_count=1,
            target_count=1,
            summary_file=summary_file,
            message="",
        )

    async def fake_flow1(*args, **kwargs):
        calls.append(("flow1", kwargs))
        return 1

    async def fake_flow2(*args, **kwargs):
        calls.append(("flow2", kwargs))
        return 1

    def fake_pending_count(*args, **kwargs):
        pending_calls["count"] += 1
        return 1 if pending_calls["count"] >= 2 else 0

    def fake_authorize(**kwargs):
        calls.append(("flow3", kwargs))
        return 0

    monkeypatch.setattr(panel_runner, "_load_panel_config", lambda *args, **kwargs: cfg)
    monkeypatch.setattr(panel_runner, "load_env", lambda path: {"PAYPAL_PAYMENT_MODE": "long_link"})
    monkeypatch.setattr(panel_runner, "run_register_tool_only", fake_register_only)
    monkeypatch.setattr(panel_runner, "run_paypal_register", fake_flow1)
    monkeypatch.setattr(panel_runner, "run_paypal_pay", fake_flow2)
    monkeypatch.setattr(panel_runner, "_run_paypal_authorize", fake_authorize)
    monkeypatch.setattr(panel_runner, "_count_flow1_ready_registered", lambda selected_email="": 0)
    monkeypatch.setattr(
        panel_runner,
        "_count_payment_links",
        lambda selected_email="": link_calls.update(count=link_calls["count"] + 1) or (1 if link_calls["count"] >= 2 else 0),
    )
    monkeypatch.setattr(panel_runner, "_count_pending_auth", fake_pending_count)

    exit_code = panel_runner.run_action(args)

    assert exit_code == 0
    assert [name for name, _kwargs in calls] == ["register-only", "flow1", "flow2", "flow3"]
    assert calls[0][1]["mail_source"] == "hotmail"
    assert calls[0][1]["selected_email"] == "user@example.com"
    assert calls[1][1]["checkout_region"] == "jp"
    assert calls[2][1]["card_source_mode"] == "local_random"
    assert calls[2][1]["flow2_region_mode"] == "jp"
    assert calls[3][1]["count"] == 1
    event = json.loads(capsys.readouterr().out)
    assert event["flow"] == "paypal-auto-jp-nocard"
    assert event["status"] == "success"


def test_paypal_auto_jp_nocard_reuses_ready_registered_account_without_registering(monkeypatch, capsys) -> None:
    args = panel_runner.parse_args(
        [
            "paypal-auto-jp-nocard",
            "--count",
            "1",
            "--workers",
            "1",
            "--mail-source",
            "hotmail",
        ]
    )
    calls = []
    pending_calls = {"count": 0}

    def fail_register_only(*args, **kwargs):
        raise AssertionError("register-only should not run when registered account is ready")

    async def fake_flow1(*args, **kwargs):
        calls.append(("flow1", kwargs))
        return 1

    async def fake_flow2(*args, **kwargs):
        calls.append(("flow2", kwargs))
        return 1

    def fake_pending_count(*args, **kwargs):
        pending_calls["count"] += 1
        return 1 if pending_calls["count"] >= 2 else 0

    def fake_authorize(**kwargs):
        calls.append(("flow3", kwargs))
        return 0

    monkeypatch.setattr(
        panel_runner,
        "_load_panel_config",
        lambda *args, **kwargs: {
            "mail": {"source": "hotmail"},
            "mail_sources": {"hotmail": {"source": "hotmail", "accounts_file": "hotmail.txt"}},
        },
    )
    monkeypatch.setattr(panel_runner, "load_env", lambda path: {"PAYPAL_PAYMENT_MODE": "long_link"})
    monkeypatch.setattr(panel_runner, "run_register_tool_only", fail_register_only)
    monkeypatch.setattr(panel_runner, "_count_flow1_ready_registered", lambda selected_email="": 1)
    monkeypatch.setattr(panel_runner, "run_paypal_register", fake_flow1)
    monkeypatch.setattr(panel_runner, "run_paypal_pay", fake_flow2)
    monkeypatch.setattr(panel_runner, "_run_paypal_authorize", fake_authorize)
    monkeypatch.setattr(panel_runner, "_count_payment_links", lambda selected_email="": 1)
    monkeypatch.setattr(panel_runner, "_count_pending_auth", fake_pending_count)

    exit_code = panel_runner.run_action(args)

    assert exit_code == 0
    assert [name for name, _kwargs in calls] == ["flow1", "flow2", "flow3"]
    assert calls[0][1]["checkout_region"] == "jp"
    event = json.loads(capsys.readouterr().out)
    assert event["status"] == "success"


def test_paypal_auto_jp_nocard_without_long_link_skips_flow1(monkeypatch, capsys) -> None:
    args = panel_runner.parse_args(
        [
            "paypal-auto-jp-nocard",
            "--count",
            "1",
            "--workers",
            "1",
            "--mail-source",
            "hotmail",
        ]
    )
    calls = []
    pending_calls = {"count": 0}

    async def fail_flow1(*args, **kwargs):
        raise AssertionError("flow1 should be skipped when PAYPAL_PAYMENT_MODE=short_link")

    async def fake_flow2(*args, **kwargs):
        calls.append(("flow2", kwargs))
        return 1

    def fake_pending_count(*args, **kwargs):
        pending_calls["count"] += 1
        return 1 if pending_calls["count"] >= 2 else 0

    def fake_authorize(**kwargs):
        calls.append(("flow3", kwargs))
        return 0

    monkeypatch.setattr(
        panel_runner,
        "_load_panel_config",
        lambda *args, **kwargs: {
            "mail": {"source": "hotmail"},
            "mail_sources": {"hotmail": {"source": "hotmail", "accounts_file": "hotmail.txt"}},
        },
    )
    monkeypatch.setattr(panel_runner, "load_env", lambda path: {"PAYPAL_PAYMENT_MODE": "short_link"})
    monkeypatch.setattr(panel_runner, "_count_flow1_ready_registered", lambda selected_email="": 0)
    monkeypatch.setattr(panel_runner, "_count_direct_pay_ready", lambda selected_email="": 1)
    monkeypatch.setattr(panel_runner, "_count_payment_links", lambda selected_email="": 0)
    monkeypatch.setattr(panel_runner, "_count_pending_auth", fake_pending_count)
    monkeypatch.setattr(panel_runner, "run_paypal_register", fail_flow1)
    monkeypatch.setattr(panel_runner, "run_paypal_pay", fake_flow2)
    monkeypatch.setattr(panel_runner, "_run_paypal_authorize", fake_authorize)

    exit_code = panel_runner.run_action(args)

    assert exit_code == 0
    assert [name for name, _kwargs in calls] == ["flow2", "flow3"]
    assert calls[0][1]["card_source_mode"] == "local_random"
    assert calls[0][1]["flow2_region_mode"] == "jp"
    event = json.loads(capsys.readouterr().out)
    assert event["status"] == "success"


def test_paypal_auto_jp_nocard_legacy_long_link_false_still_skips_flow1(monkeypatch, capsys) -> None:
    args = panel_runner.parse_args(
        [
            "paypal-auto-jp-nocard",
            "--count",
            "1",
            "--workers",
            "1",
            "--mail-source",
            "hotmail",
        ]
    )
    calls = []
    pending_calls = {"count": 0}

    async def fail_flow1(*args, **kwargs):
        raise AssertionError("flow1 should be skipped when legacy PAYPAL_USE_LONG_LINK=false")

    async def fake_flow2(*args, **kwargs):
        calls.append(("flow2", kwargs))
        return 1

    def fake_pending_count(*args, **kwargs):
        pending_calls["count"] += 1
        return 1 if pending_calls["count"] >= 2 else 0

    def fake_authorize(**kwargs):
        calls.append(("flow3", kwargs))
        return 0

    monkeypatch.setattr(
        panel_runner,
        "_load_panel_config",
        lambda *args, **kwargs: {
            "mail": {"source": "hotmail"},
            "mail_sources": {"hotmail": {"source": "hotmail", "accounts_file": "hotmail.txt"}},
        },
    )
    monkeypatch.setattr(panel_runner, "load_env", lambda path: {"PAYPAL_USE_LONG_LINK": "false"})
    monkeypatch.setattr(panel_runner, "_count_flow1_ready_registered", lambda selected_email="": 0)
    monkeypatch.setattr(panel_runner, "_count_direct_pay_ready", lambda selected_email="": 1)
    monkeypatch.setattr(panel_runner, "_count_payment_links", lambda selected_email="": 0)
    monkeypatch.setattr(panel_runner, "_count_pending_auth", fake_pending_count)
    monkeypatch.setattr(panel_runner, "run_paypal_register", fail_flow1)
    monkeypatch.setattr(panel_runner, "run_paypal_pay", fake_flow2)
    monkeypatch.setattr(panel_runner, "_run_paypal_authorize", fake_authorize)

    exit_code = panel_runner.run_action(args)

    assert exit_code == 0
    assert [name for name, _kwargs in calls] == ["flow2", "flow3"]
    event = json.loads(capsys.readouterr().out)
    assert event["status"] == "success"


def test_paypal_auto_jp_nocard_without_long_link_registers_then_direct_pay(monkeypatch, capsys) -> None:
    args = panel_runner.parse_args(
        [
            "paypal-auto-jp-nocard",
            "--count",
            "1",
            "--workers",
            "1",
            "--mail-source",
            "hotmail",
        ]
    )
    calls = []
    direct_counts = iter([0, 1, 1])
    pending_counts = iter([0, 1])

    def fake_register_tool_only(*args, **kwargs):
        calls.append(("register-only", kwargs))
        return SimpleNamespace(ok=True, success_count=1, target_count=1, returncode=0, summary_file="registered_sessions.txt")

    async def fail_flow1(*args, **kwargs):
        raise AssertionError("flow1 should still be skipped after register-only when PAYPAL_USE_LONG_LINK=false")

    async def fake_flow2(*args, **kwargs):
        calls.append(("flow2", kwargs))
        return 1

    def fake_authorize(**kwargs):
        calls.append(("flow3", kwargs))
        return 0

    monkeypatch.setattr(
        panel_runner,
        "_load_panel_config",
        lambda *args, **kwargs: {
            "mail": {"source": "hotmail"},
            "mail_sources": {"hotmail": {"source": "hotmail", "accounts_file": "hotmail.txt"}},
        },
    )
    monkeypatch.setattr(panel_runner, "load_env", lambda path: {"PAYPAL_USE_LONG_LINK": "false"})
    monkeypatch.setattr(panel_runner, "_count_flow1_ready_registered", lambda selected_email="": 0)
    monkeypatch.setattr(panel_runner, "_count_direct_pay_ready", lambda selected_email="": next(direct_counts))
    monkeypatch.setattr(panel_runner, "_count_payment_links", lambda selected_email="": 0)
    monkeypatch.setattr(panel_runner, "_count_pending_auth", lambda selected_email="": next(pending_counts))
    monkeypatch.setattr(panel_runner, "run_register_tool_only", fake_register_tool_only)
    monkeypatch.setattr(panel_runner, "run_paypal_register", fail_flow1)
    monkeypatch.setattr(panel_runner, "run_paypal_pay", fake_flow2)
    monkeypatch.setattr(panel_runner, "_run_paypal_authorize", fake_authorize)

    exit_code = panel_runner.run_action(args)

    assert exit_code == 0
    assert [name for name, _kwargs in calls] == ["register-only", "flow2", "flow3"]
    event = json.loads(capsys.readouterr().out)
    assert event["status"] == "success"


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
        env=None,
    ):
        captured.update(
            {
                "cfg": received_cfg,
                "count": count,
                "workers": workers,
                "mail_source": mail_source,
                "selected_email": selected_email,
                "env": env,
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
    assert isinstance(captured["env"], dict)
    assert captured["cfg"]["mail"]["active_source"] == "hotmail"
    output = capsys.readouterr().out
    event = json.loads(output)
    assert event["flow"] == "register-only"
    assert event["status"] == "success"
    assert event["account"] == "user@example.com"
    assert event["path"] == str(summary_file)
