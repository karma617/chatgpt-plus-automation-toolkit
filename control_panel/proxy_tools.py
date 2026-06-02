from __future__ import annotations

import re


PROXY_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)
SUPPORTED_PROXY_SCHEMES = ("http://", "https://", "socks5://")
DEFAULT_PROXY_SCHEME = "http://"


def ensure_proxy_scheme(line: str, default_scheme: str = DEFAULT_PROXY_SCHEME) -> str:
    text = str(line or "").strip()
    if not text:
        return ""
    if PROXY_SCHEME_RE.match(text):
        return text
    scheme = (default_scheme or DEFAULT_PROXY_SCHEME).strip().lower()
    if scheme not in SUPPORTED_PROXY_SCHEMES:
        scheme = DEFAULT_PROXY_SCHEME
    return f"{scheme}{text}"


def add_proxy_schemes(content: str, default_scheme: str = DEFAULT_PROXY_SCHEME) -> str:
    lines = [ensure_proxy_scheme(line, default_scheme) for line in str(content or "").splitlines()]
    normalized = [line for line in lines if line]
    return "\n".join(normalized) + ("\n" if normalized else "")
