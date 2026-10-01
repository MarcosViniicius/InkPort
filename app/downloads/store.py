"""Persistent download tracking (no HTTP here).

Every served file gets a ``FileRecord`` (its identity + lifecycle) and each
attempt gets a ``DownloadEvent``. The HTTP layer (``app/downloads/response.py``)
calls ``start_download`` / ``finish_download``; everything else here is the
domain used by the panel, the API and the future auto-cleanup rules.

Counters are updated with SQL expressions (``col + 1``) so simultaneous
downloads cannot lose an update.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256

from sqlalchemy import case, func, or_, select, update

from app.database import session_scope
from app.database.models import (
    Book,
    DownloadEvent,
    DownloadStatus,
    FileRecord,
    FileState,
    utcnow,
)

logger = logging.getLogger(__name__)

#: States that make a file stop being offered in the catalogs.
HIDDEN_STATES: tuple[str, ...] = (FileState.BLOCKED.value, FileState.DELETED.value)

#: Coarse client labels, so we never store raw user-agents or IPs.
_CLIENT_HINTS = (
    ("koreader", "KOReader"),
    ("crosspoint", "CrossPoint"),
    ("xteink", "Xteink"),
    ("moon", "Moon+ Reader"),
    ("calibre", "Calibre"),
    ("dalvik", "Android"),
    ("okhttp", "Android"),
    ("cfnetwork", "iOS"),
    ("webkit", "Navegador"),
    ("gecko", "Navegador"),
    ("firefox", "Navegador"),
    ("chrome", "Navegador"),
    ("safari", "Navegador"),
    ("curl", "curl"),
    ("wget", "wget"),
)


@dataclass(slots=True)
class TrackStart:
    """What the HTTP layer needs to close the attempt later."""

    id: str
    countable: bool
    total_bytes: int


# ---------------------------------------------------------------------------
# Identity / helpers
# ---------------------------------------------------------------------------
def served_name(book: Book) -> str:
    """The file name the user actually receives (title + extension)."""
    fmt = (book.format or "").lower()
    base = (book.title or book.id or "arquivo").replace("/", "-")
    return f"{base}.{fmt}" if fmt else base


def extract_extension(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def client_from_ua(user_agent: str | None) -> tuple[str | None, str | None]:
    """``(label, key)`` from a User-Agent.

    The label is a friendly family name; the key is a short, non-reversible hash
    (stable per client, useful to tell two readers apart). No IP, no raw UA.
    """
    text = (user_agent or "").strip()
    if not text:
        return None, None
    low = text.lower()
    for needle, label in _CLIENT_HINTS:
        if needle in low:
            return label, sha256(text.encode("utf-8", "replace")).hexdigest()[:12]
    return "Desconhecido", sha256(text.encode("utf-8", "replace")).hexdigest()[:12]


def range_flags(header: str | None) -> tuple[bool, bool, bool]:
    """``(range_request, resume, countable)`` from a ``Range`` header.

    ``countable`` is True for a plain download or an open-ended range
    (``bytes=start-``); a bounded partial (``bytes=0-1023``) is not counted as a
    download.
    """
    if not header:
        return False, False, True
    try:
        spec = header.split("=", 1)[1].strip()
    except IndexError:
        return True, False, False
    if "," in spec:  # multipart range: not a single download
        return True, False, False
    start_text, _, end_text = spec.partition("-")
    start = int(start_text) if start_text.isdigit() else 0
    open_ended = end_text == ""
    return True, start > 0, open_ended


# ---------------------------------------------------------------------------
# Record lifecycle
# ---------------------------------------------------------------------------
def ensure_record(session, book: Book) -> FileRecord:
    """Return the file's tracking record, creating/refreshing it if needed."""
    record = session.scalar(select(FileRecord).where(FileRecord.book_id == book.id))
    name = served_name(book)
    if record is None:
        record = FileRecord(
            book_id=book.id,
            file_hash=book.file_hash,
            file_name=name,
            file_ext=extract_extension(name),
            size_bytes=book.file_size or 0,
        )
        session.add(record)
        session.flush()
        return record
    # Keep the identity fresh (rename, reconversion, size change).
    record.file_hash = book.file_hash or record.file_hash
    record.file_name = name
    record.file_ext = extract_extension(name)
    record.size_bytes = book.file_size or record.size_bytes
    return record


