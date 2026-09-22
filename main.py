#!/usr/bin/env python3
"""
main.py
=======
Entry point for the Manga Translator desktop application.

Run with::

    python main.py

(Or, equivalently, ``flet run main.py`` for live-reload during development.)

The application launches a Flet desktop window.  See README.md for the full
setup (installing requirements, downloading a Hebrew font, choosing an OCR
backend).
"""

from __future__ import annotations

import logging
import os
import sys

# Make local packages importable when running the script directly.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join(LOG_DIR, "manga_translator.log"),
                             encoding="utf-8"),
    ],
)
log = logging.getLogger("manga_translator")


def main() -> int:
    log.info("Starting Manga Translator desktop app...")
    try:
        import flet as ft  # type: ignore
    except ImportError:
        log.error("Flet is not installed.  Run: pip install -r requirements.txt")
        print(
            "ERROR: Flet is not installed.\n"
            "Install dependencies with:  pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 1

    from gui.app import MangaTranslatorApp

    app = MangaTranslatorApp()

    def _main(page: ft.Page) -> None:
        app.build(page)

    try:
        ft.run(_main)  # opens a native desktop window (Flet 1.0: ft.run)
    except Exception as exc:  # pragma: no cover
        log.exception("Flet failed to start")
        print(f"ERROR launching the app: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
