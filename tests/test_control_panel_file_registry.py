from pathlib import Path

from control_panel.file_registry import PanelFile, get_panel_files


def _u(text: str) -> str:
    return text.encode("ascii").decode("unicode_escape")


def test_get_panel_files_resolves_known_paths(tmp_path: Path) -> None:
    files = get_panel_files(tmp_path)

    expected = {
        "paypal_card_codes": "data/paypal/card_codes.txt",
        "paypal_cards": "data/paypal/cards.txt",
        "paypal_phones": "data/paypal/phones.txt",
        "proxy_default": "data/proxies/proxies.txt",
        "proxy_jp": "data/proxies/proxies_jp.txt",
        "proxy_us": "data/proxies/proxies_us.txt",
        "hotmail_accounts": "data/hotmail/accounts.txt",
        "register_only_sessions": "output/register_only/registered_sessions.txt",
        "register_only_used": "output/register_only/used_emails.txt",
        "paypal_links": "output/paypal注册/长链接账号/account.txt",
        "paypal_pending_auth": "output/paypal注册/待授权账号/account.txt",
    }

    for key, relative in expected.items():
        assert key in files
        assert isinstance(files[key], PanelFile)
        assert files[key].path == tmp_path / Path(relative)


def test_panel_file_keys_match_instances(tmp_path: Path) -> None:
    files = get_panel_files(tmp_path)

    for key, panel_file in files.items():
        assert panel_file.key == key
        assert panel_file.label
        assert panel_file.kind in {"txt", "env"}


def test_register_only_files_have_stable_keys_paths_and_labels(tmp_path: Path) -> None:
    files = get_panel_files(tmp_path)

    sessions = files["register_only_sessions"]
    used = files["register_only_used"]

    assert sessions.path == tmp_path / "output" / "register_only" / "registered_sessions.txt"
    assert used.path == tmp_path / "output" / "register_only" / "used_emails.txt"
    assert sessions.label == _u(r"\u4ec5\u6ce8\u518c Session \u6e05\u5355")
    assert used.label == _u(r"\u4ec5\u6ce8\u518c\u5df2\u7528\u90ae\u7bb1")
