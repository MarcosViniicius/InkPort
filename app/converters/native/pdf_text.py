"""PDF (with a text layer) -> reflowable XHTML chapters -- no Calibre.

PyMuPDF gives us positioned spans. This module turns them back into a book:

* lines broken by the page layout are rejoined into paragraphs, with
  de-hyphenation (``inquie-\\ntação`` -> ``inquietação``) and ligatures the PDF
  encoder split into several spans (``codi`` + ``fi`` + ``cador``);
* headings are inferred from font size/weight/pattern and levelled as h1/h2/h3;
* running headers, footers and page numbers repeated across pages are dropped;
* two-column pages are read column by column instead of zig-zag;
* paragraphs that continue on the next page are merged;
* embedded images are preserved (full-page backgrounds/scans are not).

The result feeds :func:`app.converters.epub.text_builder.write_text_epub`.
"""

from __future__ import annotations

import html
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from app.converters.webpage.split import consolidate_chapters

__all__ = ["PdfText", "extract_pdf_text"]

#: ``1``, ``2.3``, ``3.2.1`` ... at the start of a short line: a section heading.
_NUMBERED_HEADING = re.compile(
    r"^\s*\d{1,2}(?:\.\d{1,2}){0,3}[.)]?\s+[A-Za-zÀ-ÖØ-öø-ÿ]{2,}"
)
#: A line that is only a page number (possibly surrounded by dashes).
_PAGE_NUMBER = re.compile(r"^\s*[-–—]?\s*(?:\d{1,4}|[ivxlcdm]{1,7})\s*[-–—]?\s*$", re.I)
#: Sentence-ending characters: a paragraph that ends with one does not merge on.
_TERMINAL = (".", "!", "?", ";", ":", "»", "\"", "'", ")", "]", "…")
#: Known standalone section titles in the languages we handle.
_TITLE_KEYWORDS = {
    "resumo", "abstract", "sumário", "sumario",
    "introdução", "introducao", "introduction",
    "conclusão", "conclusao", "conclusion", "considerações finais",
    "referências", "referencias", "references", "bibliografia", "bibliography",
    "agradecimentos", "acknowledgments", "acknowledgements",
    "apêndice", "apendice", "appendix", "prefácio", "prefacio", "foreword",
    "índice", "indice", "glossário", "glossario", "glossary",
}

_PT_WORDS = {"de", "que", "para", "com", "uma", "não", "os", "as", "dos", "das", "em", "por"}
_EN_WORDS = {"the", "and", "of", "to", "in", "is", "that", "for", "with", "as", "on", "by"}


@dataclass(slots=True)
class PdfText:
    """Extracted, reflowed content ready to become an EPUB."""

    title: str
    author: str
    language: str
    #: ``(chapter title, xhtml body)`` pairs for ``write_text_epub``.
    chapters: list[tuple[str, str]]
    #: ``(filename, data, mime)`` resources referenced by the chapter bodies.
    images: list[tuple[str, bytes, str]]
    page_count: int


@dataclass(slots=True)
class _Line:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    size: float
    font: str
    bold: bool
    page: int
    page_height: float
    edge: bool = False
    drop: bool = False
    heading: int = 0
    band: int = 0
    col: int = 0


@dataclass(slots=True)
class _Block:
    kind: str  # "para" | "heading" | "image"
    text: str = ""
    level: int = 0
    xhtml: str = ""
    page: int = 0
    size: float = 0.0
    #: Position on the page, so images can be interleaved with the text.
    y: float = 0.0
    x0: float = 0.0
    x1: float = 0.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def extract_pdf_text(source: Path, *, max_image_long_side: int = 1600) -> PdfText:
    """Extract text and figures from a PDF with a text layer.

    ``max_image_long_side`` caps the long side of kept images; pass ``0`` to
    keep them untouched.
    """
    pymupdf = _import_pymupdf()
    with pymupdf.open(str(source)) as doc:
        page_count = doc.page_count
        meta = dict(doc.metadata or {})

        pages: list[list[_Line]] = []
        for index in range(page_count):
            pages.append(_page_lines(doc.load_page(index), index))

        _mark_running_headers(pages, page_count)
        body_size, body_font = _body_metrics(pages)
        for index, lines in enumerate(pages):
            for line in lines:
                line.heading = _heading_level(line, body_size, body_font)
            _drop_figure_labels(doc.load_page(index), lines, body_size)
        _drop_title_block(pages)

        blocks: list[_Block] = []
        images: list[tuple[str, bytes, str]] = []
        seen_xrefs: set[int] = set()
        for index in range(page_count):
            page = doc.load_page(index)
            page_blocks, page_images = _page_blocks(
                pymupdf, doc, index, page, pages[index],
                max_image_long_side=max_image_long_side, seen_xrefs=seen_xrefs,
            )
            blocks.extend(page_blocks)
            images.extend(page_images)

    blocks = _merge_across_pages(blocks)

    detected = _title_author_from_text(pages)
    title = _clean_meta(meta.get("title")) or detected["title"]
    author = _clean_meta(meta.get("author")) or detected["author"]
    language = _guess_language(meta, pages) or "pt"

    chapters = _build_chapters(blocks, title or "Documento")
    return PdfText(
        title=title or "Documento",
        author=author,
        language=language,
        chapters=chapters,
        images=images,
        page_count=page_count,
    )


