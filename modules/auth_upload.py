from __future__ import annotations

import json
import time
import urllib.parse
from dataclasses import dataclass
from typing import Any


def env_bool(value: str | None, default: bool = False) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return default
    if text in {"1", "true", "yes", "on", "y"}:
        return True
    if text in {"0", "false", "no", "off", "n"}:
        return False
    return default


def _env_first(env: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = str(env.get(key) or "").strip()
        if value:
            return value
    return ""


def normalize_base_url(value: str) -> str:
    raw = str(value or "").strip().rstrip("/")
    if not raw:
        return ""
    parsed = urllib.parse.urlsplit(raw)
    if not parsed.scheme or not parsed.netloc:
        return raw
    path = parsed.path.rstrip("/")
    for marker in ("/api/v1", "/api"):
        index = path.lower().find(marker)
        if index >= 0:
            path = path[:index]
            break
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path.rstrip("/"), "", ""))


def join_url(base_url: str, path: str) -> str:
    base = str(base_url or "").strip().rstrip("/")
    suffix = str(path or "").strip() or "/"
    if suffix.startswith("http://") or suffix.startswith("https://"):
        return suffix
    return f"{base}/{suffix.lstrip('/')}"


def normalize_cpa_auth_files_url(api_url: str) -> str:
    normalized = str(api_url or "").strip().rstrip("/")
    lower = normalized.lower()
    if not normalized:
        return ""
    if lower.endswith("/auth-files"):
        return normalized
    if lower.endswith("/v0/management") or lower.endswith("/management"):
        return f"{normalized}/auth-files"
    if lower.endswith("/v0"):
        return f"{normalized}/management/auth-files"
    return f"{normalized}/v0/management/auth-files"


def parse_upload_targets(env: dict[str, str]) -> tuple[str, ...]:
    raw = (
        env.get("AUTH_UPLOAD_TARGET")
        or env.get("AUTH_SERVER_TARGET")
        or env.get("AUTH_UPLOAD_PROVIDER")
        or "cpa"
    )
    text = str(raw or "").strip().lower().replace("，", ",").replace("+", ",")
    aliases = {
        "": (),
        "none": (),
        "off": (),
        "false": (),
        "no": (),
        "cpa": ("cpa",),
        "auth": ("cpa",),
        "auth_server": ("cpa",),
        "account_pool": ("cpa",),
        "sub": ("sub2api",),
        "subapi": ("sub2api",),
        "s2a": ("sub2api",),
        "sub2api": ("sub2api",),
        "all": ("cpa", "sub2api"),
        "both": ("cpa", "sub2api"),
    }
    if text in aliases:
        return aliases[text]
    result: list[str] = []
    for part in (item.strip() for item in text.split(",")):
        if part in aliases:
            result.extend(aliases[part])
    deduped: list[str] = []
    for item in result:
        if item and item not in deduped:
            deduped.append(item)
    return tuple(deduped)


def auth_upload_enabled(env: dict[str, str]) -> bool:
    return env_bool(env.get("AUTH_SERVER_UPLOAD"), default=False)


def session_upload_enabled(env: dict[str, str]) -> bool:
    return env_bool(env.get("SESSION_EXPORT_SERVER_UPLOAD"), default=False)


def _auth_headers(api_key: str, header_name: str, auth_scheme: str) -> dict[str, str]:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    key = str(api_key or "").strip()
    if not key:
        return headers
    name = str(header_name or "").strip()
    scheme = str(auth_scheme or "").strip()
    if name:
        headers[name] = f"{scheme} {key}".strip() if scheme else key
        return headers
    headers["X-API-Key"] = key
    return headers


@dataclass(frozen=True)
class UploadResult:
    target: str
    ok: bool
    skipped: bool = False
    status_code: int = 0
    error: str = ""


def cpa_upload_payload(bundle: dict[str, Any], account_type: str = "") -> dict[str, Any]:
    payload = dict(bundle)
    if account_type and not payload.get("account_type"):
        payload["account_type"] = account_type
    return payload


def sub2api_upload_payload(
    bundle: dict[str, Any],
    env: dict[str, str],
    *,
    default_priority: int = 1,
    default_concurrency: int = 10,
) -> dict[str, Any]:
    email = str(bundle.get("email") or "").strip()
    credentials = {
        "access_token": bundle.get("access_token", ""),
        "chatgpt_account_id": bundle.get("account_id", ""),
        "client_id": bundle.get("client_id", ""),
        "expires_at": _sub2api_expires_timestamp(bundle),
        "expires_in": 863999,
        "organization_id": bundle.get("workspace_id", ""),
        "refresh_token": bundle.get("refresh_token", ""),
    }
    account_item: dict[str, Any] = {
        "name": (email or "unknown")[:64],
        "platform": "openai",
        "type": "oauth",
        "credentials": credentials,
        "extra": {"load_factor": 10},
        "concurrency": default_concurrency,
        "priority": default_priority,
        "rate_multiplier": 1.0,
        "auto_pause_on_expired": True,
    }
    group_ids = _parse_int_list(env.get("SUB2API_GROUP_IDS") or env.get("SUB2API_GROUP_ID"))
    if group_ids:
        account_item["group_ids"] = group_ids
    return {
        "data": {
            "type": "sub2api-data",
            "version": 1,
            "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "proxies": [],
            "accounts": [account_item],
        },
        "skip_default_group_bind": not bool(group_ids),
    }


