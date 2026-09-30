"""Database queries used by the OPDS feeds (kept separate from HTTP)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import Book, Category
from app.downloads import store as downloads
from app.library.repository import BookQuery, Page, search


def get_category(session: Session, slug: str) -> Category | None:
    return session.scalar(select(Category).where(Category.slug == slug))


def is_offerable(session: Session, book_id: str) -> bool:
    """False when the file is blocked/deleted, so OPDS must hide it."""
    return downloads.should_offer(session, book_id)


def list_books(
    session: Session,
    *,
    page: int = 1,
    per_page: int = 30,
    sort: str = "added_desc",
    category_slug: str | None = None,
    author: str | None = None,
    series: str | None = None,
    tag: str | None = None,
    query: str | None = None,
    collapse: bool = False,
) -> Page:
    """List books. ``collapse=True`` shows one entry per work (device feeds)."""
    category_id = None
    if category_slug:
        category = get_category(session, category_slug)
        if category is None:
            return Page(items=[], total=0, page=1, per_page=per_page)
        category_id = category.id

    return search(
        session,
        BookQuery(
            q=query,
            category_id=category_id,
            author=author,
            series=series,
            tag=tag,
            sort=sort,
            page=page,
            per_page=per_page,
            collapse_variants=collapse,
            # Blocked/deleted files never show up in the OPDS catalogs.
            exclude_states=downloads.HIDDEN_STATES,
        ),
    )


def count_books(session: Session) -> int:
    from sqlalchemy import func

    return int(session.scalar(select(func.count(Book.id))) or 0)


def variants(session: Session, book: Book) -> list[Book]:
    """All files that represent the same work: the original plus conversions."""
    origin_id = book.origin_book_id or book.id
    origin = session.get(Book, origin_id) if origin_id else None
    converted = session.scalars(
        select(Book).where(Book.origin_book_id == origin_id)
    ).all()

    seen: dict[str, Book] = {}
    for candidate in [origin, book, *converted]:
        if candidate is not None:
            seen.setdefault(candidate.id, candidate)
    return list(seen.values())


#: Profiles that are device-independent (serve any screen).
UNIVERSAL_PROFILES = {"", "generic_epub", "manga_epub"}


def rank_variants(variants: list[Book]) -> list[Book]:
    """Order acquisition candidates: format first, then universal, then original.

    Clients such as CrossPoint use the first usable link, so the device-neutral
    file (which adapts to any screen) is offered before device-specific ones.
    """
    from app.opds.constants import FORMAT_PRIORITY

    def rank(book: Book) -> tuple[int, int, int]:
        try:
            fmt_rank = FORMAT_PRIORITY.index(book.format)
        except ValueError:
            fmt_rank = len(FORMAT_PRIORITY)
        universal = 0 if (book.device_profile or "") in UNIVERSAL_PROFILES else 1
        original = 0 if book.is_original else 1
        return (fmt_rank, universal, original)

    return sorted(variants, key=rank)


def ranked_variants(session: Session, book: Book) -> list[Book]:
    """``variants`` already ordered for a generic (unknown) client."""
    return rank_variants(variants(session, book))


def variants_for_device(session: Session, book: Book, device_slug: str) -> list[Book]:
    """Variants ordered for a specific device: its own file first.

    Used by ``/opds/device/<slug>`` so a reader configured with that slug picks
    the file that was actually produced for it, falling back to the universal
    (adaptive) EPUB and finally to the other files.
    """
    all_variants = variants(session, book)
    # A blocked/deleted variant must not become an acquisition link.
    hidden = downloads.hidden_book_ids(session, [v.id for v in all_variants])
    all_variants = [v for v in all_variants if v.id not in hidden] or [book]
    device_files = [v for v in all_variants if v.device_profile == device_slug]
    universal = [v for v in all_variants if (v.device_profile or "") in UNIVERSAL_PROFILES]
    others = [v for v in all_variants if v not in device_files and v not in universal]

    ordered: list[Book] = []
    for group in (rank_variants(device_files), rank_variants(universal), rank_variants(others)):
        for candidate in group:
            if candidate not in ordered:
                ordered.append(candidate)
    return ordered or all_variants
