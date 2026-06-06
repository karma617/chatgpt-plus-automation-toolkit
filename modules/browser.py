from __future__ import annotations

import asyncio
import contextlib
import hashlib
import importlib
import json
import os
import platform
import random
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from .utils import log, resolve_path


_WINDOWS_VIEWPORTS = [
    {"width": 1720, "height": 900},
    {"width": 1366, "height": 768},
    {"width": 1440, "height": 900},
    {"width": 1536, "height": 864},
    {"width": 1600, "height": 900},
    {"width": 1680, "height": 1050},
    {"width": 1920, "height": 1080},
]
_DEFAULT_BROWSER_WINDOW = {"width": 1720, "height": 900}
_US_TIMEZONES = [
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
    "America/Phoenix",
]
_JP_TIMEZONES = ["Asia/Tokyo"]
_DEFAULT_TIMEZONES = ["Asia/Shanghai", "Asia/Tokyo", "America/Los_Angeles"]
_ALLOWED_BROWSER_LOCALES = ("en-US", "zh-CN", "zh-JP")
_ALLOWED_BROWSER_LANGUAGE_BASES = ("en", "zh")
_FINGERPRINT_STORE_LOCK = threading.Lock()
_FINGERPRINT_SCHEMA_VERSION = 3
_BROWSER_FINGERPRINT_ENABLED_ENV = "BROWSER_FINGERPRINT_ENABLED"
_BROWSER_ENGINE_ENV = "BROWSER_ENGINE"
_BROWSER_LOCALE_ENV = "BROWSER_LOCALE"
_BROWSER_ENGINE_CHROMIUM = "chromium"
_BROWSER_ENGINE_CAMOUFOX = "camoufox"
_BROWSER_ENGINE_ALIASES = {
    "": _BROWSER_ENGINE_CHROMIUM,
    "default": _BROWSER_ENGINE_CHROMIUM,
    "playwright": _BROWSER_ENGINE_CHROMIUM,
    "chrome": _BROWSER_ENGINE_CHROMIUM,
    "chromium": _BROWSER_ENGINE_CHROMIUM,
    "camoufox": _BROWSER_ENGINE_CAMOUFOX,
}
_CAMOUFOX_REQUIREMENT = "camoufox[geoip]>=0.4.11"
_CAMOUFOX_INSTALL_LOCK = threading.Lock()


def _u(value: str) -> str:
    return value.encode("ascii").decode("unicode_escape")


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


