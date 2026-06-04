import asyncio
from pathlib import Path

from modules import paypal_card_redeem, paypal_flow_state, paypal_pay, proxy_pool as proxy_pool_module, utils


def _use_paypal_files(monkeypatch, tmp_path: Path, links_file: Path) -> None:
    state_file = tmp_path / "paypal_flow_state.json"
    discard_file = tmp_path / "paypal_flow_discarded_emails.txt"
    pending_file = tmp_path / "pending.txt"
    monkeypatch.setattr(paypal_flow_state, "PAYPAL_FLOW_STATE_FILE", state_file)
    monkeypatch.setattr(paypal_flow_state, "PAYPAL_FLOW_DISCARDED_FILE", discard_file)
    monkeypatch.setattr(paypal_pay, "LINK_POOL_FILE", links_file)
    monkeypatch.setattr(paypal_pay, "PENDING_AUTH_DIR", tmp_path)
    monkeypatch.setattr(paypal_pay, "PENDING_AUTH_FILE", pending_file)


def test_run_paypal_pay_auto_redeems_when_card_pool_is_empty(monkeypatch, tmp_path: Path) -> None:
    cards_file = tmp_path / "cards.txt"
    codes_file = tmp_path / "card_codes.txt"
    used_file = tmp_path / "card_codes_used.txt"
    failed_file = tmp_path / "card_codes_failed.txt"
    phones_file = tmp_path / "phones.txt"
    links_file = tmp_path / "links.txt"

    codes_file.write_text("CODE-1\n", encoding="utf-8")
    cards_file.write_text("", encoding="utf-8")
    used_file.write_text("", encoding="utf-8")
    failed_file.write_text("", encoding="utf-8")
    phones_file.write_text("15555550123|https://sms.example.test/get\n", encoding="utf-8")
    links_file.write_text("user@example.com----query-code----https://pay.example.test/session\n", encoding="utf-8")
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "PAYPAL_CARD_REDEEM_ENABLED=true",
                f"PAYPAL_CARDS_FILE={cards_file}",
                f"PAYPAL_CARD_CODES_FILE={codes_file}",
                f"PAYPAL_CARD_CODES_USED_FILE={used_file}",
                f"PAYPAL_CARD_CODES_FAILED_FILE={failed_file}",
                f"PAYPAL_PHONES_FILE={phones_file}",
                "PAYPAL_USE_PROXY=false",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    def fake_redeem(code, cfg):
        return (
            True,
            f"{code}----4111111111111111----2030/4----123----15555550123----JOHN DOE----1 Main St, New York NY 10001, US----https://sms.example.test/get",
        )

    async def fake_pay_one(*args, **kwargs):
        return False

    monkeypatch.setattr(utils, "PROJECT_ROOT", tmp_path)
    _use_paypal_files(monkeypatch, tmp_path, links_file)
    monkeypatch.setattr(paypal_card_redeem, "redeem_code_once", fake_redeem)
    monkeypatch.setattr(paypal_pay, "pay_one", fake_pay_one)

    result = asyncio.run(paypal_pay.run_paypal_pay({}, count=1, workers=1))

    assert result == 0
    assert "4111111111111111" in cards_file.read_text(encoding="utf-8")
    assert used_file.read_text(encoding="utf-8").strip() == "CODE-1"
    assert codes_file.read_text(encoding="utf-8").strip() == ""


def test_redeem_code_once_uses_bom_stripped_proxy(monkeypatch, tmp_path: Path) -> None:
    proxy_file = tmp_path / "proxies_us.txt"
    proxy_file.write_text("\ufeffhttp://user:pass@proxy.example.test:3010\n", encoding="utf-8")
    cfg = paypal_card_redeem.RedeemConfig(
        enabled=True,
        api_url="https://card.example.test/api/exchange/verify",
        api_key="",
        timeout_sec=20,
        code_field="key",
        codes_file=tmp_path / "codes.txt",
        cards_file=tmp_path / "cards.txt",
        used_file=tmp_path / "used.txt",
        failed_file=tmp_path / "failed.txt",
        append_when_status_used=True,
        max_auto_fetch=20,
        retry_per_code=2,
        use_proxy=True,
        proxy_file=proxy_file,
        stop_on_request_error=True,
    )
    captured = {}

    class FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            return {
                "content": {
                    "card_number": "4111111111111111",
                    "expiry_date": "2030/4",
                    "cvv": "123",
                }
            }

    def fake_post(url, **kwargs):
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr(paypal_card_redeem.requests, "post", fake_post)

    ok, _ = paypal_card_redeem.redeem_code_once("CODE-1", cfg)

    assert ok
    assert captured["proxies"] == {
        "http": "http://user:pass@proxy.example.test:3010",
        "https": "http://user:pass@proxy.example.test:3010",
    }


