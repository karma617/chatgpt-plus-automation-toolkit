import re
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


def test_browser_session_forces_incognito_non_persistent_context() -> None:
    source = (ROOT / "modules" / "browser.py").read_text(encoding="utf-8")

    assert "isolated: bool = True" in source
    assert "self.isolated = True" in source
    assert "chromium.launch(" in source
    assert "new_context(**self._context_options())" in source
    assert "launch_persistent_context(" not in source
    assert "add_init_script(script=script)" in source


def test_paypal_auto_filler_uses_incognito_context() -> None:
    source = (ROOT / "modules" / "paypal_auto_filler.py").read_text(encoding="utf-8")

    assert "args.incognito = True" in source
    assert "p.chromium.launch(**launch_kwargs)" in source
    assert "browser.new_context(**ctx_kwargs)" in source
    assert "launch_persistent_context(" not in source


def test_browser_session_uses_random_legal_fingerprint_surface() -> None:
    source = (ROOT / "modules" / "browser.py").read_text(encoding="utf-8")

    assert '_BROWSER_FINGERPRINT_ENABLED_ENV = "BROWSER_FINGERPRINT_ENABLED"' in source
    assert "_browser_fingerprint_enabled()" in source
    assert "_is_truthy(os.environ.get(_BROWSER_FINGERPRINT_ENABLED_ENV), False)" in source
    assert "if self.fingerprint:" in source
    assert "_build_fingerprint" in source
    assert "BROWSER_RANDOM_FINGERPRINT" in source
    assert "_ALLOWED_BROWSER_LOCALES" in source
    assert "_accept_language_header" in source
    assert "chrome_full_version" in source
    assert "client_hints" in source
    assert "timezone_id" in source
    assert "Accept-Language" in source
    assert "--lang=" in source
    assert "device_scale_factor" in source
    assert "hardwareConcurrency" in source
    assert "navigator.userAgentData.getHighEntropyValues" in source
    assert "webgl_renderer" in source
    assert "canvas_noise_seed" in source
    assert "audio_noise_seed" in source
    assert "webkitAudioContext" in source


def test_business_flows_keep_stable_fingerprint_inputs_for_explicit_enable() -> None:
    paths = [
        ROOT / "main.py",
        ROOT / "modules" / "paypal_register.py",
        ROOT / "modules" / "paypal_pay.py",
        ROOT / "modules" / "free_register.py",
    ]
    source = "\n".join(path.read_text(encoding="utf-8") for path in paths)

    assert "account_id=account.email" in source
    assert "account_id=email" in source
    assert "log_prefix=prefix" in source
    for line in re.findall(r"fingerprint_seed=.*", source):
        assert "time.time_ns()" not in line


def test_chatgpt_register_turnstile_backoff_is_low_frequency() -> None:
    source = (ROOT / "modules" / "chatgpt_register.py").read_text(encoding="utf-8")

    assert "click_count < 2" in source
    assert "now - last_click >= 12" in source
    assert "rotate proxy/session" in source
    assert "clickableHint" in source
    assert "successVisible" in source
    assert "managed challenge has no visible widget/iframe" in source
    assert '"body"' not in source[source.index("async def click_cloudflare_turnstile_widget") :]


def test_turnstile_click_does_not_target_hidden_response_input() -> None:
    chatgpt_source = (ROOT / "modules" / "chatgpt_register.py").read_text(encoding="utf-8")
    free_source = (ROOT / "modules" / "free_browser_flow.py").read_text(encoding="utf-8")

    assert "input[name=\"cf-turnstile-response\"]" in chatgpt_source
    assert "'input[name=\"cf-turnstile-response\"]'," not in chatgpt_source[
        chatgpt_source.index("async def click_cloudflare_turnstile_widget") :
    ]
    assert "node.getAttribute('type') === 'hidden'" in chatgpt_source
    assert "body" not in free_source[free_source.index("selectors = [") : free_source.index("if clicked:")]
    assert "node.getAttribute('type') === 'hidden'" in free_source
    assert "\\\\u30bb\\\\u30ad\\\\u30e5\\\\u30ea\\\\u30c6\\\\u30a3\\\\u691c\\\\u8a3c" in chatgpt_source


