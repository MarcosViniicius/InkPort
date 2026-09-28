"""Native conversion handler: Kindle/DOCX/RTF previewed without Calibre.

The Web Reader shows these formats by converting the file to EPUB **once** into
the reader cache and then delegating to the EPUB handler (full fidelity: the
book keeps its own CSS and images). The conversion is done by the project's own
converters, so nothing has to be installed:

* MOBI / AZW / AZW3 / PDB / PRC -> :class:`KindleToEpubConverter`
* DOCX                          -> :class:`DocxToEpubConverter`
* RTF                           -> :class:`TextToEpubConverter`

LIT (Microsoft Reader) stays with the Calibre handler, when Calibre exists.
"""

from __future__ import annotations

import dataclasses
import logging
import tempfile
import threading
from pathlib import Path
from shutil import copyfile

from starlette.responses import Response

from app.reader.base import NotRenderableError, ReaderContext, ReaderError, ReaderHandler
from app.reader.epub import EpubHandler

logger = logging.getLogger(__name__)

_FORMATS = frozenset({"mobi", "azw", "azw3", "pdb", "prc", "docx", "rtf"})

_inflight: set[str] = set()
_failed: dict[str, str] = {}
_guard = threading.Lock()


class NativeConvertHandler(ReaderHandler):
    name = "native_convert"
    label = "Conversão nativa"
    formats = _FORMATS

    def __init__(self) -> None:
        self._epub = EpubHandler()

    # -- availability ------------------------------------------------------
    def _converter(self, ctx: ReaderContext):
        """The converter that can turn this file into an EPUB here, if any."""
        from app.converters.base import ConversionRequest
        from app.converters.registry import select_converter
        from app.devices.registry import get_profile
        from app.metadata.model import BookMetadata

        request = ConversionRequest(
            source=ctx.path,
            detection=ctx.detection,
            metadata=BookMetadata(title=getattr(ctx.book, "title", "") or ctx.path.stem),
            profile=get_profile("eink_generic"),
            target_format="epub",
            workdir=ctx.path.parent,
            options={},
        )
        try:
            return select_converter(request)
        except Exception:  # noqa: BLE001 - a broken probe must stay a friendly error
            logger.exception("converter probe failed", extra={"format": ctx.format})
            return None

    def _epub_path(self, ctx: ReaderContext) -> Path:
        return ctx.cache.directory() / "converted.epub"

    def _failure(self, ctx: ReaderContext) -> str | None:
        with _guard:
            return _failed.get(_key(ctx))

    def needs_prepare(self, ctx: ReaderContext) -> bool:
        if self._epub_path(ctx).exists():
            return False
        return self._failure(ctx) is None

    def begin_prepare(self, ctx: ReaderContext) -> bool:
        with _guard:
            if _key(ctx) in _inflight:
                return False
            _inflight.add(_key(ctx))
            return True

    def prepare(self, ctx: ReaderContext) -> None:
        try:
            ctx.cache.file("converted.epub", lambda tmp: self._convert(ctx, tmp))
        except Exception as exc:  # noqa: BLE001 - recorded and shown to the user
            logger.warning(
                "readable conversion failed",
                extra={"book_id": ctx.book_id, "format": ctx.format, "error": str(exc)},
            )
            with _guard:
                _failed[_key(ctx)] = str(exc)[:240] or "falha na conversão"
        finally:
            with _guard:
                _inflight.discard(_key(ctx))

    # -- handler API -------------------------------------------------------
    def manifest(self, ctx: ReaderContext) -> dict:
        from app.reader.manifest import build_manifest

        converter = self._converter(ctx)
        if converter is None:
            return build_manifest(
                ctx,
                handler=self,
                capabilities=self.capabilities(ctx),
                message=(
                    "Não consegui preparar este formato para leitura no navegador. "
                    "Baixe o arquivo para ler no seu dispositivo."
                ),
                state="unavailable",
            )
        failure = self._failure(ctx)
        if failure:
            return build_manifest(
                ctx,
                handler=self,
                capabilities=self.capabilities(ctx),
                message=f"Não foi possível converter para leitura: {failure}",
                state="error",
            )
        epub = self._epub_path(ctx)
        if not epub.exists():
            return build_manifest(
                ctx,
                handler=self,
                capabilities={"native": False},
                message="Preparando o livro para leitura no navegador…",
                state="preparing",
            )
        manifest = self._epub.manifest(self._as_epub(ctx, epub))
        manifest["converted_from"] = ctx.format
        manifest["state"] = "ready"
        return manifest

    def capabilities(self, ctx: ReaderContext) -> dict:
        return {"native": False, "pages": False, "chapters": False, "assets": False, "audio": False}

    def chapter(self, ctx: ReaderContext, key: str) -> Response:
        return self._epub.chapter(self._as_epub(ctx, self._require_epub(ctx)), key)

    def asset(self, ctx: ReaderContext, key: str) -> Response:
        return self._epub.asset(self._as_epub(ctx, self._require_epub(ctx)), key)

    # -- helpers -----------------------------------------------------------
    def _require_epub(self, ctx: ReaderContext) -> Path:
        epub = self._epub_path(ctx)
        if epub.exists():
            return epub
        failure = self._failure(ctx)
        if failure:
            raise ReaderError(f"A conversão para leitura falhou: {failure}")
        try:
            return ctx.cache.file("converted.epub", lambda tmp: self._convert(ctx, tmp))
        except Exception as exc:  # noqa: BLE001
            raise ReaderError(f"A conversão para leitura falhou: {exc}") from exc

    def _convert(self, ctx: ReaderContext, dest: Path) -> None:
        """Run the project converter into ``dest`` (called inside the cache)."""
        converter = self._converter(ctx)
        if converter is None:
            raise NotRenderableError("Nenhum conversor disponível para este formato.")

        from app.converters.base import ConversionRequest
        from app.devices.registry import get_profile
        from app.metadata.model import BookMetadata

        with tempfile.TemporaryDirectory(prefix="reader_conv_") as workdir:
            request = ConversionRequest(
                source=ctx.path,
                detection=ctx.detection,
                metadata=BookMetadata(title=getattr(ctx.book, "title", "") or ctx.path.stem),
                profile=get_profile("eink_generic"),
                target_format="epub",
                workdir=Path(workdir),
                options={},
            )
            result = converter.run(request)
            if not result.output_path.exists():
                raise ReaderError("O conversor não produziu um arquivo.")
            copyfile(result.output_path, dest)

    def _as_epub(self, ctx: ReaderContext, epub: Path) -> ReaderContext:
        return dataclasses.replace(ctx, path=epub, format="epub")


def _key(ctx: ReaderContext) -> str:
    return f"{ctx.book_id}:{ctx.cache.fingerprint}"
