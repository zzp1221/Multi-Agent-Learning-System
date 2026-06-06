"""FastAPI entrypoint for the zhixue Python agent runtime."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import secrets
import tempfile
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import AsyncIterator

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


class ResourceSemanticResult(BaseModel):
    resource_id: str = Field(alias="resourceId")
    score: float
    reason: str
    hits: list[ResourceSemanticHit] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


class ResourceSemanticSearchResponse(BaseModel):
    query: str
    available: bool
    message: str
    results: list[ResourceSemanticResult] = Field(default_factory=list)

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


def _build_resource_semantic_response(query: str, rows: list[dict]) -> ResourceSemanticSearchResponse:
    grouped: dict[str, ResourceSemanticResult] = {}
    for row in rows:
        resource_id = str(row.get("resource_id") or "")
        if not resource_id:
            continue
        similarity = float(row.get("similarity") or 0)
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
                score=similarity,
                reason="向量语义匹配",
                hits=[hit],
            )
        else:
            result.hits.append(hit)
            result.score = max(result.score, similarity)
    results = sorted(grouped.values(), key=lambda item: item.score, reverse=True)
    return ResourceSemanticSearchResponse(
        query=query,
        available=True,
        message="ok" if results else "没有匹配到可用资源",
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
    try:
        rows = _search_resource_chunks(
            normalized_query,
            top_k=top_k,
            domain=domain or SETTINGS.retrieval_domain,
            user_id=user_id,
        )
        response = _build_resource_semantic_response(normalized_query, rows)
    except Exception as exc:
        LOGGER.warning("Resource semantic search unavailable for query=%r: %s", normalized_query, exc)
        response = ResourceSemanticSearchResponse(
            query=normalized_query,
            available=False,
            message=f"semantic search unavailable: {exc}",
            results=[],
        )
    return JSONResponse(response.model_dump(by_alias=True, mode="json"))
