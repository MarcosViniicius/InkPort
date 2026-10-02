"""Manga/comic text enlargement -- classic computer vision, no AI required.

Bubbles and loose text are found with a pure-Pillow pipeline (binarise, flood
fill from the border to find enclosed white areas, label components), the free
space around the text is measured and the text raster is enlarged to use it.

Everything here is best-effort and offline: no OCR, no ONNX, no external binary
and no network. A page that cannot be interpreted is returned untouched.
"""

from app.converters.manga.pipeline import enlarge_page

__all__ = ["enlarge_page"]
