import asyncio
import json
import struct

import modules.browser as browser_module
from modules.browser import (
    BrowserSession,
    Socks5AuthProxyBridge,
    get_or_create_account_fingerprint,
    parse_proxy,
    prepare_proxy_for_playwright,
)


def test_parse_proxy_normalizes_socks5h_for_playwright() -> None:
    proxy = parse_proxy("socks5h://user:pass@example.com:1080")

    assert proxy == {
        "server": "socks5://example.com:1080",
        "username": "user",
        "password": "pass",
    }


def test_parse_proxy_keeps_socks5_scheme() -> None:
    assert parse_proxy("socks5://example.com:1080") == {"server": "socks5://example.com:1080"}


def test_parse_proxy_supports_host_port_user_pass() -> None:
    assert parse_proxy("us.cliproxy.io:3010:user:pass") == {
        "server": "http://us.cliproxy.io:3010",
        "username": "user",
        "password": "pass",
    }


def test_parse_proxy_supports_user_pass_at_host_port_without_scheme() -> None:
    assert parse_proxy("user:pass@us.cliproxy.io:3010") == {
        "server": "http://us.cliproxy.io:3010",
        "username": "user",
        "password": "pass",
    }


def test_account_fingerprint_is_persisted_and_reused(tmp_path, monkeypatch) -> None:
    store_path = tmp_path / "account_fingerprints.json"
    monkeypatch.setattr(browser_module, "_fingerprint_store_path", lambda: store_path)

    first = get_or_create_account_fingerprint(
        "User@Example.com",
        "socks5h://japan.example:1080",
        log_prefix="[test]",
    )
    second = get_or_create_account_fingerprint(
        "user@example.com",
        "socks5h://different.example:1080",
        log_prefix="[test]",
    )
    store = json.loads(store_path.read_text(encoding="utf-8"))

    assert first["profile_id"] == second["profile_id"]
    assert first["user_agent"] == second["user_agent"]
    assert first["webgl_renderer"] == second["webgl_renderer"]
    assert first["canvas_noise_seed"] == second["canvas_noise_seed"]
    assert first["audio_noise_seed"] == second["audio_noise_seed"]
    assert first["client_hints"] == second["client_hints"]
    assert len(store) == 1
    record = next(iter(store.values()))
    assert record["account"] == "user@example.com"
    assert record["fingerprint"]["profile_id"] == first["profile_id"]


