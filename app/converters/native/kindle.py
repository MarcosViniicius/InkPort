"""Native EPUB -> Kindle writer (AZW3/KF8 and legacy MOBI) in pure Python.

This module removes the dependency on Calibre's ``ebook-convert`` for the
Kindle targets.  It parses an EPUB with the standard library, then assembles a
PalmDB database containing either:

* ``azw3`` -- a standalone KF8 (Mobipocket 8) book: a MOBI header version 8,
  EXTH metadata (including the cover/thumbnail offsets), the "skeleton"/
  "fragment" indices, an NCX navigation index, an FDST flow table and the
  reconstructed XHTML/CSS/images; or
* ``mobi`` -- a legacy Mobipocket 6 book: a MOBI header version 6, EXTH
  metadata and the XHTML as plain PalmDOC text with ``recindex`` image links.

Everything is written from the format definition (derived from the public
MOBI/KF8 documentation and the behaviour of the well-known readers); no
external executable is invoked.  Only Pillow (already a core dependency) is
used, to normalise embedded images to JPEG.
"""

from __future__ import annotations

import logging
import posixpath
import re
import struct
import zipfile
import zlib
from contextlib import suppress
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

__all__ = ["KindleConversionError", "epub_to_kindle", "kindle_available"]

# --- Palm/MOBI constants ---------------------------------------------------
_PALM_HEADER_LENGTH = 78
_RECORD_HEADER_LENGTH = 8
#: 2020-01-01 00:00:00 expressed in the Palm epoch (1904-01-01).
_PALM_EPOCH_2020 = 3660595200
_TEXT_RECORD_SIZE = 4096
_MOBI6_HEADER_LENGTH = 0xE4  # 228
_MOBI8_HEADER_LENGTH = 0x108  # 264
_BOOK_TYPE = 2
_UTF8_CODEPAGE = 65001
_INDEX_NONE = 0xFFFFFFFF

# MOBI header fields, relative to the "MOBI" magic (record-0 offset minus 16).
_OFF_MOBI_LENGTH = 0x04
_OFF_MOBI_TYPE = 0x08
_OFF_CODEPAGE = 0x0C
_OFF_UNIQUE_ID = 0x10
_OFF_VERSION = 0x14
_OFF_FIRST_NON_BOOK = 0x40
_OFF_TITLE_OFFSET = 0x44
_OFF_TITLE_LENGTH = 0x48
_OFF_LOCALE = 0x4C
_OFF_MIN_VERSION = 0x58
_OFF_FIRST_IMAGE = 0x5C
_OFF_EXTH_FLAGS = 0x70
_OFF_DRM_OFFSET = 0x98
_OFF_DRM_COUNT = 0x9C
_OFF_FIRST_CONTENT = 0xB0
_OFF_LAST_CONTENT = 0xB2
_OFF_FDST_OFFSET = 0xB0
_OFF_FDST_COUNT = 0xB4
_OFF_FCIS_INDEX = 0xB8
_OFF_FCIS_COUNT = 0xBC
_OFF_FLIS_INDEX = 0xC0
_OFF_FLIS_COUNT = 0xC4
_OFF_EXTRA_FLAGS = 0xE0
_OFF_NCX_INDEX = 0xE4
_OFF_FRAG_INDEX = 0xE8
_OFF_SKEL_INDEX = 0xEC
_OFF_DATP_INDEX = 0xF0
_OFF_GUIDE_INDEX = 0xF4

# EXTH record types used here.
_EXTH_AUTHOR = 100
_EXTH_PUBLISHER = 101
_EXTH_DESCRIPTION = 103
_EXTH_PUBLISHED = 106
_EXTH_CONTRIBUTOR = 108
_EXTH_ASIN = 113
_EXTH_COVER_OFFSET = 201
_EXTH_THUMB_OFFSET = 202
_EXTH_DOC_TYPE = 501
_EXTH_UPDATED_TITLE = 503
_EXTH_LANGUAGE = 524

# INDX/TAGX plumbing.  Each tag entry is (tag id, values per entry, bit mask,
# is-control-separator).  The bit masks are chosen so a single control byte
# enables exactly the values written below.
_TAGX_END = b"\x00\x00\x00\x01"
_TAGX_SKELETON = [
    b"\x01\x01\x01\x00",  # 1: fragment count
    b"\x06\x02\x02\x00",  # 6: geometry (start, length)
    _TAGX_END,
]
_TAGX_FRAGMENT = [
    b"\x02\x01\x01\x00",  # 2: CNCX offset
    b"\x03\x01\x02\x00",  # 3: file number
    b"\x04\x01\x04\x00",  # 4: sequence number
    b"\x06\x02\x08\x00",  # 6: geometry (start, length)
    _TAGX_END,
]
# NCX tag table understood by KindleUnpack (see mobi_ncx.ncxExtract.parseNCX):
# tag 3 is the offset into the CNCX holding the label, tag 4 the heading level
# and tag 6 the "pos:fid" pair (fragment row, offset inside the fragment).
_TAGX_NCX = [
    b"\x03\x01\x01\x00",  # 3: CNCX offset of the label
    b"\x04\x01\x02\x00",  # 4: heading level (0 = flat table of contents)
    b"\x06\x02\x04\x00",  # 6: pos:fid (fragment row, offset)
    _TAGX_END,
]
_CTRL_SKELETON = 0x03
_CTRL_FRAGMENT = 0x0F
_CTRL_NCX = 0x07

