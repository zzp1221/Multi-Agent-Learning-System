import asyncio
import json
from pathlib import Path

import pytest

import server
from src.ai_modules.config import Settings
from src.ai_modules.llms import user_runtime_config
from src.ai_modules.llms.user_runtime_config import RuntimeProvider, UserLlmRuntimeConfig
from src.ai_modules.models import EngineStreamRequest
from src.ai_modules.models.events import (
    DonePayload,
    DoneSSEEvent,
    ProgressPayload,
    ProgressSSEEvent,
    ResourceFilePayload,
    ResourceFileSSEEvent,
    ResultChunkPayload,
    ResultChunkSSEEvent,
    VideoProgressSSEEvent,
)

INTERNAL_HEADERS = {"X-Zhixue-Internal-Token": "test-internal-token"}


async def empty_tavily_fallback(*_args, **_kwargs):
    return []


def test_health_endpoint(client) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["provider"] == "openai_compatible"
    assert response.json()["runtimeProvider"] == "openai_compatible"
    assert response.json()["smartEngineWorker"]["status"] in {
        "not_started",
        "running",
        "stopping",
        "stopped",
        "error",
    }


@pytest.mark.asyncio
async def test_smart_engine_worker_supervisor_restarts_failed_worker(monkeypatch) -> None:
    started = asyncio.Event()
    restarted = asyncio.Event()
    stop_second_worker = asyncio.Event()
    attempts = 0

    class RestartProbeWorker:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def run_forever(self) -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                started.set()
                raise RuntimeError("redis startup failed")
            restarted.set()
            await stop_second_worker.wait()

    monkeypatch.setattr(server, "SmartEngineStreamWorker", RestartProbeWorker)
    monkeypatch.setattr(server, "SMART_ENGINE_WORKER_RESTART_SECONDS", 0)
    server.SMART_ENGINE_WORKER_STATE.update(
        {
            "status": "not_started",
            "restartCount": 0,
            "lastStartedAt": None,
            "lastStoppedAt": None,
            "lastError": "",
        }
    )
    supervisor_task = asyncio.create_task(server._smart_engine_worker_supervisor_loop())

    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        await asyncio.wait_for(restarted.wait(), timeout=1)
        assert attempts == 2
        assert server.SMART_ENGINE_WORKER_STATE["status"] == "running"
        assert server.SMART_ENGINE_WORKER_STATE["restartCount"] == 1
    finally:
        stop_second_worker.set()
        supervisor_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await supervisor_task


def test_internal_stream_endpoint_requires_internal_token(client) -> None:
    payload = {
        "serviceType": "RESOURCE_GENERATION",
        "params": {"resourceType": "DOCUMENT"},
        "taskId": "task-auth",
        "traceId": "trace-auth",
    }

    assert client.post("/internal/smart-engine/stream", json=payload).status_code == 401
    assert client.post(
        "/internal/smart-engine/stream",
        json=payload,
        headers={"X-Zhixue-Internal-Token": "wrong-token"},
    ).status_code == 401


def test_internal_stream_endpoint_rejects_when_token_not_configured(client, monkeypatch) -> None:
    monkeypatch.setattr(server.SETTINGS, "python_agent_internal_token", "")
    payload = {
        "serviceType": "RESOURCE_GENERATION",
        "params": {"resourceType": "DOCUMENT"},
        "taskId": "task-auth",
        "traceId": "trace-auth",
    }

    response = client.post("/internal/smart-engine/stream", json=payload, headers=INTERNAL_HEADERS)

    assert response.status_code == 503


def test_resource_semantic_search_requires_internal_token(client) -> None:
    response = client.get("/internal/resources/search/semantic?query=dp")

    assert response.status_code == 401