# ---------------------------------------------------------------------------
# Page -> lines
# ---------------------------------------------------------------------------
def _page_lines(page, index: int) -> list[_Line]:
    height = float(page.rect.height) or 1.0
    out: list[_Line] = []
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 0:
            continue
        for raw_line in block.get("lines", []):
            spans = [
                span for span in raw_line.get("spans", [])
                if span.get("text") and span["text"].strip()
            ]
            if not spans:
                continue
            text, bold = _join_spans(spans)
            if not text.strip():
                continue
            out.append(
                _Line(
                    text=text,
                    x0=min(s["bbox"][0] for s in spans),
                    y0=min(s["bbox"][1] for s in spans),
                    x1=max(s["bbox"][2] for s in spans),
                    y1=max(s["bbox"][3] for s in spans),
                    size=max((s.get("size") or 0.0) for s in spans),
                    font=max(spans, key=lambda s: len(s["text"]))["font"],
                    bold=bold,
                    page=index,
                    page_height=height,
                    edge=(
                        min(s["bbox"][1] for s in spans) < height * 0.10
                        or max(s["bbox"][3] for s in spans) > height * 0.90
                    ),
                )
            )
    return out


def _join_spans(spans) -> tuple[str, bool]:
    """Join a line's spans, inserting a space only at real word gaps.

    Ligatures are emitted as their own span by some encoders; there the gap is
    ~0 and no space is inserted (``codi`` + ``fi`` + ``cador`` -> ``codificador``).
    """
    parts: list[str] = []
    last_x1: float | None = None
    bold = False
    for span in spans:
        text = span.get("text") or ""
        if not text.strip():
            continue
        if int(span.get("flags") or 0) & 16:
            bold = True
        bbox = span.get("bbox") or (0.0, 0.0, 0.0, 0.0)
        if parts and last_x1 is not None and not parts[-1].endswith(" "):
            size = span.get("size") or 10.0
            if bbox[0] - last_x1 > max(0.22 * size, 0.6):
                parts.append(" ")
        parts.append(text)
        last_x1 = bbox[2]
    return re.sub(r"\s+", " ", "".join(parts)).strip(), bold


# ---------------------------------------------------------------------------
# Running headers / footers
# ---------------------------------------------------------------------------
def _mark_running_headers(pages: list[list[_Line]], page_count: int) -> None:
    counts: Counter[str] = Counter()
    for lines in pages:
        for line in lines:
            if line.edge and _repeat_key(line.text):
                counts[_repeat_key(line.text)] += 1

    threshold = max(2, int(page_count * 0.4))
    repeated = {key for key, count in counts.items() if count >= threshold}

    for lines in pages:
        for line in lines:
            key = _repeat_key(line.text)
            if key and key in repeated:
                line.drop = True
            elif line.edge and _PAGE_NUMBER.match(line.text):
                # Only near the margins: a bare number inside the text is a table
                # cell ("10"), not a page number.
                line.drop = True


def _repeat_key(text: str) -> str:
    # Digits are removed so "Capítulo 3" and "Capítulo 4" collapse to one key.
    key = re.sub(r"\d+", "", text).strip().casefold()
    return key if 2 <= len(key) <= 80 else ""


#: A figure label is a small, isolated snippet ("Atenção", "Soma e norma").
_LABEL_LOOKS_META = re.compile(r"[@:/\\]|^\[\d", re.I)


def _drop_figure_labels(page, lines: list[_Line], body_size: float) -> None:
    """Drop the text drawn *inside* figures (diagram boxes, axis labels).

    Without this, the labels of an architecture diagram come out as dozens of
    one-word paragraphs in the middle of the chapter -- the worst kind of mess
    for a reader. The rule is conservative: the line has to be shorter, smaller
    and narrower than the body text, and must not look like a footnote, a
    reference or a URL.

    Lines inside a *table* are protected: their text is what the table rebuild
    needs later.
    """
    if body_size <= 0:
        return
    widths = [line.x1 - line.x0 for line in lines if not line.drop]
    column = max(widths) if widths else 0.0
    if column < 120:  # a very narrow layout: do not guess
        return
    protected = _table_regions(page, lines)
    for line in lines:
        if line.drop or line.heading:
            continue
        text = line.text.strip()
        if not text or len(text) > 40:
            continue
        if line.size >= body_size * 0.94:
            continue  # same size as the body: real text
        if (line.x1 - line.x0) > column * 0.5:
            continue  # spans the column: a paragraph or a caption
        if _LABEL_LOOKS_META.search(text) or text.endswith(_TERMINAL):
            continue  # footnotes, references, e-mails, URLs
        if re.search(r"\w{4,}", text) is None:
            continue
        middle_x = (line.x0 + line.x1) / 2
        middle_y = (line.y0 + line.y1) / 2
        if any(x0 <= middle_x <= x1 and y0 <= middle_y <= y1 for x0, y0, x1, y1 in protected):
            continue
        line.drop = True


