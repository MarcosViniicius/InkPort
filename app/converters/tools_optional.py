"""Optional accelerators found on the machine.

Nothing here is required: the project converts everything with Python packages.
These are detected only so the panel can *show* them and so exotic inputs (a
``.lit`` file, for instance) can still be handled when Calibre happens to be
installed.
"""

from __future__ import annotations

import logging
import shutil

logger = logging.getLogger(__name__)


def find_optional_tools(settings) -> dict[str, str | None]:
    """Names of the optional external tools that are present (never fetched)."""
    tools = {
        "Calibre": shutil.which("ebook-convert")
        or (settings.calibre_ebook_convert or None),
        "ImageMagick": shutil.which("magick"),
        "Ghostscript": shutil.which("gswin64c")
        or shutil.which("gswin32c")
        or shutil.which("gs"),
        "FFmpeg": shutil.which("ffmpeg") or (settings.ffmpeg_bin or None),
        "pdftoppm": shutil.which("pdftoppm"),
        "unrar": shutil.which("unrar") or shutil.which("unar"),
        "7z": shutil.which("7z") or shutil.which("7za") or shutil.which("7zz"),
    }
    return {name: path for name, path in tools.items() if path}
