"""Read ``artwork.toml`` and resolve the application version.

The version is never typed twice: it defaults to ``app/__init__.py``
(``__version__``) and can be overridden in the config or with ``--version``.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from tools.artwork import palette

CONFIG_PATH = Path(__file__).with_name("artwork.toml")
_VERSION_RE = re.compile(r'^__version__\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)


class ConfigurationError(RuntimeError):
    """Raised when the config is missing a required key."""


@dataclass(frozen=True, slots=True)
class Badge:
    """One SVG badge: ``label`` on the left, ``value`` highlighted on the right."""

    label: str
    value: str
    color: str | None = None


@dataclass(frozen=True, slots=True)
class Config:
    """Everything the generators need, resolved from the TOML file."""

    name: str
    tagline: str
    repo: str
    version: str
    chips: tuple[str, ...]
    headline: tuple[str, ...]
    bullets: tuple[str, ...]
    formats: tuple[str, ...]
    badges: tuple[Badge, ...]
    diagram: dict


def app_version() -> str:
    """``__version__`` from ``app/__init__.py`` (single source of truth)."""
    init = palette.ROOT / "app" / "__init__.py"
    match = _VERSION_RE.search(init.read_text("utf-8"))
    if not match:
        raise ConfigurationError(f"could not find __version__ in {init}")
    return match.group(1)


def load(path: Path | None = None, *, version: str | None = None) -> Config:
    """Read the TOML config; ``version`` (from the CLI) wins over everything."""
    source = path or CONFIG_PATH
    if not source.is_file():
        raise ConfigurationError(f"config not found: {source}")
    data = tomllib.loads(source.read_text("utf-8"))

    project = data.get("project") or {}
    banner = data.get("banner") or {}
    social = data.get("social") or {}
    diagram = data.get("diagram") or {}

    name = _required(project, "name", source)
    resolved_version = version or project.get("version") or app_version()

    badges = tuple(
        Badge(
            label=str(item["label"]),
            value=_interpolate(str(item["value"]), name, resolved_version),
            color=item.get("color"),
        )
        for item in (data.get("badges") or {}).get("items", [])
    )

    return Config(
        name=name,
        tagline=_interpolate(str(project.get("tagline", "")), name, resolved_version),
        repo=str(project.get("repo", "")),
        version=resolved_version,
        chips=tuple(banner.get("chips") or ()),
        headline=tuple(social.get("headline") or ()),
        bullets=tuple(social.get("bullets") or ()),
        formats=tuple(social.get("formats") or ()),
        badges=badges,
        diagram=diagram,
    )


def _interpolate(text: str, name: str, version: str) -> str:
    """``{name}`` and ``{version}`` placeholders, so the config stays DRY."""
    return text.replace("{name}", name).replace("{version}", version)


def _required(section: dict, key: str, source: Path) -> str:
    value = str(section.get(key, "")).strip()
    if not value:
        raise ConfigurationError(f"[project] {key} is required in {source.name}")
    return value
