"""Dialeto SQLAlchemy sobre o SQLCipher (SQLite cifrado em repouso).

Passar um ``creator`` com ``sqlcipher3`` **não basta**: o SQLAlchemy mapeia as
exceções do DBAPI do *dialeto*, e ``sqlcipher3.dbapi2.IntegrityError`` não
herda de ``sqlite3.IntegrityError`` -- ou seja, ``except IntegrityError``
deixaria a exceção escapar. Aqui o dialeto passa a usar o DBAPI do SQLCipher,
então o mapeamento (e o resto do comportamento do SQLite) continua correto.
"""

from __future__ import annotations

from sqlalchemy.dialects.sqlite.pysqlite import SQLiteDialect_pysqlite

#: Registry name used by ``registry.register`` and the URL scheme.
DIALECT_NAME = "sqlite.pysqlcipher"
DIALECT_SCHEME = "sqlite+pysqlcipher"
DIALECT_MODULE = "app.database.sqlcipher"
DIALECT_CLASS = "SQLCipherDialect"


class SQLCipherDialect(SQLiteDialect_pysqlite):
    """``sqlite+pysqlcipher://``: mesma linguagem, banco cifrado."""

    @classmethod
    def import_dbapi(cls):
        import sqlcipher3

        return sqlcipher3.dbapi2
