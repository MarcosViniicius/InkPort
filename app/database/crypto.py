"""Chave do banco e cifragem em repouso (SQLCipher).

O banco guarda credenciais, feeds, filas e preferências, então é **cifrado em
repouso** com o SQLCipher -- biblioteca que vem na roda do pacote
``sqlcipher3`` (nada para instalar no sistema, nada de binário externo).

A chave é aleatória (32 bytes) e vive em ``DATA_DIR/secret.key`` com permissão
0600: é ela que permite o servidor abrir o banco sozinho depois de um reboot.

**Backup**: sem a ``secret.key`` o ``opds.db`` é ilegível. Copie os dois juntos.
Se a chave se perder, os dados não voltam -- é essa a contrapartida da cifra.
"""

from __future__ import annotations

import contextlib
import logging
import os
import secrets
import shutil
import sqlite3
from pathlib import Path

from app.config import get_settings

logger = logging.getLogger(__name__)

KEY_FILENAME = "secret.key"
#: First 16 bytes of any unencrypted SQLite database.
PLAINTEXT_HEADER = b"SQLite format 3\x00"
KEY_BYTES = 32


class CryptoError(RuntimeError):
    """Raised when the database key cannot be read or created."""


def available() -> bool:
    """True when SQLCipher is importable (i.e. the wheel is installed)."""
    try:
        import sqlcipher3  # noqa: F401
    except ImportError:
        return False
    return True


def enabled() -> bool:
    """Encryption is the default; ``DB_ENCRYPTION=off`` is the escape hatch."""
    if not available():
        return False
    return (get_settings().db_encryption or "auto").strip().lower() not in {"off", "no", "false", "0"}


def key_path() -> Path:
    return get_settings().data_dir / KEY_FILENAME


def load_key(*, create: bool = True) -> str | None:
    """The database key as hex, creating a new one when needed."""
    path = key_path()
    if path.is_file():
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return value
    if not create:
        return None
    key = secrets.token_hex(KEY_BYTES)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 0600: a chave vale tanto quanto o banco.
    descriptor = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(key + "\n")
    # no-op no Windows, essencial no Linux
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)
    logger.info("chave do banco criada", extra={"path": str(path)})
    return key


def apply_key(connection, key: str) -> None:
    """Set the key on a fresh connection and fail fast if it is wrong."""
    connection.execute(f"PRAGMA key = \"x'{key}'\"")
    # A wrong key only shows up when a page is actually read.
    connection.execute("SELECT count(*) FROM sqlite_master").fetchone()


def looks_plaintext(path: Path) -> bool:
    """True when the file is a normal (unencrypted) SQLite database."""
    if not path.is_file():
        return False
    try:
        with path.open("rb") as handle:
            return handle.read(16) == PLAINTEXT_HEADER
    except OSError:
        return False


def encrypt_in_place(path: Path, key: str) -> Path:
    """Encrypt an existing plaintext database, keeping a backup.

    ``PRAGMA rekey`` refuses to run on a plaintext file, so this follows the
    documented SQLCipher path: attach a new encrypted database and
    ``sqlcipher_export`` into it, then swap the files. The database is
    checkpointed and taken out of WAL first so the backup is a single, complete
    file (the app is not running yet: this happens before the engine opens).
    """
    import sqlcipher3

    backup = path.with_suffix(path.suffix + ".plain.bak")
    _leave_wal_mode(path, sqlcipher3)
    shutil.copy2(path, backup)

    encrypted = path.with_name(path.name + ".encrypted")
    encrypted.unlink(missing_ok=True)
    literal = str(encrypted).replace("'", "''")
    connection = sqlcipher3.connect(str(path))
    try:
        connection.execute(
            f"ATTACH DATABASE '{literal}' AS cifrado KEY \"x'{key}'\""
        )
        connection.execute("SELECT sqlcipher_export('cifrado')")
        connection.execute("DETACH DATABASE cifrado")
        connection.commit()
    finally:
        connection.close()

    path.unlink(missing_ok=True)
    encrypted.replace(path)
    _drop_stale_wal(path)

    connection = sqlcipher3.connect(str(path))
    try:
        apply_key(connection, key)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.commit()
    finally:
        connection.close()

    logger.info(
        "banco em texto puro migrado para cifrado",
        extra={"database": str(path), "backup": str(backup)},
    )
    return backup


def _leave_wal_mode(path: Path, driver) -> None:
    """Checkpoint the WAL and switch to a single file, so the copy is whole."""
    connection = driver.connect(str(path))
    try:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.commit()
    finally:
        connection.close()


def _drop_stale_wal(path: Path) -> None:
    """Remove ``-wal``/``-shm`` left over from the plaintext era."""
    for suffix in ("-wal", "-shm"):
        leftover = path.with_name(path.name + suffix)
        with contextlib.suppress(OSError):
            leftover.unlink(missing_ok=True)


def is_readable_without_key(path: Path) -> bool:
    """True when a plain ``sqlite3`` connection can read it (i.e. not encrypted)."""
    if not path.is_file():
        return False
    try:
        with sqlite3.connect(path) as connection:
            connection.execute("SELECT count(*) FROM sqlite_master").fetchone()
    except sqlite3.DatabaseError:
        return False
    return True
