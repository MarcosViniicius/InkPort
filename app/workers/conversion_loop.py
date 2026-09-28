"""The conversion worker loop."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from app.database import session_scope
from app.workers import queue
from app.workers.conversion import execute_job
from app.workers.progress import JobCancelled

logger = logging.getLogger(__name__)


class ConversionLoop:
    """Pulls jobs from the queue and executes them, one at a time."""

    def __init__(self, name: str, poll_interval: float = 2.0) -> None:
        self.name = name
        self.poll_interval = poll_interval

    async def run(self, stop: asyncio.Event) -> None:
        logger.info("conversion loop started", extra={"worker": self.name})
        while not stop.is_set():
            job_id = self._claim()
            if job_id is None:
                await self._sleep(stop)
                continue
            try:
                await execute_job(job_id)
            except asyncio.CancelledError:
                raise
            except JobCancelled:  # defensive: never let it kill the loop
                logger.info("job cancelled", extra={"job_id": job_id})
            except Exception:  # noqa: BLE001
                logger.exception("unexpected error in conversion loop")
                await self._sleep(stop)
        logger.info("conversion loop stopped", extra={"worker": self.name})

    def _claim(self) -> str | None:
        with session_scope() as session:
            job = queue.claim_next(session)
            if job is None:
                return None
            logger.info("job claimed", extra={"worker": self.name, "job_id": job.id})
            return job.id

    async def _sleep(self, stop: asyncio.Event) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=self.poll_interval)
