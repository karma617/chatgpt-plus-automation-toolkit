import asyncio

from modules import paypal_pay
from modules import paypal_filler_bridge
from modules import paypal_flow_state
from modules import checkout


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


def test_load_direct_pay_accounts_uses_registered_and_link_account_lines(monkeypatch, tmp_path) -> None:
    _isolate_flow_state(monkeypatch, tmp_path)
    registered_file = tmp_path / "registered_sessions.txt"
    link_file = tmp_path / "account.txt"
    pending_file = tmp_path / "pending.txt"
    registered_file.write_text(
        "ready@hotmail.com----pw----client----rt\n",
        encoding="utf-8",
    )
    link_file.write_text(
        "linked@hotmail.com----pw2----client2----rt2----https://pay.example/checkout\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(paypal_pay, "REGISTER_ONLY_SUMMARY_FILE", registered_file)
    monkeypatch.setattr(paypal_pay, "LINK_POOL_FILE", link_file)
    monkeypatch.setattr(paypal_pay, "PENDING_AUTH_FILE", pending_file)

    items = paypal_pay._load_direct_pay_accounts()

    assert [item["email"] for item in items] == ["linked@hotmail.com", "ready@hotmail.com"]
    assert items[0]["account_line"] == "linked@hotmail.com----pw2----client2----rt2"
    assert items[0]["payment_link"] == ""
    assert items[1]["account_line"] == "ready@hotmail.com----pw----client----rt"


def test_paypal_direct_checkout_start_url_defaults() -> None:
    assert paypal_pay.paypal_direct_checkout_start_url({}) == "https://chatgpt.com/"
    assert paypal_pay.paypal_direct_checkout_start_url({"PAYPAL_DIRECT_CHECKOUT_START_URL": " https://chatgpt.com/#pricing "}) == "https://chatgpt.com/#pricing"


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


def test_upsert_link_pool_item_replaces_current_account_link(monkeypatch, tmp_path) -> None:
    link_file = tmp_path / "account.txt"
    link_file.write_text(
        "user@hotmail.com----pw----client----rt----https://pay.example/old\n"
        "next@hotmail.com----pw----client----rt----https://pay.example/next\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(paypal_pay, "LINK_POOL_FILE", link_file)

    paypal_pay.upsert_link_pool_item(
        "user@hotmail.com",
        "user@hotmail.com----pw----client----rt",
        "https://pay.example/new",
    )

    text = link_file.read_text(encoding="utf-8")
    assert "https://pay.example/old" not in text
    assert "user@hotmail.com----pw----client----rt----https://pay.example/new" in text
    assert "next@hotmail.com----pw----client----rt----https://pay.example/next" in text


def test_regenerate_flow2_payment_link_uses_forced_method_order(monkeypatch, tmp_path) -> None:
    _isolate_flow_state(monkeypatch, tmp_path)
    link_file = tmp_path / "account.txt"
    monkeypatch.setattr(paypal_pay, "LINK_POOL_FILE", link_file)
    captured = {}

    async def fake_login_existing_account_for_checkout(account, mail_source, cfg, **kwargs):
        captured["email"] = account.email
        captured["mail_source"] = mail_source
        captured["checkout_region"] = kwargs.get("checkout_region")
        captured["checkout_skip_methods"] = kwargs.get("checkout_skip_methods")
        captured["checkout_preferred_methods"] = kwargs.get("checkout_preferred_methods")
        sink = kwargs.get("checkout_method_sink")
        sink["method"] = checkout.CHECKOUT_METHOD_LOCAL_SERVICE
        return "https://pay.example/recreated"

    import modules.paypal_register as paypal_register

    monkeypatch.setattr(paypal_register, "login_existing_account_for_checkout", fake_login_existing_account_for_checkout)
    item = {
        "email": "user@hotmail.com",
        "account_line": "user@hotmail.com----pw----client----rt",
        "payment_link": "https://pay.example/old",
        "link_method": checkout.CHECKOUT_METHOD_EXTERNAL_API,
    }

    link, method = asyncio.run(
        paypal_pay.regenerate_flow2_payment_link(
            item,
            {"browser": {}, "mail": {}, "chatgpt": {}},
            worker_id=1,
            proxy="http://127.0.0.1:7897",
            flow2_region_mode="jp",
        )
    )

    assert link == "https://pay.example/recreated"
    assert method == checkout.CHECKOUT_METHOD_LOCAL_SERVICE
    assert captured["mail_source"] == "hotmail"
    assert captured["checkout_region"] == "jp"
    assert captured["checkout_skip_methods"] == {checkout.CHECKOUT_METHOD_EXTERNAL_API}
    assert checkout.CHECKOUT_METHOD_EXTERNAL_API not in captured["checkout_preferred_methods"]
    assert captured["checkout_preferred_methods"] == tuple(
        method for method in paypal_pay.PAYPAL_FLOW2_RECREATE_METHOD_ORDER if method != checkout.CHECKOUT_METHOD_EXTERNAL_API
    )
    assert item["payment_link"] == "https://pay.example/recreated"
    assert "https://pay.example/recreated" in link_file.read_text(encoding="utf-8")


def test_paypal_payment_mode_prefers_new_env_and_keeps_legacy_fallback() -> None:
    assert paypal_pay.paypal_payment_mode({}) == paypal_pay.PAYPAL_PAYMENT_MODE_LONG_LINK
    assert paypal_pay.paypal_payment_mode({"PAYPAL_USE_LONG_LINK": "false"}) == paypal_pay.PAYPAL_PAYMENT_MODE_SHORT_LINK
    assert (
        paypal_pay.paypal_payment_mode({"PAYPAL_PAYMENT_MODE": "\u77ed\u94fe\u652f\u4ed8", "PAYPAL_USE_LONG_LINK": "true"})
        == paypal_pay.PAYPAL_PAYMENT_MODE_SHORT_LINK
    )
    assert paypal_pay.paypal_payment_mode({"PAYPAL_PAYMENT_MODE": "long_link"}) == paypal_pay.PAYPAL_PAYMENT_MODE_LONG_LINK
    assert paypal_pay.paypal_use_long_link({"PAYPAL_PAYMENT_MODE": "\u957f\u94fe\u652f\u4ed8"}) is True
    assert paypal_pay.paypal_use_long_link({"PAYPAL_PAYMENT_MODE": "short_link"}) is False


def test_short_link_jp_keeps_jp_proxy_but_uses_us_billing_country(monkeypatch) -> None:
    assert paypal_pay._payment_form_country_code("jp", use_long_link=False) == "US"
    assert paypal_pay._paypal_form_country_code("jp", use_long_link=False) == "US"
    assert paypal_pay._payment_form_country_code("jp", use_long_link=True) == "JP"
    assert paypal_pay._paypal_form_country_code("jp", use_long_link=True) == "JP"

    probes = []

    def fake_probe(proxy, required_country_code, timeout_sec=12):
        probes.append((proxy, required_country_code, timeout_sec))
        return True, "country=JP ip=203.0.113.10"

    monkeypatch.setattr(paypal_pay, "_probe_flow2_proxy_country", fake_probe)

    paypal_pay._ensure_short_link_jp_proxy(
        "socks5h://jp-proxy.example.test:1080",
        env={"PAYPAL_SHORT_LINK_JP_PROXY_CHECK_TIMEOUT": "9"},
        prefix="[test]",
    )

    assert probes == [("socks5h://jp-proxy.example.test:1080", "JP", 9)]


def test_short_link_jp_proxy_country_mismatch_raises(monkeypatch) -> None:
    monkeypatch.setattr(
        paypal_pay,
        "_probe_flow2_proxy_country",
        lambda proxy, required_country_code, timeout_sec=12: (False, "country mismatch: got=US required=JP"),
    )

    try:
        paypal_pay._ensure_short_link_jp_proxy(
            "socks5h://us-proxy.example.test:1080",
            env={},
            prefix="[test]",
        )
    except RuntimeError as exc:
        assert str(exc).startswith(paypal_pay.PAYPAL_FLOW2_JP_PROXY_COUNTRY_MISMATCH)
    else:
        raise AssertionError("expected proxy country mismatch to raise")


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


def test_paypal_pay_fast_otp_submit_and_captcha_cleanup_regression() -> None:
    source = paypal_pay.Path(paypal_pay.__file__).read_text(encoding="utf-8")

    assert paypal_pay.PAYPAL_FLOW2_CODE_VERSION == "PAYPAL_SHORT_LINK_US_PAY_JP_PROXY_2026-06-05_01"
    assert "_cleanup_hosted_captcha_artifacts(page, timeout_ms=2500)" in source
    assert "await _wait_captcha_cleared(page, timeout_seconds=10)" in source
    assert "if await _is_paypal_verification_stage(page):\n                break" in source
    assert "await btn.click(timeout=2500, no_wait_after=True)" in source
    assert "await _click_paypal_otp_submit_by_dom(page)" in source
    assert "address prep timing" in source
    assert "scan native select options once" in source
    assert "select_option(key, timeout=2000)" not in source
    assert "_wait_stripe_subscribe_processing" in source
    assert "PAYPAL_FLOW2_RECREATE_LINK" in source
    assert "submit entered processing but did not redirect PayPal in 60s" in source
    assert "regenerate_flow2_payment_link" in source
    assert "_detect_link_payment_invalid_long_link" in source
    assert "opened long link shows Link payment" in source
    assert "PAYPAL_PAYMENT_MODE" in source
    assert "_ensure_short_link_jp_proxy(proxy, env=flow_env, prefix=prefix)" in source
    assert "country_code=stripe_country" in source
    assert "country_code=paypal_country" in source
    assert "checkout agreement checkbox not detected; keep as diagnostic only" in source
