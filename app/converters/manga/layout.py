"""Decide how much a region's text can grow.

Strategy **A** ("margem disponível"): the text fits inside the balloon with room
to spare, so it grows while keeping a comfortable inner margin.

Strategy **B** ("pouca margem"): there is little room, so the text is allowed to
cross its original box a little -- bounded by ``max_overflow_px`` around the
detected region, and only when the destination is background (checked by the
renderer). The artwork outside that band is never touched.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.converters.manga.detector import Region, _pixels, denoise_mask

#: Fit below this means "little margin" -> strategy B.
LITTLE_MARGIN = 1.15
#: Inner padding kept between text and balloon edge (fraction of the region).
INNER_PAD_RATIO = 0.06
#: Below this gain the enlargement is pointless and is skipped.
MIN_USEFUL_SCALE = 1.06


def usable_box(
    gray,
    region_box: tuple[int, int, int, int],
    text_box: tuple[int, int, int, int],
    *,
    ink_threshold: int = 128,
    min_word_width: int = 6,
) -> tuple[int, int, int, int]:
    """Largest centred rectangle of free space around the text, inside the region.

    A balloon is round: its bounding box would let the text spill over the
    outline at the top and bottom. This scans the free space row by row (the
    text itself is masked out, since it is what will be redrawn) and returns the
    centred rectangle with the largest area that still fits the artwork.
    """
    rx0, ry0, rx1, ry1 = region_box
    crop = gray.crop(region_box)
    width, height = crop.size
    if width < 8 or height < 8:
        return region_box
    ink = bytearray(1 if value < ink_threshold else 0 for value in _pixels(crop))
    denoise_mask(ink, width, height, min_area=6)

    # The text is what we are going to redraw: it must not bound the free space.
    tx0 = max(0, text_box[0] - rx0 - 2)
    ty0 = max(0, text_box[1] - ry0 - 2)
    tx1 = min(width, text_box[2] - rx0 + 2)
    ty1 = min(height, text_box[3] - ry0 + 2)
    for y in range(ty0, ty1):
        base = y * width
        for x in range(tx0, tx1):
            ink[base + x] = 0

    center_x = (text_box[0] + text_box[2]) // 2 - rx0
    center_y = (text_box[1] + text_box[3]) // 2 - ry0
    center_x = min(max(center_x, 0), width - 1)
    center_y = min(max(center_y, 0), height - 1)

    free = [0] * height
    for y in range(height):
        base = y * width
        left = center_x
        while left >= 0 and not ink[base + left]:
            left -= 1
        right = center_x
        while right < width and not ink[base + right]:
            right += 1
        free[y] = (center_x - left - 1) + (right - center_x - 1)

    best_width, best_half, best_area = 0, 0, 0
    current = 1 << 30
    limit = min(center_y, height - 1 - center_y)
    for half in range(0, limit + 1):
        for y in (center_y - half, center_y + half):
            if free[y] < current:
                current = free[y]
        area = current * (2 * half + 1)
        if current >= min_word_width and area > best_area:
            best_area = area
            best_width, best_half = current, half

    if best_width < min_word_width:
        return region_box
    left = max(0, center_x - best_width // 2)
    top = max(0, center_y - best_half)
    return (
        rx0 + left,
        ry0 + top,
        rx0 + min(width, left + best_width),
        ry0 + min(height, top + 2 * best_half + 1),
    )


@dataclass(slots=True)
class ScalePlan:
    """Where the original text raster should be pasted, and at which scale."""

    scale: float
    strategy: str  # "A" or "B"
    region_box: tuple[int, int, int, int]
    source_box: tuple[int, int, int, int]
    target_box: tuple[int, int, int, int]


def plan_enlargement(
    region: Region,
    *,
    max_scale: float = 2.0,
    overflow: float = 0.15,
    max_overflow_px: int = 10,
    fit_box: tuple[int, int, int, int] | None = None,
) -> ScalePlan | None:
    """Compute the enlargement plan for one region, or ``None`` to skip it."""
    rx0, ry0, rx1, ry1 = region.box
    rw, rh = max(1, rx1 - rx0), max(1, ry1 - ry0)

    tx0, ty0, tx1, ty1 = region.text_box
    tw, th = max(1, tx1 - tx0), max(1, ty1 - ty0)

    outer_x0, outer_y0, outer_x1, outer_y1 = fit_box or region.box
    ow, oh = max(1, outer_x1 - outer_x0), max(1, outer_y1 - outer_y0)
    pad = max(2, int(min(ow, oh) * INNER_PAD_RATIO))
    avail_w = max(1, ow - 2 * pad)
    avail_h = max(1, oh - 2 * pad)
    fit = min(avail_w / tw, avail_h / th)

    strategy = "A" if fit >= LITTLE_MARGIN else "B"
    allowance = overflow if strategy == "B" else 0.0
    scale = min(fit * (1.0 + allowance), max_scale)

    # Never paste the text outside the region by more than the allowed band.
    fit_allowed = min(
        (rw + 2 * max_overflow_px) / tw,
        (rh + 2 * max_overflow_px) / th,
    )
    scale = min(scale, fit_allowed)
    scale = max(1.0, min(scale, max_scale))
    if scale < MIN_USEFUL_SCALE:
        return None

    cx = (tx0 + tx1) / 2.0
    cy = (ty0 + ty1) / 2.0
    new_w = max(1, round(tw * scale))
    new_h = max(1, round(th * scale))
    nx0 = round(cx - new_w / 2.0)
    ny0 = round(cy - new_h / 2.0)
    target = (nx0, ny0, nx0 + new_w, ny0 + new_h)
    return ScalePlan(scale, strategy, region.box, region.text_box, target)
