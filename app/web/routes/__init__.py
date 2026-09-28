"""Web panel routes."""

from fastapi import APIRouter

from app.web.routes.auth_routes import router as auth_router
from app.web.routes.conversions import router as conversions_router
from app.web.routes.dashboard import router as dashboard_router
from app.web.routes.devices import router as devices_router
from app.web.routes.feeds import router as feeds_router
from app.web.routes.imports import router as imports_router
from app.web.routes.library import router as library_router
from app.web.routes.reader import router as reader_router
from app.web.routes.settings_routes import router as settings_router


def build_web_router() -> APIRouter:
    router = APIRouter()
    router.include_router(auth_router)
    router.include_router(dashboard_router)
    router.include_router(library_router)
    router.include_router(reader_router)
    router.include_router(imports_router)
    router.include_router(conversions_router)
    router.include_router(devices_router)
    router.include_router(feeds_router)
    router.include_router(settings_router)
    return router


__all__ = ["build_web_router"]
