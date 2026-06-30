"""Relation weighting policy for wiki graph traversal."""

from __future__ import annotations

from typing import Any


RELATION_WEIGHT = {
    "WIKILINK": 2.0,
    "SHARED_TAG": 1.0,
    "SHARED_SOURCE": 1.0,
    "COMMUNITY": 0.75,
}


def relation_base_weight(relation_type: Any) -> float:
    return RELATION_WEIGHT.get(str(relation_type or "").strip().upper(), 1.0)


def weighted_relation_score(relation_type: Any, strength: Any = 1) -> float:
    try:
        numeric_strength = float(strength or 0)
    except (TypeError, ValueError):
        numeric_strength = 0.0
    return relation_base_weight(relation_type) * numeric_strength
