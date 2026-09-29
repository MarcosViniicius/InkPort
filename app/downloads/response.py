"""HTTP delivery with download tracking.

``TrackedFileResponse`` is a normal ``FileResponse`` (Range, ETag, streaming,
no buffering) that also records the attempt: it counts the bytes it hands to the
server through a wrapped ``send`` and closes the event as completed, interrupted
or error. If the server streams straight from the file
(``http.response.pathsend``) the body never passes through here, so on a clean
finish it falls back to the total size.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from starlette.responses import FileResponse

from app.database.models import DownloadStatus
from app.downloads import store

logger = logging.getLogger(__name__)


class TrackedFileResponse(FileResponse):
    def __init__(
        self,
        path: Any,
        *,
        track_id: str | None = None,
        countable: bool = True,
        total_bytes: int = 0,
        **kwargs: Any,
    ) -> None:
        super().__init__(path, **kwargs)
        self.track_id = track_id
        self.countable = countable
        self.total_bytes = int(total_bytes or 0)

    async def __call__(self, scope, receive, send) -> None:  # type: ignore[override]
        # Only real GET deliveries are tracked; HEAD/websocket go straight through.
        if not self.track_id or scope.get("type") != "http":
            return await super().__call__(scope, receive, send)

        sent = 0

        async def counting_send(message):
            nonlocal sent
            if message.get("type") == "http.response.body":
                sent += len(message.get("body") or b"")
            await send(message)

        status = DownloadStatus.COMPLETED.value
        error: str | None = None
        try:
            await super().__call__(scope, receive, counting_send)
        except BaseException as exc:  # noqa: BLE001 - re-raised right after
            status = (
                DownloadStatus.INTERRUPTED.value
                if isinstance(exc, asyncio.CancelledError)
                else DownloadStatus.ERROR.value
            )
            if status == DownloadStatus.ERROR.value:
                error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            total = self.total_bytes or sent
            if status == DownloadStatus.COMPLETED.value and sent == 0:
                sent = total  # pathsend: the server sent it from the file itself
            store.finish_download(
                self.track_id,
                status=status,
                bytes_sent=sent,
                bytes_total=total,
                countable=self.countable,
                error=error,
            )
