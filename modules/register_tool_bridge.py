from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .utils import resolve_path


def _zh(text: str) -> str:
    return text.encode("ascii").decode("unicode_escape")


LABEL_REGISTER_ONLY = _zh(r"\u4ec5\u6ce8\u518c")
LABEL_TARGET_SUCCESS = _zh(r"\u76ee\u6807\u6210\u529f\u6570")
LABEL_MAIL_SOURCE = _zh(r"\u90ae\u7bb1\u6765\u6e90")
LABEL_PROXY = _zh(r"\u4ee3\u7406")
LABEL_SMS = _zh(r"\u4ec5\u6ce8\u518c/\u624b\u673a\u63a5\u7801")
LABEL_PROVIDER = _zh(r"\u5e73\u53f0")
LABEL_SMS_DISABLED = _zh(
    r"\u4ec5\u6ce8\u518c/\u624b\u673a\u63a5\u7801: "
    r"\u672a\u542f\u7528\u6216\u672a\u914d\u7f6e\uff0c"
    r"\u9047\u5230\u624b\u673a\u53f7\u5fc5\u586b\u4ecd\u6309\u539f\u903b\u8f91\u5904\u7406"
)
LABEL_REGISTER_SUCCESS_COUNT = _zh(r"\u4ec5\u6ce8\u518c\u6210\u529f\u6570")
LABEL_REGISTER_DONE = _zh(r"\u4ec5\u6ce8\u518c\u7ed3\u675f\uff0c\u6210\u529f\u6570")
LABEL_NO_REGISTER_ACCOUNTS = _zh(
    r"\u5f53\u524d\u90ae\u7bb1\u6c60\u6ca1\u6709\u53ef\u7528\u8d26\u53f7\uff0c"
    r"\u4e14\u672a\u80fd\u901a\u8fc7\u5f53\u524d\u9879\u76ee\u914d\u7f6e\u81ea\u52a8\u8865\u53f7"
)

REGISTER_ONLY_OUTPUT_DIR = resolve_path("output/register_only")
REGISTER_ONLY_SESSION_DIR = REGISTER_ONLY_OUTPUT_DIR / "sessiond"
REGISTER_ONLY_SESSION_CACHE_FILE = REGISTER_ONLY_SESSION_DIR / "session_cache.jsonl"
REGISTER_ONLY_SUMMARY_FILE = REGISTER_ONLY_OUTPUT_DIR / "registered_sessions.txt"
REGISTER_ONLY_USED_FILE = REGISTER_ONLY_OUTPUT_DIR / "used_emails.txt"
REGISTER_ONLY_IN_PROGRESS_FILE = REGISTER_ONLY_OUTPUT_DIR / "in_progress.txt"
REGISTER_ONLY_FAILED_FILE = REGISTER_ONLY_OUTPUT_DIR / "failed_accounts.txt"


@dataclass(frozen=True)
class RegisterOnlyRunResult:
    returncode: int
    success_count: int
    target_count: int
    output_dir: Path
    summary_file: Path
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and self.success_count > 0


def run_register_tool_only(
    cfg: dict[str, Any],
    *,
    count: int,
    workers: int,
    mail_source: str = "",
    selected_email: str = "",
) -> RegisterOnlyRunResult:
    """Run the migrated one-click registration path using this project's config chain."""
    del mail_source
    return asyncio.run(
        run_register_only_many(
            cfg,
            count=max(1, int(count or 1)),
            workers=max(1, int(workers or 1)),
            selected_email=selected_email,
        )
    )


