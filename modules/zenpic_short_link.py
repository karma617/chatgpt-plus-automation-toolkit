from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from .utils import log, resolve_path


DEFAULT_ZENPIC_BASE_URL = "https://oai.zenpic.xyz"
DEFAULT_ZENPIC_RETRY_COUNT = 5
DEFAULT_ZENPIC_TIMEOUT_SEC = 900
DEFAULT_ZENPIC_POLL_INTERVAL_SEC = 1.1
PAYPAL_BA_TOKEN_RE = re.compile(r"^https://www\.paypal\.com/agreements/approve\?ba_token=BA-[A-Za-z0-9_-]+")
_ZENPIC_PROXY_RE = re.compile(
    r"^[^:@\s]+:[^:@\s]+@(?:[A-Za-z0-9.-]+|\[[0-9A-Fa-f:.]+\]):\d{1,5}$"
)


@dataclass(frozen=True)
class ZenpicShortLinkResult:
    long_url: str
    task_id: str
    poll_token: str
    stage1_result: dict[str, Any]
    stage2_result: dict[str, Any]
    fallback: bool


def _env_int(env: dict[str, str], key: str, default: int, *, min_value: int = 0, max_value: int = 3600) -> int:
    raw = str(env.get(key) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(min_value, min(max_value, value))


def _base_url(env: dict[str, str]) -> str:
    value = str(env.get("PAYPAL_ZENPIC_BASE_URL") or DEFAULT_ZENPIC_BASE_URL).strip().rstrip("/")
    return value or DEFAULT_ZENPIC_BASE_URL


def _normalize_service_proxy(proxy: str | None) -> str:
    """转为 zenpic 表单要求的 user:password@host:port；该服务只接受 HTTP 代理。"""
    raw = str(proxy or "").strip().lstrip("\ufeff\u200b\u2060")
    if not raw:
        return ""
    if "://" not in raw:
        parts = raw.rsplit(":", 3)
        if len(parts) == 4 and parts[1].isdigit() and "@" not in raw:
            host, port, username, password = parts
            raw = f"http://{username}:{password}@{host}:{port}"
        else:
            raw = f"http://{raw}"
    parsed = urlparse(raw)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("zenpic 短链服务要求 HTTP 代理，不能使用 SOCKS 代理")
    if not parsed.hostname or not parsed.port or not parsed.username or parsed.password is None:
        raise ValueError("zenpic 代理格式须为 user:password@host:port")
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    value = f"{parsed.username}:{parsed.password}@{host}:{parsed.port}"
    if not _ZENPIC_PROXY_RE.match(value):
        raise ValueError("zenpic 代理格式须为 user:password@host:port")
    if parsed.port < 1 or parsed.port > 65535:
        raise ValueError("zenpic 代理端口必须在 1-65535")
    return value


def _session_text_from_record(record: dict[str, Any]) -> str:
    session_json = record.get("session_json")
    if isinstance(session_json, dict) and session_json:
        return json.dumps(session_json, ensure_ascii=False, separators=(",", ":"))
    raw_session = record.get("raw_session")
    if isinstance(raw_session, dict) and raw_session:
        return json.dumps(raw_session, ensure_ascii=False, separators=(",", ":"))
    if isinstance(raw_session, str) and raw_session.strip():
        return raw_session.strip()
    access_token = str(record.get("access_token") or record.get("accessToken") or "").strip()
    if not access_token:
        return ""
    return access_token


def _session_cache_paths(env: dict[str, str]) -> list[Path]:
    paths: list[str] = []
    if env.get("PAYPAL_ZENPIC_SESSION_CACHE_FILE"):
        paths.append(env["PAYPAL_ZENPIC_SESSION_CACHE_FILE"])
    paths.extend(
        [
            "output/paypal注册/sessiond/session_cache.jsonl",
            "output/register_only/sessiond/session_cache.jsonl",
            "output/gopay注册plus/session导出/session_cache.jsonl",
        ]
    )
    out: list[Path] = []
    seen: set[str] = set()
    for item in paths:
        path = resolve_path(item)
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def find_session_text_for_email(email: str, env: dict[str, str]) -> str:
    target = str(email or "").strip().lower()
    if not target:
        return ""
    for path in _session_cache_paths(env):
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            text = line.strip()
            if not text:
                continue
            try:
                record = json.loads(text)
            except Exception:
                continue
            if not isinstance(record, dict):
                continue
            if str(record.get("email") or "").strip().lower() != target:
                continue
            session_text = _session_text_from_record(record)
            if session_text:
                return session_text
    return ""


def _request_json(session: requests.Session, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
    response = session.request(method, url, timeout=kwargs.pop("timeout", 30), **kwargs)
    try:
        data = response.json()
    except Exception as exc:
        raise RuntimeError(f"zenpic 返回非 JSON: HTTP {response.status_code} {response.text[:300]}") from exc
    if not response.ok:
        detail = data.get("detail") or data.get("message") or data.get("error") or response.text[:300]
        raise RuntimeError(f"zenpic HTTP {response.status_code}: {detail}")
    if isinstance(data, dict):
        return data
    raise RuntimeError("zenpic 返回 JSON 不是对象")


def create_zenpic_short_link(
    *,
    email: str,
    session_text: str,
    us_proxy: str,
    jp_proxy: str,
    env: dict[str, str],
) -> ZenpicShortLinkResult:
    """提交 zenpic 阶段一/二任务，并只接受 PayPal BA 短链作为成功结果。"""
    access_token_or_session = str(session_text or "").strip()
    if not access_token_or_session:
        raise RuntimeError(f"zenpic session 为空: {email}")
    normalized_us_proxy = _normalize_service_proxy(us_proxy)
    normalized_jp_proxy = _normalize_service_proxy(jp_proxy)
    retry_count = _env_int(env, "PAYPAL_ZENPIC_RETRY_COUNT", DEFAULT_ZENPIC_RETRY_COUNT, min_value=0, max_value=20)
    timeout_sec = _env_int(env, "PAYPAL_ZENPIC_TIMEOUT_SEC", DEFAULT_ZENPIC_TIMEOUT_SEC, min_value=60, max_value=3600)
    poll_interval = max(
        0.5,
        float(str(env.get("PAYPAL_ZENPIC_POLL_INTERVAL_SEC") or DEFAULT_ZENPIC_POLL_INTERVAL_SEC).strip() or "1.1"),
    )
    base_url = _base_url(env)
    payload = {
        "accessToken": access_token_or_session,
        "usProxy": normalized_us_proxy,
        "jpProxy": normalized_jp_proxy,
        "providerProxy": normalized_us_proxy,
        "payment_locale": str(env.get("PAYPAL_ZENPIC_PAYMENT_LOCALE") or "ja-JP").strip() or "ja-JP",
        "stripe_publishable_key": str(env.get("PAYPAL_ZENPIC_STRIPE_KEY") or "").strip(),
        "device_id": str(env.get("PAYPAL_ZENPIC_DEVICE_ID") or "").strip(),
        "user_agent": str(env.get("PAYPAL_ZENPIC_USER_AGENT") or "").strip(),
        "retry_count": retry_count,
        "saveLocalProxy": False,
        "isEmailAccount": False,
    }
    http = requests.Session()
    log(f"[zenpic][{email}] 提交短链任务，retry_count={retry_count}")
    created = _request_json(http, "POST", f"{base_url}/api/tasks", json=payload, timeout=45)
    task_id = str(created.get("task_id") or created.get("taskId") or "").strip()
    poll_token = str(created.get("pollToken") or created.get("poll_token") or "").strip()
    if not task_id or not poll_token:
        raise RuntimeError(f"zenpic 创建任务返回缺少 task_id/poll_token: {created}")
    deadline = time.monotonic() + timeout_sec
    last_state: dict[str, Any] = {}
    headers = {"X-Task-Poll-Token": poll_token}
    while time.monotonic() < deadline:
        state = _request_json(http, "GET", f"{base_url}/api/tasks/{task_id}", headers=headers, timeout=45)
        last_state = state
        status = str(state.get("status") or "").lower()
        phase = str(state.get("phase") or "")
        message = str(state.get("message") or "")
        log(f"[zenpic][{email}] status={status or '-'} phase={phase or '-'} {message[:120]}")
        if status in {"succeeded", "failed", "canceled", "canceling"}:
            break
        time.sleep(poll_interval)
    else:
        raise RuntimeError(f"zenpic 任务超时: task_id={task_id}")

    if str(last_state.get("status") or "").lower() != "succeeded":
        detail = last_state.get("error") or last_state.get("message") or last_state
        raise RuntimeError(f"zenpic 任务失败: {detail}")
    stage1 = last_state.get("stage1_result") if isinstance(last_state.get("stage1_result"), dict) else {}
    stage2 = last_state.get("stage2_result") if isinstance(last_state.get("stage2_result"), dict) else {}
    result = last_state.get("result") if isinstance(last_state.get("result"), dict) else {}
    final_stage = stage2 or result
    long_url = str(final_stage.get("long_url") or "").strip()
    fallback = bool(final_stage.get("fallback"))
    if not PAYPAL_BA_TOKEN_RE.match(long_url):
        raise RuntimeError(f"zenpic 阶段二未返回 PayPal BA 短链: {long_url or final_stage}")
    if fallback:
        raise RuntimeError(f"zenpic 阶段二已回退，不使用该链接: {long_url}")
    return ZenpicShortLinkResult(
        long_url=long_url,
        task_id=task_id,
        poll_token=poll_token,
        stage1_result=stage1,
        stage2_result=stage2,
        fallback=fallback,
    )

