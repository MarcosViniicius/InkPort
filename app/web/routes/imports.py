"""Web panel: uploads and server-side imports.

Import and conversion are offered together: the form lets the user pick a
device profile and a target format, and every imported file is queued for
conversion right away. The compatible targets for each file are shown in the
result list, so nothing has to be looked up on the book page afterwards.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database.base import get_session
from app.database.models import SourceKind
from app.devices.registry import all_profiles, get_profile
from app.library import repository
from app.library.conversions import compatible_targets, enqueue_conversion
from app.library.detect import detect
from app.library.importer import ImportOutcome, import_file
from app.library.scanner import scan_directory
from app.security.auth import require_panel
from app.storage.paths import human_size, safe_filename
from app.web.templating import render

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/import", dependencies=[Depends(require_panel)], tags=["painel"])
CHUNK = 1024 * 1024

OUTPUT_FORMATS = ["auto", "epub", "cbz", "pdf", "mobi", "azw3", "kepub"]


@router.get("")
def import_page(request: Request, session: Session = Depends(get_session)):
    return render(request, "import.html", _base_context(session))


@router.post("/upload")
async def upload(
    request: Request,
    files: list[UploadFile] = File(default=[]),
    category: str = Form(""),
    convert: str = Form(""),
    target_format: str = Form("auto"),
    device_profile: str = Form("generic_epub"),
    keep_original: str = Form("on"),
    session: Session = Depends(get_session),
):
    settings = get_settings()
    settings.inbox_dir.mkdir(parents=True, exist_ok=True)
    max_bytes = settings.max_upload_mb * 1024 * 1024
    results: list[dict] = []

    # Every import belongs to a category: it is how the library and the OPDS
    # catalog stay organised. Either pick an existing one or type a new name.
    if not category.strip():
        context = _base_context(session)
        context.update(
            {
                "err": "Escolha uma categoria (ou digite uma nova) antes de importar.",
                "target_format": target_format,
                "device_profile": device_profile,
                "keep_original": bool(keep_original),
                "convert": bool(convert),
            }
        )
        return render(request, "import.html", context, status_code=400)

    for upload_file in files:
        if not upload_file.filename:
            continue
        name = safe_filename(upload_file.filename)
        target = settings.inbox_dir / name
        counter = 1
        while target.exists():
            target = settings.inbox_dir / f"{Path(name).stem} ({counter}){Path(name).suffix}"
            counter += 1

        size = 0
        too_large = False
        try:
            with target.open("wb") as handle:
                while chunk := await upload_file.read(CHUNK):
                    size += len(chunk)
                    if size > max_bytes:
                        too_large = True
                        break
                    handle.write(chunk)
        finally:
            await upload_file.close()

        if too_large:
            target.unlink(missing_ok=True)
            results.append({"name": name, "status": "too-large", "message": "Acima do limite de upload"})
            continue

        detection = detect(target)
        outcome = import_file(
            session, target, category=category or None, source=SourceKind.UPLOAD.value, move=True
        )
        results.append(
            _result(session, name, outcome, detection, size, convert, target_format, device_profile, keep_original)
        )

    context = _base_context(session)
    context.update({"results": results, "convert": bool(convert),
                    "target_format": target_format, "device_profile": device_profile,
                    "keep_original": bool(keep_original)})
    return render(request, "import.html", context)


@router.post("/scan")
def scan(
    request: Request,
    path: str = Form(...),
    category: str = Form(""),
    recursive: str = Form(""),
    move: str = Form(""),
    convert: str = Form(""),
    target_format: str = Form("auto"),
    device_profile: str = Form("generic_epub"),
    keep_original: str = Form("on"),
    session: Session = Depends(get_session),
):
    root = Path(path).expanduser()
    if not root.exists():
        raise HTTPException(status_code=404, detail="Caminho não encontrado")
    if not category.strip():
        from urllib.parse import quote

        return RedirectResponse(
            f"/import?err={quote('Escolha uma categoria (ou digite uma nova) antes de varrer a pasta.')}",
            status_code=303,
        )
    report = scan_directory(
        session, root, recursive=bool(recursive), category=category or None, move=bool(move)
    )

    queued = 0
    if convert:
        for outcome in report.outcomes:
            if outcome.status == "imported" and outcome.book is not None:
                enqueue_conversion(
                    session, outcome.book,
                    target_format=target_format, device_profile=device_profile,
                    keep_original=bool(keep_original),
                )
                queued += 1

    context = _base_context(session)
    context.update({"report": report, "queued": queued, "convert": bool(convert),
                    "target_format": target_format, "device_profile": device_profile,
                    "keep_original": bool(keep_original)})
    return render(request, "import.html", context)


@router.post("/inspect")
def inspect(
    request: Request,
    path: str = Form(...),
    device_profile: str = Form("generic_epub"),
    session: Session = Depends(get_session),
):
    target = Path(path).expanduser()
    if not target.exists():
        raise HTTPException(status_code=404, detail="Arquivo não encontrado")
    detection = detect(target)
    from app.metadata.extractor import extract_metadata

    context = _base_context(session)
    context.update(
        {
            "inspection": {"path": str(target), "detection": detection,
                           "metadata": extract_metadata(target, detection)},
            "targets": compatible_targets_from_detection(detection, device_profile, session),
            "device_profile": device_profile,
        }
    )
    return render(request, "import.html", context)


# --- helpers --------------------------------------------------------------
def compatible_targets_from_detection(detection, device_profile: str, session: Session) -> list[dict]:
    from app.converters.catalog import targets_for

    return targets_for(detection, get_profile(device_profile, session))


def _base_context(session: Session) -> dict:
    settings = get_settings()
    return {
        "active": "import",
        "categories": repository.categories(session),
        "profiles": all_profiles(session),
        "output_formats": OUTPUT_FORMATS,
        "max_upload_mb": settings.max_upload_mb,
        "inbox_dir": str(settings.inbox_dir),
        "device_profile": "generic_epub",
        "target_format": "auto",
        "keep_original": True,
    }


def _result(
    session: Session,
    name: str,
    outcome: ImportOutcome,
    detection,
    size: int,
    convert: str,
    target_format: str,
    device_profile: str,
    keep_original: str,
) -> dict:
    item: dict = {
        "name": name,
        "status": outcome.status,
        "message": outcome.message,
        "detection": detection,
        "size": human_size(size),
        "book_id": outcome.book.id if outcome.book else None,
    }

    if outcome.status == "imported" and outcome.book is not None:
        item["targets"] = compatible_targets(session, outcome.book, device_profile)
        if convert:
            job = enqueue_conversion(
                session, outcome.book,
                target_format=target_format, device_profile=device_profile,
                keep_original=bool(keep_original),
            )
            item["job_target"] = job.target_format
            item["queued"] = True

    return item
