"""Built-in device profiles.

Verified capability, not marketing claims: a profile only lists a format as
native when that is documented for the device (see ``docs/device-formats.md``).

Xteink is one preset among many -- the defaults target generic e-ink readers.
"""

from __future__ import annotations

from app.devices.profile import DeviceProfile

BUILTIN_PROFILES: dict[str, DeviceProfile] = {}


def _register(profile: DeviceProfile) -> None:
    BUILTIN_PROFILES[profile.slug] = profile


def _eink(
    slug: str,
    name: str,
    width: int,
    height: int,
    *,
    brand: str,
    ppi: int = 300,
    description: str = "",
    recommended: bool = False,
    comic_output: str = "epub_images",
    native: list[str] | None = None,
    preferred: str = "epub",
    quality: int = 84,
    notes: str = "",
    color: bool = False,
    manga: bool = False,
    crop: bool = True,
    max_mb: int = 300,
    image_format: str = "jpg",
    posterize: int = 0,
    spreads: str = "auto",
) -> None:
    """Shortcut for the common e-ink case (grayscale, EPUB-first, 16 levels)."""
    _register(DeviceProfile(
        slug=slug,
        name=name,
        description=description,
        brand=brand,
        recommended=recommended,
        screen_width=width,
        screen_height=height,
        ppi=ppi,
        grayscale=not color,
        color=color,
        gray_levels=256 if color else 16,
        native_formats=native or ["epub"],
        preferred_format=preferred,
        comic_output=comic_output,
        image_quality=quality,
        image_format=image_format,
        posterize_levels=posterize,
        crop_margins=crop,
        spreads=spreads,
        manga_rtl=manga,
        max_file_size_mb=max_mb,
        notes=notes,
    ))


# ---------------------------------------------------------------------------
# Genérico -- the defaults
# ---------------------------------------------------------------------------
_eink(
    "eink_generic", "E-ink genérico 6\"", 1072, 1448, brand="Genérico",
    description="Qualquer leitor e-ink de 6 polegadas, 300 ppi. Um bom começo.",
    recommended=True, notes="Manga e scans saem em EPUB de imagens em tons de cinza.",
)
_eink(
    "eink_generic_7", "E-ink genérico 7\"", 1264, 1680, brand="Genérico",
    description="Leitores e-ink de 7 polegadas (%s)".replace("%s", "Libra, Oasis, Era, Vision"),
)

_register(DeviceProfile(
    slug="generic_epub",
    name="EPUB universal (qualquer tela)",
    description=(
        "Não fixa a tela: mantém a resolução da origem e cada leitor ajusta a "
        "página ao seu visor. Para quando você não sabe em que aparelho vai ler."
    ),
    brand="Universal",
    screen_width=0,
    screen_height=0,
    ppi=0,
    grayscale=False,
    color=True,
    gray_levels=256,
    native_formats=["epub"],
    preferred_format="epub",
    comic_output="epub_images",
    image_quality=88,
    image_format="jpg",
    sharpen=0.4,
    preserve_resolution=True,
    max_long_side=2000,
    crop_margins=False,
    max_file_size_mb=0,
    notes="Mantém a resolução (teto de 2000 px) e nunca passa de 2048×3072.",
))

_register(DeviceProfile(
    slug="manga_epub",
    name="Mangá universal",
    description="Mangá em EPUB adaptável, cor, alta qualidade, leitura RTL.",
    brand="Universal",
    screen_width=0,
    screen_height=0,
    ppi=0,
    grayscale=False,
    color=True,
    gray_levels=256,
    native_formats=["epub", "cbz"],
    preferred_format="epub",
    comic_output="epub_images",
    image_quality=90,
    image_format="jpg",
    sharpen=0.5,
    preserve_resolution=True,
    max_long_side=2400,
    crop_margins=True,
    manga_rtl=True,
    max_file_size_mb=0,
))

# ---------------------------------------------------------------------------
# Xteink -- verified: 4.3" E Ink, reading viewport 480x800 portrait, ~217 ppi.
# Native documents: EPUB, TXT. Images: JPG, BMP. CBZ/PDF/MOBI are advertised on
# some listings but the detailed spec says not to assume them.
# ---------------------------------------------------------------------------
_eink(
    "xteink_x4_pro", "Xteink X4 Pro", 480, 800, brand="Xteink", ppi=217,
    description='4.3" E Ink de bolso, 16 níveis de cinza.',
    native=["epub", "txt", "jpg", "bmp"], quality=80,
    image_format="png", posterize=16, crop=True, max_mb=180,
    notes=(
        "CBZ/CBR/PDF/MOBI NÃO são garantidos pelo fabricante. Manga e scans viram "
        "EPUB de imagens em 16 níveis (exato para o painel), em PNG sem perda. "
        "O firmware rejeita imagens acima de 2048×3072."
    ),
)
_eink(
    "xteink_x4_pro_manga", "Xteink X4 Pro (mangá)", 480, 800, brand="Xteink", ppi=217,
    description="Mangá no X4 Pro: RTL, 16 níveis, página inteira.",
    quality=78, image_format="png", posterize=16, manga=True, max_mb=180,
    notes="Direita para esquerda, 480×800, PNG sem perda.",
)

