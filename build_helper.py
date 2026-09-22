#!/usr/bin/env python3
"""
build_helper.py
===============
Constructs and runs the `flet pack` command with a proper argv list.

Why a Python helper (and not pure CMD)?
---------------------------------------
Windows CMD cannot safely build a long command line that contains paths with
spaces / quotes (e.g. ``--add-data "C:\\Users\\...;flet_web/web"``).  The
``. was unexpected at this time.`` error in earlier builds came from nested
double-quotes inside ``set "PACK=..."`` and from ``for /f`` Python one-liners.

By doing the path discovery + argv construction in Python we sidestep every
CMD quoting issue: ``subprocess.run(argv)`` passes the list directly to the
OS with no shell, so quotes/semicolons/colons in paths are never re-parsed.

Usage:
    python build_helper.py            # full build (default)
    python build_helper.py full       # single .exe, JP+EN OCR bundled
    python build_helper.py lite       # ~190MB, no OCR bundled
    python build_helper.py dir        # portable folder, OCR bundled
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SEP = os.pathsep  # ';' on Windows, ':' on Linux/macOS  (PyInstaller's add-data separator)


# --------------------------------------------------------------------------- #
# Path discovery (each returns an absolute path or None)
# --------------------------------------------------------------------------- #
def find_flet_web_dir() -> str | None:
    """Location of the flet_web static Flutter-web assets folder."""
    try:
        import flet_web  # type: ignore
        p = os.path.join(os.path.dirname(flet_web.__file__), "web")
        return p if os.path.isdir(p) else None
    except Exception:
        return None


def find_easyocr_models() -> str | None:
    """The pre-downloaded EasyOCR detection models (~/.EasyOCR/model)."""
    p = os.path.join(os.path.expanduser("~"), ".EasyOCR", "model")
    if os.path.isdir(p) and os.listdir(p):
        return p
    return None


def find_flet_cli() -> str | None:
    """Absolute path to the `flet` CLI script, or None."""
    # `shutil.which` searches PATH; on Windows the Scripts dir is on PATH
    # after `pip install flet flet-cli`.
    return shutil.which("flet")


# --------------------------------------------------------------------------- #
# Asset verification
# --------------------------------------------------------------------------- #
def ensure_assets() -> None:
    """Generate the icon and download fonts if they are missing."""
    icon_png = os.path.join(HERE, "assets", "icon.png")
    icon_ico = os.path.join(HERE, "assets", "icon.ico")
    if not (os.path.isfile(icon_png) and os.path.isfile(icon_ico)):
        print("[assets] generating icon ...")
        subprocess.run([sys.executable, "generate_icon.py"], check=False)

    varela = os.path.join(HERE, "assets", "fonts", "VarelaRound-Regular.ttf")
    if not os.path.isfile(varela):
        print("[assets] downloading Hebrew fonts ...")
        # bash may not exist on Windows; the download script is optional.
        subprocess.run(["bash", "assets/fonts/download_font.sh"], check=False)


# --------------------------------------------------------------------------- #
# Build the flet pack argv list
# --------------------------------------------------------------------------- #
def build_argv(mode: str) -> list[str]:
    flet = find_flet_cli()
    if not flet:
        raise SystemExit(
            "[ERROR] The `flet` CLI was not found on PATH.\n"
            "        Run:  pip install flet flet-cli"
        )

    argv: list[str] = [flet, "pack", "main.py"]

    # ---- common arguments (fully quoted paths are handled by argv) ------ #
    argv += ["-i", "assets/icon.ico"]
    argv += ["-n", "MangaTranslator"]
    argv += ["--distpath", "dist"]

    # Static assets bundled into every build.
    argv += ["--add-data", f"assets/fonts{SEP}assets/fonts"]
    argv += ["--add-data", f"assets/icon.png{SEP}assets"]
    argv += ["--add-data", f"assets/icon.ico{SEP}assets"]

    # flet_web's static Flutter-web assets (so web fallback works).
    fweb = find_flet_web_dir()
    if fweb:
        argv += ["--add-data", f"{fweb}{SEP}flet_web/web"]
        print(f"[assets] bundling flet_web/web from {fweb}")
    else:
        print("[warn] flet_web/web not found - web fallback disabled")

    argv += ["--hidden-import", "arabic_reshaper"]
    argv += ["--hidden-import", "bidi"]
    argv += ["--hidden-import", "deep_translator"]
    argv += ["--product-name", "Manga Translator"]
    argv += ["--company-name", "MangaTranslator"]
    argv += ["--product-version", "1.0.0.0"]
    argv += ["--file-version", "1.0.0.0"]
    argv += ["--copyright", "Manga Translator JP/EN to HE"]

    # ---- mode-specific arguments --------------------------------------- #
    if mode == "dir":
        argv += ["-D"]  # one-folder bundle (faster startup)

    if mode == "lite":
        print("[lite] no OCR backends bundled - install manga-ocr/easyocr "
              "separately for OCR to work.")
    else:
        # full / dir : bundle manga-ocr + EasyOCR + shared PyTorch.
        argv += ["--pyinstaller-build-args"]
        for pkg in (
            "manga_ocr", "transformers", "tokenizers", "huggingface_hub",
            "safetensors", "easyocr", "scipy", "skimage",
            "torch", "torchvision", "flet_web",
        ):
            argv += ["--collect-all", pkg]

        eom = find_easyocr_models()
        if eom:
            argv += ["--add-data", f"{eom}{SEP}easyocr_model"]
            print(f"[assets] bundling EasyOCR models from {eom}")
        else:
            print("[warn] ~/.EasyOCR/model not found - the .exe will "
                  "download EasyOCR models on first run (needs internet).")

    return argv


# --------------------------------------------------------------------------- #
def main() -> int:
    mode = (sys.argv[1] if len(sys.argv) > 1 else "full").lower()
    if mode not in ("full", "lite", "dir"):
        print(f"[ERROR] unknown mode '{mode}'. Use full / lite / dir.")
        return 2

    print("=" * 60)
    print(f" Manga Translator - build helper (mode: {mode})")
    print("=" * 60)

    ensure_assets()
    argv = build_argv(mode)

    print()
    print("[run] " + " ".join(_quote(a) for a in argv))
    print()
    result = subprocess.run(argv)
    if result.returncode != 0:
        print()
        print(f"[ERROR] flet pack failed (exit {result.returncode}).")
        return result.returncode

    print()
    print("=" * 60)
    print(" Build complete!")
    out = os.path.join(HERE, "dist")
    if mode == "dir":
        print(f" Output folder: {out}\\MangaTranslator\\")
    else:
        print(f" Output exe:    {out}\\MangaTranslator.exe")
    print(" Copy it to any Windows machine and double-click.")
    print("=" * 60)
    return 0


def _quote(arg: str) -> str:
    """Pretty-print a single argv token for the log (no shell impact)."""
    if any(c in arg for c in " \t\""):
        return '"' + arg.replace('"', '\\"') + '"'
    return arg


if __name__ == "__main__":
    raise SystemExit(main())
