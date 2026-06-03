from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_playwright_keyboard_press_is_not_called_with_timeout() -> None:
    source = (ROOT / "modules" / "free_browser_flow.py").read_text(encoding="utf-8")

    assert 'keyboard.press("Enter", timeout=' not in source


def test_log_output_does_not_force_gbk_roundtrip() -> None:
    source = (ROOT / "modules" / "utils.py").read_text(encoding="utf-8")

    assert ".encode(\"gbk\"" not in source
    assert ".decode(\"gbk\"" not in source


def test_free_browser_flow_waits_for_turnstile_before_submit_paths() -> None:
    source = (ROOT / "modules" / "free_browser_flow.py").read_text(encoding="utf-8")

    assert "solve_turnstile_if_present" in source
    assert "wait_before_cloudflare_submit" in source
    assert 'reason="email-submit"' in source
    assert 'reason="submit"' in source


def test_chatgpt_register_unknown_page_retry_waits_five_seconds() -> None:
    source = (ROOT / "modules" / "chatgpt_register.py").read_text(encoding="utf-8")

    assert "UNKNOWN_PAGE_RETRY_WAIT_MS = 5000" in source
    assert "self.page.wait_for_timeout(UNKNOWN_PAGE_RETRY_WAIT_MS)" in source
