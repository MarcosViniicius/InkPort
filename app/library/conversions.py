"""Conversion helpers used by the import flow, the API and the panel.

Centralises "which targets make sense for this file" and "enqueue a conversion",
so the same rules apply whether the user converts from the import page, the book
page or the REST API.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.converters.catalog import targets_for
from app.converters.planner import plan_conversion
from app.database.models import Book, ConversionJob
from app.devices.registry import get_profile
from app.library.detect import detect
from app.metadata.extractor import extract_metadata
from app.storage.paths import resolve_library_path
from app.workers import queue


def compatible_targets(
    session: Session, book: Book, device_slug: str | None = None
) -> list[dict]:
    """Conversion targets that make sense for this book + device."""
    path = resolve_library_path(book.file_path)
    detection = detect(path)
    profile = get_profile(device_slug or book.device_profile, session)
    return targets_for(detection, profile)


def plan_for_book(
    session: Session, book: Book, device_slug: str | None, options: dict | None = None
) -> tuple[str, dict]:
    """Return ``(target_format, options)`` for the automatic strategy."""
    options = dict(options or {})
    path = resolve_library_path(book.file_path)
    detection = detect(path)
    metadata = extract_metadata(path, detection)
    profile = get_profile(device_slug or book.device_profile, session)
    plan = plan_conversion(detection, metadata, profile)
    if plan.options.get("force_page_pipeline"):
        options["force_page_pipeline"] = True
    if plan.options.get("optimize"):
        options["optimize"] = True
    return plan.target_format, options


def manga_text_options(mode: str | bool) -> dict:
    """Options for the manga/comic text modes, with the configured limits.

    ``mode`` accepts the legacy boolean and the mode string: ``off``,
    ``experimental`` (no OCR), ``ocr`` (OCR-guided) or ``ocr_font`` (rewritten
    with the bundled font). The engine receives everything through the job
    options, so it never reads the settings itself (plain worker-thread data).
    """
    if isinstance(mode, bool):
        mode = "experimental" if mode else "off"
    mode = str(mode or "off").strip().lower()
    if mode in {"", "0", "false", "none", "off"}:
        return {"manga_text_mode": "off", "manga_enlarge_text": False}
    if mode not in {"experimental", "ocr", "ocr_font"}:
        mode = "experimental"

    from app.security import runtime

    return {
        "manga_text_mode": mode,
        "manga_enlarge_text": mode == "experimental",
        "manga_max_scale": runtime.get("manga_max_scale", 2.0),
        "manga_overflow": runtime.get("manga_overflow", 0.15),
        "manga_max_overflow_px": runtime.get("manga_max_overflow_px", 10),
        "manga_ocr_lang": runtime.get("manga_ocr_lang", "pt"),
        "manga_ocr_model": runtime.get("manga_ocr_model", "small"),
        "manga_ocr_min_score": runtime.get("manga_ocr_min_score", 0.5),
        "manga_font": runtime.get("manga_font", ""),
        "manga_font_min_score": runtime.get("manga_font_min_score", 0.75),
    }


def enqueue_conversion(
    session: Session,
    book: Book,
    *,
    target_format: str = "auto",
    device_profile: str = "generic_epub",
    keep_original: bool = True,
    options: dict | None = None,
) -> ConversionJob:
    """Queue a conversion, resolving ``auto`` to the planner's best target."""
    options = dict(options or {})
    target = target_format
    if target in {"", "auto"}:
        target, options = plan_for_book(session, book, device_profile, options)
    return queue.enqueue(
        session,
        book,
        target_format=target,
        device_profile=device_profile,
        options=options,
        keep_original=keep_original,
    )
