"""Split the ink of a region into lines and words -- geometry only, no OCR.

The pixels are already there: a line of dialogue is a run of ink rows, and a
word is a run of ink columns inside that line. Grouping by projection gives
word bounding boxes that can be moved and scaled individually, which is what
makes the re-layout possible.

Reading direction matters: Japanese manga is right-to-left, so the words of a
line are ordered accordingly (``right_to_left=True``).
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image

from app.converters.manga.detector import interior_ink_mask


def _pixels(image: Image.Image):
    getter = getattr(image, "get_flattened_data", None)
    if getter is not None:
        return getter()
    return image.getdata()  # pragma: no cover - old Pillow


@dataclass(slots=True)
class Word:
    """One word, in full-page coordinates."""

    box: tuple[int, int, int, int]


@dataclass(slots=True)
class TextBlock:
    """Words of one region plus the metrics needed to lay them out again."""

    words: list[Word]
    lines: list[list[int]]  # indices into ``words``, in reading order
    line_height: int
    line_gap: int
    word_gap: int
    order: str  # "ltr" or "rtl"

    @property
    def box(self) -> tuple[int, int, int, int]:
        x0 = min(word.box[0] for word in self.words)
        y0 = min(word.box[1] for word in self.words)
        x1 = max(word.box[2] for word in self.words)
        y1 = max(word.box[3] for word in self.words)
        return (x0, y0, x1, y1)


def _runs(flags: list[bool], max_gap: int) -> list[tuple[int, int]]:
    """Group consecutive ``True`` runs, tolerating gaps up to ``max_gap``."""
    groups: list[tuple[int, int]] = []
    start = None
    gap = 0
    for index, flag in enumerate(flags):
        if flag:
            if start is None:
                start = index
            gap = 0
        elif start is not None:
            gap += 1
            if gap > max_gap:
                groups.append((start, index - gap))
                start = None
    if start is not None:
        groups.append((start, len(flags) - 1 - gap if gap else len(flags) - 1))
    return groups


def _median(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def segment_words(
    gray: Image.Image,
    box: tuple[int, int, int, int],
    *,
    ink_threshold: int = 128,
    right_to_left: bool = False,
) -> TextBlock | None:
    """Find the words inside ``box``; ``None`` when there is nothing usable."""
    x0, y0, x1, y1 = box
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None
    crop = gray.crop(box)
    # At reader resolution the gap between two text lines can be a single pixel,
    # which would merge every line into one band. Analysing an upscaled copy
    # (nearest keeps the strokes crisp) makes those gaps usable; coordinates are
    # mapped back at the end.
    zoom = 2 if max(crop.size) < 512 else 1
    if zoom > 1:
        crop = crop.resize((crop.width * zoom, crop.height * zoom), Image.NEAREST)
    width, height = crop.size
    raw = bytearray(1 if value < ink_threshold else 0 for value in _pixels(crop))
    # Drop the balloon outline/art that crosses the crop: only the glyphs remain.
    data = interior_ink_mask(raw, width, height, max_area=int(0.25 * width * height))

    row_flags = [any(data[y * width:(y + 1) * width]) for y in range(height)]
    row_bands = _runs(row_flags, max_gap=zoom)
    if not row_bands:
        return None

    # Merge bands separated by less than a pixel (antialiasing noise); a real
    # line gap is wider than that and must stay a separate line.
    join_tolerance = max(0, zoom - 1)
    merged: list[list[int]] = [list(row_bands[0])]
    for start, end in row_bands[1:]:
        if start - merged[-1][1] - 1 <= join_tolerance:
            merged[-1][1] = end
        else:
            merged.append([start, end])

    words: list[Word] = []
    lines: list[list[int]] = []
    word_spaces: list[int] = []
    for start, end in merged:
        band_height = end - start + 1
        gap = max(2, round(0.35 * band_height))
        column_flags = [
            any(data[y * width + x] for y in range(start, end + 1)) for x in range(width)
        ]
        column_bands = _runs(column_flags, max_gap=gap)
        line: list[int] = []
        for cx0, cx1 in column_bands:
            if cx1 - cx0 + 1 < zoom:
                continue
            line.append(len(words))
            words.append(
                Word(
                    (
                        round(x0 + cx0 / zoom),
                        round(y0 + start / zoom),
                        round(x0 + (cx1 + 1) / zoom),
                        round(y0 + (end + 1) / zoom),
                    )
                )
            )
        if len(line) < 1:
            continue
        # Horizontal gaps between words on this line (used as word spacing).
        for left, right in zip(line, line[1:], strict=False):
            word_spaces.append(words[right].box[0] - words[left].box[2])
        lines.append(line)

    if not words:
        return None

    # Vertical rhythm of the original text.
    line_heights = [max(words[i].box[3] for i in line) - min(words[i].box[1] for i in line)
                    for line in lines]
    line_height = max(1, _median(line_heights))
    ordered_lines = sorted(lines, key=lambda line: min(words[i].box[1] for i in line))
    between: list[int] = []
    for previous, current in zip(ordered_lines, ordered_lines[1:], strict=False):
        previous_bottom = max(words[i].box[3] for i in previous)
        current_top = min(words[i].box[1] for i in current)
        between.append(max(0, current_top - previous_bottom))
    line_gap = max(1, round(0.2 * line_height))
    if between:
        line_gap = max(line_gap, _median(between))
    word_gap = max(2, _median([gap for gap in word_spaces if gap > 0]))

    order = "rtl" if right_to_left else "ltr"
    reading_lines = []
    for line in ordered_lines:
        ordered = sorted(line, key=lambda index: words[index].box[0], reverse=right_to_left)
        reading_lines.append(ordered)

    return TextBlock(
        words=words,
        lines=reading_lines,
        line_height=line_height,
        line_gap=line_gap,
        word_gap=word_gap,
        order=order,
    )
