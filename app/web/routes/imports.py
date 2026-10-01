"""Web panel: uploads and server-side imports.

Import and conversion are offered together: the form lets the user pick a
device profile and a target format, and every imported file is queued for
conversion right away. The compatible targets for each file are shown in the
result list, so nothing has to be looked up on the book page afterwards.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database.base import get_session
from app.database.models import SourceKind
from app.devices.registry import all_profiles, get_profile
from app.library import repository
from app.library.conversions import compatible_targets, enqueue_conversion
from app.library.detect import detect, is_supported
from app.library.importer import STATUS_IMPORTED, ImportOutcome, import_file
from app.library.scanner import scan_directory
from app.rss.downloader import Downloader, DownloadError
from app.security.auth import require_panel
from app.storage.paths import human_size, safe_filename
from app.web.templating import render

if TYPE_CHECKING:
    from app.converters.webpage.metadata import PageMetadata

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/import", dependencies=[Depends(require_panel)], tags=["painel"])
CHUNK = 1024 * 1024

OUTPUT_FORMATS = ["auto", "epub", "cbz", "pdf", "mobi", "azw3", "kepub"]


@router.get("")
def import_page(request: Request, session: Session = Depends(get_session)):
    context = _base_context(session)
    # After a scan without category we redirect back with ?err=…&category=…;
    # keep the typed value so the user does not lose it.
    echoed = (request.query_params.get("category") or "").strip()
    if echoed:
        context["category"] = echoed
    return render(request, "import.html", context)


@router.post("/upload")
async def upload(
    request: Request,
    files: list[UploadFile] = File(default=[]),
    category: str = Form(""),
    convert: str = Form(""),
    target_format: str = Form("auto"),
    device_profile: str = Form("generic_epub"),
    keep_original: str = Form(""),
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
                "category": category.strip(),
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
                    "category": category.strip(),
                    "target_format": target_format, "device_profile": device_profile,
                    "keep_original": bool(keep_original)})
    return render(request, "import.html", context)


@router.post("/text")
def import_from_text(
    request: Request,
    title: str = Form(""),
    author: str = Form(""),
    text: str = Form(""),
    category: str = Form(""),
    convert: str = Form(""),
    target_format: str = Form("auto"),
    device_profile: str = Form("generic_epub"),
    keep_original: str = Form(""),
    session: Session = Depends(get_session),
):
    """Importa um texto colado no formulário como livro TXT, já convertendo.

    O texto é gravado como um arquivo ``.txt`` no inbox e segue o mesmo caminho
    do upload (detecção, dedup por hash, capa, fila de conversão).
    """
    name = (title or "").strip()
    body = (text or "").strip()
    eco = {
        "text_title": name,
        "text_author": (author or "").strip(),
        "text_body": text or "",
        "category": category.strip(),
        "target_format": target_format,
        "device_profile": device_profile,
        "keep_original": bool(keep_original),
        "convert": bool(convert),
    }
    if not name:
        return _page_error(request, session, "Informe um título para o texto.", eco)
    if not body:
        return _page_error(request, session, "Cole o texto antes de importar.", eco)
    if not category.strip():
        return _page_error(
            request,
            session,
            "Escolha uma categoria (ou digite uma nova) antes de importar.",
            eco,
        )

    settings = get_settings()
    settings.inbox_dir.mkdir(parents=True, exist_ok=True)
    max_bytes = settings.max_upload_mb * 1024 * 1024
    payload = body.encode("utf-8")
    if len(payload) > max_bytes:
        return _page_error(request, session, "O texto passa do limite de upload.", eco)

    base = safe_filename(name) or "texto"
    target = settings.inbox_dir / f"{base}.txt"
    counter = 1
    while target.exists():
        target = settings.inbox_dir / f"{base} ({counter}).txt"
        counter += 1
    try:
        target.write_bytes(payload)
    except OSError:
        logger.exception("text import failed to stage file")
        return _page_error(request, session, "Não foi possível gravar o texto.", eco)

    size = target.stat().st_size
    detection = detect(target)
    outcome = import_file(
        session,
        target,
        category=category.strip(),
        source=SourceKind.UPLOAD.value,
        move=True,
        title_override=name,
    )
    if outcome.status == STATUS_IMPORTED and outcome.book is not None:
        wanted = (author or "").strip()
        if wanted and not outcome.book.author:
            outcome.book.author = wanted
            session.commit()

    item = _result(
        session, target.name, outcome, detection, size,
        convert, target_format, device_profile, keep_original,
    )
    context = _base_context(session)
    context.update(
        {
            "results": [item],
            "category": category.strip(),
            "convert": bool(convert),
            "target_format": target_format,
            "device_profile": device_profile,
            "keep_original": bool(keep_original),
        }
    )
    return render(request, "import.html", context)


@router.post("/url")
def import_from_url(
    request: Request,
    url: str = Form(""),
    category: str = Form(""),
    convert: str = Form(""),
    target_format: str = Form("auto"),
    device_profile: str = Form("generic_epub"),
    keep_original: str = Form(""),
    session: Session = Depends(get_session),
):
    """Baixa uma página da web e a importa como livro, já convertendo.

    Guardar o endereço em ``source_url`` é o que faz o conversor de páginas
    resolver imagens e links relativos; sem isso a página viraria um EPUB sem
    imagens. Vale para qualquer endereço http(s) — é o mesmo limite de confiança
    dos feeds, que também são buscados pelo servidor.
    """
    endereco = url.strip()
    eco = {
        "url": endereco,
        "category": category.strip(),
        "target_format": target_format,
        "device_profile": device_profile,
        "keep_original": bool(keep_original),
        "convert": bool(convert),
    }
    if not endereco:
        return _page_error(request, session, "Informe o endereço da página.", eco)

    parsed = urlparse(endereco)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return _page_error(
            request, session, "O endereço precisa começar com http:// ou https://", eco
        )
    if not category.strip():
        return _page_error(
            request,
            session,
            "Escolha uma categoria (ou digite uma nova) antes de importar.",
            eco,
        )

    settings = get_settings()
    settings.inbox_dir.mkdir(parents=True, exist_ok=True)
    try:
        with Downloader() as downloader:
            downloaded = downloader.download(endereco, settings.inbox_dir)
    except DownloadError as exc:
        return _page_error(request, session, str(exc), eco)
    except Exception:  # noqa: BLE001 - a bad address must never answer 500
        logger.exception("url import failed", extra={"url": endereco})
        return _page_error(request, session, "Não foi possível baixar este endereço.", eco)

    target = downloaded.path
    if not is_supported(target):
        target.unlink(missing_ok=True)
        tipo = downloaded.content_type or "tipo desconhecido"
        return _page_error(
            request, session, f"O endereço devolveu algo que não sei importar ({tipo}).", eco
        )

    size = target.stat().st_size
    detection = detect(target)
    # O título da página vale mais que o nome do arquivo baixado ("artigo").
    page = _read_page_metadata(target, endereco)
    outcome = import_file(
        session,
        target,
        category=category.strip(),
        source=SourceKind.DOWNLOAD.value,
        source_url=endereco,
        move=True,
        title_override=page.title or None,
    )
    if outcome.status != STATUS_IMPORTED:
        # Nada importado: o que baixamos foi só uma cópia de trabalho.
        target.unlink(missing_ok=True)
    elif outcome.book is not None and page.author and not outcome.book.author:
        outcome.book.author = page.author
        session.commit()

    item = _result(
        session, target.name, outcome, detection, size,
        convert, target_format, device_profile, keep_original,
    )
    context = _base_context(session)
    context.update(
        {
            "results": [item],
            "category": category.strip(),
            "convert": bool(convert),
            "target_format": target_format,
            "device_profile": device_profile,
            "keep_original": bool(keep_original),
        }
    )
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
    keep_original: str = Form(""),
    session: Session = Depends(get_session),
):
    root = Path(path).expanduser()
    if not root.exists():
        raise HTTPException(status_code=404, detail="Caminho não encontrado")
    if not category.strip():
        from urllib.parse import quote

        return RedirectResponse(
            f"/import?err={quote('Escolha uma categoria (ou digite uma nova) antes de varrer a pasta.')}"
            f"&category={quote(category.strip())}",
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
                    "category": category.strip(),
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
        "category": "",
        "profiles": all_profiles(session),
        "output_formats": OUTPUT_FORMATS,
        "max_upload_mb": settings.max_upload_mb,
        "inbox_dir": str(settings.inbox_dir),
        "device_profile": "generic_epub",
        "target_format": "auto",
        "keep_original": False,
    }


def _page_error(request: Request, session: Session, message: str, eco: dict):
    """Re-render the import page with a readable error (no raw 400 body).

    ``eco`` devolve o que o usuário já tinha escolhido, para ele corrigir o
    endereço sem perder categoria, formato e dispositivo.
    """
    context = _base_context(session)
    context.update(eco)
    context["err"] = message
    return render(request, "import.html", context, status_code=400)


def _read_page_metadata(path: Path, page_url: str) -> PageMetadata:
    """Título/autor/descrição declarados pela própria página.

    Reusa o leitor de metadados do conversor de páginas — o mesmo que o EPUB usa.
    Nunca lança: um HTML estranho não pode impedir a importação.
    """
    from app.converters.webpage.dom import parse
    from app.converters.webpage.metadata import PageMetadata, get_metadata

    try:
        return get_metadata(parse(path.read_bytes()), page_url)
    except Exception:  # noqa: BLE001 - página esquisita ainda vira livro
        logger.debug("page metadata failed", extra={"url": page_url})
        return PageMetadata()


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
