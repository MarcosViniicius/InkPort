"""Shared model helpers."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_uuid() -> str:
    return uuid.uuid4().hex
