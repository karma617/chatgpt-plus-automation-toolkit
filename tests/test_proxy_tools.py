from control_panel.proxy_tools import add_proxy_schemes, ensure_proxy_scheme


def test_ensure_proxy_scheme_adds_http_by_default() -> None:
    assert ensure_proxy_scheme("127.0.0.1:7890") == "http://127.0.0.1:7890"
    assert ensure_proxy_scheme("user:pass@host:8080") == "http://user:pass@host:8080"


def test_ensure_proxy_scheme_keeps_supported_existing_scheme() -> None:
    assert ensure_proxy_scheme("http://host:8080") == "http://host:8080"
    assert ensure_proxy_scheme("https://host:8080") == "https://host:8080"
    assert ensure_proxy_scheme("socks5://host:1080") == "socks5://host:1080"


def test_add_proxy_schemes_normalizes_each_non_empty_line() -> None:
    content = "\n127.0.0.1:7890\nsocks5://127.0.0.1:1080\nexample.com:8080:user:pass\n"

    assert add_proxy_schemes(content) == (
        "http://127.0.0.1:7890\n"
        "socks5://127.0.0.1:1080\n"
        "http://example.com:8080:user:pass\n"
    )
