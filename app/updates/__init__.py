"""Self-update monitoring: notice, details and one-click update.

Public surface used by the panel and the workers; the git plumbing lives in
``app.updates.git``.
"""

from __future__ import annotations

from app.updates.service import (
    apply_update_in_background,
    check_updates_in_background,
    details,
    maybe_check,
    preflight,
    snapshot,
)

__all__ = [
    "apply_update_in_background",
    "check_updates_in_background",
    "details",
    "maybe_check",
    "preflight",
    "snapshot",
]
