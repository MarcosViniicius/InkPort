"""Arquivos compactados: tudo em Python, nada instalado no sistema.

Cobre o que antes exigia `unrar`/`7z` na máquina:
ZIP/CBZ, TAR/CBT, 7z/CB7 (py7zr) e RAR/CBR (a unrar.dll que acompanha o projeto).

Run with:  python tests/archives.py
"""

from __future__ import annotations

import io
import os
import shutil
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

WORKDIR = Path(tempfile.mkdtemp(prefix="opds_arch_"))
os.environ["DATA_DIR"] = str(WORKDIR)
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ.setdefault("ADMIN_PASSWORD", "test-password")  # senha explicita: pula o assistente

FIXTURES = ROOT / "tests" / "fixtures"

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  ok   {name}")
    else:
        FAILED.append(f"{name}: {detail}")
        print(f"  FAIL {name} {detail}")


def _png_bytes(color: tuple[int, int, int] = (10, 200, 30)) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (40, 60), color).save(buffer, "PNG")
    return buffer.getvalue()


def _make_zip(path: Path, names: list[str]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name in names:
            zf.writestr(name, _png_bytes())


def _make_tar(path: Path, names: list[str]) -> None:
    data = _png_bytes()
    with tarfile.open(path, "w") as tf:
        for name in names:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))


def _make_7z(path: Path, names: list[str]) -> None:
    import py7zr

    data = _png_bytes()
    with py7zr.SevenZipFile(path, "w") as archive:
        for name in names:
            archive.writestr(data, name)


