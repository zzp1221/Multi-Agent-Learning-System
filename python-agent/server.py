"""FastAPI entrypoint for the zhixue Python agent runtime."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
import tempfile
import uuid
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse
from opentelemetry import trace
from pydantic import BaseModel, ConfigDict, Field
import psycopg2
import psycopg2.extras

from src.ai_modules.config import get_settings
from src.ai_modules.generation.content_chain import OpenAICompatibleStructuredGenerator
from src.ai_modules.llms.openai_compatible import OpenAICompatibleClient
from src.ai_modules.memory import ConversationMessageDocument, MongoConversationMessageStore
from src.ai_modules.models import DonePayload, DoneSSEEvent, EngineStreamRequest, ErrorPayload, ErrorSSEEvent
from src.ai_modules.observability import configure_observability
from src.ai_modules.runtime.smart_engine_stream_worker import SmartEngineStreamWorker
from src.ai_modules.supervisor import PythonAgentSupervisor

LOGGER = logging.getLogger(__name__)
SETTINGS = get_settings()
TRACER = trace.get_tracer(__name__)
SUPERVISOR = PythonAgentSupervisor()
MESSAGE_STORE = MongoConversationMessageStore()
ACTIVE_STREAM_TASKS: dict[str, asyncio.Task[None]] = {}
INTERNAL_TOKEN_HEADER = "X-Zhixue-Internal-Token"
INTERNAL_TOKEN_FILE = Path("/run/secrets/zhixue-python-agent-internal-token")
RESOURCE_IMPORT_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "zhixue-ai-resource-library")


class FileCancelledTasks:
    """Cross-worker cancellation markers stored on the shared local filesystem."""

    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir

    def ensure_ready(self) -> None:
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def _marker_path(self, task_id: str) -> Path:
        digest = hashlib.sha256(task_id.encode("utf-8")).hexdigest()
        return self.root_dir / f"{digest}.cancelled"

    def add(self, task_id: str) -> None:
        self.ensure_ready()
        self._marker_path(task_id).touch()

    def discard(self, task_id: str) -> None:
        self._marker_path(task_id).unlink(missing_ok=True)

    def __contains__(self, task_id: object) -> bool:
        if not isinstance(task_id, str):
            return False
        return self._marker_path(task_id).exists()


CANCELLED_TASKS = FileCancelledTasks(
    Path(tempfile.gettempdir()) / SETTINGS.app_name / "task-cancellations"
)


class InternalConversationMessageRequest(BaseModel):
    """Append a single transcript message for a conversation."""

    role: str
    content: str
    image_urls: list[str] = Field(default_factory=list, alias="imageUrls")
    user_id: str | None = Field(default=None, alias="userId")

    model_config = ConfigDict(populate_by_name=True)


class ResourceSemanticHit(BaseModel):
    chunk_id: int = Field(alias="chunkId")
    chunk_no: int = Field(alias="chunkNo")
    similarity: float
    content: str
    source_url: str = Field(default="", alias="sourceUrl")

    model_config = ConfigDict(populate_by_name=True)


class ResourceExternalCandidate(BaseModel):
    title: str
    source_url: str = Field(alias="sourceUrl")
    source_name: str = Field(default="", alias="sourceName")
    summary_text: str = Field(default="", alias="summaryText")
    resource_type: str = Field(default="READING", alias="resourceType")
    display_type: str = Field(default="DOCUMENT", alias="displayType")
    difficulty_level: str = Field(default="MIXED", alias="difficultyLevel")
    cover_url: str = Field(default="", alias="coverUrl")
    quality_score: float = Field(default=0.6, alias="qualityScore")
    popularity_score: float = Field(default=0.0, alias="popularityScore")
    tags: list[str] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


class ResourceSemanticResult(BaseModel):
    resource_id: str = Field(alias="resourceId")
    score: float
    reason: str
    hits: list[ResourceSemanticHit] = Field(default_factory=list)
    external_resource: ResourceExternalCandidate | None = Field(default=None, alias="externalResource")

    model_config = ConfigDict(populate_by_name=True)


class ResourceSemanticSearchResponse(BaseModel):
    query: str
    available: bool
    message: str
    results: list[ResourceSemanticResult] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


class NoteTodo(BaseModel):
    title: str
    priority: str = "MEDIUM"
    completed: bool = False


class NoteAnalysisRequest(BaseModel):
    title: str = ""
    markdown_content: str = Field(default="", alias="markdownContent")
    plain_text: str = Field(default="", alias="plainText")

    model_config = ConfigDict(populate_by_name=True)


class NoteAnalysisResponse(BaseModel):
    summary: str
    keywords: list[str] = Field(default_factory=list)
    todos: list[NoteTodo] = Field(default_factory=list)
    provider: str = "heuristic"
    model: str = "local-note-analyzer"

    model_config = ConfigDict(populate_by_name=True)


class NoteIndexRequest(BaseModel):
    user_id: str = Field(alias="userId")
    note_id: str = Field(alias="noteId")
    resource_id: str = Field(alias="resourceId")
    title: str
    markdown_content: str = Field(default="", alias="markdownContent")
    plain_text: str = Field(default="", alias="plainText")
    content_hash: str = Field(alias="contentHash")
    tags: list[str] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


class NoteIndexResponse(BaseModel):
    indexed: bool
    chunk_count: int = Field(alias="chunkCount")
    message: str

    model_config = ConfigDict(populate_by_name=True)


class NoteSemanticHit(BaseModel):
    chunk_id: int = Field(alias="chunkId")
    chunk_no: int = Field(alias="chunkNo")
    similarity: float
    content: str

    model_config = ConfigDict(populate_by_name=True)


class NoteSemanticResult(BaseModel):
    note_id: str = Field(alias="noteId")
    resource_id: str = Field(alias="resourceId")
    score: float
    reason: str
    hits: list[NoteSemanticHit] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


class NoteSemanticSearchResponse(BaseModel):
    query: str
    available: bool
    message: str
    results: list[NoteSemanticResult] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


def verify_internal_token(
    x_zhixue_internal_token: str | None = Header(default=None, alias=INTERNAL_TOKEN_HEADER),
) -> None:
    """Validate Java control-plane calls to internal Python Agent endpoints."""

    expected_token = internal_token()
    if not expected_token:
        LOGGER.error("PYTHON_AGENT_INTERNAL_TOKEN is not configured; rejecting internal request")
        raise HTTPException(status_code=503, detail="Internal token is not configured")

    supplied_token = (x_zhixue_internal_token or "").strip()
    if not supplied_token or not secrets.compare_digest(supplied_token, expected_token):
        raise HTTPException(status_code=401, detail="Invalid internal token")


def internal_token() -> str:
    configured_token = SETTINGS.python_agent_internal_token.strip()
    if configured_token:
        return configured_token
    env_token = os.getenv("PYTHON_AGENT_INTERNAL_TOKEN", "").strip()
    if env_token:
        return env_token
    try:
        return INTERNAL_TOKEN_FILE.read_text(encoding="utf-8").strip() if INTERNAL_TOKEN_FILE.exists() else ""
    except OSError as exc:
        LOGGER.warning("Failed to read internal token file %s: %s", INTERNAL_TOKEN_FILE, exc)
        return ""


def _embedding_vec_str(vec: list[float]) -> str:
    return "[" + ",".join(str(v) for v in vec) + "]"


def _resource_uuid(url: str) -> str:
    return str(uuid.uuid5(RESOURCE_IMPORT_NAMESPACE, f"resource:{url}"))


def _embed_resource_query(query: str) -> list[float]:
    from retrieval.vector_searcher import VectorSearcher

    return VectorSearcher(
        dimension=SETTINGS.knowledge_embedding_dimension,
        model=SETTINGS.knowledge_embedding_model_name,
    )._embed(query)


def _search_resource_chunks(query: str, top_k: int, domain: str | None = None, user_id: str | None = None) -> list[dict]:
    embedding = _embed_resource_query(query)
    vec_str = _embedding_vec_str(embedding)
    db_config = SETTINGS.postgres_connect_kwargs()
    params: list[object] = [vec_str, domain, domain, user_id, user_id, user_id, user_id, vec_str, top_k]

    with psycopg2.connect(**db_config) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                  rc.id AS chunk_id,
                  rc.resource_id::text AS resource_id,
                  rc.chunk_no,
                  rc.content,
                  ROUND((1 - (rc.embedding <=> %s::vector))::numeric, 4) AS similarity,
                  COALESCE(rd.source_ref, lr.metadata_json ->> 'sourceUrl', '') AS source_url
                FROM rag.resource_chunk rc
                JOIN rag.resource_document rd ON rd.id = rc.document_id
                JOIN app.learning_resource lr ON lr.id = rc.resource_id
                WHERE lr.status = 'ACTIVE'
                  AND (%s IS NULL OR rc.domain = %s)
                  AND (
                    rc.access_scope::text = 'GLOBAL'
                    OR (
                      %s IS NOT NULL
                      AND rc.access_scope::text = 'USER'
                      AND rc.owner_user_id = %s::uuid
                    )
                    OR (
                      %s IS NOT NULL
                      AND rc.access_scope::text = 'COURSE'
                      AND EXISTS (
                        SELECT 1
                        FROM app.user_course_enrollments e
                        WHERE e.user_id = %s::uuid AND e.course_id = rc.course_id
                      )
                    )
                  )
                ORDER BY rc.embedding <=> %s::vector
                LIMIT %s
                """,
                params,
            )
            return [dict(row) for row in cur.fetchall()]


