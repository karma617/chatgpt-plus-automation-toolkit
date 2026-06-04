import asyncio
from types import SimpleNamespace

import main
import pytest
from modules import paypal_flow_state
from modules import register_tool_bridge as bridge


class FakeProxyPool:
    def count(self) -> int:
        return 1

    def pick(self, worker_id: int) -> str:
        return f"http://proxy-{worker_id}"


def test_pick_task_proxy_prefers_random_sequence() -> None:
    class RandomProxyPool:
        def random_sequence(self):
            return ["http://random-proxy", "http://other-proxy"]

        def pick(self, worker_id: int):
            raise AssertionError("pick should not be used when random_sequence has values")

    assert main.pick_task_proxy(RandomProxyPool(), "http://fallback", seed=1) == "http://random-proxy"


class FakeStore:
    def __init__(self) -> None:
        self.blocked_emails: set[str] = set()

    def pending_count(self) -> int:
        return 1


class RunAccountStore:
    def __init__(self, account) -> None:
        self.account = account
        self.saved_success = []
        self.completed = []
        self.returned = []
        self.failed = []

    def claim_next(self, worker_id=1):
        return self.account

    def save_success(self, *args, **kwargs):
        self.saved_success.append((args, kwargs))

    def complete(self, email):
        self.completed.append(email)

    def return_to_pool(self, account):
        self.returned.append(account.email)

    def save_failed(self, email, reason):
        self.failed.append((email, reason))


class FakeRunAccountPage:
    url = "https://chatgpt.com/"

    async def goto(self, *args, **kwargs):
        self.url = args[0]


class FakeRunAccountSession:
    def __init__(self, *args, **kwargs) -> None:
        self.page = FakeRunAccountPage()
        self.isolated = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def current_page(self):
        return self.page


class FakeChatGPTRegister:
    def __init__(self, *args, **kwargs) -> None:
        pass

    async def run_until_logged_in(self, account, since) -> None:
        return None


def test_run_register_only_many_uses_register_only_outputs_and_disables_payment_link(monkeypatch, tmp_path) -> None:
    output_dir = tmp_path / "output" / "register_only"
    session_dir = output_dir / "sessiond"
    session_cache = session_dir / "session_cache.jsonl"
    summary_file = output_dir / "registered_sessions.txt"
    used_file = output_dir / "used_emails.txt"
    in_progress_file = output_dir / "in_progress.txt"
    failed_file = output_dir / "failed_accounts.txt"
    sms_selection = {"provider_label": "fake-sms"}
    created_store = SimpleNamespace(name="store")
    captured = {}

    async def fake_ensure_register_accounts(cfg, store, desired_count: int) -> int:
        captured["ensure_cfg"] = cfg
        captured["ensure_store"] = store
        captured["ensure_count"] = desired_count
        return desired_count

    async def fake_run_account(cfg, store, worker_id: int, **kwargs):
        captured["run_cfg"] = cfg
        captured["run_store"] = store
        captured["worker_id"] = worker_id
        captured["kwargs"] = kwargs
        summary_file.write_text("user@example.com----session\n", encoding="utf-8")
        return True

    def fake_create_store(cfg):
        captured["store_cfg"] = cfg
        return created_store

    monkeypatch.setattr(bridge, "REGISTER_ONLY_OUTPUT_DIR", output_dir)
    monkeypatch.setattr(bridge, "REGISTER_ONLY_SESSION_DIR", session_dir)
    monkeypatch.setattr(bridge, "REGISTER_ONLY_SESSION_CACHE_FILE", session_cache)
    monkeypatch.setattr(bridge, "REGISTER_ONLY_SUMMARY_FILE", summary_file)
    monkeypatch.setattr(bridge, "REGISTER_ONLY_USED_FILE", used_file)
    monkeypatch.setattr(bridge, "REGISTER_ONLY_IN_PROGRESS_FILE", in_progress_file)
    monkeypatch.setattr(bridge, "REGISTER_ONLY_FAILED_FILE", failed_file)
    monkeypatch.setattr(bridge, "register_only_mode", lambda env=None: "email")
    monkeypatch.setattr(main, "create_store", fake_create_store)
    monkeypatch.setattr(main, "ensure_register_accounts", fake_ensure_register_accounts)
    monkeypatch.setattr(main, "create_proxy_pool", lambda cfg: FakeProxyPool())
    monkeypatch.setattr(main, "resolve_flow1_sms_selection", lambda: sms_selection)
    monkeypatch.setattr(main, "run_account", fake_run_account)
    monkeypatch.setattr(main, "log", lambda message: None)
    monkeypatch.setattr(main, "worker_log", lambda worker_id, message: None)

    result = asyncio.run(
        bridge.run_register_only_many(
            {
                "mail": {"source": "hotmail"},
                "output": {"success_file": "old-success.txt", "failed_file": "old-failed.txt"},
            },
            count=1,
            workers=1,
        )
    )

    assert result.returncode == 0
    assert result.success_count == 1
    assert result.target_count == 1
    assert result.output_dir == session_dir
    assert result.summary_file == summary_file
    assert captured["store_cfg"]["output"]["success_file"] == str(summary_file)
    assert captured["store_cfg"]["output"]["failed_file"] == str(failed_file)
    assert captured["store_cfg"]["output"]["in_progress_file"] == str(in_progress_file)
    assert captured["ensure_store"] is created_store
    assert captured["ensure_count"] == 1
    assert captured["run_store"] is created_store
    assert captured["worker_id"] == 1
    assert captured["kwargs"]["proxy"] == "http://proxy-1"
    assert captured["kwargs"]["sms_selection"] is None
    assert captured["kwargs"]["create_payment_link"] is False
    assert captured["kwargs"]["session_cache_path"] == session_cache
    assert captured["kwargs"]["session_source"] == "register_only_gui"
    assert session_cache.exists()
    assert in_progress_file.exists()
    assert failed_file.exists()
    assert used_file.read_text(encoding="utf-8") == "user@example.com\n"


