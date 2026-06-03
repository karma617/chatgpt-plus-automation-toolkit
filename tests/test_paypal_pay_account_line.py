from modules import paypal_pay
from modules import paypal_filler_bridge
from modules import paypal_flow_state


def _isolate_flow_state(monkeypatch, tmp_path):
    state_file = tmp_path / "paypal_flow_state.json"
    discard_file = tmp_path / "paypal_flow_discarded_emails.txt"
    monkeypatch.setattr(paypal_flow_state, "PAYPAL_FLOW_STATE_FILE", state_file)
    monkeypatch.setattr(paypal_flow_state, "PAYPAL_FLOW_DISCARDED_FILE", discard_file)
    return state_file, discard_file


def test_load_link_pool_keeps_full_mail_account_line(monkeypatch, tmp_path) -> None:
    _isolate_flow_state(monkeypatch, tmp_path)
    link_file = tmp_path / "account.txt"
    pending_file = tmp_path / "pending.txt"
    link_file.write_text(
        "user@hotmail.com----pw----client----rt----https://pay.example/checkout\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(paypal_pay, "LINK_POOL_FILE", link_file)
    monkeypatch.setattr(paypal_pay, "PENDING_AUTH_FILE", pending_file)

    items = paypal_pay.load_link_pool()

    assert items == [
        {
            "email": "user@hotmail.com",
            "query_code": "user@hotmail.com",
            "payment_link": "https://pay.example/checkout",
            "account_line": "user@hotmail.com----pw----client----rt",
        }
    ]


def test_save_pending_auth_keeps_full_mail_account_line(monkeypatch, tmp_path) -> None:
    state_file, _discard_file = _isolate_flow_state(monkeypatch, tmp_path)
    pending_file = tmp_path / "pending.txt"
    monkeypatch.setattr(paypal_pay, "PENDING_AUTH_DIR", tmp_path)
    monkeypatch.setattr(paypal_pay, "PENDING_AUTH_FILE", pending_file)

    paypal_pay.save_pending_auth(
        "user@hotmail.com",
        "user@hotmail.com",
        account_line="user@hotmail.com----pw----client----rt",
    )

    assert pending_file.read_text(encoding="utf-8") == "user@hotmail.com----pw----client----rt\n"
    state = paypal_flow_state.load_state(state_file)
    assert state["user@hotmail.com"]["status"] == paypal_flow_state.STATUS_PAID_PENDING_AUTH


def test_discard_flow2_link_marks_manual_discard_and_removes_link(monkeypatch, tmp_path) -> None:
    state_file, discard_file = _isolate_flow_state(monkeypatch, tmp_path)
    link_file = tmp_path / "account.txt"
    link_file.write_text(
        "user@hotmail.com----pw----client----rt----https://pay.example/checkout\n"
        "next@hotmail.com----pw----client----rt----https://pay.example/next\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(paypal_pay, "LINK_POOL_FILE", link_file)

    paypal_pay.discard_flow2_link("user@hotmail.com", reason="nonzero_checkout_amount: US$20.00")

    assert "user@hotmail.com" not in link_file.read_text(encoding="utf-8")
    assert "next@hotmail.com" in link_file.read_text(encoding="utf-8")
    assert "user@hotmail.com" in discard_file.read_text(encoding="utf-8")
    state = paypal_flow_state.load_state(state_file)
    assert state["user@hotmail.com"]["status"] == paypal_flow_state.STATUS_DISCARDED


def test_classify_checkout_due_amount_detects_zero_and_nonzero() -> None:
    zero = paypal_pay.classify_checkout_due_amount("Due today\nUS$0.00\nPay now")
    nonzero = paypal_pay.classify_checkout_due_amount("Due today\nUS$20.00\nPay now")

    assert zero["status"] == "zero"
    assert nonzero["status"] == "nonzero"
    assert nonzero["amount_value"] == 20.0


def test_classify_checkout_amount_candidates_prefers_order_total() -> None:
    result = paypal_pay.classify_checkout_amount_candidates(
        [
            {
                "selector": "#ProductSummary-totalAmount",
                "priority": 6,
                "text": "ChatGPT Plus Subscription US$20.00 monthly",
            },
            {
                "selector": "[data-testid='order-details-footer-subtotal-amount']",
                "priority": 4,
                "text": "US$20.00",
            },
            {
                "selector": "#OrderDetails-TotalAmount",
                "priority": 0,
                "text": "US$20.00",
            },
        ]
    )

    assert result["status"] == "nonzero"
    assert result["amount_value"] == 20.0
    assert result["selector"] == "#OrderDetails-TotalAmount"


def test_filler_flow2_keeps_full_mail_account_line(monkeypatch, tmp_path) -> None:
    _isolate_flow_state(monkeypatch, tmp_path)
    pending_file = tmp_path / "pending.txt"
    link_file = tmp_path / "account.txt"
    monkeypatch.setattr(paypal_pay, "PENDING_AUTH_DIR", tmp_path)
    monkeypatch.setattr(paypal_pay, "PENDING_AUTH_FILE", pending_file)
    monkeypatch.setattr(paypal_filler_bridge, "PENDING_AUTH_FILE", pending_file)
    monkeypatch.setattr(paypal_filler_bridge, "LINK_POOL_FILE", link_file)
    monkeypatch.setattr(
        paypal_filler_bridge,
        "load_link_pool",
        lambda: [
            {
                "email": "user@hotmail.com",
                "query_code": "user@hotmail.com",
                "payment_link": "https://pay.example/checkout",
                "account_line": "user@hotmail.com----pw----client----rt",
            }
        ],
    )
    monkeypatch.setattr(paypal_filler_bridge, "_run_one", lambda *args, **kwargs: 0)
    removed: list[str] = []
    monkeypatch.setattr(paypal_filler_bridge, "remove_from_link_pool", removed.append)
    monkeypatch.setattr(paypal_filler_bridge, "_flow2_proxy_pool", lambda env: None)
    monkeypatch.setattr(paypal_filler_bridge, "load_env", lambda path: {})

    success = paypal_filler_bridge.run_paypal_filler_flow2({"browser": {"headless": True}}, count=1)

    assert success == 1
    assert pending_file.read_text(encoding="utf-8") == "user@hotmail.com----pw----client----rt\n"
    assert removed == ["user@hotmail.com"]
