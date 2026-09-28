"""Archive extraction with no external program to install.

Every format the library accepts is opened by Python itself:

* ``zip``/``cbz``            -> :mod:`zipfile`
* ``tar``/``cbt``            -> :mod:`tarfile`
* ``7z``/``cb7`` (and ``cb7``) -> :mod:`py7zr` (pure Python, pip)
* ``rar``/``cbr``            -> :mod:`rarfile` (pip) driving a backend

RAR is the only format without a pure-Python decoder. ``rarfile`` needs
something that understands the container, and we accept, in order:

1. a bundled wheel with the unrar library (``unrar`` / ``unrar-cffi``), when it
   is installed for this Python version;
2. ``unrar``/``unar``/``7z`` if the machine already has them;
3. **bsdtar**, which ships with Windows 10+ (``C:\\Windows\\System32\\tar.exe``)
   and with macOS (``/usr/bin/tar``) -- so on those systems nothing has to be
   installed.

Only on a bare Linux box with none of the above does a RAR need one extra
package, and the error message says exactly which one.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path

from app.library import formats
from app.library.containers import natural_key

logger = logging.getLogger(__name__)

#: Extensions handled by each family.
ZIP_EXTS = frozenset({"zip", "cbz"})
TAR_EXTS = frozenset({"tar", "cbt"})
SEVENZIP_EXTS = frozenset({"7z", "cb7"})
RAR_EXTS = frozenset({"rar", "cbr"})


class ArchiveError(RuntimeError):
    """Raised when an archive cannot be opened, with a message for the user."""


def _ext(path: Path) -> str:
    return path.suffix.lower().lstrip(".")


def extract(source: Path, dest: Path) -> None:
    """Extract ``source`` into ``dest`` (created if needed)."""
    dest.mkdir(parents=True, exist_ok=True)
    ext = _ext(source)
    if ext in ZIP_EXTS:
        _extract_zip(source, dest)
    elif ext in TAR_EXTS:
        _extract_tar(source, dest)
    elif ext in SEVENZIP_EXTS:
        _extract_7z(source, dest)
    elif ext in RAR_EXTS:
        _extract_rar(source, dest)
    else:
        raise ArchiveError(f"Formato de arquivo não suportado: .{ext}")


def extract_images(source: Path, dest: Path) -> list[Path]:
    """Extract and return the image files, in natural reading order."""
    extract(source, dest)
    images = [
        path
        for path in dest.rglob("*")
        if path.is_file() and path.suffix.lower().lstrip(".") in formats.IMAGE_EXTS
    ]
    return sorted(images, key=lambda p: natural_key(str(p.relative_to(dest))))


def list_images(source: Path) -> list[str]:
    """Names of the images inside the archive, without extracting anything."""
    ext = _ext(source)
    try:
        if ext in ZIP_EXTS:
            with zipfile.ZipFile(source) as zf:
                return [n for n in zf.namelist() if _is_image_name(n)]
        if ext in TAR_EXTS:
            with tarfile.open(source) as tf:
                return [m.name for m in tf.getmembers() if m.isfile() and _is_image_name(m.name)]
        if ext in SEVENZIP_EXTS:
            import py7zr  # type: ignore

            with py7zr.SevenZipFile(source, "r") as archive:
                return [n for n in archive.getnames() if _is_image_name(n)]
        if ext in RAR_EXTS:
            import rarfile  # type: ignore

            for _label, configure in _rar_backends():
                try:
                    configure()
                    with rarfile.RarFile(source) as archive:
                        return [n for n in archive.namelist() if _is_image_name(n)]
                except Exception:  # noqa: BLE001 - try the next backend
                    continue
            return []
    except Exception as exc:  # noqa: BLE001 - a listing is best effort
        logger.debug("archive listing failed", extra={"file": str(source), "error": str(exc)})
    return []


def _is_image_name(name: str) -> bool:
    return not name.endswith("/") and name.rsplit(".", 1)[-1].lower() in formats.IMAGE_EXTS


def _safe_write(dest: Path, name: str, data: bytes) -> None:
    """Write one archive member, refusing anything that escapes ``dest``."""
    target = safe_target(dest, name)
    if target is None:
        logger.warning("archive member ignored (path escape)", extra={"member": name})
        return
    safe_write_member(dest, name, data)


def safe_target(dest: Path, name: str) -> Path | None:
    """Resolve a member name inside ``dest`` (or None when it escapes)."""
    root = dest.resolve()
    target = (dest / name.replace("\\", "/")).resolve()
    if target != root and root not in target.parents:
        return None
    return target


def safe_write_member(dest: Path, name: str, data: bytes) -> None:
    """Write a member that is already known to be inside ``dest``."""
    target = safe_target(dest, name)
    if target is None:  # pragma: no cover - callers check first
        return
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    except OSError as exc:
        logger.warning("archive member not written", extra={"member": name, "error": str(exc)})


# --- families -------------------------------------------------------------
def _extract_zip(source: Path, dest: Path) -> None:
    with zipfile.ZipFile(source) as zf:
        for member in zf.infolist():
            if member.filename.endswith("/"):
                continue
            _safe_write(dest, member.filename, zf.read(member))


def _extract_tar(source: Path, dest: Path) -> None:
    with tarfile.open(source) as tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue
            handle = tf.extractfile(member)
            if handle is None:
                continue
            _safe_write(dest, member.name, handle.read())


def _extract_7z(source: Path, dest: Path) -> None:
    try:
        import py7zr  # type: ignore
    except ImportError as exc:  # pragma: no cover - py7zr is a declared dependency
        raise ArchiveError(
            "Para abrir CB7/7z o pacote 'py7zr' precisa estar instalado "
            "(pip install py7zr)."
        ) from exc
    try:
        with py7zr.SevenZipFile(source, "r") as archive:
            if archive.needs_password():
                raise ArchiveError("Este arquivo 7z é protegido por senha.")
            # py7zr sanitizes each member name itself (no '../' escape) and
            # streams to disk, so a large archive does not sit in memory.
            archive.extractall(path=dest)
    except ArchiveError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ArchiveError(f"Não consegui abrir o arquivo 7z: {exc}") from exc


def _extract_rar(source: Path, dest: Path) -> None:
    # 1. The UnRAR library we ship with the project: full support (RAR3/RAR5,
    #    solid, multi-volume) and nothing to install.
    from app.converters import unrar_dll

    earlier: list[str] = []
    if unrar_dll.available():
        try:
            unrar_dll.extract(source, dest)
            return
        except unrar_dll.UnrarError as exc:
            if exc.fatal:  # missing volume, password: no backend can fix it
                raise ArchiveError(str(exc)) from exc
            earlier.append(f"UnRAR: {exc}")
            logger.info(
                "bundled UnRAR failed; trying other backends", extra={"error": str(exc)}
            )

    # 2. rarfile with whatever backend the machine offers.
    try:
        import rarfile  # type: ignore
    except ImportError as exc:  # pragma: no cover - rarfile is a declared dependency
        raise ArchiveError(
            "Para abrir CBR/RAR o pacote 'rarfile' precisa estar instalado "
            "(pip install rarfile)."
        ) from exc

    errors: list[str] = list(earlier)
    for label, configure in _rar_backends():
        try:
            configure()
            with rarfile.RarFile(source) as archive:
                for info in archive.infolist():
                    if info.isdir():
                        continue
                    with archive.open(info) as handle:
                        _safe_write(dest, info.filename, handle.read())
            return
        except ArchiveError:
            raise
        except Exception as exc:  # noqa: BLE001 - try the next backend
            errors.append(f"{label}: {exc}")
            continue

    if not errors:
        raise ArchiveError(RAR_MISSING_MESSAGE)
    raise ArchiveError(
        "Não consegui abrir o CBR/RAR (" + "; ".join(errors[:3]) + "). " + RAR_MISSING_MESSAGE
    )


def _rar_backends():
    """Backends to try, in order: default detection, then each tool forced."""
    import rarfile  # type: ignore

    def default() -> None:
        rarfile.tool_setup(force=True)

    yield "automático", default

    candidates = (
        ("bsdtar", "BSDTAR_TOOL", ("bsdtar", "tar"), True),
        ("unrar", "UNRAR_TOOL", ("unrar",), False),
        ("unar", "UNAR_TOOL", ("unar", "lsar"), False),
        ("7z", "SEVENZIP_TOOL", ("7z", "7za", "7zz"), False),
    )
    for label, attribute, names, bsdtar_mode in candidates:
        found = _find_tool(names, bsdtar_only=bsdtar_mode)
        if not found:
            continue

        def configure(attribute=attribute, found=found, label=label, bsdtar_mode=bsdtar_mode) -> None:
            setattr(rarfile, attribute, found)
            flags = {
                "unrar": label == "unrar",
                "unar": label == "unar",
                "bsdtar": label == "bsdtar",
                "sevenzip": label == "7z",
                "sevenzip2": False,
            }
            rarfile.tool_setup(force=True, **flags)

        yield found, configure


def _find_tool(names: tuple[str, ...], *, bsdtar_only: bool = False) -> str | None:
    """Locate one of ``names``; ``tar`` must really be bsdtar (libarchive)."""
    for name in names:
        found = shutil.which(name)
        if not found:
            continue
        if name == "tar":
            # GNU tar cannot read RAR; only bsdtar (libarchive) can. Windows'
            # system `tar.exe` is bsdtar, so there we just confirm the version.
            if os.name != "nt" and "bsdtar" not in Path(found).name:
                continue
            try:
                out = subprocess.run(
                    [found, "--version"], capture_output=True, text=True, timeout=10
                )
                if "bsdtar" not in (out.stdout + out.stderr).lower():
                    continue
            except Exception:  # noqa: BLE001
                continue
        return found
    return None


def rar_backend() -> str | None:
    """The RAR backend that actually answers a capability probe."""
    import rarfile  # type: ignore

    for label, configure in _rar_backends():
        try:
            configure()
            # check_cmd really runs the tool (e.g. `bsdtar --version`).
            if rarfile.UNRAR_TOOL or rarfile.BSDTAR_TOOL or getattr(rarfile, "SEVENZIP_TOOL", None):
                return label if isinstance(label, str) and label != "automático" else "automático"
            return "automático"
        except Exception:  # noqa: BLE001 - try the next one
            continue
    return None


RAR_MISSING_MESSAGE = (
    "Este CBR/RAR precisa de um leitor de RAR. No Windows e no macOS o "
    "próprio sistema já traz o 'bsdtar' e nada precisa ser instalado; se esta "
    "mensagem aparece, instale o pacote Python 'rarfile' e um destes: o wheel "
    "'unrar' (pip install unrar), ou o utilitário do sistema 'bsdtar'."
)


def available_backends() -> dict[str, object]:
    """What the archive layer can open right now (used by the panel)."""
    from app.converters import unrar_dll

    rar_with_dll = unrar_dll.available()
    return {
        "zip": True,
        "tar": True,
        "7z": _module_available("py7zr"),
        "rar": rar_with_dll or rar_backend() is not None,
        "rar_backend": "unrar.dll (embutida)" if rar_with_dll else rar_backend(),
    }


def _module_available(name: str) -> bool:
    try:
        __import__(name)
        return True
    except ImportError:
        return False