# ---------------------------------------------------------------------------
# Kindle -- AZW3/EPUB; Send to Kindle accepts EPUB since 2022.
# ---------------------------------------------------------------------------
_eink(
    "kindle_paperwhite", "Kindle Paperwhite", 1236, 1648, brand="Kindle",
    description='Paperwhite 5 / Signature: 6.8", 300 ppi.',
    native=["azw3", "epub", "pdf"],
)
_eink(
    "kindle_basic", "Kindle (11ª geração)", 1072, 1448, brand="Kindle",
    description='Kindle básico 2022: 6", 300 ppi.',
    native=["azw3", "epub", "pdf"],
)
_eink(
    "kindle_oasis", "Kindle Oasis", 1264, 1680, brand="Kindle",
    description='7", 300 ppi.', native=["azw3", "epub", "pdf"],
)
_eink(
    "kindle_scribe", "Kindle Scribe", 1860, 2480, brand="Kindle",
    description='10.2", 300 ppi. Ótimo para páginas duplas.', crop=False,
    native=["epub", "pdf"], quality=88,
)
_eink(
    "kindle_colorsoft", "Kindle Colorsoft", 1272, 1696, brand="Kindle",
    description='7", e-ink colorido.', color=True, quality=88,
    native=["epub", "pdf"],
)
_eink(
    "kindle_legacy", "Kindle antigo (MOBI)", 600, 800, brand="Kindle", ppi=167,
    description="Kindles pré-2022: MOBI copiado por USB.",
    native=["mobi", "azw3"], preferred="mobi", quality=72, max_mb=150,
)

# ---------------------------------------------------------------------------
# Kobo -- KEPUB native; opens CBZ and PDF too.
# ---------------------------------------------------------------------------
_eink(
    "kobo_clara", "Kobo Clara HD / 2E", 1072, 1448, brand="Kobo",
    description='6", 300 ppi, KEPUB.', native=["kepub", "epub", "cbz", "pdf"],
    preferred="kepub", comic_output="cbz",
)
_eink(
    "kobo_libra", "Kobo Libra 2", 1264, 1680, brand="Kobo",
    description='7", 300 ppi, KEPUB.', native=["kepub", "epub", "cbz", "pdf"],
    preferred="kepub", comic_output="cbz",
)
_eink(
    "kobo_sage", "Kobo Sage", 1440, 1920, brand="Kobo",
    description='8", 300 ppi.', native=["kepub", "epub", "cbz", "pdf"],
    preferred="kepub", comic_output="cbz", quality=86,
)
_eink(
    "kobo_elipsa", "Kobo Elipsa", 1404, 1872, brand="Kobo",
    description='10.3", 227 ppi.', native=["kepub", "epub", "cbz", "pdf"],
    ppi=227, preferred="kepub", comic_output="cbz", crop=False, quality=86,
)
_eink(
    "kobo_clara_colour", "Kobo Clara Colour", 1072, 1448, brand="Kobo",
    description='6", e-ink colorido.', color=True, quality=88,
    native=["kepub", "epub", "cbz", "pdf"], preferred="kepub", comic_output="cbz",
)

# ---------------------------------------------------------------------------
# PocketBook -- opens EPUB and CBZ/CBR natively.
# ---------------------------------------------------------------------------
_eink(
    "pocketbook_era", "PocketBook Era", 1264, 1680, brand="PocketBook",
    description='7", 300 ppi.', native=["epub", "cbz", "cbr", "pdf"], comic_output="cbz",
)
_eink(
    "pocketbook_inkpad", "PocketBook InkPad", 1404, 1872, brand="PocketBook",
    description='10.3", 227 ppi.', ppi=227, native=["epub", "cbz", "cbr", "pdf"],
    comic_output="cbz", crop=False, quality=86,
)

# ---------------------------------------------------------------------------
# Android e-ink (Boox) and Tolino
# ---------------------------------------------------------------------------
_eink(
    "boox_palma", "Onyx Boox Palma", 824, 1648, brand="Boox",
    description='6.13", 300 ppi, leitor Android.', native=["epub", "cbz", "pdf"],
    preferred="epub", comic_output="epub_images",
)
_eink(
    "boox_note", "Onyx Boox Note", 1404, 1872, brand="Boox",
    description='10.3", 227 ppi, Android (abre quase tudo).', ppi=227,
    native=["epub", "cbz", "cbr", "pdf"], comic_output="cbz", crop=False, quality=86,
)
_eink(
    "tolino_vision", "Tolino Vision 6", 1264, 1680, brand="Tolino",
    description='7", 300 ppi.', native=["epub", "cbz", "pdf"], comic_output="cbz",
)
_eink(
    "tolino_shine", "Tolino Shine 3", 1072, 1448, brand="Tolino",
    description='6", 300 ppi.', native=["epub", "cbz", "pdf"], comic_output="cbz",
)

# ---------------------------------------------------------------------------
# reMarkable and tablets
# ---------------------------------------------------------------------------
_eink(
    "remarkable_2", "reMarkable 2", 1404, 1872, brand="reMarkable", ppi=226,
    description='10.3", 226 ppi. Abre PDF nativamente (preserva o layout).',
    native=["pdf", "epub"], preferred="pdf", comic_output="pdf",
    crop=False, quality=86,
)
_eink(
    "remarkable_paper_pro", "reMarkable Paper Pro", 1620, 2160, brand="reMarkable",
    description='11.8", colorido.', ppi=229, color=True,
    native=["pdf", "epub"], preferred="pdf", comic_output="pdf",
    crop=False, quality=90,
)
_eink(
    "tablet", "Tablet / celular", 1600, 2560, brand="Tela grande", ppi=320,
    description="Tela grande e colorida: imagem em alta qualidade.",
    color=True, native=["epub", "pdf", "cbz"], comic_output="pdf",
    crop=False, quality=92, max_mb=0,
)

DEFAULT_PROFILE_SLUG = "eink_generic"
