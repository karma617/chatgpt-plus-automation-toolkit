from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from modules import mail_provider
from modules.mail_provider import MailCodeTimeoutError, MailProvider
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


def test_wait_code_raises_classified_timeout(monkeypatch) -> None:
    async def fake_fetch_hotmail_graph_code(account, since, exclude):
        return None

    monkeypatch.setattr(mail_provider, "fetch_hotmail_graph_code", fake_fetch_hotmail_graph_code)
    provider = MailProvider("hotmail", timeout_sec=0, poll_interval_sec=0)
    account = MailAccount(
        email="timeout@hotmail.com",
        password="password",
        client_id="client-id",
        refresh_token="refresh-token",
    )

    try:
        asyncio.run(provider.wait_code(account, datetime(2026, 6, 2, tzinfo=timezone.utc), set()))
    except MailCodeTimeoutError as exc:
        assert "MAIL_CODE_TIMEOUT" in str(exc)
        assert "timeout@hotmail.com" in str(exc)
    else:
        raise AssertionError("expected MailCodeTimeoutError")


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


def test_hotmail_tm_openai_sender_with_code_is_accepted() -> None:
    text = "ChatGPT <noreply@tm.openai.com>\nChatGPT\n<html>711866</html>"

    assert mail_provider.looks_like_openai_mail(text)
    assert mail_provider.choose_mail_code(text, set()) == "711866"


def test_hotmail_graph_accepts_message_inside_time_skew(monkeypatch) -> None:
    account = MailAccount(
        email="user@hotmail.com",
        password="password",
        client_id="client-id",
        refresh_token="refresh-token",
    )
    since = datetime(2026, 6, 2, 9, 0, tzinfo=timezone.utc)
    received = since - mail_provider.HOTMAIL_CODE_TIME_SKEW + timedelta(seconds=1)

    async def fake_fetch_hotmail_imap_code(account, since, exclude):
        return None

    async def fake_refresh_graph_access_token(client_id, refresh_token):
        return "graph-token"

    async def fake_list_recent_messages(token):
        return [
            {
                "receivedDateTime": received.isoformat().replace("+00:00", "Z"),
                "from": {"emailAddress": {"address": "noreply@tm1.openai.com"}},
                "subject": "ChatGPT",
                "bodyPreview": "Your code is 654321",
                "body": {"content": ""},
            }
        ]

    monkeypatch.setattr(mail_provider, "load_env", lambda path: {})
    monkeypatch.setattr(mail_provider, "fetch_hotmail_imap_code", fake_fetch_hotmail_imap_code)
    monkeypatch.setattr(mail_provider, "refresh_graph_access_token", fake_refresh_graph_access_token)
    monkeypatch.setattr(mail_provider, "list_recent_messages", fake_list_recent_messages)

    code = asyncio.run(mail_provider.fetch_hotmail_graph_code(account, since, set()))

    assert code == "654321"
