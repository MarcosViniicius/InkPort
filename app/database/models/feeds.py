"""RSS/Atom feeds and their discovered items."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.database.models.base import utcnow
from app.database.models.enums import FeedItemStatus

if TYPE_CHECKING:
    pass


class Feed(Base):
    __tablename__ = "feeds"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(300))
    url: Mapped[str] = mapped_column(String(2048), unique=True)
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL")
    )
    interval_minutes: Mapped[int] = mapped_column(Integer, default=360)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    output_format: Mapped[str] = mapped_column(String(16), default="epub")
    device_profile: Mapped[str] = mapped_column(String(120), default="generic_epub")
    destination_folder: Mapped[str] = mapped_column(String(300), default="")
    keep_original: Mapped[bool] = mapped_column(Boolean, default=False)
    max_items_per_run: Mapped[int] = mapped_column(Integer, default=20)

    #: Retroactive pull: how many months back from today to import
    #: (0 = off, -1 = the whole archive). RSS only carries the latest posts;
    #: the older ones come from the site's sitemap.
    backfill_months: Mapped[int] = mapped_column(Integer, default=0)
    #: Optional sitemap URL; when empty it is derived from ``url``.
    sitemap_url: Mapped[str] = mapped_column(String(2048), default="")
    #: Set once the archive walk had nothing left to import.
    backfill_done_at: Mapped[datetime | None] = mapped_column(DateTime)

    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_error: Mapped[str | None] = mapped_column(Text)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    items: Mapped[list[FeedItem]] = relationship(
        back_populates="feed", cascade="all, delete-orphan"
    )

    def is_due(self, now: datetime | None = None) -> bool:
        if not self.active:
            return False
        if self.last_checked_at is None:
            return True
        now = now or utcnow()
        last = self.last_checked_at
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        return (now - last).total_seconds() >= self.interval_minutes * 60


class FeedItem(Base):
    __tablename__ = "feed_items"
    __table_args__ = (UniqueConstraint("feed_id", "guid", name="uq_feed_item_guid"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    feed_id: Mapped[int] = mapped_column(
        ForeignKey("feeds.id", ondelete="CASCADE"), index=True
    )
    guid: Mapped[str] = mapped_column(String(1024))
    url: Mapped[str | None] = mapped_column(String(2048))
    title: Mapped[str | None] = mapped_column(String(500))
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(16), default=FeedItemStatus.SEEN.value)
    error: Mapped[str | None] = mapped_column(Text)
    book_id: Mapped[str | None] = mapped_column(
        ForeignKey("books.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    feed: Mapped[Feed] = relationship(back_populates="items")
