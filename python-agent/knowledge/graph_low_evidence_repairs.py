"""Reproducible graph repairs derived from the phase2 low-evidence list."""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path
from typing import Any


DEFAULT_REPAIR_PATH = Path(__file__).with_name("graph_low_evidence_repairs.json")
ALLOWED_DB_RELATIONS = {"WIKILINK", "SHARED_TAG", "SHARED_SOURCE", "COMMUNITY"}
SEMANTIC_RELATION_WEIGHTS = {
    "PREREQUISITE_OF": 1.4,
    "BUILDS_ON": 1.3,
    "CROSS_LAYER_RELATION": 1.3,
    "MECHANISM_APPLICATION": 1.2,
    "COMPARISON": 1.1,
}


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
            parsed = _parse_repair_pair(pair, graph_intent=graph_intent)
            if not parsed:
                continue
            from_slug, to_slug, semantic_relation, db_relation, weight = parsed
            if not from_slug or not to_slug:
                continue
            record = {
                "from_slug": from_slug,
                "to_slug": to_slug,
                "relation": db_relation,
                "weight": weight,
                "repair_id": repair_id,
                "graph_intent": graph_intent,
            }
            if semantic_relation:
                record["semantic_relation"] = semantic_relation
            records.append(record)
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
        link = {
            "from_title": from_title,
            "to_title": to_title,
            "relation": relation,
            "weight": float(record.get("weight") or 1),
            "repair_id": record.get("repair_id"),
            "graph_intent": record.get("graph_intent"),
        }
        if record.get("semantic_relation"):
            link["semantic_relation"] = record.get("semantic_relation")
        links.append(link)
    return links


def _resolve_title(index: dict[str, str], slug: Any) -> str:
    raw = str(slug or "").strip()
    if not raw:
        return ""
    return index.get(raw) or index.get(canonical_slug_key(raw)) or ""


def _parse_repair_pair(pair: Any, *, graph_intent: str) -> tuple[str, str, str, str, float] | None:
    if isinstance(pair, list):
        if len(pair) < 2:
            return None
        from_slug = str(pair[0] or "").strip()
        to_slug = str(pair[1] or "").strip()
        raw_semantic_relation = pair[2] if len(pair) >= 3 else ""
        semantic_relation = str(raw_semantic_relation or "").strip().upper()
        db_relation = "WIKILINK"
        weight = SEMANTIC_RELATION_WEIGHTS.get(semantic_relation, 1.0)
        return from_slug, to_slug, semantic_relation, db_relation, weight
    if isinstance(pair, dict):
        from_slug = str(pair.get("from") or pair.get("from_slug") or "").strip()
        to_slug = str(pair.get("to") or pair.get("to_slug") or "").strip()
        semantic_relation = str(pair.get("semanticRelation") or pair.get("semantic_relation") or graph_intent or "WIKILINK").strip().upper()
        raw_db_relation = str(pair.get("relation") or "WIKILINK").strip().upper()
        db_relation = raw_db_relation if raw_db_relation in ALLOWED_DB_RELATIONS else "WIKILINK"
        weight = float(pair.get("weight") or SEMANTIC_RELATION_WEIGHTS.get(semantic_relation, 1.0))
        return from_slug, to_slug, semantic_relation, db_relation, weight
    return None
