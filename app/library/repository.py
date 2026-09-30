"""Read-side queries for the library: search, filters, facets, pagination."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, aliased, selectinload

from app.database.models import Book, BookTag, Category, FileRecord, Tag

SORT_OPTIONS = {
    "added_desc": (Book.added_at.desc(),),
    "added_asc": (Book.added_at.asc(),),
    "title_asc": (func.lower(Book.title).asc(),),
    "title_desc": (func.lower(Book.title).desc(),),
    "author_asc": (func.lower(Book.author).asc(), Book.series_index.asc()),
    "size_desc": (Book.file_size.desc(),),
}


@dataclass(slots=True)
class BookQuery:
    q: str | None = None
    category_id: int | None = None
    author: str | None = None
    series: str | None = None
    tag: str | None = None
    content_type: str | None = None
    fmt: str | None = None
    source: str | None = None
    sort: str = "added_desc"
    page: int = 1
    per_page: int = 24
    #: Hide conversions when their original is still present, so the OPDS feed
    #: shows one entry per work instead of one per generated file.
    collapse_variants: bool = False
    #: File states to leave out (the OPDS catalogs hide blocked/deleted files;
    #: the panel does not, so the admin still sees and can free them).
    exclude_states: tuple[str, ...] = ()

    def normalized(self) -> BookQuery:
        self.page = max(1, self.page)
        self.per_page = min(200, max(1, self.per_page))
        if self.sort not in SORT_OPTIONS:
            self.sort = "added_desc"
        return self


@dataclass(slots=True)
class Page:
    items: list[Book]
    total: int
    page: int
    per_page: int

    @property
    def pages(self) -> int:
        return max(1, ceil(self.total / self.per_page)) if self.total else 1

    @property
    def has_prev(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.pages


def _base_statement(query: BookQuery):
    stmt = select(Book).options(selectinload(Book.tags), selectinload(Book.category_rel))
    filters = []

    if query.q:
        pattern = f"%{query.q.strip().lower()}%"
        filters.append(
            or_(
                func.lower(Book.title).like(pattern),
                func.lower(func.coalesce(Book.author, "")).like(pattern),
                func.lower(func.coalesce(Book.series, "")).like(pattern),
                func.lower(func.coalesce(Book.description, "")).like(pattern),
            )
        )
    if query.category_id:
        filters.append(Book.category_id == query.category_id)
    if query.author:
        filters.append(Book.author == query.author)
    if query.series:
        filters.append(Book.series == query.series)
    if query.content_type:
        filters.append(Book.content_type == query.content_type)
    if query.fmt:
        filters.append(Book.format == query.fmt)
    if query.source:
        filters.append(Book.source == query.source)
    if query.tag:
        filters.append(
            Book.id.in_(
                select(BookTag.book_id)
                .join(Tag, Tag.id == BookTag.tag_id)
                .where(Tag.name == query.tag)
            )
        )
    if query.exclude_states:
        hidden = (
            select(FileRecord.book_id)
            .where(FileRecord.book_id.is_not(None))
            .where(FileRecord.state.in_(query.exclude_states))
        )
        filters.append(Book.id.not_in(hidden))
    if query.collapse_variants:
        origin = aliased(Book)
        filters.append(
            or_(
                Book.origin_book_id.is_(None),
                Book.is_original.is_(True),
                ~select(origin.id).where(origin.id == Book.origin_book_id).exists(),
            )
        )

    if filters:
        stmt = stmt.where(*filters)
    return stmt


def search(session: Session, query: BookQuery) -> Page:
    query = query.normalized()
    total = session.scalar(
        select(func.count()).select_from(_base_statement(query).subquery())
    ) or 0

    stmt = (
        _base_statement(query)
        .order_by(*SORT_OPTIONS[query.sort], Book.id)
        .offset((query.page - 1) * query.per_page)
        .limit(query.per_page)
    )
    items = list(session.scalars(stmt).all())
    return Page(items=items, total=total, page=query.page, per_page=query.per_page)


def get_book(session: Session, book_id: str) -> Book | None:
    return session.scalar(
        select(Book)
        .options(selectinload(Book.tags), selectinload(Book.category_rel))
        .where(Book.id == book_id)
    )


def find_by_hash(session: Session, file_hash: str) -> Book | None:
    return session.scalar(select(Book).where(Book.file_hash == file_hash))


def find_by_source(session: Session, source_id: str) -> Book | None:
    return session.scalar(select(Book).where(Book.source_id == source_id))


def recent(session: Session, limit: int = 30) -> list[Book]:
    stmt = select(Book).order_by(Book.added_at.desc()).limit(limit)
    return list(session.scalars(stmt).all())


def categories(session: Session, *, with_counts: bool = True) -> list[dict]:
    stmt = select(Category).order_by(func.lower(Category.name))
    rows = list(session.scalars(stmt).all())
    if not with_counts:
        return [{"category": row, "count": 0} for row in rows]
    counts = dict(
        session.execute(
            select(Book.category_id, func.count(Book.id)).group_by(Book.category_id)
        ).all()
    )
    return [{"category": row, "count": counts.get(row.id, 0)} for row in rows]


def authors(session: Session, *, limit: int = 500) -> list[tuple[str, int]]:
    stmt = (
        select(Book.author, func.count(Book.id))
        .where(Book.author.is_not(None))
        .group_by(Book.author)
        .order_by(func.lower(Book.author))
        .limit(limit)
    )
    return [(row[0], row[1]) for row in session.execute(stmt).all()]


def series(session: Session, *, limit: int = 500) -> list[tuple[str, int]]:
    stmt = (
        select(Book.series, func.count(Book.id))
        .where(Book.series.is_not(None))
        .group_by(Book.series)
        .order_by(func.lower(Book.series))
        .limit(limit)
    )
    return [(row[0], row[1]) for row in session.execute(stmt).all()]


def tags(session: Session, *, limit: int = 500) -> list[tuple[str, int]]:
    stmt = (
        select(Tag.name, func.count(BookTag.book_id))
        .outerjoin(BookTag, BookTag.tag_id == Tag.id)
        .group_by(Tag.name)
        .order_by(func.lower(Tag.name))
        .limit(limit)
    )
    return [(row[0], row[1]) for row in session.execute(stmt).all()]


def formats(session: Session) -> list[tuple[str, int]]:
    stmt = (
        select(Book.format, func.count(Book.id))
        .group_by(Book.format)
        .order_by(func.count(Book.id).desc())
    )
    return [(row[0], row[1]) for row in session.execute(stmt).all()]


def content_type_counts(session: Session) -> dict[str, int]:
    stmt = select(Book.content_type, func.count(Book.id)).group_by(Book.content_type)
    return {row[0] or "unknown": row[1] for row in session.execute(stmt).all()}


def total_size(session: Session) -> int:
    return int(session.scalar(select(func.coalesce(func.sum(Book.file_size), 0))) or 0)
