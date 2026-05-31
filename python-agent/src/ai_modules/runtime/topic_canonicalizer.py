"""Shared topic normalization for memory and learner-profile signals."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CanonicalTopic:
    """A stable topic key plus the human-facing label and matched aliases."""

    canonical_key: str
    display_name: str
    aliases: list[str]


_TOPIC_GROUPS: dict[str, tuple[str, tuple[str, ...]]] = {
    "deadlock": (
        "死锁",
        (
            "deadlock",
            "dead lock",
            "死锁",
            "两个锁互相等",
            "两把锁互相等",
            "两个锁互相等待",
            "两把锁互相等待",
            "互相等锁",
            "锁互相等",
        ),
    ),
    "deadlock.circular_wait": (
        "循环等待",
        (
            "循环等待",
            "环路等待",
            "互相等待",
            "循环等",
            "循环依赖等待",
        ),
    ),
    "deadlock.mutual_exclusion": (
        "互斥条件",
        (
            "互斥条件",
            "互斥",
            "mutual exclusion",
        ),
    ),
    "deadlock.hold_and_wait": (
        "占有并等待",
        (
            "占有并等待",
            "持有并等待",
            "hold and wait",
        ),
    ),
    "deadlock.no_preemption": (
        "不可抢占",
        (
            "不可抢占",
            "不能抢占",
            "非抢占",
            "no preemption",
        ),
    ),
}

def _normalize_for_match(value: str) -> str:
    lowered = str(value or "").strip().lower()
    return re.sub(r"[\s,，。.!！?？:：;；、'\"`“”‘’（）()《》<>【】\[\]_-]+", "", lowered)


_ALIAS_INDEX: list[tuple[str, str, str, str]] = []
for canonical_key, (display_name, aliases) in _TOPIC_GROUPS.items():
    for alias in aliases:
        _ALIAS_INDEX.append((canonical_key, display_name, alias, _normalize_for_match(alias)))


def canonicalize_topic(topic: str) -> CanonicalTopic:
    """Return a stable topic key for a raw learner-facing topic string."""

    raw = str(topic or "").strip()
    if not raw:
        return CanonicalTopic(canonical_key="", display_name="", aliases=[])

    normalized = _normalize_for_match(raw)
    for canonical_key, display_name, alias, normalized_alias in _ALIAS_INDEX:
        if not normalized_alias:
            continue
        if normalized == normalized_alias or normalized_alias in normalized:
            aliases = [alias]
            if raw != alias:
                aliases.append(raw)
            return CanonicalTopic(
                canonical_key=canonical_key,
                display_name=display_name,
                aliases=_unique_non_empty(aliases),
            )

    fallback_key = _fallback_key(raw)
    return CanonicalTopic(canonical_key=fallback_key, display_name=raw, aliases=[raw])


def canonicalize_topics(topics: list[str]) -> list[CanonicalTopic]:
    """Canonicalize a list while preserving the first display label per key."""

    by_key: dict[str, CanonicalTopic] = {}
    for topic in topics:
        canonical = canonicalize_topic(topic)
        if not canonical.canonical_key:
            continue
        existing = by_key.get(canonical.canonical_key)
        if existing is None:
            by_key[canonical.canonical_key] = canonical
            continue
        by_key[canonical.canonical_key] = CanonicalTopic(
            canonical_key=existing.canonical_key,
            display_name=existing.display_name,
            aliases=_unique_non_empty([*existing.aliases, *canonical.aliases]),
        )
    return list(by_key.values())


def _fallback_key(value: str) -> str:
    normalized = _normalize_for_match(value)
    if not normalized:
        return ""
    if re.fullmatch(r"[a-z][a-z0-9_]{1,63}", normalized):
        return normalized
    ascii_slug = re.sub(r"[^a-z0-9]+", "_", normalized)
    ascii_slug = ascii_slug.strip("_")
    return ascii_slug or normalized[:64]


def _unique_non_empty(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        normalized = str(value or "").strip()
        if normalized and normalized not in result:
            result.append(normalized)
    return result
