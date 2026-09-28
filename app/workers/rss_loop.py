"""Periodic RSS/Atom poller."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from sqlalchemy import select

from app.database import session_scope
from app.database.models import Feed
from app.rss.service import process_feed

logger = logging.getLogger(__name__)


class RSSLoop:
    """Checks which feeds are due and processes them off the event loop."""

    def __init__(self, poll_interval: float = 60.0) -> None:
        self.poll_interval = poll_interval

    async def run(self, stop: asyncio.Event) -> None:
        logger.info("rss loop started")
        while not stop.is_set():
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("unexpected error in rss loop")
            await self._sleep(stop)
        logger.info("rss loop stopped")

    async def _tick(self) -> None:
        due = self._due_feed_ids()
        for feed_id in due:
            if feed_id is None:
                continue
            await asyncio.to_thread(self._process, feed_id)

    def _due_feed_ids(self) -> list[int]:
        with session_scope() as session:
            feeds = session.scalars(select(Feed).where(Feed.active.is_(True))).all()
            return [feed.id for feed in feeds if feed.is_due()]

    @staticmethod
    def _process(feed_id: int) -> None:
        with session_scope() as session:
            feed = session.get(Feed, feed_id)
            if feed is None or not feed.active:
                return
            process_feed(session, feed)

    async def _sleep(self, stop: asyncio.Event) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=self.poll_interval)
