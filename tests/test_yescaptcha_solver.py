from __future__ import annotations

from modules.yescaptcha_solver import YesCaptchaSolver


class FakeResponse:
    def __init__(self, body: dict) -> None:
        self.body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.body


def test_yescaptcha_solves_recaptcha_v2(monkeypatch) -> None:
    calls: list[tuple[str, dict]] = []
    bodies = [
        {"errorId": 0, "taskId": "task-1"},
        {"errorId": 0, "status": "ready", "solution": {"gRecaptchaResponse": "token-1"}},
    ]

    def fake_post(url: str, json: dict, timeout: int) -> FakeResponse:
        calls.append((url, json))
        return FakeResponse(bodies.pop(0))

    monkeypatch.setattr("modules.yescaptcha_solver.requests.post", fake_post)
    monkeypatch.setattr("modules.yescaptcha_solver.time.sleep", lambda _seconds: None)

    solver = YesCaptchaSolver("key", timeout=30, poll_interval=1)

    token = solver.solve_recaptcha_v2("https://example.test", "site-key")

    assert token == "token-1"
    assert calls[0][1]["task"]["type"] == "RecaptchaV2TaskProxyless"
    assert calls[0][1]["task"]["websiteURL"] == "https://example.test"
    assert calls[0][1]["task"]["websiteKey"] == "site-key"
    assert calls[1][1]["taskId"] == "task-1"


def test_yescaptcha_hcaptcha_falls_back_to_basic_task(monkeypatch) -> None:
    task_types: list[str] = []
    bodies = [
        {"errorId": 1, "errorCode": "ERROR_DOMAIN_NOT_ALLOWED", "errorDescription": "no"},
        {"errorId": 0, "taskId": "task-2"},
        {"errorId": 0, "status": "ready", "solution": {"token": "h-token"}},
    ]

    def fake_post(url: str, json: dict, timeout: int) -> FakeResponse:
        if url.endswith("/createTask"):
            task_types.append(json["task"]["type"])
        return FakeResponse(bodies.pop(0))

    monkeypatch.setattr("modules.yescaptcha_solver.requests.post", fake_post)
    monkeypatch.setattr("modules.yescaptcha_solver.time.sleep", lambda _seconds: None)

    solver = YesCaptchaSolver("key", timeout=30, poll_interval=1)

    token = solver.solve_hcaptcha("https://paypal.test", "h-site-key", enterprise=True)

    assert token == "h-token"
    assert task_types == ["HCaptchaEnterpriseTaskProxyless", "HCaptchaTaskProxyless"]