def _table_regions(page, lines: list[_Line]) -> list[tuple[float, float, float, float]]:
    """Areas of a page that a captioned table occupies (its text is kept)."""
    captions = [line for line in lines if _TABLE_CAPTION.match(line.text or "")]
    if not captions:
        return []
    try:
        clusters = list(page.cluster_drawings())
    except Exception:  # noqa: BLE001 - geometry must never break the conversion
        return []
    regions: list[tuple[float, float, float, float]] = []
    for rect in clusters:
        if rect.width < 60 or rect.height < 20:
            continue
        for caption in captions:
            if -70 <= caption.y0 - rect.y1 <= 90 or -90 <= rect.y0 - caption.y1 <= 70:
                regions.append((rect.x0 - 6, rect.y0 - 6, rect.x1 + 6, rect.y1 + 6))
                break
    return regions


def _drop_title_block(pages: list[list[_Line]]) -> None:
    """Remove the title/author block from the body of the first page.

    Those lines become the book's metadata; keeping them as the first chapter
    only produced a jumble of names, institutions and e-mails. Only applied when
    the page really looks like a title page (an e-mail or a "*" affiliation mark
    before the first heading).
    """
    if not pages:
        return
    first = pages[0]
    heading_at = next((index for index, line in enumerate(first) if line.heading), None)
    if heading_at is None or heading_at == 0:
        return
    before = [line for line in first[:heading_at] if not line.drop]
    looks_like_title = any(("@" in line.text) or ("*" in line.text) for line in before)
    if not looks_like_title and len(before) < 4:
        looks_like_title = True
    if not looks_like_title:
        return
    for line in before:
        line.drop = True
        line.band = -1  # marked: not a figure label, do not rebuild it as an image


# ---------------------------------------------------------------------------
# Layout: size metrics, headings, columns
# ---------------------------------------------------------------------------
def _body_metrics(pages: list[list[_Line]]) -> tuple[float, str]:
    size_chars: Counter[float] = Counter()
    font_chars: Counter[str] = Counter()
    for lines in pages:
        for line in lines:
            if line.drop or line.heading:
                continue
            weight = len(line.text)
            size_chars[round(line.size * 2) / 2] += weight
            font_chars[line.font] += weight
    body_size = size_chars.most_common(1)[0][0] if size_chars else 10.0
    body_font = font_chars.most_common(1)[0][0] if font_chars else ""
    return body_size, body_font


def _heading_level(line: _Line, body_size: float, body_font: str) -> int:
    text = line.text.strip()
    if not text or len(text) > 90:
        return 0
    words = text.split()
    if len(words) > 16 or text.endswith((".", ",", ";")):
        return 0

    ratio = line.size / body_size if body_size else 1.0
    match = _NUMBERED_HEADING.match(text)
    if match:
        label = text.split(maxsplit=1)[0].rstrip(".)")
        return min(3, label.count(".") + 1)

    if _is_keyword_title(text):
        return 1 if ratio >= 1.05 else 2
    if ratio >= 1.35 and len(words) <= 12:
        return 1
    if ratio >= 1.12 and len(words) <= 12:
        return 2
    if line.bold and ratio >= 1.04 and len(words) <= 12 and line.font != body_font:
        return 2
    return 0


def _is_keyword_title(text: str) -> bool:
    stripped = text.strip()
    clean = stripped.strip(".:").casefold()
    if clean in _TITLE_KEYWORDS:
        return True
    # A short ALL-CAPS line (without lowercase letters) reads as a heading.
    letters = [c for c in stripped if c.isalpha()]
    if len(letters) < 3 or len(stripped.split()) > 6:
        return False
    return stripped == stripped.upper()


def _order_groups(lines: list[_Line], page_width: float) -> list[list[_Line]]:
    """Split a page into reading-order groups (column segments, wide lines)."""
    if len(lines) < 6:
        return [sorted(lines, key=lambda line: (line.y0, line.x0))] if lines else []

    split_x = _find_gutter(lines, page_width)
    if split_x is None:
        return [sorted(lines, key=lambda line: (line.y0, line.x0))]

    def is_wide(line: _Line) -> bool:
        return line.x0 < split_x - 2 and line.x1 > split_x + 2

    wide = sorted((line for line in lines if is_wide(line)), key=lambda line: line.y0)
    for line in lines:
        line.band = sum(1 for w in wide if w.y1 <= line.y0 + 0.1)
        line.col = 0 if is_wide(line) else (0 if (line.x0 + line.x1) / 2 < split_x else 1)

    groups: list[list[_Line]] = []
    for band in sorted({line.band for line in lines}):
        band_lines = [line for line in lines if line.band == band]
        for line in band_lines:
            if is_wide(line):
                groups.append([line])
        for col in sorted({line.col for line in band_lines if not is_wide(line)}):
            column = sorted(
                (line for line in band_lines if not is_wide(line) and line.col == col),
                key=lambda line: (line.y0, line.x0),
            )
            if column:
                groups.append(column)
    return groups


