"""Web panel: download history and file management."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.database.base import get_session
from app.database.models import Book, FileRecord, FileState
from app.downloads import store as downloads
from app.library import service
from app.security.auth import require_panel
from app.web.templating import render

router = APIRouter(prefix="/downloads", dependencies=[Depends(require_panel)], tags=["painel"])

#: Order the filter tabs appear in.
STATE_ORDER = (
    FileState.AVAILABLE.value,
    FileState.DOWNLOADED.value,
    FileState.BLOCKED.value,
    FileState.DELETED.value,
)


def _as_int(value: str | None) -> int | None:
    text = (value or "").strip()
    return int(text) if text.isdigit() else None


def _back(path: str, message: str, *, ok: bool = False) -> RedirectResponse:
    """Return to the downloads screen (only internal paths, no open redirect)."""
    destino = path if path.startswith("/downloads") and "//" not in path else "/downloads"
    key = "ok" if ok else "err"
    separator = "&" if "?" in destino else "?"
    return RedirectResponse(f"{destino}{separator}{key}={quote(message)}", status_code=303)


@router.get("")
def downloads_page(
    request: Request,
    state: str | None = None,
    q: str | None = None,
    page: str | None = None,
    session: Session = Depends(get_session),
):
    selected = state if state in set(STATE_ORDER) else None
    data = downloads.list_records(
        session, state=selected, query=q, page=_as_int(page) or 1, per_page=50
    )
    return render(
        request,
        "downloads.html",
        {
            "active": "downloads",
            "overview": downloads.overview(session),
            "data": data,
            "events": downloads.recent_events(session, limit=30),
            "filters": {"state": selected or "", "q": q or ""},
            "state_order": STATE_ORDER,
        },
    )


@router.post("/{record_id}/state")
def change_state(
    record_id: str,
    action: str = Form(...),
    back: str = Form("/downloads"),
    session: Session = Depends(get_session),
):
    record = session.get(FileRecord, record_id)
    if record is None:
        return _back(back, "Registro não encontrado.")
    if action == "block":
        downloads.set_state_by_id(session, record_id, FileState.BLOCKED.value)
        message = "Bloqueado: o arquivo deixa de ser oferecido no catálogo OPDS."
    elif action == "unblock":
        state = (
            FileState.DOWNLOADED.value
            if record.download_count
            else FileState.AVAILABLE.value
        )
        downloads.set_state_by_id(session, record_id, state)
        message = "Liberado: o arquivo volta a ser oferecido."
    elif action == "downloaded":
        downloads.set_state_by_id(session, record_id, FileState.DOWNLOADED.value)
        message = "Arquivo marcado como já baixado."
    else:
        return _back(back, "Ação desconhecida.")
    return _back(back, message, ok=True)


@router.post("/{record_id}/delete")
def delete_file(
    record_id: str,
    back: str = Form("/downloads"),
    session: Session = Depends(get_session),
):
    """Delete the file from disk; the tracking history is kept (state=deleted)."""
    record = session.get(FileRecord, record_id)
    if record is None:
        return _back(back, "Registro não encontrado.")
    book = session.get(Book, record.book_id) if record.book_id else None
    if book is not None:
        service.delete_books(session, [book], delete_files=True)
    record.book_id = None
    record.state = FileState.DELETED.value
    session.commit()
    return _back(back, "Arquivo apagado do disco. O histórico foi mantido.", ok=True)


@router.post("/{record_id}/forget")
def forget_record(
    record_id: str,
    back: str = Form("/downloads"),
    session: Session = Depends(get_session),
):
    """Remove the record and its events from the history."""
    if not downloads.forget(session, record_id):
        return _back(back, "Registro não encontrado.")
    return _back(back, "Registro removido do histórico.", ok=True)


__all__ = ["router"]
