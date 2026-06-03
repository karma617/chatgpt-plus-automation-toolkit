import authorization_flow


ROOT = authorization_flow.AUTH_ROOT


def test_build_auth_command_uses_longer_account_timeout(tmp_path) -> None:
    command = authorization_flow.build_auth_command(
        {"account": "user@example.com", "code_address": "user@example.com"},
        tmp_path / "account.txt",
        tmp_path / "out",
    )

    index = command.index("--account-timeout-seconds")
    assert command[index + 1] == "360"


def test_no_valid_organizations_accounts_are_not_removed(monkeypatch, tmp_path) -> None:
    del monkeypatch, tmp_path
    source = (ROOT / "authorization_flow.py").read_text(encoding="utf-8")

    assert "no_valid_organizations 账号暂不移除，保留在待授权池等待下次重跑" in source
    assert "removed_no_valid_org" not in source
    assert "auth_no_valid_organizations" not in source
