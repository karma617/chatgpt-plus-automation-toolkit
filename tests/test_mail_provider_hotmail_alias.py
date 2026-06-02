from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from modules import mail_provider
from modules.mail_provider import MailProvider
from modules.storage import MailAccount


def test_hotmail_alias_uses_hotmail_graph_fetcher(monkeypatch) -> None:
    captured = {}

    async def fake_fetch_hotmail_graph_code(account, since, exclude):
        captured["account"] = account
        captured["since"] = since
        captured["exclude"] = exclude
        return "123456"

    monkeypatch.setattr(mail_provider, "fetch_hotmail_graph_code", fake_fetch_hotmail_graph_code)
    provider = MailProvider("hotmail", timeout_sec=1, poll_interval_sec=1)
    account = MailAccount(
        email="user@hotmail.com",
        password="password",
        client_id="client-id",
        refresh_token="refresh-token",
    )
    since = datetime(2026, 6, 2, tzinfo=timezone.utc)

    code = asyncio.run(provider.fetch_code(account, since, {"000000"}))

    assert code == "123456"
    assert captured == {"account": account, "since": since, "exclude": {"000000"}}


def test_hotmail_default_skips_appleemail_api(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_fetch_appleemail_code(account, since, exclude):
        calls.append("appleemail")
        return "111111"

    async def fake_fetch_hotmail_imap_code(account, since, exclude):
        calls.append("imap")
        return "222222"

    monkeypatch.setattr(mail_provider, "load_env", lambda path: {})
    monkeypatch.setattr(mail_provider, "fetch_appleemail_code", fake_fetch_appleemail_code)
    monkeypatch.setattr(mail_provider, "fetch_hotmail_imap_code", fake_fetch_hotmail_imap_code)
    account = MailAccount(
        email="user@hotmail.com",
        password="password",
        client_id="client-id",
        refresh_token="refresh-token",
    )

    code = asyncio.run(mail_provider.fetch_hotmail_graph_code(account, datetime(2026, 6, 2, tzinfo=timezone.utc), set()))

    assert code == "222222"
    assert calls == ["imap"]


def test_hotmail_uses_appleemail_only_when_enabled(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_fetch_appleemail_code(account, since, exclude):
        calls.append("appleemail")
        return "111111"

    async def fake_fetch_hotmail_imap_code(account, since, exclude):
        calls.append("imap")
        return "222222"

    monkeypatch.setattr(mail_provider, "load_env", lambda path: {"HOTMAIL_USE_APPLEEMAIL_API": "true"})
    monkeypatch.setattr(mail_provider, "fetch_appleemail_code", fake_fetch_appleemail_code)
    monkeypatch.setattr(mail_provider, "fetch_hotmail_imap_code", fake_fetch_hotmail_imap_code)
    account = MailAccount(
        email="user@hotmail.com",
        password="password",
        client_id="client-id",
        refresh_token="refresh-token",
    )

    code = asyncio.run(mail_provider.fetch_hotmail_graph_code(account, datetime(2026, 6, 2, tzinfo=timezone.utc), set()))

    assert code == "111111"
    assert calls == ["appleemail"]
