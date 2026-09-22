# Manga Translator — Japanese → Hebrew

A local **desktop application** that lets you upload a manga page (PNG/JPG),
automatically detects the speech bubbles, OCRs the original Japanese text,
translates it to **Hebrew**, erases the original text, and renders the
translated Hebrew text (correctly shaped & right-to-left) back inside the
cleaned bubbles.  The final image can be saved as
`<original_filename>_translated.png`.

## Features

- **Speech-bubble + text detection** with OpenCV (adaptive threshold,
  contour filtering, bubble-border masking so the outline is never mistaken
  for text).
- **OCR** via [`manga-ocr`](https://github.com/kha-white/manga-ocr) (a model
  trained specifically on Japanese manga) with an
  [`EasyOCR`](https://github.com/JaidedAI/EasyOCR) fallback.
- **Translation** Japanese → Hebrew (or any pair) using a robust chain:
  direct Google endpoint → deep-translator Google → MyMemory → Lingva, with
  retries and an in-memory cache.
- **Hebrew RTL rendering** with `arabic-reshaper` + `python-bidi` so letters
  join and run right-to-left.  Auto-wraps to fit each bubble and auto-tunes
  the font size.
- **Text removal** via `cv2.inpaint` (preserves the bubble outline) with a
  white-fill fallback.
- **Modern Flet GUI**: dual-column preview (original | translated), file
  picker, live progress bar + ring, per-region OCR/translation list, and a
  one-click save button.
- **Asynchronous**: the whole pipeline runs in a background thread so the UI
  never freezes.

## Architecture

```
manga-translator/
├── main.py                 # Entry point - launches the Flet window
├── config.py               # All tunable parameters (paths, thresholds, ...)
├── requirements.txt
├── core/                   # Image-processing / OCR / translation logic
│   ├── bubble_detector.py  #   OpenCV bubble + text detection
│   ├── ocr_engine.py       #   manga-ocr / EasyOCR wrapper (lazy-loaded)
│   ├── translator.py       #   JP->HE translation with fallback chain
│   ├── text_renderer.py    #   Hebrew RTL rendering with PIL + bidi
│   ├── inpainter.py        #   Text removal (cv2.inpaint)
│   └── pipeline.py         #   Orchestrator: image -> translated image
├── gui/
│   └── app.py              # Flet desktop UI
├── assets/fonts/           # Hebrew-capable TTF fonts (download_font.sh)
└── output/                 # Saved translated images go here
```

The image-processing logic (`core/`) is fully decoupled from the GUI
(`gui/`) — the pipeline can be imported and used head-less in a script.

## Installation

### 1. System requirements

- Python **3.10 – 3.12**
- An internet connection (only needed the first time, to download the
  manga-ocr model and for translation requests)

### 2. Install Python dependencies

```bash
cd manga-translator
pip install -r requirements.txt
```

> **Note on the OCR backends.** Both `manga-ocr` and `easyocr` pull in
> PyTorch (~1 GB).  `manga-ocr` is the recommended engine for Japanese
> manga; `easyocr` is the fallback.  If you want a lighter install you can
> comment out one of them in `requirements.txt`, but you must keep at least
> one OCR backend for the pipeline to extract text.

### 3. Download Hebrew fonts (optional but recommended)

Three Hebrew-capable fonts are bundled automatically the first time you run
the app, but if they are missing you can fetch them manually:

```bash
bash assets/fonts/download_font.sh
```

If all downloads fail (offline machine) the renderer falls back to any
system Hebrew font it can find (e.g. DejaVuSans on most Linux distros,
Arial on Windows/macOS).

## Usage

```bash
python main.py
```

A native desktop window opens:

### Single image

1. Click **Upload image** (or the drop zone) and pick a `.png` / `.jpg`
   manga page.
2. Adjust the source/target language, OCR engine and font size in the
   toolbar if needed (defaults: Japanese → Hebrew, manga-ocr, size 18).
3. Click **Process Image** (top-right).  A progress ring + bar track the
   run; the right-hand panel shows the result live.
4. The bottom panel lists every detected region with its OCR text and Hebrew
   translation.
5. Click **Save Translated Image** to export `<name>_translated.png` into the
   `output/` folder.  **Open output folder** reveals it in your file manager.

### Batch folder (whole chapter at once)

1. Click **Process Folder** in the top app bar (left of **Process Image**).
2. Pick a folder that contains your manga pages (`.png`/`.jpg`/`.jpeg`/`.webp`).
   Pages are sorted *naturally* (`page_02` before `page_10`).
3. The status line shows **"Processing page X of Y: <filename>"** with a
   live progress bar; the UI stays fully responsive (the work runs in a
   background thread).
4. When complete a green **Batch summary** banner appears:
   `Translated 20/20 pages in 47.3s  |  Output: <folder>/translated_pages`.
   The bottom panel lists every file with its status (✓ ok / ✗ error) and
   region count.
5. Click **Open output folder** in the banner to jump straight to
   `<selected folder>/translated_pages/`, which holds each translated page
   as `<name>_translated.png`.

> The OCR model is loaded once and reused across the whole batch, so
> page 2+ are much faster than page 1.  A hard cap of 500 images per run
> (`BATCH_HARD_CAP` in `config.py`) prevents accidental huge runs.

## Pipeline (per image)

```
load image
  └── detect bubbles (white rounded blobs) + border-ring mask
  └── detect text regions (adaptive threshold + dilation)
  └── group text regions by their enclosing bubble
  └── for each group:
        ├── OCR every line  (top-to-bottom)  →  concatenated text
        ├── translate the whole string        →  Hebrew
        ├── inpaint every text box            →  clean bubble
        └── render Hebrew (shaped, RTL, wrapped, auto-sized) inside the bubble
  └── return final BGR image
```

## Configuration

All knobs live in `config.py`.  Notable ones:

| Constant | Default | Meaning |
|---|---|---|
| `OCR_ENGINE` | `"auto"` | `"manga_ocr"`, `"easyocr"`, or `"auto"` |
| `DEFAULT_SOURCE_LANG` | `"ja"` | ISO code (`ja`, `en`, `auto`) |
| `DEFAULT_TARGET_LANG` | `"he"` | ISO code (`he`, `en`, `ar`) |
| `MIN_BUBBLE_AREA` | `1500` | Ignore bubbles smaller than this (px²) |
| `TEXT_MERGE_KERNEL_W/H` | `15`/`5` | How aggressively glyphs merge into lines |
| `INPAINT_TEXT_PIXELS_ONLY` | `True` | Erase only the dark text pixels, keep the bubble art |
| `DEFAULT_FONT_SIZE` | `18` | Base Hebrew font size (auto-scaled per bubble) |
| `TEXT_WIDTH_FRACTION` | `0.88` | Fraction of bubble width the text may occupy |
| `FONT_CANDIDATES` | `[...]` | TTF paths tried in order for Hebrew rendering |
| `BATCH_IMAGE_EXTS` | `(.png,.jpg,.jpeg,.webp)` | Extensions scanned in a batch folder |
| `BATCH_OUTPUT_SUBFOLDER` | `"translated_pages"` | Where batch results are written |
| `BATCH_OUTPUT_SUFFIX` | `"_translated"` | Suffix added to each batch output name |
| `BATCH_HARD_CAP` | `500` | Max images per batch run (safety limit) |

You can also override the OCR engine at runtime with the environment
variable `MANGA_OCR_ENGINE=manga_ocr|easyocr|auto`.

## Programmatic use (no GUI)

### Single image

```python
from core.pipeline import MangaTranslatorPipeline

pipe = MangaTranslatorPipeline()
result = pipe.process("page.png", save_path="output/page_translated.png")

for r in result.regions:
    print(r.index, r.ocr_text, "->", r.translated_text)
```

### Whole folder (batch)

```python
from core.pipeline import MangaTranslatorPipeline

pipe = MangaTranslatorPipeline()

def on_progress(p):
    print(p.get("message"), f"({p.get('percent',0)}%)")

batch = pipe.process_folder("chapter_01/", progress=on_progress)
print(f"Done: {batch.succeeded}/{batch.total} pages -> {batch.output_folder}")
for f in batch.files:
    print(f"  {f.name}: {f.status} ({f.regions} regions, {f.elapsed:.1f}s)")
```

A `progress` callback (`Callable[[dict], None]`) can be passed to receive
live `stage` / `message` / `percent` updates.

## Building a standalone Windows .exe

The app ships with `build_windows.bat` and `manga_translator.spec`, both of
which produce a no-console, double-clickable Windows executable that
includes the Hebrew fonts, the app icon, and (optionally) the manga-ocr
model so the end user needs **nothing else installed**.

> ⚠️ PyInstaller cannot cross-compile, so the .exe must be built **on a
> Windows machine** (or a Windows CI runner).  Run it from the project
> folder in a Command Prompt.

### Quick build (recommended)

```bat
build_windows.bat
```

This does a **full** build: installs `requirements.txt` + PyInstaller,
verifies the fonts/icon exist, then runs `flet pack` with `--collect-all
manga_ocr transformers tokenizers huggingface_hub safetensors` so the .exe
is fully self-contained.  Result: `dist\MangaTranslator.exe` (~2 GB).

### Lighter / alternative builds

| Command | Result | Size | OCR works out-of-the-box? |
|---|---|---|---|
| `build_windows.bat` | single .exe, manga-ocr bundled | ~2 GB | ✅ |
| `build_windows.bat dir` | portable *folder* (faster start), manga-ocr bundled | ~2 GB | ✅ |
| `build_windows.bat lite` | single .exe, no OCR bundled | ~50 MB | ❌ (install manga-ocr separately) |

### Using the spec file directly

```bat
pyinstaller manga_translator.spec --noconfirm
```

The spec does the same `collect_all` calls but lets you customise excludes,
UPX, one-file-vs-folder, etc. in plain Python.

### What gets bundled

- **`assets/fonts/*.ttf`** → resolved from `sys._MEIPASS` at runtime
  (see `config._resource_dir`), so Hebrew always renders.
- **`assets/icon.ico` / `icon.png`** → the window + taskbar + .exe icon.
- **`flet_web`** static assets → so the binary works in every runtime mode.
- **`manga_ocr` + `transformers` + `tokenizers` + `huggingface_hub` +
  `safetensors`** (full build only) → so OCR runs with no model download
  prompt on first use.

### Output location when running the .exe

Single-image saves go to `output/` **next to the .exe** (writable), or to
`~/MangaTranslator/output/` if the .exe folder is read-only
(e.g. `C:\Program Files`).  Batch output always goes to
`<selected folder>/translated_pages/` (next to the source images).

## Troubleshooting

- **"No OCR backend installed"** — `manga-ocr` / `easyocr` are not installed
  (or you ran the **lite** build).  Install at least one
  (`pip install manga-ocr`) or rebuild with the full `build_windows.bat`.
- **Translation returns empty** — you are offline or all free translation
  endpoints are rate-limited.  Wait a minute and retry; the cache means
  already-translated text is free on re-runs.
- **Hebrew shows as boxes / reversed** — the renderer could not find a
  Hebrew-capable TTF.  Run `bash assets/fonts/download_font.sh`.
- **App won't open** — make sure `pip install -r requirements.txt`
  succeeded, then `python main.py`.  Logs are written to `logs/`.
- **Batch found 0 images** — the folder must contain files ending in
  `.png` / `.jpg` / `.jpeg` / `.webp` (case-insensitive).  Subfolders are
  not scanned recursively by design.
- **.exe is flagged by antivirus** — PyInstaller binaries sometimes trigger
  heuristic scanners.  Sign the executable or add an exclusion.

## License

This project is provided as-is for personal use.  The bundled fonts are
released under the SIL Open Font License (see Google Fonts).  Use of the
free Google Translate endpoint is subject to Google's terms of service.
