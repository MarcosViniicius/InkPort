"""Web panel: conversion queue and job control."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.api.serializers import job as job_to_dict
from app.database.base import get_session, session_scope
from app.database.models import Book, ConversionJob, JobStatus
from app.devices.registry import all_profiles
from app.library.conversions import enqueue_conversion, manga_text_options
from app.security.auth import require_panel
from app.web.templating import render
from app.workers import queue

router = APIRouter(prefix="/conversions", dependencies=[Depends(require_panel)], tags=["painel"])


@router.get("")
def conversions_page(request: Request, session: Session = Depends(get_session)):
    jobs = queue.list_jobs(session, limit=100)
    counts = queue.counts_by_status(session)
    # Show "Em andamento" first when there is work; otherwise show everything so
    # the page is not empty-looking.
    default_tab = "active" if (counts["pending"] + counts["running"]) else "all"
    return render(
        request,
        "conversions.html",
        {
            "active": "conversions",
            "jobs": jobs,
            "counts": counts,
            "default_tab": default_tab,
            "profiles": list(all_profiles(session).values()),
        },
    )


@router.get("/status")
def status(session: Session = Depends(get_session)) -> JSONResponse:
    jobs = queue.list_jobs(session, limit=100)
    return JSONResponse(
        {
            "counts": queue.counts_by_status(session),
            "jobs": [job_to_dict(job) for job in jobs],
        }
    )


@router.post("/create")
def create_jobs(
    book_ids: str = Form(...),
    target_format: str = Form("auto"),
    device_profile: str = Form("generic_epub"),
    keep_original: str = Form("on"),
    manga_text_mode: str = Form("off"),
    manga_enlarge_text: str | None = Form(None),
    session: Session = Depends(get_session),
):
    ids = [token.strip() for token in book_ids.replace(",", " ").split() if token.strip()]
    created = 0
    missing = 0
    mode = manga_text_mode
    if mode == "off" and manga_enlarge_text is not None:
        mode = "experimental"
    options = manga_text_options(mode)
    for book_id in ids:
        book = session.get(Book, book_id)
        if book is None:
            missing += 1
            continue
        enqueue_conversion(
            session,
            book,
            target_format=target_format,
            device_profile=device_profile,
            keep_original=bool(keep_original),
            options=dict(options),
        )
        created += 1
    if not created:
        return RedirectResponse(
            f"/conversions?err={quote('Nenhum livro encontrado com esses IDs.')}",
            status_code=303,
        )
    message = f"{created} conversão(ões) na fila."
    if missing:
        message += f" {missing} ID(s) não encontrado(s)."
    return RedirectResponse(f"/conversions?ok={quote(message)}", status_code=303)


@router.post("/clear")
def clear_finished():
    with session_scope() as session:
        removed = queue.clear_finished(session)
    detail = f"{removed} registro(s) removido(s)." if removed else "Nada para limpar."
    return RedirectResponse(f"/conversions?ok={quote(detail)}", status_code=303)


@router.post("/{job_id}/cancel")
def cancel(job_id: str, session: Session = Depends(get_session)):
    job = session.get(ConversionJob, job_id)
    if job is None:
        return RedirectResponse(
            f"/conversions?err={quote('Conversão não encontrada.')}", status_code=303
        )
    was_running = job.status == JobStatus.RUNNING.value
    if not queue.cancel(session, job_id):
        return RedirectResponse(
            f"/conversions?err={quote('Esta conversão já tinha terminado.')}",
            status_code=303,
        )
    message = (
        "Cancelamento pedido — a conversão em curso será interrompida e o "
        "resultado descartado."
        if was_running
        else "Conversão cancelada antes de começar."
    )
    return RedirectResponse(f"/conversions?ok={quote(message)}", status_code=303)


@router.post("/{job_id}/retry")
def retry(job_id: str, session: Session = Depends(get_session)):
    if not queue.retry(session, job_id):
        return RedirectResponse(
            f"/conversions?err={quote('Só é possível repetir conversões que falharam ou foram descartadas.')}",
            status_code=303,
        )
    return RedirectResponse(
        f"/conversions?ok={quote('Conversão reenviada para a fila.')}", status_code=303
    )
