"""Benchmark RAG quality and latency on a fixed random 100-question set.

Usage:
  python knowledge/benchmark_rag_100.py --generate --seed 20260524 --output reports/rag_100_questions.json
  python knowledge/benchmark_rag_100.py --questions reports/rag_100_questions.json --output reports/rag_100_baseline.json
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import statistics
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import psycopg2

from knowledge.settings_helper import configure_dashscope_api_key
from retrieval.hybrid_retriever import HybridRetriever
from src.ai_modules.config import get_settings
from src.ai_modules.llms.agent_models import OpenAICompatibleJSONGenerator

RUNTIME_CONFIG = configure_dashscope_api_key()
DB_CONFIG = RUNTIME_CONFIG.postgres.model_dump()
DEFAULT_QUESTION_COUNT = 100
DEFAULT_SEED = 20260524
TOP_K = 5
JUDGE_CACHE_VERSION = "rag-judge-v1"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _json_hash(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def percentile_ms(values_ms: list[float], ratio: float) -> float:
    if not values_ms:
        return 0.0
    ordered = sorted(values_ms)
    index = max(0, min(len(ordered) - 1, round((len(ordered) - 1) * ratio)))
    return round(ordered[index], 2)


def _normalize_tags(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return [value.strip()]
        return _normalize_tags(parsed)
    return []


def generate_questions(*, seed: int, count: int) -> dict[str, Any]:
    with psycopg2.connect(**DB_CONFIG) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT ON (title)
                       id::text, title, source_ref, tags, metadata_json
                FROM rag.knowledge_document
                WHERE domain = %s AND title IS NOT NULL AND btrim(title) <> ''
                ORDER BY title, md5(id::text || %s)
                """,
                (RUNTIME_CONFIG.retrieval_domain, str(seed)),
            )
            rows = cur.fetchall()

    if len(rows) < count:
        raise RuntimeError(f"not enough knowledge documents: need {count}, got {len(rows)}")

    ordered = sorted(rows, key=lambda row: hashlib.sha256(f"{seed}:{row[0]}".encode()).hexdigest())
    questions = []
    for index, (doc_id, title, source_ref, tags, metadata_json) in enumerate(ordered[:count], start=1):
        normalized_tags = _normalize_tags(tags)
        metadata = metadata_json if isinstance(metadata_json, dict) else {}
        course = str(metadata.get("course") or metadata.get("chapter") or "").strip()
        tag_hint = "、".join(normalized_tags[:4])
        context_parts = [part for part in (course, tag_hint) if part]
        context_hint = f"（参考方向：{'；'.join(context_parts)}）" if context_parts else ""
        questions.append(
            {
                "id": f"q{index:03d}",
                "documentId": doc_id,
                "expectedTitle": str(title),
                "expectedSlug": str(source_ref),
                "expectedTags": normalized_tags,
                "question": f"请解释“{title}”的核心概念、典型场景和常见误区。{context_hint}",
            }
        )

    return {
        "seed": seed,
        "count": count,
        "domain": RUNTIME_CONFIG.retrieval_domain,
        "questionSetHash": _json_hash(questions),
        "questions": questions,
    }


@contextmanager
def channel_timer(timings: dict[str, float], channel_name: str):
    started = time.perf_counter()
    try:
        yield
    finally:
        timings[channel_name] += (time.perf_counter() - started) * 1000


def run_retrieval(question: str) -> tuple[dict[str, Any], dict[str, float], float, str | None]:
    retriever = HybridRetriever(DB_CONFIG, top_k=TOP_K)
    timings = {"init_ms": 0.0, "grep_ms": 0.0, "vector_ms": 0.0, "graph_ms": 0.0, "web_ms": 0.0, "fusion_ms": 0.0}
    error: str | None = None
    started = time.perf_counter()
    result: dict[str, Any] = {"query": question, "channels": {}, "top": []}

    with psycopg2.connect(**DB_CONFIG) as conn:
        with conn.cursor() as cur:
            try:
                with channel_timer(timings, "init_ms"):
                    retriever._init(cur)

                with channel_timer(timings, "grep_ms"):
                    grep_results = retriever._grep.search(cur, question, retriever.domain)

                with channel_timer(timings, "vector_ms"):
                    vector_all = retriever._vector.search_all(cur, question, top_k=retriever.top_k, domain=retriever.domain)
                    vector_results = [(item[0], item[1], item[2]) for item in vector_all]

                with channel_timer(timings, "graph_ms"):
                    grep_slugs = [item[0] for item in grep_results.get("priority", [])[: retriever.graph_seed_n]]
                    vec_slugs = [item[0] for item in vector_results[: retriever.graph_seed_n]]
                    seed_slugs = list(dict.fromkeys(grep_slugs + vec_slugs))[: retriever.graph_seed_n]
                    graph_results = retriever._graph.expand(cur, seed_slugs, top_n=5)

                web_results: list[Any] = []
                with channel_timer(timings, "fusion_ms"):
                    fused = retriever._rrf.fuse(grep_results, vector_results, graph_results, web_results)

                result = {
                    "query": question,
                    "webSearchEnabled": False,
                    "channels": {
                        "grep": {
                            "priority": grep_results.get("priority", []),
                            "normal_count": len(grep_results.get("normal", [])),
                        },
                        "vector": [(item[0], item[1], item[2], item[3]) for item in vector_all[:5]],
                        "graph": graph_results,
                        "web": web_results,
                    },
                    "fused": fused,
                    "top": fused[:5],
                }
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"

    total_ms = (time.perf_counter() - started) * 1000
    return result, timings, total_ms, error


