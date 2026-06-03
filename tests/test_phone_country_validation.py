from modules.hero_sms_provider import PhoneCountry, configured_country_catalog, enrich_countries_with_api, local_phone_number, phone_matches_country


def test_phone_matches_selected_country() -> None:
    thailand = PhoneCountry("TH", "66", "Thailand", 50)

    assert phone_matches_country("+66882016713381", thailand) is True
    assert phone_matches_country("+4367840805228", thailand) is False


def test_local_phone_number_strips_only_matching_country_prefix() -> None:
    thailand = PhoneCountry("TH", "66", "Thailand", 50)

    assert local_phone_number("+66882016713381", thailand) == "882016713381"
    assert local_phone_number("+4367840805228", thailand) == "4367840805228"


def test_hero_sms_country_names_override_sms_activate_compat_ids() -> None:
    enriched = enrich_countries_with_api(
        configured_country_catalog(),
        [
            {"heroSmsCountry": 50, "apiName": "Austria"},
            {"heroSmsCountry": 52, "apiName": "Thailand"},
        ],
    )

    austria = next(country for country in enriched if country.iso_code == "AT")
    thailand = next(country for country in enriched if country.iso_code == "TH")

    assert austria.hero_sms_country == 50
    assert austria.dial_code == "43"
    assert thailand.hero_sms_country == 52
    assert thailand.dial_code == "66"