_MIME_XHTML = {"application/xhtml+xml", "text/html"}
_MIME_CSS = "text/css"

#: Matches the inner content of the <body> element (case-insensitive, dotall).
_BODY_RE = re.compile(r"(?is)<body\b[^>]*>(.*)</body\s*>")
_SCRIPT_RE = re.compile(r"(?is)<script\b[^>]*>.*?</script\s*>")
_STYLE_RE = re.compile(r"(?is)<style\b[^>]*>(.*?)</style\s*>")
_LINK_CSS_RE = re.compile(
    r"(?is)<link\b[^>]*\bhref\s*=\s*[\"']([^\"']+)[\"'][^>]*/?>"
)
_IMG_TAG_RE = re.compile(r"(?is)<img\b[^>]*>")
_IMG_SRC_RE = re.compile(r"""(?is)\bsrc\s*=\s*["']([^"']+)["']""")
_IMG_RECINDEX_RE = re.compile(r"""(?is)\brecindex\s*=\s*["']?(\d+)["']?""")
_HEADING_RE = re.compile(r"(?is)<h[1-3]\b[^>]*>(.*?)</h[1-3]\s*>")
_TAGS_RE = re.compile(r"(?s)<[^>]+>")
_CSS_URL_RE = re.compile(r"""(?is)url\(\s*['"]?([^'")]+)['"]?\s*\)""")


class KindleConversionError(RuntimeError):
    """Raised when an EPUB cannot be turned into a Kindle file.

    The message is meant to be shown to the user (pt-BR).
    """


@dataclass(slots=True)
class _Chapter:
    title: str
    path: str
    body: str


@dataclass(slots=True)
class _Image:
    key: str
    data: bytes
    mime: str


@dataclass(slots=True)
class _Css:
    path: str
    text: str


@dataclass(slots=True)
class _Book:
    title: str = ""
    author: str = ""
    language: str = ""
    publisher: str = ""
    description: str = ""
    series: str = ""
    series_index: float | None = None
    chapters: list[_Chapter] = field(default_factory=list)
    images: list[_Image] = field(default_factory=list)
    css: list[_Css] = field(default_factory=list)
    #: Index into ``images`` of the cover, or ``None`` when there is no image.
    cover_index: int | None = None
    #: Index into ``images`` of the generated thumbnail, if any.
    thumb_index: int | None = None


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------
def kindle_available() -> bool:
    """Return ``True`` when the native Kindle backend can run.

    The writer is pure Python; the only optional dependency is Pillow, used to
    normalise embedded images.  Without it we could still write the container,
    but image normalisation would be skipped, so we report unavailability to
    keep the target honest.
    """
    try:
        import PIL  # noqa: F401
    except Exception:  # noqa: BLE001 - any import failure disables the backend
        return False
    return True


def epub_to_kindle(
    source_epub: Path,
    out_path: Path,
    *,
    target: str = "azw3",
    title: str = "",
    author: str = "",
    language: str = "",
    publisher: str = "",
    description: str = "",
    series: str = "",
    series_index: float | None = None,
) -> None:
    """Convert ``source_epub`` into a Kindle file at ``out_path``.

    ``target`` selects the container: ``"azw3"`` writes a standalone KF8 book
    (MOBI version 8) and ``"mobi"`` writes a legacy Mobipocket 6 book.  The
    keyword arguments override/complete the metadata found inside the EPUB.
    Raises :class:`KindleConversionError` on any failure.
    """
    target = (target or "").strip().lower()
    if target not in {"azw3", "mobi"}:
        raise KindleConversionError(
            f"Formato Kindle desconhecido: {target!r}. Use 'azw3' ou 'mobi'."
        )

    source = Path(source_epub)
    if not source.is_file():
        raise KindleConversionError(f"EPUB de origem não encontrado: {source}")

    book = _read_epub(source)
    if title:
        book.title = title
    if author:
        book.author = author
    if language:
        book.language = language
    if publisher:
        book.publisher = publisher
    if description:
        book.description = description
    if series:
        book.series = series
    if series_index is not None:
        book.series_index = series_index

    if not book.title:
        book.title = source.stem or "Sem título"
    if not book.chapters:
        raise KindleConversionError("O EPUB não contém capítulos XHTML para converter.")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    data = _build_kf8(book) if target == "azw3" else _build_mobi6(book)
    out_path.write_bytes(data)
    logger.info("kindle conversion done", extra={"target": target, "bytes": len(data)})


