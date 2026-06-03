from pathlib import Path

import control_panel_app
from modules import paypal_flow_state


def test_paypal_state_page_mark_pending_auth_writes_pool_and_state(monkeypatch, tmp_path: Path) -> None:
    page = control_panel_app.PaypalAccountStatePage.__new__(control_panel_app.PaypalAccountStatePage)
    page.state_file = tmp_path / "output" / "register_only" / "paypal_flow_state.json"
    page.discard_file = tmp_path / "output" / "register_only" / "paypal_flow_discarded_emails.txt"
    page.registered_file = tmp_path / "output" / "register_only" / "registered_sessions.txt"
    page.link_file = tmp_path / "output" / "paypal" / "links" / "account.txt"
    page.pending_file = tmp_path / "output" / "paypal" / "pending" / "account.txt"

    page.registered_file.parent.mkdir(parents=True)
    page.registered_file.write_text("user@example.com----pw----client----rt\n", encoding="utf-8")
    page.discard_file.write_text("user@example.com\tmanual\n", encoding="utf-8")
    monkeypatch.setattr(paypal_flow_state, "PAYPAL_FLOW_STATE_FILE", page.state_file)
    monkeypatch.setattr(paypal_flow_state, "PAYPAL_FLOW_DISCARDED_FILE", page.discard_file)
    paypal_flow_state.mark_completed_many(["user@example.com"])
    shown = {}
    monkeypatch.setattr(control_panel_app.messagebox, "showinfo", lambda title, message: shown.update(title=title, message=message))
    monkeypatch.setattr(page, "_require_selection", lambda: ["user@example.com"])
    monkeypatch.setattr(page, "refresh", lambda: None)

    page.mark_pending_auth()

    assert page.pending_file.read_text(encoding="utf-8") == "user@example.com----pw----client----rt\n"
    assert "user@example.com" not in page.discard_file.read_text(encoding="utf-8")
    state = paypal_flow_state.load_state(page.state_file)
    assert state["user@example.com"]["status"] == paypal_flow_state.STATUS_PAID_PENDING_AUTH
    assert shown["title"] == "标记授权"
