"""
core/bubble_detector.py
======================
OpenCV-based detection of speech bubbles *and* the dark text regions
contained inside them.

Detection strategy (robust for typical manga pages - white bubbles with
black text on a varied background):

1.  **Bubble detection** - find large, fairly round/boxy white blobs whose
    contour is closed.  These are the speech bubbles.  We keep both the
    bounding box and the filled contour mask.

2.  **Border-ring mask** - for every detected bubble we dilate then erode
    its filled mask; the difference is a ring that covers the bubble's
    outline.  Pixels under any ring are excluded from text detection so the
    bubble border is never mistaken for text.

3.  **Text-region detection** - adaptive thresholding turns dark ink white.
    Horizontal+vertical dilation merges adjacent glyphs into word/line
    blocks; we then take their contours.  Each contour is a candidate text
    region.

4.  **Pairing** - every text region is attached to the *smallest* bubble
    that contains it (so the Hebrew text is later centred inside the right
    bubble).  Text outside any bubble is kept as a "free" region.

Exports :class:`TextRegion` and :class:`BubbleDetector`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

import cv2
import numpy as np

import config


@dataclass
class TextRegion:
    """A detected text-bearing area on the page."""

    x: int
    y: int
    w: int
    h: int

    # Optional enclosing bubble box (x, y, w, h).
    bubble: Optional[Tuple[int, int, int, int]] = None

    @property
    def text_box(self) -> Tuple[int, int, int, int]:
        """Tight box around the actual dark ink (padded)."""
        return (self.x, self.y, self.w, self.h)

    @property
    def render_box(self) -> Tuple[int, int, int, int]:
        """Box used for *rendering* the Hebrew text (bubble if present)."""
        if self.bubble is not None:
            return self.bubble
        return (self.x, self.y, self.w, self.h)

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        tag = "in-bubble" if self.bubble is not None else "free"
        return f"TextRegion({self.x},{self.y} {self.w}x{self.h} [{tag}])"


class BubbleDetector:
    """Detects speech bubbles and their contained text regions."""

    def __init__(
        self,
        min_bubble_area: int = config.MIN_BUBBLE_AREA,
        max_bubble_area_fraction: float = config.MAX_BUBBLE_AREA_FRACTION,
        circularity_min: float = config.BUBBLE_CIRCULARITY_MIN,
        min_text_area: int = config.MIN_TEXT_AREA,
        max_text_area: int = config.MAX_TEXT_AREA,
        min_text_aspect: float = config.MIN_TEXT_ASPECT,
        max_text_aspect: float = config.MAX_TEXT_ASPECT,
        merge_kernel_w: int = config.TEXT_MERGE_KERNEL_W,
        merge_kernel_h: int = config.TEXT_MERGE_KERNEL_H,
        text_padding: int = config.TEXT_BOX_PADDING,
        border_ring_radius: int = 3,
        min_ink_density: float = 0.03,
        max_ink_density: float = 0.90,
    ) -> None:
        self.min_bubble_area = min_bubble_area
        self.max_bubble_area_fraction = max_bubble_area_fraction
        self.circularity_min = circularity_min
        self.min_text_area = min_text_area
        self.max_text_area = max_text_area
        self.min_text_aspect = min_text_aspect
        self.max_text_aspect = max_text_aspect
        self.merge_kernel = cv2.getStructuringElement(
            cv2.MORPH_RECT, (merge_kernel_w, merge_kernel_h)
        )
        self.text_padding = text_padding
        self.border_ring_radius = border_ring_radius
        self.min_ink_density = min_ink_density
        self.max_ink_density = max_ink_density

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def detect(self, bgr_image: np.ndarray) -> List[TextRegion]:
        if bgr_image is None or bgr_image.size == 0:
            return []
        h, w = bgr_image.shape[:2]
        bubble_boxes, border_mask = self._detect_bubbles(bgr_image, w, h)
        text_boxes = self._detect_text_regions(bgr_image, w, h, border_mask)
        return self._pair(text_boxes, bubble_boxes)

    # ------------------------------------------------------------------ #
    # Bubble detection -> (boxes, border-ring mask)
    # ------------------------------------------------------------------ #
    def _detect_bubbles(
        self, bgr: np.ndarray, w: int, h: int
    ) -> Tuple[List[Tuple[int, int, int, int]], np.ndarray]:
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY) if bgr.ndim == 3 else bgr.copy()
        _, binary = cv2.threshold(gray, 210, 255, cv2.THRESH_BINARY)

        close_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
        binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, close_kernel, iterations=1)
        open_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, open_kernel, iterations=1)

        contours, _ = cv2.findContours(
            binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        max_area = self.max_bubble_area_fraction * (w * h)
        boxes: List[Tuple[int, int, int, int]] = []
        border_mask = np.zeros((h, w), dtype=np.uint8)
        ring_k = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (self.border_ring_radius * 2 + 1,) * 2
        )
        for c in contours:
            area = cv2.contourArea(c)
            if area < self.min_bubble_area or area > max_area:
                continue
            perim = cv2.arcLength(c, True)
            if perim <= 0:
                continue
            circularity = 4 * np.pi * area / (perim * perim)
            if circularity < self.circularity_min:
                continue
            x, y, bw, bh = cv2.boundingRect(c)
            if min(bw, bh) < 12:
                continue
            boxes.append((x, y, bw, bh))
            # Filled bubble mask (local) -> dilated minus eroded = border ring.
            tmp = np.zeros((h, w), dtype=np.uint8)
            cv2.drawContours(tmp, [c], -1, 255, thickness=cv2.FILLED)
            dilated = cv2.dilate(tmp, ring_k, iterations=1)
            eroded = cv2.erode(tmp, ring_k, iterations=1)
            ring = cv2.subtract(dilated, eroded)
            border_mask = cv2.bitwise_or(border_mask, ring)
        return boxes, border_mask

    # ------------------------------------------------------------------ #
    # Text-region detection
    # ------------------------------------------------------------------ #
    def _detect_text_regions(
        self,
        bgr: np.ndarray,
        w: int,
        h: int,
        border_mask: np.ndarray,
    ) -> List[Tuple[int, int, int, int]]:
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY) if bgr.ndim == 3 else bgr.copy()

        block = max(21, (min(w, h) // 50) | 1)
        thresh = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV, block, 12,
        )

        # Remove bubble-border pixels so the outline is never mistaken for text.
        if border_mask is not None and border_mask.any():
            thresh = cv2.bitwise_and(thresh, cv2.bitwise_not(border_mask))

        # Merge adjacent glyphs into word/line blocks.
        merged = cv2.dilate(thresh, self.merge_kernel, iterations=1)
        close_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 1))
        merged = cv2.morphologyEx(merged, cv2.MORPH_CLOSE, close_kernel, iterations=1)

        contours, _ = cv2.findContours(
            merged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        boxes: List[Tuple[int, int, int, int]] = []
        for c in contours:
            x, y, bw, bh = cv2.boundingRect(c)
            area = bw * bh
            if area < self.min_text_area or area > self.max_text_area:
                continue
            aspect = (bw / bh) if bh else 0
            if aspect < self.min_text_aspect or aspect > self.max_text_aspect:
                continue
            # Ink-density filter: reject near-empty boxes (noise/borders) and
            # near-solid boxes (large black fills, not text).
            roi = thresh[y:y + bh, x:x + bw]
            ink = float((roi > 0).sum()) / float(area) if area else 0.0
            if ink < self.min_ink_density or ink > self.max_ink_density:
                continue
            px0 = max(0, x - self.text_padding)
            py0 = max(0, y - self.text_padding)
            px1 = min(w, x + bw + self.text_padding)
            py1 = min(h, y + bh + self.text_padding)
            boxes.append((px0, py0, px1 - px0, py1 - py0))
        return boxes

    # ------------------------------------------------------------------ #
    # Pairing
    # ------------------------------------------------------------------ #
    @staticmethod
    def _contains(
        bubble: Tuple[int, int, int, int], box: Tuple[int, int, int, int]
    ) -> bool:
        bx, by, bw, bh = bubble
        tx, ty, tw, th = box
        return (
            tx >= bx - 2
            and ty >= by - 2
            and tx + tw <= bx + bw + 2
            and ty + th <= by + bh + 2
        )

    def _pair(
        self,
        text_boxes: List[Tuple[int, int, int, int]],
        bubbles: List[Tuple[int, int, int, int]],
    ) -> List[TextRegion]:
        regions: List[TextRegion] = []
        for t in text_boxes:
            best_bubble: Optional[Tuple[int, int, int, int]] = None
            best_area = float("inf")
            for b in bubbles:
                if self._contains(b, t):
                    area = b[2] * b[3]
                    if area < best_area:
                        best_area = area
                        best_bubble = b
            regions.append(TextRegion(t[0], t[1], t[2], t[3], bubble=best_bubble))
        return regions
