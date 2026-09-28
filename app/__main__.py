"""Run the server: ``python -m app`` (or ``uvicorn app.main:app``).

Binds to every interface by default so e-readers on the same network can reach
the catalog. Change ``HOST``/``PORT`` in the ``.env`` if needed.
"""

from __future__ import annotations

import logging

import uvicorn

from app.config import get_settings

logger = logging.getLogger(__name__)


def main() -> None:
    settings = get_settings()
    options = {
        "host": settings.host,
        "port": settings.port,
        "log_level": settings.log_level.lower(),
    }
    if settings.debug:
        uvicorn.run("app.main:app", reload=True, **options)
    else:
        if settings.trust_proxy:
            options["proxy_headers"] = True
            options["forwarded_allow_ips"] = "*"
        uvicorn.run("app.main:app", **options)


if __name__ == "__main__":
    main()