# ---------------------------------------------------------------------------
# EPUB reading
# ---------------------------------------------------------------------------
def _read_epub(source: Path) -> _Book:
    try:
        archive = zipfile.ZipFile(source)
    except zipfile.BadZipFile as exc:
        raise KindleConversionError("Arquivo EPUB inválido (não é um ZIP).") from exc

    with archive:
        names = set(archive.namelist())
        opf_path = _find_opf(archive, names)
        try:
            opf_bytes = archive.read(opf_path)
        except KeyError as exc:
            raise KindleConversionError(
                f"EPUB inválido: não foi possível ler {opf_path}."
            ) from exc
        try:
            opf = ET.fromstring(opf_bytes)
        except ET.ParseError as exc:
            raise KindleConversionError("EPUB inválido: content.opf malformado.") from exc

        base = posixpath.dirname(opf_path)
        metadata = _first_local(opf, "metadata")
        manifest = _first_local(opf, "manifest")
        spine = _first_local(opf, "spine")
        if manifest is None or spine is None:
            raise KindleConversionError("EPUB inválido: manifesto ou spine ausente.")

        items: dict[str, dict[str, str]] = {}
        for item in _iter_local(manifest, "item"):
            item_id = item.get("id")
            href = item.get("href")
            if not item_id or not href:
                continue
            items[item_id] = {
                "href": href,
                "media-type": (item.get("media-type") or "").strip().lower(),
                "properties": (item.get("properties") or "").strip(),
            }

        book = _Book()
        if metadata is not None:
            book.title = _dc_text(metadata, "title")
            book.author = _dc_text(metadata, "creator")
            book.language = _dc_text(metadata, "language")
            book.publisher = _dc_text(metadata, "publisher")
            book.description = _dc_text(metadata, "description")
            book.series, book.series_index = _series_meta(metadata)

        book.images = _read_images(archive, base, items)
        book.cover_index = _find_cover_index(metadata, items, base, book.images)
        book.thumb_index = _add_thumbnail(book)
        book.css = _read_styles(archive, base, items)
        book.chapters = _read_chapters(archive, base, items, spine)

    return book


def _find_opf(archive: zipfile.ZipFile, names: set[str]) -> str:
    container = "META-INF/container.xml"
    if container in names:
        try:
            root = ET.fromstring(archive.read(container))
        except ET.ParseError:
            root = None
        if root is not None:
            for element in root.iter():
                if element.tag.rsplit("}", 1)[-1] == "rootfile":
                    path = element.get("full-path")
                    if path:
                        return path.replace("\\", "/")
    # Fallback: the first .opf entry anywhere in the archive.
    for name in sorted(names):
        if name.lower().endswith(".opf"):
            return name
    raise KindleConversionError("EPUB inválido: content.opf não encontrado.")


def _read_chapters(
    archive: zipfile.ZipFile, base: str, items: dict[str, dict[str, str]], spine: ET.Element
) -> list[_Chapter]:
    chapters: list[_Chapter] = []
    for itemref in _iter_local(spine, "itemref"):
        item = items.get(itemref.get("idref") or "")
        if item is None or item["media-type"] not in _MIME_XHTML:
            continue
        href = item["href"].split("#", 1)[0]
        path = _join(base, href)
        if path not in archive.namelist():
            continue
        raw = archive.read(path).decode("utf-8", "replace")
        body = _BODY_RE.search(raw)
        inner = body.group(1) if body else raw
        inner = _SCRIPT_RE.sub("", inner)
        title = _chapter_title(inner, len(chapters) + 1)
        chapters.append(_Chapter(title=title, path=path, body=inner))
    return chapters


def _read_images(
    archive: zipfile.ZipFile, base: str, items: dict[str, dict[str, str]]
) -> list[_Image]:
    images: list[_Image] = []
    for item in items.values():
        if not item["media-type"].startswith("image/"):
            continue
        path = _join(base, item["href"])
        if path not in archive.namelist():
            continue
        data = _to_jpeg(archive.read(path))
        images.append(_Image(key=path, data=data, mime="image/jpeg"))
    return images


def _find_cover_index(
    metadata: ET.Element | None,
    items: dict[str, dict[str, str]],
    base: str,
    images: list[_Image],
) -> int | None:
    """Locate the cover image inside ``images`` from the OPF.

    EPUB 2 marks the cover with ``<meta name="cover" content="<item-id>"/>``;
    EPUB 3 recommends ``properties="cover-image"`` on the manifest item.  When
    neither is usable we fall back to the first image, mirroring what most
    readers do.
    """
    if not images:
        return None
    item_id = _cover_item_id(metadata, items)
    if item_id and item_id in items:
        path = _join(base, items[item_id]["href"])
        for index, image in enumerate(images):
            if image.key == path:
                return index
    return 0


def _cover_item_id(
    metadata: ET.Element | None, items: dict[str, dict[str, str]]
) -> str:
    if metadata is not None:
        for element in _iter_local(metadata, "meta"):
            name = (element.get("name") or "").strip().lower()
            if name == "cover":
                content = (element.get("content") or "").strip()
                if content:
                    return content
    for item_id, item in items.items():
        if "cover-image" in (item.get("properties") or "").split():
            return item_id
    return ""


def _add_thumbnail(book: _Book) -> int | None:
    """Append a small thumbnail of the cover and return its image index.

    The MOBI thumbnail (EXTH 202) must be its own record: if it pointed at the
    same record as the cover, KindleUnpack would mark that record "not used"
    while building the EPUB manifest and drop the real cover.  On any failure
    we return ``None`` so only EXTH 201 is written.
    """
    if book.cover_index is None or not book.images:
        return None
    data = _make_thumbnail(book.images[book.cover_index].data)
    if data is None:
        return None
    book.images.append(_Image(key="__cover_thumbnail__", data=data, mime="image/jpeg"))
    return len(book.images) - 1