def _find_gutter(lines: list[_Line], page_width: float) -> float | None:
    left = min(line.x0 for line in lines)
    right = max(line.x1 for line in lines)
    span = right - left
    if span <= 0 or span < (page_width or span) * 0.3:
        return None

    bins = 60
    width = span / bins
    cover = [0] * bins
    for line in lines:
        first = max(0, min(bins - 1, int((line.x0 - left) / width)))
        last = max(0, min(bins - 1, int((line.x1 - left) / width)))
        for k in range(first, last + 1):
            cover[k] += 1

    allowed = max(1, round(len(lines) * 0.05))
    mid_lo, mid_hi = int(bins * 0.2), int(bins * 0.8)
    best_len = best_start = run = run_start = 0
    for k in range(mid_lo, mid_hi):
        if cover[k] <= allowed:
            if run == 0:
                run_start = k
            run += 1
            if run > best_len:
                best_len, best_start = run, run_start
        else:
            run = 0

    if best_len == 0 or best_len * width < max(10.0, 0.04 * span):
        return None
    split_x = left + (best_start + best_len / 2) * width
    left_count = sum(1 for line in lines if (line.x0 + line.x1) / 2 < split_x)
    right_count = len(lines) - left_count
    if left_count < 2 or right_count < 2:
        return None
    return split_x


# ---------------------------------------------------------------------------
# Lines -> blocks
# ---------------------------------------------------------------------------
def _page_blocks(
    pymupdf,
    doc,
    index: int,
    page,
    lines: list[_Line],
    *,
    max_image_long_side: int,
    seen_xrefs: set[int],
) -> tuple[list[_Block], list[tuple[str, bytes, str]]]:
    images: list[tuple[str, bytes, str]] = []
    image_blocks, extracted = _extract_page_images(
        pymupdf, doc, page, index,
        max_image_long_side=max_image_long_side, seen_xrefs=seen_xrefs,
    )
    images.extend(extracted)
    drawn_blocks, drawn = _extract_figure_regions(
        pymupdf, page, index, lines,
        max_image_long_side=max_image_long_side, taken=image_blocks,
    )
    image_blocks.extend(drawn_blocks)
    images.extend(drawn)

    # Only now: a figure takes its text with it (the table cells are in the image).
    live = [line for line in lines if not line.drop]

    blocks: list[_Block] = []
    for group in _order_groups(live, float(page.rect.width) or 595.0):
        text_blocks = _group_to_blocks(group)
        if not text_blocks:
            continue
        left = min(line.x0 for line in group)
        right = max(line.x1 for line in group)
        here = [
            block for block in image_blocks
            if left - 30 <= (block.x0 + block.x1) / 2 <= right + 30
        ]
        # Figures go where they are on the page, not at the end of the chapter.
        blocks.extend(sorted([*text_blocks, *here], key=lambda block: block.y))
        image_blocks = [block for block in image_blocks if block not in here]
    blocks.extend(image_blocks)  # anything not matched to a column stays at the end
    return blocks, images


def _group_to_blocks(group: list[_Line]) -> list[_Block]:
    if not group:
        return []
    col_width = max(line.x1 for line in group) - min(line.x0 for line in group)
    if col_width <= 0:
        col_width = max((line.x1 - line.x0 for line in group), default=1.0)

    gaps = [
        group[i].y0 - group[i - 1].y0
        for i in range(1, len(group))
        if group[i].y0 - group[i - 1].y0 > 0
    ]
    median_gap = _median(gaps)

    blocks: list[_Block] = []
    paragraph: list[_Line] = []

    def flush() -> None:
        if not paragraph:
            return
        text = _reflow(paragraph)
        if text.strip():
            blocks.append(
                _Block(
                    kind="para", text=text, page=paragraph[0].page,
                    xhtml=f"<p>{html.escape(text)}</p>",
                    y=paragraph[0].y0,
                    x0=min(line.x0 for line in paragraph),
                    x1=max(line.x1 for line in paragraph),
                )
            )
        paragraph.clear()

    for line in group:
        if line.heading:
            flush()
            blocks.append(
                _Block(
                    kind="heading", text=line.text, level=line.heading,
                    page=line.page, size=line.size,
                    y=line.y0, x0=line.x0, x1=line.x1,
                )
            )
            continue
        if paragraph and _starts_new_paragraph(paragraph[-1], line, median_gap, col_width):
            flush()
        paragraph.append(line)
    flush()
    return blocks


def _starts_new_paragraph(prev: _Line, current: _Line, median_gap: float, col_width: float) -> bool:
    if median_gap and current.y0 - prev.y0 > median_gap * 1.45:
        return True
    if prev.size and current.size > prev.size * 1.14:
        return True
    prev_width = prev.x1 - prev.x0
    if col_width and prev_width < col_width * 0.82 and not prev.text.rstrip().endswith("-"):
        return True
    return bool(
        col_width
        and prev_width >= col_width * 0.82
        and current.x0 - prev.x0 > 0.8 * max(prev.size, 6.0)
    )


