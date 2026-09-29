"""Internal REST API (JSON), mounted under ``/api``."""

from fastapi import APIRouter

from app.api.books import router as books_router
from app.api.conversions import router as conversions_router
from app.api.devices import router as devices_router
from app.api.downloads import router as downloads_router
from app.api.feeds import router as feeds_router
from app.api.system import router as system_router
from app.api.uploads import router as uploads_router


def build_api_router() -> APIRouter:
    router = APIRouter()
    router.include_router(system_router)
    router.include_router(books_router)
    router.include_router(uploads_router)
    router.include_router(conversions_router)
    router.include_router(devices_router)
    router.include_router(feeds_router)
    router.include_router(downloads_router)
    return router


__all__ = ["build_api_router"]
