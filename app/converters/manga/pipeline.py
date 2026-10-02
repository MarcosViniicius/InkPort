"""Per-page orchestration: detect -> measure -> enlarge.

Three modes, tried in order of precision:

* ``experimental`` -- geometry only, no OCR (bubbles + raster words, see
  ``reflow.py``). Works everywhere, but segmentation on dense pages is fragile.
* ``ocr`` -- the optional RapidOCR backend reads the page, so whole words and
  their reading order are known; the original glyphs are still moved around.
* ``ocr_font`` -- same OCR, but the text is redrawn with the bundled font at the
  size that fills the balloon (clear and sharp; falls back to ``ocr`` per region
  when the OCR confidence is low).

Best-effort by design: any region that cannot be interpreted is left alone and
never fails the conversion.
"""

from __future__ import annotations

import logging

from PIL import Image

from app.converters.manga import ocr
from app.converters.manga.detector import Region, detect_regions
from app.converters.manga.enlarge import apply_plan, apply_reflow
from app.converters.manga.layout import plan_enlargement, usable_box
from app.converters.manga.ocr import OcrLine
from app.converters.manga.reflow import plan_reflow
from app.converters.manga.relayout import (
    block_from_ocr,
    order_lines,
    plan_rewrite,
    region_text_box,
    render_rewrite,
)
from app.converters.manga.segmentation import segment_words

logger = logging.getLogger(__name__)

#: A page rarely needs more than this many regions enlarged; caps the work.
MAX_REGIONS = 48


def _empty_stats() -> dict:
    return {
        "regions": 0,
        "enlarged": 0,
        "reflow": 0,
        "strategy_a": 0,
        "strategy_b": 0,
        "ocr": 0,
        "ocr_font": 0,
    }


def enlarge_page(
    img: Image.Image,
    *,
    options: dict | None = None,
    detect_side: int = 600,
) -> tuple[Image.Image, dict]:
    """Enlarge the text of ``img`` using the free space around it."""
    opts = options or {}
    mode = str(opts.get("manga_text_mode") or "").strip().lower()
    if not mode:
        mode = "experimental" if opts.get("manga_enlarge_text") else "off"
    if mode == "off":
        return img, _empty_stats()

    if mode in {"ocr", "ocr_font"}:
        stats = _enlarge_with_ocr(img, opts, mode)
        if stats is not None:
            return img, stats
        logger.warning("OCR unavailable or found no text; using the experimental mode")
        mode = "experimental"

    return _enlarge_geometry(img, opts, detect_side, mode)


def _enlarge_geometry(
    img: Image.Image, opts: dict, detect_side: int, mode: str
) -> tuple[Image.Image, dict]:
    max_scale = float(opts.get("manga_max_scale", 2.0) or 2.0)
    overflow = float(opts.get("manga_overflow", 0.15) or 0.0)
    max_overflow_px = int(opts.get("manga_max_overflow_px", 10) or 0)
    right_to_left = bool(opts.get("right_to_left"))

    try:
        regions = detect_regions(img, detect_side=detect_side)
    except Exception as exc:  # noqa: BLE001 - detection is best-effort
        logger.warning("manga text detection failed", extra={"error": str(exc)})
        return img, _empty_stats()

    stats = _empty_stats()
    stats["regions"] = len(regions)
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


def _belongs(line: OcrLine, box: tuple[int, int, int, int]) -> bool:
    """True when the OCR line is inside the region (or mostly inside it)."""
    x0, y0, x1, y1 = box
    cx = (line.box[0] + line.box[2]) / 2
    cy = (line.box[1] + line.box[3]) / 2
    if x0 <= cx <= x1 and y0 <= cy <= y1:
        return True
    overlap_x = max(0, min(x1, line.box[2]) - max(x0, line.box[0]))
    overlap_y = max(0, min(y1, line.box[3]) - max(y0, line.box[1]))
    line_area = max(1, (line.box[2] - line.box[0]) * (line.box[3] - line.box[1]))
    return (overlap_x * overlap_y) / line_area >= 0.5


def _enlarge_with_ocr(img: Image.Image, opts: dict, mode: str) -> dict | None:
    """OCR-guided enlargement; ``None`` means "fall back to experimental"."""
    lines = ocr.recognize(
        img,
        lang=str(opts.get("manga_ocr_lang") or "pt"),
        model_size=str(opts.get("manga_ocr_model") or "small"),
        min_score=float(opts.get("manga_ocr_min_score", 0.5) or 0.5),
    )
    if not lines:
        return None

    try:
        regions = detect_regions(img)
    except Exception as exc:  # noqa: BLE001
        logger.warning("manga text detection failed", extra={"error": str(exc)})
        return None

    max_scale = float(opts.get("manga_max_scale", 2.0) or 2.0)
    overflow = float(opts.get("manga_overflow", 0.15) or 0.0)
    max_overflow_px = int(opts.get("manga_max_overflow_px", 10) or 0)
    font_file = str(opts.get("manga_font") or "") or None
    font_min_score = float(opts.get("manga_font_min_score", 0.75) or 0.0)

    stats = _empty_stats()
    stats["regions"] = len(regions)
    stats["ocr_lines"] = len(lines)
    taken: set[int] = set()
    gray = img if img.mode in {"L", "1"} else img.convert("L")

    for region in sorted(regions, key=lambda item: item.area, reverse=True):
        if stats["enlarged"] >= MAX_REGIONS:
            break
        picked = [
            index
            for index, line in enumerate(lines)
            if index not in taken and _belongs(line, region.box)
        ]
        if not picked:
            continue
        taken.update(picked)
        chosen = [lines[index] for index in picked]
        try:
            changed = _relayout_region(
                img, gray, region, chosen, mode, max_scale, overflow, max_overflow_px,
                font_file, font_min_score,
            )
        except Exception as exc:  # noqa: BLE001 - never break the page
            logger.debug("OCR region skipped", extra={"error": str(exc)})
            continue
        if not changed:
            continue
        stats["enlarged"] += 1
        if changed == "ocr_font":
            stats["ocr_font"] += 1
        else:
            stats["ocr"] += 1
    return stats


def _relayout_region(
    img: Image.Image,
    gray: Image.Image,
    region: Region,
    chosen: list[OcrLine],
    mode: str,
    max_scale: float,
    overflow: float,
    max_overflow_px: int,
    font_file: str | None,
    font_min_score: float,
) -> str | bool:
    text_box = region_text_box(chosen, region.text_box)
    fit_box = usable_box(gray, region.box, text_box)

    confident = min(line.score for line in chosen) >= font_min_score
    if mode == "ocr_font" and confident:
        plan = plan_rewrite(order_lines(chosen), region.box, fit_box=fit_box, font_file=font_file)
        if plan is not None and render_rewrite(img, plan, font_file=font_file):
            return "ocr_font"

    # Conservative path: move the original glyphs. The OCR language is always
    # written left-to-right (the right-to-left flag is about page order).
    block = block_from_ocr(chosen, right_to_left=False)
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
        source_box=text_box,
    )
    if reflow_plan is not None and (
        uniform is None or reflow_plan.scale > uniform.scale + 1e-6
    ):
        return "ocr" if apply_reflow(img, reflow_plan) else False
    if uniform is not None:
        return "ocr" if apply_plan(img, uniform) else False
    return False
