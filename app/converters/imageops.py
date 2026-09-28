"""Pillow-based image operations shared by converters and cover generation.

These are the "no external tool required" primitives. External binaries
(ImageMagick, Ghostscript, ffmpeg, Calibre) are used only where Pillow cannot
do the job -- PDF rendering, RAR extraction, reflowable conversions.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps

try:  # Pillow >= 9.1
    RESAMPLE = Image.Resampling.LANCZOS
except AttributeError:  # pragma: no cover
    RESAMPLE = Image.LANCZOS

JPEG_EXTS = {"jpg", "jpeg"}
GRAYSCALE_FORMATS = {"png", "bmp", "jpeg", "jpg", "gif", "tiff", "webp"}


@dataclass(slots=True)
class ImageStats:
    width: int
    height: int
    mode: str
    dominant_is_gray: bool


def open_image(source) -> Image.Image:
    """Open and normalise an image from a path, bytes or file object.

    Applies EXIF rotation and flattens alpha onto white, so downstream code
    only ever sees ``RGB`` or ``L``.
    """
    import io

    if isinstance(source, (bytes, bytearray)):
        source = io.BytesIO(source)
    img = Image.open(source)
    return normalise(img)


def normalise(img: Image.Image) -> Image.Image:
    img = ImageOps.exif_transpose(img)
    if img.mode == "P":
        img = img.convert("RGBA")
    if img.mode in {"RGBA", "LA"}:
        background = Image.new("RGB", img.size, (255, 255, 255))
        background.paste(img, mask=img.split()[-1])
        img = background
    elif img.mode not in {"RGB", "L", "1"}:
        img = img.convert("RGB")
    return img


def is_grayscale(img: Image.Image, *, tolerance: int = 8) -> bool:
    if img.mode in {"L", "1"}:
        return True
    small = img.convert("RGB").resize((64, 64))
    return all(
        abs(r - g) <= tolerance and abs(g - b) <= tolerance
        for r, g, b in small.getdata()
    )


def to_grayscale(img: Image.Image) -> Image.Image:
    if img.mode in {"L", "1"}:
        return img
    return ImageOps.grayscale(img)


def fit_image(
    img: Image.Image,
    target_w: int,
    target_h: int,
    *,
    allow_upscale: bool = False,
    allow_rotate: bool = False,
    pad: bool = False,
    background: int = 255,
) -> Image.Image:
    """Fit ``img`` inside ``target_w x target_h`` keeping aspect ratio.

    ``allow_rotate`` rotates portrait pages 90 degrees when that fills the
    target panel better -- this is how a 800x480 landscape e-ink panel gets
    used fully by a portrait manga page.
    """
    src_w, src_h = img.size
    if src_w <= 0 or src_h <= 0:
        return img

    if allow_rotate and target_w > target_h and src_h > src_w:
        # Panel is landscape and the page is portrait: rotate to fill it.
        rotated = img.rotate(90, expand=True)
        if _fill_ratio(rotated.size, target_w, target_h) > _fill_ratio(img.size, target_w, target_h):
            img = rotated

    scale = min(target_w / img.width, target_h / img.height)
    if scale > 1 and not allow_upscale:
        scale = 1.0
    new_size = (max(1, round(img.width * scale)), max(1, round(img.height * scale)))
    if new_size != img.size:
        img = img.resize(new_size, RESAMPLE)

    if pad and img.size != (target_w, target_h):
        canvas = Image.new("RGBA" if img.mode == "RGBA" else "L" if img.mode == "L" else "RGB",
                           (target_w, target_h), background)
        canvas.paste(img, ((target_w - img.width) // 2, (target_h - img.height) // 2))
        img = canvas
    return img


def _fill_ratio(size: tuple[int, int], target_w: int, target_h: int) -> float:
    scale = min(target_w / size[0], target_h / size[1])
    return (size[0] * scale) * (size[1] * scale)


def autocrop_margins(
    img: Image.Image, *, threshold: int = 245, padding: int = 4, max_crop: float = 0.35
) -> Image.Image:
    """Trim uniform (white/blank) borders. ``max_crop`` caps how much is removed."""
    gray = to_grayscale(img)
    bw = gray.point(lambda p: 255 if p < threshold else 0)
    bbox = bw.getbbox()
    if not bbox:
        return img
    left, top, right, bottom = bbox
    w, h = img.size
    if (right - left) < w * (1 - max_crop) or (bottom - top) < h * (1 - max_crop):
        return img
    left = max(0, left - padding)
    top = max(0, top - padding)
    right = min(w, right + padding)
    bottom = min(h, bottom + padding)
    if (left, top, right, bottom) == (0, 0, w, h):
        return img
    return img.crop((left, top, right, bottom))


def apply_gamma(img: Image.Image, gamma: float) -> Image.Image:
    if abs(gamma - 1.0) < 1e-3:
        return img
    inv = 1.0 / max(0.05, gamma)
    table = [min(255, int(((i / 255.0) ** inv) * 255 + 0.5)) for i in range(256)]
    if img.mode == "L":
        return img.point(table)
    return img.point(table * 3 if img.mode == "RGB" else table)


def enhance_for_eink(img: Image.Image, *, contrast: float = 1.0) -> Image.Image:
    """Gentle sharpening + optional contrast -- helps line art on e-ink."""
    out = img
    if contrast != 1.0:
        from PIL import ImageEnhance

        out = ImageEnhance.Contrast(out).enhance(contrast)
    return out.filter(ImageFilter.UnsharpMask(radius=1.1, percent=60, threshold=3))


def sharpen_image(img: Image.Image, amount: float) -> Image.Image:
    """Unsharp mask; ``amount`` 0..2 (0.5 is a good gentle default for text)."""
    if amount <= 0:
        return img
    return img.filter(
        ImageFilter.UnsharpMask(radius=1.0, percent=int(amount * 100), threshold=3)
    )


def quantize_levels(img: Image.Image, levels: int, *, dither: bool = False) -> Image.Image:
    """Reduce to ``levels`` gray steps (a 16-level e-ink panel shows exactly 16).

    This is not a loss for the target device: it is the device's own capability,
    and it removes the halftone noise that JPEG would otherwise spend bits on.
    """
    if levels < 2:
        return img
    if levels == 2:
        bilevel = img.convert("1", dither=Image.Dither.FLOYDSTEINBERG if dither else Image.Dither.NONE)
        return bilevel
    step = 255 / (levels - 1)
    table = [min(255, int(round(round(value / step) * step))) for value in range(256)]
    return img.point(table)


def apply_contrast(img: Image.Image, factor: float) -> Image.Image:
    if factor == 1.0:
        return img
    from PIL import ImageEnhance

    return ImageEnhance.Contrast(img).enhance(factor)


def cap_size(img: Image.Image, max_w: int, max_h: int) -> Image.Image:
    """Downscale only if the image exceeds the given bounds (never upscales)."""
    if max_w <= 0 and max_h <= 0:
        return img
    scale = 1.0
    if max_w > 0 and img.width > max_w:
        scale = min(scale, max_w / img.width)
    if max_h > 0 and img.height > max_h:
        scale = min(scale, max_h / img.height)
    if scale >= 1.0:
        return img
    return img.resize(
        (max(1, round(img.width * scale)), max(1, round(img.height * scale))), RESAMPLE
    )


def cap_long_side(img: Image.Image, max_side: int) -> Image.Image:
    if not max_side or max(img.size) <= max_side:
        return img
    return cap_size(img, max_side, max_side)


def encode_image(
    img: Image.Image,
    ext: str,
    *,
    quality: int = 82,
    grayscale: bool = False,
    optimize: bool = True,
    dither: bool = False,
) -> bytes:
    """Encode an image to bytes in the given format (jpg/png/webp/bmp)."""
    import io

    ext = (ext or "jpg").lower().lstrip(".")
    if grayscale:
        img = to_grayscale(img)
        if dither:
            img = img.convert("1", dither=Image.Dither.FLOYDSTEINBERG)

    buffer = io.BytesIO()
    if ext in {"jpg", "jpeg"}:
        img.convert("L" if grayscale else "RGB").save(
            buffer, format="JPEG", quality=quality, optimize=optimize,
            progressive=False, subsampling=2 if quality < 90 else 0,
        )
    elif ext == "png":
        img.save(buffer, format="PNG", optimize=optimize)
    elif ext == "webp":
        img.save(buffer, format="WEBP", quality=quality, method=4)
    elif ext == "bmp":
        (img.convert("L") if grayscale else img.convert("RGB")).save(buffer, format="BMP")
    else:
        img.save(buffer, format=ext.upper())
    return buffer.getvalue()


def save_image(
    img: Image.Image,
    path: Path,
    *,
    quality: int = 82,
    grayscale: bool = False,
    optimize: bool = True,
    dither: bool = False,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        encode_image(
            img, path.suffix, quality=quality, grayscale=grayscale,
            optimize=optimize, dither=dither,
        )
    )
    return path


def make_thumbnail(img: Image.Image, max_side: int = 400, *, grayscale: bool = False) -> Image.Image:
    copy = img.copy()
    copy.thumbnail((max_side, max_side), RESAMPLE)
    if grayscale:
        copy = to_grayscale(copy)
    return copy


def image_stats(path: Path) -> ImageStats:
    img = open_image(path)
    return ImageStats(img.width, img.height, img.mode, is_grayscale(img))
