"""The central ``Book`` entity -- one row per file in the library."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.database.models.base import new_uuid, utcnow
from app.database.models.classification import Category, Tag
from app.database.models.enums import ContentType, SourceKind

if TYPE_CHECKING:
    from app.database.models.jobs import ConversionJob


class Book(Base):
    __tablename__ = "books"
    __table_args__ = (
        Index("ix_books_type_added", "content_type", "added_at"),
        UniqueConstraint("file_path", name="uq_books_file_path"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)

    # descriptive metadata
    title: Mapped[str] = mapped_column(String(500), index=True)
    sort_title: Mapped[str | None] = mapped_column(String(500))
    author: Mapped[str | None] = mapped_column(String(300), index=True)
    series: Mapped[str | None] = mapped_column(String(300), index=True)
    series_index: Mapped[float | None] = mapped_column(Float)
    publisher: Mapped[str | None] = mapped_column(String(300))
    language: Mapped[str | None] = mapped_column(String(16))
    description: Mapped[str | None] = mapped_column(Text)
    isbn: Mapped[str | None] = mapped_column(String(32))
    published: Mapped[str | None] = mapped_column(String(32))
    page_count: Mapped[int | None] = mapped_column(Integer)
    reading_direction: Mapped[str | None] = mapped_column(String(8))
    age_rating: Mapped[str | None] = mapped_column(String(24))

    # classification
    content_type: Mapped[str] = mapped_column(
        String(16), default=ContentType.UNKNOWN.value, index=True
    )
    media_type: Mapped[str | None] = mapped_column(String(120))
    format: Mapped[str] = mapped_column(String(16), index=True)
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL")
    )

    # files
    file_path: Mapped[str] = mapped_column(String(1024))  # relative to library_dir
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    file_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    cover_path: Mapped[str | None] = mapped_column(String(1024))
    cover_hash: Mapped[str | None] = mapped_column(String(64))

    # provenance
    source: Mapped[str] = mapped_column(String(16), default=SourceKind.UPLOAD.value)
    source_url: Mapped[str | None] = mapped_column(String(2048))
    source_id: Mapped[str | None] = mapped_column(String(512))
    origin_book_id: Mapped[str | None] = mapped_column(
        ForeignKey("books.id", ondelete="SET NULL")
    )
    device_profile: Mapped[str | None] = mapped_column(String(120))
    is_original: Mapped[bool] = mapped_column(Boolean, default=True)

    # bookkeeping
    added_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    missing: Mapped[bool] = mapped_column(Boolean, default=False)

    category_rel: Mapped[Category | None] = relationship(back_populates="books")
    tags: Mapped[list[Tag]] = relationship(
        secondary="book_tags", back_populates="books", lazy="selectin"
    )
    jobs: Mapped[list[ConversionJob]] = relationship(
        back_populates="book",
        cascade="all, delete-orphan",
        foreign_keys="ConversionJob.book_id",
    )

    # --- view helpers ----------------------------------------------------
    @property
    def tag_names(self) -> list[str]:
        return sorted(tag.name for tag in self.tags)

    @property
    def size_mb(self) -> float:
        return round(self.file_size / (1024 * 1024), 2)

    @property
    def display_author(self) -> str:
        return self.author or "Autores desconhecidos"

    @property
    def display_series(self) -> str:
        return self.series or "Sem série"
