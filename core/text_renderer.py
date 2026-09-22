"""
core/text_renderer.py
=====================
Renders translated Hebrew text into an image, correctly shaped and
right-to-left, automatically wrapped to fit inside a given bounding box.

Hebrew cannot be drawn naively: the logical string stored in Python is in
logical order, but the glyphs must be painted right-to-left and the letters
of a single word must be *joined* (Hebrew letters connect to the previous
letter on the right).  Two libraries solve this:

* ``arabic_reshaper.reshape(text)``  -> re-orders/substitutes glyphs so that
  letters assume their correct presentation forms.
* ``bidi.algorithm.get_display(text)`` -> produces the visual order string
  that Pillow's ``ImageDraw.text`` should actually draw left-to-right so
  that the resulting glyphs appear right-to-left on the page.

We run both, per *line*, then greedily wrap words so the whole block fits
inside the target box.  The font size is auto-tuned so the wrapped block
fills the box nicely without overflowing.

Font selection falls back through a list of candidate TTF paths defined in
``config.FONT_CANDIDATES`` so the renderer keeps working even when no bundled
font is present.
"""

from __future__ import annotations

import logging
import math
from typing import List, Optional, Tuple

import numpy as np
import config

log = logging.getLogger(__name__)

# Heavy / optional imports - done lazily so importing the module never fails.
_arabic_reshaper = None
_get_display = None


def _ensure_bidi():
    global _arabic_reshaper, _get_display
    if _arabic_reshaper is not None:
        return
    import arabic_reshaper  # type: ignore
    from bidi.algorithm import get_display  # type: ignore
    _arabic_reshaper = arabic_reshaper
    _get_display = get_display


def shape_hebrew(text: str) -> str:
    """Return the visual-order string for a Hebrew logical string."""
    _ensure_bidi()
    reshaped = _arabic_reshaper.reshape(text)
    return _get_display(reshaped)


# --------------------------------------------------------------------------- #
class HebrewTextRenderer:
    """Draws Hebrew text into a PIL image with auto-wrapping + font fallback."""

    def __init__(
        self,
        font_candidates: Optional[List[str]] = None,
        base_size: int = config.DEFAULT_FONT_SIZE,
        min_size: int = config.MIN_FONT_SIZE,
        max_size: int = config.MAX_FONT_SIZE,
        text_color: tuple = config.TEXT_COLOR,
        line_spacing_factor: float = config.LINE_SPACING_FACTOR,
        width_fraction: float = config.TEXT_WIDTH_FRACTION,
    ) -> None:
        self.font_candidates = font_candidates or list(config.FONT_CANDIDATES)
        self.base_size = base_size
        self.min_size = min_size
        self.max_size = max_size
        self.text_color = text_color
        self.line_spacing_factor = line_spacing_factor
        self.width_fraction = width_fraction
        self._font_path: Optional[str] = None
        self._resolved: bool = False

    # ------------------------------------------------------------------ #
    # Font loading
    # ------------------------------------------------------------------ #
    def _resolve_font_path(self) -> Optional[str]:
        """Return the first existing candidate TTF path, else None."""
        for path in self.font_candidates:
            try:
                import os
                if os.path.isfile(path):
                    self._font_path = path
                    return path
            except Exception:
                continue
        self._font_path = None
        return None

    def _load_font(self, size: int):
        from PIL import ImageFont

        path = self._font_path or self._resolve_font_path()
        if path:
            try:
                return ImageFont.truetype(path, size)
            except Exception as exc:  # corrupt file etc.
                log.warning("Failed to load font %s: %s", path, exc)
        # Final fallback: PIL's default bitmap font (will NOT shape Hebrew
        # correctly, but keeps the program from crashing).
        log.warning("No Hebrew-capable TTF found; using PIL default font.")
        return ImageFont.load_default()

    # ------------------------------------------------------------------ #
    # Wrapping
    # ------------------------------------------------------------------ #
    def _wrap(self, text: str, font, max_width: int) -> List[str]:
        """
        Greedily wrap *text* (logical Hebrew) into lines whose visual order
        width fits *max_width*.
        """
        text = (text or "").strip()
        if not text:
            return []

        # Allow explicit newlines in the translation to be honoured.
        paragraphs = text.split("\n")
        lines: List[str] = []
        for para in paragraphs:
            words = para.split(" ")
            if not words:
                continue
            current: List[str] = []
            for word in words:
                candidate = " ".join(current + [word])
                visual = shape_hebrew(candidate)
                width = font.getlength(visual)
                if width <= max_width or not current:
                    current.append(word)
                else:
                    lines.append(" ".join(current))
                    current = [word]
            if current:
                lines.append(" ".join(current))
        return lines

    # ------------------------------------------------------------------ #
    # Public rendering API
    # ------------------------------------------------------------------ #
    def render_into_box(self, pil_image, text: str, box: Tuple[int, int, int, int]):
        """
        Draw *text* centred inside *box* on *pil_image* (PIL.Image).

        box = (x, y, w, h) in pixels (OpenCV image coordinates).
        Returns the list of visual lines actually drawn.
        """
        from PIL import ImageDraw

        x, y, w, h = box
        if w <= 4 or h <= 4 or not text:
            return []

        # Allow a margin around the text block.
        max_w = int(w * self.width_fraction)
        max_h = h

        # Auto-tune font size: start from a size derived from box height,
        # shrink until the wrapped block fits the box height.
        start_size = max(
            self.min_size, min(self.max_size, int(h / 2.2), self.base_size
        ))
        font = self._load_font(start_size)
        size = start_size
        lines: List[str] = []
        while size >= self.min_size:
            font = self._load_font(size)
            lines = self._wrap(text, font, max_w)
            if not lines:
                break
            line_h = int(size * self.line_spacing_factor)
            block_h = line_h * len(lines)
            if block_h <= max_h:
                break
            size -= 2
        if not lines:
            return []

        draw = ImageDraw.Draw(pil_image)
        line_h = int(size * self.line_spacing_factor)
        block_h = line_h * len(lines)

        # Find the widest visual line so we can centre horizontally.
        visual_lines = [shape_hebrew(ln) for ln in lines]
        line_widths = [font.getlength(v) for v in visual_lines]
        max_line_w = max(line_widths) if line_widths else 0

        # Centre the block inside the box.
        block_x = x + (w - max_line_w) // 2
        start_y = y + (h - block_h) // 2

        cur_y = start_y
        for vis, lw in zip(visual_lines, line_widths):
            # Centre this line within the block's horizontal extent.
            cx = block_x + int((max_line_w - lw) / 2)
            # NOTE: Pillow draws RTL strings correctly when the visual order
            # is supplied (which shape_hebrew produces).
            draw.text((cx, cur_y), vis, font=font, fill=self.text_color)
            cur_y += line_h
        return visual_lines

    # ------------------------------------------------------------------ #
    def render_on_cv_image(
        self, cv_bgr_image: np.ndarray, text: str, box: Tuple[int, int, int, int]
    ) -> np.ndarray:
        """Convenience wrapper that operates directly on an OpenCV BGR image."""
        from PIL import Image

        # BGR -> RGB, render, RGB -> BGR.
        rgb = cv_bgr_image[:, :, ::-1].copy()
        pil = Image.fromarray(rgb)
        self.render_into_box(pil, text, box)
        out = np.array(pil)[:, :, ::-1].copy()
        return out
