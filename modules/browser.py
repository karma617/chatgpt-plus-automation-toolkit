from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import BrowserContext, Page, async_playwright

from .utils import resolve_path


def _normalize_proxy_raw(raw: str) -> str:
    if "://" in raw:
        return raw
    parts = raw.rsplit(":", 3)
    if len(parts) == 4 and parts[1].isdigit() and "@" not in raw:
        host, port, username, password = parts
        return f"http://{username}:{password}@{host}:{port}"
    return f"http://{raw}"


def parse_proxy(value: str | None) -> dict[str, str] | None:
    if not value:
        return None
    raw = value.strip()
    # Tolerate BOM/zero-width characters from edited proxy files.
    raw = raw.lstrip("\ufeff\u200b\u2060")
    if not raw:
        return None
    raw = _normalize_proxy_raw(raw)
    parsed = urlparse(raw)
    if not parsed.hostname or not parsed.port:
        raise ValueError("代理格式错误，应为 host:port、http://host:port 或 socks5://host:port")
    scheme = parsed.scheme.lower()
    if scheme == "socks5h":
        scheme = "socks5"
    proxy = {"server": f"{scheme}://{parsed.hostname}:{parsed.port}"}
    if parsed.username:
        proxy["username"] = parsed.username
    if parsed.password:
        proxy["password"] = parsed.password
    return proxy