def test_register_only_mode_defaults_to_email() -> None:
    assert bridge.register_only_mode({}) == "email"
    assert bridge.register_only_mode({"REGISTER_ONLY_MODE": "phone"}) == "phone"
    assert bridge.register_only_mode({"REGISTER_ONLY_MODE": "\u624b\u673a\u53f7\u6ce8\u518c"}) == "phone"
    assert bridge.register_only_mode({"REGISTER_ONLY_MODE": "bad-value"}) == "email"


def test_run_register_only_many_uses_sms_only_in_phone_mode(monkeypatch, tmp_path) -> None:
    output_dir = tmp_path / "output" / "register_only"
    session_dir = output_dir / "sessiond"
    session_cache = session_dir / "session_cache.jsonl"
    summary_file = output_dir / "registered_sessions.txt"
    used_file = output_dir / "used_emails.txt"
    in_progress_file = output_dir / "in_progress.txt"
    failed_file = output_dir / "failed_accounts.txt"
    sms_selection = {"provider_label": "fake-sms"}
    created_store = SimpleNamespace(name="store")
    captured = {}

    async def fake_ensure_register_accounts(cfg, store, desired_count: int) -> int:
        return desired_count

    async def fake_run_account(cfg, store, worker_id: int, **kwargs):
        captured["kwargs"] = kwargs
        summary_file.write_text("phone@example.com----session\n", encoding="utf-8")
        return True

    monkeypatch.setattr(bridge, "REGISTER_ONLY_OUTPUT_DIR", output_dir)
    monkeypatch.setattr(bridge, "REGISTER_ONLY_SESSION_DIR", session_dir)
    monkeypatch.setattr(bridge, "REGISTER_ONLY_SESSION_CACHE_FILE", session_cache)
    monkeypatch.setattr(bridge, "REGISTER_ONLY_SUMMARY_FILE", summary_file)
    monkeypatch.setattr(bridge, "REGISTER_ONLY_USED_FILE", used_file)
    monkeypatch.setattr(bridge, "REGISTER_ONLY_IN_PROGRESS_FILE", in_progress_file)
    monkeypatch.setattr(bridge, "REGISTER_ONLY_FAILED_FILE", failed_file)
    monkeypatch.setattr(bridge, "register_only_mode", lambda env=None: "phone")
    monkeypatch.setattr(main, "create_store", lambda cfg: created_store)
    monkeypatch.setattr(main, "ensure_register_accounts", fake_ensure_register_accounts)
    monkeypatch.setattr(main, "create_proxy_pool", lambda cfg: None)
    monkeypatch.setattr(main, "resolve_flow1_sms_selection", lambda: sms_selection)
    monkeypatch.setattr(main, "run_account", fake_run_account)
    monkeypatch.setattr(main, "log", lambda message: None)
    monkeypatch.setattr(main, "worker_log", lambda worker_id, message: None)

    result = asyncio.run(
        bridge.run_register_only_many(
            {
                "mail": {"source": "hotmail"},
                "output": {"success_file": "old-success.txt", "failed_file": "old-failed.txt"},
                "chatgpt": {"start_url": "https://chatgpt.com/auth/login", "entry_action": "signup"},
            },
            count=1,
            workers=1,
        )
    )

    assert result.returncode == 0
    assert captured["kwargs"]["sms_selection"] is sms_selection
    assert captured["kwargs"]["create_payment_link"] is False


