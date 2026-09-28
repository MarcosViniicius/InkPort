"""REST API for uploads and server-side folder imports."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.api import serializers
from app.api.deps import require_api
from app.config import get_settings
from app.database.base import get_session
from app.database.models import SourceKind
from app.library.conversions import enqueue_conversion
from app.library.detect import detect
from app.library.importer import import_file
from app.library.scanner import scan_directory
from app.metadata.extractor import extract_metadata
from app.storage.paths import safe_filename

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/imports", tags=["API: importação"], dependencies=[Depends(require_api)])

CHUNK = 1024 * 1024


@router.post("/upload")
async def upload_files(
    files: list[UploadFile] = File(...),
    category: str | None = Form(None),
    convert: bool = Form(False),
    target_format: str = Form("auto"),
    device_profile: str = Form("generic_epub"),
    keep_original: bool = Form(True),
    session: Session = Depends(get_session),
) -> dict:
    settings = get_settings()
    settings.inbox_dir.mkdir(parents=True, exist_ok=True)
    max_bytes = settings.max_upload_mb * 1024 * 1024

    results = []
    for upload in files:
        name = safe_filename(upload.filename or "upload.bin")
        target = settings.inbox_dir / name
        counter = 1
        while target.exists():
            target = settings.inbox_dir / f"{Path(name).stem} ({counter}){Path(name).suffix}"
            counter += 1

        size = 0
        too_large = False
        try:
            with target.open("wb") as handle:
                while chunk := await upload.read(CHUNK):
                    size += len(chunk)
                    if size > max_bytes:
                        too_large = True
                        break
                    handle.write(chunk)
        finally:
            await upload.close()

        if too_large:
            target.unlink(missing_ok=True)
            results.append(
                {
                    "filename": upload.filename,
                    "status": "too-large",
                    "message": "Excede o limite de upload",
                }
            )
            continue

        try:
            outcome = import_file(
                session, target, category=category,
                source=SourceKind.UPLOAD.value, move=True,
            )
            entry = {
                "filename": upload.filename,
                "status": outcome.status,
                "message": outcome.message,
                "book": serializers.book_summary(outcome.book) if outcome.book else None,
            }
            if convert and outcome.status == "imported" and outcome.book is not None:
                job = enqueue_conversion(
                    session,
                    outcome.book,
                    target_format=target_format,
                    device_profile=device_profile,
                    keep_original=keep_original,
                )
                entry["conversion"] = {"job_id": job.id, "target_format": job.target_format}
            results.append(entry)
        except Exception as exc:  # noqa: BLE001
            logger.exception("upload failed", extra={"filename": upload.filename})
            target.unlink(missing_ok=True)
            results.append({"filename": upload.filename, "status": "error", "message": str(exc)})

    imported = sum(1 for r in results if r["status"] == "imported")
    return {"results": results, "imported": imported, "total": len(results)}


@router.post("/scan")
def scan_path(
    path: str = Form(...),
    category: str | None = Form(None),
    recursive: bool = Form(True),
    move: bool = Form(False),
    session: Session = Depends(get_session),
) -> dict:
    root = Path(path).expanduser()
    if not root.exists():
        raise HTTPException(status_code=404, detail="Caminho não encontrado no servidor")
    if not root.is_dir():
        outcome = import_file(session, root, category=category, move=move)
        return {"imported": int(outcome.status == "imported"), "results": [outcome.message]}

    report = scan_directory(session, root, recursive=recursive, category=category, move=move)
    return {
        "scanned": report.scanned,
        "imported": report.imported,
        "duplicates": report.duplicates,
        "skipped": report.skipped,
        "errors": report.errors,
        "outcomes": [
            {"status": o.status, "message": o.message, "book_id": o.book.id if o.book else None}
            for o in report.outcomes
        ],
    }


@router.post("/inspect")
def inspect_path(path: str = Form(...)) -> dict:
    """Detect a server-side file and report its compatible conversions."""
    from app.converters.catalog import targets_for
    from app.devices.registry import get_profile

    target = Path(path).expanduser()
    if not target.exists():
        raise HTTPException(status_code=404, detail="Arquivo não encontrado")
    detection = detect(target)
    metadata = extract_metadata(target, detection)
    targets = targets_for(detection, get_profile(None))
    return {
        "path": str(target),
        "detection": {
            "format": detection.format,
            "content_type": detection.content_type,
            "media_type": detection.media_type,
            "page_count": detection.page_count,
            "note": detection.note,
            "image_only": detection.image_only,
        },
        "metadata": metadata.as_dict(),
        "targets": targets,
    }
