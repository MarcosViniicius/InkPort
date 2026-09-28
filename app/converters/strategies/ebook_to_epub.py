"""Text-like ebook formats -> EPUB, in pure Python.

Covers what used to need Calibre on the input side: TXT, FB2, DOCX, RTF and
MOBI/AZW/PDB (via the ``mobi`` reader package, which is a library, not a tool).
"""

from __future__ import annotations

import posixpath
import re
import shutil
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

from lxml import etree

from app.converters.base import BaseConverter, ConversionError, ConversionRequest, ConversionResult
from app.converters.epub.model import EpubMeta
from app.converters.epub.text_builder import read_cover, write_text_epub

#: Inputs handled here.
TEXT_INPUTS = {"txt"}
XML_INPUTS = {"fb2"}
DOCX_INPUTS = {"docx", "doc", "odt"}
RTF_INPUTS = {"rtf"}
KINDLE_INPUTS = {"mobi", "azw", "azw3", "pdb", "prc"}

def _output(request: ConversionRequest, name: str) -> Path:
    """Output path inside the job workdir (created if the caller did not)."""
    request.workdir.mkdir(parents=True, exist_ok=True)
    return request.workdir / name


#: Headings inside a plain-text file.
_HEADING_PATTERNS = (
    re.compile(r"^\s*(cap[íi]tulo|chapter|parte|part|se[çc][ãa]o|section|livro|book)\b.*$", re.I),
    re.compile(r"^\s*\d{1,3}(\.\d{1,3})*\s+\S.*$"),
    re.compile(r"^[A-ZÁÉÍÓÚÂÊÔÃÕÇ0-9 ,;:!?'\-]{6,70}$"),
)


class TextToEpubConverter(BaseConverter):
    """TXT -> EPUB, splitting chapters by simple heading heuristics."""

    name = "text_to_epub"
    priority = 58

    def can_handle(self, request: ConversionRequest) -> bool:
        fmt = request.detection.format
        return request.target_format == "epub" and fmt in TEXT_INPUTS | RTF_INPUTS

    def supports_pair(self, source_fmt: str, target_fmt: str) -> bool:
        return source_fmt in TEXT_INPUTS | RTF_INPUTS and target_fmt == "epub"

    def run(self, request: ConversionRequest) -> ConversionResult:
        text = _read_text(request.source, request.detection.format)
        if not text.strip():
            raise ConversionError("O arquivo não tem texto legível.")
        chapters = split_text_chapters(text)
        out = _output(request, "output.epub")
        write_text_epub(
            out,
            meta=_meta(request, title=request.metadata.title or request.source.stem),
            chapters=chapters,
            images=[],
            cover=read_cover(request.options.get("cover_path"))
        )
        request.report(100, "EPUB pronto")
        return ConversionResult(
            output_path=out,
            converter=self.name,
            message=f"{len(chapters)} capítulo(s)",
        )


class Fb2ToEpubConverter(BaseConverter):
    """FB2 -> EPUB (FictionBook is XML: text, notes and inline images)."""

    name = "fb2_to_epub"
    priority = 58

    def can_handle(self, request: ConversionRequest) -> bool:
        return request.target_format == "epub" and request.detection.format in XML_INPUTS

    def supports_pair(self, source_fmt: str, target_fmt: str) -> bool:
        return source_fmt in XML_INPUTS and target_fmt == "epub"

    def run(self, request: ConversionRequest) -> ConversionResult:
        try:
            root = etree.parse(str(request.source)).getroot()
        except (etree.XMLSyntaxError, OSError) as exc:
            raise ConversionError(f"FB2 ilegível: {exc}") from exc

        images, image_names = _fb2_images(root)
        title, author, language = _fb2_metadata(root, request)
        chapters: list[tuple[str, str]] = []
        for section in _fb2_sections(root):
            chapters.append((_fb2_title(section), _fb2_body(section, image_names)))
        if not chapters:
            raise ConversionError("FB2 sem conteúdo legível.")
        out = _output(request, "output.epub")
        write_text_epub(
            out,
            meta=EpubMeta(
                title=title,
                author=author,
                language=language or "pt",
                identifier=request.source.name,
                generator="opds-server (fb2)",
            ),
            chapters=chapters,
            images=images,
            cover=read_cover(request.options.get("cover_path"))
        )
        request.report(100, "EPUB pronto")
        return ConversionResult(
            output_path=out, converter=self.name, message=f"{len(chapters)} capítulo(s)"
        )


