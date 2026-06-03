import modules.hero_sms_provider as hero_sms_provider
from modules.hero_sms_provider import HeroSMSProvider


def _clear_cancel_retry_queue() -> None:
    with hero_sms_provider._HERO_SMS_CANCEL_CONDITION:
        hero_sms_provider._HERO_SMS_CANCEL_QUEUE.clear()
        hero_sms_provider._HERO_SMS_CANCEL_CONDITION.notify_all()


def test_cancel_failure_is_queued_for_background_retry(monkeypatch) -> None:
    _clear_cancel_retry_queue()

    calls = []

    def fake_request(self, action, **params):
        calls.append((action, params))
        raise RuntimeError("409 Conflict")

    monkeypatch.setattr(HeroSMSProvider, "request", fake_request)

    HeroSMSProvider("key", base_url="https://hero-sms.test/api", timeout=7).cancel(449100636)

    key = ("key", "https://hero-sms.test/api", 449100636)
    with hero_sms_provider._HERO_SMS_CANCEL_CONDITION:
        assert key in hero_sms_provider._HERO_SMS_CANCEL_QUEUE
        queued = hero_sms_provider._HERO_SMS_CANCEL_QUEUE[key]
    assert queued.timeout == 7
    assert calls == [("setStatus", {"id": 449100636, "status": 8})]
    _clear_cancel_retry_queue()


def test_background_retry_removes_activation_after_success(monkeypatch) -> None:
    _clear_cancel_retry_queue()

    calls = []

    hero_sms_provider._enqueue_hero_sms_cancel_retry(
        api_key="key",
        base_url="https://hero-sms.test/api",
        timeout=7,
        activation_id=449100636,
        reason="409 Conflict",
    )
    with hero_sms_provider._HERO_SMS_CANCEL_CONDITION:
        hero_sms_provider._HERO_SMS_CANCEL_QUEUE[("key", "https://hero-sms.test/api", 449100636)].next_at = 0

    def fake_request(self, action, **params):
        calls.append((self.api_key, self.base_url, self.timeout, action, params))
        return "OK"

    monkeypatch.setattr(HeroSMSProvider, "request", fake_request)

    assert hero_sms_provider._retry_pending_hero_sms_cancel_once(now=1) == 1
    with hero_sms_provider._HERO_SMS_CANCEL_CONDITION:
        assert ("key", "https://hero-sms.test/api", 449100636) not in hero_sms_provider._HERO_SMS_CANCEL_QUEUE
    assert calls == [("key", "https://hero-sms.test/api", 7, "setStatus", {"id": 449100636, "status": 8})]
    _clear_cancel_retry_queue()


def test_background_retry_failure_keeps_activation_queued(monkeypatch) -> None:
    _clear_cancel_retry_queue()

    hero_sms_provider._enqueue_hero_sms_cancel_retry(
        api_key="key",
        base_url="https://hero-sms.test/api",
        timeout=7,
        activation_id=449285982,
        reason="409 Conflict",
    )
    key = ("key", "https://hero-sms.test/api", 449285982)
    with hero_sms_provider._HERO_SMS_CANCEL_CONDITION:
        queued = hero_sms_provider._HERO_SMS_CANCEL_QUEUE[key]
        queued.next_at = 0
        original_sequence = queued.sequence

    def fake_request(self, action, **params):
        raise RuntimeError("409 Conflict")

    monkeypatch.setattr(HeroSMSProvider, "request", fake_request)

    assert hero_sms_provider._retry_pending_hero_sms_cancel_once(now=1) == 1
    with hero_sms_provider._HERO_SMS_CANCEL_CONDITION:
        queued = hero_sms_provider._HERO_SMS_CANCEL_QUEUE[key]
        assert queued.attempts == 1
        assert queued.sequence == original_sequence
        assert queued.next_at > 1
    _clear_cancel_retry_queue()


def test_background_retry_processes_due_activations_fifo(monkeypatch) -> None:
    _clear_cancel_retry_queue()

    for activation_id in (1, 2, 3):
        hero_sms_provider._enqueue_hero_sms_cancel_retry(
            api_key="key",
            base_url="https://hero-sms.test/api",
            timeout=7,
            activation_id=activation_id,
            reason="409 Conflict",
        )
    with hero_sms_provider._HERO_SMS_CANCEL_CONDITION:
        for item in hero_sms_provider._HERO_SMS_CANCEL_QUEUE.values():
            item.next_at = 0

    calls = []

    def fake_request(self, action, **params):
        calls.append(params["id"])
        return "OK"

    monkeypatch.setattr(HeroSMSProvider, "request", fake_request)

    assert hero_sms_provider._retry_pending_hero_sms_cancel_once(now=1) == 3
    assert calls == [1, 2, 3]
    _clear_cancel_retry_queue()
