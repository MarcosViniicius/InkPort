"""Periodic housekeeping: temp cleanup, stale jobs, missing files."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from app.config import get_settings
from app.database import session_scope
from app.downloads import store as downloads
from app.library.repairs import run_repairs
from app.library.service import mark_missing
from app.storage.temp import clean_temp_dir
from app.workers import queue

logger = logging.getLogger(__name__)


class MaintenanceLoop:
    def __init__(self, interval_seconds: float = 900.0) -> None:
        self.interval = interval_seconds

    async def run(self, stop: asyncio.Event) -> None:
        logger.info("maintenance loop started")
        while not stop.is_set():
            try:
                await asyncio.to_thread(self._tick)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("maintenance tick failed")
            await self._sleep(stop)
        logger.info("maintenance loop stopped")

    def _tick(self) -> None:
        settings = get_settings()
        removed = clean_temp_dir(max_age_seconds=3600)
        cleaned = None
        with session_scope() as session:
            resumed = queue.requeue_stale(session, older_than_seconds=1800)
            missing = mark_missing(session)
            # A download left "started" for hours means a hung request; close it.
            stuck_downloads = downloads.recover_interrupted(session, older_than_seconds=21600)
            if getattr(settings, "download_cleanup_enabled", False):
                from app.downloads.cleanup import CleanupRule
                from app.downloads.cleanup import run as run_cleanup

                cleaned = run_cleanup(session, CleanupRule.from_settings(settings))
            # Retenção por feed: independente da limpeza global — o dono escolhe
            # no próprio feed (ex.: o G1 guarda 5 dias, a biblioteca fica como está).
            from app.downloads.cleanup import run_feeds

            retention = run_feeds(session)
            if not retention["removed"]:
                retention = None
            repairs = run_repairs(session)
        if (
            removed
            or resumed
            or missing
            or stuck_downloads
            or cleaned
            or retention
            or any(repairs.values())
        ):
            logger.info(
                "maintenance done",
                extra={
                    "temp_removed": removed,
                    "resumed": resumed,
                    "missing": missing,
                    "stuck_downloads": stuck_downloads,
                    "downloads_cleaned": cleaned,
                    "feed_retention": retention,
                    **repairs,
                },
            )

    async def _sleep(self, stop: asyncio.Event) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=self.interval)


def startup_recovery() -> int:
    """Called once at boot: requeue jobs and close downloads killed by a crash."""
    with session_scope() as session:
        count = queue.requeue_stale(session, older_than_seconds=0)
        stuck = downloads.recover_interrupted(session, older_than_seconds=0)
    if count:
        logger.warning("requeued interrupted jobs", extra={"count": count})
    if stuck:
        logger.warning("marked interrupted downloads", extra={"count": stuck})
    return count


def current_settings_interval() -> float:
    return max(300.0, get_settings().worker_poll_interval * 300)