def test_resource_semantic_search_returns_grouped_results(client, monkeypatch) -> None:
    monkeypatch.setattr(server, "_search_resource_chunks_with_hybrid_rag", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(server, "_search_resource_tavily_candidates", empty_tavily_fallback)
    monkeypatch.setattr(
        server,
        "_search_resource_chunks",
        lambda query, top_k, domain=None, user_id=None: [
            {
                "chunk_id": 1,
                "resource_id": "70000000-0000-0000-0000-000000000001",
                "chunk_no": 1,
                "content": "dynamic programming optimal substructure",
                "similarity": 0.93,
                "source_url": "https://example.com/dp",
            },
            {
                "chunk_id": 2,
                "resource_id": "70000000-0000-0000-0000-000000000001",
                "chunk_no": 2,
                "content": "overlapping subproblems",
                "similarity": 0.91,
                "source_url": "https://example.com/dp",
            },
        ],
    )

    response = client.get(
        "/internal/resources/search/semantic",
        params={"query": "dynamic programming", "topK": 5},
        headers=INTERNAL_HEADERS,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert payload["results"][0]["resourceId"] == "70000000-0000-0000-0000-000000000001"
    assert len(payload["results"][0]["hits"]) == 2


def test_resource_semantic_search_prefers_existing_hybrid_rag(client, monkeypatch) -> None:
    def hybrid_search(query, top_k, domain=None, user_id=None):
        return [
            {
                "chunk_id": 11,
                "resource_id": "70000000-0000-0000-0000-000000000011",
                "chunk_no": 1,
                "content": "Java Thread and Runnable synchronized volatile",
                "similarity": 0.9,
                "rank_score": 0.9,
                "source_url": "https://example.com/java-thread",
                "retrieval_reason": "existing hybrid RAG",
            }
        ]

    def chunk_search(query, top_k, domain=None, user_id=None):
        raise AssertionError("chunk fallback should not run when hybrid RAG returns resources")

    monkeypatch.setattr(server, "_search_resource_chunks_with_hybrid_rag", hybrid_search)
    monkeypatch.setattr(server, "_search_resource_chunks", chunk_search)
    monkeypatch.setattr(server, "_search_resource_tavily_candidates", empty_tavily_fallback)

    response = client.get(
        "/internal/resources/search/semantic",
        params={
            "query": "Java Runnable synchronized volatile",
            "topK": 5,
            "userId": "60000000-0000-0000-0000-000000000008",
        },
        headers=INTERNAL_HEADERS,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["results"][0]["resourceId"] == "70000000-0000-0000-0000-000000000011"
    assert payload["results"][0]["reason"] == "existing hybrid RAG"


def test_hybrid_rag_resource_lookup_maps_existing_rag_refs(monkeypatch) -> None:
    import src.ai_modules.retrieval as retrieval_module

    captured = {}

    class FakeHybridRetrievalService:
        def retrieve_raw(self, query, web_search_enabled=False):
            captured["query"] = query
            captured["web_search_enabled"] = web_search_enabled
            return {
                "top": [
                    ("knowledge-thread", "Java thread basics", 0.2),
                    ("https://example.com/java-thread", "Java Thread Guide", 0.1),
                ],
                "channels": {
                    "vector": [
                        ("https://example.com/java-thread", "Java Thread Guide", 0.88, "resource"),
                        ("knowledge-thread", "Java thread basics", 0.86, "knowledge"),
                    ]
                },
            }

    def fake_resource_rows(refs, top_k, domain=None, user_id=None, query=""):
        captured["refs"] = refs
        captured["top_k"] = top_k
        captured["domain"] = domain
        captured["user_id"] = user_id
        captured["row_query"] = query
        return [{"resource_id": "70000000-0000-0000-0000-000000000011"}]

    monkeypatch.setattr(retrieval_module, "HybridRetrievalService", FakeHybridRetrievalService)
    monkeypatch.setattr(server, "_resource_rows_for_hybrid_refs", fake_resource_rows)

    rows = server._search_resource_chunks_with_hybrid_rag(
        "Java Runnable synchronized volatile",
        top_k=5,
        domain="COMPUTER_SCIENCE",
        user_id="60000000-0000-0000-0000-000000000008",
    )

    assert rows == [{"resource_id": "70000000-0000-0000-0000-000000000011"}]
    assert captured["query"] == "Java Runnable synchronized volatile"
    assert captured["web_search_enabled"] is False
    assert captured["refs"] == ["knowledge-thread", "wiki://knowledge-thread", "https://example.com/java-thread"]
    assert captured["domain"] == "COMPUTER_SCIENCE"
    assert captured["user_id"] == "60000000-0000-0000-0000-000000000008"


def test_hybrid_rag_resource_lookup_maps_raw_wiki_slug_to_wiki_source_ref(monkeypatch) -> None:
    import src.ai_modules.retrieval as retrieval_module

    captured = {}

    class FakeHybridRetrievalService:
        def retrieve_raw(self, query, web_search_enabled=False):
            del query, web_search_enabled
            return {
                "top": [("操作系统/死锁", "死锁", 0.2)],
                "channels": {"vector": []},
            }

    def fake_resource_rows(refs, top_k, domain=None, user_id=None, query=""):
        captured.update({"refs": refs, "query": query, "top_k": top_k, "domain": domain, "user_id": user_id})
        return [{"resource_id": "70000000-0000-0000-0000-000000000022"}]

    monkeypatch.setattr(retrieval_module, "HybridRetrievalService", FakeHybridRetrievalService)
    monkeypatch.setattr(server, "_resource_rows_for_hybrid_refs", fake_resource_rows)

    rows = server._search_resource_chunks_with_hybrid_rag(
        "死锁",
        top_k=3,
        domain="COMPUTER_SCIENCE",
        user_id="60000000-0000-0000-0000-000000000008",
    )

    assert rows == [{"resource_id": "70000000-0000-0000-0000-000000000022"}]
    assert captured["refs"] == ["操作系统/死锁", "wiki://操作系统/死锁"]
    assert captured["query"] == "死锁"


def test_resource_ranking_uses_generic_features_without_topic_rules() -> None:
    rows = [
        {
            "resource_id": "a",
            "similarity": 0.5,
            "rank_score": 0.5,
            "resource_type": "READING",
            "quality_score": 0.7,
            "searchable_text": "unrelated notes",
        },
        {
            "resource_id": "b",
            "similarity": 0.49,
            "rank_score": 0.49,
            "resource_type": "VIDEO",
            "quality_score": 0.9,
            "searchable_text": "graph traversal bfs dfs examples",
        },
    ]

    ranked = server._rank_resource_rows("graph traversal bfs", rows, top_k=2)

    assert [item["resource_id"] for item in ranked] == ["b", "a"]
    assert ranked[0]["lexical_coverage"] == 1.0


def test_resource_recommendation_code_has_no_fixed_topic_special_cases() -> None:
    source = Path(server.__file__).read_text(encoding="utf-8")
    forbidden_terms = ["java并发编程", "AQS", "线程池", "React state", "B+树", "死锁", "数据库索引"]

    for term in forbidden_terms:
        assert term not in source


def test_resource_semantic_search_degrades_when_embedding_unavailable(client, monkeypatch) -> None:
    def fail_search(query, top_k, domain=None, user_id=None):
        raise RuntimeError("missing embedding key")

    monkeypatch.setattr(server, "_search_resource_chunks_with_hybrid_rag", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(server, "_search_resource_chunks", fail_search)
    monkeypatch.setattr(server, "_search_resource_tavily_candidates", empty_tavily_fallback)

    response = client.get(
        "/internal/resources/search/semantic",
        params={"query": "dynamic programming"},
        headers=INTERNAL_HEADERS,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is False
    assert "missing embedding key" in payload["message"]
    assert payload["results"] == []


def test_resource_semantic_search_uses_tavily_when_embedding_unavailable(client, monkeypatch) -> None:
    def fail_search(query, top_k, domain=None, user_id=None):
        raise RuntimeError("missing embedding key")

    async def tavily_fallback(query, top_k):
        assert query == "dynamic programming"
        assert top_k == 8
        return [
            server.ResourceExternalCandidate(
                title="Dynamic Programming Guide",
                sourceUrl="https://example.com/dp-guide",
                sourceName="example.com",
                summaryText="optimal substructure and overlapping subproblems",
                resourceType="READING",
                displayType="DOCUMENT",
                difficultyLevel="MIXED",
                tags=["dynamic", "programming"],
            )
        ]

    monkeypatch.setattr(server, "_search_resource_chunks_with_hybrid_rag", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(server, "_search_resource_chunks", fail_search)
    monkeypatch.setattr(server, "_search_resource_tavily_candidates", tavily_fallback)

    response = client.get(
        "/internal/resources/search/semantic",
        params={"query": "dynamic programming"},
        headers=INTERNAL_HEADERS,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert payload["results"][0]["reason"] == "tavily_current_stage_fallback"
    assert payload["results"][0]["externalResource"]["sourceUrl"] == "https://example.com/dp-guide"


@pytest.mark.asyncio
async def test_resource_tavily_fallback_shortens_long_context_query(monkeypatch) -> None:
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "results": [
                    {
                        "url": "https://example.com/rbtree",
                        "title": "Red Black Tree Rotations",
                        "content": "insert fixup recolor rotation",
                    }
                ]
            }

    class FakeAsyncClient:
        def __init__(self, timeout):
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, _exc_type, _exc, _tb):
            return None

        async def post(self, url, json):
            captured["url"] = url
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr(server.SETTINGS, "tavily_api_key", "test-key")
    monkeypatch.setattr(server.SETTINGS, "tavily_base_url", "https://api.tavily.test/search")
    monkeypatch.setattr(server.httpx, "AsyncClient", FakeAsyncClient)

    long_query = (
        "当前学习路径上下文：" + "红黑树 插入 修复 recolor rotate uncle grandparent " * 30
    )

    candidates = await server._search_resource_tavily_candidates(long_query, 2)

    tavily_query = captured["json"]["query"]
    assert len(tavily_query) <= server.TAVILY_RESOURCE_QUERY_MAX_CHARS
    assert "红黑树" in tavily_query
    assert "recolor" in tavily_query
    assert captured["json"]["max_results"] == 6
    assert candidates[0].source_url == "https://example.com/rbtree"


def test_resource_tavily_query_keeps_short_query_unchanged() -> None:
    assert server._tavily_resource_query("dynamic programming") == "dynamic programming"


def test_resource_semantic_search_fills_short_rag_results_with_tavily(client, monkeypatch) -> None:
    def hybrid_search(query, top_k, domain=None, user_id=None):
        assert query == "graph traversal current stage"
        assert top_k == 3
        return [
            {
                "chunk_id": 21,
                "resource_id": "70000000-0000-0000-0000-000000000021",
                "chunk_no": 1,
                "content": "graph traversal bfs dfs",
                "similarity": 0.88,
                "rank_score": 0.88,
                "source_url": "https://example.com/graph-existing",
                "retrieval_reason": "existing hybrid RAG",
            }
        ]

    def chunk_search(query, top_k, domain=None, user_id=None):
        raise AssertionError("vector fallback should not run when hybrid RAG has resources")

    async def tavily_fallback(query, top_k):
        assert query == "graph traversal current stage"
        assert top_k == 2
        return [
            server.ResourceExternalCandidate(
                title="Graph Traversal Practice",
                sourceUrl="https://example.com/graph-practice",
                sourceName="example.com",
                summaryText="bfs dfs practice",
                resourceType="READING",
                displayType="DOCUMENT",
                difficultyLevel="MIXED",
                tags=["graph", "traversal"],
            )
        ]

    monkeypatch.setattr(server, "_search_resource_chunks_with_hybrid_rag", hybrid_search)
    monkeypatch.setattr(server, "_search_resource_chunks", chunk_search)
    monkeypatch.setattr(server, "_search_resource_tavily_candidates", tavily_fallback)

    response = client.get(
        "/internal/resources/search/semantic",
        params={"query": "graph traversal current stage", "topK": 3},
        headers=INTERNAL_HEADERS,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert [item["reason"] for item in payload["results"]] == [
        "existing hybrid RAG",
        "tavily_current_stage_fallback",
    ]
    assert payload["results"][1]["externalResource"]["sourceUrl"] == "https://example.com/graph-practice"


def test_resource_chunk_search_uses_domain_parameter(monkeypatch) -> None:
    executed = {}

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _tb):
            return False

        def execute(self, sql, params):
            executed["sql"] = sql
            executed["params"] = params

        def fetchall(self):
            return []

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _tb):
            return False

        def cursor(self, cursor_factory=None):
            del cursor_factory
            return FakeCursor()

    monkeypatch.setattr(server, "_embed_resource_query", lambda query: [0.1] * server.SETTINGS.knowledge_embedding_dimension)
    monkeypatch.setattr(server.psycopg2, "connect", lambda **_kwargs: FakeConnection())

    rows = server._search_resource_chunks(
        "dynamic programming",
        top_k=3,
        domain="COMPUTER_SCIENCE",
        user_id="60000000-0000-0000-0000-000000000007",
    )

    assert rows == []
    assert "AND (%s IS NULL OR rc.domain = %s)" in executed["sql"]
    assert "wikiBindingStatus" in executed["sql"]
    assert "LOW_CONFIDENCE_DROPPED" in executed["sql"]
    assert "rc.access_scope::text = 'GLOBAL'" in executed["sql"]
    assert "rc.owner_user_id = %s::uuid" in executed["sql"]
    assert "app.user_course_enrollments" in executed["sql"]
    assert executed["params"][1:3] == ["COMPUTER_SCIENCE", "COMPUTER_SCIENCE"]
    assert executed["params"][3:7] == [
        "60000000-0000-0000-0000-000000000007",
        "60000000-0000-0000-0000-000000000007",
        "60000000-0000-0000-0000-000000000007",
        "60000000-0000-0000-0000-000000000007",
    ]
    assert executed["params"][-1] == 3


def test_resource_semantic_search_passes_user_id_to_chunk_search(client, monkeypatch) -> None:
    captured = {}

    def fake_search(query, top_k, domain=None, user_id=None):
        captured.update({"query": query, "top_k": top_k, "domain": domain, "user_id": user_id})
        return []

    monkeypatch.setattr(server, "_search_resource_chunks_with_hybrid_rag", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(server, "_search_resource_chunks", fake_search)
    monkeypatch.setattr(server, "_search_resource_tavily_candidates", empty_tavily_fallback)

    response = client.get(
        "/internal/resources/search/semantic",
        params={
            "query": "dynamic programming",
            "topK": 4,
            "userId": "60000000-0000-0000-0000-000000000008",
        },
        headers=INTERNAL_HEADERS,
    )

    assert response.status_code == 200
    assert captured["top_k"] == 4
    assert captured["user_id"] == "60000000-0000-0000-0000-000000000008"


def test_sse_event_serialization() -> None:
    event = ProgressSSEEvent(
        taskId="task_001",
        traceId="trace_001",
        seq=1,
        payload=ProgressPayload(stage="accepted", percent=10, message="ok"),
    )

    serialized = event.to_sse()

    assert serialized.startswith("event: progress\n")
    assert '"taskId": "task_001"' in serialized


def test_stream_endpoint_returns_expected_event_order(client, monkeypatch) -> None:
    class StubSupervisor:
        def resolve_route(self, service_type, params):
            del service_type, params
            return None

        async def stream(self, request, cancelled=None):
            del cancelled
            yield ProgressSSEEvent(
                taskId=request.task_id,
                traceId=request.trace_id,
                seq=1,
                payload=ProgressPayload(stage="accepted", percent=10, message="任务已接收"),
            )
            yield ResultChunkSSEEvent(
                taskId=request.task_id,
                traceId=request.trace_id,
                seq=2,
                payload=ResultChunkPayload(text="开始生成资源"),
            )
            yield ProgressSSEEvent(
                taskId=request.task_id,
                traceId=request.trace_id,
                seq=3,
                payload=ProgressPayload(stage="generation", percent=60, message="生成中"),
            )
            yield ResultChunkSSEEvent(
                taskId=request.task_id,
                traceId=request.trace_id,
                seq=4,
                payload=ResultChunkPayload(text="批判审查通过"),
            )
            yield ResultChunkSSEEvent(
                taskId=request.task_id,
                traceId=request.trace_id,
                seq=5,
                payload=ResultChunkPayload(text="安全审查通过"),
            )
            yield ResourceFileSSEEvent(
                taskId=request.task_id,
                traceId=request.trace_id,
                seq=6,
                payload=ResourceFilePayload(
                    assetType="DOCUMENT",
                    title="联合索引导学文档",
                    summary="结构化导学",
                    displayMode="download",
                    fileName="document.md",
                    localPath="sandbox/document.md",
                    mimeType="text/markdown",
                ),
            )
            yield DoneSSEEvent(
                taskId=request.task_id,
                traceId=request.trace_id,
                seq=7,
                payload=DonePayload(status="SUCCESS", summary="资源生成完成"),
            )

    monkeypatch.setattr(server, "SUPERVISOR", StubSupervisor())

    payload = {
        "serviceType": "RESOURCE_GENERATION",
        "params": {"resourceType": "DOCUMENT"},
        "userId": "user-001",
        "taskId": "task-001",
        "traceId": "trace-001",
        "conversationId": "conv-001",
    }

    with client.stream(
        "POST",
        "/internal/smart-engine/stream",
        json=payload,
        headers=INTERNAL_HEADERS,
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        lines = [line for line in response.iter_lines() if line]

    event_names = [line.removeprefix("event: ") for line in lines[::2]]
    data_payloads = [
        json.loads(line.removeprefix("data: "))
        for line in lines[1::2]
    ]

    assert event_names == [
        "progress",
        "result_chunk",
        "progress",
        "result_chunk",
        "result_chunk",
        "resource_file",
        "done",
    ]
    assert data_payloads[-1]["payload"]["status"] == "SUCCESS"


def test_stream_endpoint_supports_video_generation_events(client, monkeypatch) -> None:
    class StubSupervisor:
        def resolve_route(self, service_type, params):
            assert service_type == "RESOURCE_GENERATION"
            assert params["resourceType"] == "VIDEO"
            return None

        async def stream(self, request, cancelled=None):
            del cancelled
            yield ProgressSSEEvent(
                taskId=request.task_id,
                traceId=request.trace_id,
                seq=1,
                payload=ProgressPayload(stage="accepted", percent=10, message="accepted"),
            )
            yield VideoProgressSSEEvent(
                event="video_gen:start",
                taskId=request.task_id,
                traceId=request.trace_id,
                seq=2,
                payload=ProgressPayload(stage="video_started", percent=20, message="video started"),
            )
            yield ProgressSSEEvent(
                taskId=request.task_id,
                traceId=request.trace_id,
                seq=3,
                payload=ProgressPayload(stage="video_generation", percent=60, message="video running"),
            )
            yield ProgressSSEEvent(
                taskId=request.task_id,
                traceId=request.trace_id,
                seq=4,
                payload=ProgressPayload(stage="video_render", percent=70, message="video render"),
            )
            yield ProgressSSEEvent(
                taskId=request.task_id,
                traceId=request.trace_id,
                seq=5,
                payload=ProgressPayload(stage="video_package", percent=75, message="video package"),
            )
            yield VideoProgressSSEEvent(
                event="video_gen:speech",
                taskId=request.task_id,
                traceId=request.trace_id,
                seq=6,
                payload=ProgressPayload(
                    stage="video_speech",
                    percent=80,
                    message="speech ready",
                    audioBase64="dGVzdA==",
                    avatarDataUrl="/dh_live/assets/combined_data.json.gz",
                ),
            )
            yield ResourceFileSSEEvent(
                taskId=request.task_id,
                traceId=request.trace_id,
                seq=7,
                payload=ResourceFilePayload(
                    assetType="VIDEO",
                    title="video",
                    summary="video resource",
                    displayMode="download",
                    fileName="video.mp4",
                    localPath="sandbox/video.mp4",
                    mimeType="video/mp4",
                    thumbnailPath="sandbox/video.svg",
                ),
            )
            yield ResultChunkSSEEvent(
                taskId=request.task_id,
                traceId=request.trace_id,
                seq=8,
                payload=ResultChunkPayload(text="视频生成完成"),
            )
            yield DoneSSEEvent(
                taskId=request.task_id,
                traceId=request.trace_id,
                seq=9,
                payload=DonePayload(status="SUCCESS", summary="视频生成完成"),
            )

    monkeypatch.setattr(server, "SUPERVISOR", StubSupervisor())

    payload = {
        "serviceType": "RESOURCE_GENERATION",
        "params": {
            "resourceType": "VIDEO",
            "query": "联合索引",
            "topic": "联合索引",
            "style": "hybrid",
            "duration": 60,
        },
        "userId": "user-001",
        "taskId": "task-video",
        "traceId": "trace-video",
    }

    with client.stream(
        "POST",
        "/internal/smart-engine/stream",
        json=payload,
        headers=INTERNAL_HEADERS,
    ) as response:
        assert response.status_code == 200
        lines = [line for line in response.iter_lines() if line]

    event_names = [line.removeprefix("event: ") for line in lines[::2]]
    data_payloads = [json.loads(line.removeprefix("data: ")) for line in lines[1::2]]

    assert event_names.count("progress") >= 4
    resource_file_payload = next(item["payload"] for item in data_payloads if item["event"] == "resource_file")
    assert resource_file_payload["assetType"] == "VIDEO"
    assert resource_file_payload["thumbnailPath"].endswith(".svg")
    speech_payload = next(item["payload"] for item in data_payloads if item["event"] == "video_gen:speech")
    assert speech_payload["audioBase64"]
    assert speech_payload["avatarDataUrl"] == "/dh_live/assets/combined_data.json.gz"
    completion_payload = next(item["payload"] for item in data_payloads if item["event"] == "result_chunk" and "视频生成完成" in item["payload"].get("text", ""))
    assert "视频生成完成" in completion_payload["text"]


def test_stream_endpoint_rejects_unknown_service_type(client) -> None:
    payload = {
        "serviceType": "UNKNOWN",
        "params": {},
        "taskId": "task-unknown",
        "traceId": "trace-unknown",
    }

    response = client.post("/internal/smart-engine/stream", json=payload, headers=INTERNAL_HEADERS)

    assert response.status_code == 400


def test_stream_endpoint_accepts_personalized_learning_service_type(client, monkeypatch) -> None:
    class StubSupervisor:
        def resolve_route(self, service_type, params):
            assert service_type == "PERSONALIZED_LEARNING"
            assert params["topic"] == "联合索引"
            return None

        async def stream(self, request, cancelled=None):
            del cancelled
            yield ProgressSSEEvent(
                taskId=request.task_id,
                traceId=request.trace_id,
                seq=1,
                payload=ProgressPayload(stage="profile", percent=10, message="画像分析"),
            )
            yield DoneSSEEvent(
                taskId=request.task_id,
                traceId=request.trace_id,
                seq=2,
                payload=DonePayload(status="SUCCESS", summary="个性化学习方案完成"),
            )

    monkeypatch.setattr(server, "SUPERVISOR", StubSupervisor())
    payload = {
        "serviceType": "personalized_learning",
        "params": {"topic": "联合索引"},
        "taskId": "task-personalized",
        "traceId": "trace-personalized",
    }

    with client.stream(
        "POST",
        "/internal/smart-engine/stream",
        json=payload,
        headers=INTERNAL_HEADERS,
    ) as response:
        assert response.status_code == 200
        lines = [line for line in response.iter_lines() if line]

    event_names = [line.removeprefix("event: ") for line in lines[::2]]
    assert event_names == ["progress", "done"]


def test_stream_endpoint_runs_supervisor_inside_user_llm_runtime_context(client, monkeypatch) -> None:
    class StubSupervisor:
        def resolve_route(self, service_type, params):
            del service_type, params
            return None

        async def stream(self, request, cancelled=None):
            del cancelled
            assert user_runtime_config.is_user_llm_context_active() is True
            assert user_runtime_config.current_user_llm_config() is runtime_config
            yield DoneSSEEvent(
                taskId=request.task_id,
                traceId=request.trace_id,
                seq=1,
                payload=DonePayload(status="SUCCESS", summary="ok"),
            )

    runtime_config = UserLlmRuntimeConfig(
        enabled=True,
        allowEnvironmentFallback=False,
        activeProvider="dashscope",
        fallbackProvider="",
        providers={
            "dashscope": RuntimeProvider(
                provider="dashscope",
                baseUrl="https://dashscope.aliyuncs.com/compatible-mode/v1",
                apiKey="sk-user",
                modelOverrides={"main_chat_model": "qwen-plus"},
            )
        },
        componentOverrides={},
        skillOverrides={},
    )

    async def fake_fetch_user_llm_runtime_config(**_: object) -> UserLlmRuntimeConfig:
        return runtime_config

    monkeypatch.setattr(server, "SUPERVISOR", StubSupervisor())
    monkeypatch.setattr(user_runtime_config, "fetch_user_llm_runtime_config", fake_fetch_user_llm_runtime_config)

    payload = {
        "userId": "user-llm-context",
        "serviceType": "PERSONALIZED_LEARNING",
        "params": {"topic": "联合索引"},
        "taskId": "task-user-runtime",
        "traceId": "trace-user-runtime",
    }

    with client.stream(
        "POST",
        "/internal/smart-engine/stream",
        json=payload,
        headers=INTERNAL_HEADERS,
    ) as response:
        assert response.status_code == 200
        lines = [line for line in response.iter_lines() if line]

    assert [line.removeprefix("event: ") for line in lines[::2]] == ["done"]


def test_engine_stream_request_normalizes_legacy_java_payload() -> None:
    request = EngineStreamRequest.model_validate(
        {
            "serviceType": "LEARNING_EVALUATION",
            "taskId": 12345,
            "traceId": 67890,
            "userId": 111,
            "requestPayload": {
                "params": {
                    "params": {
                        "message": "请评估我的掌握情况",
                        "knowledgePoint": "数据结构",
                    }
                }
            },
        }
    )

    assert request.service_type == "EVALUATION"
    assert request.task_id == "12345"
    assert request.trace_id == "67890"
    assert request.user_id == "111"
    assert request.params["message"] == "请评估我的掌握情况"
    assert request.params["knowledgePoint"] == "数据结构"


def test_stream_endpoint_accepts_legacy_java_wrapped_payload(client) -> None:
    payload = {
        "serviceType": "LEARNING_EVALUATION",
        "taskId": 12345,
        "traceId": 67890,
        "userId": 111,
        "requestPayload": {
            "params": {
                "params": {
                    "message": "请评估我的掌握情况",
                    "knowledgePoint": "数据结构",
                }
            }
        },
    }

    with client.stream(
        "POST",
        "/internal/smart-engine/stream",
        json=payload,
        headers=INTERNAL_HEADERS,
    ) as response:
        assert response.status_code == 200
        lines = [line for line in response.iter_lines() if line]

    event_names = [line.removeprefix("event: ") for line in lines[::2]]
    assert event_names[-1] == "done"


def test_stream_endpoint_emits_error_and_failed_done_when_supervisor_raises(client, monkeypatch) -> None:
    class BrokenSupervisor:
        def resolve_route(self, service_type, params):
            del service_type, params
            return None

        async def stream(self, request, cancelled=None):
            del request, cancelled
            for event in ():
                yield event
            raise RuntimeError("boom")

    monkeypatch.setattr(server, "SUPERVISOR", BrokenSupervisor())

    payload = {
        "serviceType": "RESOURCE_GENERATION",
        "params": {"resourceType": "DOCUMENT"},
        "taskId": "task-error",
        "traceId": "trace-error",
    }

    with client.stream(
        "POST",
        "/internal/smart-engine/stream",
        json=payload,
        headers=INTERNAL_HEADERS,
    ) as response:
        assert response.status_code == 200
        lines = [line for line in response.iter_lines() if line]

    event_names = [line.removeprefix("event: ") for line in lines[::2]]
    data_payloads = [json.loads(line.removeprefix("data: ")) for line in lines[1::2]]

    assert event_names == ["error", "done"]
    assert data_payloads[0]["payload"]["code"] == "PYTHON_AGENT_ERROR"
    assert data_payloads[0]["payload"]["message"] == "Python Agent 执行失败，请稍后重试"
    assert data_payloads[1]["payload"]["status"] == "FAILED"


def test_file_cancelled_tasks_supports_cross_worker_marker(tmp_path: Path) -> None:
    cancelled_tasks = server.FileCancelledTasks(tmp_path)

    assert "task-001" not in cancelled_tasks

    cancelled_tasks.add("task-001")

    assert "task-001" in cancelled_tasks

    cancelled_tasks.discard("task-001")

    assert "task-001" not in cancelled_tasks


def test_settings_switch_provider_via_env() -> None:
    settings = Settings.model_validate(
        {
            "APP_NAME": "agent",
            "ACTIVE_PROVIDER": "spark",
            "FALLBACK_PROVIDER": "openai_compatible",
            "SPARK_API_KEY": "spark-key",
            "OPENAI_COMPATIBLE_API_KEY": "openai-key",
            "SPARK_MODEL_NAME": "Spark Ultra",
            "MODEL_NAME": "qwen3.6-plus",
        }
    )

    assert settings.runtime_provider_name() == "spark"
    assert settings.resolve_logical_model("main_chat_model") == "Spark Ultra"


def test_settings_fallback_to_bailian_when_active_provider_not_ready() -> None:
    settings = Settings.model_validate(
        {
            "APP_NAME": "agent",
            "ACTIVE_PROVIDER": "spark",
            "FALLBACK_PROVIDER": "openai_compatible",
            "SPARK_API_KEY": "",
            "OPENAI_COMPATIBLE_API_KEY": "openai-key",
            "SPARK_MODEL_NAME": "Spark Ultra",
            "MODEL_NAME": "qwen3.6-plus",
        }
    )

    assert settings.runtime_provider_name() == "openai_compatible"
    assert settings.resolve_logical_model("main_chat_model") == "qwen3.6-plus"
