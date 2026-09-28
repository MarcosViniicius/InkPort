"""RAR support without anything to install: UnRAR loaded from the DLL we ship.

The UnRAR library is redistributed with this project (``app/vendor/unrar``),
exactly as its licence allows, and driven through ``ctypes``. That gives full
RAR support -- RAR3 and RAR5, solid archives and multi-volume sets -- which the
`bsdtar`/libarchive fallback cannot do (it fails on solid archives) and which no
pure-Python package provides.

The public API is intentionally tiny: :func:`available`, :func:`namelist` and
:func:`extract`. Everything stays inside :func:`extract`'s ``dest`` folder.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import POINTER, Structure, c_char, c_char_p, c_int, c_uint, c_void_p, c_wchar, c_wchar_p
from pathlib import Path

logger = logging.getLogger(__name__)

DLL_PATH = Path(__file__).resolve().parent.parent / "vendor" / "unrar" / "unrar.dll"

# --- constants ------------------------------------------------------------
RAR_OM_LIST = 0
RAR_OM_EXTRACT = 1
RAR_SKIP = 0
RAR_TEST = 1
RAR_EXTRACT = 2

ERAR_SUCCESS = 0
ERAR_END_ARCHIVE = 10
ERAR_BAD_DATA = 12
ERAR_BAD_ARCHIVE = 13
ERAR_UNKNOWN_FORMAT = 14
ERAR_MISSING_PASSWORD = 22
ERAR_BAD_PASSWORD = 24

RHDF_DIRECTORY = 0x20
UCM_PROCESSDATA = 1
#: UnRAR callback messages (official values from dll.hpp).
UCM_CHANGEVOLUME = 0
UCM_NEEDPASSWORD = 2
UCM_CHANGEVOLUMEW = 3
UCM_NEEDPASSWORDW = 4
RAR_VOL_ASK = 0

_ERRORS = {
    ERAR_BAD_DATA: "arquivo corrompido",
    ERAR_BAD_ARCHIVE: "arquivo RAR inválido",
    ERAR_UNKNOWN_FORMAT: "formato RAR não reconhecido",
    ERAR_MISSING_PASSWORD: "arquivo protegido por senha",
    ERAR_BAD_PASSWORD: "senha incorreta",
}


class UnrarError(RuntimeError):
    """Raised when UnRAR cannot open or extract an archive.

    ``fatal`` means no other backend can fix it either (missing volume,
    password), so the message should go straight to the user.
    """

    def __init__(self, message: str, *, fatal: bool = False) -> None:
        super().__init__(message)
        self.fatal = fatal


# --- structures (mirror of the official dll.hpp) --------------------------
class RAROpenArchiveDataEx(Structure):
    _fields_ = [
        ("ArcName", c_char_p),
        ("ArcNameW", c_wchar_p),
        ("OpenMode", c_uint),
        ("OpenResult", c_uint),
        ("CmtBuf", c_char_p),
        ("CmtBufSize", c_uint),
        ("CmtSize", c_uint),
        ("CmtState", c_uint),
        ("Flags", c_uint),
        ("Callback", c_void_p),
        ("UserData", c_void_p),
        ("OpFlags", c_uint),
        ("CmtBufW", c_wchar_p),
        ("MarkOfTheWeb", c_wchar_p),
        ("Reserved", c_uint * 23),
    ]


class RARHeaderDataEx(Structure):
    _fields_ = [
        ("ArcName", c_char * 1024),
        ("ArcNameW", c_wchar * 1024),
        ("FileName", c_char * 1024),
        ("FileNameW", c_wchar * 1024),
        ("Flags", c_uint),
        ("PackSize", c_uint),
        ("PackSizeHigh", c_uint),
        ("UnpSize", c_uint),
        ("UnpSizeHigh", c_uint),
        ("HostOS", c_uint),
        ("FileCRC", c_uint),
        ("FileTime", c_uint),
        ("UnpVer", c_uint),
        ("Method", c_uint),
        ("FileAttr", c_uint),
        ("CmtBuf", c_char_p),
        ("CmtBufSize", c_uint),
        ("CmtSize", c_uint),
        ("CmtState", c_uint),
        ("DictSize", c_uint),
        ("HashType", c_uint),
        ("Hash", c_char * 32),
        ("RedirType", c_uint),
        ("RedirName", c_wchar_p),
        ("RedirNameSize", c_uint),
        ("DirTarget", c_uint),
        ("MtimeLow", c_uint),
        ("MtimeHigh", c_uint),
        ("CtimeLow", c_uint),
        ("CtimeHigh", c_uint),
        ("AtimeLow", c_uint),
        ("AtimeHigh", c_uint),
        ("ArcNameEx", c_wchar_p),
        ("ArcNameExSize", c_uint),
        ("FileNameEx", c_wchar_p),
        ("FileNameExSize", c_uint),
        ("Reserved", c_uint * 982),
    ]


UNRARCALLBACK = ctypes.WINFUNCTYPE(c_int, c_uint, c_void_p, c_void_p, c_void_p)

_dll: ctypes.WinDLL | None = None


def _load() -> ctypes.WinDLL | None:
    global _dll
    if _dll is not None:
        return _dll
    if sys.platform != "win32" or not DLL_PATH.is_file():
        return None
    try:
        dll = ctypes.WinDLL(str(DLL_PATH))
    except OSError as exc:  # a broken/blocked DLL must not crash the app
        logger.warning("unrar.dll could not be loaded", extra={"error": str(exc)})
        return None
    dll.RAROpenArchiveEx.argtypes = [POINTER(RAROpenArchiveDataEx)]
    dll.RAROpenArchiveEx.restype = c_void_p
    dll.RARReadHeaderEx.argtypes = [c_void_p, POINTER(RARHeaderDataEx)]
    dll.RARReadHeaderEx.restype = c_int
    dll.RARProcessFileW.argtypes = [c_void_p, c_int, c_wchar_p, c_wchar_p]
    dll.RARProcessFileW.restype = c_int
    dll.RARCloseArchive.argtypes = [c_void_p]
    dll.RARCloseArchive.restype = c_int
    dll.RARSetCallback.argtypes = [c_void_p, c_void_p, c_void_p]
    dll.RARSetCallback.restype = None
    _dll = dll
    return _dll


def available() -> bool:
    """True when the bundled UnRAR library can be used here."""
    return _load() is not None


def _open(dll, source: Path, mode: int):
    data = RAROpenArchiveDataEx()
    data.ArcNameW = str(source)
    data.OpenMode = mode
    handle = dll.RAROpenArchiveEx(ctypes.byref(data))
    if not handle:
        raise UnrarError(f"Não consegui abrir o RAR (código {data.OpenResult}).")
    return handle, data


def namelist(source: Path) -> list[str]:
    """Names of the members inside a RAR (no extraction)."""
    dll = _load()
    if dll is None:
        raise UnrarError("UnRAR indisponível nesta plataforma.")
    handle, _data = _open(dll, source, RAR_OM_LIST)
    names: list[str] = []
    state: dict = {"sink": None, "abort": None}
    callback = UNRARCALLBACK(_make_callback(state))
    dll.RARSetCallback(handle, ctypes.cast(callback, c_void_p), None)
    try:
        header = RARHeaderDataEx()
        while True:
            code = dll.RARReadHeaderEx(handle, ctypes.byref(header))
            if code == ERAR_END_ARCHIVE:
                break
            _check(code)
            _fail_if_aborted(state)
            if not (header.Flags & RHDF_DIRECTORY):
                names.append(header.FileNameW)
            code = dll.RARProcessFileW(handle, RAR_SKIP, None, None)
            if code == ERAR_END_ARCHIVE:
                break
            _check(code)
            _fail_if_aborted(state)
    finally:
        dll.RARCloseArchive(handle)
    return names


def extract(source: Path, dest: Path, *, on_member=None) -> list[str]:
    """Extract every file of ``source`` into ``dest`` (streamed, path-checked).

    ``on_member(name, data)`` -- when given -- receives each member instead of
    being written directly to ``dest``.
    """
    missing = missing_volume(source)
    if missing:
        raise UnrarError(
            "Este RAR faz parte de um conjunto de volumes"
            + (f" e falta «{missing}»" if missing != "?" else "")
            + ". Envie todas as partes juntas na mesma pasta (.part2, .r00, …).",
            fatal=True,
        )

    dll = _load()
    if dll is None:
        raise UnrarError("UnRAR indisponível nesta plataforma.")
    dest.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    handle, _data = _open(dll, source, RAR_OM_EXTRACT)

    state: dict = {"sink": None, "abort": None}
    callback = UNRARCALLBACK(_make_callback(state))
    dll.RARSetCallback(handle, ctypes.cast(callback, c_void_p), None)

    try:
        header = RARHeaderDataEx()
        while True:
            code = dll.RARReadHeaderEx(handle, ctypes.byref(header))
            if code == ERAR_END_ARCHIVE:
                break
            _check(code)
            _fail_if_aborted(state)
            name = header.FileNameW.replace("\\", "/")

            if header.Flags & RHDF_DIRECTORY:
                dll.RARProcessFileW(handle, RAR_SKIP, None, None)
                continue

            if on_member is not None:
                collected: list[bytes] = []
                state["sink"] = collected.append
                code = dll.RARProcessFileW(handle, RAR_TEST, None, None)
                state["sink"] = None
                _check(code)
                _fail_if_aborted(state)
                on_member(name, b"".join(collected))
                written.append(name)
                continue

            target = _target_for(dest, name)
            if target is None:
                logger.warning("rar member ignored (path escape)", extra={"member": name})
                dll.RARProcessFileW(handle, RAR_SKIP, None, None)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("wb") as sink:
                state["sink"] = sink
                code = dll.RARProcessFileW(handle, RAR_TEST, None, None)
                state["sink"] = None
            _check(code)
            _fail_if_aborted(state)
            written.append(name)
    finally:
        state["sink"] = None
        dll.RARCloseArchive(handle)

    if on_member is None:
        # Defensive: the DLL never writes outside dest (we stream the data
        # ourselves), so anything here would be a member name we filtered.
        assert all((dest / name).resolve().is_relative_to(dest.resolve()) for name in written)
    return written


def _target_for(dest: Path, name: str) -> Path | None:
    root = dest.resolve()
    target = (dest / name).resolve()
    if target != root and root not in target.parents:
        return None
    return target


#: rarfile flags: a member continues in / comes from another volume.
RAR_FILE_SPLIT_BEFORE = 0x0001
RAR_FILE_SPLIT_AFTER = 0x0002


def missing_volume(source: Path) -> str | None:
    """Name of the missing volume when ``source`` is part of an incomplete set.

    UnRAR blocks *inside* the library while asking for the next volume, so this
    has to be decided from the (pure Python) header before calling it. Returns
    ``"?"`` when the set is incomplete but the next name is unclear.
    """
    try:
        import rarfile  # type: ignore

        with rarfile.RarFile(source) as archive:
            infos = [info for info in archive.infolist() if not info.isdir()]
    except Exception:  # noqa: BLE001 - a broken header is handled elsewhere
        return None
    if not infos:
        return None

    if infos[0].flags & RAR_FILE_SPLIT_BEFORE:
        return "?"  # we are in the middle of the set
    if not any(info.flags & RAR_FILE_SPLIT_AFTER for info in infos):
        return None

    candidate = next_volume_name(source)
    if candidate is None:
        return "?"
    return None if (source.parent / candidate).exists() else candidate


def next_volume_name(source: Path) -> str | None:
    """``x.part1.rar`` -> ``x.part2.rar``; ``x.rar`` -> ``x.r00``; ``x.r00`` -> ``x.r01``."""
    import re

    name = source.name
    match = re.match(r"^(?P<base>.+)\.part(?P<number>\d+)\.rar$", name, re.IGNORECASE)
    if match:
        following = int(match.group("number")) + 1
        return f"{match.group('base')}.part{following}.rar"
    match = re.match(r"^(?P<base>.+)\.r(?P<number>\d{2})$", name, re.IGNORECASE)
    if match:
        following = int(match.group("number")) + 1
        return f"{match.group('base')}.r{following:02d}"
    if name.lower().endswith(".rar"):
        return f"{name[:-4]}.r00"
    return None


def _make_callback(state: dict):
    """One UnRAR callback handling data, volumes and passwords.

    Returning -1 for a volume/password request *aborts* the operation. Without
    it UnRAR keeps asking for the missing volume forever and the worker hangs.
    """

    def _callback(message: int, _user, pointer, size) -> int:
        if message == UCM_PROCESSDATA and state.get("sink") is not None:
            chunk = ctypes.string_at(pointer, size)
            sink = state["sink"]
            if callable(sink):
                sink(chunk)
            else:
                sink.write(chunk)
            return 0
        if message in (UCM_CHANGEVOLUME, UCM_CHANGEVOLUMEW):
            if int(size) != RAR_VOL_ASK:  # a mere notification
                return 0
            name = ""
            if pointer:
                name = (
                    ctypes.wstring_at(pointer)
                    if message == UCM_CHANGEVOLUMEW
                    else (ctypes.string_at(pointer) or b"").decode("latin-1", "replace")
                )
            state["abort"] = state.get("abort") or (
                "Este RAR faz parte de um conjunto de volumes"
                + (f" (falta «{Path(name).name}»)" if name else "")
                + ". Envie todas as partes juntas (.part2, .r00, …)."
            )
            return -1
        if message in (UCM_NEEDPASSWORD, UCM_NEEDPASSWORDW):
            state["abort"] = state.get("abort") or "Este RAR é protegido por senha."
            return -1
        return 0

    return _callback


def _fail_if_aborted(state: dict) -> None:
    if state.get("abort"):
        raise UnrarError(state["abort"], fatal=True)


def _check(code: int) -> None:
    if code == ERAR_SUCCESS:
        return
    reason = _ERRORS.get(code, f"código {code}")
    raise UnrarError(f"Falha ao extrair o RAR: {reason}.")
