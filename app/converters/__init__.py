"""Modular conversion layer.

Converters declare what they can do; the planner picks a target; the registry
picks a converter; the runner executes it. Everything runs in Python: the only
"external" piece is the UnRAR library redistributed inside the project.
"""

from app.converters.base import (
    BaseConverter,
    ConversionError,
    ConversionRequest,
    ConversionResult,
)
from app.converters.capabilities import Capabilities, detect_toolchain
from app.converters.catalog import targets_for
from app.converters.planner import Plan, describe, plan_conversion
from app.converters.registry import all_converters, converter_names, select_converter
from app.converters.runner import run_conversion

__all__ = [
    "BaseConverter",
    "Capabilities",
    "ConversionError",
    "ConversionRequest",
    "ConversionResult",
    "Plan",
    "all_converters",
    "converter_names",
    "describe",
    "detect_toolchain",
    "plan_conversion",
    "run_conversion",
    "select_converter",
    "targets_for",
]
