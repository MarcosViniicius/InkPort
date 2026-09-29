"""Arte do projeto: gera, valida (XML, dimensões, autocontenção) e detecta deriva.

A checagem mais importante é a **deriva**: a arte versionada em `docs/images`
precisa ser exatamente o que o gerador produz hoje. Se alguém editar
`artwork.toml` e esquecer de rodar o gerador, este teste falha.

Run with:  python tests/artwork.py
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.artwork import configuration, icons, palette  # noqa: E402
from tools.artwork.generate import build_all  # noqa: E402
from tools.artwork.svg import Svg, text_width  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  ok   {name}")
    else:
        FAILED.append(f"{name}: {detail}")
        print(f"  FAIL {name} {detail}")


def _unescape_entities(text: str) -> str:
    """Remove as entidades que o gerador produz, para achar ``&`` cru."""
    for entity in ("&amp;", "&lt;", "&gt;", "&quot;", "&apos;"):
        text = text.replace(entity, "")
    return text


def main() -> int:
    print("\n[configuração e fontes únicas]")
    config = configuration.load()
    check("config carrega", bool(config.name and config.tagline), config.name)
    check(
        "versão vem de app/__init__.py",
        config.version == configuration.app_version(),
        config.version,
    )
    check(
        "override de versão via parâmetro",
        configuration.load(version="9.9.9").version == "9.9.9",
    )
    accent = palette.declarations().get("accent", "")
    check("accent vem do stylesheet", palette.ACCENT in accent, f"{palette.ACCENT} vs {accent}")
    check("mix clareia/escurece", palette.mix("#000000", "#ffffff", 0.5) == "#808080")
    check(
        "ink_on escolhe contraste",
        palette.ink_on(palette.ACCENT) == palette.ACCENT_CONTRAST,
        palette.ink_on(palette.ACCENT),
    )

    print("\n[ícones do painel]")
    catalogo = icons.names()
    check("ícones lidos do partial do painel", len(catalogo) > 10, str(len(catalogo)))
    check("ícone usado pela marca existe", "library" in catalogo, str(catalogo[:5]))
    try:
        icons.icon("nao-existe", x=0, y=0, size=10, color="#000000")
        check("ícone desconhecido falha", False, "não levantou erro")
    except icons.IconError:
        check("ícone desconhecido falha", True)

    print("\n[geração]")
    artefatos = build_all(config)
    check("gera os 4 documentos + badges", len(artefatos) >= 8, str(len(artefatos)))
    avisos = [f"{a.path}: {w}" for a in artefatos for w in a.warnings]
    check("nenhum aviso de encaixe de texto", not avisos, "; ".join(avisos[:2]))

    for artefact in artefatos:
        root = ET.fromstring(artefact.content)
        view_box = root.get("viewBox", "")
        largura = root.get("width")
        altura = root.get("height")
        check(
            f"{Path(artefact.path).name}: XML válido e viewBox coerente",
            view_box == f"0 0 {largura} {altura}",
            f"viewBox={view_box} width={largura} height={altura}",
        )
        check(
            f"{Path(artefact.path).name}: autocontido (sem script/CSS/rede)",
            "<script" not in artefact.content
            and "<style" not in artefact.content
            and "http://" not in artefact.content.replace("http://www.w3.org", ""),
        )
        check(
            f"{Path(artefact.path).name}: sem caracteres crus que quebrem o XML",
            "&" not in _unescape_entities(artefact.content),
        )

    print("\n[deriva: arte versionada == gerada]")
    for artefact in artefatos:
        alvo = ROOT / artefact.path
        if not alvo.is_file():
            check(f"{artefact.path} existe", False, "arquivo ausente")
            continue
        check(
            f"{artefact.path} em dia",
            alvo.read_text(encoding="utf-8") == artefact.content,
            "rode: python -m tools.artwork.generate",
        )

    print("\n[primitivas]")
    check(
        "largura estimada cresce com o texto",
        text_width("abc", 12) < text_width("abcdef", 12),
    )
    check(
        "monoespaçado mede por caractere",
        text_width("iiii", 12, "mono") == text_width("MMMM", 12, "mono"),
    )
    desenho = Svg(100, 50, title="teste")
    desenho.check_fit("texto muito longo para caber", size=20, family="sans", available=50, where="teste")
    check("check_fit avisa quando não cabe", bool(desenho.warnings), str(desenho.warnings))

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for failure in FAILED:
        print(f"  - {failure}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