class DocxToEpubConverter(BaseConverter):
    """DOCX -> EPUB with headings, paragraphs, lists and images."""

    name = "docx_to_epub"
    priority = 58

    def can_handle(self, request: ConversionRequest) -> bool:
        return request.target_format == "epub" and request.detection.format in DOCX_INPUTS

    def supports_pair(self, source_fmt: str, target_fmt: str) -> bool:
        return source_fmt in DOCX_INPUTS and target_fmt == "epub"

    def run(self, request: ConversionRequest) -> ConversionResult:
        if request.detection.format != "docx":
            raise ConversionError(
                f".{request.detection.format} não é suportado sem converter antes "
                "(só .docx é lido nativamente)."
            )
        try:
            from docx import Document
            from docx.table import Table
            from docx.text.paragraph import Paragraph
        except ImportError as exc:  # pragma: no cover - declared dependency
            raise ConversionError("python-docx não está instalado.") from exc

        document = Document(str(request.source))
        images, image_names = _docx_images(document)
        chapters: list[tuple[str, str]] = []
        current_title = request.metadata.title or request.source.stem
        current: list[str] = []

        for block in _iter_docx_blocks(document, Paragraph, Table):
            if isinstance(block, Paragraph):
                text = block.text.strip()
                style = (block.style.name or "").lower() if block.style is not None else ""
                if text and (style.startswith("heading") or style in {"title", "subtitle"}):
                    if current:
                        chapters.append((current_title, "\n".join(current)))
                        current = []
                    current_title = text
                    continue
                if text:
                    if style.startswith("list"):
                        current.append(f"<p>• {escape(text)}</p>")
                    else:
                        current.append(f"<p>{escape(text)}</p>")
                for rid in _docx_paragraph_images(block):
                    name = image_names.get(rid)
                    if name:
                        current.append(f'<p><img src="{name}" alt=""/></p>')
            else:
                for row in block.rows:
                    cells = [cell.text.strip() for cell in row.cells]
                    current.append("<p>" + " | ".join(escape(cell) for cell in cells) + "</p>")
        if current:
            chapters.append((current_title, "\n".join(current)))
        if not chapters:
            raise ConversionError("DOCX sem conteúdo legível.")

        out = _output(request, "output.epub")
        write_text_epub(
            out,
            meta=_meta(request, title=request.metadata.title or request.source.stem),
            chapters=chapters,
            images=images,
            cover=read_cover(request.options.get("cover_path"))
        )
        request.report(100, "EPUB pronto")
        return ConversionResult(
            output_path=out, converter=self.name, message=f"{len(chapters)} capítulo(s)"
        )


