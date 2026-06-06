from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .storage import parse_mail_line
from .utils import resolve_path


REGISTER_ONLY_OUTPUT_DIR = resolve_path("output/register_only")
PAYPAL_FLOW_STATE_FILE = REGISTER_ONLY_OUTPUT_DIR / "paypal_flow_state.json"
PAYPAL_FLOW_DISCARDED_FILE = REGISTER_ONLY_OUTPUT_DIR / "paypal_flow_discarded_emails.txt"

STATUS_REGISTERED = "registered"
STATUS_LINK_READY = "link_ready"
STATUS_PAID_PENDING_AUTH = "paid_pending_auth"
STATUS_COMPLETED = "completed"
STATUS_DISCARDED = "discarded"

TERMINAL_STATUSES = {STATUS_COMPLETED, STATUS_DISCARDED}
FLOW1_BLOCKING_STATUSES = {
    STATUS_LINK_READY,
    STATUS_PAID_PENDING_AUTH,
    STATUS_COMPLETED,
    STATUS_DISCARDED,
}
LINK_POOL_BLOCKING_STATUSES = {
    STATUS_PAID_PENDING_AUTH,
    STATUS_COMPLETED,
    STATUS_DISCARDED,
}

EMAIL_RE = re.compile(r"(?i)(?<![A-Z0-9._%+-])([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,63})(?![A-Z0-9._%+-])")


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_email(value: str | None) -> str:
    text = (value or "").strip().lower()
    return text if "@" in text else ""


def _extract_email(line: str) -> str:
    account = parse_mail_line(line)
    if account:
        return normalize_email(account.email)
    match = EMAIL_RE.search(line or "")
    return normalize_email(match.group(1) if match else "")


def _account_line_without_payment_link(line: str) -> str:
    parts = [part.strip() for part in (line or "").strip().split("----")]
    if len(parts) >= 3 and parts[-1].startswith(("http://", "https://")):
        return "----".join(parts[:-1]).strip()
    return (line or "").strip()


def _payment_link_from_line(line: str) -> str:
    parts = [part.strip() for part in (line or "").strip().split("----")]
    if len(parts) >= 3 and parts[-1].startswith(("http://", "https://")):
        return parts[-1]
    return ""


