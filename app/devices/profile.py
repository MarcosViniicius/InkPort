"""Extensible device-profile system.

A profile describes the *real* capabilities of a reading device so the
conversion planner can pick a target format and image pipeline automatically.

Important: profiles encode verified capability, not marketing claims. The
Xteink X4 Pro profile, for example, treats EPUB as native and CBZ/PDF as
"not guaranteed", because that is what the vendor's detailed spec sheet and
FAQ state. See ``docs/device-formats.md``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(slots=True)
class DeviceProfile:
    slug: str
    name: str
    description: str = ""
    #: Brand for grouping in the UI (Genérico, Kindle, Kobo, PocketBook, ...).
    brand: str = "Genérico"
    #: Highlighted in the panel as a sensible starting point.
    recommended: bool = False

    # Panel geometry. ``screen_width``/``screen_height`` are the native pixels
    # in *landscape orientation* for panels that are physically landscape.
    screen_width: int = 1200
    screen_height: int = 1600
    ppi: int = 300

    # Display capability
    grayscale: bool = True
    color: bool = False
    gray_levels: int = 16  # 2 = 1-bit, 16 = typical e-ink, 256 = full grayscale

    # Formats the device opens *natively* (verified), best first.
    native_formats: list[str] = field(default_factory=lambda: ["epub"])
    preferred_format: str = "epub"

    # Format used when the source is image-based (comics/manga/scans).
    # "epub_images" -> image-only EPUB (works on readers with no CBZ support)
    # "cbz" -> keep a comic archive
    # "pdf" -> fixed-layout PDF
    comic_output: str = "epub_images"

    # Image pipeline tuning
    image_quality: int = 82
    #: "jpg" (menor) ou "png" (sem perda — melhor para traço/preto e branco).
    image_format: str = "jpg"
    #: DPI de renderização de PDF. 0 = calculado a partir do tamanho alvo.
    render_dpi: int = 0
    #: Nivela digitalizações escuras/claras (autocontraste).
    autocontrast: bool = False
    #: Páginas duplas (spreads): "auto" divide ou gira como o KCC; também
    #: aceita "split", "rotate", "both" e "none". Só vale com tela definida.
    spreads: str = "none"
    #: Páginas duplas (spreads): "auto" (divide ou gira, como o KCC), "split",
    #: "rotate", "both" ou "none". Só faz sentido em perfis com tela definida.
    spreads: str = "none"
    #: Quantiza a imagem para N níveis de cinza (0 = desligado). Um painel e-ink
    #: mostra 16 níveis, então usar 16 é exato para ele e reduz muito o tamanho
    #: (o JPEG gasta bits com o retículo/halftone que o painel nem exibe).
    posterize_levels: int = 0
    #: Intensidade do realce (unsharp), 0 = desligado.
    sharpen: float = 0.6
    #: Não redimensiona para a tela: mantém a resolução original (limitada por
    #: ``max_long_side``). É o modo "universal", que serve em qualquer aparelho.
    preserve_resolution: bool = False
    #: Teto do lado maior no modo universal (0 = sem teto além do limite seguro).
    max_long_side: int = 0
    allow_upscale: bool = False
    rotate_portrait: bool = False
    crop_margins: bool = True
    gamma: float = 1.0
    contrast: float = 1.0
    max_image_width: int = 0   # 0 = derived from screen_width
    max_image_height: int = 0  # 0 = derived from screen_height

    # Limits
    max_file_size_mb: int = 300
    margin_px: int = 0

    # Reading behaviour
    manga_rtl: bool = False
    reflow_text: bool = True

    # Free-form notes shown in the panel
    notes: str = ""

    # --- helpers ----------------------------------------------------------
    def can_open(self, fmt: str) -> bool:
        return fmt.lower().lstrip(".") in {f.lower() for f in self.native_formats}

    @property
    def is_universal(self) -> bool:
        """True when this profile does not bake in a single screen size."""
        return self.preserve_resolution or not self.screen_width or not self.screen_height

    @property
    def screen_label(self) -> str:
        if self.is_universal:
            return "qualquer tela"
        if not self.ppi:
            return f"{self.screen_width}×{self.screen_height}"
        return f"{self.screen_width}×{self.screen_height} · {self.ppi} ppi"

    @property
    def target_width(self) -> int:
        return self.max_image_width or self.screen_width

    @property
    def target_height(self) -> int:
        return self.max_image_height or self.screen_height

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> DeviceProfile:
        valid = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in valid})
