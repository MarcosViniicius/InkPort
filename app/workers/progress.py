"""Throttled progress reporting from a worker thread into the DB.

This is also the *cooperative cancellation point*: the panel cannot kill a
thread, so it marks the job as cancelled in the database and the next progress
tick aborts the conversion.
"""

from __future__ import annotations

import logging
import threading
import time

from app.database import session_scope
from app.workers import queue

logger = logging.getLogger(__name__)


class JobCancelled(BaseException):
    """Raised when the user asked to discard a running job.

    Inherits from :class:`BaseException` on purpose: converters are full of
    ``except Exception`` guards (one bad page must not fail the whole book), and
    those must never swallow a cancellation.
    """


class ProgressReporter:
    """Callable progress sink suitable for use from a conversion thread.

    Writes are throttled by both percentage delta and elapsed time so a fast
    image loop does not hammer SQLite. Cancellation is polled on its own, slower
    interval, so it does not add a query per reported page.
    """

    def __init__(
        self,
        job_id: str,
        *,
        min_delta: int = 5,
        min_interval: float = 1.0,
        cancel_interval: float = 1.5,
    ) -> None:
        self.job_id = job_id
        self.min_delta = min_delta
        self.min_interval = min_interval
        self.cancel_interval = cancel_interval
        self._lock = threading.Lock()
        self._last_percent = -1
        self._last_time = 0.0
        self._last_check = 0.0

    def __call__(self, percent: int, message: str = "") -> None:
        now = time.monotonic()
        with self._lock:
            write_due = (
                percent >= 100
                or (
                    abs(percent - self._last_percent) >= self.min_delta
                    and now - self._last_time >= self.min_interval
                )
            )
            check_due = now - self._last_check >= self.cancel_interval
            if write_due:
                self._last_percent = percent
                self._last_time = now
            if check_due:
                self._last_check = now

        if not write_due and not check_due:
            return

        try:
            with session_scope() as session:
                if check_due and queue.is_cancelled(session, self.job_id):
                    raise JobCancelled(self.job_id)
                if write_due:
                    queue.set_progress(session, self.job_id, percent, message)
        except JobCancelled:
            raise
        except Exception as exc:  # progress must never break a conversion
            logger.debug("progress update failed", extra={"error": str(exc)})