def _make_thumbnail(data: bytes, max_side: int = 160) -> bytes | None:
    """Return a downscaled JPEG of ``data``, or ``None`` when unavailable."""
    try:
        from PIL import Image

        with Image.open(BytesIO(data)) as image:
            thumb = image.convert("RGB")
            thumb.thumbnail((max_side, max_side))
            buffer = BytesIO()
            thumb.save(buffer, "JPEG", quality=80)
            return buffer.getvalue()
    except Exception:  # noqa: BLE001 - a missing thumbnail is not fatal
        logger.warning("could not build the cover thumbnail", exc_info=True)
        return None


def _read_styles(
    archive: zipfile.ZipFile, base: str, items: dict[str, dict[str, str]]
) -> list[_Css]:
    styles: list[_Css] = []
    for item in items.values():
        if item["media-type"] != _MIME_CSS:
            continue
        path = _join(base, item["href"])
        if path not in archive.namelist():
            continue
        text = archive.read(path).decode("utf-8", "replace")
        styles.append(_Css(path=path, text=_SCRIPT_RE.sub("", text)))
    return styles


def _dc_text(metadata: ET.Element, name: str) -> str:
    for element in _iter_local(metadata, name):
        text = "".join(element.itertext()).strip()
        if text:
            return text
    return ""


def _series_meta(metadata: ET.Element) -> tuple[str, float | None]:
    series = ""
    index: float | None = None
    for element in _iter_local(metadata, "meta"):
        name = (element.get("name") or element.get("property") or "").strip().lower()
        content = (element.get("content") or "").strip() or "".join(element.itertext()).strip()
        if name in {"calibre:series", "belongs-to-collection"} and content:
            series = content
        elif name in {"calibre:series_index", "group-position"} and content:
            with suppress(ValueError):
                index = float(content)
    return series, index


def _chapter_title(inner: str, fallback: int) -> str:
    match = _HEADING_RE.search(inner)
    if match:
        text = _TAGS_RE.sub("", match.group(1)).strip()
        if text:
            return text[:120]
    return f"Capítulo {fallback}"


# ---------------------------------------------------------------------------
# shared rendering helpers
# ---------------------------------------------------------------------------
def _image_index(book: _Book) -> dict[str, int]:
    return {image.key: index for index, image in enumerate(book.images)}


def _resolve_image(src: str, chapter_path: str, images: dict[str, int]) -> int | None:
    target = src.split("#", 1)[0].split("?", 1)[0].strip()
    if not target or target.startswith(("http://", "https://", "data:", "kindle:")):
        return None
    directory = posixpath.dirname(chapter_path)
    candidate = _normalize(posixpath.join(directory, target))
    if candidate in images:
        return images[candidate]
    # Last resort: match by basename (EPUBs occasionally use odd casing).
    base = posixpath.basename(candidate)
    for key, index in images.items():
        if posixpath.basename(key) == base:
            return index
    return None


def _rewrite_img_tags(inner: str, chapter_path: str, images: dict[str, int], target: str) -> str:
    def replace(match: re.Match[str]) -> str:
        tag = match.group(0)
        src_match = _IMG_SRC_RE.search(tag)
        if not src_match:
            return tag
        index = _resolve_image(src_match.group(1), chapter_path, images)
        if index is None:
            return tag
        if target == "azw3":
            src = f"kindle:embed:{_base32(index + 1)}?mime=image/jpeg"
            return _IMG_SRC_RE.sub(f'src="{src}"', tag, count=1)
        # Legacy MOBI: drop the URL and point at the resource record instead.
        tag = _IMG_SRC_RE.sub("", tag)
        if _IMG_RECINDEX_RE.search(tag):
            return _IMG_RECINDEX_RE.sub(f'recindex="{index + 1:05d}"', tag, count=1)
        return tag.replace("<img", f'<img recindex="{index + 1:05d}"', 1)

    return _IMG_TAG_RE.sub(replace, inner)


def _inline_css(book: _Book, images: dict[str, int], target: str) -> str:
    chunks: list[str] = []
    for style in book.css:
        text = style.text
        for match in _STYLE_RE.finditer(text):
            chunks.append(match.group(1).strip())
        css = _STYLE_RE.sub("", text)
        if target == "azw3":
            path = style.path
            css = _CSS_URL_RE.sub(lambda m, p=path: _css_url(m, p, images, target), css)
        chunks.append(css.strip())
    return "\n".join(part for part in chunks if part)


def _css_url(match: re.Match[str], path: str, images: dict[str, int], target: str) -> str:
    value = match.group(1).strip()
    if value.startswith(("data:", "http://", "https://", "kindle:")):
        return match.group(0)
    index = _resolve_image(value, path, images)
    if index is None:
        return match.group(0)
    if target == "azw3":
        return f"url(kindle:embed:{_base32(index + 1)}?mime=image/jpeg)"
    return match.group(0)


