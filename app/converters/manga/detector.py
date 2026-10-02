"""Detect text-bearing regions (speech bubbles and loose text boxes).

Classic, dependency-free computer vision on top of Pillow:

1. binarise a downscaled copy (white mask / ink mask);
2. flood fill the white mask from the image border -- everything that stays
   unvisited is an *enclosed* white area, i.e. the inside of a balloon or a
   text box;
3. label the enclosed components and keep the plausible ones;
4. inside each region, label the *ink* components at full resolution: the
   strokes of the text are small blobs that do not touch the region border
   (the balloon outline does), so their union is the text bounding box.

The result is geometry only -- we never try to read the characters.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from PIL import Image

from app.converters.imageops import to_grayscale

#: Component labeller works on flat bytearrays; a deque keeps BFS iterative.
Queue = deque


@dataclass(slots=True)
class Region:
    """A detected white area that contains text.

    All coordinates are in full-resolution pixels, ``(x0, y0, x1, y1)`` with the
    right/bottom edges exclusive.
    """

    box: tuple[int, int, int, int]
    text_box: tuple[int, int, int, int]
    kind: str  # "bubble" (round) or "box" (rectangular loose text)
    area: int

    @property
    def text_size(self) -> tuple[int, int]:
        return self.text_box[2] - self.text_box[0], self.text_box[3] - self.text_box[1]


def _pixels(image: Image.Image):
    """Flat pixel values, without the deprecated ``getdata`` (Pillow >= 12)."""
    getter = getattr(image, "get_flattened_data", None)
    if getter is not None:
        return getter()
    return image.getdata()  # pragma: no cover - old Pillow


def _neighbours(index: int, width: int, height: int):
    if index % width:
        yield index - 1
    if index % width != width - 1:
        yield index + 1
    if index >= width:
        yield index - width
    if index < width * (height - 1):
        yield index + width


def _label_components(
    mask: bytearray,
    width: int,
    height: int,
    *,
    min_area: int,
    max_area: int,
) -> list[tuple[int, int, int, int, int]]:
    """4-connected components of ``mask``.

    Returns ``(area, x0, y0, x1, y1)`` with exclusive right/bottom edges.
    """
    visited = bytearray(len(mask))
    found: list[tuple[int, int, int, int, int]] = []
    for start in range(len(mask)):
        if not mask[start] or visited[start]:
            continue
        visited[start] = 1
        queue: Queue[int] = Queue()
        queue.append(start)
        area = 0
        x0, y0, x1, y1 = width, height, -1, -1
        while queue:
            index = queue.popleft()
            area += 1
            x = index % width
            y = index // width
            if x < x0:
                x0 = x
            if y < y0:
                y0 = y
            if x > x1:
                x1 = x
            if y > y1:
                y1 = y
            for neighbour in _neighbours(index, width, height):
                if mask[neighbour] and not visited[neighbour]:
                    visited[neighbour] = 1
                    queue.append(neighbour)
        if min_area <= area <= max_area:
            found.append((area, x0, y0, x1 + 1, y1 + 1))
    return found


def _outside_mask(mask: bytearray, width: int, height: int) -> bytearray:
    """Mark the mask pixels reachable from the image border."""
    outside = bytearray(len(mask))
    queue: Queue[int] = Queue()
    for x in range(width):
        for y in (0, height - 1):
            index = y * width + x
            if mask[index] and not outside[index]:
                outside[index] = 1
                queue.append(index)
    for y in range(height):
        for x in (0, width - 1):
            index = y * width + x
            if mask[index] and not outside[index]:
                outside[index] = 1
                queue.append(index)
    while queue:
        index = queue.popleft()
        for neighbour in _neighbours(index, width, height):
            if mask[neighbour] and not outside[neighbour]:
                outside[neighbour] = 1
                queue.append(neighbour)
    return outside


def _find_text(
    gray: Image.Image,
    box: tuple[int, int, int, int],
    ink_threshold: int,
) -> tuple[int, int, int, int] | None:
    """Union of the small ink blobs inside ``box`` (the text, not the outline)."""
    width, height = gray.size
    pad = 3
    x0 = max(0, box[0] - pad)
    y0 = max(0, box[1] - pad)
    x1 = min(width, box[2] + pad)
    y1 = min(height, box[3] + pad)
    crop = gray.crop((x0, y0, x1, y1))
    cw, ch = crop.size
    if cw < 8 or ch < 8:
        return None

    ink = bytearray(1 if value < ink_threshold else 0 for value in _pixels(crop))
    components = _label_components(
        ink, cw, ch, min_area=2, max_area=max(8, int(0.25 * cw * ch))
    )
    tx0, ty0, tx1, ty1 = cw, ch, -1, -1
    for _area, ax0, ay0, ax1, ay1 in components:
        # The balloon outline/artwork touches the crop border; the text does not.
        if ax0 <= 0 or ay0 <= 0 or ax1 >= cw or ay1 >= ch:
            continue
        tx0 = min(tx0, ax0)
        ty0 = min(ty0, ay0)
        tx1 = max(tx1, ax1)
        ty1 = max(ty1, ay1)
    if tx1 <= tx0 or ty1 <= ty0:
        return None

    text = (x0 + tx0, y0 + ty0, x0 + tx1, y0 + ty1)
    if text[2] - text[0] < 4 or text[3] - text[1] < 4:
        return None
    # Text must sit strictly inside the region: otherwise the outline leaked in.
    if text[0] <= box[0] or text[1] <= box[1] or text[2] >= box[2] or text[3] >= box[3]:
        return None
    return text


def detect_regions(
    img: Image.Image,
    *,
    detect_side: int = 600,
    ink_threshold: int = 128,
    white_threshold: int = 200,
    min_region_frac: float = 0.0008,
    max_region_frac: float = 0.5,
    min_side: int = 24,
) -> list[Region]:
    """Find candidate text regions in ``img`` (full-resolution coordinates)."""
    gray = to_grayscale(img)
    width, height = gray.size
    if width < 48 or height < 48:
        return []

    ratio = min(1.0, detect_side / max(width, height))
    dw, dh = max(1, round(width * ratio)), max(1, round(height * ratio))
    small = gray.resize((dw, dh), Image.BILINEAR)

    white = bytearray(1 if value >= white_threshold else 0 for value in _pixels(small))
    outside = _outside_mask(white, dw, dh)
    enclosed = bytearray(
        1 if (white[i] and not outside[i]) else 0 for i in range(len(white))
    )

    components = _label_components(
        enclosed,
        dw,
        dh,
        min_area=max(20, int(min_region_frac * dw * dh)),
        max_area=int(max_region_frac * dw * dh),
    )

    sx, sy = width / dw, height / dh
    regions: list[Region] = []
    for area, x0, y0, x1, y1 in components:
        bx0 = max(0, int(x0 * sx))
        by0 = max(0, int(y0 * sy))
        bx1 = min(width, int(round(x1 * sx)))
        by1 = min(height, int(round(y1 * sy)))
        if bx1 - bx0 < min_side or by1 - by0 < min_side:
            continue
        text = _find_text(gray, (bx0, by0, bx1, by1), ink_threshold)
        if text is None:
            continue
        bbox_area = max(1, (x1 - x0) * (y1 - y0))
        solidity = area / bbox_area
        kind = "box" if solidity >= 0.85 else "bubble"
        regions.append(
            Region((bx0, by0, bx1, by1), text, kind, int(area * sx * sy))
        )
    return regions