def _search_resource_chunks_with_hybrid_rag(
    query: str,
    top_k: int,
    domain: str | None = None,
    user_id: str | None = None,
) -> list[dict]:
    try:
        from src.ai_modules.retrieval import HybridRetrievalService

        raw_result = HybridRetrievalService().retrieve_raw(
            query,
            web_search_enabled=False,
        )
    except Exception as exc:
        LOGGER.warning("Hybrid RAG resource lookup skipped for query=%r: %s", query, exc)
        return []

    refs = _hybrid_rag_candidate_refs(raw_result, top_k=top_k)
    if not refs:
        return []
    return _resource_rows_for_hybrid_refs(refs, top_k=top_k, domain=domain, user_id=user_id)


async def _search_resource_tavily_candidates(query: str, top_k: int) -> list[ResourceExternalCandidate]:
    if top_k <= 0 or not SETTINGS.tavily_api_key.strip():
        return []
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                SETTINGS.tavily_base_url,
                json={
                    "api_key": SETTINGS.tavily_api_key,
                    "query": query,
                    "topic": "general",
                    "search_depth": "advanced",
                    "max_results": min(20, max(top_k * 3, top_k)),
                    "include_images": True,
                    "include_answer": False,
                    "include_raw_content": False,
                },
            )
            response.raise_for_status()
    except Exception as exc:
        LOGGER.warning("Tavily resource fallback failed for query=%r: %s", query, exc)
        return []

    payload = response.json()
    raw_results = payload.get("results")
    if not isinstance(raw_results, list):
        return []

    candidates: list[ResourceExternalCandidate] = []
    seen_urls: set[str] = set()
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        url = _clean_text(item.get("url"))
        title = _clean_text(item.get("title"))
        if not title or not _is_http_url(url):
            continue
        normalized_url = url.rstrip("/")
        if normalized_url.lower() in seen_urls:
            continue
        seen_urls.add(normalized_url.lower())
        summary = _clean_text(item.get("content"))
        source_name = _source_name(normalized_url)
        resource_type, display_type = _classify_external_resource(normalized_url)
        candidates.append(
            ResourceExternalCandidate(
                title=title[:160],
                sourceUrl=normalized_url,
                sourceName=source_name,
                summaryText=summary[:500],
                resourceType=resource_type,
                displayType=display_type,
                difficultyLevel="MIXED",
                coverUrl=_extract_tavily_image(item, payload),
                qualityScore=0.6,
                popularityScore=0.0,
                tags=_external_resource_tags(query),
            )
        )
        if len(candidates) >= top_k:
            break
    return candidates