# ---------------------------------------------------------------------------
# KF8 (AZW3) writer
# ---------------------------------------------------------------------------
def _build_kf8(book: _Book) -> bytes:
    images = book.images
    image_index = _image_index(book)
    css = _inline_css(book, image_index, "azw3")

    raw = bytearray()
    skeletons: list[tuple[int, int, int]] = []
    fragments: list[tuple[int, int, int, int, int]] = []
    cncx = bytearray()

    for number, chapter in enumerate(book.chapters):
        aid = _base32(number)
        head = _kf8_head(chapter.title, aid, css)
        skeleton_length = len(head) + len(b"</body></html>")
        skeleton_start = len(raw)
        insert_position = skeleton_start + len(head)
        raw += head + b"</body></html>"
        body = _rewrite_img_tags(chapter.body, chapter.path, image_index, "azw3").encode("utf-8")
        raw += body
        skeletons.append((skeleton_start, skeleton_length, 1))
        path = f"P-//*[@aid='{aid}']".encode("ascii")
        fragments.append((insert_position, len(cncx), number, number, len(body)))
        cncx += _vwi(len(path)) + path

    text = bytes(raw)
    text_records = _split_records(text)

    chunk_header = _indx(
        [_indx_label(b"0000000000") + bytes([_CTRL_FRAGMENT]) + b"\x80" * 5],
        _TAGX_FRAGMENT,
        record_count=1,
        entry_count=len(fragments),
        nctoc=1,
    )
    chunk_entries = [
        _indx_label(str(insert).encode("ascii"))
        + bytes([_CTRL_FRAGMENT])
        + _vwi(offset)
        + _vwi(file_number)
        + _vwi(sequence)
        + _vwi(0)
        + _vwi(length)
        for insert, offset, file_number, sequence, length in fragments
    ]
    chunk_data = _indx(
        chunk_entries, [], record_count=len(chunk_entries), entry_count=0, nctoc=0
    )

    skel_entries = [
        _indx_label(b"SKEL%010d" % number)
        + bytes([_CTRL_SKELETON])
        + _vwi(count)
        + _vwi(start)
        + _vwi(length)
        for number, (start, length, count) in enumerate(skeletons)
    ]
    skeleton_header = _indx(
        [_indx_label(b"SKEL0000000000") + bytes([0])],
        _TAGX_SKELETON,
        record_count=1,
        entry_count=len(skel_entries),
        nctoc=0,
    )
    skeleton_data = _indx(
        skel_entries, [], record_count=len(skel_entries), entry_count=0, nctoc=0
    )

    ncx_header, ncx_data, ncx_cncx = _build_ncx(book)

    first_non_book = 1 + len(text_records)
    chunk_header_index = first_non_book
    skeleton_header_index = chunk_header_index + 3
    ncx_header_index = chunk_header_index + 5
    first_image_index = chunk_header_index + 8
    fdst_index = first_image_index + len(images)
    flis_index = fdst_index + 1
    fcis_index = fdst_index + 2
    eof_index = fdst_index + 3

    first_image = first_image_index if images else _INDEX_NONE
    record0 = _kf8_record0(
        book=book,
        text_length=len(text),
        text_record_count=len(text_records),
        first_non_book=first_non_book,
        first_image=first_image,
        fdst_index=fdst_index,
        fcis_index=fcis_index,
        flis_index=flis_index,
        chunk_header_index=chunk_header_index,
        skeleton_header_index=skeleton_header_index,
        ncx_index=ncx_header_index,
    )

    records: list[bytes] = [record0, *text_records]
    records.append(chunk_header)
    records.append(chunk_data)
    records.append(bytes(cncx))
    records.append(skeleton_header)
    records.append(skeleton_data)
    records.append(ncx_header)
    records.append(ncx_data)
    records.append(ncx_cncx)
    records.extend(image.data for image in images)
    records.append(b"FDST" + struct.pack(">LL", 12, 1) + struct.pack(">LL", 0, len(text)))
    records.append(_flis_record())
    records.append(_fcis_record(len(text)))
    records.append(b"\xe9\x8e\r\n")
    if len(records) - 1 != eof_index:  # pragma: no cover - internal consistency
        raise KindleConversionError("Erro interno ao montar o índice KF8.")
    return _write_pdb(records, book.title)


def _build_ncx(book: _Book) -> tuple[bytes, bytes, bytes]:
    """Build the KF8 navigation index (INDX header, data, CNCX).

    Each chapter becomes one flat NCX entry: the label lives in the CNCX and
    the position is stored as the ``pos:fid`` pair the KF8 reader resolves
    through the fragment index.  Because every chapter is written as its own
    skeleton/part, the fragment row equals the chapter number and the offset is
    zero, i.e. the very start of the chapter.
    """
    cncx = bytearray()
    entries: list[bytes] = []
    for number, chapter in enumerate(book.chapters):
        label = chapter.title.encode("utf-8")
        label_offset = len(cncx)
        cncx += _vwi(len(label)) + label
        entries.append(
            _indx_label(f"NCX{number:010d}".encode("ascii"))
            + bytes([_CTRL_NCX])
            + _vwi(label_offset)
            + _vwi(0)  # heading level: a flat, single-level list
            + _vwi(number)  # fragment row (one fragment per chapter)
            + _vwi(0)  # offset inside the fragment: chapter start
        )
    header = _indx(
        [_indx_label(b"0000000000") + bytes([_CTRL_NCX]) + b"\x80" * 4],
        _TAGX_NCX,
        record_count=1,
        entry_count=len(entries),
        nctoc=1,
    )
    data = _indx(entries, [], record_count=len(entries), entry_count=0, nctoc=0)
    return header, data, bytes(cncx)


