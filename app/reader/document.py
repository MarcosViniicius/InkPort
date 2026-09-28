"""DjVu and other document formats we do not render in the browser.

No pretending: the reader shows an honest state with the download button.
"""

from __future__ import annotations

from app.reader.base import ReaderContext, ReaderHandler


class DocumentHandler(ReaderHandler):
    name = "document"
    label = "Documento"
    formats = frozenset({"djvu", "djv"})

    def manifest(self, ctx: ReaderContext) -> dict:
        from app.reader.manifest import build_manifest

        return build_manifest(
            ctx,
            handler=self,
            capabilities=self.capabilities(ctx),
            message=(
                "DjVu não é renderizado pelos navegadores. Baixe o arquivo ou converta-o "
                "para PDF/EPUB no painel."
            ),
            state="unavailable",
        )


class UnsupportedHandler(ReaderHandler):
    """Fallback when no handler claims the format."""

    name = "unsupported"
    label = "Não suportado"
    formats = frozenset()

    def matches(self, book, detection) -> bool:  # pragma: no cover - explicit fallback
        return False

    def manifest(self, ctx: ReaderContext) -> dict:
        from app.reader.manifest import build_manifest

        return build_manifest(
            ctx,
            handler=self,
            capabilities=self.capabilities(ctx),
            message=(
                "Este formato não pode ser aberto no Web Reader. "
                "Baixe o arquivo para ler no seu dispositivo."
            ),
            state="unavailable",
        )

