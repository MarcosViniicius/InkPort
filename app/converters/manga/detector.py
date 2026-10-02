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


def interior_ink_mask(mask: bytearray, width: int, height: int, *, max_area: int) -> bytearray:
    """Keep only ink components that do not touch the border (the text).

    A region crop still contains the balloon outline and bits of art where the
    curve crosses the bounding box. Those run to the edge; the glyphs do not.
    """
    visited = bytearray(len(mask))
    keep = bytearray(len(mask))
    for start in range(len(mask)):
        if not mask[start] or visited[start]:
            continue
        queue: Queue[int] = Queue()
        queue.append(start)
        visited[start] = 1
        pixels = [start]
        touches = False
        while queue:
            index = queue.popleft()
            x = index % width
            y = index // width
            if x == 0 or y == 0 or x == width - 1 or y == height - 1:
                touches = True
            for neighbour in _neighbours(index, width, height):
                if mask[neighbour] and not visited[neighbour]:
                    visited[neighbour] = 1
                    queue.append(neighbour)
                    pixels.append(neighbour)
        if touches or len(pixels) > max_area:
            continue
        for index in pixels:
            keep[index] = 1
    return keep


def denoise_mask(mask: bytearray, width: int, height: int, *, min_area: int) -> None:
    """Drop speckles (scanner/JPEG noise) that would block the free-space scan."""
    visited = bytearray(len(mask))
    for start in range(len(mask)):
        if not mask[start] or visited[start]:
            continue
        queue: Queue[int] = Queue()
        queue.append(start)
        visited[start] = 1
        pixels = [start]
        while queue:
            index = queue.popleft()
            for neighbour in _neighbours(index, width, height):
                if mask[neighbour] and not visited[neighbour]:
                    visited[neighbour] = 1
                    queue.append(neighbour)
                    pixels.append(neighbour)
        if len(pixels) < min_area:
            for index in pixels:
                mask[index] = 0


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


def _area(box: tuple[int, int, int, int]) -> int:
    return max(0, box[2] - box[0]) * max(0, box[3] - box[1])


def _intersection(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> int:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return 0
    return (x1 - x0) * (y1 - y0)


def _merge_overlapping(
    items: list[tuple[int, tuple[int, int, int, int], str]],
) -> list[tuple[int, tuple[int, int, int, int], str]]:
    """Merge regions that share the same white area (the text splits a balloon
    into several white pieces, which would otherwise be processed twice)."""
    remaining = list(items)
    merged = True
    while merged:
        merged = False
        index = 0
        while index < len(remaining):
            area, box, kind = remaining[index]
            for other_index in range(index + 1, len(remaining)):
                other_area, other_box, other_kind = remaining[other_index]
                overlap = _intersection(box, other_box)
                if not overlap:
                    continue
                if overlap / max(1, min(_area(box), _area(other_box))) < 0.35:
                    continue
                union = (
                    min(box[0], other_box[0]),
                    min(box[1], other_box[1]),
                    max(box[2], other_box[2]),
                    max(box[3], other_box[3]),
                )
                if other_area > area:
                    area, kind = other_area, other_kind
                remaining[index] = (area, union, kind)
                del remaining[other_index]
                merged = True
                break
            index += 1
    return remaining


def _find_text(
    gray: Image.Image,
    box: tuple[int, int, int, int],
    ink_threshold: int,
) -> tuple[int, int, int, int] | None:
    """Union of the small ink blobs inside ``box`` (the text, not the outline).

    The crop must not be padded: with padding, ink near the region edge is kept
    by the "does not touch the crop border" rule and then rejected for falling
    outside the region -- which silently discarded almost every real balloon
    (their text sits close to the outline). Cropping exactly at the region also
    keeps the balloon outline out of the text candidates.
    """
    width, height = gray.size
    pad = 0
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
    kept = []
    for area, ax0, ay0, ax1, ay1 in components:
        # The balloon outline/artwork touches the crop border; the text does not.
        if ax0 <= 0 or ay0 <= 0 or ax1 >= cw or ay1 >= ch:
            continue
        kept.append((area, ax0, ay0, ax1, ay1))
    if not kept:
        return None
    # A single tiny blob is usually a highlight in the artwork, not text.
    if len(kept) == 1:
        area, ax0, ay0, ax1, ay1 = kept[0]
        if area < 40 or (ax1 - ax0) < 5 or (ay1 - ay0) < 5:
            return None

    tx0, ty0, tx1, ty1 = cw, ch, -1, -1
    for _area, ax0, ay0, ax1, ay1 in kept:
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
    raw: list[tuple[int, tuple[int, int, int, int], str]] = []
    for area, x0, y0, x1, y1 in components:
        bx0 = max(0, int(x0 * sx))
        by0 = max(0, int(y0 * sy))
        bx1 = min(width, int(round(x1 * sx)))
        by1 = min(height, int(round(y1 * sy)))
        if bx1 - bx0 < min_side or by1 - by0 < min_side:
            continue
        bbox_area = max(1, (x1 - x0) * (y1 - y0))
        kind = "box" if area / bbox_area >= 0.85 else "bubble"
        raw.append((int(area * sx * sy), (bx0, by0, bx1, by1), kind))

    regions: list[Region] = []
    for area, box, kind in _merge_overlapping(raw):
        text = _find_text(gray, box, ink_threshold)
        if text is None:
            continue
        regions.append(Region(box, text, kind, area))
    regions.sort(key=lambda region: region.area, reverse=True)
    return regions
