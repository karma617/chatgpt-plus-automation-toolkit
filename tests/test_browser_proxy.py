import asyncio
import json
import struct

import modules.browser as browser_module
from modules.browser import (
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
