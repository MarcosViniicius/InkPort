"""Request/response schemas for the internal REST API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class BookUpdate(BaseModel):
    title: str | None = None
    sort_title: str | None = None
    author: str | None = None
    series: str | None = None
    series_index: float | None = None
    publisher: str | None = None
    language: str | None = None
    description: str | None = None
    isbn: str | None = None
    published: str | None = None
    reading_direction: str | None = None
    age_rating: str | None = None
    content_type: str | None = None
    category: str | None = None
    tags: list[str] | None = None
    rename_file: bool = False


class BulkDelete(BaseModel):
    book_ids: list[str]
    delete_files: bool = True


class ConversionCreate(BaseModel):
    book_ids: list[str] = Field(min_length=1)
    target_format: str = "auto"
    device_profile: str = "generic_epub"
    keep_original: bool = True
    options: dict = Field(default_factory=dict)


class DeviceProfilePayload(BaseModel):
    slug: str
    name: str
    spec: dict = Field(default_factory=dict)


class FeedPayload(BaseModel):
    name: str
    url: str
    category_id: int | None = None
    interval_minutes: int = Field(default=360, ge=5)
    active: bool = True
    output_format: str = "epub"
    device_profile: str = "generic_epub"
    destination_folder: str = ""
    keep_original: bool = False
    max_items_per_run: int = Field(default=20, ge=1, le=200)


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=6)
    username: str | None = None
