import control_panel_app
from control_panel_app import strip_ansi_for_display


def test_strip_ansi_for_display_removes_color_codes() -> None:
    raw = "\x1b[91m\x1b[1m[FAIL]\x1b[0m bad\n"

    assert strip_ansi_for_display(raw) == "[FAIL] bad\n"


def test_run_page_blocks_jp_flow1_when_long_link_disabled(monkeypatch, tmp_path) -> None:
    page = control_panel_app.RunPage.__new__(control_panel_app.RunPage)
    page.process = None
    page.root_path = tmp_path
    (tmp_path / ".env").write_text("PAYPAL_USE_LONG_LINK=false\n", encoding="utf-8")
    shown = {}
    monkeypatch.setattr(control_panel_app.messagebox, "showinfo", lambda title, message: shown.update(title=title, message=message))
    monkeypatch.setattr(page, "_runner_command", lambda action: (_ for _ in ()).throw(AssertionError("runner must not start")))

    page.start("paypal-flow1-jp")

    assert "title" in shown
    assert "PAYPAL_USE_LONG_LINK=false" in shown["message"]
