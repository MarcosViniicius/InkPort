"""Execute a planned conversion with the selected converter."""

from __future__ import annotations

import logging
from pathlib import Path

from app.converters.base import (
    ConversionError,
    ConversionRequest,
    ConversionResult,
)
from app.converters.planner import Plan, describe, plan_conversion
from app.converters.registry import select_converter
from app.devices.profile import DeviceProfile
from app.library.detection import Detection
from app.metadata.model import BookMetadata

logger = logging.getLogger(__name__)


def run_conversion(
    *,
    source: Path,
    detection: Detection,
    metadata: BookMetadata,
    profile: DeviceProfile,
    workdir: Path,
    target_format: str | None = None,
    options: dict | None = None,
    cover_path: Path | None = None,
    progress=None,
) -> tuple[ConversionResult, Plan]:
    """Plan (if needed), pick a converter and run it. Returns (result, plan)."""
    plan = plan_conversion(
        detection,
        metadata,
        profile,
        target_format=target_format,
        options=options,
        cover_path=cover_path,
    )

    request = ConversionRequest(
        source=source,
        detection=detection,
        metadata=metadata,
        profile=profile,
        target_format=plan.target_format,
        workdir=workdir,
        options=plan.options,
        progress=progress,
    )

    converter = select_converter(request)
    if converter is None:
        raise ConversionError(
            f"Nenhum conversor disponível para {detection.format} -> "
            f"{plan.target_format}. Ferramentas externas ausentes?"
        )

    logger.info(
        "conversion started",
        extra={
            "converter": converter.name,
            "plan": describe(plan),
            "source": str(source),
        },
    )
    request.report(5, f"Conversor: {converter.name}")
    result = converter.run(request)

    if not result.output_path.exists() or result.output_path.stat().st_size == 0:
        raise ConversionError("O conversor não produziu um arquivo válido.")

    logger.info(
        "conversion finished",
        extra={"converter": converter.name, "output": result.output_path.name},
    )
    return result, plan
