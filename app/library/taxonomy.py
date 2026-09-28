"""Categories and tags: create-or-get helpers used by import and the panel."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import Book, Category, Tag
from app.storage.paths import slugify


def ensure_category(session: Session, name: str | None) -> Category | None:
    if not name or not name.strip():
        return None
    name = name.strip()
    slug = slugify(name)
    category = session.scalar(select(Category).where(Category.slug == slug))
    if category is None:
        category = Category(name=name, slug=slug)
        session.add(category)
        session.flush()
    return category


def ensure_tag(session: Session, name: str) -> Tag:
    name = name.strip()
    tag = session.scalar(select(Tag).where(Tag.name == name))
    if tag is None:
        tag = Tag(name=name)
        session.add(tag)
        session.flush()
    return tag


def set_tags(session: Session, book: Book, names: list[str]) -> None:
    """Replace a book's tags with ``names`` (deduplicated, order-stable)."""
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in names:
        name = raw.strip()
        key = name.lower()
        if name and key not in seen:
            seen.add(key)
            cleaned.append(name)

    book.tags = [ensure_tag(session, name) for name in cleaned]
    session.flush()


def merge_tags(existing: list[str], extra: list[str]) -> list[str]:
    merged = list(existing)
    lowered = {t.lower() for t in merged}
    for name in extra:
        if name.strip() and name.strip().lower() not in lowered:
            merged.append(name.strip())
            lowered.add(name.strip().lower())
    return merged


def delete_empty_tags(session: Session) -> int:
    """Housekeeping after book deletion: drop tags no longer attached."""

    from app.database.models import BookTag

    used = {row[0] for row in session.execute(select(BookTag.tag_id).distinct())}
    removed = 0
    for tag in session.scalars(select(Tag)).all():
        if tag.id not in used:
            session.delete(tag)
            removed += 1
    if removed:
        session.flush()
    return removed
