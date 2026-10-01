"""Data repairs run at startup and on demand.

These fix rows written by older versions of the app. They are idempotent and
cheap, so they run on boot, on the periodic maintenance tick and from the panel.
"""

from __future__ import annotations

import logging
import posixpath
import re
import zipfile
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
    """Fix broken local references inside EPUB files, in place.

    Readers resolve ``src``/``href`` relative to the *file* that contains them,
    but generators (ours in older versions, and third parties) sometimes write
    paths relative to the OPF or another folder -- e.g. ``cover.xhtml`` at the
    OEBPS root pointing at ``../images/cover.jpg``, which lands outside the
    container. When the literal target is missing we try the OPF-relative
    reading and rewrite the reference. Nothing is re-downloaded; idempotent.
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
        try:
            if _fix_epub_image_paths(path):
                fixed += 1
        except (OSError, zipfile.BadZipFile):
            # A mislabelled/corrupt .epub must not break the boot repairs.
            continue
    return fixed


_REF_RE = re.compile(r'(?P<attr>\b(?:src|href)=")(?P<value>[^"]+)(?P<quote>")')
_HTML_SUFFIX_RE = re.compile(r"\.x?html?$", re.IGNORECASE)
_ANCHOR_RE = re.compile(rb'(?:id|name)="([^"]+)"')
_EXTERNAL_PREFIXES = ("http://", "https://", "data:", "mailto:", "#", "javascript:")


def _fix_epub_image_paths(path: Path) -> bool:
    """Rewrite broken local references in one EPUB (returns True when changed)."""
    payloads: dict[str, bytes] = {}
    with zipfile.ZipFile(path) as source:
        names = set(source.namelist())
        if "META-INF/container.xml" not in names:
            return False
        opf_dir = _opf_dir(source, names)
        if opf_dir is None:
            return False
        anchors = _anchor_index(source, names)
        for name in names:
            if not _HTML_SUFFIX_RE.search(name):
                continue
            try:
                text = source.read(name).decode("utf-8")
            except (KeyError, UnicodeDecodeError):
                continue
            fixed = _rewrite_refs(text, name, names, opf_dir, anchors)
            if fixed != text:
                payloads[name] = fixed.encode("utf-8")
        if not payloads:
            return False

        temp = path.with_name(path.name + ".tmp")
        with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED) as target:
            for name in source.namelist():
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


def _opf_dir(source: zipfile.ZipFile, names: set[str]) -> str | None:
    """Folder that holds the OPF package (the base many generators assume)."""
    match = re.search(rb'full-path="([^"]+)"', source.read("META-INF/container.xml"))
    if not match:
        return None
    opf = match.group(1).decode("utf-8", "replace").lstrip("/")
    if opf not in names:
        return None
    return posixpath.dirname(opf)


def _anchor_index(source: zipfile.ZipFile, names: set[str]) -> dict[str, str]:
    """Map anchor id -> the XHTML entry that declares it.

    Used to rescue internal links whose target file was merged away (the
    anchor usually survives in another chapter of the same book).
    """
    index: dict[str, str] = {}
    for name in names:
        if not _HTML_SUFFIX_RE.search(name):
            continue
        try:
            raw = source.read(name)
        except KeyError:
            continue
        for match in _ANCHOR_RE.finditer(raw):
            index.setdefault(match.group(1).decode("utf-8", "replace"), name)
    return index


def _rewrite_refs(
    text: str, entry: str, names: set[str], opf_dir: str, anchors: dict[str, str]
) -> str:
    entry_dir = posixpath.dirname(entry)

    def repl(match: re.Match[str]) -> str:
        value = match.group("value")
        if not value or value.startswith(_EXTERNAL_PREFIXES):
            return match.group(0)
        path, _, fragment = value.partition("#")
        if not path:
            return match.group(0)
        target = path.lstrip("/")
        if posixpath.normpath(posixpath.join(entry_dir, target)) in names:
            return match.group(0)
        # Internal cross-reference whose file was merged away: point it at the
        # chapter that really holds the anchor (#anchor when it is here).
        if fragment:
            owner = anchors.get(fragment)
            if owner == entry:
                return f'{match.group("attr")}#{fragment}{match.group("quote")}'
            if owner:
                relative = posixpath.relpath(owner, entry_dir)
                return f'{match.group("attr")}{relative}#{fragment}{match.group("quote")}'
        # "squash" leading "../" away: the reference was written as if the file
        # lived one folder deeper (a common generator slip).
        squashed = target
        while squashed.startswith("../"):
            squashed = squashed[3:]
        candidate = posixpath.normpath(posixpath.join(opf_dir, squashed))
        if candidate in names:
            new_value = posixpath.relpath(candidate, entry_dir)
            if fragment:
                new_value = f"{new_value}#{fragment}"
            return f"{match.group('attr')}{new_value}{match.group('quote')}"
        return match.group(0)

    return _REF_RE.sub(repl, text)


def repair_app_name(session: Session) -> int:
    """Rename the stored app name on rebrand (one-time, opt-out by customizing).

    Installations that saved settings when the project was called "OPDS Server"
    keep showing the old name, because the database overrides the code default.
    Only the exact old default is migrated; a custom name is left alone.
    """
    from app.security import runtime, settings_store

    if settings_store.get(session, "app_name") != "OPDS Server":
        return 0
    settings_store.set_value(session, "app_name", "InkPort")
    session.commit()
    runtime.load(session)  # the cache was filled before the repairs ran
    return 1


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
        "app_name_rebranded": repair_app_name(session),
    }
    if any(report.values()):
        logger.info("library repairs applied", extra=report)
    return report