def test_run_register_only_many_uses_runtime_env_for_phone_mode(monkeypatch, tmp_path) -> None:
    output_dir = tmp_path / "output" / "register_only"
    session_dir = output_dir / "sessiond"
    session_cache = session_dir / "session_cache.jsonl"
    summary_file = output_dir / "registered_sessions.txt"
    used_file = output_dir / "used_emails.txt"
    in_progress_file = output_dir / "in_progress.txt"
    failed_file = output_dir / "failed_accounts.txt"
    sms_selection = {"provider_label": "fake-sms"}
    created_store = SimpleNamespace(name="store")
    captured = {}

    async def fake_ensure_register_accounts(cfg, store, desired_count: int) -> int:
        captured["ensure_cfg"] = cfg
        return desired_count

    async def fake_run_account(cfg, store, worker_id: int, **kwargs):
        captured["run_cfg"] = cfg
        captured["kwargs"] = kwargs
        summary_file.write_text("phone-env@example.com----session\n", encoding="utf-8")
        return True

    monkeypatch.setattr(bridge, "REGISTER_ONLY_OUTPUT_DIR", output_dir)
    monkeypatch.setattr(bridge, "REGISTER_ONLY_SESSION_DIR", session_dir)
    monkeypatch.setattr(bridge, "REGISTER_ONLY_SESSION_CACHE_FILE", session_cache)
    monkeypatch.setattr(bridge, "REGISTER_ONLY_SUMMARY_FILE", summary_file)
    monkeypatch.setattr(bridge, "REGISTER_ONLY_USED_FILE", used_file)
    monkeypatch.setattr(bridge, "REGISTER_ONLY_IN_PROGRESS_FILE", in_progress_file)
    monkeypatch.setattr(bridge, "REGISTER_ONLY_FAILED_FILE", failed_file)
    monkeypatch.setattr(main, "create_store", lambda cfg: created_store)
    monkeypatch.setattr(main, "ensure_register_accounts", fake_ensure_register_accounts)
    monkeypatch.setattr(main, "create_proxy_pool", lambda cfg: None)
    monkeypatch.setattr(main, "resolve_flow1_sms_selection", lambda: sms_selection)
    monkeypatch.setattr(main, "run_account", fake_run_account)
    monkeypatch.setattr(main, "log", lambda message: None)
    monkeypatch.setattr(main, "worker_log", lambda worker_id, message: None)

    result = asyncio.run(
        bridge.run_register_only_many(
            {
                "mail": {"source": "hotmail"},
                "output": {"success_file": "old-success.txt", "failed_file": "old-failed.txt"},
                "chatgpt": {"start_url": "https://chatgpt.com/auth/login", "entry_action": "signup"},
            },
            count=1,
            workers=1,
            env={"REGISTER_ONLY_MODE": "phone"},
        )
    )

    assert result.returncode == 0
    assert captured["run_cfg"]["chatgpt"]["entry_action"] == "signup_phone"
    assert captured["ensure_cfg"]["chatgpt"]["entry_action"] == "signup_phone"
    assert captured["kwargs"]["sms_selection"] is sms_selection