def _clean_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()


def _is_http_url(value: str) -> bool:
    lowered = value.lower()
    return lowered.startswith("http://") or lowered.startswith("https://")


def _source_name(url: str) -> str:
    from urllib.parse import urlparse

    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _classify_external_resource(url: str) -> tuple[str, str]:
    lowered = url.lower()
    source_name = _source_name(url)
    if source_name in {"bilibili.com", "youtube.com", "youtu.be"}:
        return "VIDEO", "VIDEO"
    if source_name in {"github.com", "gitee.com", "gitlab.com", "gist.github.com"}:
        return "CODE", "CASE"
    if lowered.endswith(".pdf"):
        return "DOCUMENT", "DOCUMENT"
    return "READING", "DOCUMENT"


def _extract_tavily_image(item: dict[str, Any], payload: dict[str, Any]) -> str:
    for key in ("thumbnailUrl", "thumbnail_url", "image", "imageUrl"):
        value = item.get(key)
        if isinstance(value, str) and _is_http_url(value):
            return value
    item_images = item.get("images")
    if isinstance(item_images, list):
        for value in item_images:
            if isinstance(value, str) and _is_http_url(value):
                return value
    payload_images = payload.get("images")
    if isinstance(payload_images, list):
        for value in payload_images:
            if isinstance(value, str) and _is_http_url(value):
                return value
    return ""


def _external_resource_tags(query: str) -> list[str]:
    terms = re.findall(r"[A-Za-z0-9+\-#_.]{2,}|[\u4e00-\u9fff]{2,}", query)
    seen: set[str] = set()
    tags: list[str] = []
    for term in terms:
        normalized = term.strip()
        key = normalized.lower()
        if not normalized or key in seen:
            continue
        seen.add(key)
        tags.append(normalized[:32])
        if len(tags) >= 6:
            break
    return tags


def _hybrid_rag_candidate_refs(raw_result: dict[str, Any], top_k: int) -> list[str]:
    if not isinstance(raw_result, dict):
        return []
    refs: list[str] = []
    seen: set[str] = set()

    def append_ref(value: Any) -> None:
        ref = str(value or "").strip()
        if not ref:
            return
        normalized = ref.lower()
        if normalized in seen:
            return
        seen.add(normalized)
        refs.append(ref)

    for item in raw_result.get("top", []):
        if isinstance(item, (list, tuple)) and item:
            append_ref(item[0])

    channels = raw_result.get("channels", {})
    vector_results = channels.get("vector", []) if isinstance(channels, dict) else []
    for item in vector_results:
        if not isinstance(item, (list, tuple)) or len(item) < 4:
            continue
        if str(item[3]).strip().lower() == "resource":
            append_ref(item[0])

    return refs[: max(top_k * 4, top_k)]


