import asyncio

from modules.checkout import checkout_billing_for_region, create_plus_checkout_link, normalize_checkout_region


class FakePage:
    def __init__(self) -> None:
        self.calls = []

    async def evaluate(self, script, payload):
        self.calls.append(payload)
        return {"ok": True, "status": 200, "data": {"url": "https://pay.example/checkout"}}


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
