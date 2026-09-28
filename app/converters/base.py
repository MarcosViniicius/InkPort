"""Converter interface shared by every conversion backend."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from app.devices.profile import DeviceProfile
from app.library.detect import Detection
from app.metadata.extractor import BookMetadata

ProgressCallback = Callable[[int, str], None]


class ConversionError(RuntimeError):
    """Raised when a conversion cannot be completed. Message is user-facing."""


@dataclass(slots=True)
class ConversionRequest:
    source: Path
    detection: Detection
    metadata: BookMetadata
    profile: DeviceProfile
    target_format: str
    workdir: Path
    options: dict = field(default_factory=dict)
    progress: ProgressCallback | None = None

    def report(self, percent: int, message: str = "") -> None:
        if self.progress:
            self.progress(max(0, min(100, percent)), message)


@dataclass(slots=True)
class ConversionResult:
    output_path: Path
    converter: str
    message: str = ""
    page_count: int | None = None
    cover_path: Path | None = None


class BaseConverter:
    """A converter is a named, orderable strategy.

    ``priority`` decides which converter wins when several can handle a job;
    higher wins. This is how "prefer Calibre for reflow, Pillow for images"
    is expressed without hard-coding the decision in the planner.
    """

    name: str = "base"
    priority: int = 0

    def can_handle(self, request: ConversionRequest) -> bool:  # pragma: no cover
        raise NotImplementedError

    def run(self, request: ConversionRequest) -> ConversionResult:  # pragma: no cover
        raise NotImplementedError

    # Convenience -----------------------------------------------------------
    def supports_pair(self, source_fmt: str, target_fmt: str) -> bool:
        return False
