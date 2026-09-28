"""Background workers.

Submodules are imported explicitly by their callers (``app.workers.queue``,
``app.workers.manager``, ...) to keep the package import cheap and free of
circular dependencies.
"""