def _kf8_head(chapter_title: str, aid: str, css: str) -> bytes:
    style = f"<style type=\"text/css\">{css}</style>" if css else ""
    title = _escape_xml(chapter_title)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml"><head>'
        f"<title>{title}</title>"
        '<meta http-equiv="Content-Type" content="text/html; charset=utf-8"/>'
        f"{style}"
        f'</head><body aid="{aid}">'
    ).encode()


def _kf8_record0(
    *,
    book: _Book,
    text_length: int,
    text_record_count: int,
    first_non_book: int,
    first_image: int,
    fdst_index: int,
    fcis_index: int,
    flis_index: int,
    chunk_header_index: int,
    skeleton_header_index: int,
    ncx_index: int,
) -> bytes:
    header = bytearray(_MOBI8_HEADER_LENGTH)
    header[0:4] = b"MOBI"
    _put_u32(header, _OFF_MOBI_LENGTH, _MOBI8_HEADER_LENGTH)
    _put_u32(header, _OFF_MOBI_TYPE, _BOOK_TYPE)
    _put_u32(header, _OFF_CODEPAGE, _UTF8_CODEPAGE)
    _put_u32(header, _OFF_UNIQUE_ID, _unique_id(book.title))
    _put_u32(header, _OFF_VERSION, 8)
    for offset in range(0x18, 0x40, 4):  # orthographic/inflection/extra indices
        _put_u32(header, offset, _INDEX_NONE)
    _put_u32(header, _OFF_FIRST_NON_BOOK, first_non_book)
    _put_u32(header, _OFF_LOCALE, _locale_code(book.language))
    _put_u32(header, _OFF_MIN_VERSION, 8)
    _put_u32(header, _OFF_FIRST_IMAGE, first_image)
    _put_u32(header, _OFF_EXTH_FLAGS, 0x50)
    _put_u32(header, _OFF_DRM_OFFSET, _INDEX_NONE)
    _put_u32(header, _OFF_DRM_COUNT, _INDEX_NONE)
    _put_u32(header, _OFF_FDST_OFFSET, fdst_index)
    _put_u32(header, _OFF_FDST_COUNT, 1)
    _put_u32(header, _OFF_FCIS_INDEX, fcis_index)
    _put_u32(header, _OFF_FCIS_COUNT, 1)
    _put_u32(header, _OFF_FLIS_INDEX, flis_index)
    _put_u32(header, _OFF_FLIS_COUNT, 1)
    _put_u32(header, _OFF_EXTRA_FLAGS, 0)
    _put_u32(header, _OFF_NCX_INDEX, ncx_index)
    _put_u32(header, _OFF_FRAG_INDEX, chunk_header_index)
    _put_u32(header, _OFF_SKEL_INDEX, skeleton_header_index)
    _put_u32(header, _OFF_DATP_INDEX, _INDEX_NONE)
    _put_u32(header, _OFF_GUIDE_INDEX, _INDEX_NONE)

    exth = _exth(_kf8_exth_entries(book))
    title = book.title.encode("utf-8")
    _put_u32(header, _OFF_TITLE_OFFSET, 16 + _MOBI8_HEADER_LENGTH + len(exth))
    _put_u32(header, _OFF_TITLE_LENGTH, len(title))
    palmdoc = struct.pack(
        ">HHIHHHH", 1, 0, text_length, text_record_count, _TEXT_RECORD_SIZE, 0, 0
    )
    return palmdoc + bytes(header) + exth + title + b"\x00" * 2048


def _kf8_exth_entries(book: _Book) -> list[tuple[int, bytes]]:
    entries: list[tuple[int, bytes]] = []
    if book.author:
        entries.append((_EXTH_AUTHOR, book.author.encode("utf-8")))
    if book.publisher:
        entries.append((_EXTH_PUBLISHER, book.publisher.encode("utf-8")))
    if book.description:
        entries.append((_EXTH_DESCRIPTION, book.description.encode("utf-8")))
    if book.series:
        # Not a standard MOBI field; expose it as a contributor-free hint only
        # through the ASIN-independent "contributor" would be misleading, so
        # series is intentionally kept out of the binary (see module notes).
        logger.debug("series metadata is not representable in the MOBI container")
    entries.append((_EXTH_UPDATED_TITLE, book.title.encode("utf-8")))
    entries.append((_EXTH_DOC_TYPE, b"EBOK"))
    if book.language:
        entries.append((_EXTH_LANGUAGE, book.language.encode("utf-8")))
    if book.images and book.cover_index is not None:
        # 201 = cover image offset, 202 = thumbnail offset.  Both are indexes
        # into the resource records that follow the text (first image = 0).
        entries.append((_EXTH_COVER_OFFSET, struct.pack(">L", book.cover_index)))
        if book.thumb_index is not None:
            entries.append((_EXTH_THUMB_OFFSET, struct.pack(">L", book.thumb_index)))
    return entries


