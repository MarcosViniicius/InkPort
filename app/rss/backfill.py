"""Per-feed retroactive import.

RSS only carries the latest posts, so pulling months/years back means reading the
site's ``sitemap.xml`` (see ``app/rss/archive.py``) and importing the posts that
fall inside the feed's configured window, up to the per-run cap.

``run_backfill`` runs on every feed pass while a window is configured: it imports
a bounded batch (``max_items_per_run``) and the rest is picked up next time. The
period is a rolling filter, so it keeps checking periodically instead of doing
everything once and stopping. The sitemap read is throttled (``MIN_CHECK_SECONDS``)
so a short feed interval does not hammer the site; the manual "Buscar
retroativos" bypasses the throttle. ``preview_backfill`` does the same discovery
without downloading anything, for the panel's dry run.
"""

from __future__ import annotations

import logging
from datetime import UTC

from app.database.models import Feed, FeedItem, FeedItemStatus, SourceKind, utcnow
from app.rss.archive import (
    import_one,
    known_urls,
    normalise_url,
    posts_within_days,
    sitemap_candidates,
)
from app.rss.downloader import DownloadError
from app.rss.naming import feed_category_name
from app.rss.sitemap import SitemapPost, collect_posts

logger = logging.getLogger(__name__)

#: Minimum time between sitemap reads when not forced, so a 5-minute feed
#: interval does not re-read the site's sitemap every run.
MIN_CHECK_SECONDS = 3600


def _empty_result() -> dict:
    return {
        "sitemap": "",
        "found": 0,
        "in_window": 0,
        "skipped": 0,
        "reachable": False,
        "error": None,
        "done": False,
    }


def run_backfill(session, feed: Feed, downloader, *, force: bool = False) -> dict:
    """Import the feed's older sitemap posts, in bounded batches. Never raises.

    Called on every feed pass while the window is configured: it keeps the
    period covered over time (a batch per pass), instead of doing it all at once
    or stopping forever after the first sweep. ``force`` ignores the minimum
    interval between sitemap reads (used by "Buscar retroativos").
    """
    result = _empty_result()
    result.update({"imported": 0, "queued": 0, "errors": 0})
    if not feed.backfill_days_total:
        return result
    if not force and not _check_due(feed):
        result["skipped_recent"] = True
        return result
    feed.backfill_checked_at = utcnow()
    session.commit()

    fetch = _make_fetch(downloader)
    candidates = sitemap_candidates(feed.url, feed.sitemap_url)
    if not candidates:
        result["error"] = "URL do feed inválida para montar o sitemap."
        return result

    try:
        discovery, todo = _discover(session, feed, fetch, candidates)
    except Exception:  # noqa: BLE001 - one feed must not break the run
        logger.exception("feed backfill failed", extra={"feed_id": feed.id})
        session.rollback()
        result["error"] = "Falha inesperada ao buscar os retroativos."
        return result

    result.update(discovery)
    result["imported"] = result["queued"] = result["errors"] = 0
    imported_before = feed.backfill_imported or 0

    if result.get("error"):
        return result
    if result["found"] == 0:
        # Sem posts datados: ou o site não usa data na URL, ou o sitemap está
        # vazio. Só marca "em dia" quando o sitemap respondeu.
        feed.backfill_total = imported_before
        session.commit()
        if result["reachable"]:
            result["done"] = _mark_done(session, feed)
        return result

    # The window's size for this feed right now: what it already imported plus
    # what is still pending. The panel shows imported/total.
    feed.backfill_total = imported_before + len(todo)
    if not todo:
        result["done"] = _mark_done(session, feed)
        return result

    budget = feed.backfill_per_run_effective
    batch = todo[:budget]
    category = feed_category_name(feed)
    target = feed.output_format or "epub"
    for post in batch:
        try:
            book = import_one(
                session,
                post,
                downloader=downloader,
                category=category,
                output_format=target,
                device_profile=feed.device_profile or "generic_epub",
                keep_original=feed.keep_original,
                source_kind=SourceKind.RSS.value,
            )
        except DownloadError as exc:
            result["errors"] += 1
            logger.warning("backfill download failed", extra={"url": post.url, "error": str(exc)})
            continue
        except Exception:  # noqa: BLE001
            result["errors"] += 1
            logger.exception("backfill post failed", extra={"url": post.url})
            continue
        if book is None:
            result["errors"] += 1
            continue
        item = FeedItem(feed_id=feed.id, guid=post.url, url=post.url, title=book.title)
        item.status = FeedItemStatus.DOWNLOADED.value
        item.book_id = book.id
        session.add(item)
        result["imported"] += 1
        result["queued"] += 1

    feed.backfill_imported = imported_before + result["imported"]
    session.commit()
    if len(todo) <= len(batch):
        result["done"] = _mark_done(session, feed)
    else:
        logger.info(
            "feed backfill continues on the next run",
            extra={"feed_id": feed.id, "left": len(todo) - len(batch)},
        )
    return result


