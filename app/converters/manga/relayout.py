"""OCR-guided re-layout: conservative (same glyphs) or rewritten (font).

The OCR gives what the geometry could not: the actual words, their boxes and
their confidence. Two ways to use it:

* **conservative** -- keep the original raster and only move whole words around,
  re-flowing them to a larger size. No transcription risk, no font change.
* **rewrite** -- clear the balloon and draw the recognised text with the bundled
  font, sized to fill the free space. Sharper, but relies on the OCR being
  right, so a confidence floor is applied per region.

Both reuse the same free-space box (``layout.usable_box``) that keeps the text
inside round balloons.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PIL import Image, ImageDraw

from app.converters.manga import fonts
from app.converters.manga.layout import INNER_PAD_RATIO
from app.converters.manga.ocr import OcrLine
from app.converters.manga.segmentation import TextBlock, Word

#: Line spacing for the rewritten text (fraction of the font size).
LINE_SPACING = 1.18
#: Extra space between words when measuring/wrapping (fraction of font size).
WORD_SPACING = 0.32


def _median(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def order_lines(lines: list[OcrLine]) -> list[OcrLine]:
    """Reading order for translated manga: rows top-to-bottom, left-to-right."""
    valid = [line for line in lines if line.box[2] > line.box[0] and line.box[3] > line.box[1]]
    rows: list[list[OcrLine]] = []
    for line in sorted(valid, key=lambda item: (item.box[1], item.box[0])):
        placed = False
        for row in rows:
            top = max(row[0].box[1], line.box[1])
            bottom = min(row[0].box[3], line.box[3])
            height = min(row[0].box[3] - row[0].box[1], line.box[3] - line.box[1])
            if height > 0 and (bottom - top) / height >= 0.5:
                row.append(line)
                placed = True
                break
        if not placed:
            rows.append([line])
    return [line for row in rows for line in sorted(row, key=lambda item: item.box[0])]


def block_from_ocr(lines: list[OcrLine], *, right_to_left: bool = False) -> TextBlock | None:
    """Adapt OCR lines to the same ``TextBlock`` the re-flow engine uses.

    Reading order is decided here: the supported OCR languages are all written
    left-to-right, so lines are grouped into rows (by vertical overlap) and read
    top-to-bottom, left-to-right -- exactly how a translated manga is lettered.
    ``right_to_left`` is accepted for API symmetry but changes nothing: the page
    may be read right-to-left, the words inside a balloon are not.
    """
    ordered = order_lines(lines)
    if not ordered:
        return None

    words: list[Word] = []
    index_lines: list[list[int]] = []
    for line in ordered:
        line_words = [Word(word.box) for word in line.words if word.box[2] > word.box[0]]
        if not line_words:
            line_words = [Word(line.box)]
        line_words.sort(key=lambda word: word.box[0])
        indices: list[int] = []
        for word in line_words:
            indices.append(len(words))
            words.append(word)
        index_lines.append(indices)

    if not words:
        return None

    line_heights = [
        max(words[i].box[3] for i in line) - min(words[i].box[1] for i in line)
        for line in index_lines
    ]
    line_height = max(1, _median(line_heights))
    between: list[int] = []
    for previous, current in zip(index_lines, index_lines[1:], strict=False):
        bottom = max(words[i].box[3] for i in previous)
        top = min(words[i].box[1] for i in current)
        between.append(max(0, top - bottom))
    line_gap = max(1, round(0.2 * line_height), _median(between))

    gaps: list[int] = []
    for line in index_lines:
        for left, right in zip(line, line[1:], strict=False):
            gaps.append(abs(words[right].box[0] - words[left].box[2]))
    word_gap = max(2, _median([gap for gap in gaps if gap > 0]))

    return TextBlock(
        words=words,
        lines=index_lines,
        line_height=line_height,
        line_gap=line_gap,
        word_gap=word_gap,
        order="rtl" if right_to_left else "ltr",
    )


def text_of(lines: list[OcrLine]) -> str:
    return " ".join(line.text.strip() for line in lines if line.text.strip())


@dataclass(slots=True)
class RewriteLine:
    text: str
    x: int
    y: int
    width: int
    height: int


@dataclass(slots=True)
class RewritePlan:
    size: int
    region_box: tuple[int, int, int, int]
    source_box: tuple[int, int, int, int]
    lines: list[RewriteLine] = field(default_factory=list)


def _text_color(img: Image.Image, region_box: tuple[int, int, int, int]):
    """Dominant dark colour of the region: the ink the text was drawn with."""
    x0, y0, x1, y1 = region_box
    crop = img.crop((x0, y0, x1, y1))
    gray = crop if crop.mode == "L" else crop.convert("L")
    dark = gray.point(lambda value: value if value < 128 else 255)
    colors = dark.getcolors(maxcolors=gray.width * gray.height)
    if not colors:
        return 0 if img.mode in {"L", "1"} else (0, 0, 0)
    # Ignore the paper: pick the darkest colour that actually appears.
    colors.sort(key=lambda item: item[1])
    darkest = colors[0][1]
    if img.mode == "L" and isinstance(darkest, tuple):
        return darkest[0]
    if img.mode == "RGB" and not isinstance(darkest, tuple):
        return (darkest, darkest, darkest)
    return darkest


def plan_rewrite(
    lines: list[OcrLine],
    region_box: tuple[int, int, int, int],
    *,
    fit_box: tuple[int, int, int, int] | None = None,
    font_file: str | None = None,
    max_size: int = 120,
) -> RewritePlan | None:
    """Largest font size for which the recognised text fits, or ``None``."""
    text = text_of(lines)
    if not text:
        return None
    box = fit_box or region_box
    width = max(1, box[2] - box[0])
    height = max(1, box[3] - box[1])
    pad = max(2, int(min(width, height) * INNER_PAD_RATIO))
    avail_w = max(8, width - 2 * pad)
    avail_h = max(8, height - 2 * pad)

    best: tuple[int, list[str]] | None = None
    low, high = 7, max_size
    while low <= high:
        size = (low + high) // 2
        font, _unicode = fonts.load_font(size, font_file)
        space = size * WORD_SPACING
        wrapped = _wrap_with_space(text, font, avail_w, space)
        if not wrapped:
            return None
        line_height = round(size * LINE_SPACING)
        total = line_height * len(wrapped)
        widest = max(font.getlength(line) + space * (len(line.split()) - 1) for line in wrapped)
        if total <= avail_h and widest <= avail_w:
            best = (size, wrapped)
            low = size + 1
        else:
            high = size - 1
    if best is None:
        return None

    size, wrapped = best
    font, _unicode = fonts.load_font(size, font_file)
    line_height = round(size * LINE_SPACING)
    total_h = line_height * len(wrapped)
    top = box[1] + (height - total_h) // 2

    result: list[RewriteLine] = []
    for index, line in enumerate(wrapped):
        line_width = round(font.getlength(line))
        x = box[0] + (width - line_width) // 2
        y = top + index * line_height
        result.append(RewriteLine(line, x, y, line_width, line_height))

    source_x0 = min(line.box[0] for line in lines)
    source_y0 = min(line.box[1] for line in lines)
    source_x1 = max(line.box[2] for line in lines)
    source_y1 = max(line.box[3] for line in lines)
    source_box = (source_x0, source_y0, source_x1, source_y1)
    return RewritePlan(size, region_box, source_box, result)


def _wrap_with_space(text: str, font, max_width: int, space: float) -> list[str]:
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        width = font.getlength(candidate) + space * (len(candidate.split()) - 1)
        if width <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def render_rewrite(img: Image.Image, plan: RewritePlan, *, font_file: str | None = None) -> bool:
    """Clear the recognised text and draw it again with the bundled font."""
    from app.converters.manga.enlarge import _background_color

    background = _background_color(img, plan.region_box)
    color = _text_color(img, plan.region_box)
    draw = ImageDraw.Draw(img)
    x0, y0, x1, y1 = plan.source_box
    draw.rectangle(
        (max(0, x0 - 1), max(0, y0 - 1), min(img.width, x1 + 1), min(img.height, y1 + 1)),
        fill=background,
    )
    for line in plan.lines:
        font, _unicode = fonts.load_font(plan.size, font_file)
        draw.text((line.x, line.y), line.text, font=font, fill=color)
    return True


def region_text_box(lines: list[OcrLine], fallback: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """Union of the OCR line boxes (or the geometry box when there is none)."""
    if not lines:
        return fallback
    return (
        min(line.box[0] for line in lines),
        min(line.box[1] for line in lines),
        max(line.box[2] for line in lines),
        max(line.box[3] for line in lines),
    )


__all__ = [
    "RewritePlan",
    "block_from_ocr",
    "plan_rewrite",
    "region_text_box",
    "render_rewrite",
    "text_of",
]
