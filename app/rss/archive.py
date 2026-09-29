"""Shared "site archive" import: sitemap posts -> books.

Used by the ``import_site`` CLI and by the per-feed backfill (``backfill.py``).
Both walk a sitemap and import each post through the normal download + import +
conversion pipeline, so the logic lives here once.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from urllib.parse import urlparse

from app.database.models import Book, SourceKind
from app.library.conversions import enqueue_conversion
from app.library.importer import import_file
from app.rss.sitemap import SitemapPost

logger = logging.getLogger(__name__)


def normalise_url(url: str | None) -> str:
    """Host (without ``www``) + path, so ``/a/`` and ``/a`` match."""
    if not url:
        return ""
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower().removeprefix("www.")
    path = (parsed.path or "/").rstrip("/")
    return f"{host}{path}"


def known_urls(session) -> set[str]:
    """URLs the library already holds, by source URL or source id.

    Only *books* count: a feed item whose file was deleted must not block a
    re-import, otherwise the archive could never be pulled again.
    """
    from sqlalchemy import select

    urls: set[str] = set()
    for column in (Book.source_url, Book.source_id):
        for value in session.scalars(select(column).where(column.is_not(None))):
            urls.add(normalise_url(value))
    urls.discard("")
    return urls


def sitemap_candidates(feed_url: str, override: str = "") -> list[str]:
    """Sitemaps to try, in order.

    An explicit override wins. Otherwise the common locations are tried, because
    WordPress serves ``/wp-sitemap.xml`` and Yoast ``/sitemap_index.xml``.
    """
    override = (override or "").strip()
    if override:
        return [override]
    parsed = urlparse(feed_url or "")
    if not parsed.scheme or not parsed.netloc:
        return []
    root = f"{parsed.scheme}://{parsed.netloc}"
    return [f"{root}/sitemap.xml", f"{root}/sitemap_index.xml", f"{root}/wp-sitemap.xml"]


def post_date(post: SitemapPost) -> date:
    """Date used to place a post in the time window (month/day may be absent)."""
    return date(post.year, post.month or 1, post.day or 1)


def window_start(months: int, today: date | None = None) -> date | None:
    """First day included for ``months``; ``-1`` (whole archive) -> ``None``."""
    if months < 0:
        return None
    today = today or date.today()
    if months == 0:
        return today
    total = today.year * 12 + (today.month - 1) - months
    year, month0 = divmod(total, 12)
    return date(year, month0 + 1, 1)


def posts_within(
    posts: list[SitemapPost], months: int, *, today: date | None = None
) -> list[SitemapPost]:
    """Posts published in the last ``months`` (0 = none, -1 = everything)."""
    return posts_within_days(posts, months * 30, today=today)


def window_start_days(days: int, today: date | None = None) -> date | None:
    """First day included for ``days``; ``-1`` (whole archive) -> ``None``."""
    if days < 0:
        return None
    today = today or date.today()
    if days == 0:
        return today
    return today - timedelta(days=days)


def posts_within_days(
    posts: list[SitemapPost], days: int, *, today: date | None = None
) -> list[SitemapPost]:
    """Posts published in the last ``days`` (0 = none, -1 = everything)."""
    if days == 0:
        return []
    start = window_start_days(days, today)
    if start is None:
        return list(posts)
    return [post for post in posts if post_date(post) >= start]


def import_one(
    session,
    post: SitemapPost,
    *,
    downloader,
    category: str,
    output_format: str,
    device_profile: str,
    keep_original: bool,
    source_kind: str = SourceKind.IMPORT.value,
) -> Book | None:
    """Download one post and import it; returns the book, or ``None`` if not imported."""
    from app.storage.temp import temp_workdir

    with temp_workdir("sitemap_") as workdir:
        downloaded = downloader.download(post.url, workdir)
        outcome = import_file(
            session,
            downloaded.path,
            category=category,
            source=source_kind,
            source_url=post.url,
            source_id=post.url,
            move=True,
            title_override=post.title,
        )
        if outcome.status != "imported" or outcome.book is None:
            logger.info(
                "archive post not imported",
                extra={"url": post.url, "status": outcome.status},
            )
            return None
        book = outcome.book
        book.published = post.published
        if output_format:
            enqueue_conversion(
                session,
                book,
                target_format=output_format,
                device_profile=device_profile,
                keep_original=keep_original,
            )
        logger.info(
            "archive post imported",
            extra={"url": post.url, "title": book.title, "year": post.year},
        )
        return book


def import_posts(
    posts: list[SitemapPost],
    *,
    downloader,
    category: str,
    output_format: str,
    device_profile: str,
    keep_original: bool,
    limit: int = 0,
    dry_run: bool = False,
    on_progress=None,
) -> dict:
    """Download, import and queue the conversion of each post (CLI path)."""
    from app.database import session_scope
    from app.rss.downloader import DownloadError

    stats = {"considered": len(posts), "imported": 0, "queued": 0, "skipped": 0, "errors": 0}

    with session_scope() as session:
        known = known_urls(session)

    todo = [post for post in posts if normalise_url(post.url) not in known]
    stats["skipped"] = len(posts) - len(todo)
    if limit:
        todo = todo[:limit]
    stats["todo"] = len(todo)
    if dry_run:
        return stats

    for index, post in enumerate(todo, start=1):
        if on_progress:
            on_progress(index, len(todo), post)
        try:
            with session_scope() as session:
                book = import_one(
                    session,
                    post,
                    downloader=downloader,
                    category=category,
                    output_format=output_format,
                    device_profile=device_profile,
                    keep_original=keep_original,
                )
                if book is None:
                    stats["errors"] += 1
                    continue
                if output_format:
                    stats["queued"] += 1
                stats["imported"] += 1
        except DownloadError as exc:
            stats["errors"] += 1
            logger.warning("archive download failed", extra={"url": post.url, "error": str(exc)})
        except Exception:  # noqa: BLE001 - one post must not stop the archive
            stats["errors"] += 1
            logger.exception("archive post failed", extra={"url": post.url})
    return stats
