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
import re
import statistics
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import psycopg2

from knowledge.settings_helper import configure_dashscope_api_key
from retrieval.hybrid_retriever import HybridRetriever
from retrieval.source_quality import low_value_source_kind
from src.ai_modules.config import get_settings
from src.ai_modules.llms.agent_models import OpenAICompatibleJSONGenerator
from src.ai_modules.llms.errors import LLMServiceError

RUNTIME_CONFIG = configure_dashscope_api_key()
DB_CONFIG = RUNTIME_CONFIG.postgres.model_dump()
DEFAULT_QUESTION_COUNT = 100
DEFAULT_SEED = 20260524
TOP_K = 5
JUDGE_CACHE_VERSION = "rag-judge-v1"
DEFAULT_JUDGE_CACHE = PROJECT_ROOT / "reports" / "rag_100_judge_cache.json"
DEFAULT_EMBEDDING_CACHE = PROJECT_ROOT / "reports" / "rag_100_embedding_cache.json"
DEFAULT_EMBEDDING_CACHE_TTL_DAYS = 30
DEFAULT_JUDGE_MAX_ATTEMPTS = 3
DEFAULT_JUDGE_RETRY_BASE_SECONDS = 2.0
RAG_HIT_AT3_MIN_PCT = 99.0
RAG_SUCCESS_RATE_MIN_PCT = 99.0
RAG_AVG_LATENCY_MAX_MS = 1651.99
RAG_P95_LATENCY_MAX_MS = 4049.05
RAG_CHANNEL_ERROR_MAX = 0


def _normalize_slug(value: Any) -> str:
    return str(value or "").strip().strip('"').lower()


def _tuple_item_to_candidate(item: Any, rank: int) -> dict[str, Any]:
    if not isinstance(item, (list, tuple)) or len(item) < 3:
        return {"rank": rank, "slug": "", "title": "", "score": 0.0, "extra": None}
    try:
        score = float(item[2])
    except (TypeError, ValueError):
        score = 0.0
    return {
        "rank": rank,
        "slug": str(item[0]),
        "title": str(item[1]),
        "score": round(score, 4),
        "extra": item[3] if len(item) > 3 else None,
    }


def _top_channel_items(items: list[Any], limit: int = 10) -> list[dict[str, Any]]:
    return [_tuple_item_to_candidate(item, rank) for rank, item in enumerate(items[:limit], start=1)]


def _low_value_kind(slug: Any, title: Any) -> str | None:
    return low_value_source_kind(slug, title)


def _summarize_low_value_sources(
    *,
    grep_results: dict[str, Any],
    vector_results: list[tuple],
    graph_results: list[tuple],
    web_results: list[Any],
) -> dict[str, Any]:
    summary = {
        "byChannel": {
            "grepPriority": {"none": 0, "http": 0, "wiki": 0, "video": 0},
            "grepNormal": {"none": 0, "http": 0, "wiki": 0, "video": 0},
            "vector": {"none": 0, "http": 0, "wiki": 0, "video": 0},
            "graph": {"none": 0, "http": 0, "wiki": 0, "video": 0},
            "web": {"none": 0, "http": 0, "wiki": 0, "video": 0},
        },
        "items": [],
    }

    channel_items = {
        "grepPriority": grep_results.get("priority", []) if isinstance(grep_results, dict) else [],
        "grepNormal": grep_results.get("normal", []) if isinstance(grep_results, dict) else [],
        "vector": vector_results,
        "graph": graph_results,
        "web": web_results,
    }
    for channel, items in channel_items.items():
        for rank, item in enumerate(items[:10], start=1):
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            kind = _low_value_kind(item[0], item[1])
            if not kind:
                continue
            summary["byChannel"][channel][kind] += 1
            summary["items"].append(
                {
                    "channel": channel,
                    "rank": rank,
                    "kind": kind,
                    "slug": str(item[0]),
                    "title": str(item[1]),
                }
            )
    return summary


