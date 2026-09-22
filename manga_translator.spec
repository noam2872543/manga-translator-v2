# -*- mode: python ; coding: utf-8 -*-
# =====================================================================
# manga_translator.spec
# PyInstaller spec for the Manga Translator (JP/EN -> HE).
#
# Build (on a Windows machine):
#     pip install -r requirements.txt
#     pip install pyinstaller
#     pyinstaller manga_translator.spec --noconfirm
#
# Produces a one-file, no-console MangaTranslator.exe in dist\.
#
# Offline EasyOCR models:
#   Before building, warm the EasyOCR model cache by running
#       python -c "import easyocr; r=easyocr.Reader(['en']); r.readtext([[0]])"
#       python -c "import easyocr; r=easyocr.Reader(['ja','en']); r.readtext([[0]])"
#   The spec auto-detects ~/.EasyOCR/model and bundles it under
#   easyocr_model/ inside the .exe, so the user needs NO internet on
#   first run.
# =====================================================================

import os
import sys
from PyInstaller.utils.hooks import collect_all, collect_data_files

block_cipher = None

datas = []
binaries = []
hiddenimports = [
    "arabic_reshaper",
    "bidi",
    "bidi.algorithm",
    "deep_translator",
    "scipy",
    "scipy.special",
    "skimage",
    "skimage.filters",
]

# ---- Collect data/binaries/imports for heavy packages ----------------------
# manga-ocr + deps, EasyOCR + its scipy/skimage, PyTorch, and the flet_web
# static assets.  Wrapped so the spec still works even if a package is missing.
for pkg in (
    "manga_ocr", "transformers", "tokenizers", "huggingface_hub", "safetensors",
    "easyocr", "scipy", "skimage", "PIL",
    "torch", "torchvision",
    "flet", "flet_web",
):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as exc:
        print(f"[spec] collect_all({pkg}) skipped: {exc}")

# ---- Bundle EasyOCR detection models (offline portability) -----------------
# EasyOCR downloads models to ~/.EasyOCR/model on first run.  If that folder
# exists at build time (warm cache), bundle it as easyocr_model/ so the .exe
# works fully offline - the OCR engine points Reader() at this folder when
# frozen (see core/ocr_engine._frozen_easyocr_model_dir).
easyocr_home = os.path.join(os.path.expanduser("~"), ".EasyOCR", "model")
if os.path.isdir(easyocr_home) and os.listdir(easyocr_home):
    datas.append((easyocr_home, "easyocr_model"))
    print(f"[spec] bundling EasyOCR models from {easyocr_home}")
else:
    print("[spec] WARNING: ~/.EasyOCR/model not found - the .exe will "
          "download EasyOCR models on first run (needs internet).")

# ---- Bundle static assets (fonts + icon) ----------------------------------
sep = ";" if os.name == "nt" else ":"
datas += [
    ("assets/fonts", "assets/fonts"),
    ("assets/icon.png", "assets"),
    ("assets/icon.ico", "assets"),
]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "test", "unittest"],  # trim unused stdlib
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    name="MangaTranslator",
    debug=False,
    strip=False,
    upx=True,                       # compress the exe (smaller, slower start)
    upx_exclude=[],                 # add binaries to skip if UPX breaks them
    runtime_tmpdir=None,
    console=False,                  # --windowed / --noconsole (no black CMD)
    disable_windowed_traceback=False,
    icon="assets/icon.ico",
)

# To produce a *folder* bundle instead of a single .exe, comment out the EXE()
# above and uncomment the COLLECT block below.  Folder bundles start faster.
#
# exe = EXE(pyz, a.scripts, [], exclude_binaries=True,
#           name="MangaTranslator", debug=False, strip=False, upx=True,
#           console=False, icon="assets/icon.ico")
# coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas,
#                name="MangaTranslator")
