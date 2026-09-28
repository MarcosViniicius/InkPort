"""OPDS 1.2 catalog (Atom), split by responsibility.

- ``navigation``  : feeds that list sub-catalogs (root, authors, categories...)
- ``acquisition`` : feeds that list books (recent, all, search, per facet)
- ``assets``      : a single book entry plus cover/thumbnail/download
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.opds.v1 import acquisition, assets, navigation
from app.security.auth import require_opds_auth

router = APIRouter(
    tags=["OPDS 1.2"],
    dependencies=[Depends(require_opds_auth)],
)
router.include_router(navigation.router, prefix="/opds")
router.include_router(acquisition.router, prefix="/opds")
router.include_router(assets.router, prefix="/opds")

__all__ = ["router"]
