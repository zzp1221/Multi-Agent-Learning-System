"""Generic relevance gates for public evidence display."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

MIN_RELEVANCE_SCORE = 0.18
_QUESTION_WORDS = {
    "什么",
    "怎么",
    "如何",
    "为什么",
    "哪些",
    "哪个",
    "是否",
    "能否",
    "请问",
    "解释",
    "说明",
    "一下",
}
_STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "for",
    "how",
    "is",
    "of",
    "or",
    "the",
    "to",
    "what",
    "why",
}


@dataclass(frozen=True)
class EvidenceSelection:
    adopted: list[Any]
    discarded_count: int


def select_relevant_evidence(
    *,
    query: str,
    documents: list[Any],
    limit: int = 5,
    threshold: float = MIN_RELEVANCE_SCORE,
) -> EvidenceSelection:
    terms = extract_query_terms(query)
    if not terms:
        return EvidenceSelection(adopted=documents[:limit], discarded_count=max(0, len(documents) - limit))

    scored: list[tuple[float, int, Any]] = []
    discarded = 0
    for index, document in enumerate(documents):
        score = evidence_relevance_score(query=query, terms=terms, document=document)
        if score >= threshold:
            scored.append((score, index, document))
        else:
            discarded += 1

    scored.sort(key=lambda item: (-item[0], item[1]))
    adopted = [document for _, _, document in scored[:limit]]
    discarded += max(0, len(scored) - limit)
    return EvidenceSelection(adopted=adopted, discarded_count=discarded)


def evidence_relevance_score(*, query: str, terms: set[str], document: Any) -> float:
    text = _normalize_text(_document_text(document, include_evidence=False))
    compact_query = _compact_text(query)
    compact_text = _compact_text(text)
    if not text or not compact_query:
        return 0.0

    score = 0.0
    if compact_query in compact_text or compact_text in compact_query:
        score += 0.8

    matched_terms = {term for term in terms if term in text}
    if matched_terms:
        score += len(matched_terms) / max(len(terms), 1)
        score += min(0.25, len(matched_terms) * 0.04)
        score += min(_document_score(document), 1.0) * 0.15
        evidence_text = _normalize_text(_first_text(document, "evidence"))
        if evidence_text:
            evidence_matches = {term for term in terms if term in evidence_text}
            score += min(0.1, len(evidence_matches) * 0.02)
    return score


def extract_query_terms(text: str) -> set[str]:
    normalized = _normalize_text(text)
    terms: set[str] = set()
    for token in re.findall(r"[a-z0-9+#_.-]{2,}", normalized):
        if token not in _STOP_WORDS:
            terms.add(token)
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", normalized):
        if run not in _QUESTION_WORDS:
            terms.add(run)
        for size in range(2, min(4, len(run)) + 1):
            for start in range(0, len(run) - size + 1):
                gram = run[start : start + size]
                if gram not in _QUESTION_WORDS:
                    terms.add(gram)
    return terms


def evidence_title(document: Any) -> str:
    return _first_text(document, "title", "sourceTitle", "source_title", "slug", "url")


def evidence_url(document: Any) -> str:
    return _first_text(document, "url", "slug")


def evidence_channel(document: Any) -> str:
    return _first_text(document, "channel")


def _document_text(document: Any, *, include_evidence: bool = True) -> str:
    values = [
        _first_text(document, "title"),
        _first_text(document, "sourceTitle", "source_title"),
        _first_text(document, "snippet"),
        _first_text(document, "slug"),
        _first_text(document, "url"),
    ]
    if include_evidence:
        values.append(_first_text(document, "evidence"))
    return " ".join(value for value in values if value)


def _document_score(document: Any) -> float:
    value = _value(document, "score")
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def _first_text(document: Any, *keys: str) -> str:
    for key in keys:
        value = _value(document, key)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split()).strip()
    return ""


def _value(document: Any, key: str) -> Any:
    if isinstance(document, dict):
        return document.get(key)
    return getattr(document, key, None)


def _normalize_text(text: Any) -> str:
    return " ".join(str(text or "").lower().split())


def _compact_text(text: Any) -> str:
    return re.sub(r"\s+", "", str(text or "").lower())
