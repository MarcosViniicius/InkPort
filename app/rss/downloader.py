"""HTTP downloading for the RSS worker, with size limits and safe names."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.config import get_settings
from app.library.hashing import sha256_file
from app.storage.paths import safe_filename

logger = logging.getLogger(__name__)

USER_AGENT = "opds-server/0.1 (+https://github.com/) rss-worker"
CHUNK = 64 * 1024

#: Fallback extension when the URL has none, keyed by the response Content-Type.
_EXT_BY_MIME = {
    "text/html": "html",
    "application/xhtml+xml": "xhtml",
    "application/pdf": "pdf",
    "application/epub+zip": "epub",
    "application/x-mobipocket-ebook": "mobi",
    "application/vnd.comicbook+zip": "cbz",
    "application/vnd.comicbook-rar": "cbr",
    "application/zip": "zip",
    "application/vnd.rar": "cbr",
    "application/x-rar-compressed": "cbr",
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
    "text/plain": "txt",
}


class DownloadError(RuntimeError):
    pass


@dataclass(slots=True)
class Downloaded:
    path: Path
    content_type: str

    @property
    def is_article(self) -> bool:
        return self.content_type.startswith(("text/html", "application/xhtml"))


class Downloader:
    def __init__(self, *, timeout: float = 60.0) -> None:
        self.max_bytes = get_settings().max_upload_mb * 1024 * 1024
        self._client = httpx.Client(
            timeout=timeout, follow_redirects=True, headers={"User-Agent": USER_AGENT}
        )

    def __enter__(self) -> Downloader:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # -- feed retrieval ---------------------------------------------------
    def fetch(self, url: str) -> bytes:
        try:
            response = self._client.get(url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise DownloadError(f"Falha ao acessar {url}: {exc}") from exc
        if len(response.content) > self.max_bytes:
            raise DownloadError("Feed maior que o limite permitido.")
        return response.content

    # -- content download -------------------------------------------------
    def download(
        self, url: str, dest_dir: Path, *, filename: str | None = None
    ) -> Downloaded:
        """Download ``url``; returns the saved file plus its content type."""
        dest_dir.mkdir(parents=True, exist_ok=True)
        target = dest_dir / safe_filename(filename or _name_from_url(url))
        counter = 1
        while target.exists():
            target = dest_dir / f"{target.stem} ({counter}){target.suffix}"
            counter += 1

        content_type = ""
        try:
            with self._client.stream("GET", url) as response:
                response.raise_for_status()
                content_type = (
                    (response.headers.get("content-type") or "").split(";")[0].strip().lower()
                )
                total = 0
                with target.open("wb") as handle:
                    for chunk in response.iter_bytes(CHUNK):
                        total += len(chunk)
                        if total > self.max_bytes:
                            handle.close()
                            target.unlink(missing_ok=True)
                            raise DownloadError("Arquivo maior que o limite permitido.")
                        handle.write(chunk)
        except httpx.HTTPError as exc:
            target.unlink(missing_ok=True)
            raise DownloadError(f"Falha ao baixar {url}: {exc}") from exc

        if target.stat().st_size == 0:
            target.unlink(missing_ok=True)
            raise DownloadError("Arquivo baixado está vazio.")

        return Downloaded(path=_rename_for_type(target, content_type), content_type=content_type)

    def hash(self, path: Path) -> str:
        return sha256_file(path)


def _rename_for_type(path: Path, content_type: str) -> Path:
    """Give the file the right extension so detection/conversion can work.

    A blog item arrives as ``text/html`` from a URL with no extension; without
    this it would be saved as ``.bin`` and rejected as an unsupported format.
    """
    ext = _EXT_BY_MIME.get(content_type)
    if not ext:
        return path
    current = path.suffix.lower().lstrip(".")
    if current and current != "bin":
        return path  # the URL already provided a meaningful extension
    renamed = path.with_suffix(f".{ext}")
    counter = 1
    while renamed.exists():
        renamed = path.with_name(f"{path.stem}_{counter}.{ext}")
        counter += 1
    path.replace(renamed)
    return renamed


def _name_from_url(url: str) -> str:
    from urllib.parse import unquote, urlparse

    path = urlparse(url).path
    name = unquote(path.rsplit("/", 1)[-1]) or "download.bin"
    if "." not in name:
        name += ".bin"
    return name
