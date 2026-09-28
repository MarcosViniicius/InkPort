"""Built-in Web Reader: renders library formats in the browser.

The reader is modular: one handler per format family (``app/reader/handlers``),
selected by :mod:`app.reader.registry`. Adding a format means adding a module
and registering it -- nothing else in the panel has to change.
"""

from __future__ import annotations

from app.reader.base import (
    NotRenderableError,
    ReaderContext,
    ReaderError,
    ReaderHandler,
)
from app.reader.registry import handler_for_format, select_handler, supported_formats

__all__ = [
    "NotRenderableError",
    "ReaderContext",
    "ReaderError",
    "ReaderHandler",
    "handler_for_format",
    "select_handler",
    "supported_formats",
]
