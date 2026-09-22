"""
core/pipeline.py
================
High-level orchestrator that runs the complete lifecycle:

    image path
        -> bubble / text detection
        -> group text regions by bubble
        -> OCR (Japanese) of every line in a group, concatenated
        -> translation (Japanese -> Hebrew) of the whole group
        -> inpainting (erase every original text box in the group)
        -> Hebrew text rendering (RTL, auto-wrapped) once per bubble/group
        -> final BGR image

A bubble often contains several lines of Japanese text.  We OCR each line
separately (better accuracy than OCR-ing the whole bubble), concatenate them
in reading order (top-to-bottom), translate the *whole* string in one call so
the translator has full context, then render the single Hebrew translation
centred inside the bubble.  Free-floating text (no enclosing bubble) is
treated as its own one-line group.

A ``progress`` callback (``Callable[[dict], None]``) is invoked at each
major step so the GUI can render a progress bar / status text without the
pipeline knowing anything about the UI.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import cv2
import numpy as np

from core.bubble_detector import BubbleDetector, TextRegion
from core.inpainter import Inpainter
from core.ocr_engine import OCREngine, make_engine
from core.text_renderer import HebrewTextRenderer
from core.translator import Translator
import config

log = logging.getLogger(__name__)

ProgressFn = Callable[[Dict], None]


@dataclass
class RegionResult:
    """Per-text-region outcome kept for the UI's translation list."""

    index: int
    text_box: Tuple[int, int, int, int]
    render_box: Tuple[int, int, int, int]
    in_bubble: bool
    ocr_text: str = ""
    translated_text: str = ""
    warning: Optional[str] = None


@dataclass
class PipelineResult:
    image: np.ndarray                 # final BGR image
    regions: List[RegionResult] = field(default_factory=list)
    original_shape: Optional[Tuple[int, int]] = None
    elapsed_seconds: float = 0.0
    saved_path: Optional[str] = None


# --------------------------------------------------------------------------- #
# Batch (folder) result types
# --------------------------------------------------------------------------- #
@dataclass
class BatchFileResult:
    """Outcome of translating a single file inside a batch run."""

    path: str
    name: str
    status: str = "ok"               # "ok" | "error" | "skipped"
    output_path: Optional[str] = None
    regions: int = 0
    elapsed: float = 0.0
    error: Optional[str] = None


@dataclass
class BatchResult:
    """Aggregate outcome of a folder batch run."""

    folder: str
    output_folder: str
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    files: List[BatchFileResult] = field(default_factory=list)
    elapsed_seconds: float = 0.0


