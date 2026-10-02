"""Automatic cleanup of downloaded files.

Two independent rules live here:

* :class:`CleanupRule` (global, **Configurações → Limpeza automática**) — removes
  files that were already downloaded (or blocked) and are older than N days, so
  the server reclaims disk without touching files the user has not taken yet.
  Only ``downloaded``/``blocked`` records are candidates, never one with a
  download in flight, and **originals are left alone** unless the user opts in.
* :class:`FeedRetentionRule` (per feed, chosen in the feed form) — a feed that
  must not grow forever (news, comics): remove the posts older than N days
  regardless of state, because a 5-day-old post the owner never read is still
  yesterday's news. The feed item row stays, which is what stops the post from
  being imported again.

In both cases the download history (``file_records``/``download_events``) is
preserved: the record is only moved to ``deleted``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select

from app.database.models import (
    Book,
    ConversionJob,
    Feed,
    FeedItem,
    FileRecord,
    FileState,
    JobStatus,
)
from app.database.models.base import utcnow
from app.downloads import store

logger = logging.getLogger(__name__)

#: How many files one pass handles, so a big backlog is spread over ticks.
DEFAULT_LIMIT = 200

#: Job states that mean "this book is being worked on right now".
_ACTIVE_JOB_STATES = (JobStatus.PENDING.value, JobStatus.RUNNING.value)


@dataclass(slots=True)
class CleanupRule:
    days: int = 30
    include_originals: bool = False
    states: tuple[str, ...] = (FileState.DOWNLOADED.value, FileState.BLOCKED.value)
    limit: int = DEFAULT_LIMIT

    @classmethod
    def from_settings(cls, settings) -> CleanupRule:
        return cls(
            days=max(1, int(getattr(settings, "download_cleanup_days", 30) or 30)),
            include_originals=bool(
                getattr(settings, "download_cleanup_include_originals", False)
            ),
        )


def _eligible(session, rule: CleanupRule):
    return store.cleanup_candidates(
        session,
        states=rule.states,
        idle_seconds=rule.days * 86400,
        limit=rule.limit,
    )


def plan(session, rule: CleanupRule) -> list[dict]:
    """What a run would remove right now (no changes)."""
    rows: list[dict] = []
    for record in _eligible(session, rule):
        book = session.get(Book, record.book_id) if record.book_id else None
        if book is None or (not rule.include_originals and book.is_original):
            continue
        rows.append(
            {
                "book_id": book.id,
                "title": book.title,
                "format": book.format,
                "file_name": record.file_name,
                "size_bytes": record.size_bytes or book.file_size or 0,
                "state": record.state,
                "last_download_at": record.last_download_at,
            }
        )
    return rows


def run(session, rule: CleanupRule) -> dict:
    """Delete the eligible files and mark their records as deleted."""
    from app.library import service

    result = {"removed": 0, "freed_bytes": 0, "skipped": 0, "orphans": 0}
    for record in _eligible(session, rule):
        book = session.get(Book, record.book_id) if record.book_id else None
        if book is None:
            # The book is already gone: just close the record.
            record.book_id = None
            record.state = FileState.DELETED.value
            result["orphans"] += 1
            continue
        if not rule.include_originals and book.is_original:
            result["skipped"] += 1
            continue

        freed = record.size_bytes or book.file_size or 0
        title = book.title
        try:
            service.delete_books(session, [book], delete_files=True)
        except Exception:  # noqa: BLE001 - one file must not stop the cleanup
            logger.exception("cleanup could not delete a file", extra={"book_id": record.book_id})
            result["skipped"] += 1
            continue
        record.book_id = None
        record.state = FileState.DELETED.value
        result["removed"] += 1
        result["freed_bytes"] += freed
        logger.info(
            "cleanup removed a downloaded file",
            extra={"title": title, "bytes": freed},
        )

    if result["removed"] or result["orphans"]:
        session.commit()
    return result


# --- retenção por feed ------------------------------------------------------
@dataclass(slots=True)
class FeedRetentionRule:
    """Quantos dias de posts um feed mantém no servidor.

    O critério é a **idade do post** (``Book.added_at``), não o download: um feed
    de notícias deve descartar o que já passou, tenha o dono lido ou não. Quem
    liga é o próprio feed (``cleanup_enabled``/``cleanup_days``).
    """

    days: int = 30
    limit: int = DEFAULT_LIMIT


def feed_candidates(session, feed, rule: FeedRetentionRule) -> list[Book]:
    """Posts deste feed que passaram do prazo e podem sair agora.

    Só seleciona (não apaga nada) — quem decide é :func:`run_feed`. Fica de fora o
    que está sendo baixado neste instante e o que tem conversão pendente.
    """
    cutoff = utcnow() - timedelta(days=max(1, rule.days))
    stmt = (
        select(Book)
        .join(FeedItem, FeedItem.book_id == Book.id)
        .where(FeedItem.feed_id == feed.id)
        .where(Book.added_at <= cutoff)
        .order_by(Book.added_at.asc())
        .limit(max(1, rule.limit))
    )
    return [book for book in session.scalars(stmt).all() if _idle(session, book)]


def _idle(session, book: Book) -> bool:
    """True quando nada está usando o arquivo (download em curso, conversão na fila)."""
    record = _record_for(session, book.id)
    if record is not None and record.active_downloads > 0:
        return False
    busy = session.scalar(
        select(func.count(ConversionJob.id))
        .where(ConversionJob.book_id == book.id)
        .where(ConversionJob.status.in_(_ACTIVE_JOB_STATES))
    )
    return not busy


def _record_for(session, book_id: str) -> FileRecord | None:
    return session.scalar(select(FileRecord).where(FileRecord.book_id == book_id))


def plan_feed(session, feed, rule: FeedRetentionRule) -> list[dict]:
    """O que a retenção deste feed removeria agora (sem mexer em nada)."""
    rows: list[dict] = []
    for book in feed_candidates(session, feed, rule):
        record = _record_for(session, book.id)
        rows.append(
            {
                "feed_id": feed.id,
                "book_id": book.id,
                "title": book.title,
                "format": book.format,
                "file_name": record.file_name if record else "",
                "size_bytes": (record.size_bytes if record else 0) or book.file_size or 0,
                "state": record.state if record else FileState.AVAILABLE.value,
                "added_at": book.added_at,
            }
        )
    return rows


def run_feed(session, feed, rule: FeedRetentionRule) -> dict:
    """Remove os posts vencidos deste feed (arquivo + livro); o histórico fica.

    O item do feed **não** é esquecido: é ele que garante que o post não volte na
    próxima busca (o GUID segue conhecido). Depois da remoção o item fica sem
    ``book_id``, então a próxima passada não olha mais para ele.
    """
    from app.library import service

    result = {
        "feed_id": feed.id,
        "feed_name": feed.name,
        "removed": 0,
        "freed_bytes": 0,
        "skipped": 0,
    }
    for book in feed_candidates(session, feed, rule):
        record = _record_for(session, book.id)
        freed = (record.size_bytes if record else 0) or book.file_size or 0
        title = book.title
        try:
            service.delete_books(session, [book], delete_files=True)
        except Exception:  # noqa: BLE001 - um post não pode parar a limpeza
            logger.exception(
                "feed retention could not remove a post",
                extra={"feed_id": feed.id, "book_id": book.id},
            )
            result["skipped"] += 1
            continue
        if record is not None:
            record.book_id = None
            record.state = FileState.DELETED.value
        result["removed"] += 1
        result["freed_bytes"] += freed
        logger.info(
            "feed retention removed a post",
            extra={"feed_id": feed.id, "title": title, "bytes": freed},
        )
    if result["removed"]:
        session.commit()
    return result


def run_feeds(session, *, limit: int = DEFAULT_LIMIT) -> dict:
    """Roda a retenção de todos os feeds que a ligaram (uma passada)."""
    totals = {"feeds": 0, "removed": 0, "freed_bytes": 0, "skipped": 0}
    feeds = session.scalars(
        select(Feed).where(Feed.cleanup_enabled.is_(True)).where(Feed.cleanup_days > 0)
    ).all()
    for feed in feeds:
        rule = FeedRetentionRule(days=max(1, feed.cleanup_days or 30), limit=limit)
        report = run_feed(session, feed, rule)
        totals["feeds"] += 1
        for key in ("removed", "freed_bytes", "skipped"):
            totals[key] += report[key]
    return totals


__all__ = [
    "CleanupRule",
    "FeedRetentionRule",
    "feed_candidates",
    "plan",
    "plan_feed",
    "run",
    "run_feed",
    "run_feeds",
]
