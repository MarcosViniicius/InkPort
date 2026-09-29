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


class DownloadStatus(enum.StrEnum):
    """Lifecycle of one download attempt."""

    STARTED = "started"          # headers/stream opened, bytes may be flowing
    COMPLETED = "completed"      # the response finished (client got the body)
    INTERRUPTED = "interrupted"  # client/proxy or process went away mid-stream
    ERROR = "error"              # the server failed while streaming


class FileState(enum.StrEnum):
    """Lifecycle of a stored file, for tracking and future file management."""

    AVAILABLE = "available"    # offered normally
    DOWNLOADED = "downloaded"  # already downloaded at least once (still offered)
    BLOCKED = "blocked"        # "do not download again" / not offered
    DELETED = "deleted"        # removed from storage


class SourceKind(enum.StrEnum):
    UPLOAD = "upload"
    RSS = "rss"
    IMPORT = "import"
    #: Página/arquivo baixado de um endereço pelo painel (Importar → URL).
    DOWNLOAD = "download"
