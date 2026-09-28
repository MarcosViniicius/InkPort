"""Absolute URL builders for OPDS feeds."""

from __future__ import annotations

from urllib.parse import quote, urlencode

from app.config import get_settings

OPDS_PREFIX = "/opds"
OPDS2_PREFIX = "/opds/v2"


def absolute(path: str, query: dict | None = None) -> str:
    """Absolute URL for a path.

    Uses the address the current request arrived on (Tailscale, LAN IP, domain,
    hostname...), falling back to the configured/auto base URL outside a request.
    """
    from app.request_context import current_base_url

    base = (current_base_url() or get_settings().base_url_clean).rstrip("/")
    url = f"{base}{path}"
    if query:
        clean = {k: v for k, v in query.items() if v not in (None, "")}
        if clean:
            url = f"{url}?{urlencode(clean)}"
    return url


# --- OPDS 1.2 -------------------------------------------------------------
def root() -> str:
    return absolute(f"{OPDS_PREFIX}/")


def navigation(path: str, query: dict | None = None) -> str:
    return absolute(path, query)


def acquisition(path: str, query: dict | None = None) -> str:
    return absolute(path, query)


def search_description() -> str:
    return absolute(f"{OPDS_PREFIX}/search.xml")


def search_opds(query: str, page: int = 1, per_page: int = 30) -> str:
    return absolute(
        f"{OPDS_PREFIX}/search",
        {"q": query, "page": page, "per_page": per_page},
    )


def book_entry(book_id: str) -> str:
    return absolute(f"{OPDS_PREFIX}/books/{book_id}")


def download(book_id: str) -> str:
    return absolute(f"{OPDS_PREFIX}/download/{book_id}")


def cover(book_id: str) -> str:
    return absolute(f"{OPDS_PREFIX}/cover/{book_id}")


def thumbnail(book_id: str) -> str:
    return absolute(f"{OPDS_PREFIX}/thumbnail/{book_id}")


def author_feed(name: str, page: int = 1, per_page: int = 30) -> str:
    return absolute(
        f"{OPDS_PREFIX}/authors/{quote(name, safe='')}",
        {"page": page, "per_page": per_page},
    )


def category_feed(slug: str, page: int = 1, per_page: int = 30) -> str:
    return absolute(f"{OPDS_PREFIX}/categories/{quote(slug, safe='')}", {"page": page, "per_page": per_page})


def series_feed(name: str, page: int = 1, per_page: int = 30) -> str:
    return absolute(f"{OPDS_PREFIX}/series/{quote(name, safe='')}", {"page": page, "per_page": per_page})


def tag_feed(name: str, page: int = 1, per_page: int = 30) -> str:
    return absolute(f"{OPDS_PREFIX}/tags/{quote(name, safe='')}", {"page": page, "per_page": per_page})


# --- OPDS 2.0 -------------------------------------------------------------
def v2_root() -> str:
    return absolute(f"{OPDS2_PREFIX}/")


def v2_path(path: str) -> str:
    return absolute(f"{OPDS2_PREFIX}/{path.lstrip('/')}")
