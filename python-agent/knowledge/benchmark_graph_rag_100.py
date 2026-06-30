"""Benchmark GraphRAG-lite evidence coverage on the fixed 100-question set.

This runner keeps the legacy RAG judge fields from ``benchmark_rag_100`` while
adding graph evidence metrics grouped by ``graphIntent``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from knowledge.benchmark_rag_100 import (  # noqa: E402
    DEFAULT_EMBEDDING_CACHE_TTL_DAYS,
    DEFAULT_JUDGE_MAX_ATTEMPTS,
    DEFAULT_JUDGE_RETRY_BASE_SECONDS,
    JUDGE_CACHE_VERSION,
    RUNTIME_CONFIG,
    TOP_K,
    QueryEmbeddingCache,
    _judge_with_retries,
    _json_hash,
    _load_judge_cache,
    _read_json,
    _save_judge_cache,
    _top_candidates,
    _write_json,
    normalize_judge_payload,
    percentile_ms,
    run_retrieval,
    LLMRetrievalJudge,
    summarize_records,
)
from retrieval.slug_canonicalizer import canonicalize_slug, compact_text, slug_tail_key  # noqa: E402
from src.ai_modules.retrieval import QueryClassifier  # noqa: E402

DEFAULT_QUESTIONS = PROJECT_ROOT / "reports" / "graph_rag_100_questions.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "reports" / "graph_rag_100_current.json"
DEFAULT_CACHE = PROJECT_ROOT / "reports" / "graph_rag_100_judge_cache.json"
DEFAULT_GRAPH_EMBEDDING_CACHE = PROJECT_ROOT / "reports" / "graph_rag_100_embedding_cache.json"
GRAPH_METRIC_VERSION = "graph-rag-lite-metrics-v1"
GRAPH_JUDGE_CACHE_VERSION = f"{JUDGE_CACHE_VERSION}:graph-v1"
INTENT_MODE_ORACLE = "oracle"
INTENT_MODE_CLASSIFIER = "classifier"
GRAPH_HIT_AT3_MIN_PCT = 93.0
GRAPH_AVG_LATENCY_MAX_MS = 1956.40
GRAPH_P95_LATENCY_MAX_MS = 3730.16
GRAPH_PRIMARY_TOP5_MIN_PCT = 95.0
GRAPH_COMPLETE_EVIDENCE_TOP5_MIN_PCT = 60.0
GRAPH_EVIDENCE_RECALL_TOP5_MIN_PCT = 85.0


def _normalize_slug(value: Any) -> str:
    return canonicalize_slug(value)


def _top_slugs(candidates: list[dict[str, Any]], limit: int) -> set[str]:
    return {_normalize_slug(item.get("slug")) for item in candidates[:limit]}


def _equivalent_evidence_nodes(expected_slug: str, candidate: dict[str, Any]) -> set[str]:
    candidate_slug = _normalize_slug(candidate.get("slug"))
    if not expected_slug or not candidate_slug:
        return set()
    if expected_slug == candidate_slug:
        return {expected_slug}
    expected_tail = slug_tail_key(expected_slug)
    candidate_tail = slug_tail_key(candidate.get("slug"))
    candidate_title = compact_text(candidate.get("title"))
    if expected_tail and (expected_tail == candidate_tail or expected_tail == candidate_title):
        return {expected_slug}
    if _is_competing_equivalent_label(expected_tail, candidate_tail, candidate_title):
        return {expected_slug}
    return set()


def _is_competing_equivalent_label(expected_tail: str, *candidate_labels: str) -> bool:
    if len(expected_tail) < 4:
        return False
    for label in candidate_labels:
        if len(label) < 4:
            continue
        if label in expected_tail:
            return True
    return False


def evaluate_graph_evidence(question_item: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    primary_slug = _normalize_slug(question_item.get("expectedSlug"))
    related_slugs = {
        _normalize_slug(slug)
        for slug in question_item.get("expectedRelatedSlugs", [])
        if _normalize_slug(slug)
    }
    expected_nodes = {primary_slug, *related_slugs} - {""}
    top3 = _top_slugs(candidates, 3)
    top5 = _top_slugs(candidates, TOP_K)
    present_top3 = set(expected_nodes & top3)
    present_top5 = set(expected_nodes & top5)
    for expected_slug in expected_nodes:
        for candidate in candidates[:TOP_K]:
            present_top5.update(_equivalent_evidence_nodes(expected_slug, candidate))
        for candidate in candidates[:3]:
            present_top3.update(_equivalent_evidence_nodes(expected_slug, candidate))
    missing_top5 = expected_nodes - present_top5

    return {
        "graphIntent": question_item.get("graphIntent"),
        "primaryTop5": primary_slug in present_top5,
        "anyRelatedTop5": bool(related_slugs & present_top5),
        "partialEvidenceTop5": bool(present_top5),
        "completeEvidenceTop5": expected_nodes <= present_top5 if expected_nodes else False,
        "evidenceNodeRecallTop5": round(len(present_top5) / len(expected_nodes), 4) if expected_nodes else 0.0,
        "primaryTop3": primary_slug in present_top3,
        "anyRelatedTop3": bool(related_slugs & present_top3),
        "presentEvidenceNodesTop5": len(present_top5),
        "expectedEvidenceNodes": len(expected_nodes),
        "missingEvidenceSlugsTop5": sorted(missing_top5),
    }


def _pct(numerator: int | float, denominator: int | float) -> float:
    return round(numerator / denominator * 100, 2) if denominator else 0.0


def summarize_graph_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(records)
    present_nodes = sum(int(item["graphMetrics"]["presentEvidenceNodesTop5"]) for item in records)
    expected_nodes = sum(int(item["graphMetrics"]["expectedEvidenceNodes"]) for item in records)
    recall_values = [float(item["graphMetrics"]["evidenceNodeRecallTop5"]) for item in records]

    counts = {
        "total": total,
        "primaryTop5": sum(1 for item in records if item["graphMetrics"]["primaryTop5"]),
        "anyRelatedTop5": sum(1 for item in records if item["graphMetrics"]["anyRelatedTop5"]),
        "partialEvidenceTop5": sum(1 for item in records if item["graphMetrics"]["partialEvidenceTop5"]),
        "completeEvidenceTop5": sum(1 for item in records if item["graphMetrics"]["completeEvidenceTop5"]),
        "presentEvidenceNodesTop5": present_nodes,
        "expectedEvidenceNodes": expected_nodes,
    }
    return {
        "primaryTop5Pct": _pct(counts["primaryTop5"], total),
        "anyRelatedTop5Pct": _pct(counts["anyRelatedTop5"], total),
        "partialEvidenceTop5Pct": _pct(counts["partialEvidenceTop5"], total),
        "completeEvidenceTop5Pct": _pct(counts["completeEvidenceTop5"], total),
        "evidenceNodeRecallTop5Pct": _pct(present_nodes, expected_nodes),
        "avgEvidenceNodeRecallTop5": round(statistics.mean(recall_values), 4) if recall_values else 0.0,
        "counts": counts,
    }


def summarize_graph_quality_gates(
    summary: dict[str, Any],
    graph_summary: dict[str, Any],
    *,
    thresholds: dict[str, float] | None = None,
) -> dict[str, Any]:
    gate_thresholds = {
        "hitAt3Pct": GRAPH_HIT_AT3_MIN_PCT,
        "avgLatencyMs": GRAPH_AVG_LATENCY_MAX_MS,
        "p95LatencyMs": GRAPH_P95_LATENCY_MAX_MS,
        "primaryTop5Pct": GRAPH_PRIMARY_TOP5_MIN_PCT,
        "completeEvidenceTop5Pct": GRAPH_COMPLETE_EVIDENCE_TOP5_MIN_PCT,
        "evidenceNodeRecallTop5Pct": GRAPH_EVIDENCE_RECALL_TOP5_MIN_PCT,
        "channelErrorCount": 0,
    }
    if thresholds:
        gate_thresholds.update({key: float(value) for key, value in thresholds.items() if key in gate_thresholds})

    pass_hit_at3 = float(summary.get("hitAt3Pct") or 0.0) >= gate_thresholds["hitAt3Pct"]
    pass_latency = (
        float(summary.get("avgLatencyMs") or 0.0) <= gate_thresholds["avgLatencyMs"]
        and float(summary.get("p95LatencyMs") or 0.0) <= gate_thresholds["p95LatencyMs"]
    )
    pass_channel_errors = int(summary.get("channelErrorCount") or 0) <= int(gate_thresholds["channelErrorCount"])
    pass_primary_top5 = float(graph_summary.get("primaryTop5Pct") or 0.0) >= gate_thresholds["primaryTop5Pct"]
    pass_evidence_recall = (
        float(graph_summary.get("evidenceNodeRecallTop5Pct") or 0.0) >= gate_thresholds["evidenceNodeRecallTop5Pct"]
    )
    pass_complete_evidence = (
        float(graph_summary.get("completeEvidenceTop5Pct") or 0.0) >= gate_thresholds["completeEvidenceTop5Pct"]
    )
    return {
        "passHitAt3": pass_hit_at3,
        "passLatency": pass_latency,
        "passChannelErrors": pass_channel_errors,
        "passPrimaryTop5": pass_primary_top5,
        "passEvidenceRecall": pass_evidence_recall,
        "passCompleteEvidence": pass_complete_evidence,
        "overallPass": all(
            [
                pass_hit_at3,
                pass_latency,
                pass_channel_errors,
                pass_primary_top5,
                pass_evidence_recall,
                pass_complete_evidence,
            ]
        ),
        "thresholds": gate_thresholds,
    }


def summarize_by_graph_intent(records: list[dict[str, Any]]) -> dict[str, Any]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        intent = str(record.get("graphIntent") or "UNKNOWN")
        buckets.setdefault(intent, []).append(record)

    return {
        intent: summarize_graph_records(items)
        for intent, items in sorted(buckets.items())
    }


def classify_low_evidence_reasons(record: dict[str, Any]) -> dict[str, bool]:
    missing = {
        _normalize_slug(slug)
        for slug in record.get("graphMetrics", {}).get("missingEvidenceSlugsTop5", [])
        if _normalize_slug(slug)
    }
    diagnostics = record.get("diagnostics", {})
    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    graph_explain = diagnostics.get("graphCandidateExplainTop50", {})
    graph_explain = graph_explain if isinstance(graph_explain, dict) else {}
    channels = diagnostics.get("channelsTopN", {})
    channels = channels if isinstance(channels, dict) else {}
    alias_diagnostics = record.get("aliasDiagnostics", {})
    alias_diagnostics = alias_diagnostics if isinstance(alias_diagnostics, dict) else {}

    top_slugs = {_normalize_slug(item.get("slug")) for item in record.get("top", []) if isinstance(item, dict)}
    seed_slugs = {_normalize_slug(slug) for slug in graph_explain.get("seedSlugs", [])}
    candidate_slugs = {
        _normalize_slug(item.get("slug"))
        for item in graph_explain.get("candidates", [])
        if isinstance(item, dict)
    }
    graph_channel_slugs = {
        _normalize_slug(item.get("slug"))
        for item in channels.get("graph", [])
        if isinstance(item, dict)
    }
    direct_evidence = diagnostics.get("prerequisiteEvidence", {})
    direct_evidence = direct_evidence if isinstance(direct_evidence, dict) else {}
    direct_slugs = {
        _normalize_slug(item.get("slug"))
        for item in direct_evidence.get("directEvidenceCandidatesTopN", [])
        if isinstance(item, dict)
    }
    found_graph_slugs = seed_slugs | candidate_slugs | graph_channel_slugs | direct_slugs

    missing_alias = any(
        isinstance(item, dict)
        and (
            item.get("likelyFalseNegative") is True
            or bool(item.get("possibleTop5Matches"))
        )
        for item in alias_diagnostics.values()
    )
    resource_slug_competing = _has_resource_slug_competition(
        missing_slugs=missing,
        top_slugs=top_slugs,
        channels=channels,
        diagnostics=diagnostics,
    )
    classifier_mismatch = bool(record.get("graphIntent") != record.get("classifierGraphIntent"))
    missing_graph_edge = bool(missing - found_graph_slugs) and not missing_alias
    return {
        "missingAlias": missing_alias,
        "missingGraphEdge": missing_graph_edge,
        "resourceSlugCompeting": resource_slug_competing,
        "classifierMismatch": classifier_mismatch,
    }


def _has_resource_slug_competition(
    *,
    missing_slugs: set[str],
    top_slugs: set[str],
    channels: dict[str, Any],
    diagnostics: dict[str, Any],
) -> bool:
    candidate_items = []
    for channel_name in ("vector", "grepPriority", "grepNormal", "graph"):
        values = channels.get(channel_name, [])
        if isinstance(values, list):
            candidate_items.extend(item for item in values if isinstance(item, dict))
    candidate_items.extend(
        item
        for item in diagnostics.get("lowValueSources", {}).get("items", [])
        if isinstance(item, dict)
    )
    for item in candidate_items:
        raw_slug = str(item.get("slug") or "")
        normalized = _normalize_slug(raw_slug)
        kind = str(item.get("kind") or "")
        if raw_slug.lower().startswith(("wiki://", "http://", "https://")) and (
            normalized in missing_slugs or normalized in top_slugs
        ):
            return True
        if kind in {"wiki", "http", "video"} and normalized in top_slugs:
            return True
    return False


def _low_evidence_summary_item(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": record.get("id"),
        "graphIntent": record.get("graphIntent"),
        "classifierGraphIntent": record.get("classifierGraphIntent"),
        "retrievalGraphIntent": record.get("retrievalGraphIntent"),
        "evidenceNodeRecallTop5": record["graphMetrics"]["evidenceNodeRecallTop5"],
        "missingEvidenceSlugsTop5": record["graphMetrics"].get("missingEvidenceSlugsTop5", []),
        "topSlugs": [candidate.get("slug") for candidate in record.get("top", [])],
        "reasonCandidates": classify_low_evidence_reasons(record),
    }


def _sorted_low_evidence_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    low_records = [
        record
        for record in records
        if not record["graphMetrics"]["completeEvidenceTop5"]
    ]
    low_records.sort(
        key=lambda record: (
            float(record["graphMetrics"]["evidenceNodeRecallTop5"]),
            0 if record.get("graphIntent") == "PREREQUISITE_PATH" else 1,
            str(record.get("id") or ""),
        )
    )
    return low_records


def summarize_low_evidence_records(records: list[dict[str, Any]], limit: int = 20) -> list[dict[str, Any]]:
    return [_low_evidence_summary_item(record) for record in _sorted_low_evidence_records(records)[:limit]]


def summarize_low_evidence_by_intent(records: list[dict[str, Any]], limit_per_intent: int = 20) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in _sorted_low_evidence_records(records):
        intent = str(record.get("graphIntent") or "UNKNOWN")
        items = grouped.setdefault(intent, [])
        if len(items) < limit_per_intent:
            items.append(_low_evidence_summary_item(record))
    return dict(sorted(grouped.items()))


def summarize_intent_mismatches(records: list[dict[str, Any]]) -> dict[str, Any]:
    mismatches = [
        record
        for record in records
        if record.get("graphIntent") != record.get("classifierGraphIntent")
    ]
    examples = [
        {
            "id": record.get("id"),
            "graphIntent": record.get("graphIntent"),
            "classifierGraphIntent": record.get("classifierGraphIntent"),
            "retrievalGraphIntent": record.get("retrievalGraphIntent"),
        }
        for record in mismatches[:20]
    ]
    return {
        "count": len(mismatches),
        "pct": _pct(len(mismatches), len(records)),
        "examples": examples,
    }


def summarize_low_value_sources(records: list[dict[str, Any]]) -> dict[str, Any]:
    totals: dict[str, dict[str, int]] = {}
    examples = []
    for record in records:
        sources = record.get("diagnostics", {}).get("lowValueSources", {})
        by_channel = sources.get("byChannel", {}) if isinstance(sources, dict) else {}
        for channel, kinds in by_channel.items():
            channel_totals = totals.setdefault(channel, {"none": 0, "http": 0, "wiki": 0, "video": 0})
            for kind, count in kinds.items():
                channel_totals[kind] = channel_totals.get(kind, 0) + int(count)
        for item in sources.get("items", [])[:3] if isinstance(sources, dict) else []:
            if len(examples) >= 30:
                break
            examples.append({"id": record.get("id"), **item})
    return {"byChannel": totals, "examples": examples}


def _compact_text(value: Any) -> str:
    return compact_text(value)


def _page_alias_diagnostics(cur, slugs: set[str], top_slugs: set[str]) -> dict[str, Any]:
    if not slugs:
        return {}
    probes = sorted(slugs | top_slugs)
    cur.execute(
        """
        SELECT slug, title, is_active,
               COALESCE(aliases, '[]'::jsonb)::text AS aliases_text
        FROM rag.wiki_page
        WHERE lower(slug) = ANY(%s)
        """,
        ([slug.lower() for slug in probes],),
    )
    exact_rows = {
        _normalize_slug(row[0]): {
            "slug": row[0],
            "title": row[1],
            "isActive": bool(row[2]),
            "aliases": _parse_json_list(row[3]),
        }
        for row in cur.fetchall()
    }

    diagnostics = {}
    for missing_slug in sorted(slugs):
        compact_missing = _compact_text(missing_slug)
        title_tail = compact_missing.split("/")[-1]
        like_pattern = f"%{title_tail}%"
        cur.execute(
            """
            SELECT slug, title, is_active,
                   COALESCE(aliases, '[]'::jsonb)::text AS aliases_text
            FROM rag.wiki_page
            WHERE lower(slug) LIKE lower(%s)
               OR lower(title) LIKE lower(%s)
            ORDER BY length(slug), slug
            LIMIT 8
            """,
            (like_pattern, like_pattern),
        )
        similar = [
            {
                "slug": row[0],
                "title": row[1],
                "isActive": bool(row[2]),
                "aliases": _parse_json_list(row[3]),
                "inTop5": _normalize_slug(row[0]) in top_slugs,
            }
            for row in cur.fetchall()
        ]
        possible_matches = []
        for top_slug in top_slugs:
            compact_top = _compact_text(top_slug)
            if compact_missing and (
                compact_missing in compact_top
                or compact_top in compact_missing
                or title_tail and title_tail in compact_top
            ):
                possible_matches.append(top_slug)
        diagnostics[missing_slug] = {
            "exact": exact_rows.get(missing_slug),
            "possibleTop5Matches": sorted(set(possible_matches)),
            "similarPages": similar,
            "likelyFalseNegative": bool(possible_matches),
        }
    return diagnostics


def _parse_json_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return [value.strip()]
        return _parse_json_list(parsed)
    return []


def attach_alias_diagnostics(records: list[dict[str, Any]]) -> dict[str, Any]:
    missing_by_record = {
        record["id"]: {
            _normalize_slug(slug)
            for slug in record.get("graphMetrics", {}).get("missingEvidenceSlugsTop5", [])
            if _normalize_slug(slug)
        }
        for record in records
        if record.get("graphMetrics", {}).get("missingEvidenceSlugsTop5")
    }
    if not missing_by_record:
        return {"recordsWithMissing": 0, "likelyFalseNegativeCount": 0, "examples": []}

    import psycopg2

    likely_false_negative_count = 0
    examples = []
    with psycopg2.connect(**RUNTIME_CONFIG.postgres.model_dump()) as conn:
        with conn.cursor() as cur:
            for record in records:
                missing_slugs = missing_by_record.get(record.get("id"), set())
                if not missing_slugs:
                    continue
                top_slugs = {_normalize_slug(item.get("slug")) for item in record.get("top", [])}
                alias_diag = _page_alias_diagnostics(cur, missing_slugs, top_slugs)
                record["aliasDiagnostics"] = alias_diag
                if any(item.get("likelyFalseNegative") for item in alias_diag.values()):
                    likely_false_negative_count += 1
                    if len(examples) < 20:
                        examples.append(
                            {
                                "id": record.get("id"),
                                "graphIntent": record.get("graphIntent"),
                                "missingEvidenceSlugsTop5": sorted(missing_slugs),
                                "topSlugs": sorted(top_slugs),
                                "aliasDiagnostics": alias_diag,
                            }
                        )
    return {
        "recordsWithMissing": len(missing_by_record),
        "likelyFalseNegativeCount": likely_false_negative_count,
        "examples": examples,
    }


def attach_focused_low_case_diagnostics(records: list[dict[str, Any]]) -> dict[str, Any]:
    focus_ids = {"grq094", "grq007", "grq024", "grq037", "grq039"}
    focused = {}
    for record in records:
        if record.get("id") not in focus_ids:
            continue
        graph_explain = record.get("diagnostics", {}).get("graphCandidateExplainTop50", {})
        candidates = graph_explain.get("candidates", []) if isinstance(graph_explain, dict) else []
        missing = {
            _normalize_slug(slug)
            for slug in record.get("graphMetrics", {}).get("missingEvidenceSlugsTop5", [])
        }
        seed_slugs = {_normalize_slug(slug) for slug in graph_explain.get("seedSlugs", [])}
        candidate_slugs = {_normalize_slug(item.get("slug")) for item in candidates}
        prerequisite_evidence = record.get("diagnostics", {}).get("prerequisiteEvidence", {})
        direct_evidence = prerequisite_evidence.get("directEvidenceCandidatesTopN", [])
        direct_evidence_slugs = {_normalize_slug(item.get("slug")) for item in direct_evidence}
        protected_seed_slugs = {
            _normalize_slug(item.get("slug"))
            for item in prerequisite_evidence.get("protectedSeeds", [])
        }
        graph_channel_slugs = {
            _normalize_slug(item.get("slug"))
            for item in record.get("diagnostics", {}).get("channelsTopN", {}).get("graph", [])
        }
        focused[record["id"]] = {
            "graphIntent": record.get("graphIntent"),
            "retrievalGraphIntent": record.get("retrievalGraphIntent"),
            "queryTerms": graph_explain.get("queryTerms", []),
            "seedSlugs": sorted(seed_slugs),
            "seedProtectedTop5": record.get("diagnostics", {}).get("top5Stabilization", {}).get("seedProtectedTop5", []),
            "protectedSeedSlugs": sorted(protected_seed_slugs),
            "directEvidenceCandidatesTopN": direct_evidence[:10],
            "missingEvidenceSlugsTop5": sorted(missing),
            "missingAlreadySeedSlugs": sorted(missing & seed_slugs),
            "missingFoundInDirectEvidence": sorted(missing & direct_evidence_slugs),
            "missingFoundInGraphCandidateTop50": sorted(missing & candidate_slugs),
            "missingFoundInGraphChannelTopN": sorted(missing & graph_channel_slugs),
            "missingAbsentFromGraphCandidateTop50": sorted(missing - candidate_slugs - seed_slugs),
            "replacementReason": record.get("diagnostics", {}).get("top5Stabilization", {}).get("replacementReason", []),
            "topGraphCandidates": candidates[:10],
        }
    return focused


def _graph_judge_cache_key(question_item: dict[str, Any], candidates: list[dict[str, Any]]) -> str:
    return _json_hash(
        {
            "version": GRAPH_JUDGE_CACHE_VERSION,
            "question": question_item.get("question"),
            "expectedTitle": question_item.get("expectedTitle"),
            "expectedSlug": question_item.get("expectedSlug"),
            "expectedTags": question_item.get("expectedTags", []),
            "graphIntent": question_item.get("graphIntent"),
            "expectedRelatedSlugs": question_item.get("expectedRelatedSlugs", []),
            "graphMetricVersion": GRAPH_METRIC_VERSION,
            "candidates": [(item["rank"], item["slug"], item["title"]) for item in candidates],
        }
    )


async def benchmark_graph_questions(
    *,
    questions_path: Path,
    output_path: Path,
    judge_cache_path: Path,
    intent_mode: str = INTENT_MODE_ORACLE,
    embedding_cache_path: Path | None = DEFAULT_GRAPH_EMBEDDING_CACHE,
    embedding_cache_ttl_days: int = DEFAULT_EMBEDDING_CACHE_TTL_DAYS,
    quality_thresholds: dict[str, float] | None = None,
    judge_max_attempts: int = DEFAULT_JUDGE_MAX_ATTEMPTS,
    judge_retry_base_seconds: float = DEFAULT_JUDGE_RETRY_BASE_SECONDS,
) -> dict[str, Any]:
    question_set = _read_json(questions_path)
    questions = question_set.get("questions", [])
    if not isinstance(questions, list) or not questions:
        raise RuntimeError(f"invalid graph question set: {questions_path}")

    judge = LLMRetrievalJudge()
    classifier = QueryClassifier()
    judge_cache = _load_judge_cache(judge_cache_path)
    embedding_cache = (
        QueryEmbeddingCache(embedding_cache_path, ttl_days=embedding_cache_ttl_days)
        if embedding_cache_path
        else None
    )
    records = []

    for index, question_item in enumerate(questions, start=1):
        question = str(question_item.get("question") or "")
        classification = classifier.classify({"query": question})
        retrieval_graph_intent = (
            classification.graph_intent
            if intent_mode == INTENT_MODE_CLASSIFIER
            else str(question_item.get("graphIntent") or "")
        )
        retrieval_strategy = (
            classification.retrieval_strategy
            if intent_mode == INTENT_MODE_CLASSIFIER
            else "LOCAL_HYBRID"
        )
        result, timings, total_ms, error = run_retrieval(
            question,
            graph_intent=retrieval_graph_intent,
            retrieval_strategy=retrieval_strategy,
            include_diagnostics=True,
            embedding_cache=embedding_cache,
        )
        candidates = _top_candidates(result)
        cache_key = _graph_judge_cache_key(question_item, candidates)
        if cache_key in judge_cache:
            judge_result = normalize_judge_payload(judge_cache[cache_key])
            judge_from_cache = True
        else:
            judge_result = await _judge_with_retries(
                judge,
                question_item,
                candidates,
                max_attempts=judge_max_attempts,
                retry_base_seconds=judge_retry_base_seconds,
            )
            judge_cache[cache_key] = judge_result
            _save_judge_cache(judge_cache_path, judge_cache)
            judge_from_cache = False

        graph_metrics = evaluate_graph_evidence(question_item, candidates)
        record = {
            "id": question_item.get("id", f"grq{index:03d}"),
            "question": question,
            "graphIntent": question_item.get("graphIntent"),
            "retrievalGraphIntent": retrieval_graph_intent,
            "intentMode": intent_mode,
            "classifierGraphIntent": classification.graph_intent,
            "classifierRetrievalStrategy": classification.retrieval_strategy,
            "classifierReason": classification.reason,
            "expectedTitle": question_item.get("expectedTitle"),
            "expectedSlug": question_item.get("expectedSlug"),
            "expectedTags": question_item.get("expectedTags", []),
            "expectedRelatedSlugs": question_item.get("expectedRelatedSlugs", []),
            "expectedRelatedTitles": question_item.get("expectedRelatedTitles", []),
            "latencyMs": round(total_ms, 2),
            "channelLatencyMs": {key: round(value, 2) for key, value in timings.items()},
            "success": error is None,
            "error": error,
            "top": candidates,
            "diagnostics": result.get("diagnostics", {}),
            "judge": judge_result,
            "judgeFromCache": judge_from_cache,
            "graphMetrics": graph_metrics,
        }
        records.append(record)
        print(
            f"[{index:03d}/{len(questions)}] "
            f"{'OK' if record['success'] else 'ERR'} "
            f"hit@3={judge_result['hitAt3']} "
            f"completeTop5={graph_metrics['completeEvidenceTop5']} "
            f"recallTop5={graph_metrics['evidenceNodeRecallTop5']:.2f} "
            f"latency={record['latencyMs']:.0f}ms"
        )

    legacy_summary = summarize_records(records)
    graph_summary = summarize_graph_records(records)
    legacy_summary.update(
        summarize_graph_quality_gates(
            legacy_summary,
            graph_summary,
            thresholds=quality_thresholds,
        )
    )
    alias_summary = attach_alias_diagnostics(records)
    report = {
        "questionSet": {
            "path": str(questions_path),
            "seed": question_set.get("seed"),
            "count": len(questions),
            "hash": question_set.get("questionSetHash") or _json_hash(questions),
        },
        "settings": {
            "domain": RUNTIME_CONFIG.retrieval_domain,
            "topK": TOP_K,
            "judgeCacheVersion": JUDGE_CACHE_VERSION,
            "graphJudgeCacheVersion": GRAPH_JUDGE_CACHE_VERSION,
            "graphMetricVersion": GRAPH_METRIC_VERSION,
            "intentMode": intent_mode,
            "embeddingCache": embedding_cache.snapshot_stats() if embedding_cache else {"enabled": False},
        },
        "summary": legacy_summary,
        "graphSummary": graph_summary,
        "byGraphIntent": summarize_by_graph_intent(records),
        "intentMismatchSummary": summarize_intent_mismatches(records),
        "lowValueSourceSummary": summarize_low_value_sources(records),
        "aliasDiagnosticSummary": alias_summary,
        "focusedLowCaseDiagnostics": attach_focused_low_case_diagnostics(records),
        "lowEvidenceRecords": summarize_low_evidence_records(records),
        "lowEvidenceRecordsByIntent": summarize_low_evidence_by_intent(records),
        "records": records,
    }
    _write_json(output_path, report)
    return report


def _quality_thresholds_from_args(args: argparse.Namespace) -> dict[str, float]:
    return {
        "hitAt3Pct": args.hit_at3_min_pct,
        "avgLatencyMs": args.avg_latency_max_ms,
        "p95LatencyMs": args.p95_latency_max_ms,
        "primaryTop5Pct": args.primary_top5_min_pct,
        "completeEvidenceTop5Pct": args.complete_evidence_top5_min_pct,
        "evidenceNodeRecallTop5Pct": args.evidence_recall_top5_min_pct,
    }


def _failed_quality_gate_names(summary: dict[str, Any]) -> list[str]:
    return [
        key
        for key in (
            "passHitAt3",
            "passLatency",
            "passChannelErrors",
            "passPrimaryTop5",
            "passEvidenceRecall",
            "passCompleteEvidence",
        )
        if summary.get(key) is False
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--judge-cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--intent-mode", choices=[INTENT_MODE_ORACLE, INTENT_MODE_CLASSIFIER], default=INTENT_MODE_ORACLE)
    parser.add_argument("--embedding-cache", type=Path, default=DEFAULT_GRAPH_EMBEDDING_CACHE)
    parser.add_argument("--embedding-cache-ttl-days", type=int, default=DEFAULT_EMBEDDING_CACHE_TTL_DAYS)
    parser.add_argument("--no-embedding-cache", action="store_true")
    parser.add_argument("--judge-max-attempts", type=int, default=DEFAULT_JUDGE_MAX_ATTEMPTS)
    parser.add_argument("--judge-retry-base-seconds", type=float, default=DEFAULT_JUDGE_RETRY_BASE_SECONDS)
    parser.add_argument("--hit-at3-min-pct", type=float, default=GRAPH_HIT_AT3_MIN_PCT)
    parser.add_argument("--avg-latency-max-ms", type=float, default=GRAPH_AVG_LATENCY_MAX_MS)
    parser.add_argument("--p95-latency-max-ms", type=float, default=GRAPH_P95_LATENCY_MAX_MS)
    parser.add_argument("--primary-top5-min-pct", type=float, default=GRAPH_PRIMARY_TOP5_MIN_PCT)
    parser.add_argument("--complete-evidence-top5-min-pct", type=float, default=GRAPH_COMPLETE_EVIDENCE_TOP5_MIN_PCT)
    parser.add_argument("--evidence-recall-top5-min-pct", type=float, default=GRAPH_EVIDENCE_RECALL_TOP5_MIN_PCT)
    parser.add_argument(
        "--no-fail-on-gate",
        action="store_true",
        help="Write the report but keep exit code 0 when the quality gate fails.",
    )
    args = parser.parse_args()

    report = asyncio.run(
        benchmark_graph_questions(
            questions_path=args.questions,
            output_path=args.output,
            judge_cache_path=args.judge_cache,
            intent_mode=args.intent_mode,
            embedding_cache_path=None if args.no_embedding_cache else args.embedding_cache,
            embedding_cache_ttl_days=args.embedding_cache_ttl_days,
            quality_thresholds=_quality_thresholds_from_args(args),
            judge_max_attempts=args.judge_max_attempts,
            judge_retry_base_seconds=args.judge_retry_base_seconds,
        )
    )
    print(
        json.dumps(
            {
                "summary": report["summary"],
                "graphSummary": report["graphSummary"],
                "byGraphIntent": report["byGraphIntent"],
                "intentMismatchSummary": report["intentMismatchSummary"],
                "lowValueSourceSummary": report["lowValueSourceSummary"],
                "aliasDiagnosticSummary": report["aliasDiagnosticSummary"],
                "focusedLowCaseDiagnostics": report["focusedLowCaseDiagnostics"],
                "lowEvidenceRecords": report["lowEvidenceRecords"],
                "lowEvidenceRecordsByIntent": report["lowEvidenceRecordsByIntent"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print(f"Saved graph report to {args.output}")
    if not args.no_fail_on_gate and not report["summary"].get("overallPass"):
        failed_gates = ", ".join(_failed_quality_gate_names(report["summary"])) or "overallPass"
        raise SystemExit(f"GraphRAG benchmark quality gate failed: {failed_gates}")


if __name__ == "__main__":
    main()
