"""Per-page orchestration: detect -> measure -> enlarge.

Best-effort by design: any region that cannot be interpreted is left alone and
never fails the conversion. Returns the (possibly) modified image plus a small
stats dict for logging and tests.
"""

from __future__ import annotations

import logging

from PIL import Image

from app.converters.manga.detector import detect_regions
from app.converters.manga.enlarge import apply_plan, apply_reflow
from app.converters.manga.layout import plan_enlargement, usable_box
from app.converters.manga.reflow import plan_reflow
from app.converters.manga.segmentation import segment_words

logger = logging.getLogger(__name__)

#: A page rarely needs more than this many regions enlarged; caps the work.
MAX_REGIONS = 48


def enlarge_page(
    img: Image.Image,
    *,
    options: dict | None = None,
    detect_side: int = 600,
) -> tuple[Image.Image, dict]:
    """Enlarge the text of ``img`` using the free space around it."""
    opts = options or {}
    max_scale = float(opts.get("manga_max_scale", 2.0) or 2.0)
    overflow = float(opts.get("manga_overflow", 0.15) or 0.0)
    max_overflow_px = int(opts.get("manga_max_overflow_px", 10) or 0)
    right_to_left = bool(opts.get("right_to_left"))

    try:
        regions = detect_regions(img, detect_side=detect_side)
    except Exception as exc:  # noqa: BLE001 - detection is best-effort
        logger.warning("manga text detection failed", extra={"error": str(exc)})
        return img, {"regions": 0, "enlarged": 0, "reflow": 0, "strategy_a": 0, "strategy_b": 0}

    stats = {
        "regions": len(regions),
        "enlarged": 0,
        "reflow": 0,
        "strategy_a": 0,
        "strategy_b": 0,
    }
    gray = img if img.mode in {"L", "1"} else img.convert("L")
    for region in sorted(regions, key=lambda item: item.area, reverse=True):
        if stats["enlarged"] >= MAX_REGIONS:
            break
        try:
            block = segment_words(gray, region.box, right_to_left=right_to_left)
            fit_box = usable_box(gray, region.box, region.text_box)
            reflow_plan = (
                plan_reflow(block, region.box, max_scale=max_scale, fit_box=fit_box)
                if block
                else None
            )
            uniform = plan_enlargement(
                region,
                max_scale=max_scale,
                overflow=overflow,
                max_overflow_px=max_overflow_px,
                fit_box=fit_box,
            )
            changed = False
            if reflow_plan is not None and (
                uniform is None or reflow_plan.scale > uniform.scale + 1e-6
            ):
                changed = apply_reflow(img, reflow_plan)
                label = "reflow"
            elif uniform is not None:
                changed = apply_plan(img, uniform)
                label = uniform.strategy
            else:
                continue
            if changed:
                stats["enlarged"] += 1
                if label == "reflow":
                    stats["reflow"] += 1
                elif label == "A":
                    stats["strategy_a"] += 1
                else:
                    stats["strategy_b"] += 1
        except Exception as exc:  # noqa: BLE001 - never break the page
            logger.debug("manga region skipped", extra={"error": str(exc)})
            continue
    return img, stats


def available() -> bool:
    """The engine always works: it only needs Pillow."""
    return True
