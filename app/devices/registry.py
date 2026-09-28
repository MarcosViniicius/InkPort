"""Device profile registry: built-ins merged with user-defined profiles."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.devices.builtin import BUILTIN_PROFILES, DEFAULT_PROFILE_SLUG
from app.devices.profile import DeviceProfile

logger = logging.getLogger(__name__)


def custom_profiles(session: Session) -> dict[str, DeviceProfile]:
    """User-defined profiles only (built-ins come from code, never the DB)."""
    from app.database.models import DeviceProfileModel

    rows = session.scalars(
        select(DeviceProfileModel).where(DeviceProfileModel.builtin.is_(False))
    ).all()
    result: dict[str, DeviceProfile] = {}
    for row in rows:
        try:
            result[row.slug] = DeviceProfile.from_dict(row.spec)
        except Exception:  # pragma: no cover - bad data must not break the app
            logger.warning("invalid stored device profile", extra={"slug": row.slug})
    return result


def all_profiles(session: Session | None = None) -> dict[str, DeviceProfile]:
    profiles = dict(BUILTIN_PROFILES)
    if session is not None:
        profiles.update(custom_profiles(session))
    return profiles


def get_profile(slug: str | None, session: Session | None = None) -> DeviceProfile:
    profiles = all_profiles(session)
    if slug and slug in profiles:
        return profiles[slug]
    return profiles[DEFAULT_PROFILE_SLUG]


def save_custom_profile(session: Session, profile: DeviceProfile) -> None:
    from app.database.models import DeviceProfileModel

    row = session.scalar(
        select(DeviceProfileModel).where(DeviceProfileModel.slug == profile.slug)
    )
    if row is None:
        row = DeviceProfileModel(slug=profile.slug, name=profile.name, builtin=False)
        session.add(row)
    row.name = profile.name
    row.spec = profile.to_dict()
    session.commit()


def delete_custom_profile(session: Session, slug: str) -> bool:
    from app.database.models import DeviceProfileModel

    row = session.scalar(
        select(DeviceProfileModel).where(DeviceProfileModel.slug == slug)
    )
    if row is None or row.builtin:
        return False
    session.delete(row)
    session.commit()
    return True


def seed_builtin_profiles(session: Session) -> None:
    """Sync built-ins into the DB.

    Code is the source of truth for built-in profiles: their stored spec is
    refreshed on every start so corrections (screen size, quality...) actually
    take effect. User-created profiles (``builtin=False``) are never touched.
    """
    from app.database.models import DeviceProfileModel

    rows = {
        row.slug: row
        for row in session.scalars(select(DeviceProfileModel)).all()
    }
    for slug, profile in BUILTIN_PROFILES.items():
        row = rows.get(slug)
        if row is None:
            session.add(
                DeviceProfileModel(
                    slug=slug, name=profile.name, spec=profile.to_dict(), builtin=True
                )
            )
        elif row.builtin:
            row.name = profile.name
            row.spec = profile.to_dict()
    session.commit()