def start_download(
    session,
    book: Book,
    *,
    user_agent: str | None = None,
    range_header: str | None = None,
    kind: str = "download",
) -> TrackStart:
    """Open an attempt. Never raises into the delivery path."""
    record = ensure_record(session, book)
    _, resume, countable = range_flags(range_header)
    client, client_key = client_from_ua(user_agent)
    event = DownloadEvent(
        file_id=record.id,
        book_id=book.id,
        kind=kind,
        status=DownloadStatus.STARTED.value,
        client=client,
        client_key=client_key,
        range_request=bool(range_header),
        resume=resume,
    )
    session.add(event)
    session.execute(
        update(FileRecord)
        .where(FileRecord.id == record.id)
        .values(active_downloads=FileRecord.active_downloads + 1)
    )
    session.commit()
    return TrackStart(id=event.id, countable=countable, total_bytes=record.size_bytes or 0)


def finish_download(
    track_id: str | None,
    *,
    status: str,
    bytes_sent: int = 0,
    bytes_total: int = 0,
    countable: bool = True,
    error: str | None = None,
) -> None:
    """Close an attempt and update the aggregate. Never raises."""
    if not track_id:
        return
    try:
        with session_scope() as session:
            event = session.get(DownloadEvent, track_id)
            if event is None or event.status != DownloadStatus.STARTED.value:
                return  # already closed (idempotent)
            now = utcnow()
            event.status = status
            event.bytes_sent = max(0, int(bytes_sent))
            event.bytes_total = max(0, int(bytes_total))
            event.finished_at = now
            if error:
                event.error = error[:300]

            values: dict = {}
            if event.file_id:
                values["active_downloads"] = func.max(
                    0, FileRecord.active_downloads - 1
                )
                completed = status == DownloadStatus.COMPLETED.value and countable
                if completed:
                    values["download_count"] = FileRecord.download_count + 1
                    values["last_download_at"] = now
                    values["first_download_at"] = func.coalesce(
                        FileRecord.first_download_at, now
                    )
                    values["state"] = case(
                        (FileRecord.state == FileState.AVAILABLE.value, FileState.DOWNLOADED.value),
                        else_=FileRecord.state,
                    )
                session.execute(
                    update(FileRecord).where(FileRecord.id == event.file_id).values(**values)
                )
    except Exception:  # noqa: BLE001 - tracking must never break delivery
        logger.exception("download tracking failed to close an attempt")


def recover_interrupted(session, *, older_than_seconds: int = 0) -> int:
    """Mark attempts left ``started`` (crash/hang) as interrupted."""
    cutoff = utcnow() - timedelta(seconds=max(0, older_than_seconds))
    events = list(
        session.scalars(
            select(DownloadEvent).where(
                DownloadEvent.status == DownloadStatus.STARTED.value
            )
        ).all()
    )
    stale = []
    for event in events:
        started = event.started_at
        if started is not None and started.tzinfo is None:
            from datetime import UTC

            started = started.replace(tzinfo=UTC)
        if started is None or started <= cutoff:
            stale.append(event)
    for event in stale:
        event.status = DownloadStatus.INTERRUPTED.value
        event.finished_at = utcnow()
        if event.file_id:
            session.execute(
                update(FileRecord)
                .where(FileRecord.id == event.file_id)
                .values(active_downloads=func.max(0, FileRecord.active_downloads - 1))
            )
    if stale:
        session.commit()
    return len(stale)


# ---------------------------------------------------------------------------
# Queries and management (used by the panel, the API and future cleanup)
# ---------------------------------------------------------------------------
def get_record(session, book_id: str) -> FileRecord | None:
    return session.scalar(select(FileRecord).where(FileRecord.book_id == book_id))


def records_for(session, book_ids: list[str]) -> dict[str, FileRecord]:
    if not book_ids:
        return {}
    rows = session.scalars(select(FileRecord).where(FileRecord.book_id.in_(book_ids))).all()
    return {record.book_id: record for record in rows if record.book_id}