def _fusion_replacements(pre_fused: list[tuple], post_fused: list[tuple], limit: int = 5) -> list[dict[str, Any]]:
    replacements = []
    for index in range(limit):
        before = pre_fused[index] if index < len(pre_fused) else None
        after = post_fused[index] if index < len(post_fused) else None
        before_slug = str(before[0]) if isinstance(before, (list, tuple)) and before else None
        after_slug = str(after[0]) if isinstance(after, (list, tuple)) and after else None
        if before_slug == after_slug:
            continue
        replacements.append(
            {
                "rank": index + 1,
                "before": _tuple_item_to_candidate(before, index + 1) if before else None,
                "after": _tuple_item_to_candidate(after, index + 1) if after else None,
            }
        )
    return replacements


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _json_hash(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class QueryEmbeddingCache:
    """Persistent benchmark-only query embedding cache."""

    def __init__(
        self,
        path: Path | str,
        *,
        ttl_days: int = DEFAULT_EMBEDDING_CACHE_TTL_DAYS,
        now_fn=None,
    ) -> None:
        self.path = Path(path)
        self.ttl_days = max(1, int(ttl_days))
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._entries: dict[str, Any] = self._load_entries()
        self.stats = {
            "hits": 0,
            "misses": 0,
            "expired": 0,
            "modelDimensionMismatches": 0,
            "writes": 0,
        }

    def get(self, query: str, *, model: str, dimension: int) -> list[float] | None:
        key = self._key(query, model=model, dimension=dimension)
        entry = self._entries.get(key)
        if not isinstance(entry, dict):
            self.stats["misses"] += 1
            return None
        if str(entry.get("model") or "") != str(model) or int(entry.get("dimension") or 0) != int(dimension):
            self.stats["modelDimensionMismatches"] += 1
            return None
        created_at = self._parse_created_at(entry.get("createdAt"))
        entry_ttl_days = int(entry.get("ttlDays") or self.ttl_days)
        if created_at is None or self._now() - created_at > timedelta(days=entry_ttl_days):
            self.stats["expired"] += 1
            return None
        embedding = entry.get("embedding")
        if not isinstance(embedding, list) or len(embedding) != int(dimension):
            self.stats["modelDimensionMismatches"] += 1
            return None
        self.stats["hits"] += 1
        return [float(value) for value in embedding]

    def set(self, query: str, *, model: str, dimension: int, embedding: list[float]) -> None:
        if len(embedding) != int(dimension):
            raise ValueError(f"embedding dimension mismatch: expected {dimension}, got {len(embedding)}")
        key = self._key(query, model=model, dimension=dimension)
        self._entries[key] = {
            "model": str(model),
            "dimension": int(dimension),
            "querySha256": self._query_hash(query),
            "embedding": [float(value) for value in embedding],
            "createdAt": self._now().isoformat(),
            "ttlDays": self.ttl_days,
        }
        self.stats["writes"] += 1
        self.save()

    def save(self) -> None:
        _write_json(self.path, {"version": 1, "entries": self._entries})

    def snapshot_stats(self) -> dict[str, int | str]:
        return {"path": str(self.path), **self.stats}

    def _load_entries(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        payload = _read_json(self.path)
        if isinstance(payload, dict) and isinstance(payload.get("entries"), dict):
            return dict(payload["entries"])
        return payload if isinstance(payload, dict) else {}

    def _now(self) -> datetime:
        value = self._now_fn()
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _parse_created_at(self, value: Any) -> datetime | None:
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _key(self, query: str, *, model: str, dimension: int) -> str:
        return f"{model}:{int(dimension)}:{self._query_hash(query)}"

    def _query_hash(self, query: str) -> str:
        return hashlib.sha256(str(query or "").encode("utf-8")).hexdigest()


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


def _install_embedding_cache(retriever: HybridRetriever, embedding_cache: QueryEmbeddingCache | None) -> None:
    if embedding_cache is None or getattr(retriever._vector, "_benchmark_embedding_cache_installed", False):
        return
    vector = retriever._vector
    original_embed = vector._embed

    def cached_embed(text: str) -> list[float]:
        cached = embedding_cache.get(text, model=vector.model, dimension=vector.dimension)
        if cached is not None:
            return cached
        embedding = original_embed(text)
        embedding_cache.set(text, model=vector.model, dimension=vector.dimension, embedding=embedding)
        return embedding

    vector._embed = cached_embed
    vector._benchmark_embedding_cache_installed = True


def _search_vector_with_retries(
    retriever: HybridRetriever,
    cur,
    question: str,
    graph_intent: str | None = None,
    max_attempts: int = 1,
    embedding_cache: QueryEmbeddingCache | None = None,
) -> list[tuple]:
    _install_embedding_cache(retriever, embedding_cache)
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return retriever._vector.search_all(
                cur,
                question,
                top_k=retriever.top_k,
                domain=retriever.domain,
            )
        except Exception as exc:
            last_error = exc
            if attempt >= max_attempts:
                break
            time.sleep(0.5 * attempt)
    assert last_error is not None
    raise last_error


def _benchmark_diagnostics_from_debug(
    retriever: HybridRetriever,
    *,
    graph_intent: str | None,
    timings: dict[str, float],
    embedding_cache: QueryEmbeddingCache | None,
    debug: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(debug, dict):
        return None

    grep_results = debug.get("grepResults") if isinstance(debug.get("grepResults"), dict) else {}
    vector_results = debug.get("vectorResults") if isinstance(debug.get("vectorResults"), list) else []
    graph_results = debug.get("graphResults") if isinstance(debug.get("graphResults"), list) else []
    web_results = debug.get("webResults") if isinstance(debug.get("webResults"), list) else []
    pre_fused = debug.get("preFused") if isinstance(debug.get("preFused"), list) else []
    post_fused = debug.get("postFused") if isinstance(debug.get("postFused"), list) else []
    prerequisite_evidence = (
        debug.get("prerequisiteEvidence") if isinstance(debug.get("prerequisiteEvidence"), dict) else {}
    )

    return {
        "retrievalGraphIntent": graph_intent,
        "channelErrors": debug.get("channelErrors", {}),
        "embeddingCache": embedding_cache.snapshot_stats() if embedding_cache else None,
        "graphSeedSlugs": debug.get("graphSeedSlugs", []),
        "channelsTopN": {
            "grepPriority": _top_channel_items(grep_results.get("priority", []), 10),
            "grepNormal": _top_channel_items(grep_results.get("normal", []), 10),
            "vector": _top_channel_items(vector_results, 10),
            "graph": _top_channel_items(graph_results, 10),
            "web": _top_channel_items(web_results, 10),
        },
        "preFused": _top_channel_items(pre_fused, 10),
        "postFused": _top_channel_items(post_fused, 10),
        "fusionReplacementsTop5": _fusion_replacements(pre_fused, post_fused),
        "prerequisiteEvidence": retriever._format_prerequisite_evidence(prerequisite_evidence),
        "top5Stabilization": debug.get("top5Stabilization", {}),
        "strongGrepTop3": debug.get("strongGrepTop3", {}),
        "strongGrepTop": debug.get("strongGrepTop", {}),
        "strongGrepEvidence": debug.get("strongGrepEvidence", {}),
        "queryObjectTop3": debug.get("queryObjectTop3", {}),
        "explicitGraphEvidence": debug.get("explicitGraphEvidence", {}),
        "wikiTraversal": {
            **(debug.get("wikiTraversal", {}) if isinstance(debug.get("wikiTraversal"), dict) else {}),
            "wiki_neighbors_ms": round(timings.get("graph_ms", 0.0), 2),
        },
        "lowValueSources": _summarize_low_value_sources(
            grep_results=grep_results,
            vector_results=vector_results,
            graph_results=graph_results,
            web_results=web_results,
        ),
        "graphCandidateExplainTop50": debug.get("graphCandidateExplainTop50", {}),
    }


def run_retrieval(
    question: str,
    *,
    graph_intent: str | None = None,
    retrieval_strategy: str = "LOCAL_HYBRID",
    include_diagnostics: bool = False,
    embedding_cache: QueryEmbeddingCache | None = None,
) -> tuple[dict[str, Any], dict[str, float], float, str | None]:
    retriever = HybridRetriever(DB_CONFIG, top_k=TOP_K)
    timings = {"init_ms": 0.0, "grep_ms": 0.0, "vector_ms": 0.0, "graph_ms": 0.0, "web_ms": 0.0, "fusion_ms": 0.0}
    error: str | None = None
    started = time.perf_counter()
    result: dict[str, Any] = {"query": question, "graphIntent": graph_intent, "channels": {}, "top": []}

    with psycopg2.connect(**DB_CONFIG) as conn:
        with conn.cursor() as cur:
            try:
                with channel_timer(timings, "init_ms"):
                    retriever.initialize(cur)
                    _install_embedding_cache(retriever, embedding_cache)

                strategy = retrieval_strategy.strip().upper()
                retrieve_fn = retriever.retrieve_grep_first if strategy == "LOCAL_GREP_FIRST" else retriever.retrieve
                result = retrieve_fn(
                    cur,
                    question,
                    web_search_enabled=False,
                    graph_intent=graph_intent,
                    timings=timings,
                    include_diagnostics=include_diagnostics,
                )
                result.setdefault("retrievalStrategy", strategy if strategy == "LOCAL_GREP_FIRST" else "LOCAL_HYBRID")
                debug = result.pop("_retrievalDebug", None)
                diagnostics = _benchmark_diagnostics_from_debug(
                    retriever,
                    graph_intent=graph_intent,
                    timings=timings,
                    embedding_cache=embedding_cache,
                    debug=debug,
                )
                if diagnostics:
                    result["diagnostics"] = diagnostics
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"

    total_ms = (time.perf_counter() - started) * 1000
    result.setdefault("channelErrors", {})
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


async def benchmark_questions(
    *,
    questions_path: Path,
    output_path: Path,
    judge_cache_path: Path,
    retrieval_strategy: str = "LOCAL_HYBRID",
    embedding_cache_path: Path | None = DEFAULT_EMBEDDING_CACHE,
    embedding_cache_ttl_days: int = DEFAULT_EMBEDDING_CACHE_TTL_DAYS,
    quality_thresholds: dict[str, float] | None = None,
    judge_max_attempts: int = DEFAULT_JUDGE_MAX_ATTEMPTS,
    judge_retry_base_seconds: float = DEFAULT_JUDGE_RETRY_BASE_SECONDS,
) -> dict[str, Any]:
    question_set = _read_json(questions_path)
    questions = question_set.get("questions", [])
    if not isinstance(questions, list) or not questions:
        raise RuntimeError(f"invalid question set: {questions_path}")

    judge = LLMRetrievalJudge()
    judge_cache = _load_judge_cache(judge_cache_path)
    embedding_cache = (
        QueryEmbeddingCache(embedding_cache_path, ttl_days=embedding_cache_ttl_days)
        if embedding_cache_path
        else None
    )
    records = []
    total_started = time.perf_counter()

    for index, question_item in enumerate(questions, start=1):
        question = str(question_item.get("question") or "")
        result, timings, total_ms, error = run_retrieval(
            question,
            retrieval_strategy=retrieval_strategy,
            embedding_cache=embedding_cache,
        )
        candidates = _top_candidates(result)
        cache_key = _judge_cache_key(question_item, candidates)
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
            "channelErrors": result.get("channelErrors", {}),
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

    summary = summarize_records(records)
    summary.update(summarize_rag_quality_gates(summary, thresholds=quality_thresholds))
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
            "retrievalStrategy": retrieval_strategy,
            "embeddingCache": embedding_cache.snapshot_stats() if embedding_cache else {"enabled": False},
        },
        "summary": summary,
        "records": records,
        "elapsedSeconds": round(time.perf_counter() - total_started, 2),
    }
    _write_json(output_path, report)
    return report


async def _judge_with_retries(
    judge: LLMRetrievalJudge,
    question_item: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    max_attempts: int = DEFAULT_JUDGE_MAX_ATTEMPTS,
    retry_base_seconds: float = DEFAULT_JUDGE_RETRY_BASE_SECONDS,
) -> dict[str, Any]:
    attempts = max(1, int(max_attempts))
    last_error: LLMServiceError | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await judge.judge(question_item, candidates)
        except LLMServiceError as exc:
            last_error = exc
            if not exc.retryable or attempt >= attempts:
                raise
            await asyncio.sleep(max(0.0, retry_base_seconds) * attempt)
    assert last_error is not None
    raise last_error


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
        **summarize_channel_errors(records),
    }