def test_account_fingerprint_refreshes_legacy_records(tmp_path, monkeypatch) -> None:
    store_path = tmp_path / "account_fingerprints.json"
    key = browser_module._account_fingerprint_key("legacy@example.com")
    store_path.write_text(
        json.dumps(
            {
                key: {
                    "account": "legacy@example.com",
                    "fingerprint": {
                        "schema_version": 1,
                        "profile_id": "legacy",
                        "user_agent": "legacy",
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(browser_module, "_fingerprint_store_path", lambda: store_path)

    fingerprint = get_or_create_account_fingerprint("legacy@example.com", "socks5h://japan.example:1080")
    store = json.loads(store_path.read_text(encoding="utf-8"))

    assert fingerprint["profile_id"] != "legacy"
    assert fingerprint["schema_version"] == browser_module._FINGERPRINT_SCHEMA_VERSION
    assert "webgl_renderer" in fingerprint
    assert store[key]["fingerprint"]["profile_id"] == fingerprint["profile_id"]


def test_fingerprint_languages_are_limited_to_chinese_or_english(monkeypatch) -> None:
    monkeypatch.setenv("BROWSER_RANDOM_FINGERPRINT", "0")

    for proxy in (
        "socks5h://japan.example:1080",
        "http://us.example:8080",
        "",
    ):
        fingerprint = browser_module._build_fingerprint("language-test", proxy)
        languages = fingerprint["languages"]

        assert fingerprint["locale"] in {"en-US", "zh-CN", "zh-JP"}
        assert all(str(item).split("-", 1)[0] in {"en", "zh"} for item in languages)
        assert not any(str(item).startswith("ja") for item in languages)


def test_accept_language_header_follows_fingerprint_language() -> None:
    assert browser_module._accept_language_header(["zh-JP", "zh-CN", "zh", "en-US", "en"]) == (
        "zh-JP,zh-CN;q=0.9,zh;q=0.8,en-US;q=0.7,en;q=0.6"
    )
    assert browser_module._accept_language_header(["zh-CN", "zh", "en-US", "en"]) == (
        "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7"
    )
    assert browser_module._accept_language_header(["en-US", "en"]) == "en-US,en;q=0.9"


def test_browser_session_disables_fingerprint_by_default(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("BROWSER_FINGERPRINT_ENABLED", raising=False)

    session = BrowserSession(
        profile_dir=tmp_path / "profile",
        headless=True,
        slow_mo=0,
        timeout_ms=1000,
        proxy="socks5h://japan.example:1080",
        fingerprint_seed="seed",
        account_id="user@example.com",
    )

    assert session.fingerprint is None
    assert session._context_options() == {
        "locale": "zh-JP",
        "viewport": {"width": 1720, "height": 900},
        "screen": {
            "width": 1720,
            "height": 900,
            "availWidth": 1720,
            "availHeight": 860,
        },
        "extra_http_headers": {
            "Accept-Language": "zh-JP,zh-CN;q=0.9,zh;q=0.8,en-US;q=0.7,en;q=0.6",
        },
    }


def test_browser_session_fingerprint_can_be_enabled_explicitly(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BROWSER_FINGERPRINT_ENABLED", "1")
    monkeypatch.setenv("BROWSER_RANDOM_FINGERPRINT", "0")

    session = BrowserSession(
        profile_dir=tmp_path / "profile",
        headless=True,
        slow_mo=0,
        timeout_ms=1000,
        proxy="http://us.example:8080",
        fingerprint_seed="seed",
    )
    options = session._context_options()

    assert session.fingerprint
    assert options["user_agent"] == session.fingerprint["user_agent"]
    assert options["locale"] == session.fingerprint["locale"]
    assert options["extra_http_headers"]["Accept-Language"] == browser_module._accept_language_header(
        session.fingerprint.get("languages")
    )


def test_browser_session_defaults_to_chromium_engine(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("BROWSER_ENGINE", raising=False)

    session = BrowserSession(
        profile_dir=tmp_path / "profile",
        headless=True,
        slow_mo=0,
        timeout_ms=1000,
    )

    assert session.browser_engine == "chromium"


def test_browser_session_accepts_camoufox_engine_without_chrome_fingerprint(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BROWSER_FINGERPRINT_ENABLED", "1")

    session = BrowserSession(
        profile_dir=tmp_path / "profile",
        headless=True,
        slow_mo=0,
        timeout_ms=1000,
        browser_engine="camoufox",
        fingerprint_seed="seed",
    )

    assert session.browser_engine == "camoufox"
    assert session.fingerprint is None
    assert session._context_options()["locale"] == "zh-JP"
    assert session.browser_locale == "zh-JP"
    assert session.browser_languages == ["zh-JP", "zh-CN", "zh", "en-US", "en"]


def test_camoufox_launch_keeps_locale_out_of_launch_layer(tmp_path, monkeypatch) -> None:
    captured = {}

    class FakeCamoufox:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def __aenter__(self):
            return object()

    monkeypatch.setattr(browser_module, "_load_async_camoufox", lambda: FakeCamoufox)
    session = BrowserSession(
        profile_dir=tmp_path / "profile",
        headless=True,
        slow_mo=0,
        timeout_ms=1000,
        browser_engine="camoufox",
        camoufox_geoip=True,
        proxy="socks5h://japan.example:1080",
    )

    async def run() -> None:
        proxy, bridge = await prepare_proxy_for_playwright(session.proxy)
        assert bridge is None
        await session._launch_camoufox(proxy)

    asyncio.run(run())

    assert captured["geoip"] is True
    assert captured["window"] == (1720, 900)
    assert captured["firefox_user_prefs"]["dom.storageManager.prompt.testing.allow"] is True
    assert captured["firefox_user_prefs"]["permissions.default.persistent-storage"] == 1
    assert "locale" not in captured
    assert captured["proxy"] == {"server": "socks5://japan.example:1080"}


def test_camoufox_launch_repairs_missing_geoip_extra(tmp_path, monkeypatch) -> None:
    events: list[str] = []

    class NotInstalledGeoIPExtra(RuntimeError):
        pass

    class FakeCamoufox:
        def __init__(self, **kwargs):
            events.append("init")

        async def __aenter__(self):
            if events.count("enter") == 0:
                events.append("enter")
                raise NotInstalledGeoIPExtra("Please install the geoip extra to use this feature: pip install camoufox[geoip]")
            events.append("enter")
            return object()

    monkeypatch.setattr(browser_module, "_load_async_camoufox", lambda: FakeCamoufox)
    monkeypatch.setattr(browser_module, "_repair_camoufox_geoip_extra", lambda: events.append("repair"))
    session = BrowserSession(
        profile_dir=tmp_path / "profile",
        headless=True,
        slow_mo=0,
        timeout_ms=1000,
        browser_engine="camoufox",
        camoufox_geoip=True,
    )

    asyncio.run(session._launch_camoufox(None))

    assert events == ["init", "enter", "repair", "init", "enter"]


def test_browser_locale_env_can_use_english(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BROWSER_LOCALE", "en-US")

    session = BrowserSession(
        profile_dir=tmp_path / "profile",
        headless=True,
        slow_mo=0,
        timeout_ms=1000,
        browser_engine="camoufox",
    )

    assert session.browser_locale == "en-US"
    assert session.browser_languages == ["en-US", "en"]


def test_browser_session_rejects_unknown_engine(tmp_path) -> None:
    try:
        BrowserSession(
            profile_dir=tmp_path / "profile",
            headless=True,
            slow_mo=0,
            timeout_ms=1000,
            browser_engine="firefox",
        )
    except ValueError as exc:
        assert "BROWSER_ENGINE 不支持" in str(exc)
    else:
        raise AssertionError("unknown browser engine should fail")


def test_load_async_camoufox_installs_when_missing(monkeypatch) -> None:
    class FakeCamoufox:
        pass

    state = {"installed": False}

    def fake_import():
        return FakeCamoufox if state["installed"] else None

    def fake_install() -> None:
        state["installed"] = True

    monkeypatch.setattr(browser_module, "_import_async_camoufox", fake_import)
    monkeypatch.setattr(browser_module, "_install_camoufox_runtime", fake_install)

    assert browser_module._load_async_camoufox() is FakeCamoufox
    assert state["installed"] is True


def test_load_async_camoufox_reports_import_failure_after_install(monkeypatch) -> None:
    monkeypatch.setattr(browser_module, "_import_async_camoufox", lambda: None)
    monkeypatch.setattr(browser_module, "_install_camoufox_runtime", lambda: None)

    try:
        browser_module._load_async_camoufox()
    except RuntimeError as exc:
        assert "仍无法导入" in str(exc)
    else:
        raise AssertionError("Camoufox import failure should fail after attempted install")


def test_install_camoufox_runtime_runs_pip_and_fetch(monkeypatch) -> None:
    commands: list[list[str]] = []

    class Result:
        returncode = 0

    def fake_run(command, check=False):
        commands.append(command)
        assert check is False
        return Result()

    monkeypatch.setattr(browser_module.subprocess, "run", fake_run)
    monkeypatch.setattr(browser_module.sys, "frozen", False, raising=False)

    browser_module._install_camoufox_runtime()

    assert commands == [
        [browser_module.sys.executable, "-m", "pip", "install", "-U", browser_module._CAMOUFOX_REQUIREMENT],
        [browser_module.sys.executable, "-m", "camoufox", "fetch"],
    ]


def test_bundled_camoufox_executable_is_detected(tmp_path, monkeypatch) -> None:
    root = tmp_path / "app"
    executable = root / "tools" / "camoufox" / "camoufox.exe"
    executable.parent.mkdir(parents=True)
    executable.write_text("", encoding="utf-8")
    monkeypatch.setattr(browser_module, "resolve_path", lambda value: root / value)

    assert browser_module._bundled_camoufox_executable() == executable


def test_account_fingerprint_refreshes_cached_non_english_chinese_language(tmp_path, monkeypatch) -> None:
    store_path = tmp_path / "account_fingerprints.json"
    key = browser_module._account_fingerprint_key("jp-cache@example.com")
    stale = browser_module._build_fingerprint("stale", "socks5h://japan.example:1080", force_random=True)
    stale["schema_version"] = browser_module._FINGERPRINT_SCHEMA_VERSION
    stale["locale"] = "ja-JP"
    stale["languages"] = ["ja-JP", "ja", "en-US", "en"]
    store_path.write_text(
        json.dumps(
            {
                key: {
                    "account": "jp-cache@example.com",
                    "fingerprint": stale,
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(browser_module, "_fingerprint_store_path", lambda: store_path)

    fingerprint = get_or_create_account_fingerprint("jp-cache@example.com", "socks5h://japan.example:1080")
    store = json.loads(store_path.read_text(encoding="utf-8"))

    assert fingerprint["profile_id"] != stale["profile_id"]
    assert fingerprint["locale"] in {"en-US", "zh-CN", "zh-JP"}
    assert not any(str(item).startswith("ja") for item in fingerprint["languages"])
    assert store[key]["fingerprint"]["profile_id"] == fingerprint["profile_id"]


def test_prepare_proxy_bridges_authenticated_socks5() -> None:
    async def run() -> None:
        proxy, bridge = await prepare_proxy_for_playwright("socks5://user:pass@example.com:1080")
        try:
            assert bridge is not None
            assert proxy is not None
            assert proxy["server"].startswith("socks5://127.0.0.1:")
            assert "username" not in proxy
            assert "password" not in proxy
        finally:
            if bridge:
                await bridge.close()

    asyncio.run(run())


def test_prepare_proxy_leaves_http_auth_unchanged() -> None:
    async def run() -> None:
        proxy, bridge = await prepare_proxy_for_playwright("http://user:pass@example.com:8080")
        assert bridge is None
        assert proxy == {
            "server": "http://example.com:8080",
            "username": "user",
            "password": "pass",
        }

    asyncio.run(run())


def test_prepare_proxy_bridges_mislabelled_http_1080_socks5(monkeypatch) -> None:
    async def fake_probe(host: str, port: int, timeout: float = 1.5) -> bool:
        assert (host, port) == ("example.com", 1080)
        return True

    async def run() -> None:
        monkeypatch.setattr(browser_module, "_looks_like_socks5_proxy", fake_probe)
        proxy, bridge = await prepare_proxy_for_playwright("http://user:pass@example.com:1080")
        try:
            assert bridge is not None
            assert proxy is not None
            assert proxy["server"].startswith("socks5://127.0.0.1:")
        finally:
            if bridge:
                await bridge.close()

    asyncio.run(run())


def test_socks5_auth_bridge_connects_to_authenticated_upstream() -> None:
    async def run() -> None:
        seen: dict[str, object] = {}

        async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            greeting = await reader.readexactly(4)
            seen["greeting"] = greeting
            writer.write(b"\x05\x02")
            await writer.drain()

            auth_version = await reader.readexactly(1)
            username_len = (await reader.readexactly(1))[0]
            username = await reader.readexactly(username_len)
            password_len = (await reader.readexactly(1))[0]
            password = await reader.readexactly(password_len)
            seen["auth"] = (auth_version, username, password)
            writer.write(b"\x01\x00")
            await writer.drain()

            header = await reader.readexactly(4)
            domain_len = (await reader.readexactly(1))[0]
            domain = await reader.readexactly(domain_len)
            port = struct.unpack("!H", await reader.readexactly(2))[0]
            seen["connect"] = (header, domain, port)
            writer.write(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
            await writer.drain()

            payload = await reader.readexactly(4)
            seen["payload"] = payload
            writer.write(b"pong")
            await writer.drain()
            writer.close()

        upstream_server = await asyncio.start_server(upstream, "127.0.0.1", 0)
        upstream_port = upstream_server.sockets[0].getsockname()[1]
        bridge = Socks5AuthProxyBridge("127.0.0.1", upstream_port, "user", "pass")
        local_server = await bridge.start()
        local_port = int(local_server.rsplit(":", 1)[1])
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", local_port)
            writer.write(b"\x05\x01\x00")
            await writer.drain()
            assert await reader.readexactly(2) == b"\x05\x00"

            domain = b"target.example"
            writer.write(b"\x05\x01\x00\x03" + bytes([len(domain)]) + domain + struct.pack("!H", 443))
            await writer.drain()
            assert await reader.readexactly(10) == b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00"

            writer.write(b"ping")
            await writer.drain()
            assert await reader.readexactly(4) == b"pong"
            writer.close()
            await writer.wait_closed()
        finally:
            await bridge.close()
            upstream_server.close()
            await upstream_server.wait_closed()

        assert seen["greeting"] == b"\x05\x02\x00\x02"
        assert seen["auth"] == (b"\x01", b"user", b"pass")
        assert seen["connect"] == (b"\x05\x01\x00\x03", b"target.example", 443)
        assert seen["payload"] == b"ping"

    asyncio.run(run())
