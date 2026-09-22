"""
gui/app.py
==========
Flet 1.0 desktop UI for the Manga Translator.

Layout
------
* AppBar with the app title + a "Process" button.
* Top toolbar: source-language dropdown, target-language dropdown (Hebrew by
  default), OCR-engine dropdown, font-size slider, and an "open output
  folder" button.
* Two columns:
    Left  -> original image preview (clickable drop zone + upload button)
    Right -> processed image preview (with "Save Translated Image" button)
* A bottom panel listing every detected region with its OCR text and Hebrew
  translation, plus any per-region warnings.
* A progress bar + status line that updates live while the pipeline runs.

Threading
---------
OCR + translation are blocking and slow, so the whole pipeline runs in a
background ``threading.Thread``.  Flet's ``page.update()`` is safe to call
from any thread, so the background thread pushes updates straight to the UI
via a thread-safe helper.

Flet 1.0 notes
--------------
* ``Image.src`` accepts ``str | bytes`` directly - we pass PNG bytes.
* ``BoxFit.CONTAIN`` replaces the old ``ImageFit.CONTAIN``.
* Container no longer has drag/drop handlers; the "drop zone" is a clickable
  styled container that opens the native file picker.
"""

from __future__ import annotations

import io
import logging
import os
import threading
from typing import List, Optional

import numpy as np

try:
    import flet as ft  # type: ignore
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Flet is not installed.  Run `pip install flet` to launch the GUI."
    ) from exc

import config
from core.pipeline import MangaTranslatorPipeline, RegionResult

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Image helpers
# --------------------------------------------------------------------------- #
def _cv_to_png_bytes(bgr: np.ndarray) -> bytes:
    """Encode a BGR numpy image to PNG bytes for Flet's Image.src."""
    import cv2

    ok, buf = cv2.imencode(".png", bgr)
    if not ok:
        raise RuntimeError("Failed to encode image to PNG")
    return buf.tobytes()