def test_chatgpt_register_runtime_wires_flaresolverr_fallback() -> None:
    source = (ROOT / "modules" / "chatgpt_register.py").read_text(encoding="utf-8")
    client_source = (ROOT / "modules" / "flaresolverr_client.py").read_text(encoding="utf-8")
    service_source = (ROOT / "modules" / "flaresolverr_service.py").read_text(encoding="utf-8")
    runner_source = (ROOT / "panel_runner.py").read_text(encoding="utf-8")

    assert "from .flaresolverr_client import" in source
    assert "await self.try_flaresolverr_challenge()" in source
    assert "solve_with_flaresolverr" in source
    assert "proxy=self.proxy" in source
    assert "try FlareSolverr or rotate proxy/session" in source
    assert "page.context.add_cookies(result.cookies)" in client_source
    assert "add_init_script(" in client_source
    assert "tools/flaresolverr/FlareSolverr.exe" in service_source
    assert "ghcr.io/flaresolverr/flaresolverr:latest" in service_source
    assert "ensure_flaresolverr_service(env_values" in runner_source


def test_chatgpt_register_profile_fill_uses_stable_japanese_dom_fields() -> None:
    source = (ROOT / "modules" / "chatgpt_register.py").read_text(encoding="utf-8")
    fill_profile = source[source.index("async def fill_profile(self)") : source.index("async def handle_phone_required")]
    detect_state = source[source.index("async def detect_state") : source.index("async def click_entry")]

    assert "async def page_looks_like_profile_page" in source
    assert "async def fill_profile_stable_fields" in source
    assert 'input[name="name"], input[autocomplete="name"]' in source
    assert 'input[name="age"], input[inputmode="numeric"], input[type="number"]' in source
    assert 'input[name="birthday"][type="hidden"]' in source
    assert "fields.length >= 2" in source
    assert 'if any(key in low for key in ["tell us about yourself"' not in detect_state
    assert fill_profile.index("fill_profile_stable_fields") < fill_profile.index("fill_profile_by_js")


def test_phone_register_mode_never_falls_back_to_email_fill() -> None:
    source = (ROOT / "modules" / "chatgpt_register.py").read_text(encoding="utf-8")
    email_branch = source[source.index('if state == "email":') : source.index('if state == "password":')]
    phone_gate = email_branch[email_branch.index("if self.is_phone_signup_mode()") : email_branch.index("self.log(f")]
    phone_switch = source[source.index("async def click_phone_switch") : source.index("async def fill_email")]

    assert "await self.force_phone_login_entry()" in phone_gate
    assert "continue" in phone_gate
    assert "await self.fill_email(account.email)" not in phone_gate
    assert "PHONE_ENTRY_ACTIONS" in source
    assert "async def force_phone_login_entry" in source
    assert r"\u96fb\u8a71\u756a\u53f7\u3067\u7d9a\u884c" in phone_switch
    assert r"\u643a\u5e2f\u96fb\u8a71\u3067\u7d9a\u884c" in phone_switch
    assert r"\u96fb\u8a71\u756a\u53f7|\u643a\u5e2f\u96fb\u8a71|\u643a\u5e2f" in phone_switch


def test_phone_login_detection_requires_dom_evidence_not_url_only() -> None:
    source = (ROOT / "modules" / "chatgpt_register.py").read_text(encoding="utf-8")
    helper = source[source.index("async def is_phone_login_page") : source.index("async def visible_input_count")]

    assert "phone_url =" in helper
    assert "phone_inputs = await visible_input_count" in helper
    assert "username_inputs = await visible_input_count" in helper
    assert 'if "usernamekind=phone_number" in url or "screen_hint=phone" in url:' not in helper


def test_login_state_machine_handles_account_picker_and_signin_problem_pages() -> None:
    source = (ROOT / "modules" / "chatgpt_register.py").read_text(encoding="utf-8")
    run_loop = source[source.index("async def run_until_logged_in") : source.index("async def refresh_page")]

    assert 'if state == "account_picker":' in run_loop
    assert "await self.handle_account_picker(account)" in run_loop
    assert 'if state == "signin_problem":' in run_loop
    assert "await self.handle_signin_problem()" in run_loop
    assert "SIGNIN_PROBLEM_RETRY_CURRENT_FLOW" in source


