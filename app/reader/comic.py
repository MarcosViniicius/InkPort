"""Comic archive handler (CBZ/ZIP/CBT; CBR/CB7 when a tool is available).

Pages are read one at a time straight from the archive -- nothing is
unpacked wholesale -- and sorted naturally so ``10`` comes after ``2``. The
frontend composes single/double-page layouts and honours RTL manga.
"""

from __future__ import annotations

import tarfile
import zipfile
from pathlib import Path

from starlette.responses import Response

from app.library.containers import natural_key
from app.library.formats import IMAGE_EXTS, mime_for
from app.reader import images
from app.reader.base import NotRenderableError, ReaderContext, ReaderError, ReaderHandler

_ZIP_EXTS = {"cbz", "zip"}
_TAR_EXTS = {"cbt"}
_TOOL_EXTS = {"cbr", "cb7", "rar", "7z"}
_PAGE_CACHE_HEADERS = {"Cache-Control": "private, max-age=86400"}


class ComicHandler(ReaderHandler):
    name = "comic"
    label = "Quadrinho"
    formats = frozenset({"cbz", "cbt", "cbr", "cb7", "zip", "rar", "7z"})

    def capabilities(self, ctx: ReaderContext) -> dict:
        return {
            "native": False,
            "pages": True,
            "chapters": False,
            "assets": False,
            "audio": False,
        }

    def manifest(self, ctx: ReaderContext) -> dict:
        from app.reader.manifest import build_manifest

        pages = self._image_names(ctx.book_id, ctx.path)
        if pages is None:
            raise NotRenderableError(self._tool_message(ctx.path))
        message = None if pages else "Nenhuma imagem encontrada no arquivo."
        return build_manifest(
            ctx,
            handler=self,
            capabilities=self.capabilities(ctx),
            page_count=len(pages),
            message=message,
        )

    def page(self, ctx: ReaderContext, index: int, *, width: int = 0, gray: bool = False) -> Response:
        names = self._image_names(ctx.book_id, ctx.path)
        if names is None:
            raise NotRenderableError(self._tool_message(ctx.path))
        if index < 0 or index >= len(names):
            raise ReaderError("Página fora do intervalo.", status_code=404)

        name = names[index]
        ext = name.rsplit(".", 1)[-1].lower()
        needs_transform = bool(width) or gray or ext not in images.WEB_IMAGE_EXTS
        if not needs_transform:
            return Response(
                self._read(ctx.path, name),
                media_type=mime_for(ext) or "application/octet-stream",
                headers=_PAGE_CACHE_HEADERS,
            )

        cache_name = f"c{index:05d}-w{width or 0}-{'g' if gray else 'c'}.jpg"
        data = self._read(ctx.path, name)

        def build(tmp) -> None:
            encoded, _ = images.transform_image(data, width=width, gray=gray)
            tmp.write_bytes(encoded)

        try:
            cached = ctx.cache.file(cache_name, build)
        except ValueError as exc:
            raise ReaderError(f"Imagem inválida na página {index + 1}.") from exc
        return Response(cached.read_bytes(), media_type="image/jpeg", headers=_PAGE_CACHE_HEADERS)

    # -- archive access ----------------------------------------------------
    def _image_names(self, book_id: str, path: Path) -> list[str] | None:
        ext = path.suffix.lower().lstrip(".")
        if ext in _ZIP_EXTS:
            return _zip_images(path)
        if ext in _TAR_EXTS:
            return _tar_images(path)
        if ext in _TOOL_EXTS:
            return None
        return None

    def _read(self, path: Path, name: str) -> bytes:
        ext = path.suffix.lower().lstrip(".")
        try:
            if ext in _ZIP_EXTS:
                with zipfile.ZipFile(path) as archive:
                    return archive.read(name)
            if ext in _TAR_EXTS:
                with tarfile.open(path) as archive:
                    member = archive.extractfile(name)
                    if member is None:
                        raise KeyError(name)
                    return member.read()
        except (KeyError, OSError, zipfile.BadZipFile, tarfile.TarError) as exc:
            raise ReaderError("Página não encontrada no arquivo.", status_code=404) from exc
        raise NotRenderableError(self._tool_message(path))

    def _tool_message(self, path: Path) -> str:
        ext = path.suffix.lower().lstrip(".").upper()
        return (
            f"Arquivos {ext} precisam do unrar/7-Zip no servidor para serem abertos aqui. "
            "Baixe o arquivo ou converta para CBZ."
        )


def _zip_images(path: Path) -> list[str]:
    try:
        with zipfile.ZipFile(path) as archive:
            names = [
                name
                for name in archive.namelist()
                if not name.endswith("/") and name.rsplit(".", 1)[-1].lower() in IMAGE_EXTS
            ]
    except (zipfile.BadZipFile, OSError):
        return []
    return sorted(names, key=natural_key)


def _tar_images(path: Path) -> list[str]:
    try:
        with tarfile.open(path) as archive:
            names = [
                member.name
                for member in archive.getmembers()
                if member.isfile() and member.name.rsplit(".", 1)[-1].lower() in IMAGE_EXTS
            ]
    except (tarfile.TarError, OSError):
        return []
    return sorted(names, key=natural_key)