class MangaTranslatorPipeline:
    """Runs the full image->translated-image transformation."""

    def __init__(
        self,
        detector: Optional[BubbleDetector] = None,
        ocr_engine: Optional[OCREngine] = None,
        translator: Optional[Translator] = None,
        inpainter: Optional[Inpainter] = None,
        renderer: Optional[HebrewTextRenderer] = None,
        source_lang: str = config.DEFAULT_SOURCE_LANG,
    ) -> None:
        self.detector = detector or BubbleDetector()
        self.ocr = ocr_engine or make_engine()
        self.translator = translator or Translator()
        self.inpainter = inpainter or Inpainter()
        self.renderer = renderer or HebrewTextRenderer()
        # Current source language ("ja" / "en" / "auto"); passed to every
        # ocr.recognize() call so the unified engine routes to the right
        # backend.  Updated by the GUI when the user changes the dropdown.
        self.source_lang = source_lang or config.DEFAULT_SOURCE_LANG

    # ------------------------------------------------------------------ #
    def set_source_lang(self, lang: str) -> None:
        """Set the source language used for OCR + translation."""
        lang = (lang or config.DEFAULT_SOURCE_LANG).lower()
        if lang != self.source_lang:
            self.source_lang = lang
            # Keep the translator in sync (its source affects the API call).
            self.translator.set_languages(source=lang)

    # ------------------------------------------------------------------ #
    def process(
        self,
        image_path: str,
        progress: Optional[ProgressFn] = None,
        save_path: Optional[str] = None,
    ) -> PipelineResult:
        import time

        t0 = time.time()
        self._emit(progress, stage="start",
                   message=f"Loading {os.path.basename(image_path)}")

        bgr = self._load_image(image_path)
        if bgr is None:
            raise FileNotFoundError(f"Could not load image: {image_path}")
        result = PipelineResult(image=bgr.copy(), original_shape=bgr.shape[:2])

        # ---- Step 1: detection -------------------------------------- #
        self._emit(progress, stage="detect",
                   message="Detecting speech bubbles & text")
        regions: List[TextRegion] = self.detector.detect(bgr)
        n = len(regions)
        self._emit(progress, stage="detect_done",
                   message=f"Found {n} text region(s)", total=n, current=0)
        if n == 0:
            result.elapsed_seconds = time.time() - t0
            self._emit(progress, stage="done",
                       message="No text regions detected; returning original image")
            return result

        # ---- Step 2: group regions by bubble ------------------------ #
        groups: List[List[TextRegion]] = self._group_regions(regions)
        self._emit(progress, stage="group",
                   message=f"Grouped into {len(groups)} bubble(s)/caption(s)")

        # ---- Step 3..6: per-group OCR/translate/inpaint/render ----- #
        for gi, group in enumerate(groups):
            pct = int((gi / max(1, len(groups))) * 100)
            self._emit(
                progress, stage="group",
                message=f"Processing group {gi + 1}/{len(groups)} "
                        f"({len(group)} line(s))",
                current=gi + 1, total=len(groups), percent=pct,
            )

            # Order lines top-to-bottom so the OCR text reads naturally.
            group_sorted = sorted(group, key=lambda r: (r.y, r.x))
            render_box = group_sorted[0].render_box  # bubble box or free box

            # --- OCR every line, concatenate --- #
            ocr_parts: List[str] = []
            warnings: List[str] = []
            for region in group_sorted:
                tx, ty, tw, th = region.text_box
                crop = self._safe_crop(bgr, tx, ty, tw, th)
                try:
                    txt = self.ocr.recognize(crop, source_lang=self.source_lang) if crop is not None else ""
                except Exception as exc:  # pragma: no cover
                    log.warning("OCR failed: %s", exc)
                    txt = ""
                    warnings.append(f"OCR error: {exc}")
                ocr_parts.append(txt)
            ocr_text = "\n".join(p for p in ocr_parts if p).strip()

            # --- Translate the combined text --- #
            if ocr_text:
                try:
                    he_text = self.translator.translate(ocr_text)
                except Exception as exc:  # pragma: no cover
                    log.warning("Translation failed: %s", exc)
                    he_text = ""
                    warnings.append(f"Translation error: {exc}")
            else:
                he_text = ""

            # --- Inpaint every text box in the group --- #
            for region in group_sorted:
                try:
                    bgr = self.inpainter.remove(bgr, region.text_box)
                except Exception as exc:  # pragma: no cover
                    log.warning("Inpaint failed: %s", exc)
                    warnings.append(f"Inpaint error: {exc}")

            # --- Render the single Hebrew translation once per group -- #
            if he_text:
                try:
                    bgr = self.renderer.render_on_cv_image(bgr, he_text, render_box)
                except Exception as exc:  # pragma: no cover
                    log.warning("Render failed: %s", exc)
                    warnings.append(f"Render error: {exc}")

            # Record per-line results for the UI (translation shared).
            warning_str = " | ".join(warnings) if warnings else None
            for idx, region in enumerate(group_sorted):
                result.regions.append(RegionResult(
                    index=len(result.regions),
                    text_box=region.text_box,
                    render_box=region.render_box,
                    in_bubble=region.bubble is not None,
                    ocr_text=ocr_parts[idx] if idx < len(ocr_parts) else "",
                    translated_text=he_text,
                    warning=warning_str,
                ))

            self._emit(
                progress, stage="group_done",
                message=f"Group {gi + 1}/{len(groups)} ready",
                current=gi + 1, total=len(groups),
                percent=int(((gi + 1) / len(groups)) * 100),
            )

        result.image = bgr
        result.elapsed_seconds = time.time() - t0
        if save_path:
            result.saved_path = self._save(result.image, save_path)
        self._emit(
            progress, stage="done",
            message=f"Finished in {result.elapsed_seconds:.1f}s "
                    f"({len(groups)} group(s), {n} line(s))",
            percent=100,
        )
        return result

    # ------------------------------------------------------------------ #
    # Grouping
    # ------------------------------------------------------------------ #
    @staticmethod
    def _group_regions(regions: List[TextRegion]) -> List[List[TextRegion]]:
        """
        Group text regions by their enclosing bubble.  Free regions (no
        bubble) each form their own single-element group so they are still
        processed.  Returns a list of groups (each a list of TextRegion).
        """
        groups: Dict[Optional[Tuple[int, int, int, int]], List[TextRegion]] = {}
        for r in regions:
            key = r.bubble  # may be None
            groups.setdefault(key, []).append(r)
        # Deterministic order: bubble groups first (sorted by bubble y,x),
        # then free regions sorted by y,x.
        bubble_keys = sorted(
            [k for k in groups if k is not None],
            key=lambda b: (b[1], b[0]),
        )
        free = sorted(groups.get(None, []), key=lambda r: (r.y, r.x))
        ordered: List[List[TextRegion]] = [groups[k] for k in bubble_keys]
        for r in free:
            ordered.append([r])
        return ordered

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _load_image(image_path: str) -> Optional[np.ndarray]:
        if not os.path.isfile(image_path):
            return None
        return cv2.imread(image_path, cv2.IMREAD_COLOR)

    @staticmethod
    def _safe_crop(
        bgr: np.ndarray, x: int, y: int, w: int, h: int
    ) -> Optional[np.ndarray]:
        H, W = bgr.shape[:2]
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(W, x + w), min(H, y + h)
        if x1 <= x0 or y1 <= y0:
            return None
        return bgr[y0:y1, x0:x1].copy()

    @staticmethod
    def _save(bgr: np.ndarray, path: str) -> str:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        ok = cv2.imwrite(path, bgr)
        if not ok:
            raise RuntimeError(f"Failed to write image to {path}")
        return path

    @staticmethod
    def _emit(progress: Optional[ProgressFn], **payload) -> None:
        if progress is None:
            return
        try:
            progress(payload)
        except Exception:  # pragma: no cover
            log.exception("progress callback raised; ignored")

    # ------------------------------------------------------------------ #
    # Batch (folder) processing
    # ------------------------------------------------------------------ #
    @staticmethod
    def _natural_sort_key(path: str):
        """Sort page_02 before page_10 (lexicographic sort would not)."""
        import re

        name = os.path.basename(path)
        # Split into alternating non-digit / digit chunks so numbers compare
        # numerically while text compares lexicographically.
        return [
            int(chunk) if chunk.isdigit() else chunk.lower()
            for chunk in re.split(r"(\d+)", name)
        ]

    def scan_folder(self, folder: str) -> List[str]:
        """
        Return the supported image files inside *folder* (non-recursive),
        naturally sorted so ``page_02`` precedes ``page_10``.
        """
        exts = tuple(e.lower() for e in config.BATCH_IMAGE_EXTS)
        out: List[str] = []
        if not os.path.isdir(folder):
            return out
        for name in os.listdir(folder):
            full = os.path.join(folder, name)
            if not os.path.isfile(full):
                continue
            if name.lower().endswith(exts):
                out.append(full)
        out.sort(key=self._natural_sort_key)
        return out

    def process_folder(
        self,
        folder: str,
        progress: Optional[ProgressFn] = None,
        output_subfolder: Optional[str] = None,
        output_suffix: Optional[str] = None,
    ) -> BatchResult:
        """
        Translate every supported image inside *folder* and write the results
        into ``<folder>/<output_subfolder>/`` (default ``translated_pages/``).

        Each image becomes ``<name><suffix>.png`` (default
        ``<name>_translated.png``).  The OCR model is loaded once and reused
        across all images (the OCREngine is lazy + cached).  Progress is
        emitted per file so the UI can show "Page X of Y".
        """
        import time

        t0 = time.time()
        folder = os.path.abspath(folder)
        sub = output_subfolder or config.BATCH_OUTPUT_SUBFOLDER
        suffix = output_suffix if output_suffix is not None else config.BATCH_OUTPUT_SUFFIX
        output_folder = os.path.join(folder, sub)

        images = self.scan_folder(folder)
        batch = BatchResult(folder=folder, output_folder=output_folder,
                            total=len(images))

        self._emit(progress, stage="batch_start",
                   message=f"Scanned {len(images)} image(s) in {os.path.basename(folder)}",
                   total=len(images), current=0, percent=0)

        if not images:
            os.makedirs(output_folder, exist_ok=True)
            batch.elapsed_seconds = time.time() - t0
            self._emit(progress, stage="batch_done",
                       message="No images found in the selected folder.",
                       total=0, current=0, percent=100)
            return batch

        os.makedirs(output_folder, exist_ok=True)

        for i, path in enumerate(images):
            name = os.path.basename(path)
            stem = os.path.splitext(name)[0]
            out_path = os.path.join(output_folder, f"{stem}{suffix}.png")
            self._emit(
                progress, stage="batch_file",
                message=f"Processing page {i + 1}/{len(images)}: {name}",
                current=i + 1, total=len(images),
                percent=int((i / len(images)) * 100),
                file=name, index=i,
            )

            t_file = time.time()
            try:
                res = self.process(path, save_path=out_path,
                                   progress=None)  # don't spam per-region events
                bf = BatchFileResult(
                    path=path, name=name, status="ok",
                    output_path=res.saved_path or out_path,
                    regions=len(res.regions),
                    elapsed=time.time() - t_file,
                )
                batch.succeeded += 1
            except Exception as exc:  # pragma: no cover - per-file robustness
                log.warning("Batch: failed to process %s: %s", name, exc)
                bf = BatchFileResult(
                    path=path, name=name, status="error",
                    elapsed=time.time() - t_file, error=str(exc),
                )
                batch.failed += 1
            batch.files.append(bf)

            self._emit(
                progress, stage="batch_file_done",
                message=(
                    f"Page {i + 1}/{len(images)} done "
                    f"({bf.regions} regions, {bf.elapsed:.1f}s)"
                    + (f" [ERROR: {bf.error}]" if bf.status == "error" else "")
                ),
                current=i + 1, total=len(images),
                percent=int(((i + 1) / len(images)) * 100),
                file=name, index=i, status=bf.status,
            )

        batch.elapsed_seconds = time.time() - t0
        self._emit(
            progress, stage="batch_done",
            message=(
                f"Batch complete: {batch.succeeded}/{batch.total} pages "
                f"translated in {batch.elapsed_seconds:.1f}s. "
                f"Output: {batch.output_folder}"
            ),
            current=batch.total, total=batch.total, percent=100,
            succeeded=batch.succeeded, failed=batch.failed,
            output_folder=batch.output_folder,
        )
        return batch
