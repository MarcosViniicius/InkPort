"""OPDS 1.2 and 2.0 catalog endpoints."""

from app.opds import urls
from app.opds.v1 import router as v1_router
from app.opds.v2 import router as v2_router

__all__ = ["urls", "v1_router", "v2_router"]
