from modules import proxy_config


def test_flow2_jp_proxy_file_does_not_fallback_to_us() -> None:
    env = {"PAYPAL_PROXY_FILE_US": "data/proxies/us.txt", "PAYPAL_PROXY_FILE": "legacy.txt"}

    assert proxy_config.paypal_flow2_proxy_file(env, "jp") == "data/proxies/proxies_jp.txt"


def test_flow2_jp_proxy_file_uses_explicit_jp_value() -> None:
    env = {
        "PAYPAL_PROXY_FILE_US": "data/proxies/us.txt",
        "PAYPAL_PROXY_FILE_JP": "data/proxies/jp.txt",
    }

    assert proxy_config.paypal_flow2_proxy_file(env, "jp") == "data/proxies/jp.txt"


def test_flow2_us_proxy_file_prefers_us_value() -> None:
    env = {
        "PAYPAL_PROXY_FILE_US": "data/proxies/us.txt",
        "PAYPAL_PROXY_FILE": "legacy.txt",
    }

    assert proxy_config.paypal_flow2_proxy_file(env, "default") == "data/proxies/us.txt"


def test_local_proxy_defaults_to_127_7987() -> None:
    assert proxy_config.local_proxy_url({}) == "http://127.0.0.1:7987"


def test_local_proxy_adds_http_scheme() -> None:
    assert proxy_config.local_proxy_url({"LOCAL_PROXY_URL": "127.0.0.1:7890"}) == "http://127.0.0.1:7890"


def test_register_local_proxy_defaults_to_127_7897() -> None:
    assert proxy_config.register_local_proxy_url({}) == "http://127.0.0.1:7897"


def test_register_local_proxy_adds_http_scheme() -> None:
    assert proxy_config.register_local_proxy_url({"REGISTER_LOCAL_PROXY_URL": "127.0.0.1:7898"}) == "http://127.0.0.1:7898"


def test_paypal_register_local_proxy_defaults_to_127_7897() -> None:
    assert proxy_config.paypal_register_local_proxy_url({}) == "http://127.0.0.1:7897"


def test_paypal_register_local_proxy_adds_http_scheme() -> None:
    assert proxy_config.paypal_register_local_proxy_url({"PAYPAL_REGISTER_LOCAL_PROXY_URL": "127.0.0.1:7899"}) == "http://127.0.0.1:7899"
