import asyncio

from modules import checkout
from modules.checkout import checkout_billing_for_region, create_plus_checkout_link, get_chatgpt_session, normalize_checkout_region


class FakePage:
    def __init__(self, response=None) -> None:
        self.calls = []
        self.response = response or {"ok": True, "status": 200, "data": {"url": "https://pay.example/checkout"}}

    async def evaluate(self, script, payload):
        del script
        self.calls.append(payload)
        return self.response


class FakeSessionPage:
    async def evaluate(self, script):
        del script
        return {
            "__session_error": True,
            "status": 403,
            "contentType": "text/html",
            "text": "<html>challenge</html>",
        }


def _base_cfg() -> dict[str, str]:
    return {
        "plan_name": "chatgptplusplan",
        "billing_country": "US",
        "currency": "USD",
        "cancel_url": "https://chatgpt.com/#pricing",
        "promo_campaign_id": "plus-1-month-free",
        "checkout_ui_mode": "hosted",
    }


def _disable_external_checkout(monkeypatch) -> None:
    monkeypatch.setattr(checkout, "load_env", lambda path: {"PAYPAL_CHECKOUT_FALLBACK_API_URL": "off"})


def test_checkout_region_normalization_and_billing() -> None:
    assert normalize_checkout_region("jp") == "jp"
    assert normalize_checkout_region("Japan") == "jp"
    assert normalize_checkout_region("us") == "us"
    assert normalize_checkout_region("") == "us"
    assert checkout_billing_for_region("jp") == {"country": "US", "currency": "USD"}
    assert checkout_billing_for_region("us") == {"country": "US", "currency": "USD"}


def test_external_checkout_api_url_defaults_to_payurl() -> None:
    assert checkout._external_checkout_api_url({}) == "https://payurl.ark2.cn/api/checkout"
    assert checkout._external_checkout_api_url({"PAYPAL_CHECKOUT_FALLBACK_API_URL": "off"}) == ""


def test_create_plus_checkout_link_uses_us_billing_for_jp_pay_mode(monkeypatch) -> None:
    _disable_external_checkout(monkeypatch)
    page = FakePage()
    link = asyncio.run(create_plus_checkout_link(page, "token", _base_cfg(), checkout_region="jp"))

    assert link == "https://pay.example/checkout"
    payload = page.calls[0]["payload"]
    assert payload["billing_details"] == {"country": "US", "currency": "USD"}
    assert payload["checkout_ui_mode"] == "hosted"


def test_create_plus_checkout_link_keeps_cfg_billing_without_region(monkeypatch) -> None:
    _disable_external_checkout(monkeypatch)
    page = FakePage()
    cfg = {**_base_cfg(), "billing_country": "ID", "currency": "IDR"}
    asyncio.run(create_plus_checkout_link(page, "token", cfg))

    payload = page.calls[0]["payload"]
    assert payload["billing_details"] == {"country": "ID", "currency": "IDR"}


def test_create_plus_checkout_link_falls_back_to_stripe_init(monkeypatch) -> None:
    _disable_external_checkout(monkeypatch)
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
    _disable_external_checkout(monkeypatch)
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


def test_create_plus_checkout_link_prefers_external_checkout_api(monkeypatch) -> None:
    page = FakePage({"ok": True, "status": 200, "data": {"url": "https://pay.example/internal"}})
    calls = []

    class FakeResponse:
        status_code = 200
        text = '{"preferredCheckoutUrl":"https://pay.openai.com/c/pay/hosted_cs_live_external"}'

        def json(self):
            return {"preferredCheckoutUrl": "https://pay.openai.com/c/pay/hosted_cs_live_external"}

    class FakeHttpSession:
        def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return FakeResponse()

    monkeypatch.setattr(
        checkout,
        "load_env",
        lambda path: {
            "PAYPAL_CHECKOUT_FALLBACK_API_URL": "https://fallback.example.test",
            "PAYPAL_CHECKOUT_FALLBACK_API_KEY": "secret-key",
            "PAYPAL_CHECKOUT_FALLBACK_TIMEOUT": "33",
            "PAYPAL_CHECKOUT_FALLBACK_PROXY": "http://fallback-proxy.example.test:8080",
            "PAYPAL_CHECKOUT_FALLBACK_DEFAULT_PROXY_ID": "line-a",
        },
    )
    monkeypatch.setattr(checkout, "_new_http_session", lambda proxy=None: FakeHttpSession())

    link = asyncio.run(create_plus_checkout_link(page, "access-token", _base_cfg(), checkout_region="jp"))

    assert link == "https://pay.openai.com/c/pay/hosted_cs_live_external"
    assert page.calls == []
    assert calls[0][0] == "https://fallback.example.test/api/checkout"
    request = calls[0][1]
    assert request["headers"]["X-API-Key"] == "secret-key"
    assert request["headers"]["Authorization"] == "Bearer secret-key"
    assert request["timeout"] == 33
    assert request["json"]["job_id"]
    assert request["json"]["requestId"] == request["json"]["job_id"]
    assert request["json"]["accessToken"] == "access-token"
    assert request["json"]["token"] == "access-token"
    assert request["json"]["paymentMethod"] == "paypal"
    assert request["json"]["processorEntity"] == "openai_llc"
    assert request["json"]["link_type"] == "paypal"
    assert request["json"]["ui_language"] == "en"
    assert request["json"]["country"] == "US"
    assert request["json"]["currency"] == "USD"
    assert request["json"]["use_promo"] is True
    assert request["json"]["skip_stripe_page"] is True
    assert request["json"]["promo_code"] == "STRIPEATLASGPT4BIZ050126"
    assert request["json"]["proxy"] == "http://fallback-proxy.example.test:8080"
    assert request["json"]["proxyUrl"] == "http://fallback-proxy.example.test:8080"
    assert request["json"]["default_proxy_id"] == "line-a"
    assert request["json"]["stripe_proxy_id"] == ""
    assert request["json"]["stripe_proxy"] == ""