class KindleToEpubConverter(BaseConverter):
    """MOBI/AZW/AZW3/PDB/PRC -> EPUB, using the pure-Python ``mobi`` reader."""

    name = "kindle_to_epub"
    priority = 58

    def can_handle(self, request: ConversionRequest) -> bool:
        return request.target_format == "epub" and request.detection.format in KINDLE_INPUTS

    def supports_pair(self, source_fmt: str, target_fmt: str) -> bool:
        return source_fmt in KINDLE_INPUTS and target_fmt == "epub"

    def run(self, request: ConversionRequest) -> ConversionResult:
        try:
            import mobi  # type: ignore
        except ImportError as exc:  # pragma: no cover - declared dependency
            raise ConversionError(
                "Para abrir MOBI/AZW o pacote 'mobi' precisa estar instalado."
            ) from exc

        workdir = Path(tempfile.mkdtemp(prefix="kindle_in_"))
        unpacked: Path | None = None
        try:
            request.report(15, "Descompactando o arquivo Kindle")
            try:
                result = mobi.extract(str(request.source))
            except Exception as exc:  # noqa: BLE001 - one clear message
                raise ConversionError(f"Não consegui ler o arquivo Kindle: {exc}") from exc

            # mobi.extract returns (working directory, main file). The main file
            # is an EPUB for KF8 books (mobi8/) or HTML for older MOBI (mobi7/).
            directory = Path(result[0]) if isinstance(result, (tuple, list)) else Path(result)
            unpacked = directory
            main = (
                Path(result[1])
                if isinstance(result, (tuple, list)) and len(result) > 1
                else None
            )
            out = _output(request, "output.epub")

            if main is not None and main.is_file() and main.suffix.lower() == ".epub":
                shutil.copyfile(main, out)
                request.report(100, "EPUB pronto")
                return ConversionResult(
                    output_path=out,
                    converter=self.name,
                    message="EPUB extraído do arquivo Kindle",
                )

            epub_candidate = _find_epub(directory)
            if epub_candidate is not None:
                shutil.copyfile(epub_candidate, out)
                request.report(100, "EPUB pronto")
                return ConversionResult(
                    output_path=out,
                    converter=self.name,
                    message="EPUB extraído do arquivo Kindle",
                )

            html_file = _find_html(directory)
            if html_file is None:
                raise ConversionError("O arquivo Kindle não tem conteúdo legível.")
            body = html_file.read_text(encoding="utf-8", errors="replace")
            write_text_epub(
                out,
                meta=_meta(request, title=request.metadata.title or request.source.stem),
                chapters=[(request.metadata.title or request.source.stem, _html_body(body))],
                images=_kindle_images(directory),
            cover=read_cover(request.options.get("cover_path"))
            )
            request.report(100, "EPUB pronto")
            return ConversionResult(
                output_path=out, converter=self.name, message="Convertido do Kindle"
            )
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
            if unpacked is not None:
                # The mobi package unpacks into the system temp dir: clean it.
                shutil.rmtree(unpacked, ignore_errors=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _meta(request: ConversionRequest, *, title: str) -> EpubMeta:
    metadata = request.metadata
    return EpubMeta(
        title=title,
        author=metadata.author or "",
        language=metadata.language or "pt",
        publisher=metadata.publisher or "",
        description=metadata.description or "",
        date=metadata.published or None,
        subjects=list(metadata.tags or []),
        identifier=request.source.name,
        generator="opds-server",
    )


def _read_text(source: Path, fmt: str) -> str:
    if fmt in RTF_INPUTS:
        try:
            from striprtf.striprtf import rtf_to_text
        except ImportError as exc:  # pragma: no cover - declared dependency
            raise ConversionError("Para abrir RTF o pacote 'striprtf' é necessário.") from exc
        raw = source.read_bytes()
        for encoding in ("utf-8", "cp1252", "latin-1"):
            try:
                decoded = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:  # pragma: no cover - latin-1 never fails
            decoded = raw.decode("utf-8", "replace")
        return rtf_to_text(decoded)
    raw = source.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")  # pragma: no cover


def split_text_chapters(text: str, *, max_lines: int = 6000) -> list[tuple[str, str]]:
    """Split plain text into ``(title, xhtml)`` chapters."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    chapters: list[list[str]] = []
    titles: list[str] = []
    current: list[str] = []
    current_title = ""
    for line in lines:
        if _is_heading(line) and len(current) >= 3:
            titles.append(current_title)
            chapters.append(current)
            current_title = line.strip()
            current = [f"<h2>{escape(line.strip())}</h2>"]
            continue
        current.append(f"<p>{escape(line.strip())}</p>" if line.strip() else "")
        if len(current) >= max_lines:
            titles.append(current_title)
            chapters.append(current)
            current, current_title = [], ""
    titles.append(current_title)
    chapters.append(current)

    if len(chapters) == 1:
        title = current_title or "Texto"
        return [(title, "\n".join(chapters[0]))]
    result: list[tuple[str, str]] = []
    for title, body in zip(titles, chapters, strict=False):
        body_html = "\n".join(part for part in body if part)
        if body_html.strip():
            result.append((title or "Seção", body_html))
    if result:
        return result
    return [("Texto", f"<p>{escape(text)}</p>")]


def _is_heading(line: str) -> bool:
    stripped = line.strip()
    if not stripped or len(stripped) > 80:
        return False
    return any(pattern.match(stripped) for pattern in _HEADING_PATTERNS)


# --- FB2 ------------------------------------------------------------------
FB2_NS = "{http://www.gribuser.ru/xml/fictionbook/2.0}"


def _fb2_tag(element) -> str:
    return element.tag.replace(FB2_NS, "") if isinstance(element.tag, str) else ""


def _fb2_metadata(root, request: ConversionRequest) -> tuple[str, str, str]:
    title = request.metadata.title or ""
    author = request.metadata.author or ""
    language = request.metadata.language or ""
    info = root.find(f".//{FB2_NS}title-info")
    if info is not None:
        node = info.find(f"{FB2_NS}book-title")
        if node is not None and node.text and not title:
            title = node.text.strip()
        person = info.find(f"{FB2_NS}author")
        if person is not None and not author:
            parts = [
                (child.text or "").strip()
                for child in person
                if _fb2_tag(child) in {"first-name", "middle-name", "last-name", "nickname"}
            ]
            author = " ".join(part for part in parts if part)
        node = info.find(f"{FB2_NS}lang")
        if node is not None and node.text and not language:
            language = node.text.strip()
    return title or request.source.stem, author, language


def _fb2_sections(root):
    body = root.find(f"{FB2_NS}body")
    if body is None:
        return []
    return [child for child in body if _fb2_tag(child) == "section"]


def _fb2_title(section) -> str:
    title = section.find(f"{FB2_NS}title")
    if title is None:
        return ""
    return " ".join((node.text or "").strip() for node in title.iter() if node.text).strip()[:200]


def _fb2_body(section, image_names: dict[str, str]) -> str:
    parts: list[str] = []
    for child in section:
        tag = _fb2_tag(child)
        if tag == "title":
            continue
        if tag == "section":
            inner = _fb2_title(child)
            if inner:
                parts.append(f"<h2>{escape(inner)}</h2>")
            parts.append(_fb2_body(child, image_names))
        elif tag in {"p", "subtitle", "cite", "epigraph", "text-author"}:
            text = _fb2_inline(child, image_names)
            if text.strip():
                parts.append(f"<p>{text}</p>")
        elif tag == "image":
            name = image_names.get((child.get("{http://www.w3.org/1999/xlink}href") or "").lstrip("#"))
            if name:
                parts.append(f'<p><img src="{name}" alt=""/></p>')
    return "\n".join(parts)


def _fb2_inline(element, image_names: dict[str, str]) -> str:
    parts: list[str] = []
    if element.text:
        parts.append(escape(element.text))
    for child in element:
        tag = _fb2_tag(child)
        inner = _fb2_inline(child, image_names)
        if tag in {"emphasis", "strong", "strikethrough", "sub", "sup", "code"}:
            parts.append(inner)
        elif tag == "image":
            name = image_names.get((child.get("{http://www.w3.org/1999/xlink}href") or "").lstrip("#"))
            if name:
                parts.append(f'<img src="{name}" alt=""/>')
            else:
                parts.append(inner)
        elif tag == "a":
            parts.append(inner)
        else:
            parts.append(inner if inner else "")
    return "".join(parts)


def _fb2_images(root) -> tuple[list[tuple[str, bytes, str]], dict[str, str]]:
    """``[(filename, data, mime)]`` plus ``{binary id: filename}``."""
    import base64

    images: list[tuple[str, bytes, str]] = []
    names: dict[str, str] = {}
    for binary in root.iter(f"{FB2_NS}binary"):
        identifier = binary.get("id") or ""
        mime = binary.get("content-type") or "image/jpeg"
        try:
            data = base64.b64decode((binary.text or "").strip())
        except (ValueError, TypeError):
            continue
        ext = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}.get(
            mime, "jpg"
        )
        filename = f"{identifier or 'image'}.{ext}"
        images.append((filename, data, mime))
        names[identifier] = filename
    return images, names


# --- DOCX -----------------------------------------------------------------
def _iter_docx_blocks(document, paragraph_class, table_class):
    from docx.oxml.ns import qn

    body = document.element.body
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            yield paragraph_class(child, document)
        elif child.tag == qn("w:tbl"):
            yield table_class(child, document)


#: OOXML relationship namespace, used by inline drawings.
_DOCX_REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def _docx_paragraph_images(paragraph) -> list[str]:
    """Relationship ids of the images drawn inside this paragraph."""
    found: list[str] = []
    for node in paragraph._p.iter():
        if not isinstance(node.tag, str) or not node.tag.endswith("}blip"):
            continue
        rid = node.get(f"{_DOCX_REL_NS}embed") or node.get(f"{_DOCX_REL_NS}link")
        if rid:
            found.append(rid)
    return found


def _docx_images(document) -> tuple[list[tuple[str, bytes, str]], dict[str, str]]:
    images: list[tuple[str, bytes, str]] = []
    names: dict[str, str] = {}
    for rel_id, part in getattr(document.part, "related_parts", {}).items():
        content_type = getattr(part, "content_type", "") or ""
        if not content_type.startswith("image/"):
            continue
        ext = content_type.split("/")[-1].replace("jpeg", "jpg")
        filename = f"{rel_id}.{ext}"
        try:
            data = part.blob
        except Exception:  # noqa: BLE001 - a broken part must not break the book
            continue
        images.append((filename, data, content_type))
        names[rel_id] = filename
    return images, names


# --- Kindle ---------------------------------------------------------------
def _find_epub(path: Path) -> Path | None:
    for candidate in path.rglob("*.epub"):
        return candidate
    return None


def _find_html(path: Path) -> Path | None:
    candidates = sorted(
        (p for p in path.rglob("*.html")), key=lambda p: p.stat().st_size, reverse=True
    )
    return candidates[0] if candidates else None


def _kindle_images(path: Path) -> list[tuple[str, bytes, str]]:
    images: list[tuple[str, bytes, str]] = []
    for candidate in sorted(path.rglob("*")):
        if not candidate.is_file():
            continue
        ext = candidate.suffix.lower().lstrip(".")
        if ext not in {"jpg", "jpeg", "png", "gif", "webp", "svg"}:
            continue
        mime = {
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "png": "image/png",
            "gif": "image/gif",
            "webp": "image/webp",
            "svg": "image/svg+xml",
        }[ext]
        images.append((posixpath.basename(candidate.name), candidate.read_bytes(), mime))
    return images


def _html_body(markup: str) -> str:
    """Inner body of an unpacked Kindle HTML file."""
    match = re.search(r"<body[^>]*>(.*)</body>", markup, re.IGNORECASE | re.DOTALL)
    body = match.group(1) if match else markup
    body = re.sub(r"<script.*?</script>", "", body, flags=re.IGNORECASE | re.DOTALL)
    return body.strip() or "<p></p>"
