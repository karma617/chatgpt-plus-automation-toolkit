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