def preview_backfill(
    session, feed: Feed, downloader, *, sample: int = 30
) -> dict:
    """Dry run: what the retroactive pull would import, without downloading."""
    result = _empty_result()
    result["sample"] = []
    result["more"] = 0
    if not feed.backfill_days_total:
        result["error"] = "Retroativos desligados."
        return result

    candidates = sitemap_candidates(feed.url, feed.sitemap_url)
    if not candidates:
        result["error"] = "URL do feed inválida para montar o sitemap."
        return result

    try:
        result, todo = _discover(session, feed, _make_fetch(downloader), candidates)
    except Exception:  # noqa: BLE001 - a preview must never explode
        logger.exception("feed backfill preview failed", extra={"feed_id": feed.id})
        result["error"] = "Falha inesperada ao ler o sitemap."
        return result

    result["new"] = len(todo)
    result["sample"] = [
        {"title": post.title, "published": post.published, "url": post.url}
        for post in todo[:sample]
    ]
    result["more"] = max(0, len(todo) - sample)
    return result


def _make_fetch(downloader):
    """Cached fetcher: the root sitemap is read once even across candidates."""
    cache: dict[str, bytes] = {}

    def fetch(url: str) -> bytes:
        if url not in cache:
            cache[url] = downloader.fetch(url)
        return cache[url]

    return fetch


def _discover(session, feed: Feed, fetch, candidates: list[str]) -> tuple[dict, list[SitemapPost]]:
    """Locate the sitemap, filter the window and dedup; imports nothing."""
    result = _empty_result()
    posts: list[SitemapPost] = []
    errors: list[str] = []
    for candidate in candidates:
        try:
            fetch(candidate)
        except DownloadError as exc:
            errors.append(str(exc))
            continue
        result["reachable"] = True
        found = collect_posts(fetch, candidate, max_sitemaps=60)
        if found:
            posts = found
            result["sitemap"] = candidate
            break

    if not posts:
        result["sitemap"] = candidates[0]
        if not result["reachable"]:
            result["error"] = errors[0] if errors else "Não encontrei o sitemap do site."
        return result, []

    result["found"] = len(posts)
    window = posts_within_days(posts, feed.backfill_days_total)
    result["in_window"] = len(window)

    known = known_urls(session)
    seen = _seen_guids(session, feed.id)
    todo = [
        post
        for post in window
        if normalise_url(post.url) not in known and post.url not in seen
    ]
    result["skipped"] = len(window) - len(todo)
    return result, todo


def _check_due(feed: Feed) -> bool:
    """True when enough time passed since the last sitemap read."""
    last = feed.backfill_checked_at
    if last is None:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    return (utcnow() - last).total_seconds() >= MIN_CHECK_SECONDS


def _mark_done(session, feed: Feed) -> bool:
    """Record that the window is caught up right now, and persist it.

    Informational only: the next feed pass looks again, because the period is a
    rolling window and the feed run is the natural cadence to keep it covered.
    """
    feed.backfill_done_at = utcnow()
    session.commit()
    return True


def _seen_guids(session, feed_id: int) -> set[str]:
    from sqlalchemy import select

    return set(
        session.scalars(select(FeedItem.guid).where(FeedItem.feed_id == feed_id)).all()
    )
