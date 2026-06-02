from __future__ import annotations

from types import SimpleNamespace

from modules import auth_upload


def test_parse_upload_targets_supports_both_and_none() -> None:
    assert auth_upload.parse_upload_targets({"AUTH_UPLOAD_TARGET": "both"}) == ("cpa", "sub2api")
    assert auth_upload.parse_upload_targets({"AUTH_UPLOAD_TARGET": "none"}) == ()
    assert auth_upload.parse_upload_targets({}) == ("cpa",)


def test_upload_cpa_uses_cpa_warehouse_url_and_bearer_key(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_post(url: str, **kwargs):
        calls.append({"url": url, **kwargs})
        return SimpleNamespace(status_code=201, text="ok")

    monkeypatch.setattr(auth_upload, "requests", SimpleNamespace(post=fake_post), raising=False)
    monkeypatch.setitem(__import__("sys").modules, "requests", SimpleNamespace(post=fake_post))

    result = auth_upload.upload_cpa(
        {"email": "a@example.com", "access_token": "at"},
        {
            "CPA_SERVER_URL": "https://cpa.example/base",
            "CPA_SERVER_API_KEY": "cpa-key",
        },
    )

    assert result.ok is True
    assert calls[0]["url"] == "https://cpa.example/base/v0/management/auth-files"
    assert calls[0]["headers"]["Authorization"] == "Bearer cpa-key"
    assert calls[0]["timeout"] == 30
    assert calls[0]["files"]["file"][0] == "a@example.com.json"


def test_upload_sub2api_uses_warehouse_import_payload_and_x_api_key(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_post(url: str, json: dict, headers: dict, timeout: int):
        calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return SimpleNamespace(status_code=200, text="ok")

    monkeypatch.setattr(auth_upload, "requests", SimpleNamespace(post=fake_post), raising=False)
    monkeypatch.setitem(__import__("sys").modules, "requests", SimpleNamespace(post=fake_post))

    result = auth_upload.upload_sub2api(
        {"email": "b@example.com", "access_token": "at", "refresh_token": "rt"},
        {
            "SUB2API_SERVER_URL": "https://sub.example/api/v1",
            "SUB2API_API_KEY": "sub-token",
            "SUB2API_GROUP_IDS": "1,2",
        },
    )

    assert result.ok is True
    assert calls[0]["url"] == "https://sub.example/api/v1/admin/accounts/data"
    assert calls[0]["headers"]["x-api-key"] == "sub-token"
    account = calls[0]["json"]["data"]["accounts"][0]
    assert account["name"] == "b@example.com"
    assert account["group_ids"] == [1, 2]
    assert account["priority"] == 1
    assert account["concurrency"] == 10
    assert account["credentials"]["refresh_token"] == "rt"
