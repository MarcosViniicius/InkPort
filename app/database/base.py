"""Engine/session plumbing for SQLite via SQLAlchemy 2.0.

SQLite is used with WAL mode and a busy timeout so a single-process app with a
few worker threads does not trip over "database is locked".

The database is **encrypted at rest** with SQLCipher (``sqlcipher3`` wheel, no
system library to install): the key lives in ``DATA_DIR/secret.key`` and is
applied before anything else on every connection. A plaintext database from an
older version is migrated in place on first boot (with a ``.plain.bak`` copy).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.dialects import registry
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings
from app.database import crypto
from app.database.sqlcipher import (
    DIALECT_CLASS,
    DIALECT_MODULE,
    DIALECT_NAME,
    DIALECT_SCHEME,
)

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None
_db_key: str | None = None


def _database_url_and_key() -> tuple[str, str | None]:
    """Pick the encrypted dialect when available; migrate a plaintext file once."""
    settings = get_settings()
    if not crypto.enabled():
        if not crypto.available():
            logger.warning(
                "SQLCipher indisponível: o banco ficará em TEXTO PURO "
                "(instale 'sqlcipher3' para cifrar em repouso)"
            )
        else:
            logger.warning("cifragem do banco desligada por configuração")
        return settings.database_url, None

    key = crypto.load_key()
    if key is None:  # pragma: no cover - load_key cria a chave quando falta
        raise crypto.CryptoError("não consegui obter a chave do banco")

    if crypto.looks_plaintext(settings.db_path):
        crypto.encrypt_in_place(settings.db_path, key)

    registry.register(DIALECT_NAME, DIALECT_MODULE, DIALECT_CLASS)
    # "sqlite:///..." -> "sqlite+pysqlcipher:///..." (mesmo tratamento de caminho).
    url = settings.database_url.replace("sqlite:", f"{DIALECT_SCHEME}:", 1)
    return url, key


def get_engine() -> Engine:
    global _engine, _db_key
    if _engine is None:
        url, _db_key = _database_url_and_key()
        _engine = create_engine(
            url,
            echo=False,
            future=True,
            connect_args={"check_same_thread": False, "timeout": 30},
        )

        @event.listens_for(_engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection, _record):  # noqa: ANN001
            if _db_key:
                # A chave vem antes de qualquer outra coisa na conexão.
                crypto.apply_key(dbapi_connection, _db_key)
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()

    return _engine


def _session_factory() -> sessionmaker[Session]:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            bind=get_engine(), autoflush=False, expire_on_commit=False, future=True
        )
    return _SessionLocal


def get_session() -> Iterator[Session]:
    """FastAPI dependency: yields a session and always closes it."""
    session = _session_factory()()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for workers and scripts."""
    session = _session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    """Create every table (and add missing columns). Called on startup."""
    from app.database import models  # noqa: F401  (register the metadata)
    from app.database.schema import ensure_schema

    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    # New columns on an existing table: create_all does not do this.
    ensure_schema(engine)
