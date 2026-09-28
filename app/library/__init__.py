"""Library domain: detection, importing, catalogue queries and file operations.

Submodules are imported explicitly by their callers
(``from app.library.repository import search`` and so on). Keeping this package
free of eager imports avoids an import cycle with ``app.metadata`` and
``app.converters``.
"""
