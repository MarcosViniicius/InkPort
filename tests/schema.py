"""Migração aditiva de schema: colunas novas entram num banco antigo.

O projeto não tem framework de migração -- ``create_all`` cria tabelas mas não
adiciona coluna a uma tabela que já existe. ``ensure_schema`` cobre esse caso; o
teste simula um banco antigo (tabela ``feeds`` sem as colunas novas) e confere
que as colunas são adicionadas com os defaults, sem tocar nos dados.

Run with:  python tests/schema.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

WORKDIR = Path(tempfile.mkdtemp(prefix="opds_schema_"))
os.environ["DATA_DIR"] = str(WORKDIR)
os.environ["SECRET_KEY"] = "schema-test"
os.environ["DB_ENCRYPTION"] = "off"

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  ok   {name}")
    else:
        FAILED.append(f"{name}: {detail}")
        print(f"  FAIL {name} {detail}")


def main() -> int:
    from sqlalchemy import inspect

    from app.database import models  # noqa: F401  (register the metadata)
    from app.database.base import get_engine
    from app.database.schema import ensure_schema

    engine = get_engine()
    with engine.begin() as connection:
        # Banco "antigo": só as colunas originais.
        connection.exec_driver_sql(
            "CREATE TABLE feeds ("
            "id INTEGER PRIMARY KEY, "
            "name VARCHAR(300) NOT NULL, "
            "url VARCHAR(2048) NOT NULL)"
        )
        connection.exec_driver_sql(
            "INSERT INTO feeds (name, url) VALUES ('Antigo', 'https://exemplo.com/feed.xml')"
        )

    print("\n[colunas novas entram no banco antigo]")
    added = ensure_schema(engine)
    columns = {column["name"] for column in inspect(engine).get_columns("feeds")}
    esperadas = {"backfill_months", "sitemap_url", "backfill_done_at"}
    check("colunas de retroativos adicionadas", esperadas <= columns, str(added) or str(columns))
    check(
        "outras colunas do modelo também entram",
        {"interval_minutes", "active", "output_format"} <= columns,
        str(sorted(columns)),
    )

    print("\n[idempotente e sem perder dados]")
    check("rodar de novo não adiciona nada", ensure_schema(engine) == [], "adicionou de novo")
    with engine.begin() as connection:
        row = connection.exec_driver_sql(
            "SELECT name, url, backfill_months, sitemap_url, backfill_done_at FROM feeds"
        ).one()
    check(
        "linha antiga sobreviveu com os defaults",
        row[0] == "Antigo" and row[1] == "https://exemplo.com/feed.xml"
        and row[2] == 0 and row[3] == "" and row[4] is None,
        str(row),
    )

    print("\n[banco novo nasce com o schema completo]")
    novo = WORKDIR / "novo"
    os.environ["DATA_DIR"] = str(novo)
    from app.database import base

    base._engine = None
    base._SessionLocal = None
    from app.database import init_db

    init_db()
    cols_novo = {column["name"] for column in inspect(base.get_engine()).get_columns("feeds")}
    check("init_db cria as colunas novas", esperadas <= cols_novo, str(sorted(cols_novo)))

    import shutil

    shutil.rmtree(WORKDIR, ignore_errors=True)
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for failure in FAILED:
        print(f"  - {failure}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
