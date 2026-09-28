"""REST API for device profiles."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_api
from app.api.schemas import DeviceProfilePayload
from app.database.base import get_session
from app.database.models import DeviceProfileModel
from app.devices.profile import DeviceProfile
from app.devices.registry import (
    all_profiles,
    delete_custom_profile,
    save_custom_profile,
    seed_builtin_profiles,
)

router = APIRouter(prefix="/api/devices", tags=["API: dispositivos"], dependencies=[Depends(require_api)])


@router.get("")
def list_devices(session: Session = Depends(get_session)) -> dict:
    seed_builtin_profiles(session)
    builtin_slugs = {
        row.slug for row in session.scalars(select(DeviceProfileModel).where(DeviceProfileModel.builtin.is_(True)))
    }
    return {
        "items": [
            {"builtin": slug in builtin_slugs, **profile.to_dict()}
            for slug, profile in all_profiles(session).items()
        ]
    }


@router.post("")
def upsert_device(payload: DeviceProfilePayload, session: Session = Depends(get_session)) -> dict:
    spec = dict(payload.spec)
    spec.setdefault("slug", payload.slug)
    spec["name"] = payload.name
    profile = DeviceProfile.from_dict(spec)
    save_custom_profile(session, profile)
    return profile.to_dict()


@router.delete("/{slug}")
def delete_device(slug: str, session: Session = Depends(get_session)) -> dict:
    if not delete_custom_profile(session, slug):
        raise HTTPException(status_code=409, detail="Perfil embutido ou inexistente")
    return {"ok": True}