def test_ensure_card_supply_stops_after_network_request_error(monkeypatch, tmp_path: Path) -> None:
    cards_file = tmp_path / "cards.txt"
    codes_file = tmp_path / "codes.txt"
    used_file = tmp_path / "used.txt"
    failed_file = tmp_path / "failed.txt"
    cards_file.write_text("", encoding="utf-8")
    codes_file.write_text("CODE-1\nCODE-2\n", encoding="utf-8")
    calls = []

    def fake_redeem(code, cfg):
        calls.append(code)
        return False, "request_error: tls failed"

    monkeypatch.setattr(paypal_card_redeem, "redeem_code_once", fake_redeem)

    paypal_card_redeem.ensure_card_supply(
        {
            "PAYPAL_CARD_REDEEM_ENABLED": "true",
            "PAYPAL_CARDS_FILE": str(cards_file),
            "PAYPAL_CARD_CODES_FILE": str(codes_file),
            "PAYPAL_CARD_CODES_USED_FILE": str(used_file),
            "PAYPAL_CARD_CODES_FAILED_FILE": str(failed_file),
            "PAYPAL_CARD_REDEEM_STOP_ON_REQUEST_ERROR": "true",
        },
        1,
    )

    assert calls == ["CODE-1"]
    assert codes_file.read_text(encoding="utf-8").splitlines() == ["CODE-1", "CODE-2"]


def test_run_paypal_pay_local_random_card_mode_works_without_card_pool(monkeypatch, tmp_path: Path) -> None:
    cards_file = tmp_path / "cards.txt"
    phones_file = tmp_path / "phones.txt"
    links_file = tmp_path / "links.txt"
    cards_file.write_text("", encoding="utf-8")
    phones_file.write_text("15555550123|https://sms.example.test/get\n", encoding="utf-8")
    links_file.write_text("user@example.com----query-code----https://pay.example.test/session\n", encoding="utf-8")
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "PAYPAL_CARD_SOURCE=local_random",
                f"PAYPAL_CARDS_FILE={cards_file}",
                f"PAYPAL_PHONES_FILE={phones_file}",
                "PAYPAL_USE_PROXY=false",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    captured = {}

    async def fake_pay_one(item, card, *args, **kwargs):
        captured["email"] = item["email"]
        captured["card_number"] = card.number
        captured["zip"] = card.zip_code
        return False

    monkeypatch.setattr(utils, "PROJECT_ROOT", tmp_path)
    _use_paypal_files(monkeypatch, tmp_path, links_file)
    monkeypatch.setattr(paypal_pay, "pay_one", fake_pay_one)

    result = asyncio.run(paypal_pay.run_paypal_pay({}, count=1, workers=1))

    assert result == 0
    assert captured["email"] == "user@example.com"
    assert len(captured["card_number"]) >= 13
    assert captured["card_number"][0] in {"4", "5"}
    assert captured["zip"]


def test_run_paypal_pay_local_random_override_works_without_env_switch(monkeypatch, tmp_path: Path) -> None:
    cards_file = tmp_path / "cards.txt"
    phones_file = tmp_path / "phones.txt"
    links_file = tmp_path / "links.txt"
    cards_file.write_text("", encoding="utf-8")
    phones_file.write_text("15555550123|https://sms.example.test/get\n", encoding="utf-8")
    links_file.write_text("user2@example.com----query-code----https://pay.example.test/session\n", encoding="utf-8")
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                f"PAYPAL_CARDS_FILE={cards_file}",
                f"PAYPAL_PHONES_FILE={phones_file}",
                "PAYPAL_USE_PROXY=false",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    hit = {"ok": False}

    async def fake_pay_one(*args, **kwargs):
        hit["ok"] = True
        return False

    monkeypatch.setattr(utils, "PROJECT_ROOT", tmp_path)
    _use_paypal_files(monkeypatch, tmp_path, links_file)
    monkeypatch.setattr(paypal_pay, "pay_one", fake_pay_one)

    result = asyncio.run(paypal_pay.run_paypal_pay({}, count=1, workers=1, card_source_mode="local_random"))

    assert result == 0
    assert hit["ok"]


