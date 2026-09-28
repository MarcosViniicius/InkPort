"""Settings-backed key/value access (used for credentials and prefs)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.database.models import Setting


def get(session: Session, key: str, default: str | None = None) -> str | None:
    row = session.get(Setting, key)
    return row.value if row and row.value is not None else default


def set_value(session: Session, key: str, value: str | None) -> None:
    row = session.get(Setting, key)
    if row is None:
        row = Setting(key=key, value=value)
        session.add(row)
    else:
        row.value = value


def all_values(session: Session) -> dict[str, str | None]:
    return {row.key: row.value for row in session.query(Setting).all()}