# ---------------------------------------------------------------------------
# legacy MOBI 6 writer
# ---------------------------------------------------------------------------
def _build_mobi6(book: _Book) -> bytes:
    image_index = _image_index(book)
    html = _mobi6_html(book, image_index)
    text = html.encode("utf-8")
    text_records = _split_records(text)

    first_non_book = 1 + len(text_records)
    first_image_index = first_non_book
    fcis_index = first_image_index + len(book.images)
    flis_index = fcis_index + 1
    eof_index = flis_index + 1
    first_image = first_image_index if book.images else _INDEX_NONE
    record0 = _mobi6_record0(
        book=book,
        text_length=len(text),
        text_record_count=len(text_records),
        first_non_book=first_non_book,
        first_image=first_image,
        fcis_index=fcis_index,
        flis_index=flis_index,
    )

    records: list[bytes] = [record0, *text_records]
    records.extend(image.data for image in book.images)
    records.append(_flis_record())
    records.append(_fcis_record(len(text)))
    records.append(b"\xe9\x8e\r\n")
    if len(records) - 1 != eof_index:  # pragma: no cover - internal consistency
        raise KindleConversionError("Erro interno ao montar o MOBI.")
    return _write_pdb(records, book.title)


def _mobi6_html(book: _Book, image_index: dict[str, int]) -> str:
    css = _inline_css(book, image_index, "mobi")
    title = _escape_xml(book.title)
    style = f"<style type=\"text/css\">{css}</style>" if css else ""
    parts = [
        f"<html><head><title>{title}</title>"
        '<meta http-equiv="content-type" content="text/html; charset=utf-8"/>'
        f"{style}</head><body>"
    ]
    for number, chapter in enumerate(book.chapters):
        if number:
            parts.append("<mbp:pagebreak/>")
        parts.append(_rewrite_img_tags(chapter.body, chapter.path, image_index, "mobi"))
    parts.append("</body></html>")
    return "".join(parts)


def _mobi6_record0(
    *,
    book: _Book,
    text_length: int,
    text_record_count: int,
    first_non_book: int,
    first_image: int,
    fcis_index: int,
    flis_index: int,
) -> bytes:
    header = bytearray(_MOBI6_HEADER_LENGTH)
    header[0:4] = b"MOBI"
    _put_u32(header, _OFF_MOBI_LENGTH, _MOBI6_HEADER_LENGTH)
    _put_u32(header, _OFF_MOBI_TYPE, _BOOK_TYPE)
    _put_u32(header, _OFF_CODEPAGE, _UTF8_CODEPAGE)
    _put_u32(header, _OFF_UNIQUE_ID, _unique_id(book.title))
    _put_u32(header, _OFF_VERSION, 6)
    for offset in range(0x18, 0x40, 4):
        _put_u32(header, offset, _INDEX_NONE)
    _put_u32(header, _OFF_FIRST_NON_BOOK, first_non_book)
    _put_u32(header, _OFF_LOCALE, _locale_code(book.language))
    _put_u32(header, _OFF_MIN_VERSION, 6)
    _put_u32(header, _OFF_FIRST_IMAGE, first_image)
    _put_u32(header, _OFF_EXTH_FLAGS, 0x50)
    _put_u32(header, _OFF_DRM_OFFSET, _INDEX_NONE)
    _put_u32(header, _OFF_DRM_COUNT, _INDEX_NONE)
    struct.pack_into(">HH", header, _OFF_FIRST_CONTENT, 1, 1)
    _put_u32(header, _OFF_FCIS_INDEX, fcis_index)
    _put_u32(header, _OFF_FCIS_COUNT, 1)
    _put_u32(header, _OFF_FLIS_INDEX, flis_index)
    _put_u32(header, _OFF_FLIS_COUNT, 1)
    struct.pack_into(">H", header, _OFF_EXTRA_FLAGS + 2, 0)  # traildata flags

    exth = _exth(_kf8_exth_entries(book))
    title = book.title.encode("utf-8")
    _put_u32(header, _OFF_TITLE_OFFSET, 16 + _MOBI6_HEADER_LENGTH + len(exth))
    _put_u32(header, _OFF_TITLE_LENGTH, len(title))
    palmdoc = struct.pack(
        ">HHIHHHH", 1, 0, text_length, text_record_count, _TEXT_RECORD_SIZE, 0, 0
    )
    payload = palmdoc + bytes(header) + exth + title
    return payload + b"\x00" * ((-len(payload)) % 4)


# ---------------------------------------------------------------------------
# PalmDB / MOBI primitives
# ---------------------------------------------------------------------------
def _write_pdb(records: list[bytes], name: str) -> bytes:
    count = len(records)
    header = bytearray(_PALM_HEADER_LENGTH)
    encoded_name = name.encode("utf-8")[:31]
    header[0 : len(encoded_name)] = encoded_name
    _put_u32(header, 36, _PALM_EPOCH_2020)
    _put_u32(header, 40, _PALM_EPOCH_2020)
    _put_u32(header, 44, _PALM_EPOCH_2020)
    header[60:64] = b"BOOK"
    header[64:68] = b"MOBI"
    _put_u32(header, 68, count * 2 - 1)
    struct.pack_into(">H", header, 76, count)

    position = _PALM_HEADER_LENGTH + _RECORD_HEADER_LENGTH * count + 2
    offsets: list[int] = []
    for record in records:
        offsets.append(position)
        position += len(record)

    out = bytearray(header)
    for index, offset in enumerate(offsets):
        out += struct.pack(">LBBH", offset, 0, 0, (index * 2) & 0xFFFF)
    out += b"\x00\x00"
    for record in records:
        out += record
    return bytes(out)