def upload_bundle(bundle: dict[str, Any], env: dict[str, str], account_type: str = "") -> list[UploadResult]:
    targets = parse_upload_targets(env)
    if not targets:
        return [UploadResult("none", ok=False, skipped=True, error="upload_target_disabled")]
    results: list[UploadResult] = []
    for target in targets:
        if target == "cpa":
            results.append(upload_cpa(bundle, env, account_type=account_type))
        elif target == "sub2api":
            results.append(upload_sub2api(bundle, env))
        else:
            results.append(UploadResult(target, ok=False, skipped=True, error="unknown_upload_target"))
    return results


def upload_cpa(bundle: dict[str, Any], env: dict[str, str], account_type: str = "") -> UploadResult:
    base_url = _env_first(env, "CPA_SERVER_URL", "AUTH_SERVER_URL")
    api_key = _env_first(env, "CPA_SERVER_API_KEY", "AUTH_SERVER_API_KEY", "ACCOUNT_POOL_API_KEY")
    if not base_url or not api_key:
        return UploadResult("cpa", ok=False, skipped=True, error="missing_cpa_url_or_api_key")
    return _post_cpa_auth_file(
        normalize_cpa_auth_files_url(base_url),
        cpa_upload_payload(bundle, account_type=account_type),
        api_key,
        timeout=30,
    )


def upload_sub2api(bundle: dict[str, Any], env: dict[str, str]) -> UploadResult:
    base_url = normalize_base_url(_env_first(env, "SUB2API_SERVER_URL", "SUB2API_API_URL"))
    api_key = _env_first(env, "SUB2API_API_KEY", "SUB2API_API_TOKEN", "SUB2API_TOKEN")
    if not base_url or not api_key:
        return UploadResult("sub2api", ok=False, skipped=True, error="missing_sub2api_url_or_api_key")
    return _post_json(
        "sub2api",
        join_url(base_url, "/api/v1/admin/accounts/data"),
        sub2api_upload_payload(bundle, env),
        {"Accept": "application/json", "Content-Type": "application/json", "x-api-key": api_key},
        30,
    )


def _post_json(target: str, url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int) -> UploadResult:
    try:
        import requests

        resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
        if resp.status_code < 200 or resp.status_code >= 300:
            return UploadResult(target, ok=False, status_code=resp.status_code, error=resp.text[:200])
        return UploadResult(target, ok=True, status_code=resp.status_code)
    except Exception as exc:  # noqa: BLE001
        return UploadResult(target, ok=False, error=str(exc))


def _post_cpa_auth_file(url: str, payload: dict[str, Any], api_key: str, timeout: int) -> UploadResult:
    try:
        import requests

        filename = f"{str(payload.get('email') or 'unknown').strip() or 'unknown'}.json"
        content = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        headers = {"Authorization": f"Bearer {api_key}"}
        resp = requests.post(
            url,
            files={"file": (filename, content, "application/json")},
            headers=headers,
            timeout=timeout,
        )
        if resp.status_code in (200, 201):
            return UploadResult("cpa", ok=True, status_code=resp.status_code)
        if resp.status_code in (404, 405, 415):
            raw_url = f"{url}?name={urllib.parse.quote(filename)}"
            fallback = requests.post(
                raw_url,
                data=content,
                headers={**headers, "Content-Type": "application/json"},
                timeout=timeout,
            )
            if fallback.status_code in (200, 201):
                return UploadResult("cpa", ok=True, status_code=fallback.status_code)
            return UploadResult("cpa", ok=False, status_code=fallback.status_code, error=fallback.text[:200])
        return UploadResult("cpa", ok=False, status_code=resp.status_code, error=resp.text[:200])
    except Exception as exc:  # noqa: BLE001
        return UploadResult("cpa", ok=False, error=str(exc))


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value or "").strip())
    except (TypeError, ValueError):
        return default


def _parse_int_list(value: Any) -> list[int]:
    result: list[int] = []
    for item in str(value or "").replace("，", ",").split(","):
        number = _as_int(item, 0)
        if number > 0:
            result.append(number)
    return result


def _sub2api_expires_at(bundle: dict[str, Any]) -> str:
    value = bundle.get("expired") or bundle.get("expires") or bundle.get("expires_at")
    epoch = 0.0
    if isinstance(value, (int, float)):
        epoch = float(value)
    elif isinstance(value, str):
        text = value.strip()
        if text:
            try:
                epoch = float(text)
            except ValueError:
                return text
    if epoch <= 0:
        return ""
    return time.strftime("%Y-%m-%dT%H:%M:%S+08:00", time.gmtime(epoch + 8 * 3600))


def _sub2api_expires_timestamp(bundle: dict[str, Any]) -> int:
    value = bundle.get("expired") or bundle.get("expires") or bundle.get("expires_at")
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value.strip()))
        except ValueError:
            pass
    return int(time.time() + 864000)
