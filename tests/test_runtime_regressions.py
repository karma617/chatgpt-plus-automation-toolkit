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


def test_browser_session_supports_isolated_non_persistent_context() -> None:
    source = (ROOT / "modules" / "browser.py").read_text(encoding="utf-8")

    assert "isolated: bool = False" in source
    assert "chromium.launch(" in source
    assert "new_context(viewport=" in source
    assert "launch_persistent_context(" in source


def test_register_and_paypal_flows_use_isolated_browser_sessions() -> None:
    paths = [
        ROOT / "main.py",
        ROOT / "modules" / "paypal_register.py",
        ROOT / "modules" / "paypal_pay.py",
        ROOT / "modules" / "free_register.py",
    ]
    source = "\n".join(path.read_text(encoding="utf-8") for path in paths)

    assert source.count("isolated=True") >= 5


def test_isolated_flows_do_not_delete_persistent_profiles() -> None:
    paypal_source = (ROOT / "modules" / "paypal_register.py").read_text(encoding="utf-8")
    free_source = (ROOT / "modules" / "free_register.py").read_text(encoding="utf-8")

    assert 'if not session_kwargs.get("isolated"):' in paypal_source
    assert "if not success and not keep_profile and not session.isolated:" in free_source