class Socks5AuthProxyBridge:
    def __init__(self, upstream_host: str, upstream_port: int, username: str, password: str):
        self.upstream_host = upstream_host
        self.upstream_port = upstream_port
        self.username = username
        self.password = password
        self._server: asyncio.AbstractServer | None = None
        self._tasks: set[asyncio.Task] = set()

    async def start(self) -> str:
        self._server = await asyncio.start_server(self._handle_client, "127.0.0.1", 0)
        sockets = self._server.sockets or []
        if not sockets:
            raise RuntimeError("failed to start local socks5 bridge")
        host, port = sockets[0].getsockname()[:2]
        return f"socks5://{host}:{port}"

    async def close(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task:
            self._tasks.add(task)
        upstream_writer: asyncio.StreamWriter | None = None
        try:
            await self._accept_local_client(reader, writer)
            atyp, address_payload, port_payload = await self._read_connect_request(reader, writer)
            upstream_reader, upstream_writer = await asyncio.open_connection(
                self.upstream_host,
                self.upstream_port,
            )
            await self._connect_upstream(upstream_reader, upstream_writer, atyp, address_payload, port_payload)
            writer.write(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
            await writer.drain()
            await self._relay(reader, writer, upstream_reader, upstream_writer)
        except Exception:
            if not writer.is_closing():
                with contextlib.suppress(Exception):
                    writer.write(b"\x05\x01\x00\x01\x00\x00\x00\x00\x00\x00")
                    await writer.drain()
        finally:
            if upstream_writer:
                upstream_writer.close()
                with contextlib.suppress(Exception):
                    await upstream_writer.wait_closed()
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            if task:
                self._tasks.discard(task)

    async def _accept_local_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        header = await reader.readexactly(2)
        version, method_count = header[0], header[1]
        methods = await reader.readexactly(method_count)
        if version != 5 or 0 not in methods:
            writer.write(b"\x05\xff")
            await writer.drain()
            raise RuntimeError("local socks5 client does not support no-auth")
        writer.write(b"\x05\x00")
        await writer.drain()

    async def _read_connect_request(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> tuple[int, bytes, bytes]:
        header = await reader.readexactly(4)
        version, command, _reserved, atyp = header
        if version != 5 or command != 1:
            writer.write(b"\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00")
            await writer.drain()
            raise RuntimeError("local socks5 client sent unsupported command")
        if atyp == 1:
            address_payload = await reader.readexactly(4)
        elif atyp == 3:
            size_payload = await reader.readexactly(1)
            address_payload = size_payload + await reader.readexactly(size_payload[0])
        elif atyp == 4:
            address_payload = await reader.readexactly(16)
        else:
            writer.write(b"\x05\x08\x00\x01\x00\x00\x00\x00\x00\x00")
            await writer.drain()
            raise RuntimeError("local socks5 client sent unsupported address type")
        port_payload = await reader.readexactly(2)
        return atyp, address_payload, port_payload

    async def _connect_upstream(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        atyp: int,
        address_payload: bytes,
        port_payload: bytes,
    ) -> None:
        writer.write(b"\x05\x02\x00\x02")
        await writer.drain()
        response = await reader.readexactly(2)
        if response[0] != 5:
            raise RuntimeError("upstream socks5 proxy sent invalid greeting")
        if response[1] == 2:
            username = self.username.encode("utf-8")
            password = self.password.encode("utf-8")
            if len(username) > 255 or len(password) > 255:
                raise ValueError("socks5 username/password is too long")
            writer.write(b"\x01" + bytes([len(username)]) + username + bytes([len(password)]) + password)
            await writer.drain()
            auth_response = await reader.readexactly(2)
            if auth_response != b"\x01\x00":
                raise RuntimeError("upstream socks5 authentication failed")
        elif response[1] != 0:
            raise RuntimeError("upstream socks5 proxy rejected supported auth methods")

        writer.write(b"\x05\x01\x00" + bytes([atyp]) + address_payload + port_payload)
        await writer.drain()
        reply = await reader.readexactly(4)
        if reply[0] != 5 or reply[1] != 0:
            await self._drain_socks5_address(reader, reply[3])
            raise RuntimeError(f"upstream socks5 connect failed: rep={reply[1] if reply else 'unknown'}")
        await self._drain_socks5_address(reader, reply[3])

    async def _drain_socks5_address(self, reader: asyncio.StreamReader, atyp: int) -> None:
        if atyp == 1:
            await reader.readexactly(4)
        elif atyp == 3:
            size = await reader.readexactly(1)
            await reader.readexactly(size[0])
        elif atyp == 4:
            await reader.readexactly(16)
        else:
            raise RuntimeError("upstream socks5 proxy sent unsupported address type")
        await reader.readexactly(2)

    async def _relay(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        upstream_reader: asyncio.StreamReader,
        upstream_writer: asyncio.StreamWriter,
    ) -> None:
        async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                writer.write(data)
                await writer.drain()

        tasks = {
            asyncio.create_task(pipe(client_reader, upstream_writer)),
            asyncio.create_task(pipe(upstream_reader, client_writer)),
        }
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*done, *pending, return_exceptions=True)


async def _looks_like_socks5_proxy(host: str, port: int, timeout: float = 1.5) -> bool:
    writer: asyncio.StreamWriter | None = None
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
        writer.write(b"\x05\x02\x00\x02")
        await writer.drain()
        response = await asyncio.wait_for(reader.readexactly(2), timeout=timeout)
        return response[0] == 5 and response[1] in {0, 2, 255}
    except Exception:
        return False
    finally:
        if writer:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()


async def prepare_proxy_for_playwright(value: str | None) -> tuple[dict[str, str] | None, Socks5AuthProxyBridge | None]:
    proxy = parse_proxy(value)
    if not proxy:
        return None, None
    parsed = urlparse(proxy.get("server", ""))
    scheme = parsed.scheme.lower()
    has_auth = bool(proxy.get("username") or proxy.get("password"))
    if (
        scheme in {"http", "https"}
        and has_auth
        and (parsed.port or 0) == 1080
        and parsed.hostname
        and await _looks_like_socks5_proxy(parsed.hostname, parsed.port or 0)
    ):
        scheme = "socks5"
    if scheme == "socks5" and has_auth:
        bridge = Socks5AuthProxyBridge(
            upstream_host=parsed.hostname or "",
            upstream_port=parsed.port or 0,
            username=proxy.get("username") or "",
            password=proxy.get("password") or "",
        )
        local_server = await bridge.start()
        return {"server": local_server}, bridge
    return proxy, None


class BrowserSession:
    def __init__(self, profile_dir: str | Path, headless: bool, slow_mo: int, timeout_ms: int, proxy: str | None = None, **kwargs):
        self.profile_dir = resolve_path(profile_dir)
        self.headless = headless
        self.slow_mo = slow_mo
        self.timeout_ms = timeout_ms
        self.proxy = proxy
        self._playwright = None
        self._proxy_bridge: Socks5AuthProxyBridge | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None

    async def __aenter__(self) -> "BrowserSession":
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        try:
            proxy, self._proxy_bridge = await prepare_proxy_for_playwright(self.proxy)
            self._playwright = await async_playwright().start()
            self.context = await self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_dir),
                headless=self.headless,
                slow_mo=self.slow_mo,
                viewport={"width": 1365, "height": 900},
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                    "--disable-gpu",
                ],
                proxy=proxy,
            )
        except Exception:
            await self.__aexit__(None, None, None)
            raise
        self.context.set_default_timeout(self.timeout_ms)
        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        return self

    async def current_page(self) -> Page:
        if not self.context:
            raise RuntimeError("浏览器上下文未启动")
        if self.page and not self.page.is_closed():
            return self.page
        pages = [page for page in self.context.pages if not page.is_closed()]
        if pages:
            self.page = pages[-1]
            return self.page
        self.page = await self.context.new_page()
        return self.page

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self.context:
            await self.context.close()
        if self._playwright:
            await self._playwright.stop()
        if self._proxy_bridge:
            await self._proxy_bridge.close()
            self._proxy_bridge = None
