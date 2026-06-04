import get_oauth_rt
from modules.auth_upload import UploadResult


def test_upload_bundle_to_server_returns_none_when_upload_disabled(monkeypatch) -> None:
    monkeypatch.setattr(get_oauth_rt, "load_root_env", lambda: {"AUTH_UPLOAD_TARGET": "none"})

    assert get_oauth_rt.upload_bundle_to_server({"email": "user@example.com"}) is None


def test_upload_bundle_to_server_returns_false_when_enabled_upload_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        get_oauth_rt,
        "load_root_env",
        lambda: {
            "AUTH_SERVER_UPLOAD": "true",
            "SUB2API_SERVER_URL": "https://sub.example",
            "SUB2API_API_KEY": "sub-key",
            "AUTH_UPLOAD_TARGET": "sub2api",
        },
    )
    monkeypatch.setattr(
        get_oauth_rt,
        "upload_bundle",
        lambda payload, env, account_type="": [
            UploadResult("sub2api", ok=False, error="network down")
        ],
    )

    assert get_oauth_rt.upload_bundle_to_server({"email": "user@example.com"}) is False
