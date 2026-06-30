"""Generic relevance scoring for public evidence display."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

HIGH_RELEVANCE_SCORE = 0.58
MEDIUM_RELEVANCE_SCORE = 0.18
EVIDENCE_STATE_HIGH = "HIGH_CONFIDENCE"
EVIDENCE_STATE_PARTIAL = "PARTIAL"
EVIDENCE_STATE_LOW = "LOW_CONFIDENCE"
EVIDENCE_STATE_EMPTY = "EMPTY"
_QUESTION_WORDS = {
    "什么",
    "是什么",
    "是",
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
_INSTRUCTION_PHRASES = {
    "帮我",
    "请",
    "请你",
    "解释一下",
    "解释",
    "说明",
    "详细说明",
    "详细解释",
    "用表格",
    "表格",
    "列表",
    "分点",
    "至少",
    "不少于",
    "不低于",
    "以内",
    "不超过",
    "控制在",
    "左右",
    "字",
    "个字",
    "字符",
    "please",
    "explain",
    "describe",
    "detail",
    "details",
    "table",
    "bullet",
    "bullets",
    "within",
    "under",
    "less",
    "least",
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
class QueryTermProfile:
    topic_terms: set[str]
    instruction_terms: set[str]


@dataclass(frozen=True)
class EvidenceSelection:
    adopted: list[Any]
    discarded_count: int
    high_confidence: list[Any] | None = None
    medium_confidence: list[Any] | None = None
    low_confidence: list[Any] | None = None
    evidence_state: str = EVIDENCE_STATE_EMPTY
    raw_candidate_count: int = 0
    adopted_high_count: int = 0
    adopted_medium_count: int = 0
    fallback_low_count: int = 0


def select_relevant_evidence(
    *,
    query: str,
    documents: list[Any],
    limit: int = 5,
    threshold: float = MEDIUM_RELEVANCE_SCORE,
    extra_terms: set[str] | None = None,
    allow_low_fallback: bool = True,
) -> EvidenceSelection:
    raw_count = len(documents)
    terms = extract_query_terms(query) | {term.lower() for term in (extra_terms or set()) if term}
    terms = {term for term in terms if term and term not in _INSTRUCTION_PHRASES}
    if not terms:
        adopted = documents[:limit]
        return EvidenceSelection(
            adopted=adopted,
            discarded_count=max(0, len(documents) - len(adopted)),
            high_confidence=adopted,
            medium_confidence=[],
            low_confidence=[],
            evidence_state=EVIDENCE_STATE_HIGH if adopted else EVIDENCE_STATE_EMPTY,
            raw_candidate_count=raw_count,
            adopted_high_count=len(adopted),
        )

    scored: list[tuple[float, int, Any]] = []
    for index, document in enumerate(documents):
        score = evidence_relevance_score(query=query, terms=terms, document=document)
        scored.append((score, index, document))

    scored.sort(key=lambda item: (-item[0], item[1]))
    high = [(score, index, doc) for score, index, doc in scored if score >= HIGH_RELEVANCE_SCORE]
    medium = [
        (score, index, doc)
        for score, index, doc in scored
        if threshold <= score < HIGH_RELEVANCE_SCORE
    ]
    low = [(score, index, doc) for score, index, doc in scored if score < threshold]

    adopted_scored = (high + medium)[:limit]
    state = EVIDENCE_STATE_HIGH if high else EVIDENCE_STATE_PARTIAL if medium else EVIDENCE_STATE_EMPTY
    if allow_low_fallback and not adopted_scored and low:
        adopted_scored = low[: min(limit, 3)]
        state = EVIDENCE_STATE_LOW

    adopted = [document for _, _, document in adopted_scored]
    adopted_ids = {id(document) for document in adopted}
    adopted_high_count = sum(1 for _, _, doc in high if id(doc) in adopted_ids)
    adopted_medium_count = sum(1 for _, _, doc in medium if id(doc) in adopted_ids)
    fallback_low_count = sum(1 for _, _, doc in low if id(doc) in adopted_ids)
    return EvidenceSelection(
        adopted=adopted,
        discarded_count=max(0, len(documents) - len(adopted)),
        high_confidence=[document for _, _, document in high],
        medium_confidence=[document for _, _, document in medium],
        low_confidence=[document for _, _, document in low],
        evidence_state=state,
        raw_candidate_count=raw_count,
        adopted_high_count=adopted_high_count,
        adopted_medium_count=adopted_medium_count,
        fallback_low_count=fallback_low_count,
    )


def evidence_relevance_score(*, query: str, terms: set[str], document: Any) -> float:
    raw_text = _document_text(document, include_evidence=False)
    text = _normalize_text(raw_text)
    compact_query = _compact_text(query)
    compact_text = _compact_text(text)
    if not text or not compact_query:
        return 0.0

    score = 0.0
    if compact_query in compact_text or compact_text in compact_query:
        score += 0.8

    acronym_terms = _document_acronyms(raw_text)
    matched_terms = {term for term in terms if term in text or term in acronym_terms}
    if matched_terms:
        score += len(matched_terms) / max(len(terms), 1)
        score += min(0.25, len(matched_terms) * 0.04)
        score += min(_document_score(document), 1.0) * 0.15
        evidence_text = _normalize_text(_first_text(document, "evidence"))
        if evidence_text:
            evidence_matches = {term for term in terms if term in evidence_text}
            score += min(0.1, len(evidence_matches) * 0.02)
    return score


def _document_acronyms(text: Any) -> set[str]:
    value = str(text or "")
    acronyms: set[str] = set()
    for word in re.findall(r"\b[A-Z][A-Za-z0-9+#.]*\b", value):
        capitals = "".join(re.findall(r"[A-Z]", word))
        if len(capitals) >= 2:
            acronyms.add(capitals.lower())
    return acronyms


def extract_query_terms(text: str) -> set[str]:
    return query_term_profile(text).topic_terms


def query_term_profile(text: str) -> QueryTermProfile:
    normalized = _strip_length_constraints(_normalize_text(text))
    terms: set[str] = set()
    instruction_terms: set[str] = set()
    for token in re.findall(r"[a-z0-9+#_.-]{2,}", normalized):
        if token in _STOP_WORDS or token in _INSTRUCTION_PHRASES:
            instruction_terms.add(token)
        else:
            terms.add(token)
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", normalized):
        cleaned_run = _remove_instruction_phrases(run)
        if not cleaned_run:
            instruction_terms.add(run)
            continue
        if cleaned_run in _QUESTION_WORDS or cleaned_run in _INSTRUCTION_PHRASES:
            instruction_terms.add(cleaned_run)
            continue
        if cleaned_run != run:
            instruction_terms.add(run.replace(cleaned_run, ""))
        terms.add(cleaned_run)
        for size in range(2, min(4, len(cleaned_run)) + 1):
            for start in range(0, len(cleaned_run) - size + 1):
                gram = cleaned_run[start : start + size]
                if gram not in _QUESTION_WORDS and gram not in _INSTRUCTION_PHRASES:
                    terms.add(gram)
                else:
                    instruction_terms.add(gram)
    return QueryTermProfile(topic_terms=terms, instruction_terms={term for term in instruction_terms if term})


def _strip_length_constraints(text: str) -> str:
    text = re.sub(
        r"(?:至少|不少于|不低于|超过|大于|多于|不超过|少于|小于|以内|内|之内|以下|控制在|within|under|at least)?\s*\d{1,5}\s*(?:字|个字|字符|words?|chars?|characters?)",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\s+", " ", text).strip()


def _remove_instruction_phrases(text: str) -> str:
    cleaned = str(text or "")
    changed = True
    while changed:
        changed = False
        for phrase in sorted(_INSTRUCTION_PHRASES | _QUESTION_WORDS, key=len, reverse=True):
            if phrase and phrase in cleaned:
                cleaned = cleaned.replace(phrase, "")
                changed = True
    return cleaned.strip()


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