def _reflow(lines: list[_Line]) -> str:
    out = lines[0].text
    for line in lines[1:]:
        nxt = line.text
        if not nxt:
            continue
        if out.endswith("\u00ad") or (out.endswith("-") and nxt[:1].islower()):
            out = out[:-1] + nxt
        else:
            out = f"{out.rstrip()} {nxt.lstrip()}"
    return re.sub(r"\s+", " ", out).strip()


def _merge_across_pages(blocks: list[_Block]) -> list[_Block]:
    merged: list[_Block] = []
    for block in blocks:
        previous = merged[-1] if merged else None
        if (
            block.kind == "para"
            and previous is not None
            and previous.kind == "para"
            and _continues(previous.text, block.text)
        ):
            text = _join_paragraphs(previous.text, block.text)
            merged[-1] = _Block(
                kind="para", text=text, page=previous.page, xhtml=f"<p>{html.escape(text)}</p>"
            )
        else:
            merged.append(block)
    return merged


def _continues(previous: str, current: str) -> bool:
    previous = previous.rstrip()
    if not previous:
        return False
    if previous.endswith("-"):
        return True
    if previous.endswith(_TERMINAL):
        return False
    return current[:1].islower()


def _join_paragraphs(previous: str, current: str) -> str:
    previous = previous.rstrip()
    current = current.lstrip()
    if previous.endswith("-") and current[:1].islower():
        return previous[:-1] + current
    return f"{previous} {current}"


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------
def _extract_page_images(
    pymupdf,
    doc,
    page,
    index: int,
    *,
    max_image_long_side: int,
    seen_xrefs: set[int],
) -> tuple[list[_Block], list[tuple[str, bytes, str]]]:
    try:
        infos = page.get_images(full=True)
    except Exception:
        return [], []

    blocks: list[_Block] = []
    images: list[tuple[str, bytes, str]] = []
    page_area = float(page.rect.width) * float(page.rect.height)

    for order, info in enumerate(infos):
        xref = info[0]
        if xref in seen_xrefs:
            continue
        try:
            rects = page.get_image_rects(xref)
        except Exception:
            continue
        if not rects:
            continue
        rect = rects[0]
        if page_area and (rect.width * rect.height) / page_area >= 0.9:
            continue  # full-page background / scanned page, not a figure
        try:
            data = doc.extract_image(xref)
        except Exception:
            continue
        raw = data.get("image")
        width, height = data.get("width"), data.get("height")
        if not raw or not width or not height or width < 24 or height < 24:
            continue
        seen_xrefs.add(xref)
        name, blob, mime = _prepare_image(
            raw, data.get("ext") or "png", width, height,
            index, order, max_image_long_side,
        )
        images.append((name, blob, mime))
        blocks.append(
            _Block(
                kind="image", page=index,
                xhtml=f'<figure><img src="images/{html.escape(name)}" alt=""/></figure>',
                y=rect.y0, x0=rect.x0, x1=rect.x1,
            )
        )
    return blocks, images


#: "Figura 1:", "Figure 2.", "Tabela 1:" ... anchor a figure region.
_CAPTION = re.compile(
    r"^\s*(figura|figure|tabela|table|quadro|gr[áa]fico)\s*\d", re.IGNORECASE
)
#: A caption that starts a *table* (those are rebuilt as real HTML tables).
_TABLE_CAPTION = re.compile(r"^\s*(tabela|table)\s*\d", re.IGNORECASE)
#: A figure region is never taller than this share of the page.
_MAX_FIGURE_SHARE = 0.45
#: Smallest region worth turning into an image.
_MIN_FIGURE_HEIGHT = 55.0
#: Bounds for a rebuilt table.
_MIN_TABLE_ROWS = 2
_MIN_TABLE_COLS = 2
_MAX_TABLE_COLS = 14


def _table_grid(lines: list[_Line]) -> list[list[str]] | None:
    """Rebuild a table from its text lines, or ``None`` when it is not one.

    The unit is the *cell*, not the line: a header whose text wraps
    ("Comprimento máximo do caminho") is drawn as several lines and must stay one
    cell, while two different rows drawn at almost the same height must stay
    apart. So: columns from the x positions, cells by grouping a column's lines
    vertically, and rows by grouping the cells whose centres line up.
    """
    usable = [line for line in lines if line.text.strip()]
    if len(usable) < _MIN_TABLE_ROWS * _MIN_TABLE_COLS:
        return None

    anchors: list[float] = []
    for line in sorted(usable, key=lambda item: item.x0):
        if not anchors or all(abs(line.x0 - anchor) > 12 for anchor in anchors):
            anchors.append(line.x0)
    if not _MIN_TABLE_COLS <= len(anchors) <= _MAX_TABLE_COLS:
        return None

    heights = [line.y1 - line.y0 for line in usable if line.y1 > line.y0]
    line_height = _median(heights) or 8.0

    cells: list[list] = []  # [coluna, centro em y, texto]
    for line in usable:
        column = min(range(len(anchors)), key=lambda index: abs(anchors[index] - line.x0))
        cells.append([column, (line.y0 + line.y1) / 2, line.text.strip()])
    cells.sort(key=lambda cell: (cell[1], cell[0]))

    merged: list[list] = []
    for cell in cells:
        if (
            merged
            and merged[-1][0] == cell[0]
            and abs(merged[-1][1] - cell[1]) <= 0.8 * line_height
        ):
            merged[-1][2] = f"{merged[-1][2]} {cell[2]}".strip()
            merged[-1][1] = (merged[-1][1] + cell[1]) / 2
        else:
            merged.append(list(cell))

    rows: list[dict] = []
    for cell in sorted(merged, key=lambda item: item[1]):
        placed = False
        for row in rows:
            if abs(row["y"] - cell[1]) <= 0.9 * line_height:
                row["cells"].append(cell)
                row["y"] = (row["y"] + cell[1]) / 2
                placed = True
                break
        if not placed:
            rows.append({"y": cell[1], "cells": [cell]})

    if len(rows) < _MIN_TABLE_ROWS:
        return None

    grid: list[list[str]] = []
    for row in rows:
        row_cells = ["" for _ in anchors]
        for column, _centre, text in sorted(row["cells"], key=lambda item: item[0]):
            row_cells[column] = (
                f"{row_cells[column]} {text}".strip() if row_cells[column] else text
            )
        if any(row_cells):
            grid.append(row_cells)

    if len(grid) < _MIN_TABLE_ROWS:
        return None
    widest = max(sum(1 for cell in row if cell) for row in grid)
    if widest < _MIN_TABLE_COLS:
        return None
    return grid


