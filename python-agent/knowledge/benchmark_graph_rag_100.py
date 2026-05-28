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

from knowledge.benchmark_rag_100 import (  # noqa: E402
    JUDGE_CACHE_VERSION,
    RUNTIME_CONFIG,
    TOP_K,
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
from src.ai_modules.retrieval import QueryClassifier  # noqa: E402

DEFAULT_QUESTIONS = PROJECT_ROOT / "reports" / "graph_rag_100_questions.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "reports" / "graph_rag_100_current.json"
DEFAULT_CACHE = PROJECT_ROOT / "reports" / "graph_rag_100_judge_cache.json"
GRAPH_METRIC_VERSION = "graph-rag-lite-metrics-v1"
GRAPH_JUDGE_CACHE_VERSION = f"{JUDGE_CACHE_VERSION}:graph-v1"
INTENT_MODE_ORACLE = "oracle"
INTENT_MODE_CLASSIFIER = "classifier"


def _normalize_slug(value: Any) -> str:
    return str(value or "").strip().strip('"').lower()


def _top_slugs(candidates: list[dict[str, Any]], limit: int) -> set[str]:
    return {_normalize_slug(item.get("slug")) for item in candidates[:limit]}


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
    present_top5 = expected_nodes & top5
    missing_top5 = expected_nodes - top5

    return {
        "graphIntent": question_item.get("graphIntent"),
        "primaryTop5": primary_slug in top5,
        "anyRelatedTop5": bool(related_slugs & top5),
        "partialEvidenceTop5": bool(present_top5),
        "completeEvidenceTop5": expected_nodes <= top5 if expected_nodes else False,
        "evidenceNodeRecallTop5": round(len(present_top5) / len(expected_nodes), 4) if expected_nodes else 0.0,
        "primaryTop3": primary_slug in top3,
        "anyRelatedTop3": bool(related_slugs & top3),
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


def summarize_by_graph_intent(records: list[dict[str, Any]]) -> dict[str, Any]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        intent = str(record.get("graphIntent") or "UNKNOWN")
        buckets.setdefault(intent, []).append(record)

    return {
        intent: summarize_graph_records(items)
        for intent, items in sorted(buckets.items())
    }


def summarize_low_evidence_records(records: list[dict[str, Any]], limit: int = 20) -> list[dict[str, Any]]:
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
    return [
        {
            "id": record.get("id"),
            "graphIntent": record.get("graphIntent"),
            "classifierGraphIntent": record.get("classifierGraphIntent"),
            "evidenceNodeRecallTop5": record["graphMetrics"]["evidenceNodeRecallTop5"],
            "missingEvidenceSlugsTop5": record["graphMetrics"].get("missingEvidenceSlugsTop5", []),
            "topSlugs": [candidate.get("slug") for candidate in record.get("top", [])],
        }
        for record in low_records[:limit]
    ]


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
    return re.sub(r"[\s\"'《》“”]+", "", str(value or "").strip().lower())


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
        graph_channel_slugs = {
            _normalize_slug(item.get("slug"))
            for item in record.get("diagnostics", {}).get("channelsTopN", {}).get("graph", [])
        }
        focused[record["id"]] = {
            "graphIntent": record.get("graphIntent"),
            "retrievalGraphIntent": record.get("retrievalGraphIntent"),
            "queryTerms": graph_explain.get("queryTerms", []),
            "seedSlugs": sorted(seed_slugs),
            "missingEvidenceSlugsTop5": sorted(missing),
            "missingAlreadySeedSlugs": sorted(missing & seed_slugs),
            "missingFoundInGraphCandidateTop50": sorted(missing & candidate_slugs),
            "missingFoundInGraphChannelTopN": sorted(missing & graph_channel_slugs),
            "missingAbsentFromGraphCandidateTop50": sorted(missing - candidate_slugs - seed_slugs),
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
) -> dict[str, Any]:
    question_set = _read_json(questions_path)
    questions = question_set.get("questions", [])
    if not isinstance(questions, list) or not questions:
        raise RuntimeError(f"invalid graph question set: {questions_path}")

    judge = LLMRetrievalJudge()
    classifier = QueryClassifier()
    judge_cache = _load_judge_cache(judge_cache_path)
    records = []

    for index, question_item in enumerate(questions, start=1):
        question = str(question_item.get("question") or "")
        classification = classifier.classify({"query": question})
        retrieval_graph_intent = (
            classification.graph_intent
            if intent_mode == INTENT_MODE_CLASSIFIER
            else str(question_item.get("graphIntent") or "")
        )
        result, timings, total_ms, error = run_retrieval(
            question,
            graph_intent=retrieval_graph_intent,
            include_diagnostics=True,
        )
        candidates = _top_candidates(result)
        cache_key = _graph_judge_cache_key(question_item, candidates)
        if cache_key in judge_cache:
            judge_result = normalize_judge_payload(judge_cache[cache_key])
            judge_from_cache = True
        else:
            judge_result = await judge.judge(question_item, candidates)
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
        },
        "summary": legacy_summary,
        "graphSummary": graph_summary,
        "byGraphIntent": summarize_by_graph_intent(records),
        "intentMismatchSummary": summarize_intent_mismatches(records),
        "lowValueSourceSummary": summarize_low_value_sources(records),
        "aliasDiagnosticSummary": alias_summary,
        "focusedLowCaseDiagnostics": attach_focused_low_case_diagnostics(records),
        "lowEvidenceRecords": summarize_low_evidence_records(records),
        "records": records,
    }
    _write_json(output_path, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--judge-cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--intent-mode", choices=[INTENT_MODE_ORACLE, INTENT_MODE_CLASSIFIER], default=INTENT_MODE_ORACLE)
    args = parser.parse_args()

    report = asyncio.run(
        benchmark_graph_questions(
            questions_path=args.questions,
            output_path=args.output,
            judge_cache_path=args.judge_cache,
            intent_mode=args.intent_mode,
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
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print(f"Saved graph report to {args.output}")


if __name__ == "__main__":
    main()
