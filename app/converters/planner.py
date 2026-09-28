"""Conversion planning: choose the target format and pipeline for a job.

This is the "auto" brain. It inspects the input, the device profile and the
available tools, then returns a concrete target + options. The actual work is
done by whichever converter the registry selects for that plan.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.devices.profile import DeviceProfile
from app.library.detection import Detection
from app.metadata.model import BookMetadata


@dataclass(slots=True)
class Plan:
    target_format: str
    options: dict = field(default_factory=dict)
    reason: str = ""
    explicit: bool = False


def plan_conversion(
    detection: Detection,
    metadata: BookMetadata,
    profile: DeviceProfile,
    *,
    target_format: str | None = None,
    options: dict | None = None,
    cover_path: Path | None = None,
) -> Plan:
    options = dict(options or {})
    options.setdefault(
        "reading_direction", metadata.reading_direction or ("rtl" if profile.manga_rtl else "ltr")
    )
    if cover_path is not None:
        options.setdefault("cover_path", str(cover_path))

    if target_format:
        return _explicit(detection, profile, target_format, options)

    return _auto(detection, metadata, profile, options)


def _explicit(
    detection: Detection, profile: DeviceProfile, target: str, options: dict
) -> Plan:
    target = target.lower()
    if target in {"epub_optimized", "epub-optimized"}:
        options["optimize"] = True
        return Plan("epub", options, "EPUB otimizado solicitado", explicit=True)

    _mark_page_pipeline(detection, target, options)
    return Plan(target, options, f"Formato {target.upper()} solicitado", explicit=True)


def _auto(
    detection: Detection,
    metadata: BookMetadata,
    profile: DeviceProfile,
    options: dict,
) -> Plan:
    if detection.is_image:
        return Plan("jpg", options, "Imagem única: otimização redimensionada", explicit=False)

    if detection.is_comic or detection.pages:
        target = "epub" if profile.comic_output == "epub_images" else profile.comic_output
        options["force_page_pipeline"] = True
        source = "quadrinho" if detection.is_comic else "PDF de imagens"
        return Plan(target, options, f"{source}: pipeline de imagens para {target.upper()}")

    if detection.is_document:
        if detection.image_only or not profile.reflow_text:
            target = "epub" if profile.comic_output == "epub_images" else profile.comic_output
            options["force_page_pipeline"] = True
            return Plan(target, options, "PDF digitalizado: preservar layout como imagens")
        # Text PDFs are reflowed natively by PyMuPDF -- no external tool.
        target = profile.preferred_format
        if target in {"pdf", "cbz", "jpg", "png"}:
            target = "epub"
        return Plan(target, options, "PDF com texto: reflow nativo")

    if detection.is_ebook:
        target = profile.preferred_format
        if target == detection.format == "epub":
            options["optimize"] = True
            return Plan("epub", options, "EPUB já é o formato ideal: apenas otimizar")
        if target != "epub" and detection.format != "epub":
            # Only an EPUB source can produce the other ebook formats directly.
            return Plan("epub", options, "Primeiro para EPUB, depois para o formato do aparelho")
        return Plan(target, options, f"E-book convertido para {target.upper()}")

    return Plan(profile.preferred_format, options, "Estratégia padrão do perfil")


def _mark_page_pipeline(detection: Detection, target: str, options: dict) -> None:
    """Force the image pipeline when the source is page-based and the target
    is not a reflowable book conversion."""
    page_like = (
        detection.is_comic
        or detection.is_image
        or (detection.is_document and detection.image_only)
        or (detection.is_ebook and detection.image_only)
    )
    if page_like and target in {"epub", "cbz", "pdf"}:
        options["force_page_pipeline"] = True


def describe(plan: Plan) -> str:
    extra = " (forçado)" if plan.options.get("force_page_pipeline") else ""
    return f"{plan.target_format.upper()}{extra} — {plan.reason}"
