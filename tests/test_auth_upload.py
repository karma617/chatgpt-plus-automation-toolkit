from __future__ import annotations

from types import SimpleNamespace

from modules import auth_upload


def test_parse_upload_targets_supports_both_and_none() -> None:
    assert auth_upload.parse_upload_targets({"AUTH_UPLOAD_TARGET": "both"}) == ("cpa", "sub2api")
    assert auth_upload.parse_upload_targets({"AUTH_UPLOAD_TARGET": "none"}) == ()
    assert auth_upload.parse_upload_targets({}) == ("cpa",)


def test_auth_upload_enabled_infers_explicit_sub2api_target_with_credentials() -> None:
    env = {
        "AUTH_UPLOAD_TARGET": "sub2api",
        "SUB2API_SERVER_URL": "https://sub.example",
        "SUB2API_API_KEY": "sub-token",
    }

    assert auth_upload.auth_upload_enabled(env) is True


def test_parse_upload_targets_infers_sub2api_when_only_sub2api_credentials_exist() -> None:
    env = {
        "SUB2API_SERVER_URL": "https://sub.example",
        "SUB2API_API_KEY": "sub-token",
    }

    assert auth_upload.parse_upload_targets(env) == ("sub2api",)


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
            "SUB2API_PRIORITY": "3",
            "SUB2API_CONCURRENCY": "4",
        },
    )

    assert result.ok is True
    assert calls[0]["url"] == "https://sub.example/api/v1/admin/accounts/data"
    assert calls[0]["headers"]["x-api-key"] == "sub-token"
    assert calls[0]["headers"]["Idempotency-Key"].startswith("import-")
    account = calls[0]["json"]["data"]["accounts"][0]
    assert account["name"] == "b@example.com"
    assert account["group_ids"] == [1, 2]
    assert account["priority"] == 3
    assert account["concurrency"] == 4
    assert account["credentials"]["refresh_token"] == "rt"


def test_upload_sub2api_uses_configured_import_path_header_and_timeout(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_post(url: str, json: dict, headers: dict, timeout: int):
        calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return SimpleNamespace(status_code=200, text="ok")

    monkeypatch.setattr(auth_upload, "requests", SimpleNamespace(post=fake_post), raising=False)
    monkeypatch.setitem(__import__("sys").modules, "requests", SimpleNamespace(post=fake_post))

    result = auth_upload.upload_sub2api(
        {"email": "b@example.com", "access_token": "at", "refresh_token": "rt"},
        {
            "SUB2API_SERVER_URL": "https://sub.example",
            "SUB2API_API_KEY": "sub-token",
            "SUB2API_IMPORT_PATH": "/custom/import",
            "SUB2API_API_KEY_HEADER": "Authorization",
            "SUB2API_AUTH_SCHEME": "Bearer",
            "SUB2API_TIMEOUT": "9",
        },
    )

    assert result.ok is True
    assert calls[0]["url"] == "https://sub.example/custom/import"
    assert calls[0]["headers"]["Authorization"] == "Bearer sub-token"
    assert calls[0]["timeout"] == 9


def test_sub2api_frontend_admin_url_is_normalized_to_api_origin() -> None:
    assert auth_upload.normalize_base_url("https://sub.example/admin/accounts") == "https://sub.example"


def test_upload_sub2api_resolves_group_name_and_uses_origin_api(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_get(url: str, headers: dict, timeout: int):
        calls.append({"method": "get", "url": url, "headers": headers, "timeout": timeout})
        return SimpleNamespace(
            status_code=200,
            text="ok",
            json=lambda: {
                "code": 0,
                "data": {
                    "items": [
                        {"id": 5, "name": "openai-plus"},
                        {"id": 7, "name": "other"},
                    ]
                },
            },
        )

    def fake_post(url: str, json: dict, headers: dict, timeout: int):
        calls.append({"method": "post", "url": url, "json": json, "headers": headers, "timeout": timeout})
        return SimpleNamespace(status_code=200, text="ok")

    fake_requests = SimpleNamespace(get=fake_get, post=fake_post)
    monkeypatch.setattr(auth_upload, "requests", fake_requests, raising=False)
    monkeypatch.setitem(__import__("sys").modules, "requests", fake_requests)

    result = auth_upload.upload_sub2api(
        {"email": "b@example.com", "access_token": "at", "refresh_token": "rt"},
        {
            "SUB2API_SERVER_URL": "https://sub.example/admin/accounts",
            "SUB2API_API_KEY": "sub-token",
            "SUB2API_GROUP_IDS": "openai-plus",
        },
    )

    assert result.ok is True
    assert calls[0]["method"] == "get"
    assert calls[0]["url"].startswith("https://sub.example/api/v1/admin/groups?")
    assert calls[0]["headers"]["x-api-key"] == "sub-token"
    assert calls[1]["method"] == "post"
    assert calls[1]["url"] == "https://sub.example/api/v1/admin/accounts/data"
    assert calls[1]["json"]["data"]["accounts"][0]["group_ids"] == [5]