def _table_xhtml(grid: list[list[str]], header: bool) -> str:
    parts = ["<table>"]
    for index, row in enumerate(grid):
        cells = "".join(
            f"<{'th' if header and index == 0 else 'td'}>{html.escape(cell)}</{'th' if header and index == 0 else 'td'}>"
            for cell in row
        )
        parts.append(f"<tr>{cells}</tr>")
    parts.append("</table>")
    return "".join(parts)


def _extract_figure_regions(
    pymupdf,
    page,
    index: int,
    lines: list[_Line],
    *,
    max_image_long_side: int,
    taken: list[_Block],
) -> tuple[list[_Block], list[tuple[str, bytes, str]]]:
    """Turn the *drawn* content of a page into figures.

    Scanned or print-to-PDF pages are one full-page image with a text layer, so
    the diagrams have no separate image to extract. Their place is still
    knowable: the labels drawn inside a figure (which ``_drop_figure_labels``
    already identified) and the vector clusters the page declares. Each cluster
    becomes one cropped image, a nearby caption ("Figura 1", "Tabela 1") widens
    it, and the text it covers is removed so the figure is not duplicated as a
    paragraph -- which is what turns a table full of cells into a readable
    picture.
    """
    page_rect = page.rect
    width, height = float(page_rect.width), float(page_rect.height)
    page_area = width * height

    try:
        clusters = list(page.cluster_drawings())
    except Exception:  # noqa: BLE001 - geometry must never break the conversion
        clusters = []
    candidates = [
        rect for rect in clusters
        if rect.width >= 60 and rect.height >= 28
        and rect.width * rect.height >= 0.012 * page_area
        and rect.width * rect.height <= 0.6 * page_area
    ]
    captions = [line for line in lines if _CAPTION.match(line.text or "")]
    candidates.extend(
        _extend_to_caption(box, captions) for box in _cluster_label_boxes(lines)
    )

    blocks: list[_Block] = []
    images: list[tuple[str, bytes, str]] = []
    rendered: list[tuple[float, float, float, float]] = []

    for order, rect in enumerate(candidates):
        box = pymupdf.Rect(rect)
        caption = next(
            (
                line for line in captions
                if -70 <= line.y0 - box.y1 <= 70 or -70 <= box.y0 - line.y1 <= 70
            ),
            None,
        )
        if caption is not None:
            # The caption is part of the figure block: include it horizontally so
            # a narrow cluster does not cut the figure in half.
            box.x0 = min(box.x0, caption.x0 - 4)
            box.x1 = max(box.x1, caption.x1 + 4)
        box.x0 = max(0.0, box.x0 - 4)
        box.x1 = min(width, box.x1 + 4)
        box.y0 = max(0.0, box.y0 - 4)
        box.y1 = min(height, box.y1 + 4)
        if box.height < 50 or box.width < 60:
            continue
        if box.height * box.width > 0.6 * page_area:
            continue
        if any(_overlaps(box, other) for other in rendered):
            continue
        if any(block.kind == "image" and box.y0 <= block.y <= box.y1 for block in taken):
            continue  # an embedded image already covers this figure

        # A table caption asks for a real table: the cells become text again
        # (searchable, reflowable) instead of one picture of the grid.
        if caption is not None and _TABLE_CAPTION.match(caption.text or ""):
            inside = [
                line for line in lines
                if not line.drop and not line.edge
                and box.x0 <= (line.x0 + line.x1) / 2 <= box.x1
                and box.y0 <= (line.y0 + line.y1) / 2 <= box.y1
            ]
            grid = _table_grid(inside)
            if grid is not None:
                first_row_y = min(line.y0 for line in inside)
                header = any(
                    line.bold for line in inside if abs(line.y0 - first_row_y) <= 4
                )
                for line in inside:
                    line.drop = True  # its text now lives in the table
                rendered.append((box.x0, box.y0, box.x1, box.y1))
                blocks.append(
                    _Block(
                        kind="table", page=index, xhtml=_table_xhtml(grid, header),
                        y=box.y0, x0=box.x0, x1=box.x1,
                    )
                )
                continue

        try:
            pixmap = page.get_pixmap(clip=box, dpi=_FIGURE_DPI)
            raw = pixmap.tobytes("png")
        except Exception:  # noqa: BLE001 - a failed figure must not break the page
            continue
        if not raw or _looks_blank(raw):
            continue
        rendered.append((box.x0, box.y0, box.x1, box.y1))
        name = f"figura_{index + 1:04d}_{order:02d}.png"
        raw, mime = _shrink_figure(raw, max_image_long_side)
        if mime == "image/jpeg":
            name = name.rsplit(".", 1)[0] + ".jpg"
        images.append((name, raw, mime))
        blocks.append(
            _Block(
                kind="image", page=index,
                xhtml=f'<figure><img src="images/{html.escape(name)}" alt=""/></figure>',
                y=box.y0, x0=box.x0, x1=box.x1,
            )
        )

    if rendered:
        # The figure owns its text: keeping it would print the table twice.
        for line in lines:
            if line.drop:
                continue
            middle_x = (line.x0 + line.x1) / 2
            if any(
                box[0] <= middle_x <= box[2] and box[1] <= (line.y0 + line.y1) / 2 <= box[3]
                for box in rendered
            ):
                line.drop = True
    return blocks, images