def _top_candidates(result: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = []
    for rank, item in enumerate(result.get("top", [])[:TOP_K], start=1):
        if not isinstance(item, (list, tuple)) or len(item) < 3:
            continue
        candidates.append(
            {
                "rank": rank,
                "slug": str(item[0]),
                "title": str(item[1]),
                "score": float(item[2]),
                "extra": item[3] if len(item) > 3 else None,
            }
        )
    return candidates


def _judge_cache_key(question_item: dict[str, Any], candidates: list[dict[str, Any]]) -> str:
    return _json_hash(
        {
            "version": JUDGE_CACHE_VERSION,
            "question": question_item.get("question"),
            "expectedTitle": question_item.get("expectedTitle"),
            "expectedTags": question_item.get("expectedTags", []),
            "candidates": [(item["rank"], item["slug"], item["title"]) for item in candidates],
        }
    )


def _load_judge_cache(cache_path: Path) -> dict[str, Any]:
    if not cache_path.exists():
        return {}
    payload = _read_json(cache_path)
    return payload if isinstance(payload, dict) else {}


def _save_judge_cache(cache_path: Path, cache: dict[str, Any]) -> None:
    _write_json(cache_path, cache)


class LLMRetrievalJudge:
    def __init__(self) -> None:
        settings = get_settings()
        if not settings.provider_ready(settings.resolve_component_provider("judge_llm")):
            raise RuntimeError("judge_llm provider is not ready")
        provider_name = settings.resolve_component_provider("judge_llm")
        model_name = settings.resolve_component_model(
            "judge_llm",
            default_logical_model="fast_model",
            provider_name=provider_name,
        )
        self.generator = OpenAICompatibleJSONGenerator(
            model_name=model_name,
            provider_name=provider_name,
            temperature=0.0,
            cache_namespace="rag_100_retrieval_judge",
        )

    async def judge(self, question_item: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
        payload = await self.generator.generate(
            system_prompt=(
                "你是 RAG 检索质量裁判。请判断候选文档是否能直接帮助回答用户问题。"
                "只返回 JSON，字段必须是 hitAt1、hitAt3、bestRank、relevanceScore、reason。"
                "hitAt3=true 表示前三个候选中至少一个与问题直接相关。"
                "bestRank 是最佳相关候选排名，若没有相关候选则为 null。"
                "relevanceScore 是 0 到 1 的整体相关性评分。"
            ),
            user_prompt=json.dumps(
                {
                    "question": question_item.get("question"),
                    "expectedTitle": question_item.get("expectedTitle"),
                    "expectedSlug": question_item.get("expectedSlug"),
                    "expectedTags": question_item.get("expectedTags", []),
                    "candidates": candidates,
                },
                ensure_ascii=False,
            ),
            max_tokens=420,
        )
        return normalize_judge_payload(payload)


def normalize_judge_payload(payload: dict[str, Any]) -> dict[str, Any]:
    best_rank = payload.get("bestRank")
    if best_rank is not None:
        try:
            best_rank = int(best_rank)
        except (TypeError, ValueError):
            best_rank = None
    score = payload.get("relevanceScore", 0.0)
    try:
        score = float(score)
    except (TypeError, ValueError):
        score = 0.0
    return {
        "hitAt1": bool(payload.get("hitAt1")),
        "hitAt3": bool(payload.get("hitAt3")),
        "bestRank": best_rank,
        "relevanceScore": max(0.0, min(1.0, score)),
        "reason": str(payload.get("reason") or "").strip()[:500],
    }


async def benchmark_questions(*, questions_path: Path, output_path: Path, judge_cache_path: Path) -> dict[str, Any]:
    question_set = _read_json(questions_path)
    questions = question_set.get("questions", [])
    if not isinstance(questions, list) or not questions:
        raise RuntimeError(f"invalid question set: {questions_path}")

    judge = LLMRetrievalJudge()
    judge_cache = _load_judge_cache(judge_cache_path)
    records = []
    total_started = time.perf_counter()

    for index, question_item in enumerate(questions, start=1):
        question = str(question_item.get("question") or "")
        result, timings, total_ms, error = run_retrieval(question)
        candidates = _top_candidates(result)
        cache_key = _judge_cache_key(question_item, candidates)
        if cache_key in judge_cache:
            judge_result = normalize_judge_payload(judge_cache[cache_key])
            judge_from_cache = True
        else:
            judge_result = await judge.judge(question_item, candidates)
            judge_cache[cache_key] = judge_result
            _save_judge_cache(judge_cache_path, judge_cache)
            judge_from_cache = False

        record = {
            "id": question_item.get("id", f"q{index:03d}"),
            "question": question,
            "expectedTitle": question_item.get("expectedTitle"),
            "expectedSlug": question_item.get("expectedSlug"),
            "expectedTags": question_item.get("expectedTags", []),
            "latencyMs": round(total_ms, 2),
            "channelLatencyMs": {key: round(value, 2) for key, value in timings.items()},
            "success": error is None,
            "error": error,
            "top": candidates,
            "judge": judge_result,
            "judgeFromCache": judge_from_cache,
        }
        records.append(record)
        print(
            f"[{index:03d}/{len(questions)}] "
            f"{'OK' if record['success'] else 'ERR'} "
            f"hit@3={judge_result['hitAt3']} "
            f"score={judge_result['relevanceScore']:.2f} "
            f"latency={record['latencyMs']:.0f}ms"
        )

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
        },
        "summary": summarize_records(records),
        "records": records,
        "elapsedSeconds": round(time.perf_counter() - total_started, 2),
    }
    _write_json(output_path, report)
    return report


