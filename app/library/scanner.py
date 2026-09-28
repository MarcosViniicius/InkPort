"""Scan a folder and import everything recognisable inside it."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from app.database.models import SourceKind
from app.library.detect import is_supported
from app.library.importer import ImportOutcome, import_file

logger = logging.getLogger(__name__)

SKIP_NAMES = {".ds_store", "thumbs.db", "desktop.ini"}


@dataclass(slots=True)
class ScanReport:
    scanned: int = 0
    imported: int = 0
    duplicates: int = 0
    skipped: int = 0
    errors: int = 0
    outcomes: list[ImportOutcome] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.imported + self.duplicates + self.skipped + self.errors


def iter_supported_files(root: Path, *, recursive: bool = True):
    if root.is_file():
        yield root
        return
    pattern = "**/*" if recursive else "*"
    for path in sorted(root.glob(pattern)):
        if not path.is_file():
            continue
        if path.name.lower() in SKIP_NAMES or path.name.startswith("."):
            continue
        if is_supported(path):
            yield path


def scan_directory(
    session: Session,
    root: Path,
    *,
    recursive: bool = True,
    category: str | None = None,
    move: bool = False,
    source: str = SourceKind.IMPORT.value,
) -> ScanReport:
    report = ScanReport()
    if not root.exists():
        return report

    for path in iter_supported_files(root, recursive=recursive):
        report.scanned += 1
        try:
            outcome = import_file(
                session, path, category=category, source=source, move=move
            )
        except Exception as exc:  # one bad file must not stop the scan
            logger.exception("scan import failed", extra={"path": str(path)})
            outcome = ImportOutcome("error", message=str(exc))

        report.outcomes.append(outcome)
        if outcome.status == "imported":
            report.imported += 1
        elif outcome.status == "duplicate":
            report.duplicates += 1
        elif outcome.status in {"unsupported", "no-room"}:
            report.skipped += 1
        else:
            report.errors += 1

    logger.info(
        "scan finished",
        extra={"root": str(root), "imported": report.imported, "duplicates": report.duplicates},
    )
    return report
