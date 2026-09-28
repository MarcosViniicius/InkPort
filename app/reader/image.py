"""Single-image handler.

Web-native images are served as-is; TIFF/HEIC and friends are converted on the
fly (and cached) so the browser can show them too.
"""

from __future__ import annotations

import mimetypes

from starlette.responses import Response

from app.library.formats import IMAGE_EXTS, media_type_for
from app.reader import images
from app.reader.base import NotRenderableError, ReaderContext, ReaderHandler

_PAGE_HEADERS = {"Cache-Control": "private, max-age=86400"}


class ImageHandler(ReaderHandler):
    name = "image"
    label = "Imagem"
    formats = frozenset(IMAGE_EXTS)

    def capabilities(self, ctx: ReaderContext) -> dict:
        return {
            "native": images.is_web_image(ctx.format),
            "pages": True,
            "chapters": False,
            "assets": False,
            "audio": False,
        }

    def manifest(self, ctx: ReaderContext) -> dict:
        from app.reader.manifest import build_manifest

        message = None
        width = height = 0
        try:
            image = images.load_image(ctx.path.read_bytes())
            width, height = image.size
        except Exception:  # noqa: BLE001 - unreadable image -> honest state
            message = (
                "Este formato de imagem não pôde ser lido no servidor "
                "(HEIC costuma exigir pillow-heif). Baixe o arquivo."
            )
        return build_manifest(
            ctx,
            handler=self,
            capabilities=self.capabilities(ctx),
            page_count=1 if message is None else 0,
            message=message,
            extra={"image": {"width": width, "height": height}},
        )

    def page(self, ctx: ReaderContext, index: int, *, width: int = 0, gray: bool = False) -> Response:
        data, mime = self._display(ctx, width=width, gray=gray)
        return Response(data, media_type=mime, headers=_PAGE_HEADERS)

    def raw(self, ctx: ReaderContext) -> Response | None:
        if images.is_web_image(ctx.format):
            return None
        data, mime = self._display(ctx, width=0, gray=False)
        return Response(data, media_type=mime, headers=_PAGE_HEADERS)

    def _display(self, ctx: ReaderContext, *, width: int, gray: bool) -> tuple[bytes, str]:
        path = ctx.path
        if images.is_web_image(ctx.format) and not width and not gray:
            return path.read_bytes(), _guess_mime(ctx.format, ctx.book)

        def build(tmp) -> None:
            data, _ = images.transform_image(path.read_bytes(), width=width, gray=gray)
            tmp.write_bytes(data)

        try:
            cached = ctx.cache.file(f"img-w{width or 0}-{'g' if gray else 'c'}.jpg", build)
        except ValueError as exc:
            raise NotRenderableError("Não foi possível converter esta imagem.") from exc
        return cached.read_bytes(), "image/jpeg"


def _guess_mime(fmt: str, book) -> str:
    ext = (fmt or "").lower().lstrip(".")
    guessed = mimetypes.guess_type(f"x.{ext}")[0]
    return guessed or media_type_for(ext, getattr(book, "media_type", None))
