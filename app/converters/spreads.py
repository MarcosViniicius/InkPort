"""Double-page spread handling (port of KCC's ``image.py::splitCheck``).

A manga volume has two kinds of page:

* a normal page (portrait) — kept as is;
* a **spread** (a drawn double page, landscape) — on a portrait reader it would
  be shrunk to a small strip, so KCC either **splits** it in two or **rotates**
  it 90 degrees so it fills the screen when the device is turned.

Reference behaviour (``--splitter`` default ``0: Split``, ``--manga-style``
reverses the halves, ``--norotate``/``--rotateright`` tweak the rotation):

================  =====================================================
aspect ratio      action
================  =====================================================
<= 1.16           nothing (not a spread)
1.16 .. 1.8       split into two pages
>= 1.8            rotate 90 degrees
================  =====================================================
"""

from __future__ import annotations

from PIL import Image

from app.devices.profile import DeviceProfile

#: Below this the page is not considered a spread (KCC uses 1.16).
SPREAD_MIN_RATIO = 1.16
#: At or above this, rotating is better than splitting (KCC uses 1.8).
SPREAD_BISECT_RATIO = 1.8

SPREAD_MODES = ("none", "auto", "split", "rotate", "both")


def expand_spreads(
    img: Image.Image, profile: DeviceProfile, opts: dict
) -> list[Image.Image]:
    """Return the page(s) an image becomes: itself, two halves, or a rotated page.

    ``opts["spreads"]`` is one of ``none`` (never touch), ``auto`` (KCC default:
    split or rotate), ``split``, ``rotate`` or ``both``.
    """
    mode = str(opts.get("spreads") or "none").lower()
    if mode == "none" or mode not in SPREAD_MODES:
        return [img]

    width, height = img.size
    if height <= 0 or width <= 0:
        return [img]

    target_w = profile.target_width
    target_h = profile.target_height
    # Only spreads matter: the page is landscape while the screen is portrait
    # (or the other way round).
    if (width > height) == (target_w > target_h):
        return [img]

    ratio = width / height
    if ratio <= SPREAD_MIN_RATIO:
        return [img]

    pages: list[Image.Image] = []

    should_split = mode in {"auto", "split", "both"} and (
        mode == "both" or ratio < SPREAD_BISECT_RATIO
    )
    should_rotate = mode in {"rotate", "both"} or (
        mode == "auto" and ratio >= SPREAD_BISECT_RATIO
    )

    if should_split:
        left = img.crop((0, 0, width // 2, height))
        right = img.crop((width // 2, 0, width, height))
        # Manga is read right to left: the right half comes first.
        pages.extend([right, left] if opts.get("right_to_left") else [left, right])

    if should_rotate:
        angle = -90 if opts.get("rotate_right") else 90
        pages.append(img.rotate(angle, expand=True, resample=Image.Resampling.BICUBIC))

    return pages or [img]
