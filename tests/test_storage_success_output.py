from modules.storage import AccountStore


def _store(tmp_path):
    return AccountStore(
        accounts_file=str(tmp_path / "accounts.txt"),
        raw_pool_file=str(tmp_path / "raw.txt"),
        success_file=str(tmp_path / "success.txt"),
        failed_file=str(tmp_path / "failed.txt"),
        in_progress_file=str(tmp_path / "in_progress.txt"),
    )


def test_save_success_can_write_full_mail_account_line_for_register_only(tmp_path) -> None:
    store = _store(tmp_path)
    full_line = "user@hotmail.com----password----client-id----refresh-token"

    store.save_success("user@hotmail.com", "user@hotmail.com", "session-cache.jsonl", account_line=full_line)

    assert (tmp_path / "success.txt").read_text(encoding="utf-8") == full_line + "\n"


def test_save_success_keeps_legacy_three_part_output_without_account_line(tmp_path) -> None:
    store = _store(tmp_path)

    store.save_success("user@example.com", "code-address", "payment-link")

    assert (tmp_path / "success.txt").read_text(encoding="utf-8") == "user@example.com----code-address----payment-link\n"