def main() -> int:
    from app.converters import unrar_dll
    from app.converters.archives import (
        ArchiveError,
        available_backends,
        extract,
        extract_images,
        list_images,
    )
    from app.converters.unrar_dll import next_volume_name

    print("[ZIP/CBZ, TAR/CBT e 7z/CB7 em Python puro]")
    names = ["001.png", "002.png", "010.png", "sub/003.png"]
    for suffix, maker in (("cbz", _make_zip), ("cbt", _make_tar), ("cb7", _make_7z)):
        source = WORKDIR / f"comic.{suffix}"
        maker(source, names)
        dest = WORKDIR / f"out_{suffix}"
        pages = extract_images(source, dest)
        listed = list_images(source)
        check(f".{suffix}: extrai {len(names)} arquivos", len(pages) == len(names), str(len(pages)))
        check(f".{suffix}: ordem natural", [p.name for p in pages][:3] == ["001.png", "002.png", "010.png"],
              str([p.name for p in pages]))
        check(f".{suffix}: listagem sem extrair", len(listed) == len(names), str(listed))

    print("\n[segurança: membro que tenta escapar da pasta]")
    evil = WORKDIR / "evil.cbz"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("ok.png", _png_bytes())
        zf.writestr("../../escaped.png", _png_bytes())
    dest = WORKDIR / "evil_out"
    extract(evil, dest)
    escaped = list(WORKDIR.glob("escaped.png")) + list(WORKDIR.parent.glob("escaped.png"))
    check("extrai o membro legítimo", (dest / "ok.png").exists())
    check("membro com '..' é ignorado", not escaped, str(escaped))

    print("\n[RAR/CBR: unrar.dll que vem com o projeto]")
    check("unrar.dll carregável", unrar_dll.available())
    solid = FIXTURES / "rar3-solid.rar"
    if solid.exists() and unrar_dll.available():
        out = WORKDIR / "solid_out"
        written = unrar_dll.extract(solid, out)
        check("RAR solid descompactado", len(written) == 2, str(written))
        sizes = sorted((out / name).stat().st_size for name in written)
        check("conteúdo íntegro (tamanhos do arquivo)", sizes == [2048, 2048], str(sizes))
        check("listagem do RAR", len(unrar_dll.namelist(solid)) == 2)
    else:
        check("fixture RAR presente", False, str(solid))

    subdirs = FIXTURES / "rar3-subdirs.rar"
    if subdirs.exists() and unrar_dll.available():
        out = WORKDIR / "sub_out"
        unrar_dll.extract(subdirs, out)
        files = [p for p in out.rglob("*") if p.is_file()]
        check("RAR com subpastas", len(files) >= 3, str([p.name for p in files]))

    print("\n[volumes: recusa honesta em vez de travar]")
    check(
        "próximo volume de .part1.rar",
        next_volume_name(Path("x.part1.rar")) == "x.part2.rar",
        str(next_volume_name(Path("x.part1.rar"))),
    )
    check(
        "próximo volume de .rar",
        next_volume_name(Path("x.rar")) == "x.r00",
        str(next_volume_name(Path("x.rar"))),
    )
    if unrar_dll.available():
        # O fixture solid não é multi-volume: aqui só confirmamos que a
        # checagem de volume não atrapalha um arquivo completo.
        check(
            "arquivo completo não é marcado como incompleto",
            unrar_dll.missing_volume(FIXTURES / "rar3-solid.rar") is None,
        )

    print("\n[formato desconhecido]")
    bogus = WORKDIR / "coisa.bin"
    bogus.write_bytes(b"nada")
    try:
        extract(bogus, WORKDIR / "bogus_out")
        check("formato sem suporte dá erro claro", False, "não levantou")
    except ArchiveError as exc:
        check("formato sem suporte dá erro claro", "não suportado" in str(exc), str(exc)[:60])

    print("\n[backends reportados]")
    backends = available_backends()
    check("zip/tar/7z disponíveis", bool(backends["zip"] and backends["tar"] and backends["7z"]))
    if sys.platform == "win32":
        check("RAR disponível (DLL embutida)", bool(backends["rar"]), str(backends))
    else:
        # A DLL que acompanha o projeto é do Windows; fora dele o app precisa
        # degradar para o rarfile/utilitário do sistema sem quebrar nada.
        check(
            "fora do Windows a DLL embutida não é usada",
            backends["rar_backend"] != "unrar.dll (embutida)",
            str(backends),
        )
    print(f"     {backends}")

    print("\n[UnRAR não depende de API só do Windows]")
    import ctypes  # noqa: PLC0415 - usado apenas neste bloco

    from app.converters import unrar_dll as dll_module

    check("módulo do UnRAR importa em qualquer plataforma", dll_module is not None)
    check(
        "o callback é resolvido na hora, não no import",
        hasattr(dll_module, "_callback_type"),
    )
    check("o callback é criado nesta plataforma", dll_module._callback_type() is not None)
    if not hasattr(ctypes, "WINFUNCTYPE"):
        check("a ausência de WINFUNCTYPE não derruba o import", dll_module.available() is False)

    cached_dll = dll_module._dll
    real_platform = sys.platform
    try:
        dll_module._dll = None
        sys.platform = "linux"
        check("fora do Windows, available() é False", dll_module.available() is False)
        try:
            dll_module.namelist(FIXTURES / "rar3-solid.rar")
            check("fora do Windows, extrair dá erro claro", False, "não levantou")
        except dll_module.UnrarError as exc:
            check(
                "fora do Windows, extrair dá erro claro",
                "indisponível" in str(exc),
                str(exc)[:60],
            )
    finally:
        sys.platform = real_platform
        dll_module._dll = cached_dll

    # Reproduz o caso Linux de forma deterministica: sem ctypes.WINFUNCTYPE o
    # modulo PRECISA importar (antes o import estourava e derrubava o painel).
    import importlib

    original_winfunc = getattr(ctypes, "WINFUNCTYPE", None)
    try:
        if original_winfunc is not None:
            del ctypes.WINFUNCTYPE
        recarregado = importlib.reload(dll_module)
        check("import sem WINFUNCTYPE não quebra (caso Linux)", recarregado is not None)
        check(
            "e a sondagem continua segura",
            recarregado.available() in (True, False),
        )
    except Exception as exc:  # noqa: BLE001
        check("import sem WINFUNCTYPE não quebra (caso Linux)", False, str(exc))
    finally:
        if original_winfunc is not None:
            ctypes.WINFUNCTYPE = original_winfunc  # type: ignore[attr-defined]
        importlib.reload(dll_module)

    shutil.rmtree(WORKDIR, ignore_errors=True)
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for failure in FAILED:
        print(f"  - {failure}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
