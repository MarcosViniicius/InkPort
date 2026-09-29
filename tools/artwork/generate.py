"""Regenera a arte do projeto a partir de ``artwork.toml``.

Uso:
    python -m tools.artwork.generate               # escreve os arquivos
    python -m tools.artwork.generate --check       # nao escreve; falha se estiver desatualizado
    python -m tools.artwork.generate --out DIR     # grava sob outro diretorio
    python -m tools.artwork.generate --version 1.2 # sobrescreve a versao
    python -m tools.artwork.generate --list        # lista os artefatos

O gerador nao depende de nada alem da biblioteca padrao e e deterministico: o
mesmo config produz byte a byte o mesmo SVG (por isso ``--check`` e
``tests/artwork.py`` conseguem detectar arte desatualizada).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from tools.artwork import badges, banner, configuration, diagram, logo, palette, social
from tools.artwork.configuration import Config
from tools.artwork.svg import Svg

IMAGES = Path("docs/images")


@dataclass(frozen=True, slots=True)
class Artefact:
    """One generated file: relative path, content and any layout warning."""

    path: str
    content: str
    warnings: tuple[str, ...]


def build_all(config: Config) -> list[Artefact]:
    """Every artefact, in a stable order (relatório e testes ficam previsíveis)."""
    items: list[tuple[Path, Svg]] = [
        (IMAGES / "logo.svg", _logo(config)),
        (IMAGES / "banner.svg", banner.build(config)),
        (IMAGES / "social-card.svg", social.build(config)),
        (IMAGES / "architecture.svg", diagram.build(config)),
    ]
    for badge in config.badges:
        items.append((IMAGES / "badges" / f"{_slug(badge.label)}.svg", badges.build(badge)))
    return [
        Artefact(path=str(path).replace("\\", "/"), content=svg.render(), warnings=tuple(svg.warnings))
        for path, svg in items
    ]


def main(argv: list[str]) -> int:
    if any(item in ("-h", "--help") for item in argv):
        print(__doc__)
        return 0
    as_list = "--list" in argv
    as_check = "--check" in argv
    version = _option(argv, "--version")
    out = Path(_option(argv, "--out") or palette.ROOT)

    config = configuration.load(version=version)
    artefacts = build_all(config)

    if as_list:
        for artefact in artefacts:
            print(artefact.path)
        return 0

    problems = 0
    for artefact in artefacts:
        target = out / artefact.path
        for warning in artefact.warnings:
            print(f"  aviso  {artefact.path}: {warning}")
            problems += 1
        if as_check:
            current = target.read_text(encoding="utf-8") if target.is_file() else None
            if current is None:
                print(f"  faltando  {artefact.path}")
                problems += 1
            elif current != artefact.content:
                print(f"  desatualizado  {artefact.path}")
                problems += 1
            else:
                print(f"  ok  {artefact.path}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file() and target.read_text(encoding="utf-8") == artefact.content:
            print(f"  sem mudanca  {artefact.path}")
        else:
            target.write_text(artefact.content, encoding="utf-8")
            print(f"  escrito  {artefact.path}")

    if problems:
        print(f"\n{problems} problema(s). Rode sem --check para regenerar.")
        return 1
    if not as_check:
        print(f"\n{len(artefacts)} artefato(s) prontos (versao {config.version}).")
    return 0


def _logo(config: Config) -> Svg:
    svg = Svg(
        128,
        128,
        title=f"{config.name} — marca",
        description="Marca gerada por tools/artwork.",
    )
    logo.mark(svg, 4, 4, size=120, icon_ratio=0.58)
    return svg


def _option(argv: list[str], name: str) -> str | None:
    if name in argv:
        index = argv.index(name)
        if index + 1 < len(argv):
            return argv[index + 1]
    return None


def _slug(value: str) -> str:
    return "".join(char if char.isalnum() or char in "-_" else "-" for char in value.lower()).strip("-")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.path.insert(0, str(palette.ROOT))
    raise SystemExit(main(sys.argv[1:]))
