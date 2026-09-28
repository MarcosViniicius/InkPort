"""The metadata value object passed around the whole pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class BookMetadata:
    title: str = ""
    author: str | None = None
    series: str | None = None
    series_index: float | None = None
    publisher: str | None = None
    language: str | None = None
    description: str | None = None
    isbn: str | None = None
    published: str | None = None
    page_count: int | None = None
    reading_direction: str | None = None
    age_rating: str | None = None
    content_type: str = "unknown"
    media_type: str | None = None
    format: str = "bin"
    tags: list[str] = field(default_factory=list)
    note: str | None = None

    def as_dict(self) -> dict:
        return {
            "title": self.title,
            "author": self.author,
            "series": self.series,
            "series_index": self.series_index,
            "publisher": self.publisher,
            "language": self.language,
            "description": self.description,
            "isbn": self.isbn,
            "published": self.published,
            "page_count": self.page_count,
            "reading_direction": self.reading_direction,
            "age_rating": self.age_rating,
            "tags": list(self.tags),
        }
