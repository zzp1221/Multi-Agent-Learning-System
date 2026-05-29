"""Conservative slug canonicalization shared by RAG retrieval and benchmarks."""

from __future__ import annotations

import re
import unicodedata
from typing import Any


_QUOTE_CHARS = "\"'`“”‘’《》「」『』"
_PUNCTUATION_RE = re.compile(r"[\s,，、。.!！?？:：;；()\[\]{}（）【】<>《》「」『』\"'`“”‘’_-]+")


def canonicalize_slug(value: Any) -> str:
    """Return a stable key for exact slug equivalence without inventing aliases."""

    text = str(value or "").strip().strip(_QUOTE_CHARS)
    if not text or text.lower() == "none":
        return ""
    text = unicodedata.normalize("NFKC", text)
    if text.lower().startswith("wiki://"):
        text = text[7:]
    return _PUNCTUATION_RE.sub("", text.lower())


def safe_slug_key(value: Any) -> str:
    """Canonicalize only transport/format noise that should not affect identity."""

    text = str(value or "").strip().strip(_QUOTE_CHARS)
    if not text or text.lower() == "none":
        return ""
    text = unicodedata.normalize("NFKC", text)
    if text.lower().startswith("wiki://"):
        text = text[7:]
    parts = [part.strip().strip(_QUOTE_CHARS).lower() for part in text.split("/")]
    return "/".join(part for part in parts if part)


def compact_text(value: Any) -> str:
    """Normalize labels for conservative title/alias diagnostics."""

    return canonicalize_slug(value)


def slug_tail_key(value: Any) -> str:
    """Return the normalized last path segment of a slug-like value."""

    text = str(value or "").strip()
    if text.lower().startswith("wiki://"):
        text = text[7:]
    return compact_text(text.rsplit("/", 1)[-1])