def summary(record: FileRecord | None) -> dict | None:
    """Compact, template/JSON-friendly summary of a file's tracking."""
    if record is None:
        return None
    return {
        "id": record.id,
        "book_id": record.book_id,
        "file_name": record.file_name,
        "file_ext": record.file_ext,
        "file_hash": record.file_hash,
        "size_bytes": record.size_bytes,
        "state": record.state,
        "download_count": record.download_count,
        "active_downloads": record.active_downloads,
        "first_download_at": record.first_download_at,
        "last_download_at": record.last_download_at,
    }


def stats(session, book_id: str) -> dict | None:
    return summary(get_record(session, book_id))


def set_state(session, book_id: str, state: str) -> FileRecord | None:
    """Move a file to ``available``/``downloaded``/``blocked``/``deleted``."""
    record = get_record(session, book_id)
    if record is None:
        return None
    record.state = state
    session.commit()
    return record


def mark_downloaded(session, book_id: str) -> FileRecord | None:
    """Force the 'already downloaded' state (e.g. confirmed elsewhere)."""
    return set_state(session, book_id, FileState.DOWNLOADED.value)


def block(session, book_id: str) -> FileRecord | None:
    """'Do not download again' (future: also hides it from RSS/OPDS)."""
    return set_state(session, book_id, FileState.BLOCKED.value)


def unblock(session, book_id: str) -> FileRecord | None:
    """Return to ``downloaded`` (or ``available`` when never downloaded)."""
    record = get_record(session, book_id)
    if record is None:
        return None
    record.state = (
        FileState.DOWNLOADED.value if record.download_count else FileState.AVAILABLE.value
    )
    session.commit()
    return record


def hidden_book_ids(session, book_ids: list[str]) -> set[str]:
    """Book ids whose file is blocked/deleted (batch, for list filtering)."""
    ids = [bid for bid in book_ids if bid]
    if not ids:
        return set()
    rows = session.scalars(
        select(FileRecord.book_id)
        .where(FileRecord.book_id.in_(ids))
        .where(FileRecord.state.in_(HIDDEN_STATES))
    ).all()
    return {row for row in rows if row}


def should_offer(session, book_id: str) -> bool:
    """False for blocked/deleted files.

    Not wired into the OPDS/RSS listing yet -- that is the next step; call this
    from the catalog queries to hide files.
    """
    record = get_record(session, book_id)
    if record is None:
        return True
    return record.state not in (FileState.BLOCKED.value, FileState.DELETED.value)


def set_state_by_id(session, record_id: str, state: str) -> FileRecord | None:
    """Like ``set_state`` but keyed by the record (works for orphan records)."""
    record = session.get(FileRecord, record_id)
    if record is None:
        return None
    record.state = state
    session.commit()
    return record


def forget(session, record_id: str) -> bool:
    """Drop a record and its events (used by "remove from history")."""
    record = session.get(FileRecord, record_id)
    if record is None:
        return False
    session.delete(record)
    session.commit()
    return True


def overview(session) -> dict:
    """Counts for the downloads screen header."""
    by_state = {state.value: 0 for state in FileState}
    for state, count in session.execute(
        select(FileRecord.state, func.count(FileRecord.id)).group_by(FileRecord.state)
    ).all():
        by_state[state] = int(count)
    totals = session.execute(
        select(
            func.count(FileRecord.id),
            func.coalesce(func.sum(FileRecord.size_bytes), 0),
            func.coalesce(func.sum(FileRecord.download_count), 0),
            func.coalesce(func.sum(FileRecord.active_downloads), 0),
        )
    ).one()
    return {
        "total": int(totals[0] or 0),
        "size_bytes": int(totals[1] or 0),
        "downloads": int(totals[2] or 0),
        "active": int(totals[3] or 0),
        "by_state": by_state,
    }