def test_run_paypal_pay_uses_local_proxy_when_proxy_pool_disabled(monkeypatch, tmp_path: Path) -> None:
    phones_file = tmp_path / "phones.txt"
    links_file = tmp_path / "links.txt"
    phones_file.write_text("15555550123|https://sms.example.test/get\n", encoding="utf-8")
    links_file.write_text("user3@example.com----query-code----https://pay.example.test/session\n", encoding="utf-8")
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                f"PAYPAL_PHONES_FILE={phones_file}",
                "PAYPAL_USE_PROXY=false",
                "LOCAL_PROXY_URL=http://127.0.0.1:7987",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    captured = {}

    async def fake_pay_one(*args, **kwargs):
        captured["proxy"] = kwargs.get("proxy")
        return False

    monkeypatch.setattr(utils, "PROJECT_ROOT", tmp_path)
    _use_paypal_files(monkeypatch, tmp_path, links_file)
    monkeypatch.setattr(paypal_pay, "pay_one", fake_pay_one)

    result = asyncio.run(paypal_pay.run_paypal_pay({}, count=1, workers=1, card_source_mode="local_random"))

    assert result == 0
    assert captured["proxy"] == "http://127.0.0.1:7987"


def test_run_paypal_pay_jp_uses_jp_proxy_file(monkeypatch, tmp_path: Path) -> None:
    phones_file = tmp_path / "phones.txt"
    links_file = tmp_path / "links.txt"
    jp_proxy_file = tmp_path / "jp.txt"
    us_proxy_file = tmp_path / "us.txt"
    phones_file.write_text("15555550123|https://sms.example.test/get\n", encoding="utf-8")
    links_file.write_text("user4@example.com----query-code----https://pay.example.test/session\n", encoding="utf-8")
    jp_proxy_file.write_text("http://jp-proxy.example.test:8080\n", encoding="utf-8")
    us_proxy_file.write_text("http://us-proxy.example.test:8080\n", encoding="utf-8")
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                f"PAYPAL_PHONES_FILE={phones_file}",
                "PAYPAL_USE_PROXY=true",
                f"PAYPAL_PROXY_FILE_US={us_proxy_file}",
                f"PAYPAL_PROXY_FILE_JP={jp_proxy_file}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    captured = {}

    async def fake_pay_one(*args, **kwargs):
        captured["proxy"] = kwargs.get("proxy")
        return False

    monkeypatch.setattr(utils, "PROJECT_ROOT", tmp_path)
    _use_paypal_files(monkeypatch, tmp_path, links_file)
    monkeypatch.setattr(paypal_pay, "pay_one", fake_pay_one)

    result = asyncio.run(
        paypal_pay.run_paypal_pay(
            {},
            count=1,
            workers=1,
            card_source_mode="local_random",
            flow2_region_mode="jp",
        )
    )

    assert result == 0
    assert captured["proxy"] == "http://jp-proxy.example.test:8080"


