"""Data repairs run at startup and on demand.

These fix rows written by older versions of the app. They are idempotent and
cheap, so they run on boot, on the periodic maintenance tick and from the panel.
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import Book
from app.devices.builtin import BUILTIN_PROFILES
from app.library.formats import media_type_for

logger = logging.getLogger(__name__)


def repair_media_types(session: Session) -> int:
    """Fix stored MIME types that do not match their format.

    A generic container type (e.g. ``application/zip`` for an EPUB) makes strict
    OPDS clients hide the book, because they match the type exactly.
    """
    fixed = 0
    for book in session.scalars(select(Book)).all():
        expected = media_type_for(book.format, book.media_type)
        if book.media_type != expected:
            book.media_type = expected
            fixed += 1
    if fixed:
        session.commit()
    return fixed


def repair_conversion_authors(session: Session) -> int:
    """Clear authors that are actually a device-profile slug.

    Converted files used to be named ``Title (profile_slug).epub``; the filename
    parser read the parenthesis as an author, so the catalog showed
    "manga_epub" as the author of the book.
    """
    slugs = {slug.lower() for slug in BUILTIN_PROFILES}
    fixed = 0
    for book in session.scalars(
        select(Book).where(Book.is_original.is_(False))
    ).all():
        if not book.author or book.author.lower() not in slugs:
            continue
        origin = session.get(Book, book.origin_book_id) if book.origin_book_id else None
        book.author = origin.author if origin else None
        fixed += 1
    if fixed:
        session.commit()
    return fixed


def repair_feed_item_links(session: Session) -> int:
    """Re-link feed items to the book a previous run produced.

    When ``keep_original`` is off, deleting the imported HTML used to null the
    item's ``book_id``, so "reprocess" could not find (and replace) the previous
    books. This links items back by title when the match is unambiguous.
    """
    from app.database.models import FeedItem

    items = list(
        session.scalars(
            select(FeedItem).where(FeedItem.book_id.is_(None), FeedItem.title.is_not(None))
        ).all()
    )
    if not items:
        return 0

    books = list(
        session.scalars(select(Book).where(Book.source.in_(("rss", "import")))).all()
    )
    by_title: dict[str, list[Book]] = {}
    for book in books:
        by_title.setdefault((book.title or "").strip(), []).append(book)

    fixed = 0
    for item in items:
        matches = by_title.get((item.title or "").strip(), [])
        if len(matches) == 1:
            item.book_id = matches[0].id
            fixed += 1
    if fixed:
        session.commit()
    return fixed


def apply_feed_categories(session: Session) -> int:
    """Move every book imported from a feed into ``rss/<feed>``.

    Older imports (before the rule existed) had no category, so this fixes the
    classification and the folder layout as well.
    """
    from app.database.models import Feed, FeedItem
    from app.library.service import move_to_category
    from app.rss.naming import feed_category_name

    feeds = {feed.id: feed for feed in session.scalars(select(Feed)).all()}
    if not feeds:
        return 0

    changed = 0
    items = session.scalars(select(FeedItem).where(FeedItem.book_id.is_not(None))).all()
    for item in items:
        feed = feeds.get(item.feed_id)
        book = session.get(Book, item.book_id) if item.book_id else None
        if feed is None or book is None:
            continue
        target = feed_category_name(feed)
        current = book.category_rel.name if book.category_rel else None
        if current == target:
            continue
        move_to_category(session, book, target)
        changed += 1
    return changed


def repair_missing_covers(session: Session) -> int:
    """Give a typographic cover to books that have none.

    Text EPUBs (articles, novels) carry no image, so the library grid and the
    OPDS thumbnails would be empty.
    """
    from app.config import get_settings
    from app.metadata.cover import generate_title_cover

    settings = get_settings()
    fixed = 0
    for book in session.scalars(select(Book).where(Book.cover_path.is_(None))).all():
        try:
            cover = generate_title_cover(book.title, book.author, settings.covers_dir)
        except Exception:  # pragma: no cover - never break maintenance
            continue
        book.cover_path = cover.name
        book.cover_hash = cover.stem
        fixed += 1
    if fixed:
        session.commit()
    return fixed


def cleanup_orphan_covers(session: Session) -> int:
    """Delete cover/thumbnail files no book references any more."""
    from app.config import get_settings

    covers = get_settings().covers_dir
    if not covers.exists():
        return 0

    used_names = {
        row[0]
        for row in session.execute(select(Book.cover_path).where(Book.cover_path.is_not(None)))
        if row[0]
    }
    used_stems = {Path(name).stem for name in used_names}

    removed = 0
    for path in covers.iterdir():
        if not path.is_file() or path.name in used_names:
            continue
        if path.name.startswith("thumb_"):
            base = path.name[len("thumb_"):].rsplit("_", 1)[0]
            if base in used_stems:
                continue
        try:
            path.unlink(missing_ok=True)
            removed += 1
        except OSError:
            continue
    return removed


def repair_epub_image_paths(session: Session) -> int:
    """Fix image links written before the ``../images`` correction.

    A chapter in ``OEBPS/text/`` used to point at ``images/x.jpg``, which
    resolves to ``OEBPS/text/images/x.jpg``: a broken image in every reader.
    The file is rewritten in place (nothing is re-downloaded).
    """
    from app.storage.paths import resolve_library_path

    fixed = 0
    for book in session.scalars(select(Book).where(Book.format == "epub")):
        if book.missing and not book.file_path:
            continue
        try:
            path = resolve_library_path(book.file_path)
        except (ValueError, OSError):
            continue
        if not path.is_file():
            continue
        if _fix_epub_image_paths(path):
            fixed += 1
    return fixed


def _fix_epub_image_paths(path: Path) -> bool:
    """Rewrite one EPUB's chapter references (returns True when it changed)."""
    import re
    import zipfile

    payloads: dict[str, bytes] = {}
    with zipfile.ZipFile(path) as source:
        names = source.namelist()
        if "META-INF/container.xml" not in names:
            return False
        for name in names:
            if not re.search(r"(^|/)text/[^/]+\.x?html$", name):
                continue
            text = source.read(name).decode("utf-8", "replace")
            fixed = text.replace('src="images/', 'src="../images/')
            if fixed != text:
                payloads[name] = fixed.encode("utf-8")
        if not payloads:
            return False

        temp = path.with_name(path.name + ".tmp")
        with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED) as target:
            for name in names:
                info = source.getinfo(name)
                if name in payloads:
                    target.writestr(name, payloads[name], compress_type=zipfile.ZIP_DEFLATED)
                else:
                    target.writestr(
                        info, source.read(name), compress_type=info.compress_type
                    )
    path.unlink(missing_ok=True)
    temp.replace(path)
    return True


def run_repairs(session: Session) -> dict[str, int]:
    """Run every repair; returns a small report."""
    report = {
        "media_types_fixed": repair_media_types(session),
        "authors_fixed": repair_conversion_authors(session),
        "feed_links_fixed": repair_feed_item_links(session),
        "feed_categories_fixed": apply_feed_categories(session),
        "epub_images_fixed": repair_epub_image_paths(session),
        "covers_generated": repair_missing_covers(session),
        "orphan_covers_removed": cleanup_orphan_covers(session),
    }
    if any(report.values()):
        logger.info("library repairs applied", extra=report)
    return report
