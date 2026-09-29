"""Download tracking and per-file lifecycle.

Two levels, deliberately separate:

* ``FileRecord`` -- one row per stored file (per book). It is the entity future
  file management acts on: state (available/downloaded/blocked/deleted), totals
  and timestamps. Kept even if the book is removed (``book_id`` is nulled) so the
  history survives.
* ``DownloadEvent`` -- one row per download attempt. A file can be downloaded
  many times, concurrently, and attempts can be interrupted.

The record's ``state`` is coarse on purpose (no transient "downloading");
"download iniciado" is derived from ``active_downloads``/open events, which is
recovered on boot.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.database.models.base import new_uuid, utcnow
from app.database.models.enums import DownloadStatus, FileState

if TYPE_CHECKING:
    pass


class FileRecord(Base):
    __tablename__ = "file_records"
    __table_args__ = (Index("ix_file_records_state", "state"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    #: Nulled if the book is deleted; the record (history) stays.
    book_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("books.id", ondelete="SET NULL"), unique=True
    )

    # Stable, self-sufficient identity: survives a rename and the book deletion.
    file_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    file_name: Mapped[str] = mapped_column(String(768), default="")
    file_ext: Mapped[str] = mapped_column(String(16), default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)

    state: Mapped[str] = mapped_column(String(16), default=FileState.AVAILABLE.value)
    download_count: Mapped[int] = mapped_column(Integer, default=0)
    #: Downloads in flight right now (0 when idle). Recovered on boot.
    active_downloads: Mapped[int] = mapped_column(Integer, default=0)
    first_download_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_download_at: Mapped[datetime | None] = mapped_column(DateTime)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    events: Mapped[list[DownloadEvent]] = relationship(
        back_populates="file", cascade="all, delete-orphan"
    )


class DownloadEvent(Base):
    __tablename__ = "download_events"
    __table_args__ = (Index("ix_download_events_file_started", "file_id", "started_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    file_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("file_records.id", ondelete="CASCADE"), index=True
    )
    #: Denormalised so the history still reads after the file record is gone.
    book_id: Mapped[str | None] = mapped_column(String(32))
    #: "download" today; "view" is reserved for the inline reader.
    kind: Mapped[str] = mapped_column(String(16), default="download")

    status: Mapped[str] = mapped_column(
        String(16), default=DownloadStatus.STARTED.value, index=True
    )
    #: Best-effort, non-identifying client label + a short non-reversible hash.
    client: Mapped[str | None] = mapped_column(String(64))
    client_key: Mapped[str | None] = mapped_column(String(16))
    #: Range/resume info (parsed from the request, no bodies kept).
    range_request: Mapped[bool] = mapped_column(Boolean, default=False)
    resume: Mapped[bool] = mapped_column(Boolean, default=False)

    bytes_sent: Mapped[int] = mapped_column(Integer, default=0)
    bytes_total: Mapped[int] = mapped_column(Integer, default=0)

    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    error: Mapped[str | None] = mapped_column(String(300))

    file: Mapped[FileRecord] = relationship(back_populates="events")