def test_login_state_machine_treats_offer_modal_as_logged_in() -> None:
    source = (ROOT / "modules" / "chatgpt_register.py").read_text(encoding="utf-8")
    detect_state = source[source.index("async def detect_state") : source.index("async def click_entry")]
    wait_after_code = source[source.index("async def wait_after_code_submit") : source.index("async def fill_password")]
    fill_code = source[source.index("async def fill_code") : source.index("async def fill_profile")]
    markers = source[source.index("async def chatgpt_offer_or_payment_markers") : source.index("async def is_phone_login_page")]

    assert "async def chatgpt_offer_or_payment_markers" in source
    assert "async def wait_after_code_submit" in source
    assert "await self.wait_after_code_submit()" in source
    assert "code_submit_attempts" in source
    assert "提交多次仍未跳转" in source
    assert "#modal-account-payment" in markers
    assert "Free offer" in markers
    assert "Claim offer" in markers
    assert "await chatgpt_offer_or_payment_markers(page)" in markers
    assert "or await chatgpt_logged_in_markers(self.page, low, text)" in detect_state
    assert "visible_code_inputs(self.page) > 0" not in wait_after_code
    assert "await chatgpt_logged_in_markers(self.page, text.lower(), text)" in fill_code
    assert "未找到验证码输入框" in fill_code


def test_click_continue_fallback_never_uses_random_first_button() -> None:
    source = (ROOT / "modules" / "chatgpt_register.py").read_text(encoding="utf-8")
    helper = source[source.index("async def click_continue") : source.index("async def click_by_visible_text")]

    assert "safe_candidates = []" in helper
    assert "!!el.closest('form')" in helper
    assert "candidates = safe_candidates" in helper
    assert "await candidates[0].click()" in helper


def test_paypal_offer_entry_supports_current_chatgpt_offer_labels() -> None:
    source = (ROOT / "modules" / "paypal_pay.py").read_text(encoding="utf-8")
    offer = source[source.index("async def _click_visible_offer_entry") : source.index("async def _prepare_checkout_from_chatgpt_offer")]
    dismiss = source[source.index("async def _dismiss_chatgpt_interstitials") : source.index("async def _click_zero_trial_plus_option")]

    assert 'button:has-text("Free offer")' in offer
    assert 'button:has-text("Claim offer")' in offer
    assert '/free\\\\s*offer/i' in offer
    assert '/claim\\\\s*offer/i' in offer
    assert "const bad =" in offer
    assert "#modal-account-payment" in dismiss


def test_account_picker_and_signin_problem_click_helpers_are_wired() -> None:
    source = (ROOT / "modules" / "chatgpt_register.py").read_text(encoding="utf-8")
    account_picker = source[source.index("async def handle_account_picker") : source.index("async def handle_signin_problem")]
    signin_problem = source[source.index("async def handle_signin_problem") : source.index("async def try_cloudflare_turnstile_challenge")]

    assert "async def is_account_picker_page" in source
    assert "async def click_account_picker_session" in source
    assert "button[name=\"session_id\"]" in source
    assert "Select existing session" in source
    assert "await click_account_picker_session" in account_picker
    assert "await click_account_picker_link" in account_picker
    assert "def is_signin_problem_page" in source
    assert "async def click_signin_problem_action" in source
    assert "await click_signin_problem_action" in signin_problem
    assert "problem page button no response" in signin_problem


def test_signin_problem_retry_marker_is_handled_by_outer_flows() -> None:
    chatgpt_source = (ROOT / "modules" / "chatgpt_register.py").read_text(encoding="utf-8")
    paypal_register_source = (ROOT / "modules" / "paypal_register.py").read_text(encoding="utf-8")
    paypal_pay_source = (ROOT / "modules" / "paypal_pay.py").read_text(encoding="utf-8")

    assert 'SIGNIN_PROBLEM_RETRY_CURRENT_FLOW = "SIGNIN_PROBLEM_RETRY_CURRENT_FLOW"' in chatgpt_source
    assert "def is_signin_problem_retry_reason" in chatgpt_source
    assert "startswith(SIGNIN_PROBLEM_RETRY_CURRENT_FLOW)" in chatgpt_source
    assert 'last_error["kind"] = "retry_current_flow"' in paypal_register_source
    assert "SIGNIN_PROBLEM_RETRY_CURRENT_FLOW_THRESHOLD = 3" in paypal_register_source
    assert 'last_error.get("kind") == "retry_current_flow"' in paypal_register_source
    assert "proxies.insert(proxy_attempt, proxy)" in paypal_register_source
    assert "signin problem page retry current flow" in paypal_register_source
    assert "from .chatgpt_register import ChatGPTRegister, is_signin_problem_retry_reason" in paypal_pay_source
    assert "if is_signin_problem_retry_reason(reason):" in paypal_pay_source


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