def _is_truthy(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _browser_fingerprint_enabled() -> bool:
    return _is_truthy(os.environ.get(_BROWSER_FINGERPRINT_ENABLED_ENV), False)


def _normalize_browser_engine(value: str | None) -> str:
    raw = str(value or "").strip().lower()
    engine = _BROWSER_ENGINE_ALIASES.get(raw)
    if engine:
        return engine
    allowed = ", ".join(sorted({item for item in _BROWSER_ENGINE_ALIASES if item}))
    raise ValueError(f"BROWSER_ENGINE 不支持: {value!r}，可选: {allowed}")


def _import_async_camoufox() -> Any | None:
    try:
        module = importlib.import_module("camoufox.async_api")
        return getattr(module, "AsyncCamoufox")
    except ImportError:
        return None


def _install_camoufox_runtime() -> None:
    if getattr(sys, "frozen", False):
        raise RuntimeError("当前为打包版运行环境，无法在运行时安装 Camoufox；请在打包前安装并重新构建。")

    commands = [
        [sys.executable, "-m", "pip", "install", "-U", _CAMOUFOX_REQUIREMENT],
        [sys.executable, "-m", "camoufox", "fetch"],
    ]
    for command in commands:
        log("Camoufox 自动安装执行: " + " ".join(command))
        result = subprocess.run(command, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"Camoufox 自动安装失败，命令退出码 {result.returncode}: {' '.join(command)}")


def _reload_camoufox_modules() -> None:
    # camoufox.locale 会在导入时缓存 GeoIP extra 状态；补装后必须重载。
    for module_name in ("camoufox.locale", "camoufox.utils", "camoufox.async_api"):
        module = sys.modules.get(module_name)
        if module is not None:
            importlib.reload(module)


def _is_camoufox_geoip_extra_error(exc: BaseException) -> bool:
    name = exc.__class__.__name__
    text = str(exc).lower()
    return name == "NotInstalledGeoIPExtra" or "geoip extra" in text or "camoufox[geoip]" in text


def _repair_camoufox_geoip_extra() -> None:
    log("检测到 Camoufox GeoIP extra 缺失，开始自动补装。")
    _install_camoufox_runtime()
    _reload_camoufox_modules()
    log("Camoufox GeoIP extra 自动补装完成，继续启动浏览器。")


def _load_async_camoufox() -> Any:
    async_camoufox = _import_async_camoufox()
    if async_camoufox:
        return async_camoufox

    with _CAMOUFOX_INSTALL_LOCK:
        async_camoufox = _import_async_camoufox()
        if async_camoufox:
            return async_camoufox
        log("检测到未安装 Camoufox，开始自动安装。")
        _install_camoufox_runtime()
        async_camoufox = _import_async_camoufox()
        if async_camoufox:
            log("Camoufox 自动安装完成，继续启动浏览器。")
            return async_camoufox

    raise RuntimeError("Camoufox 自动安装完成后仍无法导入，请检查 Python 环境与 pip 安装路径。")


def _bundled_camoufox_executable() -> Path | None:
    candidates = [
        resolve_path("tools/camoufox/camoufox.exe"),
        resolve_path("tools/camoufox/Cache/camoufox.exe"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _region_hint_from_proxy(proxy: str | None) -> str:
    text = (proxy or "").lower()
    if any(key in text for key in ("japan", ".jp", "tokyo", "osaka", "jp-")):
        return "jp"
    if any(key in text for key in ("us", "usa", "america", "losangeles", "newyork")):
        return "us"
    return ""


def _actual_chrome_major() -> int:
    # Keep the UA close to the bundled browser instead of hard-coding stale versions.
    try:
        from playwright._repo_version import version as playwright_version  # type: ignore

        major = int(str(playwright_version).split(".", 1)[0])
        if 100 <= major <= 160:
            return major
    except Exception:
        pass
    return 126


def _browser_languages_for_locale(locale: str) -> list[str]:
    normalized = str(locale or "").strip()
    if normalized.lower() == "zh-jp":
        return ["zh-JP", "zh-CN", "zh", "en-US", "en"]
    if normalized.lower().startswith("zh"):
        return ["zh-CN", "zh", "en-US", "en"]
    return ["en-US", "en"]


def _normalize_browser_locale(value: str | None, default: str = "zh-JP") -> str:
    raw = str(value or "").strip().replace("_", "-")
    if not raw:
        return default
    lowered = raw.lower()
    if lowered in {"zh-jp", "jp", "jp-zh", "chinese-japan", "中文-日本"}:
        return "zh-JP"
    if lowered in {"zh", "zh-cn", "cn", "chinese", "中文"}:
        return "zh-CN"
    if lowered in {"en", "en-us", "us", "english", "英文"}:
        return "en-US"
    return default


def _accept_language_header(languages: object) -> str:
    values = [str(item or "").strip() for item in languages if str(item or "").strip()] if isinstance(languages, list) else []
    if values and values[0].lower() == "zh-jp":
        return "zh-JP,zh-CN;q=0.9,zh;q=0.8,en-US;q=0.7,en;q=0.6"
    if values and values[0].lower().startswith("zh"):
        return "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7"
    return "en-US,en;q=0.9"


def _browser_fingerprint_language_allowed(fingerprint: dict[str, object]) -> bool:
    locale = str(fingerprint.get("locale") or "")
    if locale not in _ALLOWED_BROWSER_LOCALES:
        return False
    raw_languages = fingerprint.get("languages")
    if not isinstance(raw_languages, list) or not raw_languages:
        return False
    for item in raw_languages:
        value = str(item or "")
        base = value.split("-", 1)[0].lower()
        if value not in _ALLOWED_BROWSER_LOCALES and base not in _ALLOWED_BROWSER_LANGUAGE_BASES:
            return False
    return True


def _build_fingerprint(seed: str | None, proxy: str | None, *, force_random: bool | None = None) -> dict[str, object]:
    randomize = _is_truthy(os.environ.get("BROWSER_RANDOM_FINGERPRINT"), True) if force_random is None else force_random
    base = f"{seed or ''}|{proxy or ''}|{secrets.token_hex(8) if randomize else 'stable'}|{time.time_ns() if randomize else ''}"
    digest = hashlib.sha256(base.encode("utf-8", errors="ignore")).hexdigest()
    rng = random.Random(int(digest[:16], 16))
    viewport = dict(rng.choice(_WINDOWS_VIEWPORTS))
    screen = {
        "width": viewport["width"],
        "height": viewport["height"],
        "availWidth": viewport["width"],
        "availHeight": max(1, viewport["height"] - rng.choice([40, 48, 72])),
        "colorDepth": rng.choice([24, 24, 30]),
        "pixelDepth": rng.choice([24, 24, 30]),
    }
    major = max(100, _actual_chrome_major() + rng.choice([-1, 0, 0, 1]))
    full_version = f"{major}.0.{rng.randint(6200, 6999)}.{rng.randint(20, 180)}"
    user_agent = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        f"AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{full_version} Safari/537.36"
    )
    region = _region_hint_from_proxy(proxy)
    if region == "jp":
        timezone_id = rng.choice(_JP_TIMEZONES)
        locale = rng.choice(_ALLOWED_BROWSER_LOCALES)
    elif region == "us":
        timezone_id = rng.choice(_US_TIMEZONES)
        locale = "en-US"
    else:
        timezone_id = rng.choice(_DEFAULT_TIMEZONES)
        locale = rng.choice(_ALLOWED_BROWSER_LOCALES)
    languages = _browser_languages_for_locale(locale)
    webgl_profiles = [
        (
            "Google Inc. (Intel)",
            "ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11 vs_5_0 ps_5_0, D3D11)",
        ),
        (
            "Google Inc. (Intel)",
            "ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)",
        ),
        (
            "Google Inc. (NVIDIA)",
            "ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 Direct3D11 vs_5_0 ps_5_0, D3D11)",
        ),
        (
            "Google Inc. (AMD)",
            "ANGLE (AMD, AMD Radeon(TM) Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)",
        ),
    ]
    webgl_vendor, webgl_renderer = rng.choice(webgl_profiles)
    hardware_concurrency = rng.choice([4, 6, 8, 12, 16])
    device_memory = rng.choice([4, 8, 8, 16])
    dpr = rng.choice([1, 1, 1.25, 1.5])
    profile_id = hashlib.sha256(f"{digest}|account-browser-fingerprint".encode("utf-8")).hexdigest()[:16]
    return {
        "schema_version": _FINGERPRINT_SCHEMA_VERSION,
        "profile_id": profile_id,
        "created_at": int(time.time()),
        "source": "account_bound" if force_random else "session_random",
        "user_agent": user_agent,
        "chrome_major": major,
        "chrome_full_version": full_version,
        "client_hints": {
            "brands": [
                {"brand": "Chromium", "version": str(major)},
                {"brand": "Google Chrome", "version": str(major)},
                {"brand": "Not:A-Brand", "version": "99"},
            ],
            "fullVersionList": [
                {"brand": "Chromium", "version": full_version},
                {"brand": "Google Chrome", "version": full_version},
                {"brand": "Not:A-Brand", "version": "99.0.0.0"},
            ],
            "platform": "Windows",
            "platformVersion": rng.choice(["10.0.0", "13.0.0", "15.0.0"]),
            "architecture": "x86",
            "bitness": "64",
            "mobile": False,
            "model": "",
        },
        "viewport": viewport,
        "screen": screen,
        "locale": locale,
        "languages": languages,
        "timezone_id": timezone_id,
        "device_scale_factor": dpr,
        "hardware_concurrency": hardware_concurrency,
        "device_memory": device_memory,
        "platform": "Win32" if platform.system().lower() == "windows" else "Linux x86_64",
        "navigator_vendor": "Google Inc.",
        "max_touch_points": 0,
        "do_not_track": rng.choice(["1", None, None]),
        "webgl_vendor": webgl_vendor,
        "webgl_renderer": webgl_renderer,
        "canvas_noise_seed": hashlib.sha256(f"{digest}|canvas".encode("utf-8")).hexdigest()[:16],
        "webgl_noise_seed": hashlib.sha256(f"{digest}|webgl".encode("utf-8")).hexdigest()[:16],
        "audio_noise_seed": hashlib.sha256(f"{digest}|audio".encode("utf-8")).hexdigest()[:16],
        "audio_sample_rate": rng.choice([44100, 48000]),
        "media_devices": {"audioinput": rng.choice([1, 2]), "videoinput": 1, "audiooutput": rng.choice([1, 2])},
        "plugin_count": rng.choice([3, 4, 5]),
    }


def _fingerprint_store_path() -> Path:
    return resolve_path("output/browser_fingerprints/account_fingerprints.json")


def _account_fingerprint_key(account_id: str) -> str:
    normalized = str(account_id or "").strip().lower()
    return hashlib.sha256(normalized.encode("utf-8", errors="ignore")).hexdigest()


def _load_fingerprint_store(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_fingerprint_store(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _fingerprint_log_payload(fingerprint: dict[str, object]) -> dict[str, object]:
    keys = (
        "profile_id",
        "user_agent",
        "viewport",
        "screen",
        "locale",
        "languages",
        "timezone_id",
        "device_scale_factor",
        "hardware_concurrency",
        "device_memory",
        "platform",
        "navigator_vendor",
        "max_touch_points",
        "webgl_vendor",
        "webgl_renderer",
        "canvas_noise_seed",
        "webgl_noise_seed",
        "audio_noise_seed",
        "audio_sample_rate",
        "media_devices",
        "plugin_count",
        "client_hints",
    )
    return {key: fingerprint.get(key) for key in keys if key in fingerprint}


def _fingerprint_is_current(fingerprint: dict[str, object]) -> bool:
    required_keys = {
        "schema_version",
        "profile_id",
        "user_agent",
        "chrome_full_version",
        "client_hints",
        "viewport",
        "screen",
        "locale",
        "languages",
        "timezone_id",
        "device_scale_factor",
        "hardware_concurrency",
        "device_memory",
        "webgl_vendor",
        "webgl_renderer",
        "canvas_noise_seed",
        "webgl_noise_seed",
        "audio_noise_seed",
        "audio_sample_rate",
        "media_devices",
        "plugin_count",
    }
    return (
        fingerprint.get("schema_version") == _FINGERPRINT_SCHEMA_VERSION
        and required_keys.issubset(fingerprint)
        and _browser_fingerprint_language_allowed(fingerprint)
    )


def get_or_create_account_fingerprint(account_id: str, proxy: str | None, *, log_prefix: str = "") -> dict[str, object]:
    account = str(account_id or "").strip().lower()
    if not account:
        return _build_fingerprint(None, proxy)
    key = _account_fingerprint_key(account)
    store_path = _fingerprint_store_path()
    with _FINGERPRINT_STORE_LOCK:
        store = _load_fingerprint_store(store_path)
        item = store.get(key)
        if (
            isinstance(item, dict)
            and isinstance(item.get("fingerprint"), dict)
            and _fingerprint_is_current(dict(item["fingerprint"]))
        ):
            fingerprint = dict(item["fingerprint"])
            action = "reuse"
        else:
            fingerprint = _build_fingerprint(f"{account}|account-bound", proxy, force_random=True)
            store[key] = {
                "account": account,
                "fingerprint": fingerprint,
                "created_at": int(time.time()),
                "updated_at": int(time.time()),
            }
            _save_fingerprint_store(store_path, store)
            action = "refresh" if isinstance(item, dict) else "new"
    label = (log_prefix.strip() + " ") if log_prefix else ""
    message_key = r"\u5e10\u53f7\u7ed1\u5b9a\u6d4f\u89c8\u5668\u6307\u7eb9"
    log(
        f"{label}[Fingerprint] "
        + _u(message_key)
        + f" action={action} account={account} "
        + json.dumps(_fingerprint_log_payload(fingerprint), ensure_ascii=False, sort_keys=True)
    )
    return fingerprint


async def _apply_context_fingerprint(context: BrowserContext, fingerprint: dict[str, object]) -> None:
    payload = json.dumps(fingerprint, ensure_ascii=True)
    script = """
    (() => {
        const fp = __FINGERPRINT_JSON__;
        const define = (obj, key, value) => {
            try { Object.defineProperty(obj, key, { get: () => value, configurable: true }); } catch {}
        };
        const languages = Array.isArray(fp.languages) && fp.languages.length ? fp.languages : [fp.locale || 'en-US', 'en'];
        define(navigator, 'webdriver', undefined);
        define(navigator, 'platform', fp.platform || 'Win32');
        define(navigator, 'hardwareConcurrency', fp.hardware_concurrency || 8);
        define(navigator, 'deviceMemory', fp.device_memory || 8);
        define(navigator, 'languages', languages);
        define(navigator, 'language', languages[0] || fp.locale || 'en-US');
        define(navigator, 'vendor', fp.navigator_vendor || 'Google Inc.');
        define(navigator, 'maxTouchPoints', fp.max_touch_points || 0);
        if (fp.do_not_track) define(navigator, 'doNotTrack', fp.do_not_track);
        if (navigator.userAgentData && fp.client_hints) {
            define(navigator.userAgentData, 'brands', fp.client_hints.brands || []);
            define(navigator.userAgentData, 'mobile', false);
            define(navigator.userAgentData, 'platform', fp.client_hints.platform || 'Windows');
            try {
                navigator.userAgentData.getHighEntropyValues = async (hints) => {
                    const data = fp.client_hints || {};
                    const result = {
                        brands: data.brands || [],
                        mobile: false,
                        platform: data.platform || 'Windows',
                    };
                    for (const hint of hints || []) {
                        if (hint === 'fullVersionList') result.fullVersionList = data.fullVersionList || [];
                        if (hint === 'platformVersion') result.platformVersion = data.platformVersion || '';
                        if (hint === 'architecture') result.architecture = data.architecture || 'x86';
                        if (hint === 'bitness') result.bitness = data.bitness || '64';
                        if (hint === 'model') result.model = data.model || '';
                        if (hint === 'uaFullVersion') result.uaFullVersion = fp.chrome_full_version || '';
                    }
                    return result;
                };
            } catch {}
        }
        const screen = fp.screen || fp.viewport || { width: 1366, height: 768 };
        define(window.screen, 'width', screen.width || 1366);
        define(window.screen, 'height', screen.height || 768);
        define(window.screen, 'availWidth', screen.width || 1366);
        define(window.screen, 'availHeight', Math.max(1, (screen.height || 768) - 40));
        define(window.screen, 'colorDepth', screen.colorDepth || 24);
        define(window.screen, 'pixelDepth', screen.pixelDepth || 24);
        define(window, 'devicePixelRatio', fp.device_scale_factor || 1);
        window.chrome = window.chrome || { runtime: {} };
        const pluginCount = Number(fp.plugin_count || 3);
        const fakePlugins = Array.from({ length: pluginCount }, (_, i) => ({
            name: ['PDF Viewer', 'Chrome PDF Viewer', 'Chromium PDF Viewer', 'Microsoft Edge PDF Viewer', 'WebKit built-in PDF'][i] || `Chrome Plugin ${i + 1}`,
            filename: `internal-pdf-viewer-${i}.dll`,
            description: 'Portable Document Format',
            length: 1,
        }));
        define(navigator, 'plugins', fakePlugins);
        define(navigator, 'mimeTypes', fakePlugins.map((plugin) => ({ type: 'application/pdf', enabledPlugin: plugin })));
        if (navigator.mediaDevices && navigator.mediaDevices.enumerateDevices) {
            const media = fp.media_devices || {};
            navigator.mediaDevices.enumerateDevices = async () => {
                const out = [];
                const push = (kind, count) => {
                    for (let i = 0; i < Number(count || 0); i += 1) {
                        out.push({ kind, deviceId: `${kind}-${i}-${fp.profile_id || 'fp'}`, groupId: `group-${i}`, label: '' });
                    }
                };
                push('audioinput', media.audioinput || 1);
                push('videoinput', media.videoinput || 1);
                push('audiooutput', media.audiooutput || 1);
                return out;
            };
        }
        const webglVendor = fp.webgl_vendor || 'Google Inc. (Intel)';
        const webglRenderer = fp.webgl_renderer || 'ANGLE (Intel, Intel(R) UHD Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)';
        const patchWebGL = (proto) => {
            if (!proto || !proto.getParameter) return;
            const original = proto.getParameter;
            proto.getParameter = function(parameter) {
                if (parameter === 37445) return webglVendor;
                if (parameter === 37446) return webglRenderer;
                return original.call(this, parameter);
            };
        };
        patchWebGL(window.WebGLRenderingContext && window.WebGLRenderingContext.prototype);
        patchWebGL(window.WebGL2RenderingContext && window.WebGL2RenderingContext.prototype);
        const noiseByte = (seed) => {
            const text = String(seed || '0');
            let n = 0;
            for (let i = 0; i < text.length; i += 1) n = (n * 31 + text.charCodeAt(i)) & 255;
            return n || 1;
        };
        const canvasNoise = noiseByte(fp.canvas_noise_seed);
        if (window.HTMLCanvasElement && HTMLCanvasElement.prototype.toDataURL) {
            const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
            HTMLCanvasElement.prototype.toDataURL = function(...args) {
                try {
                    const ctx = this.getContext('2d');
                    if (ctx && this.width && this.height) {
                        const x = Math.max(0, this.width - 1);
                        const y = Math.max(0, this.height - 1);
                        const data = ctx.getImageData(x, y, 1, 1);
                        data.data[0] = (data.data[0] + canvasNoise) & 255;
                        ctx.putImageData(data, x, y);
                    }
                } catch {}
                return originalToDataURL.apply(this, args);
            };
        }
        const audioRate = Number(fp.audio_sample_rate || 48000);
        const patchAudioContext = (contextName) => {
            if (!window[contextName]) return;
            const OriginalAudioContext = window[contextName];
            window[contextName] = function(...args) {
                const ctx = new OriginalAudioContext(...args);
                try { define(ctx, 'sampleRate', audioRate); } catch {}
                return ctx;
            };
            window[contextName].prototype = OriginalAudioContext.prototype;
        };
        patchAudioContext('AudioContext');
        patchAudioContext('webkitAudioContext');
    })();
    """.replace("__FINGERPRINT_JSON__", payload)
    await context.add_init_script(script=script)


class BrowserSession:
    def __init__(
        self,
        profile_dir: str | Path,
        headless: bool,
        slow_mo: int,
        timeout_ms: int,
        proxy: str | None = None,
        isolated: bool = True,
        fingerprint_seed: str | None = None,
        account_id: str | None = None,
        log_prefix: str = "",
        browser_engine: str | None = None,
        camoufox_executable_path: str | None = None,
        camoufox_geoip: bool | None = None,
        browser_locale: str | None = None,
        **kwargs,
    ):
        self.profile_dir = resolve_path(profile_dir)
        self.headless = headless
        self.slow_mo = slow_mo
        self.timeout_ms = timeout_ms
        self.proxy = proxy
        # The parameter is kept for call-site compatibility; runtime is always incognito.
        self.isolated = True
        self.fingerprint_seed = fingerprint_seed
        self.account_id = account_id
        self.log_prefix = log_prefix
        self.browser_engine = _normalize_browser_engine(browser_engine or os.environ.get(_BROWSER_ENGINE_ENV))
        self.browser_locale = _normalize_browser_locale(
            browser_locale if browser_locale is not None else os.environ.get(_BROWSER_LOCALE_ENV)
        )
        self.browser_languages = _browser_languages_for_locale(self.browser_locale)
        self.camoufox_executable_path = str(
            camoufox_executable_path
            if camoufox_executable_path is not None
            else os.environ.get("CAMOUFOX_EXECUTABLE_PATH", "")
        ).strip()
        if camoufox_geoip is None:
            self.camoufox_geoip = _is_truthy(os.environ.get("CAMOUFOX_GEOIP"), False)
        elif isinstance(camoufox_geoip, str):
            self.camoufox_geoip = _is_truthy(camoufox_geoip, False)
        else:
            self.camoufox_geoip = bool(camoufox_geoip)
        self.fingerprint = None
        if self.browser_engine == _BROWSER_ENGINE_CHROMIUM and _browser_fingerprint_enabled():
            self.fingerprint = (
                get_or_create_account_fingerprint(account_id, proxy, log_prefix=log_prefix)
                if str(account_id or "").strip()
                else _build_fingerprint(fingerprint_seed, proxy)
            )
            # 浏览器 UI 语言只允许中/英；代理国家不应把页面文案带到日文等其他语言。
            self.fingerprint["locale"] = self.browser_locale
            self.fingerprint["languages"] = list(self.browser_languages)
        self._playwright = None
        self._camoufox_context: Any | None = None
        self._browser: Browser | None = None
        self._proxy_bridge: Socks5AuthProxyBridge | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None

    async def __aenter__(self) -> "BrowserSession":
        try:
            proxy, self._proxy_bridge = await prepare_proxy_for_playwright(self.proxy)
            self._browser = await self._launch_browser(proxy)
            self.context = await self._browser.new_context(**self._context_options())
        except Exception:
            await self.__aexit__(None, None, None)
            raise
        self.context.set_default_timeout(self.timeout_ms)
        with contextlib.suppress(Exception):
            await self.context.grant_permissions(["persistent-storage"], origin="https://chatgpt.com")
        if self.fingerprint:
            await _apply_context_fingerprint(self.context, self.fingerprint)
        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        return self

    async def _launch_browser(self, proxy: dict[str, str] | None) -> Browser:
        if self.browser_engine == _BROWSER_ENGINE_CAMOUFOX:
            return await self._launch_camoufox(proxy)
        return await self._launch_chromium(proxy)

    async def _launch_chromium(self, proxy: dict[str, str] | None) -> Browser:
        self._playwright = await async_playwright().start()
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--no-sandbox",
            "--disable-gpu",
            f"--window-size={_DEFAULT_BROWSER_WINDOW['width']},{_DEFAULT_BROWSER_WINDOW['height']}",
        ]
        if self.fingerprint:
            launch_args.append(f"--lang={self.fingerprint.get('locale') or 'en-US'}")
        return await self._playwright.chromium.launch(
            headless=self.headless,
            slow_mo=self.slow_mo,
            args=launch_args,
            proxy=proxy,
        )

    async def _launch_camoufox(self, proxy: dict[str, str] | None) -> Browser:
        AsyncCamoufox = await asyncio.to_thread(_load_async_camoufox)

        launch_kwargs: dict[str, object] = {
            "headless": self.headless,
            "slow_mo": self.slow_mo,
            # 固定窗口尺寸，避免套餐页右下角地区控件落到屏幕外。
            "window": (_DEFAULT_BROWSER_WINDOW["width"], _DEFAULT_BROWSER_WINDOW["height"]),
            "firefox_user_prefs": {
                # 自动允许 StorageManager.persist()，避免左上角权限弹窗遮挡支付方式按钮。
                "dom.storageManager.enabled": True,
                "dom.storageManager.prompt.testing": True,
                "dom.storageManager.prompt.testing.allow": True,
                "permissions.default.persistent-storage": 1,
            },
        }
        if platform.system().lower() == "windows":
            launch_kwargs["os"] = "windows"
        if proxy:
            launch_kwargs["proxy"] = proxy
        # 显式传入，避免 Camoufox 在代理场景下误用默认 GeoIP 行为。
        launch_kwargs["geoip"] = self.camoufox_geoip
        # 不在 Camoufox launch 层传 locale。Camoufox 0.4.11 会把 Intl.DisplayNames
        # 的 region 名称全部伪装成当前地区，导致套餐页国家列表全显示为“日本/美国”。
        # 语言与 Accept-Language 由 Playwright context 控制，仍保持中文界面与日区偏好。
        executable_path = str(resolve_path(self.camoufox_executable_path)) if self.camoufox_executable_path else ""
        if not executable_path:
            bundled_executable = _bundled_camoufox_executable()
            executable_path = str(bundled_executable) if bundled_executable else ""
        if executable_path:
            launch_kwargs["executable_path"] = executable_path
        # Camoufox 自带 Firefox 指纹面，不能再叠加本项目的 Chrome UA/JS 指纹。
        self._camoufox_context = AsyncCamoufox(**launch_kwargs)
        try:
            return await self._camoufox_context.__aenter__()
        except Exception as exc:
            self._camoufox_context = None
            if not _is_camoufox_geoip_extra_error(exc):
                raise
            await asyncio.to_thread(_repair_camoufox_geoip_extra)
            AsyncCamoufox = await asyncio.to_thread(_load_async_camoufox)
            self._camoufox_context = AsyncCamoufox(**launch_kwargs)
            return await self._camoufox_context.__aenter__()

    def _context_options(self) -> dict[str, object]:
        language_options: dict[str, object] = {
            "locale": self.browser_locale,
            "viewport": dict(_DEFAULT_BROWSER_WINDOW),
            "screen": {
                "width": _DEFAULT_BROWSER_WINDOW["width"],
                "height": _DEFAULT_BROWSER_WINDOW["height"],
                "availWidth": _DEFAULT_BROWSER_WINDOW["width"],
                "availHeight": _DEFAULT_BROWSER_WINDOW["height"] - 40,
            },
            "extra_http_headers": {
                "Accept-Language": _accept_language_header(self.browser_languages),
            },
        }
        if not self.fingerprint:
            return language_options
        return {
            "viewport": self.fingerprint["viewport"],
            "screen": self.fingerprint["screen"],
            "user_agent": self.fingerprint["user_agent"],
            "locale": self.fingerprint["locale"],
            "timezone_id": self.fingerprint["timezone_id"],
            "extra_http_headers": {
                "Accept-Language": _accept_language_header(self.fingerprint.get("languages")),
            },
            "device_scale_factor": self.fingerprint["device_scale_factor"],
            "is_mobile": False,
            "has_touch": False,
        }

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

    async def _safe_close(self, label: str, closer) -> None:
        """关闭浏览器资源；driver 已断开时只记日志，不覆盖主流程结果。"""
        try:
            await closer()
        except Exception as exc:
            text = str(exc)
            if any(
                marker in text
                for marker in (
                    "Connection closed while reading from the driver",
                    "Target page, context or browser has been closed",
                    "Browser has been closed",
                    "Event loop is closed",
                )
            ):
                if self.log_prefix:
                    print(f"{self.log_prefix} browser cleanup ignored: {label}: {text}", flush=True)
                return
            raise

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self.context:
            await self._safe_close("context", self.context.close)
            self.context = None
        if self._browser:
            if self._camoufox_context:
                await self._safe_close(
                    "camoufox",
                    lambda: self._camoufox_context.__aexit__(exc_type, exc, tb),
                )
                self._camoufox_context = None
            else:
                await self._safe_close("browser", self._browser.close)
            self._browser = None
        if self._playwright:
            await self._safe_close("playwright", self._playwright.stop)
            self._playwright = None
        if self._camoufox_context:
            await self._safe_close(
                "camoufox",
                lambda: self._camoufox_context.__aexit__(exc_type, exc, tb),
            )
            self._camoufox_context = None
        if self._proxy_bridge:
            await self._safe_close("proxy_bridge", self._proxy_bridge.close)
            self._proxy_bridge = None