def test_run_register_tool_only_passes_runtime_env(monkeypatch) -> None:
    captured = {}

    async def fake_many(cfg, *, count, workers, selected_email="", env=None):
        captured["env"] = env
        captured["count"] = count
        captured["workers"] = workers
        captured["selected_email"] = selected_email
        return bridge.RegisterOnlyRunResult(0, 1, 1, bridge.REGISTER_ONLY_SESSION_DIR, bridge.REGISTER_ONLY_SUMMARY_FILE)

    monkeypatch.setattr(bridge, "run_register_only_many", fake_many)

    env = {"REGISTER_ONLY_MODE": "phone"}
    result = bridge.run_register_tool_only(
        {},
        count=2,
        workers=3,
        selected_email="user@example.com",
        env=env,
    )

    assert result.ok
    assert captured["env"] is env
    assert captured["count"] == 2
    assert captured["workers"] == 3
    assert captured["selected_email"] == "user@example.com"


def test_run_account_register_only_keeps_registered_account_when_session_fetch_fails(monkeypatch, tmp_path) -> None:
    from modules.storage import MailAccount

    mail_account = MailAccount(
        email="user@example.com",
        password="pw",
        client_id="client",
        refresh_token="rt",
        raw="user@example.com----pw----client----rt",
    )
    store = RunAccountStore(mail_account)

    async def fake_fetch_session(page, prefix, attempts=4):
        raise RuntimeError("HTTP 403 content_type=text/html body='<html>challenge</html>'")

    monkeypatch.setattr(main, "BrowserSession", FakeRunAccountSession)
    monkeypatch.setattr(main, "ChatGPTRegister", FakeChatGPTRegister)
    monkeypatch.setattr(main, "fetch_chatgpt_session_with_retry", fake_fetch_session)
    monkeypatch.setattr(main, "log", lambda message: None)

    result = asyncio.run(
        main.run_account(
            {
                "mail": {"source": "hotmail"},
                "browser": {"headless": True, "slow_mo": 0, "timeout_ms": 1000},
                "chatgpt": {"start_url": "https://chatgpt.com", "entry_action": "signup"},
                "register_profile": {"age_min": 21, "age_max": 45},
            },
            store,
            worker_id=1,
            create_payment_link=False,
            session_cache_path=tmp_path / "session_cache.jsonl",
        )
    )

    assert result is True
    assert store.completed == ["user@example.com"]
    assert store.returned == []
    assert store.failed == []
    assert store.saved_success[0][1]["account_line"] == "user@example.com----pw----client----rt"


