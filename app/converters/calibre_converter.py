"""Calibre-backed conversions.

Used for everything that needs *reflowable* output or a Kindle/Kobo container:
PDF -> EPUB (text), MOBI/AZW/FB2 -> EPUB, EPUB -> MOBI/AZW3/KEPUB/PDF.

Image-based work (manga, scans) never goes through Calibre -- it uses the
Pillow pipeline, which is faster and gives us exact control over sizing.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path

from app.config import get_settings
from app.converters.base import (
    BaseConverter,
    ConversionError,
    ConversionRequest,
    ConversionResult,
)
from app.converters.tools import detect_toolchain

logger = logging.getLogger(__name__)

#: How often the Calibre subprocess is polled (progress + cancellation).
_CANCEL_POLL_SECONDS = 0.7

# Formats Calibre can consume for a *reflowable* conversion.
CALIBRE_SOURCES = {
    "epub", "mobi", "azw", "azw3", "fb2", "lit", "pdb", "rtf", "txt",
    "docx", "html", "htmlz", "pdf", "odt", "prc",
}
CALIBRE_TARGETS = {"epub", "mobi", "azw3", "kepub", "pdf", "fb2", "txt", "docx"}

# Device profile -> Calibre output profile name.
OUTPUT_PROFILES = {
    "kindle_paperwhite": "kindle_pw3",
    "kindle_basic": "kindle",
    "kindle_legacy": "kindle",
    "kindle_scribe": "kindle_pw3",
    "kobo_clara": "kobo",
    "kobo_libra": "kobo",
    "generic_epub": "tablet",
    "tablet": "tablet",
    "xteink_x4_pro": "default",
    "xteink_x4_pro_manga": "default",
    "manga_epub": "tablet",
}


class CalibreConverter(BaseConverter):
    name = "calibre"
    priority = 55

    def can_handle(self, request: ConversionRequest) -> bool:
        if not detect_toolchain().has_calibre():
            return False
        if request.options.get("force_page_pipeline"):
            return False
        target = request.target_format
        source_fmt = request.detection.format
        if target not in CALIBRE_TARGETS:
            return False
        if target == source_fmt:
            return False
        if target == "kepub" and source_fmt not in CALIBRE_SOURCES:
            return False
        return source_fmt in CALIBRE_SOURCES

    def run(self, request: ConversionRequest) -> ConversionResult:
        chain = detect_toolchain()
        if not chain.ebook_convert:
            raise ConversionError("Calibre (ebook-convert) não está instalado.")

        settings = get_settings()
        target = request.target_format
        suffix = ".kepub.epub" if target == "kepub" else f".{target}"
        out = request.workdir / f"output{suffix}"

        cmd = [chain.ebook_convert, str(request.source), str(out)]

        output_profile = request.options.get(
            "output_profile", OUTPUT_PROFILES.get(request.profile.slug, "default")
        )
        cmd += [f"--output-profile={output_profile}"]

        meta = request.metadata
        if meta.title:
            cmd += [f"--title={meta.title}"]
        if meta.author:
            cmd += [f"--authors={meta.author}"]
        if meta.publisher:
            cmd += [f"--publisher={meta.publisher}"]
        if meta.language:
            cmd += [f"--language={meta.language}"]
        if meta.series:
            cmd += [f"--series={meta.series}"]
            if meta.series_index is not None:
                cmd += [f"--series-index={int(meta.series_index)}"]

        cover = request.options.get("cover_path")
        if cover and Path(cover).exists():
            cmd += [f"--cover={cover}"]

        extra = request.options.get("calibre_args") or []
        cmd.extend(str(arg) for arg in extra)
        # `--extra-css` only exists for the EPUB output engine.
        if target in {"epub", "kepub"} and request.options.get("extra_css"):
            cmd += ["--extra-css", request.options["extra_css"]]

        request.report(15, "Executando Calibre")
        logger.info("calibre conversion", extra={"cmd": cmd[:4]})
        returncode, output = _run_interruptible(cmd, request, settings.conversion_timeout)

        if returncode != 0 or not out.exists():
            raise ConversionError("Calibre falhou: " + _error_tail(output, ""))

        request.report(95, "Conversão concluída")
        return ConversionResult(
            output_path=out,
            converter=self.name,
            message=f"Convertido para {target} via Calibre",
            page_count=request.metadata.page_count,
        )


def _run_interruptible(
    cmd: list[str], request: ConversionRequest, timeout: int | None
) -> tuple[int, str]:
    """Run Calibre, polling so a cancellation (or the timeout) can stop it.

    Output goes to a file instead of a pipe: a chatty subprocess with nobody
    reading its stdout would block forever once the pipe buffer fills.
    """
    log_path = request.workdir / "calibre.log"
    started = time.monotonic()
    with log_path.open("w", encoding="utf-8", errors="replace") as sink:
        proc = subprocess.Popen(
            cmd,
            stdout=sink,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        try:
            while True:
                try:
                    proc.wait(timeout=_CANCEL_POLL_SECONDS)
                    break
                except subprocess.TimeoutExpired:
                    elapsed = time.monotonic() - started
                    if timeout and elapsed > timeout:
                        _terminate(proc)
                        raise ConversionError(
                            f"Conversão do Calibre excedeu o tempo limite de {timeout}s."
                        ) from None
                    # A progress tick is where cancellation surfaces.
                    percent = min(90, 15 + int(elapsed * 3))
                    request.report(percent, f"Executando Calibre ({int(elapsed)}s)")
        except BaseException:
            # Includes JobCancelled (the user discarded the job): never leave a
            # Calibre process running behind us.
            _terminate(proc)
            raise

    return proc.returncode, _read_log(log_path)


def _read_log(path: Path, limit: int = 20000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[-limit:]
    except OSError:
        return ""


def _terminate(proc: subprocess.Popen) -> None:
    """Best-effort kill of the process (and its children, on Windows)."""
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                check=False,
            )
        else:
            proc.terminate()
            proc.wait(timeout=5)
    except Exception:  # noqa: BLE001 - never raise from a cleanup path
        logger.warning("could not terminate calibre process", exc_info=True)


def _error_tail(stderr: str | None, stdout: str | None) -> str:
    """Extract the meaningful part of Calibre's noisy error output."""
    text = (stderr or "") + "\n" + (stdout or "")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    errors = [
        line for line in lines
        if "error" in line.lower() or "not allowed" in line.lower()
    ]
    chosen = errors or lines[-3:]
    return " | ".join(chosen[-4:])[:600]
