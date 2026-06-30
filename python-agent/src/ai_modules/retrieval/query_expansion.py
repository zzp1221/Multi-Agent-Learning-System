"""Query expansion primitives for local RAG retrieval."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any


@dataclass(slots=True)
class QueryExpansionResult:
    original_query: str
    topic_terms: list[str] = field(default_factory=list)
    instruction_terms: list[str] = field(default_factory=list)
    expanded_queries: list[str] = field(default_factory=list)
    term_expansions: dict[str, list[str]] = field(default_factory=dict)
    ambiguous_terms: list[str] = field(default_factory=list)
    expansion_sources: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "originalQuery": self.original_query,
            "topicTerms": self.topic_terms,
            "instructionTerms": self.instruction_terms,
            "expandedQueries": self.expanded_queries,
            "termExpansions": self.term_expansions,
            "ambiguousTerms": self.ambiguous_terms,
            "expansionSources": self.expansion_sources,
        }


def normalize_expansion_term(value: Any) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"\s+", " ", text)


def compact_expansion_term(value: Any) -> str:
    return re.sub(r"[\s_\-:/，,。.;；:：()（）\"'`]+", "", normalize_expansion_term(value))


def build_expanded_queries(
    *,
    original_query: str,
    topic_terms: list[str],
    instruction_terms: list[str],
    term_expansions: dict[str, list[str]],
    expansion_sources: list[str],
    max_queries: int = 4,
) -> QueryExpansionResult:
    query = " ".join(str(original_query or "").split())
    seen_queries = {compact_expansion_term(query)}
    expanded_queries: list[str] = [query] if query else []
    ambiguous_terms: list[str] = []

    combined_query = query
    combined_added = 0
    for term, expansions in term_expansions.items():
        clean_expansions = _dedupe_terms(expansions)
        if clean_expansions:
            combined_query = _query_with_expansion(combined_query, term, clean_expansions[0])
            combined_added += 1
    combined_key = compact_expansion_term(combined_query)
    if combined_added > 1 and combined_key and combined_key not in seen_queries:
        seen_queries.add(combined_key)
        expanded_queries.append(combined_query)

    for term, expansions in term_expansions.items():
        clean_expansions = _dedupe_terms(expansions)
        if len(clean_expansions) > 1:
            ambiguous_terms.append(term)
        for expansion in clean_expansions[:3]:
            candidate = _query_with_expansion(query, term, expansion)
            compact = compact_expansion_term(candidate)
            if not compact or compact in seen_queries:
                continue
            seen_queries.add(compact)
            expanded_queries.append(candidate)
            if len(expanded_queries) >= max_queries:
                break
        if len(expanded_queries) >= max_queries:
            break

    return QueryExpansionResult(
        original_query=query,
        topic_terms=_dedupe_terms(topic_terms),
        instruction_terms=_dedupe_terms(instruction_terms),
        expanded_queries=expanded_queries,
        term_expansions={term: _dedupe_terms(values) for term, values in term_expansions.items()},
        ambiguous_terms=_dedupe_terms(ambiguous_terms),
        expansion_sources=_dedupe_terms(expansion_sources),
    )


def _query_with_expansion(query: str, term: str, expansion: str) -> str:
    clean_term = str(term or "").strip()
    clean_expansion = str(expansion or "").strip()
    if not clean_expansion:
        return query
    if not clean_term:
        return f"{query} {clean_expansion}".strip()
    pattern = re.compile(re.escape(clean_term), flags=re.IGNORECASE)
    if pattern.search(query):
        return pattern.sub(f"{clean_term} {clean_expansion}", query, count=1)
    return f"{query} {clean_expansion}".strip()


def _dedupe_terms(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = " ".join(str(value or "").split())
        key = compact_expansion_term(text)
        if not text or not key or key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result
