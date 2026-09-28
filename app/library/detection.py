"""The result of inspecting a file: what it is and how we should treat it."""

from __future__ import annotations

from dataclasses import dataclass

from app.library import formats


@dataclass(slots=True)
class Detection:
    format: str                     # normalised, lowercase extension
    media_type: str                 # MIME
    content_type: str               # formats.CONTENT_*
    page_count: int | None = None
    note: str | None = None
    corrected: bool = False         # magic bytes disagreed with the extension
    image_only: bool = False        # scans / PDFs with no usable text layer

    @property
    def is_comic(self) -> bool:
        return self.content_type == formats.CONTENT_COMIC

    @property
    def is_ebook(self) -> bool:
        return self.content_type == formats.CONTENT_EBOOK

    @property
    def is_document(self) -> bool:
        return self.content_type == formats.CONTENT_DOCUMENT

    @property
    def is_image(self) -> bool:
        return self.content_type == formats.CONTENT_IMAGE

    @property
    def pages(self) -> bool:
        """True when the file is naturally a sequence of image pages."""
        return self.content_type in {
            formats.CONTENT_COMIC,
            formats.CONTENT_IMAGE,
        } or (self.is_document and self.image_only)
