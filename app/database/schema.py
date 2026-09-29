"""Additive schema sync for databases created by an older version.

The app has no migration framework: ``Base.metadata.create_all`` creates missing
tables but never adds a column to a table that already exists. This module fills
that gap by issuing ``ALTER TABLE ... ADD COLUMN`` for every model column the
database lacks, so an upgrade (e.g. the new per-feed backfill settings) lands on
an existing installation without touching the data.

It is **additive only**: it never drops, renames or retypes anything, and a
column it cannot add safely is skipped with a warning instead of breaking the
boot.
"""

from __future__ import annotations

import logging

from sqlalchemy import inspect
from sqlalchemy.engine import Engine

from app.database.base import Base

logger = logging.getLogger(__name__)


def ensure_schema(engine: Engine) -> list[str]:
    """Add columns the models declare but the database is missing (idempotent)."""
    added: list[str] = []
    with engine.begin() as connection:
        inspector = inspect(connection)
        existing_tables = set(inspector.get_table_names())
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue  # create_all built it from scratch just now
            present = {column["name"] for column in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in present:
                    continue
                ddl = _add_column_ddl(table.name, column, connection.dialect)
                if ddl is None:
                    logger.warning(
                        "schema: cannot add a non-null column without a default",
                        extra={"table": table.name, "column": column.name},
                    )
                    continue
                connection.exec_driver_sql(ddl)
                added.append(f"{table.name}.{column.name}")
    if added:
        logger.info("schema updated", extra={"columns_added": added})
    return added


def _add_column_ddl(table_name: str, column, dialect) -> str | None:
    default = _default_sql(column)
    if not column.nullable and default is None:
        return None
    parts = [f'ALTER TABLE "{table_name}" ADD COLUMN "{column.name}" {column.type.compile(dialect=dialect)}']
    if not column.nullable:
        parts.append("NOT NULL")
    if default is not None:
        parts.append(f"DEFAULT {default}")
    return " ".join(parts)


def _default_sql(column) -> str | None:
    """SQL literal for the column default, or ``None`` when it cannot be inlined."""
    if column.server_default is not None:
        arg = getattr(column.server_default, "arg", None)
        if arg is not None:
            return str(arg)
    default = getattr(column, "default", None)
    if default is None or default.is_callable:
        return None
    return _literal(default.arg)


def _literal(value) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"
