import asyncio

from modules import checkout
from modules.checkout import checkout_billing_for_region, create_plus_checkout_link, normalize_checkout_region


class FakePage:
    def __init__(self, response=None) -> None:
        self.calls = []
        self.response = response or {"ok": True, "status": 200, "data": {"url": "https://pay.example/checkout"}}

    async def evaluate(self, script, payload):
        del script
        self.calls.append(payload)
        return self.response


def _base_cfg() -> dict[str, str]:
    return {
        "plan_name": "chatgptplusplan",
        "billing_country": "US",
        "currency": "USD",
        "cancel_url": "https://chatgpt.com/#pricing",
        "promo_campaign_id": "plus-1-month-free",
        "checkout_ui_mode": "hosted",
    }


def test_checkout_region_normalization_and_billing() -> None:
    assert normalize_checkout_region("jp") == "jp"
    assert normalize_checkout_region("Japan") == "jp"
    assert normalize_checkout_region("us") == "us"
    assert normalize_checkout_region("") == "us"
    assert checkout_billing_for_region("jp") == {"country": "US", "currency": "USD"}
    assert checkout_billing_for_region("us") == {"country": "US", "currency": "USD"}


def test_create_plus_checkout_link_uses_us_billing_for_jp_pay_mode() -> None:
    page = FakePage()
    link = asyncio.run(create_plus_checkout_link(page, "token", _base_cfg(), checkout_region="jp"))

    assert link == "https://pay.example/checkout"
    payload = page.calls[0]["payload"]
    assert payload["billing_details"] == {"country": "US", "currency": "USD"}
    assert payload["checkout_ui_mode"] == "hosted"


def test_create_plus_checkout_link_keeps_cfg_billing_without_region() -> None:
    page = FakePage()
    cfg = {**_base_cfg(), "billing_country": "ID", "currency": "IDR"}
    asyncio.run(create_plus_checkout_link(page, "token", cfg))

    payload = page.calls[0]["payload"]
    assert payload["billing_details"] == {"country": "ID", "currency": "IDR"}


def test_create_plus_checkout_link_falls_back_to_stripe_init(monkeypatch) -> None:
    page = FakePage(
        {
            "ok": True,
            "status": 200,
            "data": {
                "custom_checkout_session": {"id": "cs_live_fallback123"},
                "publishable_key": "pk_live_test123",
            },
        }
    )
    calls = []

    def fake_stripe_init(checkout_session_id, publishable_key, **kwargs):
        calls.append((checkout_session_id, publishable_key, kwargs))
        return "https://pay.openai.com/c/pay/cs_live_fallback123"

    monkeypatch.setattr(checkout, "_stripe_init_hosted_url", fake_stripe_init)

    link = asyncio.run(create_plus_checkout_link(page, "token", _base_cfg(), proxy="http://127.0.0.1:7897"))

    assert link == "https://pay.openai.com/c/pay/cs_live_fallback123"
    assert calls == [("cs_live_fallback123", "pk_live_test123", {"proxy": "http://127.0.0.1:7897"})]


def test_create_plus_checkout_link_fallback_retries_local_proxy(monkeypatch) -> None:
    page = FakePage(
        {
            "ok": True,
            "status": 200,
            "data": {
                "checkout_session_id": "cs_live_retry123",
                "publishable_key": "pk_live_retry123",
                "url": None,
            },
        }
    )
    calls = []

    def fake_stripe_init(checkout_session_id, publishable_key, **kwargs):
        calls.append((checkout_session_id, publishable_key, kwargs))
        if kwargs.get("proxy") == "socks5://bad.proxy:1080":
            raise RuntimeError("cannot complete SOCKS5 connection")
        return "https://pay.openai.com/c/pay/cs_live_retry123"

    monkeypatch.setattr(checkout, "_stripe_init_hosted_url", fake_stripe_init)

    link = asyncio.run(create_plus_checkout_link(page, "token", _base_cfg(), proxy="socks5://bad.proxy:1080"))

    assert link == "https://pay.openai.com/c/pay/cs_live_retry123"
    assert calls == [
        ("cs_live_retry123", "pk_live_retry123", {"proxy": "socks5://bad.proxy:1080"}),
        ("cs_live_retry123", "pk_live_retry123", {"proxy": "http://127.0.0.1:7897"}),
    ]
