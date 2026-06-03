from types import SimpleNamespace

from modules.extra_sms_providers import (
    ChatGptApiSmsProvider,
    NexSmsProvider,
    SmsPoolProvider,
    SmsVerificationNumberProvider,
)
from modules.hero_sms_provider import PhoneCountry
from modules.sms_provider_factory import (
    SUPPORTED_SMS_PROVIDERS,
    create_sms_provider,
    normalize_sms_provider_name,
    sms_provider_api_key_from_env,
    sms_provider_default_service,
)


def test_supported_sms_providers_include_imported_platforms() -> None:
    assert "sms-verification-number" in SUPPORTED_SMS_PROVIDERS
    assert "nexsms" in SUPPORTED_SMS_PROVIDERS
    assert "smspool" in SUPPORTED_SMS_PROVIDERS
    assert "chatgpt-api" in SUPPORTED_SMS_PROVIDERS
    assert normalize_sms_provider_name("sms_pool") == "smspool"
    assert normalize_sms_provider_name("chatgpt_api_sms") == "chatgpt-api"
    assert sms_provider_default_service("nexsms") == "ot"
    assert sms_provider_default_service("smspool") == "671"


def test_create_sms_provider_uses_fixed_compatible_endpoints() -> None:
    sms_verification = create_sms_provider("sms-verification-number", "key")
    smspool = create_sms_provider("smspool", "key")

    assert isinstance(sms_verification, SmsVerificationNumberProvider)
    assert sms_verification.base_url == "https://sms-verification-number.com/stubs/handler_api"
    assert isinstance(smspool, SmsPoolProvider)
    assert smspool.base_url == "https://api.smspool.net/stubs/handler_api.php?setting=smspool"


def test_chatgpt_api_uses_pool_file_as_api_key(tmp_path) -> None:
    pool = tmp_path / "phones.txt"
    pool.write_text("+6288200000000----https://sms.example/code\n", encoding="utf-8")
    env = {"CHATGPT_API_SMS_POOL_FILE": str(pool)}

    assert sms_provider_api_key_from_env("chatgpt-api", env) == str(pool)
    provider = create_sms_provider("chatgpt-api", str(pool))

    assert isinstance(provider, ChatGptApiSmsProvider)
    activation = provider.get_number()
    assert activation.phone_number == "+6288200000000"


def test_chatgpt_api_pool_parses_two_line_entries_and_country_prices(tmp_path) -> None:
    pool = tmp_path / "phones.txt"
    pool.write_text("+6288200000000\nhttps://sms.example/code\n", encoding="utf-8")
    provider = ChatGptApiSmsProvider(pool_file=pool)

    priced = provider.list_country_prices(
        "custom-api",
        [
            PhoneCountry("ID", "62", "Indonesia", 6),
            PhoneCountry("US", "1", "United States", 12),
        ],
    )

    assert [(country.iso_code, country.count, country.price) for country in priced] == [("ID", 1, 0.0)]


def test_chatgpt_api_get_status_extracts_code(monkeypatch) -> None:
    provider = ChatGptApiSmsProvider(pool_text="+6288200000000----https://sms.example/code")
    activation = provider.get_number()

    def fake_get(url: str, timeout: int):
        assert url == "https://sms.example/code"
        assert timeout == 30
        return SimpleNamespace(text="Your verification code is 123-456")

    monkeypatch.setattr("modules.extra_sms_providers.requests.get", fake_get)

    assert provider.get_status(activation.activation_id) == (True, "123456")


def test_nexsms_parses_activation_and_prices(monkeypatch) -> None:
    provider = NexSmsProvider("key", base_url="https://nex.example")

    def fake_request(path: str, *, method: str = "GET", query=None, body=None):
        if path == "/api/getCountryByService":
            return {"data": {"priceMap": {"0.11": 3, "0.09": 2}}}
        if path == "/api/order/purchase":
            assert method == "POST"
            assert body["serviceCode"] == "ot"
            assert body["countryId"] == 50
            assert body["price"] == 0.09
            return {"data": {"phoneNumber": "+668820000000", "price": 0.09}}
        raise AssertionError(path)

    monkeypatch.setattr(provider, "request", fake_request)

    prices = provider.list_country_prices("ot", [PhoneCountry("TH", "66", "Thailand", 50)])
    activation = provider.get_number("ot", 50, max_retries=1)

    assert [(country.iso_code, country.price, country.count) for country in prices] == [("TH", 0.09, 5)]
    assert activation.phone_number == "+668820000000"
    assert activation.activation_cost == 0.09