def load_state(path: Path | None = None) -> dict[str, dict[str, Any]]:
    state_path = path or PAYPAL_FLOW_STATE_FILE
    if not state_path.exists():
        return {}
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    accounts = data.get("accounts") if isinstance(data, dict) else None
    if not isinstance(accounts, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for key, value in accounts.items():
        email = normalize_email(str(key))
        if not email or not isinstance(value, dict):
            continue
        record = dict(value)
        record["email"] = email
        result[email] = record
    return result


def save_state(state: dict[str, dict[str, Any]], path: Path | None = None) -> Path:
    state_path = path or PAYPAL_FLOW_STATE_FILE
    state_path.parent.mkdir(parents=True, exist_ok=True)
    accounts = {
        email: dict(record, email=email)
        for email, record in sorted(state.items())
        if normalize_email(email)
    }
    payload = {
        "version": 1,
        "updated_at": _now_iso(),
        "accounts": accounts,
    }
    state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return state_path


def update_account(
    email: str,
    status: str,
    *,
    account_line: str = "",
    payment_link: str = "",
    reason: str = "",
    stage: str = "",
    link_method: str = "",
    state_path: Path | None = None,
) -> None:
    normalized = normalize_email(email)
    if not normalized:
        return
    state = load_state(state_path)
    record = dict(state.get(normalized) or {})
    record.setdefault("created_at", _now_iso())
    record["email"] = normalized
    record["status"] = status
    record["updated_at"] = _now_iso()
    if account_line:
        record["account_line"] = account_line.strip()
    if payment_link:
        record["payment_link"] = payment_link.strip()
    if reason:
        record["reason"] = reason.strip()
    if stage:
        record["stage"] = stage.strip()
    if link_method:
        record["link_method"] = link_method.strip()
    state[normalized] = record
    save_state(state, state_path)


def mark_registered(email: str, *, account_line: str = "") -> None:
    current = load_state().get(normalize_email(email), {})
    if current.get("status") in FLOW1_BLOCKING_STATUSES:
        return
    update_account(email, STATUS_REGISTERED, account_line=account_line)


def mark_link_ready(email: str, *, account_line: str = "", payment_link: str = "", link_method: str = "") -> None:
    update_account(email, STATUS_LINK_READY, account_line=account_line, payment_link=payment_link, link_method=link_method)


def bad_link_methods(email: str) -> set[str]:
    normalized = normalize_email(email)
    if not normalized:
        return set()
    record = load_state().get(normalized, {})
    raw = record.get("bad_link_methods") or []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return set()
    return {str(item or "").strip() for item in raw if str(item or "").strip()}


def mark_needs_link(email: str, *, account_line: str = "", reason: str = "", failed_link_method: str = "") -> None:
    normalized = normalize_email(email)
    if not normalized:
        return
    state = load_state()
    record = dict(state.get(normalized) or {})
    record.setdefault("created_at", _now_iso())
    record["email"] = normalized
    record["status"] = STATUS_REGISTERED
    record["updated_at"] = _now_iso()
    record.pop("payment_link", None)
    if account_line:
        record["account_line"] = _account_line_without_payment_link(account_line)
    if reason:
        record["reason"] = reason.strip()[:1000]
        record["last_error"] = reason.strip()[:1000]
    failed_method = str(failed_link_method or record.get("link_method") or "").strip()
    if failed_method:
        methods = bad_link_methods(normalized)
        methods.add(failed_method)
        record["bad_link_methods"] = sorted(methods)
        record["last_bad_link_method"] = failed_method
        record.pop("link_method", None)
    record["last_failed_stage"] = "flow2"
    state[normalized] = record
    save_state(state)


def mark_paid_pending_auth(email: str, *, account_line: str = "") -> None:
    update_account(email, STATUS_PAID_PENDING_AUTH, account_line=account_line)


def mark_completed_many(emails: Iterable[str]) -> None:
    for email in emails:
        update_account(email, STATUS_COMPLETED)


def mark_discarded_many(emails: Iterable[str], *, reason: str = "") -> None:
    for email in emails:
        normalized = normalize_email(email)
        if not normalized:
            continue
        state = load_state()
        record = dict(state.get(normalized) or {})
        record.setdefault("created_at", _now_iso())
        record["email"] = normalized
        record["status"] = STATUS_DISCARDED
        record["updated_at"] = _now_iso()
        # 作废账号不保留旧长链，避免流程2后续误复用已失效地址。
        record.pop("payment_link", None)
        record.pop("link_method", None)
        if reason:
            record["reason"] = reason.strip()
        state[normalized] = record
        save_state(state)


def mark_stage_failure(email: str, *, stage: str, reason: str = "") -> None:
    normalized = normalize_email(email)
    if not normalized:
        return
    state = load_state()
    record = dict(state.get(normalized) or {})
    if record.get("status") in TERMINAL_STATUSES:
        return
    record.setdefault("email", normalized)
    record.setdefault("status", STATUS_REGISTERED)
    record.setdefault("created_at", _now_iso())
    record["updated_at"] = _now_iso()
    record["last_failed_stage"] = stage
    if reason:
        record["last_error"] = reason[:1000]
    state[normalized] = record
    save_state(state)


def load_manual_discarded_emails(path: Path | None = None) -> set[str]:
    discard_path = path or PAYPAL_FLOW_DISCARDED_FILE
    if not discard_path.exists():
        return set()
    emails: set[str] = set()
    for raw in discard_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        email = _extract_email(line)
        if email:
            emails.add(email)
    return emails


def append_discarded_emails(emails: Iterable[str], *, reason: str = "") -> int:
    existing = load_manual_discarded_emails()
    normalized_emails = []
    for email in emails:
        normalized = normalize_email(str(email))
        if normalized and normalized not in existing:
            normalized_emails.append(normalized)
            existing.add(normalized)
    if not normalized_emails:
        return 0
    PAYPAL_FLOW_DISCARDED_FILE.parent.mkdir(parents=True, exist_ok=True)
    safe_reason = re.sub(r"[\r\n\t]+", " ", str(reason or "")).strip()[:300]
    stamp = _now_iso()
    with PAYPAL_FLOW_DISCARDED_FILE.open("a", encoding="utf-8") as fh:
        for email in normalized_emails:
            fh.write(f"{email}\t{stamp}\t{safe_reason}\n")
    return len(normalized_emails)


def state_emails_by_status(statuses: set[str], *, state_path: Path | None = None) -> set[str]:
    return {
        email
        for email, record in load_state(state_path).items()
        if str(record.get("status") or "") in statuses
    }


def file_emails(path: Path) -> set[str]:
    if not path.exists():
        return set()
    result: set[str] = set()
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        email = _extract_email(line)
        if email:
            result.add(email)
    return result


def sync_from_files(
    *,
    registered_file: Path | None = None,
    link_file: Path | None = None,
    pending_file: Path | None = None,
) -> None:
    state = load_state()
    manual_discarded = load_manual_discarded_emails()
    changed = False

    if registered_file and registered_file.exists():
        for raw in registered_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            email = _extract_email(line)
            if not email:
                continue
            record = dict(state.get(email) or {})
            status = str(record.get("status") or "")
            if email in manual_discarded:
                continue
            if status not in FLOW1_BLOCKING_STATUSES or status == STATUS_DISCARDED:
                record.setdefault("created_at", _now_iso())
                record["email"] = email
                record["status"] = STATUS_REGISTERED if status == STATUS_DISCARDED else (status or STATUS_REGISTERED)
                record["account_line"] = line
                record["updated_at"] = _now_iso()
                if record["status"] == STATUS_REGISTERED:
                    record.pop("reason", None)
                state[email] = record
                changed = True

    if link_file and link_file.exists():
        for raw in link_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            email = _extract_email(line)
            if not email:
                continue
            record = dict(state.get(email) or {})
            if record.get("status") not in LINK_POOL_BLOCKING_STATUSES:
                record.setdefault("created_at", _now_iso())
                record["email"] = email
                record["status"] = STATUS_LINK_READY
                record["account_line"] = _account_line_without_payment_link(line)
                link = _payment_link_from_line(line)
                if link:
                    record["payment_link"] = link
                record["updated_at"] = _now_iso()
                state[email] = record
                changed = True

    if pending_file and pending_file.exists():
        for raw in pending_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            email = _extract_email(line)
            if not email:
                continue
            record = dict(state.get(email) or {})
            if record.get("status") not in TERMINAL_STATUSES:
                record.setdefault("created_at", _now_iso())
                record["email"] = email
                record["status"] = STATUS_PAID_PENDING_AUTH
                record["account_line"] = line
                record["updated_at"] = _now_iso()
                state[email] = record
                changed = True

    if changed:
        save_state(state)


def flow1_blocked_emails(*, link_file: Path | None = None, pending_file: Path | None = None) -> set[str]:
    blocked = state_emails_by_status(FLOW1_BLOCKING_STATUSES)
    blocked |= load_manual_discarded_emails()
    if link_file:
        blocked |= file_emails(link_file)
    if pending_file:
        blocked |= file_emails(pending_file)
    return blocked


def link_pool_blocked_emails(*, pending_file: Path | None = None) -> set[str]:
    blocked = state_emails_by_status(LINK_POOL_BLOCKING_STATUSES)
    blocked |= load_manual_discarded_emails()
    if pending_file:
        blocked |= file_emails(pending_file)
    return blocked


def active_link_emails(link_file: Path, *, selected_email: str = "") -> set[str]:
    blocked = link_pool_blocked_emails()
    selected = normalize_email(selected_email)
    emails = file_emails(link_file) - blocked
    if selected:
        return {email for email in emails if email == selected}
    return emails
