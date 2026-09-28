"""REST API for system status, tools and maintenance."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import __version__
from app.api.deps import require_api
from app.api.schemas import PasswordChange
from app.config import get_settings
from app.converters.tools import detect_toolchain
from app.database.base import get_session
from app.database.models import Book, Feed
from app.library.repairs import run_repairs
from app.library.service import mark_missing
from app.opds import urls as opds_urls
from app.security import auth
from app.storage.temp import clean_temp_dir
from app.storage.usage import library_usage
from app.workers import queue

router = APIRouter(prefix="/api/system", tags=["API: sistema"], dependencies=[Depends(require_api)])


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "version": __version__}


@router.get("/tools")
def tools() -> dict:
    return detect_toolchain().as_dict()


@router.get("/stats")
def stats(session: Session = Depends(get_session)) -> dict:
    settings = get_settings()
    return {
        "version": __version__,
        "books": int(session.scalar(select(func.count(Book.id))) or 0),
        "feeds": int(session.scalar(select(func.count(Feed.id))) or 0),
        "library": library_usage(),
        "queue": queue.counts_by_status(session),
        "tools": detect_toolchain().as_dict(),
        "network": {
            "host": settings.host,
            "port": settings.port,
            "on_network": settings.on_network,
            "base_url": settings.base_url_clean,
            "access_urls": settings.access_urls,
        },
        "opds": {
            "v1": opds_urls.root(),
            "v2": opds_urls.v2_root(),
            "protected": bool(settings.opds_username),
        },
    }


@router.get("/settings")
def settings_info(session: Session = Depends(get_session)) -> dict:
    settings = get_settings()
    return {
        "app_name": settings.app_name,
        "host": settings.host,
        "port": settings.port,
        "base_url": settings.base_url_clean,
        "access_urls": settings.access_urls,
        "conversion_concurrency": settings.conversion_concurrency,
        "rss_worker_enabled": settings.rss_worker_enabled,
        "storage_limit_gb": settings.storage_limit_gb,
        "max_upload_mb": settings.max_upload_mb,
        "admin_username": auth.admin_username(session),
        "opds_protected": bool(settings.opds_username),
    }


@router.post("/password")
def change_password(payload: PasswordChange, session: Session = Depends(get_session)) -> dict:
    if not auth.verify_credentials(session, auth.admin_username(session), payload.current_password):
        raise HTTPException(status_code=403, detail="Senha atual incorreta")
    auth.change_password(session, payload.new_password, username=payload.username)
    return {"ok": True}


@router.post("/maintenance")
def run_maintenance(session: Session = Depends(get_session)) -> dict:
    removed = clean_temp_dir(max_age_seconds=0)
    resumed = queue.requeue_stale(session, older_than_seconds=1800)
    missing = mark_missing(session)
    repairs = run_repairs(session)
    return {
        "temp_removed": removed,
        "requeued": resumed,
        "missing_marked": missing,
        **repairs,
    }
