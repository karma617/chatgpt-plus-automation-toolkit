from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_email_fill_uses_safe_submit_helper() -> None:
    source = (ROOT / "modules" / "chatgpt_register.py").read_text(encoding="utf-8")

    assert "click_email_submit_safe(self.page, locator)" in source
    assert "click_email_submit(self.page, locator)" not in source
    assert "await self.wait_email_submit_progress()" in source
    assert "await self.wait_email_page_ready()" in source
    assert "等待页面完全加载后填入邮箱" in source
    assert "页面已加载，填入邮箱" in source
    assert "async def wait_email_page_ready" in source
    assert "timeout_ms: int = 30_000" in source
    assert "禁止提前输入邮箱" in source
    assert "inputReady" in source
    assert "email_ready_since" in source
    assert "继续按钮待输入后出现" in source
    assert "for _ in range(20)" in source
    assert "async def wait_email_submit_progress" in source
    assert "wait_for_load_state(\"networkidle\"" in source
    assert "document.readyState === 'complete'" in source
    assert "email_submit_attempts" in source
    assert "auth/login?email=" in source
    assert "普通 GET 回退" in source
    assert "尝试 Enter 二次推进" not in source


def test_safe_email_submit_rejects_phone_switch_buttons() -> None:
    source = (ROOT / "modules" / "chatgpt_register.py").read_text(encoding="utf-8")
    start = source.index("async def click_email_submit_safe")
    end = source.index("async def click_code_submit")
    helper = source[start:end]

    assert "isPhoneSwitch" in helper
    assert "isCurrentFormButton" in helper
    assert "isSwitchButton" in helper
    assert "continue with email( address)?" in helper
    assert "submit_result.get('mode'" in source
    assert "document.body" in helper
    assert "score(a) - score(b)" in helper
    assert "phone|mobile|sms|tel" in helper
    assert "!isPhoneSwitch(el)" in helper
    assert "role-click" in helper
    assert "form-request-submit" not in helper


def test_code_submit_uses_nearby_anchor_only() -> None:
    source = (ROOT / "modules" / "chatgpt_register.py").read_text(encoding="utf-8")
    fill_code = source[source.index("async def fill_code") : source.index("async def fill_profile")]
    click_continue = source[source.index("async def click_continue") : source.index("async def click_by_visible_text")]

    assert "submit_anchor: Locator | None = None" in fill_code
    assert "submit_anchor = code_inputs[0]" in fill_code
    assert "await self.page.keyboard.type(code[:6], delay=70)" in fill_code
    assert "typed_values" in fill_code
    assert "await click_code_submit(self.page, submit_anchor)" in fill_code
    assert "验证码页: 已触发提交" in fill_code
    assert "near-anchor-js" in click_continue
    assert "await anchor.press(\"Enter\")" in click_continue


def test_code_submit_rejects_resend_buttons_and_saves_debug() -> None:
    source = (ROOT / "modules" / "chatgpt_register.py").read_text(encoding="utf-8")
    click_code_submit = source[source.index("async def click_code_submit") : source.index("async def click_continue")]

    assert "async def dump_code_submit_debug" in source
    assert "auth_code_failure" in source
    assert "same code repeated without redirect" in source
    assert "resend|send again|send new|new code" in click_code_submit
    assert "code-submit-click" in click_code_submit
    assert "form-request-submit" in click_code_submit
