"""Enumerations shared by models and the rest of the app."""

from __future__ import annotations

import enum


class ContentType(enum.StrEnum):
    EBOOK = "ebook"        # reflowable text: epub, mobi, azw, fb2, txt
    COMIC = "comic"        # image pages: cbz, cbr, cb7, zip-of-images
    DOCUMENT = "document"  # pdf and other fixed layout
    IMAGE = "image"        # single image
    AUDIO = "audio"
    UNKNOWN = "unknown"


class JobStatus(enum.StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class FeedItemStatus(enum.StrEnum):
    SEEN = "seen"
    DOWNLOADED = "downloaded"
    CONVERTED = "converted"
    SKIPPED = "skipped"
    ERROR = "error"


class SourceKind(enum.StrEnum):
    UPLOAD = "upload"
    RSS = "rss"
    IMPORT = "import"
    #: Página/arquivo baixado de um endereço pelo painel (Importar → URL).
    DOWNLOAD = "download"
