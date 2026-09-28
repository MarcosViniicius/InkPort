"""Conversion strategies, one class per output family."""

from app.converters.base import BaseConverter
from app.converters.strategies.compress_image import CompressImageConverter
from app.converters.strategies.ebook_to_epub import (
    DocxToEpubConverter,
    Fb2ToEpubConverter,
    KindleToEpubConverter,
    TextToEpubConverter,
)
from app.converters.strategies.epub_to_ebook import (
    EpubToDocxConverter,
    EpubToFb2Converter,
    EpubToKepubConverter,
    EpubToTextConverter,
)
from app.converters.strategies.html_to_epub import HtmlToEpubConverter
from app.converters.strategies.images_to_cbz import ImagesToCbzConverter
from app.converters.strategies.images_to_epub import ImagesToEpubConverter
from app.converters.strategies.images_to_pdf import ImagesToPdfConverter
from app.converters.strategies.native_bridge import (
    EpubToKindleConverter,
    EpubToPdfConverter,
    PdfToEpubTextConverter,
)


def page_converters() -> list[BaseConverter]:
    return [
        ImagesToEpubConverter(),
        ImagesToCbzConverter(),
        ImagesToPdfConverter(),
        CompressImageConverter(),
    ]


def ebook_converters() -> list[BaseConverter]:
    """Pure-Python ebook conversions (the ones that used to need Calibre)."""
    return [
        EpubToKindleConverter(),
        EpubToKepubConverter(),
        EpubToPdfConverter(),
        EpubToDocxConverter(),
        EpubToFb2Converter(),
        EpubToTextConverter(),
        PdfToEpubTextConverter(),
        TextToEpubConverter(),
        Fb2ToEpubConverter(),
        DocxToEpubConverter(),
        KindleToEpubConverter(),
    ]


def strategy_converters() -> list[BaseConverter]:
    """Every converter implemented in this package (no external tools)."""
    return [HtmlToEpubConverter(), *ebook_converters(), *page_converters()]


__all__ = [
    "CompressImageConverter",
    "DocxToEpubConverter",
    "EpubToDocxConverter",
    "EpubToFb2Converter",
    "EpubToKindleConverter",
    "EpubToKepubConverter",
    "EpubToPdfConverter",
    "EpubToTextConverter",
    "Fb2ToEpubConverter",
    "HtmlToEpubConverter",
    "ImagesToCbzConverter",
    "ImagesToEpubConverter",
    "ImagesToPdfConverter",
    "KindleToEpubConverter",
    "PdfToEpubTextConverter",
    "TextToEpubConverter",
    "ebook_converters",
    "page_converters",
    "strategy_converters",
]
