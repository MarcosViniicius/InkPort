"""Converter registry: which strategies exist and which one wins a job."""

from __future__ import annotations

from app.converters.base import BaseConverter, ConversionRequest
from app.converters.calibre_converter import CalibreConverter
from app.converters.strategies import strategy_converters
from app.converters.strategies.epub_optimize import EpubOptimizerConverter


def all_converters() -> list[BaseConverter]:
    """Instantiate every converter, highest priority first.

    The Python strategies come first (they are always available); Calibre stays
    at the bottom as an optional last resort for exotic inputs, never required.
    """
    converters: list[BaseConverter] = [
        *strategy_converters(),
        CalibreConverter(),
        EpubOptimizerConverter(),
    ]
    return sorted(converters, key=lambda c: c.priority, reverse=True)


def select_converter(request: ConversionRequest) -> BaseConverter | None:
    for converter in all_converters():
        try:
            if converter.can_handle(request):
                return converter
        except Exception:  # a broken probe must not block other converters
            continue
    return None


def converter_names() -> list[str]:
    return [converter.name for converter in all_converters()]
