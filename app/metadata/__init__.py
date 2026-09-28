"""Metadata extraction and cover generation."""

from app.metadata.extractor import extract_metadata
from app.metadata.filenames import parse_filename
from app.metadata.model import BookMetadata

__all__ = ["BookMetadata", "extract_metadata", "parse_filename"]
