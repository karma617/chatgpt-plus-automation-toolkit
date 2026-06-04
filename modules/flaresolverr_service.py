from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import requests

from .flaresolverr_client import flaresolverr_api_url, flaresolverr_enabled
from .utils import resolve_path


DEFAULT_EXECUTABLE = "tools/flaresolverr/FlareSolverr.exe"
DEFAULT_DOCKER_IMAGE = "ghcr.io/flaresolverr/flaresolverr:latest"
DEFAULT_DOCKER_NAME = "chatgpt-plus-flaresolverr"
DEFAULT_LOG_FILE = "logs/flaresolverr.log"

_MANAGED_PROCESSES: list[subprocess.Popen[bytes]] = []
_LOG_HANDLES = []
_ENSURED = False


@dataclass(frozen=True)
class FlareSolverrServiceResult:
    ok: bool
    started: bool
    mode: str
    url: str
    message: str


def _env_bool(env: dict[str, str], key: str, default: bool = False) -> bool:
    raw = str(env.get(key) or "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on", "y"}:
        return True
    if raw in {"0", "false", "no", "off", "disabled", "none", "n"}:
        return False
    return default


def _env_int(env: dict[str, str], key: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(str(env.get(key) or "").strip()))
    except Exception:
        return default


def flaresolverr_auto_start(env: dict[str, str] | None = None) -> bool:
    values = env or {}
    return _env_bool(values, "FLARESOLVERR_AUTO_START", True)


def flaresolverr_startup_timeout_seconds(env: dict[str, str] | None = None) -> int:
    return _env_int(env or {}, "FLARESOLVERR_STARTUP_TIMEOUT_SECONDS", 45, minimum=3)


def _local_url_parts(api_url: str) -> tuple[str, int] | None:
    parsed = urlparse(api_url)
    host = (parsed.hostname or "").strip().lower()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        return None
    return host, int(parsed.port or 8191)


def is_flaresolverr_ready(api_url: str, timeout: float = 2.0) -> bool:
    try:
        response = requests.post(
            api_url,
            headers={"Content-Type": "application/json"},
            json={"cmd": "sessions.list"},
            timeout=timeout,
        )
        if response.status_code >= 500:
            return False
        data = response.json()
        return str(data.get("status") or "").lower() == "ok"
    except Exception:
        return False


def wait_for_flaresolverr(api_url: str, timeout_seconds: int) -> bool:
    deadline = time.time() + max(1, timeout_seconds)
    while time.time() < deadline:
        if is_flaresolverr_ready(api_url, timeout=2.0):
            return True
        time.sleep(1.0)
    return is_flaresolverr_ready(api_url, timeout=2.0)


def flaresolverr_executable_candidates(env: dict[str, str] | None = None) -> list[Path]:
    values = env or {}
    candidates: list[Path] = []
    configured = str(values.get("FLARESOLVERR_EXECUTABLE_PATH") or "").strip().strip('"')
    if configured:
        candidates.append(resolve_path(configured))
    candidates.append(resolve_path(DEFAULT_EXECUTABLE))
    candidates.append(resolve_path("tools/flaresolverr/flaresolverr.exe"))
    for name in ("FlareSolverr.exe", "flaresolverr.exe", "flaresolverr"):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))
    unique: list[Path] = []
    seen: set[str] = set()
    for item in candidates:
        key = str(item).lower()
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def find_flaresolverr_executable(env: dict[str, str] | None = None) -> Path | None:
    for candidate in flaresolverr_executable_candidates(env):
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _open_log_handle(env: dict[str, str]) -> object:
    raw = str(env.get("FLARESOLVERR_LOG_FILE") or DEFAULT_LOG_FILE).strip()
    log_path = resolve_path(raw)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = log_path.open("ab")
    _LOG_HANDLES.append(handle)
    return handle


def _start_executable(executable: Path, env: dict[str, str], api_url: str) -> subprocess.Popen[bytes]:
    parts = _local_url_parts(api_url)
    _host, port = parts or ("127.0.0.1", 8191)
    child_env = os.environ.copy()
    child_env.setdefault("PORT", str(port))
    child_env.setdefault("HOST", "0.0.0.0")
    child_env.setdefault("LOG_LEVEL", str(env.get("FLARESOLVERR_LOG_LEVEL") or "info"))
    stdout = _open_log_handle(env)
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    process = subprocess.Popen(
        [str(executable)],
        cwd=str(executable.parent),
        stdin=subprocess.DEVNULL,
        stdout=stdout,
        stderr=subprocess.STDOUT,
        env=child_env,
        creationflags=creationflags,
    )
    _MANAGED_PROCESSES.append(process)
    return process


