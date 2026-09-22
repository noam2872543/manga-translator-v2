"""
core/translator.py
==================
Thin wrapper around `deep-translator` that converts a source string
(default Japanese) into the target language (default Hebrew).

Robustness features
-------------------
* Language-code remap - Google's backend (via deep-translator) uses the
  non-standard code ``iw`` for Hebrew; callers may keep using the ISO
  ``he`` code and we transparently translate it.
* Retry with exponential backoff for transient network errors
  (Google throttles to ~5 req/s).
* Automatic fallback chain: Google -> MyMemory -> Lingva.  Each backend
  has a different endpoint, so if one is rate-limited the next is tried.
* In-memory translation cache so re-processing the same page costs nothing.
* Always returns a string (never raises) - the pipeline can keep going.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import List, Optional, Tuple

import config

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Language code mapping
# --------------------------------------------------------------------------- #
_GOOGLE_LANG_ALIASES = {
    "he": "iw", "iw": "iw",
    "zh": "zh-CN", "zh-cn": "zh-CN", "zh-tw": "zh-TW",
    "jw": "jw", "fil": "tl", "mni": "mni-Mtei",
}


def _google_code(lang: str) -> str:
    if not lang:
        return lang
    return _GOOGLE_LANG_ALIASES.get(lang.lower(), lang.lower())


# --------------------------------------------------------------------------- #
# Direct Google Translate backend (free, no library needed)
# --------------------------------------------------------------------------- #
class _DirectGoogleBackend:
    """
    Calls Google Translate's free ``translate_a/single`` endpoint directly
    via urllib (no deep-translator dependency, no API key).

    Works reliably from any machine with internet access and supports the
    Hebrew (``iw``) target code that the manga-translator needs.  Used as the
    primary backend; deep-translator's Google/MyMemory backends follow as
    fallbacks.
    """

    name = "direct_google"

    def __init__(self, source: str, target: str, timeout: float = 20.0) -> None:
        self.source = _google_code(source) if source != "auto" else "auto"
        self.target = _google_code(target)
        self.timeout = timeout

    def translate(self, text: str) -> str:
        import json
        import urllib.parse
        import urllib.request

        if not text:
            return ""
        url = (
            "https://translate.googleapis.com/translate_a/single"
            f"?client=gtx&sl={self.source}&tl={self.target}&dt=t&q="
            f"{urllib.parse.quote(text)}"
        )
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (manga-translator)"}
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        data = json.loads(raw)
        # data[0] is a list of [translated_chunk, original_chunk, ...] tuples.
        parts = [seg[0] for seg in data[0] if seg and seg[0]]
        return "".join(parts).strip()


# --------------------------------------------------------------------------- #
class Translator:
    """Japanese -> Hebrew (configurable) translator via deep-translator."""

    # Fallback chain: (backend_name).  Each entry is a fresh translator so a
    # throttled backend can be retried with a new instance next time.
    # Order matters: "direct_google" hits Google's free gtx endpoint directly
    # (no library, works reliably from anywhere) and is tried first.  The
    # deep-translator Google/MyMemory/Lingva backends follow as fallbacks.
    _BACKENDS = (
        "direct_google",
        "google",
        "mymemory",
        "lingva",
    )

    def __init__(
        self,
        service: str = config.TRANSLATOR_SERVICE,
        source_lang: str = config.DEFAULT_SOURCE_LANG,
        target_lang: str = config.DEFAULT_TARGET_LANG,
        timeout: float = config.TRANSLATOR_TIMEOUT,
        max_retries: int = 3,
        backoff: float = 0.8,
    ) -> None:
        self.service = service or "google"
        self.source_lang = source_lang or "auto"
        self.target_lang = target_lang or "he"
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff = backoff
        self._cache: dict = {}

    # ------------------------------------------------------------------ #
    def _make_backend(self, name: str):
        src = self.source_lang
        tgt = self.target_lang
        if name == "direct_google":
            return _DirectGoogleBackend(src, tgt, self.timeout)
        if name == "google":
            try:
                from deep_translator import GoogleTranslator  # type: ignore
            except ImportError:
                return None
            return GoogleTranslator(
                source=_google_code(src), target=_google_code(tgt)
            )
        if name == "mymemory":
            try:
                from deep_translator import MyMemoryTranslator  # type: ignore
            except ImportError:
                return None
            # MyMemory needs an explicit source (no "auto"); default to ja.
            return MyMemoryTranslator(
                source=(src if src != "auto" else "ja"), target=tgt
            )
        if name == "lingva":
            try:
                from deep_translator import LingvanexTranslator  # type: ignore
                return LingvanexTranslator(source=src, target=tgt)
            except (ImportError, Exception):
                return None
        return None

    # ------------------------------------------------------------------ #
    def _cache_key(self, text: str, backend: str) -> str:
        h = hashlib.md5(f"{backend}|{self.source_lang}|{self.target_lang}|{text}".encode())
        return h.hexdigest()

    # ------------------------------------------------------------------ #
    def translate(self, text: str) -> str:
        """Return the Hebrew translation of *text*, or '' on failure."""
        if not text or not text.strip():
            return ""
        cache_key = self._cache_key(text, "any")
        if cache_key in self._cache:
            return self._cache[cache_key]

        # Try the configured service first, then the fallback chain - but
        # always keep "direct_google" at the front because it's the most
        # reliable free option (no library quirks, no special rate-limit).
        preferred = self.service if self.service != "google" else "direct_google"
        order: List[str] = [preferred] + [
            b for b in self._BACKENDS if b != preferred
        ]
        last_err: Optional[str] = None
        for backend_name in order:
            result, err = self._try_backend(backend_name, text)
            if result:
                self._cache[cache_key] = result
                return result
            if err:
                last_err = err
                # Throttle avoidance: small pause before next backend.
                time.sleep(self.backoff)
        if last_err:
            log.warning("All translation backends failed for %r: %s",
                        text[:40], last_err)
        return ""

    # ------------------------------------------------------------------ #
    def _try_backend(self, name: str, text: str) -> Tuple[str, Optional[str]]:
        """Run a single backend with retries; returns (text, error)."""
        last_err: Optional[str] = None
        for attempt in range(self.max_retries):
            try:
                backend = self._make_backend(name)
                if backend is None:
                    return "", f"backend {name} unavailable"
                out = backend.translate(text)
                if out:
                    return str(out).strip(), None
                # Empty result, try again shortly.
                last_err = "empty result"
            except Exception as exc:
                last_err = f"{type(exc).__name__}: {exc}"
                log.debug("translate[%s] attempt %d failed: %s",
                          name, attempt + 1, last_err)
            time.sleep(self.backoff * (attempt + 1))
        return "", last_err or "unknown error"

    # ------------------------------------------------------------------ #
    def translate_many(self, texts) -> List[str]:
        return [self.translate(t) for t in texts]

    # ------------------------------------------------------------------ #
    def set_languages(
        self,
        source: Optional[str] = None,
        target: Optional[str] = None,
    ) -> None:
        if source and source != self.source_lang:
            self.source_lang = source
            self._cache.clear()
        if target and target != self.target_lang:
            self.target_lang = target
            self._cache.clear()
