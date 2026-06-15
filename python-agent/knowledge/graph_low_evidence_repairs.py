"""Reproducible graph repairs derived from the phase2 low-evidence list."""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path
from typing import Any


DEFAULT_REPAIR_PATH = Path(__file__).with_name("graph_low_evidence_repairs.json")


def canonical_slug_key(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "").strip())
    if text.lower().startswith("wiki://"):
        text = text[7:]
    return "".join(
        char.lower()
        for char in text
        if not unicodedata.category(char).startswith(("P", "Z"))
    )


def load_repair_link_records(path: Path | str = DEFAULT_REPAIR_PATH) -> list[dict[str, Any]]:
    target = Path(path)
    if not target.exists():
        return []
    payload = json.loads(target.read_text(encoding="utf-8"))
    repairs = payload.get("repairs", []) if isinstance(payload, dict) else []
    records: list[dict[str, Any]] = []
    for item in repairs:
        if not isinstance(item, dict):
            continue
        repair_id = str(item.get("id") or "").strip()
        graph_intent = str(item.get("graphIntent") or "").strip()
        for pair in item.get("links", []):
            if not isinstance(pair, list) or len(pair) != 2:
                continue
            from_slug = str(pair[0] or "").strip()
            to_slug = str(pair[1] or "").strip()
            if not from_slug or not to_slug:
                continue
            records.append(
                {
                    "from_slug": from_slug,
                    "to_slug": to_slug,
                    "relation": "WIKILINK",
                    "weight": 1,
                    "repair_id": repair_id,
                    "graph_intent": graph_intent,
                }
            )
    return records


def build_repair_wikilinks(
    repair_records: list[dict[str, Any]],
    *,
    pages: list[dict[str, Any]] | None = None,
    slug_to_title: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    index = {}
    for slug, title in (slug_to_title or {}).items():
        slug_text = str(slug or "").strip()
        title_text = str(title or "").strip()
        if not slug_text or not title_text:
            continue
        index[slug_text] = title_text
        index[canonical_slug_key(slug_text)] = title_text
    if pages:
        for page in pages:
            slug = str(page.get("slug") or "").strip()
            title = str(page.get("title") or "").strip()
            if not slug or not title:
                continue
            index[slug] = title
            index[canonical_slug_key(slug)] = title

    links: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for record in repair_records:
        from_title = _resolve_title(index, record.get("from_slug"))
        to_title = _resolve_title(index, record.get("to_slug"))
        relation = str(record.get("relation") or "WIKILINK").strip() or "WIKILINK"
        if not from_title or not to_title or from_title == to_title:
            continue
        key = (from_title, to_title, relation)
        if key in seen:
            continue
        seen.add(key)
        links.append(
            {
                "from_title": from_title,
                "to_title": to_title,
                "relation": relation,
                "weight": int(record.get("weight") or 1),
                "repair_id": record.get("repair_id"),
                "graph_intent": record.get("graph_intent"),
            }
        )
    return links


def _resolve_title(index: dict[str, str], slug: Any) -> str:
    raw = str(slug or "").strip()
    if not raw:
        return ""
    return index.get(raw) or index.get(canonical_slug_key(raw)) or ""