def _transparent_placeholder_png() -> bytes:
    """1x1 white PNG used as the initial Image source until a real image loads."""
    from PIL import Image

    img = Image.new("RGB", (1, 1), (255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


_PLACEHOLDER = _transparent_placeholder_png()


# --------------------------------------------------------------------------- #
class MangaTranslatorApp:
    """Controller that wires the Flet page to the processing pipeline."""

    ACCEPTED_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")

    def __init__(self) -> None:
        self.page: Optional[ft.Page] = None
        self.pipeline: Optional[MangaTranslatorPipeline] = None
        self.current_path: Optional[str] = None
        self.current_result_bytes: Optional[bytes] = None
        self.current_regions: List[RegionResult] = []
        self._worker: Optional[threading.Thread] = None
        # Batch (folder) state
        self.current_folder: Optional[str] = None
        self.last_batch_result: Optional[object] = None  # BatchResult
        self._batch_worker: Optional[threading.Thread] = None
        self._last_batch_output: Optional[str] = None

    # ------------------------------------------------------------------ #
    # Page bootstrap
    # ------------------------------------------------------------------ #
    def build(self, page: ft.Page) -> None:
        self.page = page
        page.title = "Manga Translator - Japanese to Hebrew"
        page.theme_mode = ft.ThemeMode.LIGHT
        page.padding = 0
        page.scroll = ft.ScrollMode.AUTO
        page.window.width = 1280
        page.window.height = 860
        page.window.min_width = 960
        page.window.min_height = 640
        # Set the window/taskbar icon (bundled at assets/icon.png).
        icon_png = str(config.ASSETS_DIR / "icon.png")
        if os.path.isfile(icon_png):
            page.window.icon = icon_png

        self._build_toolbar()
        self._build_previews()
        self._build_results_panel()
        self._build_appbar_actions()

        page.appbar = ft.AppBar(
            leading=ft.Icon(ft.icons.Icons.AUTO_STORIES, color=ft.Colors.WHITE, size=28),
            title=ft.Text("Manga Translator", color=ft.Colors.WHITE, size=20,
                           weight=ft.FontWeight.BOLD),
            toolbar_height=56,
            bgcolor=ft.Colors.with_opacity(0.95, ft.Colors.BLACK),
            actions=self._appbar_actions,
        )

        body = ft.Column(
            controls=[
                self.toolbar,
                ft.Divider(height=1),
                self.preview_row,
                ft.Divider(height=1),
                self.results_panel,
            ],
            spacing=0,
            expand=True,
        )
        page.add(body)
        # FilePicker is desktop-only; in the web runtime fallback it renders
        # as a red "Unknown control" box, so only register it on desktop.
        if not getattr(page, "web", False):
            page.overlay.append(self.file_picker)
        page.update()

    # ------------------------------------------------------------------ #
    # AppBar actions
    # ------------------------------------------------------------------ #
    def _build_appbar_actions(self) -> None:
        self.process_btn = ft.FilledButton(
            "Process Image",
            icon=ft.icons.Icons.PLAY_ARROW,
            on_click=self.on_process_click,
            disabled=True,
            style=ft.ButtonStyle(
                bgcolor=ft.Colors.WHITE, color=ft.Colors.BLACK,
                padding=ft.Padding.symmetric(horizontal=20, vertical=10),
            ),
        )
        self.process_folder_btn = ft.FilledButton(
            "Process Folder",
            icon=ft.icons.Icons.FOLDER,
            on_click=self.on_select_folder_click,
            style=ft.ButtonStyle(
                bgcolor=ft.Colors.with_opacity(0.15, ft.Colors.WHITE),
                color=ft.Colors.WHITE,
                padding=ft.Padding.symmetric(horizontal=18, vertical=10),
            ),
        )
        self._appbar_actions = [
            self.process_folder_btn,
            ft.Container(width=8),
            self.process_btn,
            ft.Container(width=12),
        ]

    # ------------------------------------------------------------------ #
    # Toolbar
    # ------------------------------------------------------------------ #
    def _build_toolbar(self) -> None:
        self.src_lang_dd = ft.Dropdown(
            label="Source language",
            value=config.DEFAULT_SOURCE_LANG,
            width=180,
            options=[
                ft.dropdown.Option(key="ja", text="Japanese"),
                ft.dropdown.Option(key="en", text="English"),
                ft.dropdown.Option(key="auto", text="Auto-detect"),
            ],
        )
        self.tgt_lang_dd = ft.Dropdown(
            label="Target language",
            value=config.DEFAULT_TARGET_LANG,
            width=180,
            options=[
                ft.dropdown.Option(key="he", text="Hebrew (RTL)"),
                ft.dropdown.Option(key="en", text="English"),
                ft.dropdown.Option(key="ar", text="Arabic (RTL)"),
            ],
        )
        self.ocr_engine_dd = ft.Dropdown(
            label="OCR engine",
            value=config.OCR_ENGINE,
            width=170,
            options=[
                ft.dropdown.Option(key="auto", text="Auto (manga-ocr)"),
                ft.dropdown.Option(key="manga_ocr", text="manga-ocr (JP)"),
                ft.dropdown.Option(key="easyocr", text="EasyOCR (ja+en)"),
            ],
        )
        self.font_size_slider = ft.Slider(
            min=config.MIN_FONT_SIZE, max=config.MAX_FONT_SIZE,
            value=config.DEFAULT_FONT_SIZE, divisions=10,
            label="Font size: {value}",
            width=200,
        )
        self.open_output_btn = ft.TextButton(
            "Open output folder",
            icon=ft.icons.Icons.FOLDER_OPEN,
            on_click=self.on_open_output,
        )
        self.reprocess_btn = ft.TextButton(
            "Re-run with current settings",
            icon=ft.icons.Icons.REFRESH,
            on_click=lambda _: self.on_process_click(_),
            disabled=True,
        )

        self.toolbar = ft.Container(
            content=ft.Row(
                controls=[
                    self.src_lang_dd, self.tgt_lang_dd, self.ocr_engine_dd,
                    self.font_size_slider,
                    ft.VerticalDivider(width=1),
                    self.open_output_btn, self.reprocess_btn,
                ],
                wrap=True, spacing=12,
            ),
            padding=ft.Padding.symmetric(horizontal=16, vertical=10),
            bgcolor=ft.Colors.with_opacity(0.04, ft.Colors.BLACK),
        )

    # ------------------------------------------------------------------ #
    # Previews
    # ------------------------------------------------------------------ #
    def _build_previews(self) -> None:
        # Flet 1.0: FilePicker.pick_files() and get_directory_path() are async
        # methods that return the selection directly (no on_result callback).
        self.file_picker = ft.FilePicker()
        self.upload_btn = ft.FilledButton(
            "Upload image",
            icon=ft.icons.Icons.UPLOAD_FILE,
            on_click=self.on_upload_click,
            style=ft.ButtonStyle(padding=ft.Padding.symmetric(horizontal=18, vertical=12)),
        )

        # A clickable, drop-zone-styled container that also opens the picker.
        self.drop_zone = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Icon(ft.icons.Icons.IMAGE, size=56, color=ft.Colors.GREY),
                    ft.Text("Drop a manga page here (or click to browse)",
                            size=16, weight=ft.FontWeight.W_500,
                            text_align=ft.TextAlign.CENTER),
                    ft.Text("PNG / JPG / WEBP / BMP",
                            size=13, color=ft.Colors.GREY),
                    ft.Row([self.upload_btn], alignment=ft.MainAxisAlignment.CENTER),
                ],
                alignment=ft.MainAxisAlignment.CENTER,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=8,
            ),
            alignment=ft.Alignment.CENTER,
            padding=30,
            border=ft.Border.all(2, ft.Colors.with_opacity(0.3, ft.Colors.GREY)),
            border_radius=12,
            expand=True,
            ink=True,                       # hover/press feedback
            on_click=self.on_upload_click,
        )

        self.orig_image = ft.Image(
            src=_PLACEHOLDER, fit=ft.BoxFit.CONTAIN, height=420,
            border_radius=8, gapless_playback=True, visible=False,
        )
        self.result_image = ft.Image(
            src=_PLACEHOLDER, fit=ft.BoxFit.CONTAIN, height=420,
            border_radius=8, gapless_playback=True, visible=False,
        )

        self.save_btn = ft.FilledButton(
            "Save Translated Image",
            icon=ft.icons.Icons.SAVE_ALT,
            on_click=self.on_save_click,
            disabled=True,
            style=ft.ButtonStyle(
                bgcolor=ft.Colors.BLACK, color=ft.Colors.WHITE,
                padding=ft.Padding.symmetric(horizontal=18, vertical=12),
            ),
        )

        result_placeholder = ft.Container(
            content=ft.Column(
                [ft.Icon(ft.icons.Icons.PHOTO_SIZE_SELECT_ACTUAL,
                         size=56, color=ft.Colors.GREY),
                 ft.Text("Processed result will appear here",
                         size=13, color=ft.Colors.GREY)],
                alignment=ft.MainAxisAlignment.CENTER,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            alignment=ft.Alignment.CENTER,
            expand=True,
        )

        left_card = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text("Original", weight=ft.FontWeight.BOLD, size=15),
                    ft.Stack([self.drop_zone, self.orig_image], expand=True),
                ],
                spacing=8, expand=True,
            ),
            padding=ft.Padding.all(12),
            border=ft.Border.all(1, ft.Colors.with_opacity(0.15, ft.Colors.GREY)),
            border_radius=12,
            expand=True,
        )
        right_card = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[ft.Text("Translated (Hebrew)",
                                          weight=ft.FontWeight.BOLD, size=15),
                                  ft.Container(expand=True),
                                  self.save_btn],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    ft.Stack([result_placeholder, self.result_image], expand=True),
                ],
                spacing=8, expand=True,
            ),
            padding=ft.Padding.all(12),
            border=ft.Border.all(1, ft.Colors.with_opacity(0.15, ft.Colors.GREY)),
            border_radius=12,
            expand=True,
        )

        self.preview_row = ft.Row(
            controls=[left_card, right_card],
            spacing=12, expand=True,
            alignment=ft.MainAxisAlignment.SPACE_AROUND,
        )

    # ------------------------------------------------------------------ #
    # Results (per-region translation) panel
    # ------------------------------------------------------------------ #
    def _build_results_panel(self) -> None:
        self.status_text = ft.Text(
            "Ready.  Upload a manga page (single) or click Process Folder "
            "to translate a whole chapter at once.",
            size=13, color=ft.Colors.GREY,
        )
        self.progress_bar = ft.ProgressBar(value=0, visible=False, width=400)
        self.progress_ring = ft.ProgressRing(visible=False, width=24, height=24)

        # Batch summary banner (shown only after a batch run completes).
        self.open_batch_output_btn = ft.TextButton(
            "Open output folder",
            icon=ft.icons.Icons.FOLDER_OPEN,
            on_click=lambda _: self._open_folder(self._last_batch_output),
            disabled=True,
        )
        # Detailed batch summary text (set after a run).
        self.batch_summary_text = ft.Text("", size=12, selectable=True,
                                          expand=True)
        self.batch_summary = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.icons.Icons.CHECK_CIRCLE,
                            color=ft.Colors.GREEN_600, size=22),
                    ft.Text("Batch summary", size=14,
                            weight=ft.FontWeight.BOLD),
                    ft.Container(width=10),
                    self.batch_summary_text,
                    ft.Container(expand=True),
                    self.open_batch_output_btn,
                ],
                spacing=10,
                wrap=True,
            ),
            padding=ft.Padding.symmetric(horizontal=14, vertical=10),
            bgcolor=ft.Colors.with_opacity(0.08, ft.Colors.GREEN_600),
            border=ft.Border.all(1, ft.Colors.with_opacity(0.3, ft.Colors.GREEN_600)),
            border_radius=10,
            visible=False,
        )

        self.regions_list = ft.Column(spacing=6, scroll=ft.ScrollMode.AUTO)

        self.results_panel = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Icon(ft.icons.Icons.LIST_ALT, size=18),
                            ft.Text("Detected regions", weight=ft.FontWeight.W_500,
                                    size=14),
                            ft.Container(expand=True),
                            self.progress_ring,
                            self.progress_bar,
                        ],
                        spacing=10,
                    ),
                    self.batch_summary,
                    self.status_text,
                    ft.Container(
                        content=self.regions_list,
                        padding=8,
                        border=ft.Border.all(1, ft.Colors.with_opacity(0.12, ft.Colors.GREY)),
                        border_radius=8,
                        height=200,
                    ),
                ],
                spacing=6,
            ),
            padding=ft.Padding.symmetric(horizontal=16, vertical=12),
            bgcolor=ft.Colors.with_opacity(0.02, ft.Colors.BLACK),
        )

    # ------------------------------------------------------------------ #
    # Helpers: safe UI updates from any thread
    # ------------------------------------------------------------------ #
    def _ui(self, fn) -> None:
        """Run a UI mutation on the page; thread-safe via page.update()."""
        if self.page is None:
            return
        try:
            fn()
            self.page.update()
        except Exception:  # pragma: no cover
            log.exception("UI update failed")

    def _set_status(self, text: str, busy: bool = False,
                    progress: Optional[float] = None) -> None:
        def _apply():
            self.status_text.value = text
            self.status_text.color = ft.Colors.GREY
            self.progress_ring.visible = busy
            self.progress_bar.visible = progress is not None
            if progress is not None:
                self.progress_bar.value = progress
        self._ui(_apply)

    # ------------------------------------------------------------------ #
    # File handling (async pickers - Flet 1.0 returns selections directly)
    # ------------------------------------------------------------------ #
    async def on_upload_click(self, e) -> None:
        """Open the OS file picker and load a single image (Flet 1.0 async)."""
        if self._busy():
            self._set_status("Already processing; please wait...")
            return
        try:
            files = await self.file_picker.pick_files(
                dialog_title="Choose a manga page",
                allowed_extensions=["png", "jpg", "jpeg", "webp", "bmp"],
                allow_multiple=False,
            )
        except Exception as exc:
            self._set_status(f"File picker error: {exc}")
            return
        if not files:
            return  # user cancelled
        self._load_file(files[0].path)

    async def on_select_folder_click(self, e) -> None:
        """Open the OS directory picker and start a batch run (Flet 1.0 async)."""
        if self._busy():
            self._set_status("Already processing; please wait...")
            return
        try:
            folder = await self.file_picker.get_directory_path(
                dialog_title="Select a manga chapter folder",
            )
        except Exception as exc:
            self._set_status(f"Folder picker error: {exc}")
            return
        if not folder:
            return  # user cancelled
        self._start_batch(folder)

    def _busy(self) -> bool:
        return (
            (self._worker is not None and self._worker.is_alive())
            or (self._batch_worker is not None and self._batch_worker.is_alive())
        )

    def _load_file(self, path: str) -> None:
        ext = os.path.splitext(path)[1].lower()
        if ext not in self.ACCEPTED_EXTS:
            self._set_status(f"Unsupported file type: {ext}")
            return
        self.current_path = path
        try:
            import cv2

            bgr = cv2.imread(path, cv2.IMREAD_COLOR)
            if bgr is None:
                raise ValueError("OpenCV could not decode the image")
            png = _cv_to_png_bytes(bgr)
        except Exception as exc:
            self._set_status(f"Failed to load image: {exc}")
            return

        def _apply():
            self.orig_image.src = png
            self.orig_image.visible = True
            self.drop_zone.visible = False
            self.result_image.src = _PLACEHOLDER
            self.result_image.visible = False
            self.save_btn.disabled = True
            self.reprocess_btn.disabled = False
            self.process_btn.disabled = False
            self.regions_list.controls.clear()
            self.status_text.value = (
                f"Loaded {os.path.basename(path)}  "
                f"({bgr.shape[1]}x{bgr.shape[0]}).  "
                "Click Process Image to translate."
            )
            self.status_text.color = ft.Colors.GREY
            self.progress_ring.visible = False
            self.progress_bar.visible = False
        self._ui(_apply)

    # ------------------------------------------------------------------ #
    # Process
    # ------------------------------------------------------------------ #
    def on_process_click(self, e) -> None:
        if not self.current_path:
            self._set_status("Please upload an image first.")
            return
        if self._busy():
            self._set_status("Already processing; please wait...")
            return

        try:
            self._rebuild_pipeline()
        except Exception as exc:
            self._set_status(f"Failed to configure pipeline: {exc}")
            return

        self.process_btn.disabled = True
        self.process_folder_btn.disabled = True
        self.save_btn.disabled = True
        # Hide the batch summary banner when starting a single-image run.
        self.batch_summary.visible = False
        self._set_status("Starting...", busy=True, progress=0.0)

        self._worker = threading.Thread(
            target=self._run_pipeline, args=(self.current_path,),
            daemon=True,
        )
        self._worker.start()

    def _rebuild_pipeline(self) -> None:
        from core.ocr_engine import make_engine
        from core.translator import Translator
        from core.text_renderer import HebrewTextRenderer

        src = self.src_lang_dd.value or config.DEFAULT_SOURCE_LANG
        tgt = self.tgt_lang_dd.value or config.DEFAULT_TARGET_LANG
        ocr_pref = self.ocr_engine_dd.value or config.OCR_ENGINE
        font_size = int(self.font_size_slider.value or config.DEFAULT_FONT_SIZE)

        if self.pipeline is None:
            self.pipeline = MangaTranslatorPipeline(
                ocr_engine=make_engine(ocr_pref),
                translator=Translator(source_lang=src, target_lang=tgt),
                renderer=HebrewTextRenderer(base_size=font_size),
                source_lang=src,
            )
        else:
            # Dynamically apply the user's current source/target language +
            # font size without rebuilding the (already-loaded) OCR backends.
            self.pipeline.set_source_lang(src)
            self.pipeline.translator.set_languages(source=src, target=tgt)
            self.pipeline.renderer.base_size = font_size

    def _run_pipeline(self, image_path: str) -> None:
        def progress_cb(payload: dict) -> None:
            msg = payload.get("message", "")
            pct = payload.get("percent")
            if pct is not None:
                self._set_status(msg, busy=True, progress=pct / 100.0)
            else:
                self._set_status(msg, busy=True)

        try:
            result = self.pipeline.process(image_path, progress=progress_cb)
        except Exception as exc:
            self._set_status(f"Error: {exc}")
            self._ui(lambda: setattr(self.process_btn, "disabled", False))
            return

        self.current_regions = result.regions
        try:
            png = _cv_to_png_bytes(result.image)
            self.current_result_bytes = png
        except Exception as exc:
            self._set_status(f"Failed to encode result: {exc}")
            return

        def _apply():
            self.result_image.src = png
            self.result_image.visible = True
            self.save_btn.disabled = False
            self.process_btn.disabled = False
            self._populate_regions()
            n = len(result.regions)
            self.status_text.value = (
                f"Done in {result.elapsed_seconds:.1f}s "
                f"({n} region(s) processed).  "
                "Use Save Translated Image to export."
            )
            self.status_text.color = ft.Colors.GREY
            self.progress_ring.visible = False
            self.progress_bar.visible = False
        self._ui(_apply)

    # ------------------------------------------------------------------ #
    def _populate_regions(self) -> None:
        self.regions_list.controls.clear()
        if not self.current_regions:
            self.regions_list.controls.append(
                ft.Text("No regions detected.", italic=True,
                        color=ft.Colors.GREY)
            )
            return
        for r in self.current_regions:
            ocr = r.ocr_text or "(no text)"
            he = r.translated_text or "(no translation)"
            badge = "in-bubble" if r.in_bubble else "free"
            color = ft.Colors.BLUE_GREY if r.in_bubble else ft.Colors.GREY
            warning_row = (
                ft.Text(f"[!] {r.warning}", size=11, color=ft.Colors.RED_400)
                if r.warning else ft.Text("")
            )
            self.regions_list.controls.append(
                ft.Container(
                    content=ft.Column(
                        controls=[
                            ft.Row(
                                controls=[
                                    ft.Container(
                                        content=ft.Text(badge, size=10,
                                                        color=ft.Colors.WHITE),
                                        padding=ft.Padding.symmetric(horizontal=6, vertical=2),
                                        bgcolor=color, border_radius=6,
                                    ),
                                    ft.Text(f"#{r.index + 1}", size=12,
                                            weight=ft.FontWeight.BOLD),
                                    ft.Container(expand=True),
                                    warning_row,
                                ],
                            ),
                            ft.Row(
                                controls=[
                                    ft.Text("OCR:", size=12,
                                            color=ft.Colors.GREY,
                                            weight=ft.FontWeight.BOLD),
                                    ft.Text(ocr, size=12, selectable=True),
                                ],
                            ),
                            ft.Row(
                                controls=[
                                    ft.Text("HE:", size=12,
                                            color=ft.Colors.GREY,
                                            weight=ft.FontWeight.BOLD),
                                    ft.Text(he, size=13, selectable=True,
                                            weight=ft.FontWeight.W_500),
                                ],
                            ),
                        ],
                        spacing=2,
                    ),
                    padding=8,
                    border=ft.Border.all(1, ft.Colors.with_opacity(0.12, ft.Colors.GREY)),
                    border_radius=6,
                )
            )

    # ------------------------------------------------------------------ #
    # Save
    # ------------------------------------------------------------------ #
    def on_save_click(self, e) -> None:
        if not self.current_path or self.current_result_bytes is None:
            return
        base = os.path.splitext(os.path.basename(self.current_path))[0]
        out_name = f"{base}_translated.png"
        out_path = os.path.join(str(config.OUTPUT_DIR), out_name)
        try:
            os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
            with open(out_path, "wb") as f:
                f.write(self.current_result_bytes)
        except Exception as exc:
            self._set_status(f"Save failed: {exc}")
            return
        self._set_status(f"Saved to {out_path}")

    # ------------------------------------------------------------------ #
    def on_open_output(self, e) -> None:
        self._open_folder(str(config.OUTPUT_DIR))
        self._set_status(f"Output folder: {config.OUTPUT_DIR}")

    def _open_folder(self, path: Optional[str]) -> None:
        """Open a folder in the OS file manager (cross-platform)."""
        if not path:
            return
        try:
            import shutil
            import subprocess
            import sys

            os.makedirs(path, exist_ok=True)
            if sys.platform.startswith("win"):
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                opener = shutil.which("xdg-open") or shutil.which("explorer")
                if opener:
                    subprocess.Popen([opener, path])
                else:
                    return
        except Exception as exc:
            self._set_status(f"Could not open folder: {exc}")

    # ------------------------------------------------------------------ #
    # Batch (folder) processing
    # ------------------------------------------------------------------ #
    def _start_batch(self, folder: str) -> None:
        """Pre-scan the folder, then launch the batch worker thread."""
        self.current_folder = folder
        try:
            self._rebuild_pipeline()
        except Exception as exc:
            self._set_status(f"Failed to configure pipeline: {exc}")
            return

        # Pre-scan so we can warn about empty folders / huge runs up front.
        images = self.pipeline.scan_folder(folder) if self.pipeline else []
        if not images:
            self._set_status(
                f"No images (.png/.jpg/.jpeg/.webp) found in "
                f"{os.path.basename(folder)}."
            )
            return
        if len(images) > config.BATCH_HARD_CAP:
            self._set_status(
                f"Folder has {len(images)} images (hard cap "
                f"{config.BATCH_HARD_CAP}).  Subdivide the folder."
            )
            return

        # Disable action buttons + hide single-image summary while running.
        self.process_btn.disabled = True
        self.process_folder_btn.disabled = True
        self.save_btn.disabled = True
        self.batch_summary.visible = False
        self._set_status(
            f"Starting batch: {len(images)} page(s) in "
            f"{os.path.basename(folder)}...", busy=True, progress=0.0,
        )

        self._batch_worker = threading.Thread(
            target=self._run_batch, args=(folder,), daemon=True,
        )
        self._batch_worker.start()

    def _run_batch(self, folder: str) -> None:
        from core.pipeline import BatchResult

        def progress_cb(payload: dict) -> None:
            msg = payload.get("message", "")
            pct = payload.get("percent")
            stage = payload.get("stage", "")
            if stage == "batch_done":
                # Final summary handled below; just update status text.
                self._set_status(msg, busy=False, progress=1.0)
            elif pct is not None:
                self._set_status(msg, busy=True, progress=pct / 100.0)
            else:
                self._set_status(msg, busy=True)

        try:
            result: BatchResult = self.pipeline.process_folder(
                folder, progress=progress_cb
            )
        except Exception as exc:
            self._set_status(f"Batch error: {exc}")
            self._ui(lambda: setattr(self.process_btn, "disabled", False))
            self._ui(lambda: setattr(self.process_folder_btn, "disabled", False))
            return

        self.last_batch_result = result
        self._last_batch_output = result.output_folder

        def _apply():
            self._populate_batch_results(result)
            self.batch_summary_text.value = (
                f"Translated {result.succeeded}/{result.total} pages "
                f"in {result.elapsed_seconds:.1f}s"
                + (f"  ({result.failed} failed)" if result.failed else "")
                + f"   |   Output: {result.output_folder}"
            )
            self.batch_summary.visible = True
            self.open_batch_output_btn.disabled = False
            self.process_btn.disabled = False
            self.process_folder_btn.disabled = False
            self.progress_ring.visible = False
            self.progress_bar.visible = False
        self._ui(_apply)

    def _populate_batch_results(self, result) -> None:
        """Fill the per-file results list after a batch run."""
        self.regions_list.controls.clear()
        if not result.files:
            self.regions_list.controls.append(
                ft.Text("No files were processed.", italic=True,
                        color=ft.Colors.GREY)
            )
            return
        for bf in result.files:
            if bf.status == "ok":
                icon = ft.icons.Icons.CHECK_CIRCLE
                color = ft.Colors.GREEN_600
                line = (f"{bf.name}  -  {bf.regions} regions, "
                        f"{bf.elapsed:.1f}s  ->  {os.path.basename(bf.output_path)}")
            elif bf.status == "error":
                icon = ft.icons.Icons.ERROR
                color = ft.Colors.RED_600
                line = f"{bf.name}  -  ERROR: {bf.error}"
            else:
                icon = ft.icons.Icons.SKIP_NEXT
                color = ft.Colors.GREY
                line = f"{bf.name}  -  skipped"
            self.regions_list.controls.append(
                ft.Container(
                    content=ft.Row(
                        controls=[
                            ft.Icon(icon, color=color, size=18),
                            ft.Text(line, size=12, selectable=True, expand=True),
                        ],
                        spacing=8,
                    ),
                    padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                    border=ft.Border.all(
                        1, ft.Colors.with_opacity(0.1, ft.Colors.GREY)
                    ),
                    border_radius=6,
                )
            )
