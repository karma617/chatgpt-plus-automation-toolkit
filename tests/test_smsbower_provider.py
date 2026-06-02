from modules.hero_sms_provider import PhoneCountry
from modules.smsbower_provider import SmsBowerProvider


class FakeSmsBowerProvider(SmsBowerProvider):
    def __init__(self) -> None:
        super().__init__("key")

    def request(self, action: str, **params):
        if action == "getServicesList":
            return {"dr": {"name": "OpenAI"}, "tg": {"name": "Telegram"}}
        if action == "getCountries":
            return {"38": {"eng": "Ghana", "prefix": "233"}}
        if action == "getPricesV3":
            return {
                "38": {
                    "dr": {
                        "price": "0.054",
                        "count": "7",
                        "providers": {"12": {"name": "Provider 12", "price": "0.055", "count": "3"}},
                    }
                }
            }
        if action == "getNumberV2":
            return {"activationId": "123", "phoneNumber": "233555123456", "activationCost": "0.054"}
        if action == "getStatus":
            return "STATUS_OK:123456"
        return "OK"


def test_smsbower_provider_lists_services_countries_and_operators() -> None:
    provider = FakeSmsBowerProvider()

    assert provider.get_services()["dr"] == "OpenAI"

    country = PhoneCountry("GH", "233", "Ghana", 38)
    priced = provider.list_country_prices("dr", [country])
    assert priced[0].hero_sms_country == 38
    assert priced[0].price == 0.054
    assert priced[0].count == 7

    operators = provider.get_operator_quote_options("dr", 38)
    assert operators[0].operator == "12"
    assert operators[0].label == "Provider 12"


def test_smsbower_provider_activation_and_status() -> None:
    provider = FakeSmsBowerProvider()

    activation = provider.get_number("dr", 38)

    assert activation.activation_id == 123
    assert activation.phone_number == "+233555123456"
    assert provider.get_status(123) == (True, "123456")
