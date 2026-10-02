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

from app.converters.manga.detector import Region

#: Fit below this means "little margin" -> strategy B.
LITTLE_MARGIN = 1.15
#: Inner padding kept between text and balloon edge (fraction of the region).
INNER_PAD_RATIO = 0.06
#: Below this gain the enlargement is pointless and is skipped.
MIN_USEFUL_SCALE = 1.06


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
) -> ScalePlan | None:
    """Compute the enlargement plan for one region, or ``None`` to skip it."""
    rx0, ry0, rx1, ry1 = region.box
    rw, rh = max(1, rx1 - rx0), max(1, ry1 - ry0)

    tx0, ty0, tx1, ty1 = region.text_box
    tw, th = max(1, tx1 - tx0), max(1, ty1 - ty0)

    pad = max(2, int(min(rw, rh) * INNER_PAD_RATIO))
    avail_w = max(1, rw - 2 * pad)
    avail_h = max(1, rh - 2 * pad)
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
