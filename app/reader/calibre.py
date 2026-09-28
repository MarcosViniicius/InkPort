"""Calibre-backed handler (optional): LIT and other exotic formats.

Calibre is **not required** by this project: the native handler covers Kindle,
DOCX and RTF. This one exists only for the leftovers (``.lit`` and friends), so
when ``ebook-convert`` is present the file is converted once into the reader
cache and served by the EPUB handler. When it is missing we say so honestly
instead of pretending, and offer the download.
"""

from __future__ import annotations

import dataclasses
import subprocess
import threading
from pathlib import Path

from starlette.responses import Response

from app.config import get_settings
from app.converters.tools import detect_toolchain
from app.reader.base import NotRenderableError, ReaderContext, ReaderError, ReaderHandler
from app.reader.epub import EpubHandler

#: Only what the native converter cannot read (see reader/native_convert.py).
_FORMATS = frozenset({"lit"})
_CALIBRE_TIMEOUT = 600

_inflight: set[str] = set()
_failed: dict[str, str] = {}
_guard = threading.Lock()


class CalibreHandler(ReaderHandler):
    name = "calibre"
    label = "Conversão (Calibre, opcional)"
    formats = _FORMATS

    def __init__(self) -> None:
        self._epub = EpubHandler()

    # -- availability ------------------------------------------------------
    def _tool(self) -> str | None:
        """Path of `ebook-convert` when the machine happens to have it."""
        return detect_toolchain().optional.get("Calibre")

    def _epub_path(self, ctx: ReaderContext) -> Path:
        return ctx.cache.directory() / "converted.epub"

    def _failure(self, ctx: ReaderContext) -> str | None:
        with _guard:
            return _failed.get(_key(ctx))

    def needs_prepare(self, ctx: ReaderContext) -> bool:
        if not self._tool():
            return False
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
            ctx.cache.file("converted.epub", lambda tmp: self._convert(ctx.path, tmp))
        except Exception as exc:  # noqa: BLE001 - recorded and shown to the user
            with _guard:
                _failed[_key(ctx)] = str(exc)[:240] or "falha na conversão"
        finally:
            with _guard:
                _inflight.discard(_key(ctx))

    # -- handler API -------------------------------------------------------
    def manifest(self, ctx: ReaderContext) -> dict:
        from app.reader.manifest import build_manifest

        if not self._tool():
            return build_manifest(
                ctx,
                handler=self,
                capabilities=self.capabilities(ctx),
                message=(
                    "Este formato precisa do Calibre (ebook-convert) instalado no servidor "
                    "para ser lido aqui. Baixe o arquivo para ler no seu dispositivo."
                ),
                state="unavailable",
            )
        failure = self._failure(ctx)
        if failure:
            return build_manifest(
                ctx,
                handler=self,
                capabilities=self.capabilities(ctx),
                message=f"Não foi possível converter com o Calibre: {failure}",
                state="error",
            )
        epub = self._epub_path(ctx)
        if not epub.exists():
            return build_manifest(
                ctx,
                handler=self,
                capabilities={"native": False},
                message="Convertendo para EPUB para leitura no navegador…",
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
        if not self._tool():
            raise NotRenderableError(
                "Este formato precisa do Calibre (ebook-convert) no servidor."
            )
        failure = self._failure(ctx)
        if failure:
            raise ReaderError(f"Conversão pelo Calibre falhou: {failure}")
        try:
            return ctx.cache.file("converted.epub", lambda tmp: self._convert(ctx.path, tmp))
        except Exception as exc:  # noqa: BLE001
            raise ReaderError(f"Conversão pelo Calibre falhou: {exc}") from exc

    def _convert(self, source: Path, dest: Path) -> None:
        tool = self._tool()
        if not tool:
            raise ReaderError("Calibre não encontrado.")
        timeout = min(get_settings().conversion_timeout or _CALIBRE_TIMEOUT, _CALIBRE_TIMEOUT)
        try:
            result = subprocess.run(
                [tool, str(source), str(dest), "--output-profile=generic_eink"],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ReaderError("O Calibre demorou demais para converter este arquivo.") from exc
        if result.returncode != 0 or not dest.exists():
            tail = (result.stderr or result.stdout or "").strip().splitlines()[-3:]
            raise ReaderError(" ".join(tail) or "ebook-convert retornou erro.")

    def _as_epub(self, ctx: ReaderContext, epub: Path) -> ReaderContext:
        return dataclasses.replace(ctx, path=epub, format="epub")


def _key(ctx: ReaderContext) -> str:
    return f"{ctx.book_id}:{ctx.cache.fingerprint}"
