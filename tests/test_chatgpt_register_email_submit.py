from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_email_fill_uses_safe_submit_helper() -> None:
    source = (ROOT / "modules" / "chatgpt_register.py").read_text(encoding="utf-8")

    assert "click_email_submit_safe(self.page, locator)" in source
    assert "click_email_submit(self.page, locator)" not in source


def test_safe_email_submit_rejects_phone_switch_buttons() -> None:
    source = (ROOT / "modules" / "chatgpt_register.py").read_text(encoding="utf-8")
    start = source.index("async def click_email_submit_safe")
    end = source.index("async def click_continue")
    helper = source[start:end]

    assert "isPhoneSwitch" in helper
    assert "phone|mobile|sms|tel" in helper
    assert "!isPhoneSwitch(el)" in helper
    assert "form.requestSubmit()" in helper
