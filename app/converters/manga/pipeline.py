"""Per-page orchestration: detect -> measure -> enlarge.

Best-effort by design: any region that cannot be interpreted is left alone and
never fails the conversion. Returns the (possibly) modified image plus a small
stats dict for logging and tests.
"""

from __future__ import annotations

import logging

from PIL import Image

from app.converters.manga.detector import detect_regions
from app.converters.manga.enlarge import apply_plan
from app.converters.manga.layout import plan_enlargement

logger = logging.getLogger(__name__)

#: A page rarely needs more than this many regions enlarged; caps the work.
MAX_REGIONS = 16


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

    try:
        regions = detect_regions(img, detect_side=detect_side)
    except Exception as exc:  # noqa: BLE001 - detection is best-effort
        logger.warning("manga text detection failed", extra={"error": str(exc)})
        return img, {"regions": 0, "enlarged": 0, "strategy_a": 0, "strategy_b": 0}

    stats = {"regions": len(regions), "enlarged": 0, "strategy_a": 0, "strategy_b": 0}
    for region in sorted(regions, key=lambda item: item.area, reverse=True):
        if stats["enlarged"] >= MAX_REGIONS:
            break
        try:
            plan = plan_enlargement(
                region,
                max_scale=max_scale,
                overflow=overflow,
                max_overflow_px=max_overflow_px,
            )
            if plan is None:
                continue
            if apply_plan(img, plan):
                stats["enlarged"] += 1
                if plan.strategy == "A":
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
