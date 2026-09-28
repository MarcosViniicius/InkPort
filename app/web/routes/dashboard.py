"""Web panel: dashboard."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.config import get_settings
from app.converters.tools import detect_toolchain
from app.database.base import get_session
from app.library import repository
from app.security.auth import require_panel
from app.storage.usage import library_usage
from app.web.templating import render
from app.workers import queue

router = APIRouter(dependencies=[Depends(require_panel)], tags=["painel"])


@router.get("/")
def dashboard(request: Request, session: Session = Depends(get_session)):
    settings = get_settings()
    return render(
        request,
        "dashboard.html",
        {
            "active": "dashboard",
            "recent": repository.recent(session, limit=12),
            "stats": {
                "books": sum(count for _, count in repository.formats(session)),
                "authors": len(repository.authors(session)),
                "series": len(repository.series(session)),
                "categories": len(repository.categories(session)),
            },
            "content_types": repository.content_type_counts(session),
            "formats": repository.formats(session),
            "usage": library_usage(),
            "queue": queue.counts_by_status(session),
            "tools": detect_toolchain().as_dict(),
            "max_upload_mb": settings.max_upload_mb,
        },
    )
