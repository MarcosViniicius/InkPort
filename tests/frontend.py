"""Frontend guard rails (static checks, no server needed).

Catches the failure modes that make a panel look broken:

* unbalanced CSS (one stray brace silently kills the rest of the stylesheet);
* features that are dropped by older browsers (``color-mix``, a full-screen
  fixed overlay);
* data tables that become unreadable on phones (no ``data-label`` labels);
* inline styles instead of the design system;
* external fonts/CDNs (the panel must work offline on a LAN).

Run with:  python tests/frontend.py
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "app" / "web" / "templates"
CSS = ROOT / "app" / "web" / "static" / "style.css"
JS = ROOT / "app" / "web" / "static" / "app.js"

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  ok   {name}")
    else:
        FAILED.append(f"{name}: {detail}")
        print(f"  FAIL {name} {detail}")


def strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def main() -> int:
    css = CSS.read_text(encoding="utf-8")
    body = strip_comments(css)

    # --- CSS integrity ---------------------------------------------------
    check("CSS balanceado (chaves)", body.count("{") == body.count("}"),
          f"{body.count('{')} abre / {body.count('}')} fecha")
    check("CSS limpo de declarações arriscadas",
          "color-mix(" not in body and "feTurbulence" not in body,
          "color-mix/feTurbulence encontrados")
    check("CSS tem breakpoints de celular e tablet",
          "@media (max-width: 900px)" in body and "@media (max-width: 720px)" in body)
    check("CSS usa fallback de altura de viewport",
          "100vh" in body and "100dvh" in body)
    check("CSS tem reset mínimo e variáveis",
          "--paper:" in body and "--accent:" in body and "box-sizing: border-box" in body)
    check("CSS tem dark mode completo",
          "prefers-color-scheme: dark" in body and "--paper: #14161a" in body)
    for component in (".tile", ".card", ".badge", ".progress", ".empty", ".tabs", ".flash", ".nav"):
        check(f"CSS define {component}", component in body)
    check("CSS transforma tabelas em cartões no celular",
          ".table-wrap td::before" in body and "attr(data-label)" in body)
    check("CSS respeita quem prefere menos animação",
          "prefers-reduced-motion" in body)
    check("CSS tem foco visível", ":focus-visible" in body)
    check("CSS tem alvos de toque no celular", "min-height: 42px" in body)
    check("JS existe", JS.exists() and "nav-open" in JS.read_text(encoding="utf-8"))

    # --- templates -------------------------------------------------------
    templates = {p.name: p.read_text(encoding="utf-8") for p in TEMPLATES.rglob("*.html")}
    check("páginas encontradas", len(templates) >= 10, str(len(templates)))

    base = templates.get("base.html", "")
    check("base tem skip-link e main", "skip-link" in base and 'id="main"' in base)
    check("base marca a página atual", "aria-current" in base)
    check("base tem gaveta no celular", "data-nav-toggle" in base and "scrim" in base)
    check("base tem meta description", 'name="description"' in base)
    check("base tem favicon", "rel=\"icon\"" in base)

    sections = ("Painel", "Biblioteca", "Importar", "Conversões", "Feeds", "Dispositivos", "Configurações")
    check("navegação com todas as seções", all(s in base for s in sections))

    # --- cache-busting: stale CSS + new HTML is the classic "broken panel" ---
    check("assets versionados (evita CSS antigo em cache)",
          "static_url(" in base and "static_url(" in templates.get("login.html", ""),
          "base/login não usam static_url()")
    check("nenhuma URL de asset fixa nos templates",
          not re.search(r'(href|src)="/static/', base + templates.get("login.html", "")),
          "encontrei /static/ fixo")

    # --- mobile menu: exactly one handler, or it opens and closes at once ---
    check("menu mobile tem um único handler (sem toggle duplo)",
          "window.__opdsNavBound" in JS.read_text(encoding="utf-8")
          and 'window.addEventListener("load"' in base,
          "app.js/base.html sem a proteção de handler único")

    # --- layout guards -----------------------------------------------------
    check("grids usam minmax(0/min(...)) para não estourar a largura",
          "minmax(0, 1fr)" in body and "minmax(min(" in body)
    check("botões não encolhem (não cortam o texto)",
          "flex: none" in body and "white-space: nowrap" in body)
    check("cabeçalho do celular trunca o nome",
          "text-overflow: ellipsis" in body)

    # inline styles: only a dynamic width is allowed
    offenders: list[str] = []
    for name, text in templates.items():
        for match in re.finditer(r'style="([^"]*)"', text):
            value = match.group(1)
            if "width:" in value and ("{{" in value or "'" in value):
                continue
            offenders.append(f"{name}: {value[:50]}")
    check("sem estilos inline (só larguras dinâmicas)", not offenders, "; ".join(offenders[:3]))

    # every table cell inside a .table-wrap must carry a label for the mobile layout
    unlabeled: list[str] = []
    for name, text in templates.items():
        for table in re.findall(r'<div class="table-wrap">.*?</table>', text, flags=re.S):
            for cell in re.findall(r"<td([^>]*)>", table):
                if "colspan" in cell:
                    continue
                if "data-label" not in cell:
                    unlabeled.append(f"{name}: <td{cell[:40]}>")
    check("todas as células de tabela têm data-label", not unlabeled, "; ".join(unlabeled[:3]))

    # no external resources: the panel must work offline
    external = [
        name for name, text in templates.items()
        if re.search(r'(src|href)="https?://', text)
    ]
    check("sem recursos externos (funciona offline)", not external, str(external))

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    for failure in FAILED:
        print(f"  - {failure}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