def _resource_rows_for_hybrid_refs(
    refs: list[str],
    top_k: int,
    domain: str | None = None,
    user_id: str | None = None,
) -> list[dict]:
    db_config = SETTINGS.postgres_connect_kwargs()
    with psycopg2.connect(**db_config) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                WITH ranked_ref AS (
                  SELECT ref, ordinality::int AS rag_rank
                  FROM unnest(%s::text[]) WITH ORDINALITY AS candidate(ref, ordinality)
                ),
                matched_resources AS (
                  SELECT DISTINCT ON (lr.id)
                    rc.id AS chunk_id,
                    rc.resource_id::text AS resource_id,
                    rc.chunk_no,
                    rc.content,
                    ROUND((1.0 / (60 + ranked_ref.rag_rank))::numeric, 4) AS similarity,
                    COALESCE(rd.source_ref, lr.metadata_json ->> 'sourceUrl', '') AS source_url,
                    concat_ws(' ', rd.title, rd.summary_text, rd.transcript_text, lr.title, lr.summary_text, lr.tags::text, lr.metadata_json::text, rc.content) AS searchable_text,
                    'hybrid_rag' AS retrieval_channel,
                    ranked_ref.rag_rank
                  FROM ranked_ref
                  JOIN rag.resource_document rd ON rd.source_ref = ranked_ref.ref
                  JOIN app.learning_resource lr ON lr.id = rd.resource_id
                  JOIN LATERAL (
                    SELECT rc.*
                    FROM rag.resource_chunk rc
                    WHERE rc.document_id = rd.id
                      AND (%s IS NULL OR rc.domain = %s)
                      AND (
                        rc.access_scope::text = 'GLOBAL'
                        OR (
                          %s IS NOT NULL
                          AND rc.access_scope::text = 'USER'
                          AND rc.owner_user_id = %s::uuid
                        )
                        OR (
                          %s IS NOT NULL
                          AND rc.access_scope::text = 'COURSE'
                          AND EXISTS (
                            SELECT 1
                            FROM app.user_course_enrollments e
                            WHERE e.user_id = %s::uuid AND e.course_id = rc.course_id
                          )
                        )
                      )
                    ORDER BY rc.chunk_no ASC
                    LIMIT 1
                  ) rc ON TRUE
                  WHERE lr.status = 'ACTIVE'
                  ORDER BY lr.id, ranked_ref.rag_rank, rc.chunk_no
                )
                SELECT
                  chunk_id,
                  resource_id,
                  chunk_no,
                  content,
                  similarity,
                  similarity AS rank_score,
                  source_url,
                  searchable_text,
                  retrieval_channel,
                  '现有多管道RAG召回' AS retrieval_reason
                FROM matched_resources
                ORDER BY rag_rank
                LIMIT %s
                """,
                [refs, domain, domain, user_id, user_id, user_id, user_id, top_k],
            )
            return [dict(row) for row in cur.fetchall()]


def _build_resource_semantic_response(
    query: str,
    rows: list[dict],
    external_candidates: list[ResourceExternalCandidate] | None = None,
) -> ResourceSemanticSearchResponse:
    grouped: dict[str, ResourceSemanticResult] = {}
    for row in rows:
        resource_id = str(row.get("resource_id") or "")
        if not resource_id:
            continue
        try:
            similarity = float(row.get("similarity") or 0)
        except (TypeError, ValueError):
            similarity = 0.0
        score = float(row.get("rank_score") or similarity)
        reason = str(row.get("retrieval_reason") or "向量语义匹配")
        hit = ResourceSemanticHit(
            chunkId=int(row.get("chunk_id") or 0),
            chunkNo=int(row.get("chunk_no") or 0),
            similarity=similarity,
            content=str(row.get("content") or "")[:800],
            sourceUrl=str(row.get("source_url") or ""),
        )
        result = grouped.get(resource_id)
        if result is None:
            grouped[resource_id] = ResourceSemanticResult(
                resourceId=resource_id,
                score=score,
                reason=reason,
                hits=[hit],
            )
        else:
            result.hits.append(hit)
            result.score = max(result.score, score)
    results = sorted(grouped.values(), key=lambda item: item.score, reverse=True)
    seen_resource_ids = {item.resource_id for item in results}
    for index, candidate in enumerate(external_candidates or [], start=1):
        resource_id = _resource_uuid(candidate.source_url)
        if resource_id in seen_resource_ids:
            continue
        seen_resource_ids.add(resource_id)
        results.append(
            ResourceSemanticResult(
                resourceId=resource_id,
                score=round(max(0.1, 0.75 - index * 0.01), 4),
                reason="tavily_current_stage_fallback",
                hits=[],
                externalResource=candidate,
            )
        )
    return ResourceSemanticSearchResponse(
        query=query,
        available=True,
        message="ok" if results else "没有匹配到可用资源",
        results=results,
    )


def _note_plain_text(request: NoteAnalysisRequest | NoteIndexRequest) -> str:
    text = (request.plain_text or "").strip()
    if text:
        return text
    markdown = (request.markdown_content or "").strip()
    no_code = re.sub(r"```[\s\S]*?```", " ", markdown)
    no_links = re.sub(r"!\[[^\]]*]\([^)]+\)", " ", no_code)
    no_links = re.sub(r"\[([^\]]+)]\([^)]+\)", r"\1", no_links)
    return re.sub(r"\s+", " ", re.sub(r"[#*_>`~\-\[\]()]", " ", no_links)).strip()


def _split_note_chunks(title: str, plain_text: str, max_chars: int = 900) -> list[str]:
    normalized = re.sub(r"\s+", " ", plain_text).strip()
    if not normalized:
        normalized = title.strip()
    sentences = re.split(r"(?<=[。！？!?；;])\s*", normalized)
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(current) + len(sentence) + 1 > max_chars and current:
            chunks.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        chunks.append(current)
    if not chunks and normalized:
        chunks.append(normalized[:max_chars])
    return chunks[:80]


def _heuristic_note_analysis(request: NoteAnalysisRequest) -> NoteAnalysisResponse:
    title = request.title.strip() or "未命名笔记"
    plain_text = _note_plain_text(request)
    sentences = [item.strip() for item in re.split(r"[。！？!?\n\r]+", plain_text) if item.strip()]
    summary_source = "。".join(sentences[:3]) if sentences else plain_text[:180]
    summary = summary_source[:360] if summary_source else f"{title} 的笔记内容较少，建议继续补充核心概念、例子和疑问。"
    keywords = _extract_note_keywords(title, plain_text)
    todos = _extract_note_todos(plain_text)
    if not todos:
        todos = [
            NoteTodo(title=f"复习《{title}》的核心概念", priority="MEDIUM", completed=False),
            NoteTodo(title="补充一个例题或反例，检查理解是否稳固", priority="LOW", completed=False),
        ]
    return NoteAnalysisResponse(summary=summary, keywords=keywords, todos=todos)


def _extract_note_keywords(title: str, plain_text: str) -> list[str]:
    candidates: list[str] = []
    for source in (title, plain_text):
        for token in re.findall(r"[\u4e00-\u9fa5A-Za-z0-9_+\-#]{2,24}", source):
            cleaned = token.strip("，。！？；：、,.!?;:")
            if len(cleaned) >= 2:
                candidates.append(cleaned)
    stop_words = {"这是", "一个", "可以", "需要", "如果", "以及", "或者", "因为", "所以", "进行", "通过", "理解"}
    seen: set[str] = set()
    ranked: list[str] = []
    for item in candidates:
        if item in stop_words or item.lower() in seen:
            continue
        seen.add(item.lower())
        ranked.append(item)
        if len(ranked) >= 8:
            break
    return ranked


def _extract_note_todos(plain_text: str) -> list[NoteTodo]:
    todos: list[NoteTodo] = []
    for line in plain_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if any(marker in stripped for marker in ("TODO", "待办", "复习", "练习", "实现", "完成")):
            todos.append(NoteTodo(title=stripped[:120], priority="MEDIUM", completed=False))
        if len(todos) >= 6:
            break
    return todos


async def _llm_note_analysis(request: NoteAnalysisRequest) -> NoteAnalysisResponse:
    generator = OpenAICompatibleStructuredGenerator()
    content = _note_plain_text(request)
    prompt = (
        "请分析下面的学习笔记，输出严格 JSON："
        '{"summary":"120字以内摘要","keywords":["知识点"],'
        '"todos":[{"title":"待办","priority":"HIGH|MEDIUM|LOW","completed":false}]}。\n'
        f"标题：{request.title}\n"
        f"正文：{content[:8000]}"
    )
    payload = await generator._call_and_parse_json_async(
        span_name="note.analyze",
        system_prompt="你是学习笔记分析助手，只输出 JSON，不编造正文中没有的结论。",
        user_prompt=prompt,
        max_tokens=1200,
        schema_hint='{"summary":"...","keywords":["..."],"todos":[{"title":"...","priority":"MEDIUM","completed":false}]}',
    )
    todos: list[NoteTodo] = []
    for item in payload.get("todos", []):
        if isinstance(item, dict):
            todos.append(
                NoteTodo(
                    title=str(item.get("title") or "").strip()[:120],
                    priority=str(item.get("priority") or "MEDIUM").strip().upper() or "MEDIUM",
                    completed=bool(item.get("completed") is True),
                )
            )
    keywords = [
        str(item).strip()[:32]
        for item in payload.get("keywords", [])
        if str(item).strip()
    ][:8]
    return NoteAnalysisResponse(
        summary=str(payload.get("summary") or "").strip()[:500],
        keywords=keywords,
        todos=[todo for todo in todos if todo.title][:8],
        provider=generator.provider_name,
        model=generator.model_name,
    )


def _index_note_chunks(request: NoteIndexRequest) -> NoteIndexResponse:
    plain_text = _note_plain_text(request)
    chunks = _split_note_chunks(request.title, plain_text)
    if not chunks:
        return NoteIndexResponse(indexed=False, chunkCount=0, message="笔记内容为空，未生成索引")
    db_config = SETTINGS.postgres_connect_kwargs()
    with psycopg2.connect(**db_config) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO rag.resource_document (
                  resource_id, title, domain, resource_type, difficulty_level, source_kind,
                  source_ref, summary_text, transcript_text, access_scope, owner_user_id,
                  metadata_json
                )
                VALUES (
                  %s::uuid, %s, %s, 'READING'::app.resource_type, 'MIXED'::app.difficulty_level,
                  'MANUAL'::app.source_kind, %s, %s, %s, 'USER'::app.access_scope,
                  %s::uuid, %s::jsonb
                )
                ON CONFLICT (resource_id) DO UPDATE SET
                  title = EXCLUDED.title,
                  summary_text = EXCLUDED.summary_text,
                  transcript_text = EXCLUDED.transcript_text,
                  metadata_json = EXCLUDED.metadata_json,
                  updated_at = now()
                RETURNING id
                """,
                (
                    request.resource_id,
                    request.title,
                    SETTINGS.retrieval_domain,
                    f"note:{request.note_id}",
                    plain_text[:500],
                    plain_text,
                    request.user_id,
                    json.dumps(
                        {
                            "noteId": request.note_id,
                            "contentHash": request.content_hash,
                            "tags": request.tags,
                            "displayType": "NOTE",
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
            document_id = cur.fetchone()[0]
            cur.execute("DELETE FROM rag.resource_chunk WHERE resource_id = %s::uuid", (request.resource_id,))
            for index, content in enumerate(chunks, 1):
                embedding = _embed_resource_query(content)
                cur.execute(
                    """
                    INSERT INTO rag.resource_chunk (
                      document_id, resource_id, chunk_no, content, embedding, token_count,
                      domain, resource_type, difficulty_level, access_scope, owner_user_id,
                      quality_score, metadata_json
                    )
                    VALUES (
                      %s::uuid, %s::uuid, %s, %s, %s::vector, %s,
                      %s, 'READING'::app.resource_type, 'MIXED'::app.difficulty_level,
                      'USER'::app.access_scope, %s::uuid, 0.72, %s::jsonb
                    )
                    """,
                    (
                        document_id,
                        request.resource_id,
                        index,
                        content,
                        _embedding_vec_str(embedding),
                        max(1, len(content) // 2),
                        SETTINGS.retrieval_domain,
                        request.user_id,
                        json.dumps(
                            {
                                "noteId": request.note_id,
                                "contentHash": request.content_hash,
                                "chunkKind": "note",
                            },
                            ensure_ascii=False,
                        ),
                    ),
                )
        conn.commit()
    return NoteIndexResponse(indexed=True, chunkCount=len(chunks), message="ok")


def _search_note_chunks(query: str, top_k: int, user_id: str) -> list[dict]:
    embedding = _embed_resource_query(query)
    vec_str = _embedding_vec_str(embedding)
    db_config = SETTINGS.postgres_connect_kwargs()
    with psycopg2.connect(**db_config) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                  rc.id AS chunk_id,
                  rc.resource_id::text AS resource_id,
                  rc.chunk_no,
                  rc.content,
                  ROUND((1 - (rc.embedding <=> %s::vector))::numeric, 4) AS similarity,
                  rc.metadata_json ->> 'noteId' AS note_id
                FROM rag.resource_chunk rc
                JOIN app.note n ON n.rag_resource_id = rc.resource_id
                WHERE n.user_id = %s::uuid
                  AND n.status = 'ACTIVE'
                  AND rc.access_scope::text = 'USER'
                  AND rc.owner_user_id = %s::uuid
                ORDER BY rc.embedding <=> %s::vector
                LIMIT %s
                """,
                (vec_str, user_id, user_id, vec_str, top_k),
            )
            return [dict(row) for row in cur.fetchall()]


def _build_note_semantic_response(query: str, rows: list[dict]) -> NoteSemanticSearchResponse:
    grouped: dict[str, NoteSemanticResult] = {}
    for row in rows:
        note_id = str(row.get("note_id") or "")
        resource_id = str(row.get("resource_id") or "")
        if not note_id or not resource_id:
            continue
        similarity = float(row.get("similarity") or 0)
        hit = NoteSemanticHit(
            chunkId=int(row.get("chunk_id") or 0),
            chunkNo=int(row.get("chunk_no") or 0),
            similarity=similarity,
            content=str(row.get("content") or "")[:800],
        )
        result = grouped.get(note_id)
        if result is None:
            grouped[note_id] = NoteSemanticResult(
                noteId=note_id,
                resourceId=resource_id,
                score=similarity,
                reason="笔记向量语义匹配",
                hits=[hit],
            )
        else:
            result.hits.append(hit)
            result.score = max(result.score, similarity)
    results = sorted(grouped.values(), key=lambda item: item.score, reverse=True)
    return NoteSemanticSearchResponse(
        query=query,
        available=True,
        message="ok" if results else "没有匹配到可用笔记",
        results=results,
    )


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Application lifecycle hooks."""

    logging.basicConfig(level=logging.INFO)
    configure_observability(SETTINGS)
    CANCELLED_TASKS.ensure_ready()
    LOGGER.info(
        "Starting %s with provider=%s runtime_provider=%s model=%s",
        SETTINGS.app_name,
        SETTINGS.model_provider,
        SETTINGS.runtime_provider_name(),
        SETTINGS.model_name,
    )

    cleanup_task = asyncio.create_task(_sandbox_cleanup_loop())
    stream_worker = SmartEngineStreamWorker(SETTINGS, SUPERVISOR, internal_token)
    stream_worker_task = asyncio.create_task(
        stream_worker.run_forever(),
        name="smart-engine-redis-stream-worker",
    )

    yield

    cleanup_task.cancel()
    stream_worker_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass
    try:
        await stream_worker_task
    except asyncio.CancelledError:
        pass
    except Exception:
        LOGGER.exception("SmartEngine Redis Streams worker stopped with an error")
    await OpenAICompatibleStructuredGenerator.close_async_clients()
    await OpenAICompatibleClient.close_shared_clients()


async def _sandbox_cleanup_loop() -> None:
    """Periodically remove sandbox files older than 2 hours."""
    import shutil
    from pathlib import Path

    sandbox_root = Path(SETTINGS.sandbox_root)
    max_age_seconds = 2 * 60 * 60  # 2 hours
    interval_seconds = 30 * 60  # 30 minutes

    while True:
        await asyncio.sleep(interval_seconds)
        try:
            if not sandbox_root.exists():
                continue
            import time
            now = time.time()
            for entry in sandbox_root.iterdir():
                try:
                    age = now - entry.stat().st_mtime
                    if age > max_age_seconds:
                        if entry.is_dir():
                            shutil.rmtree(entry)
                        else:
                            entry.unlink()
                        LOGGER.info("Sandbox cleanup: removed %s (age %.0fs)", entry.name, age)
                except OSError as exc:
                    LOGGER.warning("Sandbox cleanup failed for %s: %s", entry, exc)
        except Exception:
            LOGGER.exception("Sandbox cleanup iteration failed")


app = FastAPI(title=SETTINGS.app_name, lifespan=lifespan)


def _public_error_message(_: Exception) -> str:
    return "Python Agent 执行失败，请稍后重试"


async def _supervisor_event_stream(engine_request: EngineStreamRequest) -> AsyncIterator[str]:
    """Yield SSE events produced by the placeholder supervisor."""
    with TRACER.start_as_current_span("internal.smart_engine.stream"):
        seq = 1
        queue: asyncio.Queue[str | None] = asyncio.Queue()

        CANCELLED_TASKS.discard(engine_request.task_id)

        async def pump_events() -> None:
            # `seq` is advanced here so the outer cancellation/error branch can emit the next SSE id.
            nonlocal seq
            try:
                async for event in SUPERVISOR.stream(engine_request, cancelled=CANCELLED_TASKS):
                    seq = event.seq + 1
                    await queue.put(event.to_sse())
            except asyncio.CancelledError:
                LOGGER.info("Supervisor task cancelled: task_id=%s", engine_request.task_id)
                await queue.put(
                    ErrorSSEEvent(
                        taskId=engine_request.task_id,
                        traceId=engine_request.trace_id,
                        seq=seq,
                        payload=ErrorPayload(
                            code="TASK_CANCELLED",
                            message="任务已被取消",
                        ),
                    ).to_sse()
                )
                await queue.put(
                    DoneSSEEvent(
                        taskId=engine_request.task_id,
                        traceId=engine_request.trace_id,
                        seq=seq + 1,
                        payload=DonePayload(
                            status="FAILED",
                            summary="任务已被取消",
                        ),
                    ).to_sse()
                )
            except Exception as exc:
                LOGGER.exception(
                    "Supervisor stream failed for task_id=%s trace_id=%s",
                    engine_request.task_id,
                    engine_request.trace_id,
                )
                await queue.put(
                    ErrorSSEEvent(
                        taskId=engine_request.task_id,
                        traceId=engine_request.trace_id,
                        seq=seq,
                        payload=ErrorPayload(
                            code="PYTHON_AGENT_ERROR",
                            message=_public_error_message(exc),
                        ),
                    ).to_sse()
                )
                await queue.put(
                    DoneSSEEvent(
                        taskId=engine_request.task_id,
                        traceId=engine_request.trace_id,
                        seq=seq + 1,
                        payload=DonePayload(
                            status="FAILED",
                            summary="Supervisor 执行失败，任务已终止",
                        ),
                    ).to_sse()
                )
            finally:
                await queue.put(None)

        async def watch_cancellation() -> None:
            while True:
                await asyncio.sleep(0.5)
                if engine_request.task_id in CANCELLED_TASKS:
                    LOGGER.info("Detected shared cancellation marker: task_id=%s", engine_request.task_id)
                    if not stream_task.done():
                        stream_task.cancel()
                    return

        stream_task = asyncio.create_task(
            pump_events(),
            name=f"smart-engine:{engine_request.task_id}",
        )
        cancel_watcher_task = asyncio.create_task(
            watch_cancellation(),
            name=f"smart-engine-cancel-watcher:{engine_request.task_id}",
        )
        ACTIVE_STREAM_TASKS[engine_request.task_id] = stream_task
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield item
        finally:
            ACTIVE_STREAM_TASKS.pop(engine_request.task_id, None)
            cancel_watcher_task.cancel()
            with suppress(asyncio.CancelledError):
                await cancel_watcher_task
            if not stream_task.done():
                stream_task.cancel()
                with suppress(asyncio.CancelledError):
                    await stream_task
            CANCELLED_TASKS.discard(engine_request.task_id)


@app.get("/health")
async def health() -> JSONResponse:
    """Simple health probe for Docker and local development."""

    payload = {
        "status": "ok",
        "service": SETTINGS.app_name,
        "provider": SETTINGS.model_provider,
        "runtimeProvider": SETTINGS.runtime_provider_name(),
        "model": SETTINGS.model_name,
        "resolvedMainModel": SETTINGS.resolve_logical_model("main_chat_model"),
        "resolvedFastModel": SETTINGS.resolve_logical_model("fast_model"),
    }
    return JSONResponse(payload)


@app.post("/internal/smart-engine/stream")
async def smart_engine_stream(
    engine_request: EngineStreamRequest,
    _: None = Depends(verify_internal_token),
) -> StreamingResponse:
    """Internal streaming endpoint used by the Java BFF."""

    LOGGER.info(
        "Received task_id=%s trace_id=%s service_type=%s",
        engine_request.task_id,
        engine_request.trace_id,
        engine_request.service_type,
    )
    try:
        SUPERVISOR.resolve_route(engine_request.service_type, engine_request.params)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return StreamingResponse(
        _supervisor_event_stream(engine_request),
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/internal/smart-engine/{task_id}/cancel")
async def cancel_smart_engine_task(
    task_id: str,
    _: None = Depends(verify_internal_token),
) -> JSONResponse:
    """Cancel a running smart-engine task by its id."""
    CANCELLED_TASKS.add(task_id)
    stream_task = ACTIVE_STREAM_TASKS.get(task_id)
    if stream_task and not stream_task.done():
        stream_task.cancel()
    LOGGER.info("Task cancellation requested: task_id=%s", task_id)
    return JSONResponse({"status": "cancelled", "taskId": task_id})


@app.post("/internal/conversations/{conversation_id}/messages")
async def append_conversation_message(
    conversation_id: str,
    request: InternalConversationMessageRequest,
    _: None = Depends(verify_internal_token),
) -> JSONResponse:
    """Persist a single conversation transcript message."""

    document = ConversationMessageDocument(
        conversationId=conversation_id,
        userId=request.user_id,
        role=request.role,
        content=request.content,
        imageUrls=request.image_urls,
    )
    await MESSAGE_STORE.append_message(document)
    return JSONResponse({"messageId": document.message_id})


@app.get("/internal/conversations/{conversation_id}/messages")
async def list_conversation_messages(
    conversation_id: str,
    _: None = Depends(verify_internal_token),
    user_id: str | None = Query(default=None, alias="userId"),
    page: int | None = Query(default=None, ge=0),
    size: int | None = Query(default=None, ge=1, le=200),
) -> JSONResponse:
    """Return the persisted transcript for a conversation."""

    documents = await MESSAGE_STORE.list_messages(
        conversation_id=conversation_id,
        user_id=user_id,
        page=page,
        size=size,
    )
    return JSONResponse(
        [
            document.model_dump(by_alias=True, mode="json")
            for document in documents
        ]
    )


@app.get("/internal/resources/search/semantic")
async def search_resources_semantic(
    _: None = Depends(verify_internal_token),
    query: str = Query(min_length=1),
    top_k: int = Query(default=8, alias="topK", ge=1, le=20),
    user_id: str | None = Query(default=None, alias="userId"),
    domain: str | None = Query(default=None),
) -> JSONResponse:
    """Search RAG resource chunks and return resource-level semantic hits."""

    normalized_query = query.strip()
    if not normalized_query:
        raise HTTPException(status_code=400, detail="query must not be blank")
    rag_error = ""
    rows: list[dict] = []
    try:
        rows = _search_resource_chunks_with_hybrid_rag(
            normalized_query,
            top_k=top_k,
            domain=domain or SETTINGS.retrieval_domain,
            user_id=user_id,
        )
        if not rows:
            rows = _search_resource_chunks(
                normalized_query,
                top_k=top_k,
                domain=domain or SETTINGS.retrieval_domain,
                user_id=user_id,
            )
    except Exception as exc:
        rag_error = str(exc)
        LOGGER.warning("Resource RAG search unavailable for query=%r: %s", normalized_query, exc)

    remaining = max(0, top_k - len({str(row.get("resource_id") or "") for row in rows if row.get("resource_id")}))
    external_candidates = await _search_resource_tavily_candidates(normalized_query, remaining)
    response = _build_resource_semantic_response(normalized_query, rows, external_candidates)
    if rag_error and not response.results:
        response = ResourceSemanticSearchResponse(
            query=normalized_query,
            available=False,
            message=f"semantic search unavailable: {rag_error}",
            results=[],
        )
    return JSONResponse(response.model_dump(by_alias=True, mode="json"))


@app.post("/internal/notes/analyze")
async def analyze_note(
    request: NoteAnalysisRequest,
    _: None = Depends(verify_internal_token),
) -> JSONResponse:
    """Analyze a user note and return summary, keywords and review todos."""

    try:
        response = await _llm_note_analysis(request)
        if not response.summary.strip():
            response = _heuristic_note_analysis(request)
    except Exception as exc:
        LOGGER.warning("Note LLM analysis unavailable, using heuristic fallback: %s", exc)
        response = _heuristic_note_analysis(request)
    return JSONResponse(response.model_dump(by_alias=True, mode="json"))


@app.post("/internal/notes/index")
async def index_note(
    request: NoteIndexRequest,
    _: None = Depends(verify_internal_token),
) -> JSONResponse:
    """Index a user note into the pgvector-backed resource RAG tables."""

    try:
        response = _index_note_chunks(request)
    except Exception as exc:
        LOGGER.warning("Note RAG indexing unavailable note_id=%s: %s", request.note_id, exc)
        response = NoteIndexResponse(
            indexed=False,
            chunkCount=0,
            message=f"note indexing unavailable: {exc}",
        )
    return JSONResponse(response.model_dump(by_alias=True, mode="json"))


@app.get("/internal/notes/search/semantic")
async def search_notes_semantic(
    _: None = Depends(verify_internal_token),
    query: str = Query(min_length=1),
    top_k: int = Query(default=8, alias="topK", ge=1, le=20),
    user_id: str = Query(alias="userId"),
) -> JSONResponse:
    """Semantic search over the current user's indexed notes."""

    normalized_query = query.strip()
    if not normalized_query:
        raise HTTPException(status_code=400, detail="query must not be blank")
    try:
        rows = _search_note_chunks(normalized_query, top_k=top_k, user_id=user_id)
        response = _build_note_semantic_response(normalized_query, rows)
    except Exception as exc:
        LOGGER.warning("Note semantic search unavailable for query=%r: %s", normalized_query, exc)
        response = NoteSemanticSearchResponse(
            query=normalized_query,
            available=False,
            message=f"note semantic search unavailable: {exc}",
            results=[],
        )
    return JSONResponse(response.model_dump(by_alias=True, mode="json"))
