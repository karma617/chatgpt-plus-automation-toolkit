from __future__ import annotations

import time
from typing import Any

import requests


class PermanentCaptchaError(RuntimeError):
    def __init__(self, message: str, *, error_code: str = "") -> None:
        super().__init__(message)
        self.error_code = error_code


_PERMANENT_ERROR_CODES = frozenset(
    {
        "ERROR_DOMAIN_NOT_ALLOWED",
        "ERROR_KEY_DOES_NOT_EXIST",
        "ERROR_ZERO_BALANCE",
        "ERROR_IP_BLOCKED",
        "ERROR_IP_BLOCKED_5MIN",
        "ERROR_IP_BLOCKED_10MIN",
        "ERROR_IP_BLOCKED_60MIN",
        "ERROR_ACCOUNT_SUSPENDED",
        "ERROR_TASK_NOT_SUPPORTED",
    }
)


def _is_permanent_error_code(code: object) -> bool:
    if not code:
        return False
    value = str(code).strip().upper()
    if value in _PERMANENT_ERROR_CODES:
        return True
    return any(value.startswith(prefix) for prefix in ("ERROR_IP_BLOCKED", "ERROR_ACCOUNT_"))


def _token_from_solution(solution: dict[str, Any]) -> str:
    token = (
        solution.get("gRecaptchaResponse")
        or solution.get("token")
        or solution.get("response")
    )
    return str(token or "").strip()


class YesCaptchaSolver:
    def __init__(
        self,
        client_key: str,
        *,
        api_url: str = "https://api.yescaptcha.com",
        timeout: int = 180,
        poll_interval: int = 3,
    ) -> None:
        self.client_key = str(client_key or "").strip()
        self.api_url = api_url.rstrip("/")
        self.timeout = max(30, int(timeout or 180))
        self.poll_interval = max(1, int(poll_interval or 3))
        if not self.client_key:
            raise ValueError("YESCAPTCHA_API_KEY is required")

    def solve_recaptcha_v2(self, page_url: str, site_key: str) -> str:
        task = {
            "type": "RecaptchaV2TaskProxyless",
            "websiteURL": page_url,
            "websiteKey": site_key,
        }
        return self._solve_task(task)

    def solve_hcaptcha(self, page_url: str, site_key: str, *, enterprise: bool = True) -> str:
        task_types = ["HCaptchaEnterpriseTaskProxyless", "HCaptchaTaskProxyless"] if enterprise else ["HCaptchaTaskProxyless"]
        last_error = ""
        last_perm_code = ""
        for task_type in task_types:
            try:
                task = {
                    "type": task_type,
                    "websiteURL": page_url,
                    "websiteKey": site_key,
                }
                return self._solve_task(task)
            except PermanentCaptchaError as exc:
                last_perm_code = exc.error_code or last_perm_code
                last_error = f"[{task_type}] {exc}"
                if exc.error_code and any(
                    exc.error_code.upper().startswith(prefix)
                    for prefix in ("ERROR_IP_BLOCKED", "ERROR_ACCOUNT_", "ERROR_ZERO_BALANCE")
                ):
                    raise
            except RuntimeError as exc:
                last_error = f"[{task_type}] {exc}"
        if last_perm_code:
            raise PermanentCaptchaError(last_error, error_code=last_perm_code)
        raise RuntimeError(last_error or "YesCaptcha hCaptcha solve failed")

    def _solve_task(self, task: dict[str, Any]) -> str:
        task_id = self._create_task(task)
        solution = self._wait_task(task_id)
        token = _token_from_solution(solution)
        if not token:
            raise RuntimeError(f"YesCaptcha result missing token: {solution}")
        return token

    def _create_task(self, task: dict[str, Any]) -> str:
        body = self._post_json(
            "/createTask",
            {
                "clientKey": self.client_key,
                "task": task,
            },
        )
        if body.get("errorId", 0) != 0:
            self._raise_api_error("createTask", body)
        task_id = str(body.get("taskId") or "").strip()
        if not task_id:
            raise RuntimeError(f"YesCaptcha createTask missing taskId: {body}")
        return task_id

    def _wait_task(self, task_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            time.sleep(self.poll_interval)
            body = self._post_json(
                "/getTaskResult",
                {
                    "clientKey": self.client_key,
                    "taskId": task_id,
                },
            )
            if body.get("status") == "ready":
                solution = body.get("solution")
                if isinstance(solution, dict):
                    return solution
                raise RuntimeError(f"YesCaptcha result missing solution: {body}")
            if body.get("errorId", 0) != 0:
                self._raise_api_error("getTaskResult", body)
        raise TimeoutError(f"YesCaptcha task {task_id} timed out after {self.timeout}s")

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = requests.post(f"{self.api_url}{path}", json=payload, timeout=30)
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict):
            raise RuntimeError(f"YesCaptcha returned non-object response: {body!r}")
        return body

    @staticmethod
    def _raise_api_error(stage: str, body: dict[str, Any]) -> None:
        code = str(body.get("errorCode") or "").strip()
        desc = str(body.get("errorDescription") or "").strip()
        message = f"YesCaptcha {stage} error: errorCode={code!r} errorDescription={desc!r}"
        if _is_permanent_error_code(code):
            raise PermanentCaptchaError(message, error_code=code)
        raise RuntimeError(message)