def test_run_account_injects_account_password_into_sms_selection(monkeypatch, tmp_path) -> None:
    from modules.storage import MailAccount

    mail_account = MailAccount(
        email="phone@example.com",
        password="phone-pw",
        client_id="client",
        refresh_token="rt",
        raw="phone@example.com----phone-pw----client----rt",
    )
    store = RunAccountStore(mail_account)
    captured = {}

    class CapturingChatGPTRegister:
        def __init__(self, *args, **kwargs) -> None:
            captured["sms_selection"] = kwargs.get("sms_selection")

        async def run_until_logged_in(self, account, since) -> None:
            return None

    async def fake_fetch_session(page, prefix, attempts=4):
        raise RuntimeError("skip session fetch")

    async def fake_bind_phone_email(**kwargs):
        captured["bind_sms_selection"] = kwargs["sms_selection"]

    sms_selection = {"provider_label": "fake-sms"}
    monkeypatch.setattr(main, "BrowserSession", FakeRunAccountSession)
    monkeypatch.setattr(main, "ChatGPTRegister", CapturingChatGPTRegister)
    monkeypatch.setattr(main, "bind_register_only_phone_email", fake_bind_phone_email)
    monkeypatch.setattr(main, "fetch_chatgpt_session_with_retry", fake_fetch_session)
    monkeypatch.setattr(main, "log", lambda message: None)

    result = asyncio.run(
        main.run_account(
            {
                "mail": {"source": "hotmail"},
                "browser": {"headless": True, "slow_mo": 0, "timeout_ms": 1000},
                "chatgpt": {"start_url": "https://chatgpt.com", "entry_action": "signup_phone"},
                "register_profile": {"age_min": 21, "age_max": 45},
            },
            store,
            worker_id=1,
            sms_selection=sms_selection,
            create_payment_link=False,
            session_cache_path=tmp_path / "session_cache.jsonl",
        )
    )

    assert result is True
    assert captured["sms_selection"]["password"] == "phone-pw"
    assert captured["sms_selection"]["defer_sms_complete"] is True
    assert captured["bind_sms_selection"] is captured["sms_selection"]
    assert sms_selection == {"provider_label": "fake-sms"}


def test_bind_register_only_phone_email_uses_free_oauth_bind_and_completes_sms(monkeypatch) -> None:
    from modules.storage import MailAccount

    account = MailAccount(
        email="bind@example.com",
        password="bind-pw",
        client_id="client",
        refresh_token="rt",
        raw="bind@example.com----bind-pw----client----rt",
    )
    sms_selection = {"password": "bind-pw", "last_phone": "+819012345678"}
    register = SimpleNamespace(generated_name="Jane Bind", generated_age="33")
    captured = {"finalize": []}

    async def fake_phase2(flow, received_account, mail_provider, received_sms_selection, profile, prefix):
        captured["account"] = received_account
        captured["sms_selection"] = received_sms_selection
        captured["profile"] = profile
        return {"ok": True}

    async def fake_finalize(received_sms_selection, *, success, prefix):
        captured["finalize"].append(success)

    monkeypatch.setattr(main.free_register, "phase2_bind_and_get_token", fake_phase2)
    monkeypatch.setattr(main.free_register, "finalize_free_sms_activation", fake_finalize)
    monkeypatch.setattr(main, "log", lambda message: None)

    asyncio.run(
        main.bind_register_only_phone_email(
            page=FakeRunAccountPage(),
            account=account,
            mail_provider=SimpleNamespace(),
            sms_selection=sms_selection,
            register=register,
            cfg={"register_profile": {"age_min": 21}},
            prefix="[test]",
        )
    )

    assert captured["account"] is account
    assert captured["sms_selection"] is sms_selection
    assert captured["profile"].full_name == "Jane Bind"
    assert captured["profile"].age == "33"
    assert captured["profile"].password == "bind-pw"
    assert captured["finalize"] == [True]


def test_bind_register_only_phone_email_failure_cancels_sms(monkeypatch) -> None:
    from modules.storage import MailAccount

    account = MailAccount(email="bind-fail@example.com", password="pw")
    sms_selection = {"password": "pw", "last_phone": "+819012345678"}
    captured = {"finalize": []}

    async def fake_phase2(*args, **kwargs):
        raise RuntimeError("bind failed")

    async def fake_finalize(received_sms_selection, *, success, prefix):
        captured["finalize"].append(success)

    monkeypatch.setattr(main.free_register, "phase2_bind_and_get_token", fake_phase2)
    monkeypatch.setattr(main.free_register, "finalize_free_sms_activation", fake_finalize)
    monkeypatch.setattr(main, "log", lambda message: None)

    with pytest.raises(RuntimeError, match="bind failed"):
        asyncio.run(
            main.bind_register_only_phone_email(
                page=FakeRunAccountPage(),
                account=account,
                mail_provider=SimpleNamespace(),
                sms_selection=sms_selection,
                register=SimpleNamespace(generated_name="", generated_age=""),
                cfg={"register_profile": {"age_min": 21}},
                prefix="[test]",
            )
        )

    assert captured["finalize"] == [False]


