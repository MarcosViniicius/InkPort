"""Render the enlargement: erase the small text and paste it back bigger.

The text is a raster crop, so "enlarging" it means scaling that crop up and
pasting it over a cleared background. Before pasting, the free area the text
would spill into is checked: if it is not background (artwork/outline), the
scale is reduced until it is -- this is what keeps the rest of the page intact.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw

from app.converters.manga.layout import MIN_USEFUL_SCALE, ScalePlan

if TYPE_CHECKING:
    from app.converters.manga.reflow import ReflowPlan

logger = logging.getLogger(__name__)

try:  # Pillow >= 9.1
    RESAMPLE = Image.Resampling.LANCZOS
except AttributeError:  # pragma: no cover
    RESAMPLE = Image.LANCZOS

#: How close to the background a spilled pixel must be to count as free space.
BACKGROUND_TOLERANCE = 42


def _luminance(color) -> float:
    if isinstance(color, tuple):
        if len(color) >= 3:
            return 0.299 * color[0] + 0.587 * color[1] + 0.114 * color[2]
        return float(color[0])
    return float(color)


def _background_color(img: Image.Image, box: tuple[int, int, int, int]):
    """Dominant colour inside the region (the balloon fill)."""
    width, height = img.size
    x0 = max(0, box[0])
    y0 = max(0, box[1])
    x1 = min(width, box[2])
    y1 = min(height, box[3])
    if x1 - x0 < 2 or y1 - y0 < 2:
        return 255 if img.mode in {"L", "1"} else (255, 255, 255)
    sample = img.crop((x0, y0, x1, y1)).resize((24, 24))
    colors = sample.getcolors(maxcolors=24 * 24)
    if not colors:
        return 255 if img.mode in {"L", "1"} else (255, 255, 255)
    color = max(colors, key=lambda item: item[0])[1]
    if img.mode == "L" and isinstance(color, tuple):
        return color[0]
    if img.mode == "RGB" and not isinstance(color, tuple):
        return (color, color, color)
    return color


def _band_is_background(
    img: Image.Image,
    target: tuple[int, int, int, int],
    region: tuple[int, int, int, int],
    bg_luma: float,
) -> bool:
    """True when everything the text would spill over is background."""
    px = img.load()
    tx0, ty0, tx1, ty1 = target
    rx0, ry0, rx1, ry1 = region
    checked = 0
    bad = 0
    for y in range(max(0, ty0), min(img.height, ty1)):
        for x in range(max(0, tx0), min(img.width, tx1)):
            if rx0 <= x < rx1 and ry0 <= y < ry1:
                continue
            checked += 1
            if abs(_luminance(px[x, y]) - bg_luma) > BACKGROUND_TOLERANCE:
                bad += 1
    if checked == 0:
        return True
    return bad <= 0.1 * checked


def apply_reflow(
    img: Image.Image,
    plan: ReflowPlan,
    *,
    ink_threshold: int = 128,
) -> bool:
    """Clear the original block and paste the re-flowed words (glyphs only).

    Each word is cropped from the original before anything is erased and pasted
    with an alpha mask, so the balloon's own background (even textured) is kept.
    """
    crops = []
    for line in plan.lines:
        for item in line:
            crops.append((item, img.crop(item.source)))
    if not crops:
        return False

    bg = _background_color(img, plan.region_box)
    draw = ImageDraw.Draw(img)
    sx0, sy0, sx1, sy1 = plan.source_box
    clear = (max(0, sx0 - 1), max(0, sy0 - 1), min(img.width, sx1 + 1), min(img.height, sy1 + 1))
    draw.rectangle(clear, fill=bg)

    for item, crop in crops:
        tx0, ty0, tx1, ty1 = item.target
        width, height = tx1 - tx0, ty1 - ty0
        if width < 1 or height < 1:
            continue
        scaled = crop.resize((width, height), RESAMPLE)
        mask = _glyph_mask(scaled, ink_threshold)
        img.paste(scaled, (tx0, ty0), mask)
    return True


def _glyph_mask(image: Image.Image, ink_threshold: int) -> Image.Image:
    """Alpha ramp: dark glyph pixels opaque, paper transparent."""
    gray = image if image.mode == "L" else image.convert("L")
    ceiling = max(ink_threshold + 20, 200)
    span = max(1, ceiling - ink_threshold)

    def alpha(value: int) -> int:
        if value <= ink_threshold:
            return 255
        if value >= ceiling:
            return 0
        return int((ceiling - value) * 255 / span)

    return gray.point(alpha)


def _clip_target(
    target: tuple[int, int, int, int], width: int, height: int
) -> tuple[int, int, int, int]:
    tx0, ty0, tx1, ty1 = target
    box_w, box_h = tx1 - tx0, ty1 - ty0
    tx0 = min(max(0, tx0), max(0, width - box_w))
    ty0 = min(max(0, ty0), max(0, height - box_h))
    return (tx0, ty0, tx0 + box_w, ty0 + box_h)


def apply_plan(img: Image.Image, plan: ScalePlan) -> bool:
    """Apply one plan to ``img`` (in place). Returns whether it changed anything."""
    width, height = img.size
    rx0, ry0, rx1, ry1 = plan.region_box
    tx0, ty0, tx1, ty1 = plan.source_box
    tw, th = max(1, tx1 - tx0), max(1, ty1 - ty0)

    bg = _background_color(img, plan.region_box)
    bg_luma = _luminance(bg)

    scale = plan.scale
    target = _clip_target(plan.target_box, width, height)

    # If the spill area is not free space, pull the text back inside the region.
    if not _band_is_background(img, target, plan.region_box, bg_luma):
        fit_region = min((rx1 - rx0) / tw, (ry1 - ry0) / th)
        scale = max(1.0, min(scale, fit_region))
        if scale < MIN_USEFUL_SCALE:
            return False
        new_w = max(1, round(tw * scale))
        new_h = max(1, round(th * scale))
        cx = (tx0 + tx1) / 2.0
        cy = (ty0 + ty1) / 2.0
        target = _clip_target(
            (round(cx - new_w / 2), round(cy - new_h / 2),
             round(cx - new_w / 2) + new_w, round(cy - new_h / 2) + new_h),
            width,
            height,
        )

    new_w = target[2] - target[0]
    new_h = target[3] - target[1]
    if new_w < 1 or new_h < 1:
        return False

    crop = img.crop(plan.source_box)
    enlarged = crop.resize((new_w, new_h), RESAMPLE)

    draw = ImageDraw.Draw(img)
    # Clear the old text (plus a hair, to drop anti-aliased edges) with the fill.
    clear = (
        max(0, tx0 - 1),
        max(0, ty0 - 1),
        min(width, tx1 + 1),
        min(height, ty1 + 1),
    )
    draw.rectangle(clear, fill=bg)
    img.paste(enlarged, (target[0], target[1]))
    return True
