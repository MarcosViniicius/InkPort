"""Handler protocol for the Web Reader.

A handler answers three questions about a book: *can I render it?* (``matches``),
*what does the UI need to know?* (``manifest``) and *give me the bytes* for a
page, a chapter or an internal asset (``page``/``chapter``/``asset``).

Handlers must be honest: when a format cannot be rendered in a browser here
(e.g. DjVu, or MOBI without Calibre) they say so in the manifest instead of
raising their hands and hanging the request.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from starlette.responses import Response

from app.reader.cache import ReaderCache


class ReaderError(RuntimeError):
    """A reader failure that should surface as a friendly message."""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class NotRenderableError(ReaderError):
    """Known format, but it cannot be shown in the browser on this machine."""

    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=415)


@dataclass(slots=True)
class ReaderContext:
    """Everything a handler needs, resolved once per request."""

    book: Any
    path: Path
    detection: Any
    format: str
    #: ``"rtl"`` (manga) or ``"ltr"``.
    direction: str = "ltr"
    #: Device presets, already serialised for the manifest.
    profiles: list[dict] = field(default_factory=list)
    profile_slug: str | None = None
    #: Profile defaults that seed the reader UI (grey levels, direction, ...).
    profile: Any = None
    cache: ReaderCache = field(default_factory=lambda: ReaderCache("", None))

    @property
    def book_id(self) -> str:
        return str(getattr(self.book, "id", ""))

    @property
    def manga_rtl(self) -> bool:
        return self.direction == "rtl"

    def exists(self) -> bool:
        return self.path.exists() and self.path.is_file()

    def extension(self) -> str:
        return self.path.suffix.lower().lstrip(".")


@runtime_checkable
class ReaderHandlerProtocol(Protocol):
    """Structural view of a handler (handy for typing and tests)."""

    name: str
    formats: frozenset[str]

    def matches(self, book: Any, detection: Any) -> bool: ...

    def manifest(self, ctx: ReaderContext) -> dict: ...

    def page(self, ctx: ReaderContext, index: int, **kwargs) -> Response: ...

    def chapter(self, ctx: ReaderContext, key: str) -> Response: ...

    def asset(self, ctx: ReaderContext, key: str) -> Response: ...


class ReaderHandler:
    """Base class. Subclasses declare which formats they own."""

    name = "base"
    #: Shown in the reader UI ("EPUB", "PDF (imagem)", ...).
    label = "Documento"
    #: Normalised extensions this handler owns.
    formats: frozenset[str] = frozenset()

    # -- selection ---------------------------------------------------------
    def matches(self, book: Any, detection: Any) -> bool:
        fmt = (getattr(detection, "format", None) or getattr(book, "format", "") or "").lower()
        return fmt in self.formats

    # -- manifest ----------------------------------------------------------
    def capabilities(self, ctx: ReaderContext) -> dict:
        return {
            "native": False,
            "pages": False,
            "chapters": False,
            "assets": False,
            "audio": False,
        }

    def manifest(self, ctx: ReaderContext) -> dict:
        from app.reader.manifest import build_manifest

        return build_manifest(ctx, handler=self, capabilities=self.capabilities(ctx))

    #: Prepare = expensive one-off work (e.g. Calibre conversion).
    def needs_prepare(self, ctx: ReaderContext) -> bool:
        return False

    def prepare(self, ctx: ReaderContext) -> None:
        """Do the expensive work. Runs in a worker thread."""

    # -- bytes -------------------------------------------------------------
    def raw(self, ctx: ReaderContext) -> Response | None:
        """Override the raw file response; ``None`` means "serve the file"."""
        return None

    def page(self, ctx: ReaderContext, index: int, *, width: int = 0, gray: bool = False) -> Response:
        raise NotRenderableError("Este formato não tem páginas para renderizar.")

    def chapter(self, ctx: ReaderContext, key: str) -> Response:
        raise NotRenderableError("Este formato não tem capítulos de texto.")

    def asset(self, ctx: ReaderContext, key: str) -> Response:
        raise ReaderError("Este formato não expõe recursos internos.", status_code=404)


def safe_join(base: str, key: str) -> str:
    """Join an internal archive path, refusing anything that escapes ``base``.

    Blocks ``..``, absolute paths and Windows drive letters. The result is a
    POSIX relative path. Raises :class:`ReaderError` (404) when unsafe.
    """
    import posixpath

    raw = (key or "").replace("\\", "/")
    if not raw or raw.startswith("/") or ":" in raw.split("/")[0]:
        raise ReaderError("Recurso não encontrado.", status_code=404)
    joined = posixpath.normpath(posixpath.join(base, raw))
    if joined in {"", ".", ".."} or joined.startswith("../") or joined.startswith("/"):
        raise ReaderError("Recurso não encontrado.", status_code=404)
    return joined