def test_create_plus_checkout_link_rejects_external_hosted_fallback_without_provider_redirect(monkeypatch) -> None:
    page = FakePage({"ok": True, "status": 200, "data": {"url": "https://pay.example/internal"}})
    calls = []

    class FakeResponse:
        status_code = 200
        text = '{"fallback":true,"provider_redirect_url":"","long_url":"https://pay.openai.com/c/pay/cs_live_bad"}'

        def json(self):
            return {
                "ok": True,
                "link_type": "paypal",
                "payment_method_type": "paypal",
                "skip_stripe_page": True,
                "fallback": True,
                "extract_status": (
                    "\u5df2\u56de\u9000 hosted\uff1aStripe confirm \u5df2\u8fd4\u56de\uff0c"
                    "\u4f46\u6ca1\u6709 provider redirect"
                ),
                "provider_error": "Stripe confirm returned without provider redirect",
                "provider_redirect_url": "",
                "long_url": "https://pay.openai.com/c/pay/cs_live_bad",
                "openai_payurl": "https://pay.openai.com/c/pay/cs_live_bad",
            }

    class FakeHttpSession:
        def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return FakeResponse()

    async def fake_local_service(*args, **kwargs):
        return "https://pay.openai.com/c/pay/cs_live_local_after_provider_error"

    monkeypatch.setattr(checkout, "load_env", lambda path: {"PAYPAL_CHECKOUT_FALLBACK_API_URL": "https://fallback.example.test"})
    monkeypatch.setattr(checkout, "_new_http_session", lambda proxy=None: FakeHttpSession())
    monkeypatch.setattr(checkout, "_create_local_service_checkout_link", fake_local_service)

    link = asyncio.run(create_plus_checkout_link(page, "access-token", _base_cfg(), checkout_region="jp"))

    assert link == "https://pay.openai.com/c/pay/cs_live_local_after_provider_error"
    assert page.calls == []
    assert calls[0][0] == "https://fallback.example.test/api/checkout"


