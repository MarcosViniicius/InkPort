"""Audio handler (audiobooks): a plain ``<audio>`` player on the raw stream."""

from __future__ import annotations

from app.library.formats import AUDIO_EXTS
from app.reader.base import ReaderContext, ReaderHandler


class AudioHandler(ReaderHandler):
    name = "audio"
    label = "Áudio"
    formats = frozenset(AUDIO_EXTS)

    def capabilities(self, ctx: ReaderContext) -> dict:
        return {"native": True, "pages": False, "chapters": False, "assets": False, "audio": True}

    def manifest(self, ctx: ReaderContext) -> dict:
        from app.reader.manifest import build_manifest

        return build_manifest(
            ctx, handler=self, capabilities=self.capabilities(ctx), page_count=0
        )
