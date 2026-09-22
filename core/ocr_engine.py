"""
core/ocr_engine.py
==================
Unified, multi-language OCR engine for the Manga Translator.

Design (Strategy pattern + lazy singletons)
-------------------------------------------
* A :class:`UnifiedOCREngine` routes every ``recognize()`` call to the right
  backend based on the caller's ``source_lang``:

    - ``ja``   -> :class:`MangaOcrBackend`  (manga-ocr, best for Japanese manga)
    - ``en``   -> :class:`EasyOcrBackend(["en"])`
    - ``auto`` -> :class:`EasyOcrBackend(["ja", "en"])` (single pass, both langs)

* Each heavy backend (PyTorch weights / EasyOCR detection models) is loaded
  **lazily** on first use and guarded by a per-backend ``threading.Lock``
  (double-checked locking).  Nothing is loaded at app launch, so the GUI
  starts instantly and memory is only consumed once the user actually
  translates something.  The lock also prevents races when the Flet worker
  thread and a re-entrant click fire two ``recognize()`` calls at once.

* When the app is frozen with PyInstaller, EasyOCR's model directory is
  pointed at ``sys._MEIPASS/easyocr_model`` (bundled at build time) so the
  .exe works fully offline on Windows.  Otherwise EasyOCR downloads the
  models to ``~/.EasyOCR/model`` on first run.

* :func:`detect_language` is a fast Unicode-range heuristic that classifies a
  string as Japanese (kana/kanji) or English (Latin).  The translator can
  use it to refine an ``auto`` source-language guess.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import threading
from typing import List, Optional, Protocol, Union

import config

log = logging.getLogger(__name__)

# Type alias: an OCR input is a BGR numpy array or a PIL Image.
ImageLike = Union["np.ndarray", "object"]


# --------------------------------------------------------------------------- #
# Public protocol
# --------------------------------------------------------------------------- #
class OCREngine(Protocol):
    """Minimal interface every OCR backend (and the unified engine) honours."""

    def recognize(
        self, image_crop: ImageLike, source_lang: str = "auto"
    ) -> str:  # pragma: no cover - protocol
        ...


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _clean(text: str) -> str:
    """Normalise OCR output: strip control chars, collapse whitespace."""
    if not text:
        return ""
    text = "".join(
        ch for ch in text if ch == "\n" or ch == "\t" or ch >= " "
    )
    text = text.replace("\t", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def detect_language(text: str) -> str:
    """
    Fast Unicode-range heuristic.

    Returns ``"ja"`` if *text* contains any Hiragana / Katakana / CJK ideograph
    (or half-width katakana), otherwise ``"en"``.  Empty text -> ``"en"``.
    """
    if not text:
        return "en"
    for ch in text:
        cp = ord(ch)
        # Hiragana + Katakana
        if 0x3040 <= cp <= 0x30FF:
            return "ja"
        # CJK Unified Ideographs (Kanji/Hanzi)
        if 0x4E00 <= cp <= 0x9FAF:
            return "ja"
        # Half-width Katakana
        if 0xFF66 <= cp <= 0xFF9D:
            return "ja"
    return "en"


def _frozen_easyocr_model_dir() -> Optional[str]:
    """
    When running inside a PyInstaller bundle, return the path to the bundled
    EasyOCR model folder (``< _MEIPASS >/easyocr_model``) if it exists, so the
    .exe works fully offline.  Otherwise return ``None`` to let EasyOCR use its
    default (``~/.EasyOCR/model``) and download on first run.
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        bundled = os.path.join(sys._MEIPASS, "easyocr_model")  # type: ignore[attr-defined]
        if os.path.isdir(bundled) and os.listdir(bundled):
            return bundled
    # Also honour an explicit env override (useful for dev testing).
    env_dir = os.environ.get("EASYOCR_MODEL_DIR")
    if env_dir and os.path.isdir(env_dir):
        return env_dir
    return None


# --------------------------------------------------------------------------- #
# Backends
# --------------------------------------------------------------------------- #
class _LazyBackend:
    """
    Wraps a heavy backend with lazy initialisation + a per-backend lock.

    Double-checked locking: the instance is created exactly once even if two
    threads call :meth:`get` concurrently (e.g. the Flet worker thread and a
    second re-entrant click).  After creation the lock is never taken again,
    so concurrent *inference* calls are not serialised (PyTorch inference is
    thread-safe).
    """

    def __init__(self, factory, label: str) -> None:
        self._factory = factory
        self._label = label
        self._instance = None
        self._lock = threading.Lock()

    def get(self):
        if self._instance is not None:
            return self._instance
        with self._lock:
            if self._instance is not None:
                return self._instance
            log.info("Loading OCR backend: %s", self._label)
            self._instance = self._factory()
            log.info("OCR backend ready: %s", self._label)
        return self._instance


