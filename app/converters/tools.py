"""Compatibility shim: the tool detector became a *capabilities* detector.

Everything the server needs is a Python package now (plus the UnRAR library we
redistribute in ``app/vendor``), so ``detect_toolchain()`` reports what this
installation can do instead of which executables it found. Optional external
tools are still listed, but nothing depends on them.

New code should import from :mod:`app.converters.capabilities` directly.
"""

from __future__ import annotations

from app.converters.capabilities import Capabilities, detect_toolchain

__all__ = ["Capabilities", "detect_toolchain"]