def summarize_rag_quality_gates(
    summary: dict[str, Any],
    *,
    thresholds: dict[str, float] | None = None,
) -> dict[str, Any]:
    gate_thresholds = {
        "hitAt3Pct": RAG_HIT_AT3_MIN_PCT,
        "successRatePct": RAG_SUCCESS_RATE_MIN_PCT,
        "avgLatencyMs": RAG_AVG_LATENCY_MAX_MS,
        "p95LatencyMs": RAG_P95_LATENCY_MAX_MS,
        "channelErrorCount": RAG_CHANNEL_ERROR_MAX,
    }
    if thresholds:
        gate_thresholds.update({key: float(value) for key, value in thresholds.items() if key in gate_thresholds})

    pass_hit_at3 = float(summary.get("hitAt3Pct") or 0.0) >= gate_thresholds["hitAt3Pct"]
    pass_success_rate = float(summary.get("successRatePct") or 0.0) >= gate_thresholds["successRatePct"]
    pass_latency = (
        float(summary.get("avgLatencyMs") or 0.0) <= gate_thresholds["avgLatencyMs"]
        and float(summary.get("p95LatencyMs") or 0.0) <= gate_thresholds["p95LatencyMs"]
    )
    pass_channel_errors = int(summary.get("channelErrorCount") or 0) <= int(gate_thresholds["channelErrorCount"])
    return {
        "passHitAt3": pass_hit_at3,
        "passSuccessRate": pass_success_rate,
        "passLatency": pass_latency,
        "passChannelErrors": pass_channel_errors,
        "overallPass": all([pass_hit_at3, pass_success_rate, pass_latency, pass_channel_errors]),
        "thresholds": gate_thresholds,
    }


