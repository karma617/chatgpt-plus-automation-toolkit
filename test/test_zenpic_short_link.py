import json

import pytest

from modules import zenpic_short_link


def test_normalize_service_proxy_accepts_http_auth_proxy() -> None:
    assert (
        zenpic_short_link._normalize_service_proxy("http://user:pass@proxy.example.test:8080")
        == "user:pass@proxy.example.test:8080"
    )
    assert (
        zenpic_short_link._normalize_service_proxy("proxy.example.test:8080:user:pass")
        == "user:pass@proxy.example.test:8080"
    )


def test_normalize_service_proxy_rejects_socks() -> None:
    with pytest.raises(ValueError, match="HTTP 代理"):
        zenpic_short_link._normalize_service_proxy("socks5://user:pass@proxy.example.test:1080")


def test_find_session_text_prefers_session_json(tmp_path, monkeypatch) -> None:
    cache = tmp_path / "session_cache.jsonl"
    cache.write_text(
        json.dumps(
            {
                "email": "user@example.com",
                "access_token": "fallback-token",
                "session_json": {"accessToken": "at", "user": {"email": "user@example.com"}},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(zenpic_short_link, "_session_cache_paths", lambda env: [cache])

    text = zenpic_short_link.find_session_text_for_email("user@example.com", {})

    assert json.loads(text)["accessToken"] == "at"


def test_create_zenpic_short_link_posts_and_polls(monkeypatch) -> None:
    calls = []

    class FakeSession:
        def request(self, method, url, timeout=30, **kwargs):
            calls.append((method, url, kwargs))
            if method == "POST":
                return FakeResponse({"task_id": "task-1", "pollToken": "poll-1"})
            return FakeResponse(
                {
                    "status": "succeeded",
                    "stage1_result": {"long_url": "https://stage1.example.test"},
                    "stage2_result": {
                        "long_url": "https://www.paypal.com/agreements/approve?ba_token=BA-abc123",
                        "fallback": False,
                    },
                }
            )

    class FakeResponse:
        ok = True
        status_code = 200
        text = "{}"

        def __init__(self, data):
            self._data = data

        def json(self):
            return self._data

    monkeypatch.setattr(zenpic_short_link.requests, "Session", FakeSession)
    monkeypatch.setattr(zenpic_short_link.time, "sleep", lambda seconds: None)

    result = zenpic_short_link.create_zenpic_short_link(
        email="user@example.com",
        session_text='{"accessToken":"at"}',
        us_proxy="http://u:p@us.example.test:8080",
        jp_proxy="http://u:p@jp.example.test:8080",
        env={"PAYPAL_ZENPIC_BASE_URL": "https://oai.example.test", "PAYPAL_ZENPIC_RETRY_COUNT": "2"},
    )

    assert result.long_url.endswith("BA-abc123")
    assert calls[0][0] == "POST"
    assert calls[0][1] == "https://oai.example.test/api/tasks"
    assert calls[0][2]["json"]["usProxy"] == "u:p@us.example.test:8080"
    assert calls[0][2]["json"]["jpProxy"] == "u:p@jp.example.test:8080"
    assert calls[1][0] == "GET"
    assert calls[1][2]["headers"]["X-Task-Poll-Token"] == "poll-1"


def test_create_zenpic_short_link_rejects_non_ba_result(monkeypatch) -> None:
    class FakeSession:
        def request(self, method, url, timeout=30, **kwargs):
            if method == "POST":
                return FakeResponse({"task_id": "task-1", "pollToken": "poll-1"})
            return FakeResponse({"status": "succeeded", "stage2_result": {"long_url": "https://bad.example.test"}})

    class FakeResponse:
        ok = True
        status_code = 200
        text = "{}"

        def __init__(self, data):
            self._data = data

        def json(self):
            return self._data

    monkeypatch.setattr(zenpic_short_link.requests, "Session", FakeSession)
    monkeypatch.setattr(zenpic_short_link.time, "sleep", lambda seconds: None)

    with pytest.raises(RuntimeError, match="PayPal BA 短链"):
        zenpic_short_link.create_zenpic_short_link(
            email="user@example.com",
            session_text="token",
            us_proxy="u:p@us.example.test:8080",
            jp_proxy="u:p@jp.example.test:8080",
            env={"PAYPAL_ZENPIC_BASE_URL": "https://oai.example.test"},
        )
