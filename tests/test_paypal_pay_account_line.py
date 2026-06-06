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
    assert "force_payment_link" not in items[0]
    assert items[1]["account_line"] == "ready@hotmail.com----pw----client----rt"


def test_load_mail_pool_direct_accounts_uses_only_unseen_accounts(monkeypatch, tmp_path) -> None:
    _isolate_flow_state(monkeypatch, tmp_path)
    registered_file = tmp_path / "registered_sessions.txt"
    link_file = tmp_path / "account.txt"
    pending_file = tmp_path / "pending.txt"
    accounts_file = tmp_path / "hotmail_accounts.txt"
    accounts_file.write_text(
        "\n".join(
            [
                "fresh@hotmail.com----pw----client----rt",
                "discarded@hotmail.com----pw----client----rt",
                "state@hotmail.com----pw----client----rt",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(paypal_pay, "REGISTER_ONLY_SUMMARY_FILE", registered_file)
    monkeypatch.setattr(paypal_pay, "LINK_POOL_FILE", link_file)
    monkeypatch.setattr(paypal_pay, "PENDING_AUTH_FILE", pending_file)
    paypal_flow_state.mark_discarded_many(["discarded@hotmail.com"], reason="bad")
    paypal_flow_state.update_account(
        "state@hotmail.com",
        paypal_flow_state.STATUS_REGISTERED,
        account_line="state@hotmail.com----pw----client----rt",
    )

    items = paypal_pay._load_mail_pool_direct_accounts({"mail": {"accounts_file": str(accounts_file)}})

    assert [item["email"] for item in items] == ["fresh@hotmail.com"]
    assert items[0]["source"] == "mail_pool_direct"
    assert items[0]["account_line"] == "fresh@hotmail.com----pw----client----rt"


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
    assert paypal_pay.paypal_payment_mode({}) == paypal_pay.PAYPAL_PAYMENT_MODE_SHORT_LINK
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


def test_checkout_due_amount_jpy_guard_detects_wrong_offer_region() -> None:
    assert paypal_pay._checkout_due_amount_looks_jpy(
        {"amount_text": "¥0", "source_text": "Due today ¥0"}
    )
    assert paypal_pay._checkout_due_amount_looks_jpy(
        {"amount_text": "0 JPY", "source_text": "Estimated tax 0 JPY"}
    )
    assert not paypal_pay._checkout_due_amount_looks_jpy(
        {"amount_text": "US$0.00", "source_text": "Due today US$0.00"}
    )


def test_stripe_paypal_redirect_filter_rejects_icon_assets() -> None:
    icon_url = "https://js.stripe.com/v3/fingerprinted/img/payment-methods/icon-pm-paypal_dark@3x.png"
    redirect_url = "https://www.paypal.com/checkoutnow?token=EC-TEST"

    assert paypal_pay._find_paypal_redirect_in_value({"icon": icon_url}) == ""
    assert paypal_pay._first_paypal_redirect_in_text(f'{{"url":"{icon_url}"}}') == ""
    assert paypal_pay._find_paypal_redirect_in_value({"redirect": redirect_url}) == redirect_url


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

    assert paypal_pay.PAYPAL_FLOW2_CODE_VERSION == "PAYPAL_SHORT_LINK_US_PAY_JP_PROXY_2026-06-06_38"
    assert paypal_pay._CHATGPT_OFFER_SURFACE_MAX_ATTEMPTS == 36
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
    assert "PAYPAL_FLOW2_NO_PAYPAL_OPTION" in source
    assert "_is_no_paypal_option_reason" in source
    assert "submit entered processing but did not redirect PayPal in 180s" in source
    assert "async def _ensure_stripe_paypal_selected" in source
    assert "for frame in page.frames:" in source
    assert "iframe-paypal" in source
    assert "#payment-method-accordion-item-title-paypal" in source
    assert "input[name=\"payment-method-accordion-item-title\"][value=\"paypal\"]" in source
    assert "direct-paypal" in source
    assert "PayPal click did not select yet" in source
    assert "snapshot.get(\"hasPayPal\") and snapshot.get(\"selected\")" in source
    assert "snapshot.get(\"hasPayPal\") and (clicked or snapshot.get(\"selected\"))" not in source
    assert "ready-continue" in source
    assert "dismissed ChatGPT ready interstitial" in source
    assert "paypal payment option not available before subscribe" in source
    assert "billing country confirmed" in source
    assert "PayPal payment method confirmed" in source
    assert "selectedByText" in source
    assert "已选择\\s*PayPal" in source
    assert "regenerate_flow2_payment_link" in source
    assert "_load_mail_pool_direct_accounts" in source
    assert "mail_pool_direct" in source
    assert "短链直付池为空" in source
    assert "open_payment_link = use_long_link" in source
    assert "open_payment_link = use_long_link or bool(item.get(\"force_payment_link\"))" not in source
    assert 'item["force_payment_link"] = "1"' not in source
    short_loader = source[source.index("def _load_direct_pay_accounts") : source.index("async def _install_click_watcher")]
    assert 'source="state:link_ready"' not in short_loader
    assert "force_payment_link=True" not in short_loader
    assert "_detect_link_payment_invalid_long_link" in source
    assert "opened long link shows Link payment" in source
    assert "PAYPAL_PAYMENT_MODE" in source
    assert "_ensure_short_link_jp_proxy(proxy, env=flow_env, prefix=prefix)" in source
    assert "country_code=stripe_country" in source
    assert "country_code=paypal_country" in source
    assert "async def _select_chatgpt_offer_region_us" in source
    assert "await _select_chatgpt_offer_region_us(page, prefix)" in source
    assert "offer region selected US" in source
    assert "offer region native set unconfirmed" in source
    assert "offer region selected US by mouse" in source
    assert "offer region still not US" in source
    assert "async def _click_chatgpt_offer_plan_submit" in source
    assert "clicked offer plan submit after US region" in source
    assert "offer plan submit skipped before US region" in source
    assert "select-plan-button-plus-upgrade" in source
    assert "claim\\s+plus\\s+free\\s+offer" in source
    assert "zero/free Plus trial click skipped before US region" in source
    assert "zero/free Plus trial click skipped inside pricing modal" in source
    assert "region switch failed before offer submit" in source
    assert 'country-selector-in-pricing-modal' in source
    assert "offer region selected US by exact combobox" in source
    assert "offer region selected US by keyboard" in source
    assert "offer region keyboard skipped unsafe focus" in source
    assert "offer region wheel skipped" in source
    assert "offer region wheel exhausted" in source
    assert "offer region selected US by typeahead" in source
    assert "offer region typeahead no US highlight" in source
    assert "force reselect before submit" in source
    assert "_click_visible_us_chatgpt_offer_region_option" in source
    assert "offer region force clicked US option" in source
    assert "offer region force US option not found" in source
    assert "_click_chatgpt_offer_region_option_strict" in source
    assert "offer region strict selected" in source
    assert "_CHATGPT_OFFER_SURFACE_MAX_ATTEMPTS = 36" in source
    assert "data-paypal-offer-region-target" in source
    assert 'for open_mode in ("keyboard", "mouse")' in source
    assert "offer region strict locator pick unconfirmed" in source
    assert "_wait_chatgpt_offer_us_pricing" in source
    assert "_refresh_chatgpt_offer_region_us" in source
    assert "_click_visible_non_us_chatgpt_offer_region" in source
    assert "offer region refresh selected" in source
    assert "offer region refresh selected non-US option" in source
    assert "offer region refresh non-US option not found" in source
    assert "no-non-us-option" in source
    assert '"Japan"' in source
    assert '"United States"' in source
    assert "offer US pricing not ready before submit" in source
    assert "offer pricing still JPY after US region; continue claim offer by selected United States" not in source
    assert "offer plan submit skipped before US pricing" in source
    assert "no-scoped-root" in source
    assert "data-radix-select-viewport" in source
    assert "activeCombo || activeOption" in source
    assert "missing-modal" in source
    assert "activeEditable" in source
    assert "offer region failed to switch US after 3 attempts" in source
    assert "套餐页右下角地区未能自动切换到美国" in source
    assert "async def _chatgpt_offer_modal_visible" in source
    assert "offer entry click skipped inside pricing modal" in source
    assert "offer modal plan button skipped by entry click" in source
    assert "offer entry click skipped on checkout URL" in source
    assert "__PAYPAL_CLICK_WATCHER_BINDING__" in source
    assert "out_file.touch(exist_ok=True)" in source
    assert "eventX" in source
    assert "clicked_trial_after_offer" in source
    zero_trial = source[source.index("async def _click_zero_trial_plus_option") : source.index("async def _select_chatgpt_offer_region_us")]
    assert zero_trial.index("await _select_chatgpt_offer_region_us(page, prefix)") < zero_trial.index("await locator.click")
    assert "checkout agreement checkbox not detected; keep as diagnostic only" in source
    assert "async def _ensure_stripe_required_checkboxes" in source
    assert "checkout checkbox sweep" in source
    assert "checkout agreement checkbox JS fallback" in source
    assert "el.checked = true" in source
    assert "AiAgentPaymentSteering" in source
    assert "agent_identity_token" in source
    assert "unchecked_agent" in source
    assert "skipped_agent" in source
    assert "async def fill_stripe" in source
    assert "_attach_stripe_network_probe(page)" in source
    assert 'page.on("request", on_request)' in source
    assert "response_body" in source
    assert "_STRIPE_CONFIRM_BODY_CAPTURE_LIMIT" in source
    assert "_STRIPE_CONFIRM_REQUEST_CAPTURE_LIMIT" in source
    assert "_summarize_stripe_confirm_response" in source
    assert "confirmSummary" in source
    assert "confirmResponse" in source
    assert "_extract_stripe_confirm_redirect_url" in source
    assert "_is_paypal_redirect_url" in source
    assert "host != \"paypal.com\" and not host.endswith(\".paypal.com\")" in source
    assert "PayPal redirect found in confirm response" in source
    assert "networkEvents" in source
    assert "network.json" in source
    assert "_STRIPE_STABLE_US_BILLING_PROFILES" in source
    assert "_pick_stripe_stable_us_billing_profile" in source
    assert "using stable US billing address" in source
    assert "async def _stripe_address_targets" in source
    assert "elements-inner-address" in source
    assert "billing address iframe fill" in source
    assert "async def _fill_stripe_address_element" in source
    assert "async def _verify_stripe_billing_address_complete" in source
    assert "billing address verify attempt" in source
    assert "stripe_billing_address_incomplete" in source
    assert "billing country dropdown committed" in source
    assert "billing state dropdown committed" in source
    assert "aria-invalid" in source
    assert "_US_STATE_NAMES" in source
    assert "_checkout_due_amount_looks_jpy" in source
    assert "checkout still JPY after US offer region" in source
    assert r"\u652f\u4ed8\u9875\u4ecd\u663e\u793a\u65e5\u5143\u96f6\u91d1\u989d" in source
    assert "async def _paypal_redirect_page" in source
    assert "page.context.pages" in source
    assert "waiting PayPal redirect... {second}s" in source
    assert "did not redirect PayPal in 180s" in source
    assert "PayPal opened in new page" in source
    assert "stripe_page = await fill_stripe" in source
    assert "recreate_on_missing_paypal=False" in source
    assert "recreate_on_missing_paypal=open_payment_link" not in source
    assert "async def _wait_checkout_surface_after_offer_submit" in source
    assert "正在加载安全结账" in source
    assert "checkout loading finished" in source
    assert "checkout loading did not finish" in source
    assert "return await _wait_checkout_surface_after_offer_submit(page, prefix, email=email)" in source
    assert r"\u5b98\u65b9\u4f18\u60e0\u8d26\u5355\u9875\u672a\u51fa\u73b0 PayPal" in source
    no_paypal_block = source[source.index("if _is_no_paypal_option_reason(reason):") : source.index("if _is_recreate_link_reason(reason):")]
    assert "discard_flow2_link(item[\"email\"], reason=reason or PAYPAL_FLOW2_NO_PAYPAL_OPTION)" in no_paypal_block
    assert "async def _accept_stripe_address_suggestion" in source
    assert "accepted address suggestion" in source
    address_suggestion = source[source.index("async def _accept_stripe_address_suggestion") : source.index("async def _dismiss_stripe_address_suggestions")]
    assert "stateNames" in address_suggestion
    assert "stateFull" in address_suggestion
    assert "/usa|unitedstates/.test(key)" not in address_suggestion
    assert "async def _dismiss_stripe_address_suggestions" in source
    offer_submit = source[
        source.index("async def _click_chatgpt_offer_plan_submit") : source.index("async def _click_visible_offer_entry")
    ]
    assert "close|cancel|dismiss" in offer_submit
    assert "signatureOf" in offer_submit
    assert "bad.test(item.signature)" in offer_submit
    offer_entry = source[
        source.index("async def _click_visible_offer_entry") : source.index("async def _save_chatgpt_offer_failure_debug")
    ]
    assert 'button:has-text("Subscribe")' not in offer_entry
    assert 'a:has-text("Subscribe")' not in offer_entry
    assert 'button:has-text("Plus")' not in offer_entry
    assert 'a:has-text("Plus")' not in offer_entry
    assert "/subscribe/i" not in offer_entry
    assert "AddressAutocomplete--clear-dropdown-button" in source
    assert "billing-address-autocomplete-results" in source
    assert "address suggestion overlay still visible" in source
    assert "async def _save_stripe_failure_debug" in source
    assert "failure debug saved" in source
    nonzero_block = source[source.index('if due_amount.get("status") == "nonzero"') : source.index('if due_amount.get("status") == "zero"')]
    assert "if use_long_link:" in nonzero_block
    assert "discard_flow2_link(email, reason=reason)" in nonzero_block
    assert "mark_link_for_regeneration(" not in nonzero_block
    assert "await regenerate_flow2_payment_link(" not in source
    assert "if success + active_slots >= target:" in source
    assert "if success + failed_slots + active_slots >= target:" not in source