def summarize_channel_errors(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_channel: dict[str, int] = {}
    questions = []
    for record in records:
        channel_errors = record.get("channelErrors")
        if not isinstance(channel_errors, dict):
            channel_errors = record.get("diagnostics", {}).get("channelErrors", {})
        if not isinstance(channel_errors, dict) or not channel_errors:
            continue

        questions.append(str(record.get("id") or ""))
        for channel in channel_errors:
            by_channel[str(channel)] = by_channel.get(str(channel), 0) + 1

    return {
        "channelErrorCount": sum(by_channel.values()),
        "channelErrorQuestions": questions,
        "channelErrorByChannel": dict(sorted(by_channel.items())),
    }


def _quality_thresholds_from_args(args: argparse.Namespace) -> dict[str, float]:
    return {
        "hitAt3Pct": args.hit_at3_min_pct,
        "successRatePct": args.success_rate_min_pct,
        "avgLatencyMs": args.avg_latency_max_ms,
        "p95LatencyMs": args.p95_latency_max_ms,
        "channelErrorCount": args.channel_error_max,
    }


def _failed_rag_quality_gate_names(summary: dict[str, Any]) -> list[str]:
    return [
        key
        for key in (
            "passHitAt3",
            "passSuccessRate",
            "passLatency",
            "passChannelErrors",
        )
        if summary.get(key) is False
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--generate", action="store_true")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--count", type=int, default=DEFAULT_QUESTION_COUNT)
    parser.add_argument("--questions", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--judge-cache", type=Path, default=DEFAULT_JUDGE_CACHE)
    parser.add_argument("--embedding-cache", type=Path, default=DEFAULT_EMBEDDING_CACHE)
    parser.add_argument("--embedding-cache-ttl-days", type=int, default=DEFAULT_EMBEDDING_CACHE_TTL_DAYS)
    parser.add_argument("--no-embedding-cache", action="store_true")
    parser.add_argument("--judge-max-attempts", type=int, default=DEFAULT_JUDGE_MAX_ATTEMPTS)
    parser.add_argument("--judge-retry-base-seconds", type=float, default=DEFAULT_JUDGE_RETRY_BASE_SECONDS)
    parser.add_argument("--hit-at3-min-pct", type=float, default=RAG_HIT_AT3_MIN_PCT)
    parser.add_argument("--success-rate-min-pct", type=float, default=RAG_SUCCESS_RATE_MIN_PCT)
    parser.add_argument("--avg-latency-max-ms", type=float, default=RAG_AVG_LATENCY_MAX_MS)
    parser.add_argument("--p95-latency-max-ms", type=float, default=RAG_P95_LATENCY_MAX_MS)
    parser.add_argument("--channel-error-max", type=int, default=RAG_CHANNEL_ERROR_MAX)
    parser.add_argument(
        "--no-fail-on-gate",
        action="store_true",
        help="Write the report but keep exit code 0 when the quality gate fails.",
    )
    parser.add_argument(
        "--retrieval-strategy",
        choices=["LOCAL_HYBRID", "LOCAL_GREP_FIRST"],
        default="LOCAL_HYBRID",
    )
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
            retrieval_strategy=args.retrieval_strategy,
            embedding_cache_path=None if args.no_embedding_cache else args.embedding_cache,
            embedding_cache_ttl_days=args.embedding_cache_ttl_days,
            quality_thresholds=_quality_thresholds_from_args(args),
            judge_max_attempts=args.judge_max_attempts,
            judge_retry_base_seconds=args.judge_retry_base_seconds,
        )
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"Saved report to {args.output}")
    if not args.no_fail_on_gate and not report["summary"].get("overallPass"):
        failed_gates = ", ".join(_failed_rag_quality_gate_names(report["summary"])) or "overallPass"
        raise SystemExit(f"RAG benchmark quality gate failed: {failed_gates}")


if __name__ == "__main__":
    main()
