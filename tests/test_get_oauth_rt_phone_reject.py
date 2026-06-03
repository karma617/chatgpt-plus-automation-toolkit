import get_oauth_rt


class _FakeLocator:
    def __init__(self, text: str = "", visible: bool = False) -> None:
        self._text = text
        self._visible = visible

    @property
    def first(self):
        return self

    def is_visible(self, timeout=0):
        del timeout
        return self._visible

    def inner_text(self, timeout=0):
        del timeout
        return self._text

    def click(self, timeout=0):
        del timeout
        return None


class _FakeFrame:
    def __init__(self, text: str = "") -> None:
        self._text = text

    def locator(self, selector: str):
        if selector == "body":
            return _FakeLocator(self._text)
        return _FakeLocator()


class _FakePage:
    def __init__(self, text: str = "", frames=None) -> None:
        self._text = text
        self.frames = frames or []

    def locator(self, selector: str):
        if selector == "body":
            return _FakeLocator(self._text)
        return _FakeLocator()


def test_whatsapp_fallback_is_phone_rejected() -> None:
    page = _FakePage(
        "We couldn't send a text message to this phone number, so we switched to WhatsApp. "
        "Continue to send a verification code on WhatsApp."
    )

    assert get_oauth_rt.is_phone_number_rejected_page(page) is True


def test_whatsapp_fallback_detected_in_frame() -> None:
    page = _FakePage("", frames=[_FakeFrame("Continue to send a verification code on WhatsApp.")])

    assert get_oauth_rt.is_phone_number_rejected_page(page) is True


def test_voip_reject_is_detected_as_dedicated_phone_reject() -> None:
    page = _FakePage(
        get_oauth_rt._u(
            r"\u8fd9\u4f3c\u4e4e\u662f\u4e2a\u865a\u62df\u53f7\u7801\uff08\u4e5f\u79f0\u4e3a VoIP\uff09\u3002"
            r"\u8bf7\u63d0\u4f9b\u6709\u6548\u7684\u975e\u865a\u62df\u7535\u8bdd\u53f7\u7801\u4ee5\u7ee7\u7eed"
        )
    )

    assert get_oauth_rt.is_phone_voip_rejected_page(page) is True
    assert get_oauth_rt.is_phone_number_rejected_page(page) is True


def test_raise_phone_rejection_prefers_voip_error() -> None:
    page = _FakePage("This appears to be a virtual number. Provide a valid non-virtual phone number to continue.")

    try:
        get_oauth_rt.raise_phone_rejection_if_needed(page)
    except RuntimeError as exc:
        assert "PHONE_VOIP_REJECTED" in str(exc)
    else:
        raise AssertionError("expected PHONE_VOIP_REJECTED")
