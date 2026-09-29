"""Processing one RSS/Atom feed: fetch, dedup, download, import, convert.

Robust across feed flavours:

* **file enclosure** (EPUB/PDF/CBZ...) -> imported as a book;
* **article inline in the feed** -> used directly (no page fetch, so paywalls,
  bot blocking and JavaScript-only pages do not break it);
* **article behind a link** -> downloaded; if the page comes back thin or the
  download fails, the inline content is used instead;
* **audio (podcast)** -> skipped with a clear message instead of an error.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.database import session_scope
from app.database.models import Feed, FeedItem, FeedItemStatus, SourceKind, utcnow
from app.library import repository
from app.library.conversions import enqueue_conversion
from app.library.importer import import_file
from app.rss.downloader import Downloader, DownloadError
from app.rss.parser import FeedEntry, parse_feed
from app.storage.temp import temp_workdir

logger = logging.getLogger(__name__)

#: Feed ids being fetched right now (the panel shows a "buscando…" badge).
_RUNNING: set[int] = set()


def feeds_in_progress() -> set[int]:
    return set(_RUNNING)


@dataclass(slots=True)
class FeedReport:
    feed_id: int
    feed_name: str
    fetched: int = 0
    downloaded: int = 0
    imported: int = 0
    queued: int = 0
    skipped: int = 0
    articles: int = 0
    backfilled: int = 0
    backfill_done: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict:
        return {
            "feed_id": self.feed_id,
            "feed_name": self.feed_name,
            "fetched": self.fetched,
            "downloaded": self.downloaded,
            "imported": self.imported,
            "queued": self.queued,
            "skipped": self.skipped,
            "articles": self.articles,
            "backfilled": self.backfilled,
            "backfill_done": self.backfill_done,
            "errors": self.errors,
        }


@dataclass(slots=True)
class _Source:
    path: Path
    page_url: str
    content_type: str
    from_inline: bool = False

    @property
    def is_article(self) -> bool:
        return self.content_type.startswith(("text/html", "application/xhtml")) or self.path.suffix.lower() in {
            ".html", ".htm", ".xhtml",
        }

    @property
    def is_audio(self) -> bool:
        return self.content_type.startswith("audio/")


def reset_feed(session: Session, feed: Feed, *, remove_books: bool = False) -> dict:
    """Forget processed items so the next run imports them again.

    With ``remove_books`` it also deletes the books this feed imported (and the
    conversions produced from them, plus their files). That is what makes
    "reprocess everything" rebuild the articles instead of piling duplicates on
    top of the old ones.
    """
    from app.database.models import Book
    from app.library.service import delete_books

    items = list(session.scalars(select(FeedItem).where(FeedItem.feed_id == feed.id)).all())
    book_ids = {item.book_id for item in items if item.book_id}
    removed_items = len(items)
    removed_books = 0

    if remove_books and book_ids:
        originals = list(session.scalars(select(Book).where(Book.id.in_(book_ids))).all())
        conversions = list(
            session.scalars(select(Book).where(Book.origin_book_id.in_(book_ids))).all()
        )
        unique = list({book.id: book for book in [*originals, *conversions]}.values())
        if unique:
            removed_books = delete_books(session, unique, delete_files=True)

    for item in items:
        session.delete(item)
    feed.last_checked_at = None
    session.commit()
    return {"items": removed_items, "books": removed_books}


def process_feed_by_id(feed_id: int) -> FeedReport | None:
    """Process a feed using its own session.

    Safe to call from a worker thread: a SQLAlchemy Session belongs to the
    thread that created it, so routes must never pass theirs across threads.
    """
    with session_scope() as session:
        feed = session.get(Feed, feed_id)
        if feed is None:
            return None
        _RUNNING.add(feed_id)
        try:
            return process_feed(session, feed)
        finally:
            _RUNNING.discard(feed_id)


def process_feed(session: Session, feed: Feed, downloader: Downloader | None = None) -> FeedReport:
    report = FeedReport(feed_id=feed.id, feed_name=feed.name)
    own_downloader = downloader is None
    downloader = downloader or Downloader()

    try:
        try:
            payload = downloader.fetch(feed.url)
            parsed = parse_feed(payload, feed.url)
        except DownloadError as exc:
            _record_failure(session, feed, str(exc))
            report.errors.append(str(exc))
            return report

        if parsed.error:
            _record_failure(session, feed, parsed.error)
            report.errors.append(parsed.error)
            return report

        feed.last_checked_at = utcnow()
        feed.last_success_at = utcnow()
        feed.last_error = None
        feed.consecutive_failures = 0
        session.commit()

        report.fetched = len(parsed.entries)
        category_name = _category_name(session, feed)
        known = _known_guids(session, feed.id)

        # The cap is "how many *new* items this run handles", not a slice of the
        # feed. Slicing first was a bug: a feed carrying more entries than the
        # cap would only ever import the first page, and the older entries stayed
        # unknown forever no matter how often the feed ran.
        processed = 0
        for entry in parsed.entries:
            if entry.guid in known:
                report.skipped += 1
                continue
            if processed >= feed.max_items_per_run:
                remaining = sum(1 for other in parsed.entries if other.guid not in known) - processed
                logger.info(
                    "feed has more new items than the per-run limit",
                    extra={
                        "feed_id": feed.id,
                        "feed_name": feed.name,
                        "handled": processed,
                        "left_for_next_run": max(0, remaining),
                    },
                )
                break
            _process_entry(session, feed, entry, downloader, category_name, report)
            processed += 1

        session.commit()  # persist the RSS work before the archive pass

        # Retroactive pull (sitemap): only while there is something left in the
        # configured window. Runs a bounded batch per call so a huge archive is
        # consumed across runs instead of stalling one feed run for hours. Kept
        # after the commit above so a backfill failure cannot discard the RSS work.
        if feed.backfill_months and feed.backfill_done_at is None:
            from app.rss.backfill import run_backfill

            back = run_backfill(session, feed, downloader)
            report.backfilled = back.get("imported", 0)
            report.backfill_done = bool(back.get("done"))
            if back.get("error"):
                logger.info(
                    "feed backfill could not run",
                    extra={"feed_id": feed.id, "error": back["error"]},
                )
            session.commit()
    except StaleDataError:
        # The feed was deleted while this run was in flight (the panel and the
        # worker share the database, and feed searches can take minutes).
        logger.info(
            "feed disappeared during processing",
            extra={"feed_id": report.feed_id, "feed_name": report.feed_name},
        )
        session.rollback()
        return report
    finally:
        if own_downloader:
            downloader.close()

    logger.info("feed processed", extra={**report.as_dict(), "feed_id": feed.id})
    return report


def _process_entry(session, feed, entry: FeedEntry, downloader, category_name, report: FeedReport) -> None:
    item = FeedItem(feed_id=feed.id, guid=entry.guid, url=entry.url, title=entry.title)
    session.add(item)
    session.flush()

    # Podcast: identified from the enclosure, without downloading the audio.
    if entry.is_audio:
        _skip(item, "Item de áudio (podcast) — não suportado.", report)
        return

    try:
        with temp_workdir("rss_") as workdir:
            source = _obtain_source(downloader, entry, workdir)

            if source is None:
                _skip(item, "Item sem URL nem conteúdo aproveitável.", report)
                return

            if source.is_audio:
                _skip(item, "Item de áudio (podcast) — não suportado.", report)
                return

            digest = downloader.hash(source.path)
            item.content_hash = digest
            report.downloaded += 1

            if repository.find_by_hash(session, digest) is not None:
                _skip(item, "Conteúdo já existe na biblioteca.", report)
                return

            if source.is_article:
                report.articles += 1

            outcome = import_file(
                session,
                source.path,
                category=category_name,
                source=SourceKind.RSS.value,
                source_url=source.page_url or entry.url,
                source_id=entry.guid,
                move=True,
                title_override=entry.title,
            )

            if outcome.status != "imported" or outcome.book is None:
                _skip(item, outcome.message, report)
                return

            book = outcome.book
            item.status = FeedItemStatus.DOWNLOADED.value
            item.book_id = book.id
            report.imported += 1

            target = feed.output_format or _default_target(source)
            if target:
                enqueue_conversion(
                    session,
                    book,
                    target_format=target,
                    device_profile=feed.device_profile,
                    keep_original=feed.keep_original,
                )
                report.queued += 1
    except DownloadError as exc:
        item.status = FeedItemStatus.ERROR.value
        item.error = str(exc)
        report.errors.append(f"{entry.title or entry.guid}: {exc}")
    except Exception as exc:  # noqa: BLE001 - one entry must not kill the feed
        logger.exception("feed entry failed", extra={"guid": entry.guid})
        item.status = FeedItemStatus.ERROR.value
        item.error = str(exc)
        report.errors.append(f"{entry.title or entry.guid}: {exc}")


# ---------------------------------------------------------------------------
# Source selection
# ---------------------------------------------------------------------------
def _obtain_source(downloader, entry: FeedEntry, workdir: Path) -> _Source | None:
    """Pick the content for an entry, preferring what the feed already carries.

    Exception: when the feed's copy carries **no image at all** and the entry has
    a page, the page is fetched too. Charts and screenshots often live only on the
    page (a Hugo summary, a feed that strips figures), and an article converted
    without them loses part of its content. Any problem keeps the inline copy.
    """
    inline = entry.inline_html
    page_url = entry.url or ""

    if entry.has_inline_article and not _is_file_target(entry):
        if entry.url and not _has_images(inline):
            from_page = _try_page_with_images(downloader, entry, workdir, inline)
            if from_page is not None:
                return from_page
        return _write_inline(inline, page_url, workdir)

    if entry.url:
        try:
            downloaded = downloader.download(entry.url, workdir)
        except DownloadError:
            if inline:
                logger.info("page download failed; using inline content", extra={"url": entry.url})
                return _write_inline(inline, page_url, workdir)
            raise

        if (
            inline
            and (not downloaded.content_type or downloaded.content_type.startswith(("text/html", "application/xhtml")))
            and _page_is_thin(downloaded.path, inline)
        ):
            logger.info("page looks thin; using inline content", extra={"url": entry.url})
            return _write_inline(inline, page_url, workdir)

        return _Source(
            path=downloaded.path,
            page_url=page_url,
            content_type=downloaded.content_type,
        )

    if inline:
        return _write_inline(inline, page_url, workdir)

    return None


def _is_file_target(entry: FeedEntry) -> bool:
    mime = (entry.content_type or "").lower()
    return bool(mime) and not mime.startswith(("text/html", "application/xhtml"))


#: How much of a page is scanned when looking for images.
_SCAN_BYTES = 512 * 1024


def _has_images(html: str | None) -> bool:
    if not html:
        return False
    return re.search(r"<img\b", html, re.IGNORECASE) is not None


def _try_page_with_images(downloader, entry: FeedEntry, workdir: Path, inline: str) -> _Source | None:
    """The page, when it has images the inline copy lacks (else ``None``)."""
    try:
        downloaded = downloader.download(entry.url or "", workdir)
    except DownloadError:
        return None
    mime = (downloaded.content_type or "").lower()
    if mime and not mime.startswith(("text/html", "application/xhtml")):
        return None  # the "page" is actually a file (handled elsewhere)
    try:
        with downloaded.path.open("rb") as handle:
            head = handle.read(_SCAN_BYTES).decode("utf-8", "replace")
    except OSError:
        return None
    if not _has_images(head):
        return None
    if _page_is_thin(downloaded.path, inline):
        return None  # a stub (paywall, JS-only): the feed copy is better
    logger.info(
        "inline content had no images; using the page instead",
        extra={"url": entry.url, "title": entry.title},
    )
    return _Source(
        path=downloaded.path, page_url=entry.url or "", content_type=downloaded.content_type
    )


#: Content-type prefixes -> preview kind.
_KIND_BY_MIME = (
    (("application/pdf",), "pdf"),
    (("application/epub", "application/x-mobipocket", "application/vnd.amazon"), "ebook"),
    (("image/",), "image"),
    (("application/zip", "application/x-cbz", "application/vnd.comicbook"), "comic"),
)


def classify_entry(entry: FeedEntry) -> str:
    """What would happen to this entry, without downloading anything."""
    if entry.is_audio:
        return "podcast"
    mime = (entry.content_type or "").lower()
    if mime:
        for prefixes, kind in _KIND_BY_MIME:
            if mime.startswith(prefixes):
                return kind
        if not mime.startswith(("text/html", "application/xhtml")):
            return "file"
    return "article"


@dataclass(slots=True)
class FeedPreview:
    """A dry run of a feed URL: what it is and what it would import."""

    ok: bool = False
    error: str | None = None
    title: str | None = None
    total: int = 0
    kinds: dict[str, int] = field(default_factory=dict)
    sample: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "error": self.error,
            "title": self.title,
            "total": self.total,
            "kinds": self.kinds,
            "sample": self.sample,
        }


def preview_feed(url: str, *, sample: int = 10, scan_limit: int = 200) -> FeedPreview:
    """Fetch and parse a feed without importing anything."""
    downloader = Downloader()
    try:
        try:
            payload = downloader.fetch(url)
        except DownloadError as exc:
            return FeedPreview(ok=False, error=str(exc))
        parsed = parse_feed(payload, url)
    finally:
        downloader.close()

    if parsed.error:
        return FeedPreview(ok=False, error=parsed.error)

    kinds: Counter[str] = Counter()
    rows: list[dict] = []
    for entry in parsed.entries[:scan_limit]:
        kind = classify_entry(entry)
        kinds[kind] += 1
        if len(rows) < sample:
            rows.append(
                {
                    "title": entry.title or entry.url or "(sem título)",
                    "kind": kind,
                    "url": entry.url,
                    "published": entry.published,
                }
            )
    return FeedPreview(
        ok=True,
        title=parsed.title,
        total=len(parsed.entries),
        kinds=dict(kinds),
        sample=rows,
    )


def _write_inline(html: str, page_url: str, workdir: Path) -> _Source:
    path = workdir / "article.html"
    # A declaration saves the file from being guessed as Latin-1 further down
    # the line (lxml, Calibre and friends).
    marker = '<meta charset="utf-8">'
    if "charset" not in html[:1024].lower():
        if "<head" in html.lower():
            html = re.sub(r"(<head[^>]*>)", r"\1" + marker, html, count=1, flags=re.IGNORECASE)
        else:
            html = marker + html
    path.write_text(html, encoding="utf-8", errors="replace")
    return _Source(path=path, page_url=page_url, content_type="text/html", from_inline=True)


def _page_is_thin(path: Path, inline: str) -> bool:
    """True when the fetched page is clearly a stub (blocked, paywall, JS-only)."""
    try:
        size = path.stat().st_size
    except OSError:
        return True
    return size < max(2000, int(len(inline) * 0.4))


def _default_target(source: _Source) -> str:
    if source.is_article:
        return "epub"
    return ""


def _skip(item: FeedItem, message: str, report: FeedReport) -> None:
    item.status = FeedItemStatus.SKIPPED.value
    item.error = message
    report.skipped += 1


def _known_guids(session: Session, feed_id: int) -> set[str]:
    rows = session.scalars(select(FeedItem.guid).where(FeedItem.feed_id == feed_id)).all()
    return set(rows)


def _category_name(session: Session, feed: Feed) -> str:
    """Every feed's books go to ``rss/<feed name>`` (see ``rss/naming.py``)."""
    from app.rss.naming import feed_category_name

    return feed_category_name(feed)


def _record_failure(session: Session, feed: Feed, message: str) -> None:
    feed.last_checked_at = utcnow()
    feed.last_error = message[:2000]
    feed.consecutive_failures = (feed.consecutive_failures or 0) + 1
    session.commit()
