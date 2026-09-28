"""Per-request base URL.

Links in OPDS feeds must point at the address the *client* used, not at an IP we
guessed. If the phone reaches the server over Tailscale, a VPN, a hostname or a
domain, the download links have to use that same address -- otherwise the client
is sent to a network it is not on.

The middleware in ``app.main`` stores the resolved base URL here; the URL
builders read it. ``ContextVar`` propagates correctly into FastAPI's threadpool
execution and into the worker's own tasks.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

_base_url: ContextVar[str | None] = ContextVar("request_base_url", default=None)


def set_base_url(value: str | None) -> Token:
    return _base_url.set(value)


def reset_base_url(token: Token) -> None:
    _base_url.reset(token)


def current_base_url() -> str | None:
    """The base URL for the request being handled, or ``None`` outside one."""
    return _base_url.get()
