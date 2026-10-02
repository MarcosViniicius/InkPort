"""Re-flow words inside a region to make the text as large as possible.

Uniform scaling is limited by the original line breaks: a wide, short line
cannot grow even when the balloon is tall. Moving whole *words* around solves
that -- breaking a line in two, or joining two short ones, lets the glyphs grow
until they fill the available box. No OCR: the words are the original raster.

Strategy: binary search the largest scale for which the greedy wrap of the
words (in reading order) still fits the region's inner box. Lines are centred
on the original block and the leftover height becomes extra line spacing.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.converters.manga.layout import INNER_PAD_RATIO, MIN_USEFUL_SCALE
from app.converters.manga.segmentation import TextBlock, Word


@dataclass(slots=True)
class ReflowWord:
    source: tuple[int, int, int, int]
    target: tuple[int, int, int, int]


@dataclass(slots=True)
class ReflowPlan:
    scale: float
    strategy: str
    region_box: tuple[int, int, int, int]
    source_box: tuple[int, int, int, int]
    lines: list[list[ReflowWord]]


def _word_width(word: Word) -> int:
    return word.box[2] - word.box[0]


def _word_height(word: Word) -> int:
    return word.box[3] - word.box[1]


def _wrap(words: list[Word], capacity: float, space: int) -> list[list[Word]]:
    """Greedy line breaking in original pixels (``capacity`` = box / scale)."""
    lines: list[list[Word]] = []
    current: list[Word] = []
    width = 0.0
    for word in words:
        word_width = _word_width(word)
        extra = word_width + (space if current else 0)
        if current and width + extra > capacity:
            lines.append(current)
            current = [word]
            width = word_width
        else:
            current.append(word)
            width += extra
    if current:
        lines.append(current)
    return lines


def _reading_order(block: TextBlock) -> list[Word]:
    ordered: list[Word] = []
    for line in block.lines:
        ordered.extend(block.words[index] for index in line)
    return ordered


def plan_reflow(
    block: TextBlock,
    region_box: tuple[int, int, int, int],
    *,
    max_scale: float = 2.0,
    min_scale: float = MIN_USEFUL_SCALE,
    fit_box: tuple[int, int, int, int] | None = None,
) -> ReflowPlan | None:
    """Largest re-flowed scale that fits the region, or ``None``."""
    words = _reading_order(block)
    if len(words) < 2:
        return None

    rx0, ry0, rx1, ry1 = fit_box or region_box
    region_w, region_h = max(1, rx1 - rx0), max(1, ry1 - ry0)
    pad = max(2, int(min(region_w, region_h) * INNER_PAD_RATIO))
    avail_w = max(1, region_w - 2 * pad)
    avail_h = max(1, region_h - 2 * pad)

    # Re-flowing keeps the lines compact; the original spacing was often airy.
    search_gap = max(1, round(0.2 * block.line_height))

    widest = max(_word_width(word) for word in words)
    cap = min(max_scale, avail_w / widest)
    if cap < min_scale:
        return None

    space = block.word_gap
    low, high = min_scale, cap
    best = None
    for _ in range(20):
        middle = (low + high) / 2
        lines = _wrap(words, avail_w / middle, space)
        total = (
            len(lines) * block.line_height * middle
            + max(0, len(lines) - 1) * search_gap * middle
        )
        if total <= avail_h + 0.5:
            best = middle
            low = middle
        else:
            high = middle
    if best is None or best < min_scale:
        return None

    lines = _wrap(words, avail_w / best, space)
    line_height = block.line_height * best
    gap = search_gap * best
    leftover = avail_h - (len(lines) * line_height + max(0, len(lines) - 1) * gap)
    if len(lines) > 1 and leftover > 0:
        gap += min(leftover / (len(lines) - 1), 0.5 * line_height)
    total_height = len(lines) * line_height + max(0, len(lines) - 1) * gap

    block_box = block.box
    center_x = (block_box[0] + block_box[2]) / 2
    center_y = (block_box[1] + block_box[3]) / 2
    avail_x0, avail_x1 = rx0 + pad, rx1 - pad
    top = center_y - total_height / 2
    top = min(max(top, ry0 + pad), ry1 - pad - total_height)

    result_lines: list[list[ReflowWord]] = []
    for line in lines:
        widths = [(_word_width(word) + space) * best for word in line]
        line_width = sum(widths) - (space * best if line else 0)
        left = center_x - line_width / 2
        left = min(max(left, avail_x0), avail_x1 - line_width)
        cursor = left + line_width if block.order == "rtl" else left
        line_height_scaled = max(_word_height(word) for word in line) * best
        bottom = top + line_height_scaled
        items: list[ReflowWord] = []
        for word, width in zip(line, widths, strict=False):
            word_width = width - space * best
            if block.order == "rtl":
                x0 = cursor - word_width
                cursor -= width
            else:
                x0 = cursor
                cursor += width
            items.append(
                ReflowWord(
                    source=word.box,
                    target=(round(x0), round(bottom - _word_height(word) * best),
                            round(x0 + word_width), round(bottom)),
                )
            )
        result_lines.append(items)
        top = bottom + gap

    source_box = block_box
    return ReflowPlan(best, "reflow", region_box, source_box, result_lines)
