from __future__ import annotations

import importlib.util
import sys
from argparse import Namespace
from pathlib import Path


def _load_probe_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "chatgpt_challenge_probe.py"
    spec = importlib.util.spec_from_file_location("chatgpt_challenge_probe", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_flaresolverr_auth_proxy_uses_session_proxy() -> None:
    mod = _load_probe_module()
    flare_proxy, needs_session = mod._split_flaresolverr_proxy(
        "socks5h://user:pass@example.test:1080"
    )

    assert needs_session is True
    assert flare_proxy == {
        "url": "socks5://example.test:1080",
        "username": "user",
        "password": "pass",
    }


def test_flaresolverr_request_proxy_without_auth_stays_request_level() -> None:
    mod = _load_probe_module()
    args = Namespace(url="https://chatgpt.com/", timeout_seconds=90, flare_wait_seconds=5)

    payload, flare_proxy, needs_session = mod._flare_payload(
        args,
        "http://127.0.0.1:7897",
    )

    assert needs_session is False
    assert flare_proxy == {"url": "http://127.0.0.1:7897"}
    assert payload["proxy"] == {"url": "http://127.0.0.1:7897"}
    assert payload["waitInSeconds"] == 5


def test_flaresolverr_auth_proxy_not_sent_to_request_get() -> None:
    mod = _load_probe_module()
    args = Namespace(url="https://chatgpt.com/", timeout_seconds=90, flare_wait_seconds=7)

    payload, flare_proxy, needs_session = mod._flare_payload(
        args,
        "http://user:pass@example.test:8080",
    )

    assert needs_session is True
    assert flare_proxy == {
        "url": "http://example.test:8080",
        "username": "user",
        "password": "pass",
    }
    assert "proxy" not in payload
    assert payload["waitInSeconds"] == 7


def test_challenge_marker_prevents_success_classification() -> None:
    mod = _load_probe_module()

    ok, reason = mod._looks_like_success(
        {
            "url": "https://chatgpt.com/",
            "title": "Just a moment...",
            "text_sample": "Checking your browser before accessing ChatGPT",
            "html_sample": "<script src='https://challenges.cloudflare.com/turnstile/v0/api.js'></script>",
            "cloudflare": {"present": True},
        }
    )

    assert ok is False
    assert reason == "challenge_present"