def summarize_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    latencies = [float(item["latencyMs"]) for item in records]
    scores = [float(item["judge"]["relevanceScore"]) for item in records]
    count = len(records)
    hit_at_1 = sum(1 for item in records if item["judge"]["hitAt1"])
    hit_at_3 = sum(1 for item in records if item["judge"]["hitAt3"])
    success = sum(1 for item in records if item["success"])

    channel_names = sorted({name for item in records for name in item.get("channelLatencyMs", {})})
    channel_summary = {}
    for name in channel_names:
        values = [float(item["channelLatencyMs"].get(name, 0.0)) for item in records]
        channel_summary[name] = {
            "avgMs": round(statistics.mean(values), 2) if values else 0.0,
            "p95Ms": percentile_ms(values, 0.95),
            "maxMs": round(max(values), 2) if values else 0.0,
        }

    return {
        "total": count,
        "success": success,
        "successRatePct": round(success / count * 100, 2) if count else 0.0,
        "hitAt1": hit_at_1,
        "hitAt3": hit_at_3,
        "hitAt1Pct": round(hit_at_1 / count * 100, 2) if count else 0.0,
        "hitAt3Pct": round(hit_at_3 / count * 100, 2) if count else 0.0,
        "avgRelevanceScore": round(statistics.mean(scores), 4) if scores else 0.0,
        "avgLatencyMs": round(statistics.mean(latencies), 2) if latencies else 0.0,
        "medianLatencyMs": round(statistics.median(latencies), 2) if latencies else 0.0,
        "p95LatencyMs": percentile_ms(latencies, 0.95),
        "maxLatencyMs": round(max(latencies), 2) if latencies else 0.0,
        "channels": channel_summary,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--generate", action="store_true")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--count", type=int, default=DEFAULT_QUESTION_COUNT)
    parser.add_argument("--questions", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--judge-cache", type=Path, default=Path("reports/rag_100_judge_cache.json"))
    args = parser.parse_args()

    if args.generate:
        payload = generate_questions(seed=args.seed, count=args.count)
        _write_json(args.output, payload)
        print(f"Generated {len(payload['questions'])} questions -> {args.output}")
        return

    if args.questions is None:
        raise SystemExit("--questions is required unless --generate is set")

    report = asyncio.run(
        benchmark_questions(
            questions_path=args.questions,
            output_path=args.output,
            judge_cache_path=args.judge_cache,
        )
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"Saved report to {args.output}")


if __name__ == "__main__":
    main()
