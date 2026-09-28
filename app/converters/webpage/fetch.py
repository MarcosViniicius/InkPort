"""Download images for the webpage conversion (kept separate from the DOM code)."""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

USER_AGENT = "opds-server/0.1 (+webpage-to-epub)"
DEFAULT_MAX_BYTES = 12 * 1024 * 1024


class ImageFetcher:
    def __init__(self, *, timeout: float = 20.0, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        self.max_bytes = max_bytes
        self._client = httpx.Client(
            timeout=timeout, follow_redirects=True, headers={"User-Agent": USER_AGENT}
        )

    def __enter__(self) -> ImageFetcher:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def __call__(self, url: str) -> tuple[bytes, str] | None:
        try:
            response = self._client.get(url)
            response.raise_for_status()
            content_type = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
            if not content_type.startswith("image/"):
                return None
            data = response.content
            if not data or len(data) > self.max_bytes:
                return None
            return data, content_type
        except httpx.HTTPError as exc:
            logger.debug("image fetch failed", extra={"url": url, "error": str(exc)})
            return None
