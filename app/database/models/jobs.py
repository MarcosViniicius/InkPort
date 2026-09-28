"""Conversion jobs -- the persistent, resumable task queue."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.database.models.base import new_uuid, utcnow
from app.database.models.enums import JobStatus

if TYPE_CHECKING:
    from app.database.models.book import Book


class ConversionJob(Base):
    __tablename__ = "conversion_jobs"
    __table_args__ = (Index("ix_jobs_status_created", "status", "created_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    book_id: Mapped[str] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"), index=True
    )

    target_format: Mapped[str] = mapped_column(String(16))
    device_profile: Mapped[str] = mapped_column(String(120), default="generic_epub")
    converter: Mapped[str | None] = mapped_column(String(120))
    options: Mapped[dict] = mapped_column(JSON, default=dict)

    status: Mapped[str] = mapped_column(
        String(16), default=JobStatus.PENDING.value, index=True
    )
    progress: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)

    output_book_id: Mapped[str | None] = mapped_column(
        ForeignKey("books.id", ondelete="SET NULL")
    )
    output_path: Mapped[str | None] = mapped_column(String(1024))
    keep_original: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime)

    book: Mapped[Book] = relationship(back_populates="jobs", foreign_keys=[book_id])
    output_book: Mapped[Book | None] = relationship(
        foreign_keys=[output_book_id], lazy="selectin"
    )

    @property
    def is_active(self) -> bool:
        return self.status in {JobStatus.PENDING.value, JobStatus.RUNNING.value}
