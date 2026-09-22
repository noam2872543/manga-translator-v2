"""
config.py
=========
Central configuration for the Manga Translator application.

All tunable parameters (paths, detection thresholds, OCR/translation
settings, font preferences) live here so the rest of the codebase stays
declarative and easy to adjust.

PyInstaller note
----------------
When the app is frozen with PyInstaller (``--onefile`` or ``--onedir``),
read-only resources (fonts, default config) are extracted to
``sys._MEIPASS`` (a temporary folder).  ``BASE_DIR`` therefore resolves to
that extraction folder at runtime so bundled fonts are found.  ``OUTPUT_DIR``
is a *writable* location - it defaults to a folder next to the .exe if
writable, otherwise ``~/MangaTranslator`` in the user's home.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List


# --------------------------------------------------------------------------- #
# Frozen / dev path resolution
# --------------------------------------------------------------------------- #
def _is_frozen() -> bool:
    """True when running inside a PyInstaller bundle."""
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def _resource_dir() -> Path:
    """
    Directory containing bundled read-only resources (fonts, etc.).

    When frozen: PyInstaller's extraction folder (``sys._MEIPASS``).
    When running from source: the folder containing this file.
    """
    if _is_frozen():
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent


def _writable_output_dir() -> Path:
    """
    A writable folder for saved single-image translations.

    When frozen: prefer a folder next to the .exe (so the user finds the
    output easily); fall back to ``~/MangaTranslator/output`` if the .exe
    folder is not writable (e.g. Program Files on Windows).
    When running from source: the local ``output/`` folder.
    """
    if _is_frozen():
        exe_dir = Path(sys.executable).resolve().parent
        candidate = exe_dir / "output"
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return candidate
        except Exception:
            home_out = Path.home() / "MangaTranslator" / "output"
            home_out.mkdir(parents=True, exist_ok=True)
            return home_out
    return Path(__file__).resolve().parent / "output"


# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
BASE_DIR: Path = _resource_dir()

ASSETS_DIR: Path = BASE_DIR / "assets"
FONTS_DIR: Path = ASSETS_DIR / "fonts"
OUTPUT_DIR: Path = _writable_output_dir()

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Candidate Hebrew-capable TrueType fonts, tried in order.  Drop a .ttf into
# assets/fonts/ and add its name here.  VarelaRound is preferred because it
# renders the boldest/cleanest Hebrew at small bubble sizes; David Libre and
# Assistant are kept as alternatives.  A download helper lives in
# assets/fonts/download_font.sh.
FONT_CANDIDATES: List[str] = [
    # Bundled fonts (preferred) - place .ttf files in assets/fonts/
    str(FONTS_DIR / "VarelaRound-Regular.ttf"),
    str(FONTS_DIR / "DavidLibre-Regular.ttf"),
    str(FONTS_DIR / "Assistant-Regular.ttf"),
    str(FONTS_DIR / "Assistant-Bold.ttf"),
    str(FONTS_DIR / "Rubik-Regular.ttf"),
    # Common system fonts (best-effort, used if no bundled font exists)
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",            # has Hebrew
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",                         # Arch
    "/System/Library/Fonts/Supplemental/Arial Hebrew.ttf",         # macOS
    "C:\\Windows\\Fonts\\arial.ttf",                               # Windows
]

# --------------------------------------------------------------------------- #
# OCR engine
# --------------------------------------------------------------------------- #
# "manga_ocr" : best for Japanese manga (heavy: pulls PyTorch)
# "easyocr"   : multi-language fallback (also pulls PyTorch)
# "auto"      : try manga_ocr first, then easyocr
# NOTE: this is only a *hint* for the unified engine when the caller does not
# pass an explicit source_lang to recognize(); the per-call lang always wins.
OCR_ENGINE: str = os.environ.get("MANGA_OCR_ENGINE", "auto")

# Source language hint passed to the translator.  "auto" lets deep-translator
# detect the language; "ja" forces Japanese; "en" forces English.
DEFAULT_SOURCE_LANG: str = "ja"
DEFAULT_TARGET_LANG: str = "he"

# EasyOCR language codes used for the English and auto-detect backends.
EASYOCR_EN_LANGS: List[str] = ["en"]
EASYOCR_AUTO_LANGS: List[str] = ["ja", "en"]

# When the app is frozen with PyInstaller, EasyOCR's models are bundled into
# <_MEIPASS>/easyocr_model.  The OCR engine reads this env var as a fallback
# (the code also auto-detects the frozen path).  Set it in dev to point
# EasyOCR at a pre-downloaded model cache.
EASYOCR_MODEL_DIR_ENV: str = "EASYOCR_MODEL_DIR"

# --------------------------------------------------------------------------- #
# Speech-bubble / text detection parameters
# --------------------------------------------------------------------------- #
# Bubble detection (large white rounded regions)
MIN_BUBBLE_AREA: int = 1500          # ignore tiny specks
MAX_BUBBLE_AREA_FRACTION: float = 0.6  # ignore blobs larger than 60% of image
BUBBLE_CIRCULARITY_MIN: float = 0.25  # contours must be reasonably round/boxy

# Text-region detection (dark ink clusters inside the image)
MIN_TEXT_AREA: int = 80              # min pixel area for a text blob
MAX_TEXT_AREA: int = 250_000
MIN_TEXT_ASPECT: float = 0.05        # w/h lower bound (very tall text rare)
MAX_TEXT_ASPECT: float = 25.0        # w/h upper bound (very long single line)

# Horizontal dilation used to merge individual glyphs into word/line blocks.
TEXT_MERGE_KERNEL_W: int = 15
TEXT_MERGE_KERNEL_H: int = 5

# Padding (px) added around a detected text box before OCR + inpainting.
TEXT_BOX_PADDING: int = 4

# --------------------------------------------------------------------------- #
# Inpainting
# --------------------------------------------------------------------------- #
# 0 = Telea, 1 = Navier-Stokes  (cv2.inpaint)
INPAINT_FLAG: int = 1
INPAINT_RADIUS: int = 3
# When True we only erase the *dark text pixels* (mask from threshold) and
# inpaint around them, preserving the bubble outline.  When False we simply
# flood the whole bubble's text-box region with white (faster, less elegant).
INPAINT_TEXT_PIXELS_ONLY: bool = True
# Threshold above which a pixel is considered "white background" (text mask).
TEXT_MASK_THRESHOLD: int = 200

# --------------------------------------------------------------------------- #
# Hebrew text rendering
# --------------------------------------------------------------------------- #
# Base font size; the renderer auto-scales it to fit the bubble.
DEFAULT_FONT_SIZE: int = 18
MIN_FONT_SIZE: int = 9
MAX_FONT_SIZE: int = 40
# Line spacing factor relative to font size.
LINE_SPACING_FACTOR: float = 1.25
# Fraction of bubble width the text block is allowed to occupy (leaves a
# small margin on each side).
TEXT_WIDTH_FRACTION: float = 0.88
# Color of the rendered Hebrew text (near-black, matches manga ink).
TEXT_COLOR: tuple = (20, 20, 20)
# Color used to fill a bubble if inpainting is disabled.
BUBBLE_FILL_COLOR: tuple = (255, 255, 255)

# --------------------------------------------------------------------------- #
# Translation service
# --------------------------------------------------------------------------- #
# deep-translator backend name.  "google" is free and needs no API key.
TRANSLATOR_SERVICE: str = "google"
# How many seconds to wait for a single translation request.
TRANSLATOR_TIMEOUT: float = 20.0

# --------------------------------------------------------------------------- #
# Batch (folder) processing
# --------------------------------------------------------------------------- #
# Image extensions scanned when a folder is selected for batch translation.
BATCH_IMAGE_EXTS: tuple = (".png", ".jpg", ".jpeg", ".webp")
# Subfolder (inside the selected folder) where translated pages are written.
BATCH_OUTPUT_SUBFOLDER: str = "translated_pages"
# Suffix appended to each translated file name (before the extension).
BATCH_OUTPUT_SUFFIX: str = "_translated"
# If a folder contains more than this many images, warn the user before
# starting (a hard cap prevents accidental huge runs).
BATCH_HARD_CAP: int = 500
