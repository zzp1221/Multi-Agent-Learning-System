"""Shared source quality classification for RAG retrieval channels."""

from __future__ import annotations

import re
from typing import Any


LOW_VALUE_NONE = "none"
LOW_VALUE_HTTP = "http"
LOW_VALUE_WIKI_MIRROR = "wiki"
LOW_VALUE_VIDEO = "video"
_SQL_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def normalize_source_text(value: Any) -> str:
    return str(value or "").strip().lower()


def low_value_source_kind(slug: Any, title: Any = "") -> str | None:
    """Classify source refs that should not dominate local graph evidence."""
    normalized_slug = normalize_source_text(slug)
    normalized_title = normalize_source_text(title)
    if not normalized_slug or normalized_slug == "none":
        return LOW_VALUE_NONE
    if re.match(r"^https?://", normalized_slug):
        return LOW_VALUE_HTTP
    if normalized_slug.startswith("wiki://"):
        return LOW_VALUE_WIKI_MIRROR
    if _looks_like_video_source(normalized_slug, normalized_title):
        return LOW_VALUE_VIDEO
    return None


def low_value_penalty(slug: Any, title: Any = "") -> float:
    kind = low_value_source_kind(slug, title)
    if kind == LOW_VALUE_NONE:
        return 0.25
    if kind == LOW_VALUE_HTTP:
        return 0.35
    if kind == LOW_VALUE_WIKI_MIRROR:
        return 0.6
    if kind == LOW_VALUE_VIDEO:
        return 0.4
    return 1.0


def is_low_value_source(slug: Any, title: Any = "") -> bool:
    return low_value_source_kind(slug, title) is not None


def low_value_source_filter_sql(table_alias: str) -> str:
    """Build the SQL predicate that mirrors ``low_value_source_kind``."""
    alias = str(table_alias or "").strip()
    if not _SQL_IDENTIFIER_PATTERN.fullmatch(alias):
        raise ValueError(f"invalid SQL table alias: {table_alias!r}")
    source_ref = f"{alias}.source_ref"
    title = f"COALESCE({alias}.title, '')"
    source_text = f"(btrim({source_ref}) || ' ' || {title})"
    return f"""
                  AND {source_ref} IS NOT NULL
                  AND btrim({source_ref}) <> ''
                  AND lower(btrim({source_ref})) <> 'none'
                  AND lower(btrim({source_ref})) NOT LIKE 'wiki://%%'
                  AND lower(btrim({source_ref})) NOT LIKE 'http://%%'
                  AND lower(btrim({source_ref})) NOT LIKE 'https://%%'
                  AND lower({title}) NOT LIKE '%%视频%%'
                  AND lower({title}) NOT LIKE '%%video%%'
                  AND lower({source_text}) NOT LIKE '%%视频资源%%'
                  AND lower({source_text}) NOT LIKE '%%video%%'
"""


def _looks_like_video_source(normalized_slug: str, normalized_title: str) -> bool:
    text = f"{normalized_slug} {normalized_title}"
    video = "\u89c6\u9891"
    video_resource = "\u89c6\u9891\u8d44\u6e90"
    return video_resource in text or video in normalized_title or "video" in text
