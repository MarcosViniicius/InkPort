"""PDF handler.

Two paths, best first:

1. **native**: the browser's own PDF viewer in an iframe (``/raw``), which is
   pixel-perfect and needs no server work;
2. **image mode**: pages rendered by PyMuPDF through ``/page/{index}``, useful
   on phones and to preview how a scanned PDF looks on a 16-grey e-ink panel.
"""

from __future__ import annotations

from fastapi.responses import FileResponse
from starlette.responses import Response

from app.reader import images
from app.reader.base import ReaderContext, ReaderError, ReaderHandler

_PAGE_CACHE_HEADERS = {"Cache-Control": "private, max-age=86400"}


class PdfHandler(ReaderHandler):
    name = "pdf"
    label = "PDF"
    formats = frozenset({"pdf"})

    def capabilities(self, ctx: ReaderContext) -> dict:
        return {
            "native": True,
            "pages": True,
            "image_mode": True,
            "chapters": False,
            "assets": False,
            "audio": False,
        }

    def manifest(self, ctx: ReaderContext) -> dict:
        from app.reader.manifest import build_manifest

        count = self._page_count(ctx)
        message = None
        if count <= 0:
            message = "Não foi possível contar as páginas; use o visualizador nativo."
        return build_manifest(
            ctx,
            handler=self,
            capabilities=self.capabilities(ctx),
            page_count=count,
            message=message,
        )

    def page(self, ctx: ReaderContext, index: int, *, width: int = 0, gray: bool = False) -> Response:
        count = self._page_count(ctx)
        if index < 0 or (count and index >= count):
            raise ReaderError("Página fora do intervalo.", status_code=404)
        ext = "png" if gray else "jpg"
        name = f"page-{index:05d}-w{width or 0}-{'g' if gray else 'c'}.{ext}"

        def build(tmp) -> None:
            data, _ = images.render_pdf_page(ctx.path, index, width=width, gray=gray)
            tmp.write_bytes(data)

        try:
            cached = ctx.cache.file(name, build)
        except IndexError as exc:
            raise ReaderError("Página fora do intervalo.", status_code=404) from exc
        except Exception as exc:  # noqa: BLE001 - surface a friendly message
            raise ReaderError(f"Não foi possível renderizar a página: {exc}") from exc
        return FileResponse(
            cached,
            media_type="image/png" if gray else "image/jpeg",
            headers=_PAGE_CACHE_HEADERS,
        )

    def _page_count(self, ctx: ReaderContext) -> int:
        try:
            return images.pdf_page_count(ctx.path)
        except Exception:  # noqa: BLE001 - PyMuPDF missing/corrupt PDF
            return int(getattr(ctx.detection, "page_count", 0) or 0)