def _cluster_label_boxes(lines: list[_Line]) -> list[tuple[float, float, float, float]]:
    """Union the dropped labels of a figure into one box per figure."""
    labels = [
        line for line in lines
        if line.drop and line.band != -1 and not line.edge and line.text.strip()
    ]
    if not labels:
        return []
    labels.sort(key=lambda line: line.y0)
    boxes: list[list[float]] = []
    for line in labels:
        placed = False
        for box in boxes:
            # Same figure area: close vertically and horizontally related. Labels
            # of one diagram can sit far apart (a caption decides the real box).
            if line.y0 - box[3] <= 110 and line.x0 <= box[2] + 60 and line.x1 >= box[0] - 60:
                box[0] = min(box[0], line.x0)
                box[1] = min(box[1], line.y0)
                box[2] = max(box[2], line.x1)
                box[3] = max(box[3], line.y1)
                placed = True
                break
        if not placed:
            boxes.append([line.x0, line.y0, line.x1, line.y1])
    return [tuple(box) for box in boxes]


def _extend_to_caption(
    box: tuple[float, float, float, float], captions: list[_Line]
) -> tuple[float, float, float, float]:
    """Grow a label cluster down to its caption: that is the figure's real box."""
    x0, y0, x1, y1 = box
    for caption in captions:
        if 0 <= caption.y0 - y1 <= 110:  # caption just below the drawing
            return (x0, y0, x1, max(y1, caption.y0 - 4))
        if 0 <= y0 - caption.y1 <= 40:  # caption above (tables)
            return (x0, min(y0, caption.y0 - 6), x1, y1)
    return box


def _overlaps(a, b) -> bool:
    return not (a.x1 <= b[0] or a.x0 >= b[2] or a.y1 <= b[1] or a.y0 >= b[3])


def _looks_blank(png: bytes) -> bool:
    """A crop with almost no variation is blank space, not a figure."""
    try:
        from io import BytesIO

        from PIL import Image

        with Image.open(BytesIO(png)) as image:
            small = image.convert("L").resize((24, 24))
            low, high = small.getextrema()
        return (high - low) < 12
    except Exception:  # noqa: BLE001 - when in doubt, keep the figure
        return False


#: Render resolution for figure regions (readable on e-ink without bloat).
_FIGURE_DPI = 150


def _shrink_figure(raw: bytes, max_side: int) -> tuple[bytes, str]:
    """Keep a rendered figure inside a sane size (JPEG when it is a photo)."""
    if not max_side:
        return raw, "image/png"
    try:
        from app.converters.imageops import cap_long_side, encode_image, open_image

        image = open_image(raw)
        if max(image.size) > max_side:
            image = cap_long_side(image, max_side)
        grayscale = image.mode in {"L", "1"}
        target = "png" if grayscale else "jpg"
        data = encode_image(image, target, quality=88, grayscale=grayscale)
        return data, "image/png" if target == "png" else "image/jpeg"
    except Exception:  # noqa: BLE001 - keep the PNG when Pillow cannot help
        return raw, "image/png"


def _prepare_image(
    raw: bytes, ext: str, width: int, height: int, page: int, order: int, max_side: int
) -> tuple[str, bytes, str]:
    ext = {"jpeg": "jpg", "jpe": "jpg"}.get(ext.lower(), ext.lower())
    if ext not in {"jpg", "png", "webp"}:
        ext = "png"

    if max_side and max(width, height) > max_side:
        try:
            from app.converters.imageops import cap_long_side, encode_image, open_image

            img = cap_long_side(open_image(raw), max_side)
            target = "png" if img.mode in {"L", "1"} else "jpg"
            raw = encode_image(img, target, quality=85, grayscale=img.mode in {"L", "1"})
            ext = target
        except Exception:
            pass  # keep the original bytes if Pillow cannot handle them

    name = f"figure_{page + 1:04d}_{order:02d}.{ext}"
    mime = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}.get(ext, "image/png")
    return name, raw, mime