async def run_register_only_many(
    cfg: dict[str, Any],
    *,
    count: int,
    workers: int,
    selected_email: str = "",
) -> RegisterOnlyRunResult:
    import main as main_app

    register_cfg = clone_register_only_config(cfg, selected_email=selected_email)
    ensure_output_files()
    store = main_app.create_store(register_cfg)
    target = max(1, int(count or 1))
    pending = await main_app.ensure_register_accounts(register_cfg, store, target)
    if pending <= 0:
        main_app.log(LABEL_NO_REGISTER_ACCOUNTS)
        return RegisterOnlyRunResult(
            1,
            0,
            target,
            REGISTER_ONLY_SESSION_DIR,
            REGISTER_ONLY_SUMMARY_FILE,
            LABEL_NO_REGISTER_ACCOUNTS,
        )

    proxy_pool = main_app.create_proxy_pool(register_cfg)
    sms_selection = main_app.resolve_flow1_sms_selection()
    worker_count = max(1, int(workers or 1))
    counter = main_app.SuccessCounter(target)
    counter.target = target

    proxy_status = f"enabled, proxy_count={proxy_pool.count()}" if proxy_pool else "disabled"
    main_app.log(
        f"{LABEL_REGISTER_ONLY}: workers={worker_count}, "
        f"{LABEL_TARGET_SUCCESS}={target}, "
        f"{LABEL_MAIL_SOURCE}={register_cfg.get('mail', {}).get('active_source', register_cfg.get('mail', {}).get('source'))}, "
        f"{LABEL_PROXY}={proxy_status}"
    )
    if sms_selection:
        country = sms_selection.get("country")
        main_app.log(
            f"{LABEL_SMS}: {LABEL_PROVIDER}={sms_selection.get('provider_label')}, "
            f"country={getattr(country, 'name', '-')}(+{getattr(country, 'dial_code', '-')})"
        )
    else:
        main_app.log(LABEL_SMS_DISABLED)

    async def worker_loop(worker_id: int) -> None:
        while True:
            if not await counter.acquire_slot():
                return
            proxy = proxy_pool.pick(worker_id) if proxy_pool else None
            result = await main_app.run_account(
                register_cfg,
                store,
                worker_id,
                proxy=proxy,
                sms_selection=sms_selection,
                create_payment_link=False,
                session_cache_path=REGISTER_ONLY_SESSION_CACHE_FILE,
                session_source="register_only_gui",
            )
            if result is None:
                await counter.release_slot(success=False)
                return
            total = await counter.release_slot(success=result is True)
            if result is True:
                main_app.worker_log(worker_id, f"{LABEL_REGISTER_SUCCESS_COUNT} {total}/{counter.target}")

    tasks = [asyncio.create_task(worker_loop(worker_id)) for worker_id in range(1, worker_count + 1)]
    await asyncio.gather(*tasks)
    mirror_used_emails()
    main_app.log(f"{LABEL_REGISTER_DONE} {counter.value}/{counter.target}")
    return RegisterOnlyRunResult(
        0 if counter.value >= counter.target else 1,
        counter.value,
        counter.target,
        REGISTER_ONLY_SESSION_DIR,
        REGISTER_ONLY_SUMMARY_FILE,
        "",
    )


def clone_register_only_config(cfg: dict[str, Any], *, selected_email: str = "") -> dict[str, Any]:
    cloned = dict(cfg)
    output_cfg = dict(cloned.get("output") or {})
    output_cfg["success_file"] = str(REGISTER_ONLY_SUMMARY_FILE)
    output_cfg["failed_file"] = str(REGISTER_ONLY_FAILED_FILE)
    output_cfg["in_progress_file"] = str(REGISTER_ONLY_IN_PROGRESS_FILE)
    cloned["output"] = output_cfg
    if selected_email:
        mail_cfg = dict(cloned.get("mail") or {})
        source_path = resolve_path(str(mail_cfg.get("accounts_file") or ""))
        raw_path = resolve_path(str(mail_cfg.get("raw_pool_file") or ""))
        selected_path = REGISTER_ONLY_OUTPUT_DIR / "selected_account.txt"
        selected_line = find_account_line(selected_email, source_path) or find_account_line(selected_email, raw_path)
        if not selected_line:
            raise RuntimeError(f"selected email not found in current mailbox config: {selected_email}")
        selected_path.parent.mkdir(parents=True, exist_ok=True)
        selected_path.write_text(selected_line.rstrip() + "\n", encoding="utf-8")
        mail_cfg["accounts_file"] = str(selected_path)
        mail_cfg["raw_pool_file"] = str(raw_path if raw_path.exists() else selected_path)
        cloned["mail"] = mail_cfg
    return cloned


def find_account_line(email: str, path: Path) -> str:
    from .storage import parse_mail_line

    target = (email or "").strip().lower()
    if not target or not path.exists():
        return ""
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        account = parse_mail_line(raw)
        if account and account.email.strip().lower() == target:
            return raw
    return ""


def ensure_output_files() -> None:
    for path in (
        REGISTER_ONLY_SUMMARY_FILE,
        REGISTER_ONLY_USED_FILE,
        REGISTER_ONLY_IN_PROGRESS_FILE,
        REGISTER_ONLY_FAILED_FILE,
        REGISTER_ONLY_SESSION_CACHE_FILE,
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text("", encoding="utf-8")


def mirror_used_emails() -> None:
    emails: set[str] = set()
    if REGISTER_ONLY_SUMMARY_FILE.exists():
        for raw in REGISTER_ONLY_SUMMARY_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
            email = raw.split("----", 1)[0].strip().lower()
            if email and "@" in email:
                emails.add(email)
    if not emails:
        return
    existing = set()
    if REGISTER_ONLY_USED_FILE.exists():
        existing = {
            line.strip().lower()
            for line in REGISTER_ONLY_USED_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()
            if line.strip()
        }
    merged = sorted(existing | emails)
    REGISTER_ONLY_USED_FILE.write_text("\n".join(merged) + "\n", encoding="utf-8")
