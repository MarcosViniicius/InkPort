"""Handler registry.

One explicit list, in priority order. Adding a new format means dropping a new
handler module next to the others and adding it here.
"""

from __future__ import annotations

from app.reader.audio import AudioHandler
from app.reader.base import ReaderHandler
from app.reader.calibre import CalibreHandler
from app.reader.comic import ComicHandler
from app.reader.document import DocumentHandler
from app.reader.epub import EpubHandler
from app.reader.fb2 import Fb2Handler
from app.reader.image import ImageHandler
from app.reader.markup import MarkupHandler
from app.reader.native_convert import NativeConvertHandler
from app.reader.pdf import PdfHandler
from app.reader.text import TextHandler

#: Priority order. Formats do not overlap, but the order documents intent.
_HANDLER_CLASSES: tuple[type[ReaderHandler], ...] = (
    EpubHandler,
    PdfHandler,
    ComicHandler,
    ImageHandler,
    TextHandler,
    Fb2Handler,
    MarkupHandler,
    NativeConvertHandler,
    CalibreHandler,
    AudioHandler,
    DocumentHandler,
)

_INSTANCES: tuple[ReaderHandler, ...] = tuple(cls() for cls in _HANDLER_CLASSES)

_FORMAT_MAP: dict[str, ReaderHandler] = {}
for _handler in _INSTANCES:
    for _fmt in _handler.formats:
        _FORMAT_MAP.setdefault(_fmt, _handler)
del _handler, _fmt


def handlers() -> tuple[ReaderHandler, ...]:
    return _INSTANCES


def handler_for_format(fmt: str | None) -> ReaderHandler | None:
    return _FORMAT_MAP.get((fmt or "").lower().lstrip("."))


def select_handler(book, detection) -> ReaderHandler | None:
    for handler in _INSTANCES:
        if handler.matches(book, detection):
            return handler
    return None


def supported_formats() -> frozenset[str]:
    return frozenset(_FORMAT_MAP)