# ---------------------------------------------------------------------------
# Blocks -> chapters
# ---------------------------------------------------------------------------
def _strip_repeated_title(text: str, title: str) -> str:
    """Drop a leading repetition of the chapter title inside its first paragraph."""
    head = (title or "").strip()
    body = (text or "").strip()
    if head and body.lower().startswith(head.lower()):
        return body[len(head):].strip(" .:-—–")
    return body


def _build_chapters(blocks: list[_Block], fallback_title: str) -> list[tuple[str, str]]:
    levels = [block.level for block in blocks if block.kind == "heading"]
    if not levels:
        body = "\n".join(
            block.xhtml for block in blocks if block.kind != "para" or block.text.strip()
        )
        return [(fallback_title, body or "<p></p>")]

    top = min(levels)
    preface: list[str] = []

    # A title-page line is markedly larger than every other heading: keep it as
    # the front-matter title instead of a nearly empty chapter that the later
    # consolidation would silently absorb.
    if (
        fallback_title
        and blocks
        and blocks[0].kind == "heading"
        and blocks[0].level == top
        and blocks[0].text.strip()
    ):
        others = [b.size for b in blocks if b.kind == "heading" and b is not blocks[0]]
        if not others or blocks[0].size >= 1.15 * max(others):
            preface.append(f'<p class="doc-title">{html.escape(blocks[0].text)}</p>')
            blocks = blocks[1:]

    chapters: list[tuple[str, str]] = []
    parts: list[str] = []
    title: str | None = None

    for block in blocks:
        if block.kind == "heading" and block.level == top:
            if title is not None:
                chapters.append((title, "\n".join(parts)))
            else:
                preface.extend(parts)
            title = block.text
            parts = []
        elif block.kind == "heading":
            level = min(4, block.level - top + 1)
            parts.append(f"<h{level}>{html.escape(block.text)}</h{level}>")
        elif block.kind in {"image", "table"} or block.text.strip():
            if block.kind == "para" and title and not parts:
                # PDFs often repeat the title as the first line of the chapter.
                trimmed = _strip_repeated_title(block.text, title)
                if not trimmed:
                    continue
                if trimmed != block.text:
                    block = _Block(
                        kind="para", text=trimmed, page=block.page,
                        xhtml=f"<p>{html.escape(trimmed)}</p>",
                        y=block.y, x0=block.x0, x1=block.x1,
                    )
            parts.append(block.xhtml)

    if title is not None:
        chapters.append((title, "\n".join(parts)))
    elif preface or parts:
        return [(fallback_title, "\n".join(preface + parts))]

    if not chapters:
        return [(fallback_title, "<p></p>")]

    # Merge only genuinely tiny sections; never collapse the whole book into
    # one chapter just because it is short (that is for heading-less PDFs).
    chapters = consolidate_chapters(
        chapters, min_chars=400, target_chars=6000, single_below=0
    )
    if preface and any(part.strip() for part in preface):
        chapters.insert(0, (fallback_title, "\n".join(preface)))
    return chapters


# ---------------------------------------------------------------------------
# Metadata helpers
# ---------------------------------------------------------------------------
def _clean_meta(value) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _title_author_from_text(pages: list[list[_Line]]) -> dict[str, str]:
    """Fallback: the largest top-of-page line is the title, the second the author."""
    candidates = [
        line for lines in pages[:2] for line in lines
        if not line.drop and line.text.strip() and len(line.text) <= 200
    ]
    candidates.sort(key=lambda line: line.size, reverse=True)
    title = candidates[0].text if candidates else ""
    author = ""
    if len(candidates) > 1 and candidates[1].size >= candidates[0].size * 0.7:
        author = candidates[1].text
    return {"title": title, "author": author}


def _guess_language(meta: dict, pages: list[list[_Line]]) -> str:
    declared = _clean_meta(meta.get("language")).lower()
    if declared:
        base = declared.replace("_", "-").split("-")[0]
        if 2 <= len(base) <= 3:
            return base
    sample = " ".join(
        line.text.casefold()
        for lines in pages[:6]
        for line in lines
        if not line.heading and not line.drop
    )[:20000]
    tokens = re.findall(r"[a-zà-öø-ÿ]+", sample)
    if not tokens:
        return ""
    pt = sum(1 for token in tokens if token in _PT_WORDS)
    en = sum(1 for token in tokens if token in _EN_WORDS)
    if pt == en == 0:
        return ""
    return "pt" if pt >= en else "en"


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _import_pymupdf():
    try:
        import pymupdf
    except ImportError:
        try:
            import fitz as pymupdf
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise RuntimeError(
                "PyMuPDF é necessário para extrair PDF com texto (pip install PyMuPDF)."
            ) from exc
    return pymupdf
