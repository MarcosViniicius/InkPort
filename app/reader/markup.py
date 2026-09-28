"""Standalone HTML/XHTML handler.

The document is sanitised and shown in a sandboxed iframe. Sibling assets
(images, CSS, fonts) in the *same folder* are served through the asset route --
never anything outside it.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from urllib.parse import unquote

from starlette.responses import HTMLResponse, Response

from app.reader.base import ReaderContext, ReaderError, ReaderHandler
from app.reader.sanitize import asset_url_factory, sanitize_css, sanitize_html

_SAFE_ASSET_EXTS = frozenset(
    {"png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "avif", "css",
     "woff", "woff2", "ttf", "otf", "eot", "mp3", "mp4", "webm", "ogg"}
)
_HEADERS = {"Cache-Control": "private, max-age=3600"}


class MarkupHandler(ReaderHandler):
    name = "markup"
    label = "HTML"
    formats = frozenset({"html", "htm", "xhtml"})

    def capabilities(self, ctx: ReaderContext) -> dict:
        return {"native": False, "pages": False, "chapters": True, "assets": True, "audio": False}

    def manifest(self, ctx: ReaderContext) -> dict:
        from app.reader.manifest import build_manifest

        return build_manifest(
            ctx,
            handler=self,
            capabilities=self.capabilities(ctx),
            chapters=[{"key": "index", "title": ctx.book.title, "index": 0}],
        )

    def chapter(self, ctx: ReaderContext, key: str) -> HTMLResponse:
        if unquote(key) not in {"index", Path(ctx.path).name}:
            raise ReaderError("Capítulo não encontrado.", status_code=404)
        try:
            data = ctx.path.read_bytes()
        except OSError as exc:
            raise ReaderError("Arquivo não encontrado.", status_code=404) from exc
        body = sanitize_html(
            data,
            base="",
            asset_url=asset_url_factory(ctx.book_id),
            chapter_url=None,
            title=ctx.book.title,
        )
        return HTMLResponse(body, headers=_HEADERS)

    def asset(self, ctx: ReaderContext, key: str) -> Response:
        target = _safe_sibling(ctx.path, unquote(key))
        if target is None:
            raise ReaderError("Recurso não encontrado.", status_code=404)
        data = target.read_bytes()
        ext = target.suffix.lower().lstrip(".")
        if ext == "css":
            text = data.decode("utf-8", errors="replace")
            cleaned = sanitize_css(
                text, base="", asset_url=asset_url_factory(ctx.book_id)
            )
            return Response(cleaned, media_type="text/css", headers=_HEADERS)
        mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        return Response(data, media_type=mime, headers=_HEADERS)


def _safe_sibling(source: Path, key: str) -> Path | None:
    if not key or ":" in key.split("/")[0]:
        return None
    parent = source.parent.resolve()
    candidate = (parent / key).resolve()
    if candidate == parent or parent not in candidate.parents:
        return None
    if not candidate.is_file():
        return None
    if candidate.suffix.lower().lstrip(".") not in _SAFE_ASSET_EXTS:
        return None
    return candidate
