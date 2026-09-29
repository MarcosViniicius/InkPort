"""Download tracking subsystem.

* ``store`` -- persistent FileRecord/DownloadEvent domain (no HTTP).
* ``response.TrackedFileResponse`` -- the delivery wrapper used by OPDS and the
  web panel.

Future file management (auto-cleanup, blocking listings) builds on ``store``.
"""

from __future__ import annotations

from app.downloads import store
from app.downloads.response import TrackedFileResponse

__all__ = ["TrackedFileResponse", "store"]
