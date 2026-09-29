"""Banco cifrado (SQLCipher): chave, migração do banco antigo e leitura sem chave.

O caso mais delicado é a **migração**: quem já tinha um ``opds.db`` em texto
puro precisa vê-lo virar cifrado sem perder nada, com backup ao lado.

Run with:  python tests/encryption.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

WORKDIR = Path(tempfile.mkdtemp(prefix="opds_crypto_"))

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  ok   {name}")
    else:
        FAILED.append(f"{name}: {detail}")
        print(f"  FAIL {name} {detail}")


def _use(data_dir: Path, *, encryption: str) -> None:
    """Aponta o app para outra pasta e derruba caches de settings/engine."""
    os.environ["DATA_DIR"] = str(data_dir)
    os.environ["DB_ENCRYPTION"] = encryption
    os.environ.setdefault("SECRET_KEY", "test-secret-key")

    from app.config import get_base_settings, get_settings
    from app.database import base
    from app.security import runtime, setup

    get_settings.cache_clear()
    get_base_settings.cache_clear()
    runtime.reset()
    setup.reset()
    base._engine = None
    base._SessionLocal = None
    base._db_key = None


def _plaintext_database(data_dir: Path) -> None:
    """Cria, em subprocesso, um banco em texto puro (simula a versão antiga)."""
    data_dir.mkdir(parents=True, exist_ok=True)
    script = (
        "import os, sys\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "from sqlalchemy import text\n"
        "from app.database import init_db\n"
        "from app.database.base import session_scope\n"
        "init_db()\n"
        "with session_scope() as session:\n"
        "    session.execute(text(\"INSERT INTO settings (key, value) VALUES ('migracao', 'sobreviveu')\"))\n"
        "print('preparado')\n"
    )
    env = {**os.environ, "DATA_DIR": str(data_dir), "DB_ENCRYPTION": "off"}
    result = subprocess.run(
        [sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=120
    )
    if "preparado" not in result.stdout:
        raise RuntimeError(f"não consegui preparar o banco em texto puro: {result.stderr[-300:]}")


def main() -> int:
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    from app.database import crypto, init_db
    from app.database.base import session_scope

    print("\n[dependência e chave]")
    check("sqlcipher3 disponível", crypto.available())
    if not crypto.available():
        return 1

    novo = WORKDIR / "novo"
    _use(novo, encryption="auto")
    check("cifragem ligada por padrão", crypto.enabled())

    print("\n[banco novo nasce cifrado]")
    init_db()
    with session_scope() as session:
        session.execute(
            text("INSERT INTO settings (key, value) VALUES ('segredo', 'guardado')")
        )
    banco = novo / "opds.db"
    check("arquivo do banco existe", banco.is_file())
    check("não é um SQLite em texto puro", not crypto.looks_plaintext(banco))
    check("sqlite3 comum não consegue ler", not crypto.is_readable_without_key(banco))
    chave = crypto.key_path()
    check("chave criada em DATA_DIR/secret.key", chave.is_file())
    conteudo = chave.read_text(encoding="utf-8").strip()
    check("chave tem 64 hexadecimais (32 bytes)", len(conteudo) == 64, str(len(conteudo)))
    if os.name != "nt":
        modo = chave.stat().st_mode & 0o777
        check("chave com permissão 0600", modo == 0o600, oct(modo))
    check("carregar de novo devolve a mesma chave", crypto.load_key() == conteudo)

    print("\n[dados sobrevivem e erro do banco é mapeado]")
    with session_scope() as session:
        valor = session.execute(
            text("SELECT value FROM settings WHERE key = 'segredo'")
        ).scalar()
    check("leitura com a chave funciona", valor == "guardado", str(valor))
    try:
        with session_scope() as session:
            session.execute(text("INSERT INTO settings (key, value) VALUES ('segredo', 'x')"))
        check("chave duplicada vira IntegrityError do SQLAlchemy", False, "não levantou")
    except IntegrityError:
        check("chave duplicada vira IntegrityError do SQLAlchemy", True)

    print("\n[chave errada não abre o banco]")
    import sqlcipher3

    errado = sqlcipher3.connect(str(banco))
    errado.execute(f"PRAGMA key = \"x'{'00' * 32}'\"")
    try:
        errado.execute("SELECT count(*) FROM sqlite_master").fetchone()
        check("chave errada falha", False, "abriu com chave errada")
    except Exception:  # noqa: BLE001
        check("chave errada falha", True)
    finally:
        errado.close()

    print("\n[migração de banco em texto puro, com backup]")
    migrado = WORKDIR / "migrado"
    _plaintext_database(migrado)
    antigo = migrado / "opds.db"
    check("banco antigo estava em texto puro", crypto.looks_plaintext(antigo))

    _use(migrado, encryption="auto")
    init_db()  # é aqui que a migração acontece
    with session_scope() as session:
        valor = session.execute(
            text("SELECT value FROM settings WHERE key = 'migracao'")
        ).scalar()
    check("dado antigo sobreviveu à migração", valor == "sobreviveu", str(valor))
    check("banco agora está cifrado", not crypto.looks_plaintext(antigo))
    check("sqlite3 comum já não lê", not crypto.is_readable_without_key(antigo))
    backup = antigo.with_suffix(".db.plain.bak")
    check("backup em texto puro foi guardado", backup.is_file())
    check("backup ainda é legível como SQLite comum", crypto.is_readable_without_key(backup))
    vizinhos = [
        antigo,
        antigo.with_name(antigo.name + "-wal"),
        antigo.with_name(antigo.name + "-shm"),
    ]
    vazamento = [
        arquivo.name
        for arquivo in vizinhos
        if arquivo.is_file() and b"sobreviveu" in arquivo.read_bytes()
    ]
    check("nenhum resquício do texto puro ao lado do banco", not vazamento, str(vazamento))
    check(
        "o backup ainda contém o dado (a busca acima faz sentido)",
        b"sobreviveu" in backup.read_bytes(),
    )

    print("\n[escape hatch: texto puro quando pedido]")
    solto = WORKDIR / "solto"
    _use(solto, encryption="off")
    init_db()
    banco_solto = solto / "opds.db"
    check("com DB_ENCRYPTION=off o banco é texto puro", crypto.looks_plaintext(banco_solto))
    check("e nenhuma chave é criada", not (solto / "secret.key").exists())

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for failure in FAILED:
        print(f"  - {failure}")
    import shutil

    shutil.rmtree(WORKDIR, ignore_errors=True)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
