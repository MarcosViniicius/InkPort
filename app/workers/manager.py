"""Starts and stops every background worker."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from app.config import Settings
from app.workers.conversion_loop import ConversionLoop
from app.workers.maintenance import MaintenanceLoop, startup_recovery
from app.workers.rss_loop import RSSLoop

logger = logging.getLogger(__name__)


@dataclass
class WorkerManager:
    settings: Settings
    _stop: asyncio.Event = field(default_factory=asyncio.Event)
    _tasks: list[asyncio.Task] = field(default_factory=list)

    async def start(self) -> None:
        startup_recovery()

        for index in range(max(1, self.settings.conversion_concurrency)):
            loop = ConversionLoop(
                name=f"convert-{index + 1}",
                poll_interval=self.settings.worker_poll_interval,
            )
            self._tasks.append(asyncio.create_task(loop.run(self._stop)))

        if self.settings.rss_worker_enabled:
            rss = RSSLoop(poll_interval=60.0)
            self._tasks.append(asyncio.create_task(rss.run(self._stop)))

        maintenance = MaintenanceLoop(interval_seconds=900.0)
        self._tasks.append(asyncio.create_task(maintenance.run(self._stop)))

        logger.info("workers started", extra={"count": len(self._tasks)})

    async def stop(self) -> None:
        self._stop.set()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        logger.info("workers stopped")