def _split_records(text: bytes) -> list[bytes]:
    records = [text[i : i + _TEXT_RECORD_SIZE] for i in range(0, len(text), _TEXT_RECORD_SIZE)]
    return records or [b""]


def _indx_label(label: bytes) -> bytes:
    if len(label) > 0xFF:
        raise KindleConversionError("Rótulo de índice grande demais.")
    return bytes([len(label)]) + label


def _indx(
    entries: list[bytes],
    tag_table: list[bytes],
    *,
    record_count: int,
    entry_count: int,
    nctoc: int,
) -> bytes:
    header = bytearray(192)
    header[0:4] = b"INDX"
    _put_u32(header, 4, 192)  # header length; also the start of the TAGX table
    _put_u32(header, 16, 1)  # header type
    _put_u32(header, 24, record_count)
    _put_u32(header, 28, _UTF8_CODEPAGE)
    _put_u32(header, 32, _INDEX_NONE)
    _put_u32(header, 36, entry_count)
    _put_u32(header, 52, nctoc)

    tagx = b""
    if tag_table:
        tagx = (
            b"TAGX"
            + struct.pack(">LL", 12 + 4 * len(tag_table), 1)
            + b"".join(tag_table)
        )
    position = 192 + len(tagx)
    offsets: list[int] = []
    for entry in entries:
        offsets.append(position)
        position += len(entry)
    _put_u32(header, 20, position)  # IDXT start
    idxt = b"IDXT" + b"".join(struct.pack(">H", offset) for offset in offsets)
    return bytes(header) + tagx + b"".join(entries) + idxt


def _exth(entries: list[tuple[int, bytes]]) -> bytes:
    body = b"".join(struct.pack(">LL", kind, 8 + len(data)) + data for kind, data in entries)
    total = 12 + len(body)
    return (
        b"EXTH"
        + struct.pack(">LL", total, len(entries))
        + body
        + b"\x00" * ((-total) % 4)
    )


def _flis_record() -> bytes:
    return b"FLIS" + struct.pack(
        ">LHHLLHHLLL", 8, 65, 0, 0, 0xFFFFFFFF, 1, 3, 3, 1, 0xFFFFFFFF
    )


def _fcis_record(text_length: int) -> bytes:
    return b"FCIS" + struct.pack(
        ">LLLLLLLLLLHHL", 20, 16, 2, 0, text_length, 0, 40, 0, 40, 8, 1, 1, 0
    )


def _vwi(value: int) -> bytes:
    """Encode a variable-width integer as used inside KF8 indices."""
    if value < 0:
        raise KindleConversionError("Valor negativo em índice KF8.")
    if value == 0:
        return b"\x80"
    groups: list[int] = []
    while value:
        groups.append(value & 0x7F)
        value >>= 7
    out = bytearray(reversed(groups))
    out[-1] |= 0x80
    return bytes(out)


def _base32(value: int, pad: int = 4) -> str:
    digits = "0123456789ABCDEFGHIJKLMNOPQRSTUV"
    encoded = ""
    if value == 0:
        encoded = "0"
    else:
        while value:
            value, remainder = divmod(value, 32)
            encoded = digits[remainder] + encoded
    return encoded.rjust(pad, "0")


def _to_jpeg(data: bytes) -> bytes:
    """Normalise an image to baseline JPEG, the safest KF8/MOBI format."""
    try:
        from PIL import Image

        with Image.open(BytesIO(data)) as image:
            if image.mode in {"RGBA", "LA", "P"}:
                rgba = image.convert("RGBA")
                background = Image.new("RGB", rgba.size, (255, 255, 255))
                background.paste(rgba, mask=rgba.split()[-1])
                converted = background
            else:
                converted = image.convert("RGB")
            buffer = BytesIO()
            converted.save(buffer, "JPEG", quality=85)
            return buffer.getvalue()
    except Exception:  # noqa: BLE001 - keep the original bytes when Pillow fails
        logger.warning("could not normalise image to JPEG", exc_info=True)
        return data


def _unique_id(title: str) -> int:
    return zlib.crc32(title.encode("utf-8")) & 0xFFFFFFFF


#: Mobipocket locale codes (primary language id).
_LOCALES = {
    "pt": 22,
    "en": 9,
    "es": 10,
    "fr": 12,
    "de": 7,
    "it": 16,
    "nl": 19,
    "ru": 25,
    "ja": 17,
    "zh": 4,
}


def _locale_code(language: str) -> int:
    primary = (language or "").replace("_", "-").split("-", 1)[0].lower()
    return _LOCALES.get(primary, 0)


def _put_u32(buffer: bytearray, offset: int, value: int) -> None:
    struct.pack_into(">L", buffer, offset, value & 0xFFFFFFFF)


def _escape_xml(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _iter_local(root: ET.Element, name: str):
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] == name:
            yield element


def _first_local(root: ET.Element, name: str) -> ET.Element | None:
    return next(_iter_local(root, name), None)


def _join(base: str, href: str) -> str:
    href = href.split("#", 1)[0].split("?", 1)[0]
    if not base:
        return _normalize(href)
    return _normalize(posixpath.join(base, href))


def _normalize(path: str) -> str:
    return posixpath.normpath(path.lstrip("/"))