def _docker_enabled(env: dict[str, str]) -> bool:
    return _env_bool(env, "FLARESOLVERR_DOCKER_ENABLED", True)


def _run_docker(args: list[str], timeout: int = 25) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def _start_docker(env: dict[str, str], api_url: str) -> tuple[bool, str]:
    docker = shutil.which("docker")
    if not docker:
        return False, "docker_not_found"
    parts = _local_url_parts(api_url)
    if not parts:
        return False, "remote_url_not_managed"
    host, port = parts
    bind_host = "127.0.0.1" if host in {"localhost", "::1"} else host
    image = str(env.get("FLARESOLVERR_DOCKER_IMAGE") or DEFAULT_DOCKER_IMAGE).strip()
    name = str(env.get("FLARESOLVERR_DOCKER_NAME") or DEFAULT_DOCKER_NAME).strip()
    inspect = _run_docker([docker, "inspect", "-f", "{{.State.Running}}", name], timeout=10)
    if inspect.returncode == 0:
        if "true" in inspect.stdout.lower():
            return True, "docker_existing_running"
        started = _run_docker([docker, "start", name], timeout=25)
        return started.returncode == 0, f"docker_start:{started.stdout.strip()[:200]}"
    run = _run_docker(
        [
            docker,
            "run",
            "-d",
            "--name",
            name,
            "-p",
            f"{bind_host}:{port}:8191",
            "-e",
            f"LOG_LEVEL={str(env.get('FLARESOLVERR_LOG_LEVEL') or 'info')}",
            "--restart",
            "unless-stopped",
            image,
        ],
        timeout=60,
    )
    return run.returncode == 0, f"docker_run:{run.stdout.strip()[:200]}"


def ensure_flaresolverr_service(
    env: dict[str, str] | None = None,
    *,
    log_func: Callable[[str], None] | None = None,
    once: bool = True,
) -> FlareSolverrServiceResult:
    global _ENSURED
    values = env or {}
    api_url = flaresolverr_api_url(values)

    def emit(message: str) -> None:
        if log_func:
            log_func(f"[FlareSolverr] {message}")

    if once and _ENSURED:
        return FlareSolverrServiceResult(True, False, "cached", api_url, "already_ensured")
    if not flaresolverr_enabled(values):
        return FlareSolverrServiceResult(True, False, "disabled", api_url, "disabled")
    if is_flaresolverr_ready(api_url):
        _ENSURED = True
        emit(f"ready: {api_url}")
        return FlareSolverrServiceResult(True, False, "existing", api_url, "ready")
    if not flaresolverr_auto_start(values):
        emit(f"not ready and auto-start disabled: {api_url}")
        return FlareSolverrServiceResult(False, False, "disabled_auto_start", api_url, "not_ready")
    if not _local_url_parts(api_url):
        emit(f"remote api not managed locally: {api_url}")
        return FlareSolverrServiceResult(False, False, "remote", api_url, "remote_not_ready")

    timeout = flaresolverr_startup_timeout_seconds(values)
    executable = find_flaresolverr_executable(values)
    if executable:
        emit(f"starting executable: {executable}")
        try:
            _start_executable(executable, values, api_url)
            if wait_for_flaresolverr(api_url, timeout):
                _ENSURED = True
                emit(f"started executable and ready: {api_url}")
                return FlareSolverrServiceResult(True, True, "executable", api_url, "started")
            emit("executable started but api is not ready before timeout")
        except Exception as exc:
            emit(f"executable start failed: {exc}")
    else:
        emit("local executable not found: tools/flaresolverr/FlareSolverr.exe")

    if _docker_enabled(values):
        ok, detail = _start_docker(values, api_url)
        emit(detail)
        if ok and wait_for_flaresolverr(api_url, timeout):
            _ENSURED = True
            emit(f"docker ready: {api_url}")
            return FlareSolverrServiceResult(True, True, "docker", api_url, "started")
    else:
        emit("docker fallback disabled")

    return FlareSolverrServiceResult(False, False, "unavailable", api_url, "not_ready")