class MangaOcrBackend:
    """Japanese-manga-optimised OCR via ``manga_ocr.MangaOcr``."""

    name = "manga_ocr"

    def __init__(self) -> None:
        self._lazy = _LazyBackend(self._load, "manga-ocr")

    def _load(self):
        try:
            from manga_ocr import MangaOcr  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "manga-ocr is not installed.  Run `pip install manga-ocr`."
            ) from exc
        return MangaOcr()

    def recognize(self, image: ImageLike, source_lang: str = "ja") -> str:
        model = self._lazy.get()
        from PIL import Image
        import numpy as np

        if isinstance(image, np.ndarray):
            arr = image[:, :, ::-1].copy() if image.ndim == 3 else image
            pil_img = Image.fromarray(arr)
        else:
            pil_img = image
        text = model(pil_img)
        return _clean(text)


class EasyOcrBackend:
    """EasyOCR backend for English (and ja+en auto-detect)."""

    name = "easyocr"

    def __init__(self, langs: List[str]) -> None:
        self._langs = list(langs)
        self._lazy = _LazyBackend(lambda: self._load(), f"easyocr-{self._langs}")

    def _load(self):
        try:
            import easyocr  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "easyocr is not installed.  Run `pip install easyocr`."
            ) from exc
        kwargs = dict(gpu=False, verbose=False)
        model_dir = _frozen_easyocr_model_dir()
        if model_dir:
            kwargs["model_storage_directory"] = model_dir
            log.info("EasyOCR using bundled models at %s", model_dir)
        return easyocr.Reader(self._langs, **kwargs)

    def recognize(self, image: ImageLike, source_lang: str = "en") -> str:
        reader = self._lazy.get()
        import numpy as np

        if not isinstance(image, np.ndarray):
            image = np.array(image)
        results = reader.readtext(image, detail=0, paragraph=True)
        text = " ".join(str(r) for r in results if r)
        return _clean(text)


class StubBackend:
    """Returns empty text - lets the GUI run even with no OCR installed."""

    name = "stub"

    def recognize(self, image: ImageLike, source_lang: str = "auto") -> str:
        return ""


# --------------------------------------------------------------------------- #
# Unified engine (the public face of this module)
# --------------------------------------------------------------------------- #
class UnifiedOCREngine:
    """
    Routes ``recognize()`` to the right backend by ``source_lang``.

    All backends are constructed cheaply (no models loaded); heavy models load
    lazily on first use, each guarded by its own lock.
    """

    def __init__(self, preference: str = "auto") -> None:
        self.preference = preference or "auto"
        # One backend per language combo.  EasyOCR(["en"]) and
        # EasyOCR(["ja","en"]) are separate Readers so the right language
        # model set is loaded for each path.
        self._manga = MangaOcrBackend()
        self._easy_en = EasyOcrBackend(["en"])
        self._easy_jaen = EasyOcrBackend(["ja", "en"])
        self._stub = StubBackend()
        self._has_manga_ocr = _can_import("manga_ocr")
        self._has_easyocr = _can_import("easyocr")

    # ------------------------------------------------------------------ #
    def recognize(self, image: ImageLike, source_lang: Optional[str] = None) -> str:
        lang = (source_lang or self.preference or "auto").lower()

        if lang == "ja":
            if self._has_manga_ocr:
                try:
                    return self._manga.recognize(image, "ja")
                except RuntimeError as exc:
                    # manga-ocr import failed at runtime -> fall back to ja+en
                    log.warning("manga-ocr unavailable (%s); using EasyOCR ja+en.", exc)
                    self._has_manga_ocr = False
            return self._easy_or_stub(self._easy_jaen, image)

        if lang == "en":
            return self._easy_or_stub(self._easy_en, image)

        # auto
        if self._has_easyocr:
            return self._easy_jaen.recognize(image, "auto")
        if self._has_manga_ocr:
            return self._manga.recognize(image, "ja")
        log.warning("No OCR backend installed; returning empty text.")
        return self._stub.recognize(image, "auto")

    # ------------------------------------------------------------------ #
    def _easy_or_stub(self, backend: EasyOcrBackend, image: ImageLike) -> str:
        if self._has_easyocr:
            return backend.recognize(image)
        log.warning("easyocr unavailable; returning empty text.")
        return self._stub.recognize(image)

    # ------------------------------------------------------------------ #
    @property
    def name(self) -> str:
        return "unified"


def _can_import(module_name: str) -> bool:
    """Return True if *module_name* is importable, without keeping it loaded."""
    import importlib
    try:
        importlib.import_module(module_name)
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Factory
# --------------------------------------------------------------------------- #
def make_engine(preference: str = config.OCR_ENGINE) -> OCREngine:
    """
    Build the unified OCR engine.

    ``preference`` is a *hint* used only when the caller does not pass an
    explicit ``source_lang`` to ``recognize()``; the per-call lang always wins.
    """
    return UnifiedOCREngine(preference=preference or "auto")
