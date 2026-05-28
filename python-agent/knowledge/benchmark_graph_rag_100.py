"""Benchmark GraphRAG-lite evidence coverage on the fixed 100-question set.

This runner keeps the legacy RAG judge fields from ``benchmark_rag_100`` while
adding graph evidence metrics grouped by ``graphIntent``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
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
        result, timings, total_ms, error = run_retrieval(
            question,
            graph_intent=str(question_item.get("graphIntent") or ""),
        )
        candidates = _top_candidates(result)
        classification = classifier.classify({"query": question})
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
        },
        "summary": legacy_summary,
        "graphSummary": graph_summary,
        "byGraphIntent": summarize_by_graph_intent(records),
        "records": records,
    }
    _write_json(output_path, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--judge-cache", type=Path, default=DEFAULT_CACHE)
    args = parser.parse_args()

    report = asyncio.run(
        benchmark_graph_questions(
            questions_path=args.questions,
            output_path=args.output,
            judge_cache_path=args.judge_cache,
        )
    )
    print(
        json.dumps(
            {
                "summary": report["summary"],
                "graphSummary": report["graphSummary"],
                "byGraphIntent": report["byGraphIntent"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print(f"Saved graph report to {args.output}")


if __name__ == "__main__":
    main()
