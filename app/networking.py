"""Network address discovery, so the catalog is usable from other devices.

When the server binds to a wildcard address (``0.0.0.0``) and no explicit
``BASE_URL`` is configured, OPDS/RSS links must point at the machine's LAN
address -- otherwise every link says ``localhost`` and no e-reader can follow it.
"""

from __future__ import annotations

import logging
import socket
from functools import lru_cache

logger = logging.getLogger(__name__)

WILDCARD_HOSTS = {"0.0.0.0", "::", "", "*"}
LOOPBACK_NAMES = {"localhost", "127.0.0.1", "::1", "[::1]"}


def is_wildcard(host: str | None) -> bool:
    return (host or "").strip() in WILDCARD_HOSTS


def is_loopback(host: str | None) -> bool:
    if not host:
        return False
    host = host.strip().lower()
    return host in LOOPBACK_NAMES or host.startswith("127.")


@lru_cache(maxsize=1)
def primary_ipv4() -> str | None:
    """Best-effort LAN address of this machine (cached).

    Opens a UDP socket to a public address; no packets are sent, it only asks
    the OS which local interface would be used for outbound traffic.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(0.5)
            sock.connect(("8.8.8.8", 80))
            address = sock.getsockname()[0]
            if address and not address.startswith("127."):
                return address
    except OSError:
        pass

    try:
        address = socket.gethostbyname(socket.gethostname())
        if address and not address.startswith("127."):
            return address
    except OSError:
        pass
    return None


def local_ipv4_addresses() -> list[str]:
    """Every non-loopback IPv4 address this host owns (best effort)."""
    addresses: set[str] = set()
    primary = primary_ipv4()
    if primary:
        addresses.add(primary)

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            if address and not address.startswith("127."):
                addresses.add(address)
    except OSError:
        pass

    # Put the primary address first for stable display.
    ordered = sorted(addresses)
    if primary in ordered:
        ordered.remove(primary)
        ordered.insert(0, primary)
    return ordered


def resolve_base_url(configured: str, host: str, port: int) -> str:
    """Work out the public base URL used to build absolute links.

    Priority: an explicit non-loopback ``BASE_URL`` -> the LAN IP when bound to
    a wildcard address -> the configured host.
    """
    configured = (configured or "").strip().rstrip("/")
    if configured and not _is_loopback_url(configured):
        return configured

    if is_wildcard(host):
        address = primary_ipv4()
        if address:
            return f"http://{address}:{port}"
        return configured or f"http://localhost:{port}"

    if is_loopback(host):
        return configured or f"http://localhost:{port}"

    return f"http://{host}:{port}"


def request_base_url(request, settings) -> str:
    """Base URL for the current request.

    Default behaviour (``USE_REQUEST_HOST=true``) is "use the address the client
    used", read from the ``Host`` header. That is what makes the catalog work
    over Tailscale, a VPN, a LAN IP or a domain without reconfiguring anything.

    With ``USE_REQUEST_HOST=false``, a configured ``BASE_URL`` (or the LAN
    auto-detection) wins -- useful behind a reverse proxy that must advertise a
    canonical URL.
    """
    configured = (getattr(settings, "base_url", "") or "").strip().rstrip("/")

    if not getattr(settings, "use_request_host", True):
        return configured or resolve_base_url(configured, settings.host, settings.port)

    headers = getattr(request, "headers", {}) or {}
    host = headers.get("host")
    if getattr(settings, "trust_proxy", False):
        host = headers.get("x-forwarded-host") or host
        scheme = headers.get("x-forwarded-proto") or getattr(getattr(request, "url", None), "scheme", "http")
    else:
        scheme = getattr(getattr(request, "url", None), "scheme", "http")

    if host:
        return f"{scheme}://{host}".rstrip("/")

    # No Host header (unusual): fall back to the configured/auto base.
    return configured or resolve_base_url(configured, settings.host, settings.port)


def access_urls(host: str, port: int, *, base_url: str | None = None) -> list[str]:
    """Human-readable list of URLs where the panel is reachable."""
    base = (base_url or "").rstrip("/")
    urls = [base] if base else []
    if is_wildcard(host):
        urls.extend(f"http://{address}:{port}" for address in local_ipv4_addresses())
        urls.append(f"http://localhost:{port}")
    else:
        urls.append(f"http://{host}:{port}")

    seen: set[str] = set()
    ordered: list[str] = []
    for url in urls:
        if url and url not in seen:
            seen.add(url)
            ordered.append(url)
    return ordered


def _is_loopback_url(url: str) -> bool:
    from urllib.parse import urlparse

    try:
        return is_loopback(urlparse(url).hostname)
    except ValueError:
        return False