def test_run_account_phone_bind_failure_does_not_save_success(monkeypatch, tmp_path) -> None:
    from modules.storage import MailAccount

    mail_account = MailAccount(
        email="phone-bind-fail@example.com",
        password="phone-pw",
        client_id="client",
        refresh_token="rt",
        raw="phone-bind-fail@example.com----phone-pw----client----rt",
    )
    store = RunAccountStore(mail_account)

    async def fake_bind_phone_email(**kwargs):
        raise RuntimeError("email bind failed")

    monkeypatch.setattr(main, "BrowserSession", FakeRunAccountSession)
    monkeypatch.setattr(main, "ChatGPTRegister", FakeChatGPTRegister)
    monkeypatch.setattr(main, "bind_register_only_phone_email", fake_bind_phone_email)
    monkeypatch.setattr(main, "log", lambda message: None)

    result = asyncio.run(
        main.run_account(
            {
                "mail": {"source": "hotmail"},
                "browser": {"headless": True, "slow_mo": 0, "timeout_ms": 1000},
                "chatgpt": {"start_url": "https://chatgpt.com", "entry_action": "signup_phone"},
                "register_profile": {"age_min": 21, "age_max": 45},
            },
            store,
            worker_id=1,
            sms_selection={"provider_label": "fake-sms"},
            create_payment_link=False,
            session_cache_path=tmp_path / "session_cache.jsonl",
        )
    )

    assert result is False
    assert store.saved_success == []
    assert store.completed == []
    assert store.returned == ["phone-bind-fail@example.com"]
    assert store.failed and store.failed[0][0] == "phone-bind-fail@example.com"


def test_apply_paypal_blocked_emails_blocks_discarded_registered_and_linked(monkeypatch, tmp_path) -> None:
    output_dir = tmp_path / "output" / "register_only"
    summary_file = output_dir / "registered_sessions.txt"
    used_file = output_dir / "used_emails.txt"
    state_file = output_dir / "paypal_flow_state.json"
    discard_file = output_dir / "paypal_flow_discarded_emails.txt"
    link_file = tmp_path / "output" / "paypal" / "links" / "account.txt"
    pending_file = tmp_path / "output" / "paypal" / "pending" / "account.txt"
    summary_file.parent.mkdir(parents=True)
    summary_file.write_text("registered@example.com----pw\n", encoding="utf-8")
    used_file.write_text("used@example.com\n", encoding="utf-8")
    discard_file.write_text("discarded@example.com\tmanual\n", encoding="utf-8")
    link_file.parent.mkdir(parents=True)
    link_file.write_text("linked@example.com----pw----https://pay.example\n", encoding="utf-8")
    pending_file.parent.mkdir(parents=True)
    pending_file.write_text("pending@example.com----pw\n", encoding="utf-8")

    monkeypatch.setattr(bridge, "REGISTER_ONLY_SUMMARY_FILE", summary_file)
    monkeypatch.setattr(bridge, "REGISTER_ONLY_USED_FILE", used_file)
    monkeypatch.setattr(bridge, "PAYPAL_LINK_POOL_FILE", link_file)
    monkeypatch.setattr(bridge, "PAYPAL_PENDING_AUTH_FILE", pending_file)
    monkeypatch.setattr(paypal_flow_state, "PAYPAL_FLOW_STATE_FILE", state_file)
    monkeypatch.setattr(paypal_flow_state, "PAYPAL_FLOW_DISCARDED_FILE", discard_file)
    store = FakeStore()

    blocked = bridge.apply_paypal_blocked_emails(store)

    assert {
        "registered@example.com",
        "used@example.com",
        "discarded@example.com",
        "linked@example.com",
        "pending@example.com",
    }.issubset(blocked)
    assert blocked.issubset(store.blocked_emails)
