"""Magic-byte sniffing, the layer that catches mislabelled files."""

from __future__ import annotations

from pathlib import Path

from app.library.formats import MAGIC_SIGNATURES, MOBI_OFFSET, MOBI_SIGNATURE


def sniff_signature(head: bytes) -> tuple[str, str] | None:
    if (
        len(head) >= MOBI_OFFSET + len(MOBI_SIGNATURE)
        and head[MOBI_OFFSET:MOBI_OFFSET + len(MOBI_SIGNATURE)] == MOBI_SIGNATURE
    ):
        return "mobi", "application/x-mobipocket-ebook"
    for signature, fmt, mime in MAGIC_SIGNATURES:
        if head.startswith(signature):
            return fmt, mime
    return None


def sniff_file(path: Path, *, size: int = 4096) -> tuple[str, str] | None:
    try:
        with path.open("rb") as handle:
            head = handle.read(size)
    except OSError:
        return None
    return sniff_signature(head)


def resolve_format(ext: str, head: bytes | None) -> tuple[str, str, bool]:
    """Combine extension + magic.

    Returns ``(format, mime, corrected)`` where ``corrected`` means the bytes
    won over the extension.
    """
    from app.library.formats import mime_for

    fmt = ext
    mime = mime_for(ext)
    if not head:
        return fmt, mime, False

    magic = sniff_signature(head)
    if not magic:
        return fmt, mime, False

    magic_fmt, magic_mime = magic
    if _compatible(magic_fmt, ext):
        # The extension is authoritative when it agrees with the magic bytes.
        # (EPUB is a ZIP container; we must report application/epub+zip, not
        # application/zip -- clients match on the exact MIME type.)
        return fmt, mime, False
    return magic_fmt, magic_mime, True


#: Container formats whose magic bytes are shared with several extensions.
_ZIP_BASED = frozenset({"zip", "epub", "cbz", "cb7", "cbt", "docx", "kepub", "odt"})
_RAR_BASED = frozenset({"rar", "cbr"})
_SEVENZ_BASED = frozenset({"7z", "cb7"})


def _compatible(magic_fmt: str, ext: str) -> bool:
    if magic_fmt == ext:
        return True
    if magic_fmt == "zip":
        return ext in _ZIP_BASED
    if magic_fmt == "rar":
        return ext in _RAR_BASED
    if magic_fmt == "7z":
        return ext in _SEVENZ_BASED
    # Image magic that opens "II*\x00"/"MM\x00*" also covers .tif/.tiff, etc.
    if magic_fmt == "tiff":
        return ext in {"tif", "tiff"}
    if magic_fmt == "jpg":
        return ext in {"jpg", "jpeg"}
    if magic_fmt == "mp3":
        return ext in {"mp3", "m4a", "m4b"}
    # Every Kindle container starts with "BOOKMOBI": the extension is what tells
    # MOBI (PalmDOC) apart from AZW3/KF8, and both use the same MIME type.
    if magic_fmt == "mobi":
        return ext in {"mobi", "azw", "azw3", "prc", "pdb"}
    return False
