"""Optional OCR backend: RapidOCR (PP-OCRv6, ONNX Runtime). No network at runtime.

RapidOCR is the deployment-friendly conversion of PaddleOCR's PP-OCR models:
Apache-2.0, CPU-only, mobile-sized models and a multilingual recogniser that
includes Portuguese. It is an *optional* extra, exactly like Calibre: when it is
not installed (or the models cannot be fetched) the manga engine falls back to
the geometry-only path and nothing breaks.

The engine is created lazily and reused: building it takes seconds, running it
takes well under a second per page. Calls are serialised because the underlying
ONNX session is shared between worker threads.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

logger = logging.getLogger(__name__)

#: Recognition languages offered in the panel (PP-OCRv6 multilingual).
LANGUAGES = ("pt", "en", "es", "fr", "de", "it")
#: Model tiers: tiny is fastest, small is the accuracy/speed sweet spot.
MODEL_SIZES = ("tiny", "small", "medium")

_lock = threading.Lock()
_engine = None
_engine_key: tuple | None = None


@dataclass(slots=True)
class OcrWord:
    text: str
    score: float
    box: tuple[int, int, int, int]


@dataclass(slots=True)
class OcrLine:
    text: str
    score: float
    box: tuple[int, int, int, int]
    words: list[OcrWord] = field(default_factory=list)


def available() -> bool:
    """True when the optional OCR packages are importable (never raises)."""
    try:
        import onnxruntime  # noqa: F401
        import rapidocr  # noqa: F401
    except Exception:  # noqa: BLE001 - a missing extra is not an error
        return False
    return True


def models_dir() -> Path:
    from app.config import get_settings

    return get_settings().data_dir / "ocr" / "models"


def _quad_box(quad) -> tuple[int, int, int, int]:
    xs = [int(point[0]) for point in quad]
    ys = [int(point[1]) for point in quad]
    return (min(xs), min(ys), max(xs) + 1, max(ys) + 1)


def _get_engine(model_root: str, lang: str, model_size: str):
    global _engine, _engine_key
    key = (model_root, lang, model_size)
    if _engine is not None and _engine_key == key:
        return _engine

    from rapidocr import ModelType, RapidOCR

    sizes = {
        "tiny": ModelType.TINY,
        "small": ModelType.SMALL,
        "medium": ModelType.MEDIUM,
    }
    model_type = sizes.get(str(model_size).lower(), ModelType.SMALL)

    logger.info(
        "creating OCR engine (first run downloads the models)",
        extra={"models": model_root, "lang": lang, "size": model_size},
    )
    engine = RapidOCR(params={
        "Global.model_root_dir": model_root,
        "Global.text_score": 0.3,
        "Global.use_cls": False,
        "Global.return_word_box": True,
        "Global.log_level": "error",
        "Det.model_type": model_type,
        "Rec.model_type": model_type,
        "Rec.lang_type": lang,
    })
    _engine, _engine_key = engine, key
    return engine


def recognize(
    img: Image.Image,
    *,
    lang: str = "pt",
    model_size: str = "small",
    min_score: float = 0.4,
    model_root: Path | None = None,
) -> list[OcrLine] | None:
    """Read the text of ``img``; ``None`` when OCR is unavailable or failed."""
    if not available():
        return None
    root = Path(model_root or models_dir())
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.warning("could not create the OCR model directory", extra={"path": str(root)})

    try:
        with _lock:  # one shared ONNX session across worker threads
            engine = _get_engine(str(root), lang, model_size)
            result = engine(img)
    except Exception as exc:  # noqa: BLE001 - OCR must never break a conversion
        logger.warning("OCR failed", extra={"error": str(exc)})
        return None
    return _parse(result, min_score)


def _parse(result, min_score: float) -> list[OcrLine]:
    text_lines = list(result.txts or ())
    scores = list(result.scores or ())
    boxes = result.boxes
    quads = [boxes[index] for index in range(len(boxes))] if boxes is not None else []
    words_by_line = list(result.word_results or ())

    lines: list[OcrLine] = []
    for index, text in enumerate(text_lines):
        score = float(scores[index]) if index < len(scores) else 0.0
        if score < min_score or not text.strip():
            continue
        box = _quad_box(quads[index]) if index < len(quads) else (0, 0, 1, 1)
        words: list[OcrWord] = []
        if index < len(words_by_line):
            for word in words_by_line[index] or ():
                try:
                    word_text, word_score, word_quad = word[0], float(word[1]), word[2]
                except (TypeError, IndexError, ValueError):
                    continue
                if not word_text.strip():
                    continue
                words.append(OcrWord(word_text, word_score, _quad_box(word_quad)))
        lines.append(OcrLine(text, score, box, words))
    return lines
