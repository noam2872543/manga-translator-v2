"""
core/inpainter.py
=================
Removes the original text from a speech bubble / text region so the bubble
is clean and ready to receive the Hebrew text.

Two strategies are supported (both operate on a single text region at a time):

1.  ``INPAINT_TEXT_PIXELS_ONLY=True`` (default, elegant)
    Build a binary mask of the *dark text pixels* inside the box (using
    thresholding + a little dilation so the edges are covered), then call
    ``cv2.inpaint``.  This preserves the bubble outline and surrounding art.

2.  ``INPAINT_TEXT_PIXELS_ONLY=False`` (fast, crude)
    Flood the entire box with white.  Used as a fallback or when the user
    explicitly wants the simplest behaviour.

The returned image is a *new* array (the input is never mutated in place),
so the caller can keep the original for preview/comparison.
"""

from __future__ import annotations

import logging
from typing import Tuple

import cv2
import numpy as np

import config

log = logging.getLogger(__name__)


class Inpainter:
    """Erases text pixels from an image region."""

    def __init__(
        self,
        flag: int = config.INPAINT_FLAG,
        radius: int = config.INPAINT_RADIUS,
        text_pixels_only: bool = config.INPAINT_TEXT_PIXELS_ONLY,
        mask_threshold: int = config.TEXT_MASK_THRESHOLD,
        fill_color: Tuple[int, int, int] = config.BUBBLE_FILL_COLOR,
    ) -> None:
        self.flag = flag
        self.radius = radius
        self.text_pixels_only = text_pixels_only
        self.mask_threshold = mask_threshold
        self.fill_color = tuple(int(c) for c in fill_color)

    # ------------------------------------------------------------------ #
    def remove(self, bgr_image: np.ndarray, box: Tuple[int, int, int, int]) -> np.ndarray:
        """
        Erase text inside *box* = (x, y, w, h).  Returns a new BGR image.
        """
        out = bgr_image.copy()
        x, y, w, h = box
        if w <= 1 or h <= 1:
            return out
        H, W = out.shape[:2]
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(W, x + w), min(H, y + h)
        if x1 <= x0 or y1 <= y0:
            return out

        if not self.text_pixels_only:
            # Crude white flood of the whole box.
            cv2.rectangle(out, (x0, y0), (x1, y1), self.fill_color, -1)
            return out

        # Build a text mask for the region: dark pixels -> 255.
        roi = out[y0:y1, x0:x1]
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY) if roi.ndim == 3 else roi.copy()
        _, mask_roi = cv2.threshold(
            gray, self.mask_threshold, 255, cv2.THRESH_BINARY_INV
        )
        # Dilate so the mask covers the glyph anti-aliasing halo.
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        mask_roi = cv2.dilate(mask_roi, k, iterations=1)

        # Paint the full-size mask onto an image-wide mask array.
        full_mask = np.zeros((H, W), dtype=np.uint8)
        full_mask[y0:y1, x0:x1] = mask_roi

        # If the region produced no text pixels, just fill the box white so
        # we still leave a clean bubble behind.
        if full_mask.max() == 0:
            cv2.rectangle(out, (x0, y0), (x1, y1), self.fill_color, -1)
            return out

        try:
            out = cv2.inpaint(out, full_mask, self.radius, self.flag)
        except cv2.error as exc:  # pragma: no cover - defensive
            log.warning("cv2.inpaint failed (%s); falling back to white fill.", exc)
            cv2.rectangle(out, (x0, y0), (x1, y1), self.fill_color, -1)
        return out
