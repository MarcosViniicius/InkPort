"""Format taxonomy: extensions, MIME types and magic signatures.

Single source of truth for "what kind of file is this". Everything else
(detection, conversion planning, OPDS content types) imports from here.
"""

from __future__ import annotations

IMAGE_EXTS = frozenset(
    {"jpg", "jpeg", "png", "gif", "bmp", "webp", "tif", "tiff", "avif", "heic"}
)
ARCHIVE_EXTS = frozenset({"cbz", "cbr", "cb7", "cbt", "zip", "rar", "7z"})
EBOOK_EXTS = frozenset(
    {"epub", "mobi", "azw", "azw3", "fb2", "txt", "docx", "rtf", "lit", "pdb", "prc"}
)
#: Web pages. Blog/RSS items are usually articles, not book files; we support
#: turning them into EPUB (Calibre, or the built-in extractor).
WEB_EXTS = frozenset({"html", "htm", "xhtml"})
DOCUMENT_EXTS = frozenset({"pdf", "djvu", "djv"})
AUDIO_EXTS = frozenset({"mp3", "m4b", "m4a", "flac", "ogg", "opus", "aac"})

ALL_EXTS = IMAGE_EXTS | ARCHIVE_EXTS | EBOOK_EXTS | DOCUMENT_EXTS | AUDIO_EXTS | WEB_EXTS

#: Extensions that are always "comic archives" regardless of contents.
COMIC_ARCHIVE_EXTS = frozenset({"cbz", "cbr", "cb7", "cbt"})

MIME_BY_EXT: dict[str, str] = {
    "epub": "application/epub+zip",
    "mobi": "application/x-mobipocket-ebook",
    "azw": "application/x-mobipocket-ebook",
    "azw3": "application/x-mobipocket-ebook",
    "fb2": "application/x-fictionbook+xml",
    "txt": "text/plain",
    "pdf": "application/pdf",
    "cbz": "application/vnd.comicbook+zip",
    "cbr": "application/vnd.comicbook-rar",
    "cb7": "application/x-cb7",
    "zip": "application/zip",
    "rar": "application/vnd.rar",
    "7z": "application/x-7z-compressed",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "bmp": "image/bmp",
    "webp": "image/webp",
    "tif": "image/tiff",
    "tiff": "image/tiff",
    "mp3": "audio/mpeg",
    "m4b": "audio/mp4",
    "m4a": "audio/mp4",
    "flac": "audio/flac",
    "ogg": "audio/ogg",
    "opus": "audio/opus",
    "html": "text/html",
    "htm": "text/html",
    "xhtml": "application/xhtml+xml",
}

#: (signature, normalised format, MIME). Longest/most specific first.
MAGIC_SIGNATURES: tuple[tuple[bytes, str, str], ...] = (
    (b"%PDF", "pdf", "application/pdf"),
    (b"PK\x03\x04", "zip", "application/zip"),
    (b"Rar!\x1a\x07", "rar", "application/vnd.rar"),
    (b"7z\xbc\xaf\x27\x1c", "7z", "application/x-7z-compressed"),
    (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
    (b"\xff\xd8\xff", "jpg", "image/jpeg"),
    (b"GIF8", "gif", "image/gif"),
    (b"II*\x00", "tiff", "image/tiff"),
    (b"MM\x00*", "tiff", "image/tiff"),
    (b"fLaC", "flac", "audio/flac"),
    (b"OggS", "ogg", "audio/ogg"),
    (b"ID3", "mp3", "audio/mpeg"),
    (b"\xff\xfb", "mp3", "audio/mpeg"),
    (b"\x1f\x8b", "gz", "application/gzip"),
    (b"BM", "bmp", "image/bmp"),
)

#: "BOOKMOBI" lives at byte offset 60 in MOBI/AZW files.
MOBI_OFFSET = 60
MOBI_SIGNATURE = b"BOOKMOBI"

CONTENT_EBOOK = "ebook"
CONTENT_COMIC = "comic"
CONTENT_DOCUMENT = "document"
CONTENT_IMAGE = "image"
CONTENT_AUDIO = "audio"
CONTENT_UNKNOWN = "unknown"

PDF_MEDIA_TYPE = "application/pdf"
EPUB_MEDIA_TYPE = "application/epub+zip"


def content_type_for(fmt: str) -> str:
    if fmt in EBOOK_EXTS or fmt in WEB_EXTS:
        return CONTENT_EBOOK
    if fmt in DOCUMENT_EXTS:
        return CONTENT_DOCUMENT
    if fmt in IMAGE_EXTS:
        return CONTENT_IMAGE
    if fmt in AUDIO_EXTS:
        return CONTENT_AUDIO
    if fmt in ARCHIVE_EXTS:
        return CONTENT_COMIC
    return CONTENT_UNKNOWN


def mime_for(fmt: str) -> str:
    return MIME_BY_EXT.get(fmt, "application/octet-stream")


def media_type_for(fmt: str, stored: str | None = None) -> str:
    """Canonical MIME type for a format.

    The stored value is only used for unknown formats. This keeps OPDS
    acquisition links correct even when the database holds a generic
    container type (e.g. ``application/zip`` for an EPUB). Clients such as
    CrossPoint match the type exactly, so a wrong type hides the book.
    """
    canonical = MIME_BY_EXT.get((fmt or "").lower().lstrip("."))
    if canonical:
        return canonical
    return stored or "application/octet-stream"


def normalize_ext(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def is_supported_ext(fmt: str) -> bool:
    return fmt in ALL_EXTS