def test_create_plus_checkout_link_falls_back_to_internal_when_external_fails(monkeypatch) -> None:
    page = FakePage({"ok": True, "status": 200, "data": {"url": "https://pay.example/internal"}})

    class FakeResponse:
        status_code = 500
        text = "bad gateway"

        def json(self):
            return {"error": "bad_gateway"}

    class FakeHttpSession:
        def post(self, url, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(checkout, "load_env", lambda path: {"PAYPAL_CHECKOUT_FALLBACK_API_URL": "https://fallback.example.test"})
    monkeypatch.setattr(checkout, "_new_http_session", lambda proxy=None: FakeHttpSession())

    async def fake_local_service(*args, **kwargs):
        raise RuntimeError("local service down")

    monkeypatch.setattr(checkout, "_create_local_service_checkout_link", fake_local_service)

    link = asyncio.run(create_plus_checkout_link(page, "access-token", _base_cfg(), checkout_region="jp"))

    assert link == "https://pay.example/internal"
    assert len(page.calls) == 1


def test_create_plus_checkout_link_uses_local_service_after_external_failure(monkeypatch) -> None:
    page = FakePage({"ok": True, "status": 200, "data": {"url": "https://pay.example/internal"}})
    calls = []
    stripe_calls = []

    class FakeResponse:
        def __init__(self, status_code, text, data):
            self.status_code = status_code
            self.text = text
            self._data = data

        def json(self):
            return self._data

    class FakeHttpSession:
        def __init__(self, proxy=None):
            self.proxy = proxy

        def post(self, url, **kwargs):
            calls.append((url, self.proxy, kwargs))
            if url == checkout.CHATGPT_CHECKOUT_URL:
                return FakeResponse(
                    200,
                    '{"checkout_session_id":"cs_live_local123","publishable_key":"pk_live_local123"}',
                    {"checkout_session_id": "cs_live_local123", "publishable_key": "pk_live_local123"},
                )
            return FakeResponse(502, '{"error":"bad_gateway"}', {"error": "bad_gateway"})

    def fake_stripe_init(checkout_session_id, publishable_key, **kwargs):
        stripe_calls.append((checkout_session_id, publishable_key, kwargs))
        return "https://pay.openai.com/c/pay/cs_live_local123"

    monkeypatch.setattr(checkout, "load_env", lambda path: {"PAYPAL_CHECKOUT_FALLBACK_API_URL": "https://fallback.example.test"})
    monkeypatch.setattr(checkout, "_new_http_session", lambda proxy=None: FakeHttpSession(proxy))
    monkeypatch.setattr(checkout, "_stripe_init_hosted_url", fake_stripe_init)

    link = asyncio.run(
        create_plus_checkout_link(
            page,
            "access-token",
            _base_cfg(),
            checkout_region="jp",
            proxy="socks5://task.proxy.example.test:1080",
        )
    )

    assert link == "https://pay.openai.com/c/pay/cs_live_local123"
    assert page.calls == []
    assert calls[0][0] == "https://fallback.example.test/api/checkout"
    assert calls[0][1] is None
    assert calls[1][0] == checkout.CHATGPT_CHECKOUT_URL
    assert calls[1][1] == "socks5://task.proxy.example.test:1080"
    assert stripe_calls == [
        (
            "cs_live_local123",
            "pk_live_local123",
            {"proxy": "socks5://task.proxy.example.test:1080"},
        )
    ]


def test_create_plus_checkout_link_uses_local_generator_when_selected(monkeypatch) -> None:
    page = FakePage({"ok": True, "status": 200, "data": {"url": "https://pay.example/internal"}})
    calls = []
    stripe_calls = []
    method_sink = {}

    class FakeResponse:
        def __init__(self, status_code, text, data):
            self.status_code = status_code
            self.text = text
            self._data = data

        def json(self):
            return self._data

    class FakeHttpSession:
        def __init__(self, proxy=None):
            self.proxy = proxy

        def post(self, url, **kwargs):
            calls.append((url, self.proxy, kwargs))
            return FakeResponse(
                200,
                '{"checkout_session_id":"cs_live_gen123","publishable_key":"pk_live_gen123"}',
                {"checkout_session_id": "cs_live_gen123", "publishable_key": "pk_live_gen123"},
            )

    def fake_stripe_init(checkout_session_id, publishable_key, **kwargs):
        stripe_calls.append((checkout_session_id, publishable_key, kwargs))
        return "https://pay.openai.com/c/pay/cs_live_gen123"

    monkeypatch.setattr(
        checkout,
        "load_env",
        lambda path: {
            "PAYPAL_CHECKOUT_METHOD": checkout.CHECKOUT_METHOD_LOCAL_GENERATOR,
            "PAYPAL_CHECKOUT_LOCAL_GENERATOR_LINK_TYPE": "auto",
            "PAYPAL_CHECKOUT_LOCAL_GENERATOR_PAYMENT_LOCALE": "ja",
        },
    )
    monkeypatch.setattr(checkout, "_new_http_session", lambda proxy=None: FakeHttpSession(proxy))
    monkeypatch.setattr(checkout, "_stripe_init_hosted_url", fake_stripe_init)

    link = asyncio.run(
        create_plus_checkout_link(
            page,
            "access-token",
            _base_cfg(),
            checkout_region="jp",
            proxy="socks5h://jp.proxy.example.test:1080",
            method_sink=method_sink,
        )
    )

    assert link == "https://pay.openai.com/c/pay/cs_live_gen123"
    assert page.calls == []
    assert method_sink["method"] == checkout.CHECKOUT_METHOD_LOCAL_GENERATOR
    assert calls[0][0] == checkout.CHATGPT_CHECKOUT_URL
    assert calls[0][1] == "socks5h://jp.proxy.example.test:1080"
    request_payload = calls[0][2]["json"]
    assert request_payload["billing_details"] == {"country": "JP", "currency": "JPY"}
    assert request_payload["checkout_ui_mode"] == "hosted"
    assert stripe_calls == [
        (
            "cs_live_gen123",
            "pk_live_gen123",
            {
                "payment_locale": "ja",
                "user_agent": "",
                "proxy": "socks5h://jp.proxy.example.test:1080",
            },
        )
    ]


def test_create_plus_checkout_link_uses_hosted_url_helper_when_selected(monkeypatch) -> None:
    page = FakePage({"ok": True, "status": 200, "data": {"url": "https://pay.example/internal"}})
    calls = []
    method_sink = {}

    class FakeResponse:
        status_code = 200
        text = '{"checkout_session_id":"cs_live_helper123","publishable_key":"pk_live_helper123"}'

        def json(self):
            return {
                "checkout_session_id": "cs_live_helper123",
                "publishable_key": "pk_live_helper123",
            }

    class FakeHttpSession:
        def __init__(self, proxy=None):
            self.proxy = proxy

        def post(self, url, **kwargs):
            calls.append((url, self.proxy, kwargs))
            return FakeResponse()

    monkeypatch.setattr(
        checkout,
        "load_env",
        lambda path: {
            "PAYPAL_CHECKOUT_METHOD": checkout.CHECKOUT_METHOD_HOSTED_URL_HELPER,
            "PAYPAL_CHECKOUT_HOSTED_HELPER_LOCALE": "ja",
        },
    )
    monkeypatch.setattr(checkout, "_new_http_session", lambda proxy=None: FakeHttpSession(proxy))

    link = asyncio.run(
        create_plus_checkout_link(
            page,
            "access-token",
            _base_cfg(),
            checkout_region="jp",
            proxy="socks5h://jp.proxy.example.test:1080",
            method_sink=method_sink,
        )
    )

    assert link.startswith("https://pay.openai.com/c/pay/cs_live_helper123#")
    assert page.calls == []
    assert method_sink["method"] == checkout.CHECKOUT_METHOD_HOSTED_URL_HELPER
    assert calls[0][0] == checkout.CHATGPT_CHECKOUT_URL
    assert calls[0][1] == "socks5h://jp.proxy.example.test:1080"
    request = calls[0][2]
    assert request["headers"]["Authorization"] == "Bearer access-token"
    assert request["json"] == {
        "plan_name": "chatgptplusplan",
        "billing_details": {"country": "US", "currency": "USD"},
        "cancel_url": "https://chatgpt.com/#pricing",
        "promo_campaign": {
            "promo_campaign_id": "plus-1-month-free",
            "is_coupon_from_query_param": False,
        },
        "checkout_ui_mode": "hosted",
    }


def test_create_plus_checkout_link_auto_prefers_hosted_url_helper_before_local_generator(monkeypatch) -> None:
    page = FakePage({"ok": True, "status": 200, "data": {"url": "https://pay.example/internal"}})
    calls = []
    method_sink = {}

    class FakeResponse:
        status_code = 200
        text = '{"checkout_session_id":"cs_live_autohelper123","publishable_key":"pk_live_autohelper123"}'

        def json(self):
            return {
                "checkout_session_id": "cs_live_autohelper123",
                "publishable_key": "pk_live_autohelper123",
            }

    class FakeHttpSession:
        def __init__(self, proxy=None):
            self.proxy = proxy

        def post(self, url, **kwargs):
            calls.append((url, self.proxy, kwargs))
            return FakeResponse()

    monkeypatch.setattr(checkout, "load_env", lambda path: {"PAYPAL_CHECKOUT_METHOD": checkout.CHECKOUT_METHOD_AUTO})
    monkeypatch.setattr(checkout, "_new_http_session", lambda proxy=None: FakeHttpSession(proxy))

    async def fail_if_local_generator_called(*args, **kwargs):
        raise AssertionError("local_generator must not run before hosted_url_helper")

    monkeypatch.setattr(checkout, "_create_local_generator_checkout_link", fail_if_local_generator_called)

    link = asyncio.run(
        create_plus_checkout_link(
            page,
            "access-token",
            _base_cfg(),
            checkout_region="jp",
            proxy="socks5h://jp.proxy.example.test:1080",
            skip_methods={checkout.CHECKOUT_METHOD_EXTERNAL_API, checkout.CHECKOUT_METHOD_LOCAL_SERVICE},
            method_sink=method_sink,
        )
    )

    assert link.startswith("https://pay.openai.com/c/pay/cs_live_autohelper123#")
    assert page.calls == []
    assert method_sink["method"] == checkout.CHECKOUT_METHOD_HOSTED_URL_HELPER
    assert calls[0][0] == checkout.CHATGPT_CHECKOUT_URL


def test_hosted_url_helper_builds_pay_openai_fragment() -> None:
    link = checkout.build_hosted_url_helper_pay_url(
        "cs_live_abc123",
        "pk_live_xyz123",
        locale="zh",
    )

    assert link.startswith("https://pay.openai.com/c/pay/cs_live_abc123#")
    assert "%2B" in link or "%3D" in link or "#" in link


def test_create_plus_checkout_link_uses_browser_only_when_selected(monkeypatch) -> None:
    page = FakePage()
    monkeypatch.setattr(checkout, "load_env", lambda path: {"PAYPAL_CHECKOUT_METHOD": checkout.CHECKOUT_METHOD_BROWSER_CHECKOUT})

    link = asyncio.run(create_plus_checkout_link(page, "token", _base_cfg(), checkout_region="jp"))

    assert link == "https://pay.example/checkout"
    assert len(page.calls) == 1
    assert page.calls[0]["payload"]["billing_details"] == {"country": "US", "currency": "USD"}


def test_create_plus_checkout_link_skips_previously_invalid_method(monkeypatch) -> None:
    page = FakePage({"ok": True, "status": 200, "data": {"url": "https://pay.example/internal"}})
    method_sink = {}

    async def fake_local_service(*args, **kwargs):
        return "https://pay.openai.com/c/pay/cs_live_local_skip"

    class FailIfExternalCalled:
        def post(self, url, **kwargs):
            raise AssertionError("external API must be skipped")

    monkeypatch.setattr(checkout, "load_env", lambda path: {"PAYPAL_CHECKOUT_FALLBACK_API_URL": "https://fallback.example.test"})
    monkeypatch.setattr(checkout, "_new_http_session", lambda proxy=None: FailIfExternalCalled())
    monkeypatch.setattr(checkout, "_create_local_service_checkout_link", fake_local_service)

    link = asyncio.run(
        create_plus_checkout_link(
            page,
            "access-token",
            _base_cfg(),
            checkout_region="jp",
            skip_methods={checkout.CHECKOUT_METHOD_EXTERNAL_API},
            method_sink=method_sink,
        )
    )

    assert link == "https://pay.openai.com/c/pay/cs_live_local_skip"
    assert method_sink["method"] == checkout.CHECKOUT_METHOD_LOCAL_SERVICE
    assert page.calls == []


def test_create_plus_checkout_link_explicit_method_ignores_bad_method_skip(monkeypatch) -> None:
    page = FakePage({"ok": True, "status": 200, "data": {"url": "https://pay.example/internal"}})
    calls = []
    method_sink = {}

    class FakeResponse:
        status_code = 200
        text = '{"preferredCheckoutUrl":"https://pay.openai.com/c/pay/hosted_cs_live_external"}'

        def json(self):
            return {"preferredCheckoutUrl": "https://pay.openai.com/c/pay/hosted_cs_live_external"}

    class FakeHttpSession:
        def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return FakeResponse()

    monkeypatch.setattr(
        checkout,
        "load_env",
        lambda path: {
            "PAYPAL_CHECKOUT_METHOD": checkout.CHECKOUT_METHOD_EXTERNAL_API,
            "PAYPAL_CHECKOUT_FALLBACK_API_URL": "https://fallback.example.test",
        },
    )
    monkeypatch.setattr(checkout, "_new_http_session", lambda proxy=None: FakeHttpSession())

    link = asyncio.run(
        create_plus_checkout_link(
            page,
            "access-token",
            _base_cfg(),
            checkout_region="jp",
            skip_methods={checkout.CHECKOUT_METHOD_EXTERNAL_API},
            method_sink=method_sink,
        )
    )

    assert link == "https://pay.openai.com/c/pay/hosted_cs_live_external"
    assert calls[0][0] == "https://fallback.example.test/api/checkout"
    assert method_sink["method"] == checkout.CHECKOUT_METHOD_EXTERNAL_API
    assert page.calls == []


def test_get_chatgpt_session_reports_html_response() -> None:
    try:
        asyncio.run(get_chatgpt_session(FakeSessionPage()))
    except RuntimeError as exc:
        assert "HTTP 403" in str(exc)
        assert "text/html" in str(exc)
        assert "challenge" in str(exc)
    else:
        raise AssertionError("expected session fetch failure")