def list_records(
    session,
    *,
    state: str | None = None,
    query: str | None = None,
    page: int = 1,
    per_page: int = 50,
) -> dict:
    """Paginated, filterable list for the downloads screen."""
    from app.database.models import Book

    page = max(1, page)
    per_page = min(200, max(1, per_page))
    conditions = []
    if state:
        conditions.append(FileRecord.state == state)
    if query and query.strip():
        pattern = f"%{query.strip().lower()}%"
        conditions.append(
            or_(
                func.lower(FileRecord.file_name).like(pattern),
                func.lower(func.coalesce(Book.title, "")).like(pattern),
            )
        )

    base = select(FileRecord, Book).outerjoin(Book, Book.id == FileRecord.book_id)
    counting = (
        select(func.count(FileRecord.id))
        .outerjoin(Book, Book.id == FileRecord.book_id)
        .where(*conditions)
    )
    total = int(session.scalar(counting) or 0)
    statement = (
        base.where(*conditions)
        .order_by(func.coalesce(FileRecord.last_download_at, FileRecord.updated_at).desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    rows = []
    for record, book in session.execute(statement).all():
        item = summary(record) or {}
        item["title"] = book.title if book is not None else (record.file_name or "arquivo")
        item["author"] = book.author if book is not None else None
        item["format"] = book.format if book is not None else record.file_ext
        rows.append(item)
    # ``rows`` (not ``items``): Jinja would resolve ``data.items`` to the dict method.
    return {"rows": rows, "total": total, "page": page, "per_page": per_page}


def recent_events(session, *, limit: int = 30) -> list[dict]:
    """Latest attempts, for the history table."""
    from app.database.models import Book

    statement = (
        select(DownloadEvent, FileRecord, Book)
        .join(FileRecord, FileRecord.id == DownloadEvent.file_id)
        .outerjoin(Book, Book.id == FileRecord.book_id)
        .order_by(DownloadEvent.started_at.desc())
        .limit(max(1, limit))
    )
    rows = []
    for event, record, book in session.execute(statement).all():
        rows.append(
            {
                "id": event.id,
                "title": book.title if book is not None else (record.file_name or "arquivo"),
                "file_name": record.file_name,
                "status": event.status,
                "client": event.client,
                "resume": event.resume,
                "range_request": event.range_request,
                "bytes_sent": event.bytes_sent,
                "bytes_total": event.bytes_total,
                "started_at": event.started_at,
                "finished_at": event.finished_at,
            }
        )
    return rows


def cleanup_candidates(
    session,
    *,
    states: tuple[str, ...] = (FileState.DOWNLOADED.value,),
    idle_seconds: int = 0,
    limit: int = 100,
) -> list[FileRecord]:
    """Rows a future auto-cleanup may act on: downloaded, idle, not in-flight.

    It only selects; deleting the file/records is left to the cleanup routine so
    policies stay in one place.
    """
    stmt = (
        select(FileRecord)
        .where(FileRecord.state.in_(states))
        .where(FileRecord.active_downloads == 0)
        .order_by(func.coalesce(FileRecord.last_download_at, FileRecord.updated_at).asc())
        .limit(max(1, limit))
    )
    if idle_seconds:
        # Reference date: last download, or when it was last touched (e.g. when
        # it was blocked). A blocked-but-never-downloaded file still ages out.
        cutoff = utcnow() - timedelta(seconds=max(0, idle_seconds))
        reference = func.coalesce(
            FileRecord.last_download_at, FileRecord.updated_at, FileRecord.created_at
        )
        stmt = stmt.where(reference <= cutoff)
    return list(session.scalars(stmt).all())


def prune_events(session, *, keep_days: int = 180, limit: int = 500) -> int:
    """Trim the event history (keeps the aggregate on the file record)."""
    cutoff = utcnow() - timedelta(days=max(1, keep_days))
    rows = list(
        session.scalars(
            select(DownloadEvent)
            .where(DownloadEvent.finished_at.is_not(None))
            .where(DownloadEvent.finished_at <= cutoff)
            .limit(limit)
        ).all()
    )
    for event in rows:
        session.delete(event)
    if rows:
        session.commit()
    return len(rows)


__all__ = [
    "HIDDEN_STATES",
    "TrackStart",
    "block",
    "cleanup_candidates",
    "client_from_ua",
    "ensure_record",
    "extract_extension",
    "finish_download",
    "forget",
    "get_record",
    "hidden_book_ids",
    "list_records",
    "mark_downloaded",
    "overview",
    "prune_events",
    "recent_events",
    "set_state_by_id",
    "range_flags",
    "records_for",
    "recover_interrupted",
    "served_name",
    "set_state",
    "should_offer",
    "start_download",
    "stats",
    "summary",
    "unblock",
]