def test_run_paypal_pay_randomizes_proxy_order_on_proxy_failure(monkeypatch, tmp_path: Path) -> None:
    phones_file = tmp_path / "phones.txt"
    links_file = tmp_path / "links.txt"
    proxy_file = tmp_path / "jp.txt"
    phones_file.write_text("15555550123|https://sms.example.test/get\n", encoding="utf-8")
    links_file.write_text("user5@example.com----query-code----https://pay.example.test/session\n", encoding="utf-8")
    proxy_file.write_text(
        "\n".join(
            [
                "http://good-proxy.example.test:1080",
                "http://bad-proxy.example.test:1080",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                f"PAYPAL_PHONES_FILE={phones_file}",
                "PAYPAL_USE_PROXY=true",
                f"PAYPAL_PROXY_FILE_JP={proxy_file}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    attempts = []
    shuffled = []

    def fake_shuffle(values):
        values.reverse()
        shuffled.append(values[:])

    async def fake_pay_one(*args, **kwargs):
        proxy = kwargs.get("proxy")
        attempts.append(proxy)
        if "bad-proxy" in proxy:
            kwargs["last_error"]["reason"] = "Page.goto: net::ERR_SOCKS_CONNECTION_FAILED"
            return False
        return True

    monkeypatch.setattr(utils, "PROJECT_ROOT", tmp_path)
    _use_paypal_files(monkeypatch, tmp_path, links_file)
    monkeypatch.setattr(proxy_pool_module.random, "shuffle", fake_shuffle)
    monkeypatch.setattr(paypal_pay, "pay_one", fake_pay_one)

    result = asyncio.run(
        paypal_pay.run_paypal_pay(
            {},
            count=1,
            workers=1,
            card_source_mode="local_random",
            flow2_region_mode="jp",
        )
    )

    assert result == 1
    assert shuffled == [
        [
            "http://bad-proxy.example.test:1080",
            "http://good-proxy.example.test:1080",
        ]
    ]
    assert attempts == [
        "http://bad-proxy.example.test:1080",
        "http://good-proxy.example.test:1080",
    ]


def test_run_paypal_pay_rebinds_proxy_after_flow_error(monkeypatch, tmp_path: Path) -> None:
    phones_file = tmp_path / "phones.txt"
    links_file = tmp_path / "links.txt"
    proxy_file = tmp_path / "jp.txt"
    phones_file.write_text("15555550123|https://sms.example.test/get\n", encoding="utf-8")
    links_file.write_text("user-flow-error@example.com----query-code----https://pay.example.test/session\n", encoding="utf-8")
    proxy_file.write_text(
        "\n".join(
            [
                "http://good-proxy.example.test:1080",
                "http://first-proxy.example.test:1080",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                f"PAYPAL_PHONES_FILE={phones_file}",
                "PAYPAL_USE_PROXY=true",
                f"PAYPAL_PROXY_FILE_JP={proxy_file}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    attempts = []

    def fake_shuffle(values):
        values.reverse()

    async def fake_pay_one(*args, **kwargs):
        proxy = kwargs.get("proxy")
        attempts.append(proxy)
        if "first-proxy" in proxy:
            kwargs["last_error"]["reason"] = "checkout ui stuck after submit"
            return False
        return True

    monkeypatch.setattr(utils, "PROJECT_ROOT", tmp_path)
    _use_paypal_files(monkeypatch, tmp_path, links_file)
    monkeypatch.setattr(proxy_pool_module.random, "shuffle", fake_shuffle)
    monkeypatch.setattr(paypal_pay, "pay_one", fake_pay_one)

    result = asyncio.run(
        paypal_pay.run_paypal_pay(
            {},
            count=1,
            workers=1,
            card_source_mode="local_random",
            flow2_region_mode="jp",
        )
    )

    assert result == 1
    assert attempts == [
        "http://first-proxy.example.test:1080",
        "http://good-proxy.example.test:1080",
    ]


def test_run_paypal_pay_limits_stripe_redirect_proxy_retry_to_three(monkeypatch, tmp_path: Path) -> None:
    phones_file = tmp_path / "phones.txt"
    links_file = tmp_path / "links.txt"
    proxy_file = tmp_path / "jp.txt"
    phones_file.write_text("15555550123|https://sms.example.test/get\n", encoding="utf-8")
    links_file.write_text("user6@example.com----query-code----https://pay.example.test/session\n", encoding="utf-8")
    proxy_file.write_text(
        "\n".join(f"http://proxy-{index}.example.test:1080" for index in range(1, 7)) + "\n",
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                f"PAYPAL_PHONES_FILE={phones_file}",
                "PAYPAL_USE_PROXY=true",
                f"PAYPAL_PROXY_FILE_JP={proxy_file}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    attempts = []

    async def fake_pay_one(*args, **kwargs):
        attempts.append(kwargs.get("proxy"))
        kwargs["last_error"]["reason"] = paypal_pay.PAYPAL_FLOW2_STRIPE_PAYPAL_TIMEOUT
        return False

    monkeypatch.setattr(utils, "PROJECT_ROOT", tmp_path)
    _use_paypal_files(monkeypatch, tmp_path, links_file)
    monkeypatch.setattr(proxy_pool_module.random, "shuffle", lambda values: None)
    monkeypatch.setattr(paypal_pay, "pay_one", fake_pay_one)

    result = asyncio.run(
        paypal_pay.run_paypal_pay(
            {},
            count=1,
            workers=1,
            card_source_mode="local_random",
            flow2_region_mode="jp",
        )
    )

    assert result == 0
    assert attempts == [
        "http://proxy-1.example.test:1080",
        "http://proxy-2.example.test:1080",
        "http://proxy-3.example.test:1080",
        "http://proxy-4.example.test:1080",
    ]


def test_run_paypal_pay_replaces_nonzero_discard_with_next_link(monkeypatch, tmp_path: Path) -> None:
    phones_file = tmp_path / "phones.txt"
    links_file = tmp_path / "links.txt"
    phones_file.write_text("15555550123|https://sms.example.test/get\n", encoding="utf-8")
    links_file.write_text(
        "\n".join(
            [
                "bad@example.com----query-code----https://pay.example.test/nonzero",
                "good@example.com----query-code----https://pay.example.test/zero",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                f"PAYPAL_PHONES_FILE={phones_file}",
                "PAYPAL_USE_PROXY=false",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    attempted: list[str] = []

    async def fake_pay_one(item, *args, **kwargs):
        attempted.append(item["email"])
        if item["email"] == "bad@example.com":
            paypal_pay.discard_flow2_link(item["email"], reason="nonzero_checkout_amount: US$20.00")
            kwargs["last_error"]["reason"] = paypal_pay.PAYPAL_FLOW2_NONZERO_AMOUNT
            return False
        return True

    monkeypatch.setattr(utils, "PROJECT_ROOT", tmp_path)
    _use_paypal_files(monkeypatch, tmp_path, links_file)
    monkeypatch.setattr(paypal_pay, "pay_one", fake_pay_one)

    result = asyncio.run(paypal_pay.run_paypal_pay({}, count=1, workers=1, card_source_mode="local_random"))

    assert result == 1
    assert attempted == ["bad@example.com", "good@example.com"]
    assert "bad@example.com" not in links_file.read_text(encoding="utf-8")
    assert "good@example.com" in links_file.read_text(encoding="utf-8")
    assert "bad@example.com" in (tmp_path / "paypal_flow_discarded_emails.txt").read_text(encoding="utf-8")


def test_run_paypal_pay_moves_bad_generated_link_back_to_registered(monkeypatch, tmp_path: Path) -> None:
    phones_file = tmp_path / "phones.txt"
    links_file = tmp_path / "links.txt"
    phones_file.write_text("15555550123|https://sms.example.test/get\n", encoding="utf-8")
    links_file.write_text(
        "relink@example.com----pw----client----rt----https://pay.example.test/stuck\n",
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                f"PAYPAL_PHONES_FILE={phones_file}",
                "PAYPAL_USE_PROXY=false",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    async def fake_pay_one(item, *args, **kwargs):
        kwargs["last_error"]["reason"] = f"{paypal_pay.PAYPAL_FLOW2_RECREATE_LINK}: stuck after submit"
        return False

    monkeypatch.setattr(utils, "PROJECT_ROOT", tmp_path)
    _use_paypal_files(monkeypatch, tmp_path, links_file)
    paypal_flow_state.mark_link_ready(
        "relink@example.com",
        account_line="relink@example.com----pw----client----rt",
        payment_link="https://pay.example.test/stuck",
        link_method="external_api",
    )
    monkeypatch.setattr(paypal_pay, "pay_one", fake_pay_one)

    result = asyncio.run(paypal_pay.run_paypal_pay({}, count=1, workers=1, card_source_mode="local_random"))

    assert result == 0
    assert links_file.read_text(encoding="utf-8") == ""
    assert not (tmp_path / "paypal_flow_discarded_emails.txt").exists()
    state = paypal_flow_state.load_state(tmp_path / "paypal_flow_state.json")
    assert state["relink@example.com"]["status"] == paypal_flow_state.STATUS_REGISTERED
    assert state["relink@example.com"]["account_line"] == "relink@example.com----pw----client----rt"
    assert state["relink@example.com"]["bad_link_methods"] == ["external_api"]
    assert state["relink@example.com"]["last_bad_link_method"] == "external_api"
    assert "payment_link" not in state["relink@example.com"]
    assert "link_method" not in state["relink@example.com"]
