"""Normalising page images for the target device (or for any screen).

Shared by image-EPUB, image-CBZ and image-PDF, so sizing, cropping, grayscale,
levels and sharpening are implemented exactly once.

Two modes:

* **fit** (device profile with a known screen): resize to the device panel, with
  a high-quality (Lanczos) downscale. This is what gives a crisp page on a
  specific reader, whose own scaler is only nearest-neighbour.
* **preserve** ("universal" profile): keep the source resolution, capped so any
  reader can handle it. The EPUB then adapts to whatever screen it is opened on.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image, ImageOps

from app.converters.imageops import (
    apply_contrast,
    apply_gamma,
    autocrop_margins,
    cap_long_side,
    cap_size,
    fit_image,
    open_image,
    quantize_levels,
    save_image,
    sharpen_image,
    to_grayscale,
)
from app.converters.spreads import expand_spreads
from app.devices.profile import DeviceProfile

logger = logging.getLogger(__name__)

#: Verified limits in CrossPoint's image converters (JpegToBmpConverter.cpp:
#: MAX_IMAGE_WIDTH/MAX_IMAGE_HEIGHT). Bigger images are rejected by the reader
#: with "Image too large or invalid", so we never emit anything above this.
SAFE_MAX_WIDTH = 2048
SAFE_MAX_HEIGHT = 3072

#: Fallback cap for universal profiles that do not define their own.
DEFAULT_UNIVERSAL_LONG_SIDE = 2000


def resolve_options(profile: DeviceProfile, options: dict | None = None) -> dict:
    """Merge caller options over the profile defaults."""
    options = options or {}
    return {
        "grayscale": bool(options.get("grayscale", profile.grayscale)),
        "colour": bool(options.get("colour", profile.color)),
        "quality": int(options.get("quality", profile.image_quality)),
        "image_format": str(options.get("image_format", profile.image_format or "jpg")).lower(),
        "crop_margins": bool(options.get("crop_margins", profile.crop_margins)),
        "rotate_portrait": bool(options.get("rotate_portrait", profile.rotate_portrait)),
        "gamma": float(options.get("gamma", profile.gamma)),
        "contrast": float(options.get("contrast", profile.contrast)),
        "sharpen": float(options.get("sharpen", profile.sharpen)),
        "autocontrast": bool(options.get("autocontrast", profile.autocontrast)),
        "allow_upscale": bool(options.get("allow_upscale", profile.allow_upscale)),
        "preserve_resolution": bool(
            options.get("preserve_resolution", profile.preserve_resolution)
        ),
        "max_long_side": int(
            options.get("max_long_side", profile.max_long_side) or DEFAULT_UNIVERSAL_LONG_SIDE
        ),
        "posterize_levels": int(options.get("posterize_levels", profile.posterize_levels) or 0),
        "spreads": str(options.get("spreads", profile.spreads) or "none").lower(),
        "right_to_left": bool(
            options.get("right_to_left")
            or options.get("reading_direction") == "rtl"
            or profile.manga_rtl
        ),
        "rotate_right": bool(options.get("rotate_right", False)),
        "dither": bool(options.get("dither", profile.gray_levels <= 2)),
        "render_dpi": int(options.get("dpi", profile.render_dpi) or 0),
        # Manga/comic text enlargement (opt-in; see app/converters/manga).
        "manga_enlarge_text": bool(options.get("manga_enlarge_text", False)),
        "manga_max_scale": float(options.get("manga_max_scale", 2.0) or 2.0),
        "manga_overflow": float(options.get("manga_overflow", 0.15) or 0.0),
        "manga_max_overflow_px": int(options.get("manga_max_overflow_px", 10) or 0),
    }


def transform_page(img: Image.Image, profile: DeviceProfile, opts: dict):
    """Apply the full per-page pipeline to one opened image."""
    grayscale = opts["grayscale"] and not opts["colour"]

    # 1. tone: grayscale -> levels (autocontrast helps dark scans enormously)
    if grayscale:
        img = to_grayscale(img)
    if opts["autocontrast"]:
        img = ImageOps.autocontrast(img, cutoff=1)

    # 1b. enlarge the text before resizing: more resolution = crisper glyphs.
    if opts["manga_enlarge_text"]:
        img = _enlarge_manga_text(img, opts)

    # 2. geometry: crop blank borders, then resize for the target
    if opts["crop_margins"]:
        img = autocrop_margins(img)
    img = _resize(img, profile, opts)

    # 3. finish: sharpen after resizing (sharpening before just amplifies noise)
    img = sharpen_image(img, opts["sharpen"])
    img = apply_contrast(img, opts["contrast"])
    if opts["gamma"] != 1.0:
        img = apply_gamma(img, opts["gamma"])

    # 4. quantise to the display's real gray levels (large, honest size win)
    if opts["posterize_levels"] >= 2 and grayscale:
        img = quantize_levels(img, opts["posterize_levels"], dither=opts["dither"])

    return img, grayscale


def _enlarge_manga_text(img: Image.Image, opts: dict):
    """Best-effort manga/comic text enlargement; never breaks a conversion."""
    try:
        from app.converters.manga import enlarge_page

        enlarged, stats = enlarge_page(img, options=opts)
        if stats.get("enlarged"):
            logger.info("manga text enlarged", extra=stats)
        return enlarged
    except Exception as exc:  # noqa: BLE001 - optional nicety, not a requirement
        logger.warning("manga text enlargement skipped", extra={"error": str(exc)})
        return img


def _resize(img: Image.Image, profile: DeviceProfile, opts: dict) -> Image.Image:
    if opts["preserve_resolution"]:
        img = cap_long_side(img, opts["max_long_side"])
    else:
        img = fit_image(
            img,
            profile.target_width,
            profile.target_height,
            allow_upscale=opts["allow_upscale"],
            allow_rotate=opts["rotate_portrait"],
        )
    # Hard safety net: the reader rejects anything above these bounds.
    return cap_size(img, SAFE_MAX_WIDTH, SAFE_MAX_HEIGHT)


def process_images(
    images: list[Path],
    profile: DeviceProfile,
    outdir: Path,
    *,
    options: dict | None = None,
    progress=None,
) -> list[Path]:
    """Normalise every page for ``profile``, preserving order."""
    opts = resolve_options(profile, options)
    outdir.mkdir(parents=True, exist_ok=True)
    ext = _output_ext(opts)

    processed: list[Path] = []
    total = len(images)
    output_index = 0
    for index, image_path in enumerate(images, start=1):
        try:
            img = open_image(image_path)
        except Exception as exc:
            logger.warning(
                "skipping unreadable page",
                extra={"page": str(image_path), "error": str(exc)},
            )
            continue

        # A spread may become two pages (or a rotated one) before normalising.
        for page in expand_spreads(img, profile, opts):
            output_index += 1
            page, grayscale = transform_page(page, profile, opts)
            target = outdir / f"{output_index:05d}.{ext}"
            save_image(
                page,
                target,
                quality=opts["quality"],
                grayscale=grayscale,
                # Bilevel dithering is handled by quantize_levels; don't redo it.
                dither=opts["dither"] and opts["posterize_levels"] < 2,
            )
            processed.append(target)

        if progress and (index % 5 == 0 or index == total):
            progress(int(index / max(1, total) * 100), f"{index}/{total} páginas")

    if not processed:
        raise RuntimeError("Nenhuma página pôde ser processada.")
    return processed


def _output_ext(opts: dict) -> str:
    fmt = opts.get("image_format") or "jpg"
    return {"jpeg": "jpg", "png": "png", "webp": "webp", "bmp": "bmp"}.get(fmt, "jpg")
