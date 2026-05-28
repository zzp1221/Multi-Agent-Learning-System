import pytest

from knowledge.benchmark_graph_rag_100 import (
    evaluate_graph_evidence,
    summarize_graph_records,
    summarize_intent_mismatches,
    summarize_low_value_sources,
)
from knowledge.benchmark_rag_100 import _fusion_replacements, _summarize_low_value_sources
from retrieval.hybrid_retriever import HybridRetriever
from retrieval.graph_expander import GraphExpander
from retrieval.rrf_fusion import RRFFusion
from src.ai_modules.agents.base import PlaceholderAgent
from src.ai_modules.models import (
    EngineStreamRequest,
    ProgressPayload,
    ProgressSSEEvent,
    ResultChunkPayload,
    ResultChunkSSEEvent,
)
from src.ai_modules.retrieval import HybridRetrievalService, QueryClassifier
from src.ai_modules.supervisor import PythonAgentSupervisor


class GraphIntentRetriever:
    def __init__(self) -> None:
        self.last_graph_intent = None

    def retrieve(self, query: str, *, graph_intent: str | None = None) -> dict:
        self.last_graph_intent = graph_intent
        return {
            "query": query,
            "graphIntent": graph_intent,
            "channels": {
                "grep": {"priority": []},
                "vector": [],
                "graph": [("doc-b", "Doc B", 1.0)],
            },
            "top": [("doc-a", "Doc A", 1.0), ("doc-b", "Doc B", 0.9)],
        }


class FakeGraphCursor:
    def __init__(self) -> None:
        self._rows = []
        self._pages = {
            "incoming-id": ("incoming-id", "incoming-doc", "Incoming Doc"),
            "weak-tag-id": ("weak-tag-id", "weak-tag-doc", "Weak Tag Doc"),
            "strong-tag-id": ("strong-tag-id", "strong-tag-doc", "Strong Tag Doc"),
        }

    def execute(self, sql, params):
        if "FROM rag.wiki_page" in sql and "slug = ANY" in sql and "LEFT JOIN rag.wiki_page_graph_features" not in sql:
            self._rows = [("seed-id", "seed-doc", "Seed Doc")]
            return
        if "FROM rag.wiki_link l" in sql and "GROUP BY neighbor_id" in sql:
            self._rows = [
                ("incoming-id", "WIKILINK", 1),
                ("weak-tag-id", "SHARED_TAG", 2),
                ("strong-tag-id", "SHARED_TAG", 3),
                ("seed-id", "WIKILINK", 1),
            ]
            return
        if "LEFT JOIN rag.wiki_page_graph_features" in sql:
            candidate_ids = set(params[0])
            self._rows = [
                (*row, 1 if row[0] == "strong-tag-id" else 2, 0.9 if row[0] == "strong-tag-id" else 0.2, '["alias"]', '["tag"]')
                for page_id, row in self._pages.items()
                if page_id in candidate_ids
            ]
            self._rows = [row for row in self._rows if row[0] in candidate_ids]
            return
        if "FROM rag.wiki_page_graph_features" in sql:
            self._rows = [(1,)]
            return
        if "WHERE id::text = ANY" in sql:
            candidate_ids = set(params[0])
            self._rows = [row for page_id, row in self._pages.items() if page_id in candidate_ids]
            return
        raise AssertionError(f"unexpected SQL: {sql}")

    def fetchall(self):
        return self._rows


def test_graph_expander_uses_undirected_edges_and_filters_weak_shared_tags() -> None:
    results = GraphExpander().expand(FakeGraphCursor(), ["seed-doc"], top_n=5, min_shared_tags=3)

    assert [item[0] for item in results] == ["strong-tag-doc", "incoming-doc"]
    assert all(item[0] != "weak-tag-doc" for item in results)


def test_graph_expander_explain_candidates_keeps_candidate_rows() -> None:
    explanation = GraphExpander().explain_candidates(
        FakeGraphCursor(),
        ["seed-doc"],
        query="strong tag",
        graph_intent="PREREQUISITE_PATH",
    )

    assert [item["slug"] for item in explanation["candidates"]] == ["strong-tag-doc", "incoming-doc"]
    assert explanation["seedSlugs"] == ["seed-doc"]
    assert explanation["queryTerms"] == ["strong", "tag"]


def test_prerequisite_graph_low_value_filter_covers_video_resource_slug() -> None:
    expander = GraphExpander()
    retriever = HybridRetriever({})

    assert expander._is_low_value_resource("视频资源/离散数学-图论基础与应用", "Graph Doc") is True
    assert retriever._graph_slug_penalty("视频资源/离散数学-图论基础与应用") < 1.0


def test_query_classifier_marks_graph_relation_intent() -> None:
    result = QueryClassifier().classify({"query": "请从知识图谱角度串联操作系统安全和SQL注入的关系"})

    assert result.retrieval_strategy == "LOCAL_HYBRID"
    assert result.graph_intent == "CROSS_LAYER_RELATION"


def test_query_classifier_marks_multi_hop_relation_intent() -> None:
    result = QueryClassifier().classify({"query": "请解释鸽巢原理、图着色和欧拉图之间的多跳联系"})

    assert result.retrieval_strategy == "LOCAL_HYBRID"
    assert result.graph_intent == "MULTI_HOP_RELATION"


def test_query_classifier_keeps_plain_relation_model_as_non_graph_intent() -> None:
    result = QueryClassifier().classify({"query": "关系模型是什么"})

    assert result.retrieval_strategy == "LOCAL_HYBRID"
    assert result.graph_intent is None


def test_query_classifier_marks_graph_prerequisite_path_intent() -> None:
    result = QueryClassifier().classify({"query": "请给出从NFA到DFA最小化的学习路径和前置知识"})

    assert result.retrieval_strategy == "LOCAL_HYBRID"
    assert result.graph_intent == "PREREQUISITE_PATH"


def test_hybrid_retrieval_service_passes_graph_intent_to_retriever() -> None:
    retriever = GraphIntentRetriever()
    service = HybridRetrievalService(retriever=retriever)

    raw = service.retrieve_raw("graph query", graph_intent="PREREQUISITE_PATH")

    assert retriever.last_graph_intent == "PREREQUISITE_PATH"
    assert raw["graphIntent"] == "PREREQUISITE_PATH"


def test_rrf_graph_weight_override_remains_opt_in() -> None:
    fusion = RRFFusion()
    grep_results = {"priority": [("knowledge-a", "Knowledge A", 1.0, ["a"])], "normal": []}
    vector_results = [("knowledge-a", "Knowledge A", 0.9)]
    graph_results = [("graph-b", "Graph B", 8.0)]

    default_top = fusion.fuse(grep_results, vector_results, graph_results, [])
    graph_top = fusion.fuse(
        grep_results,
        vector_results,
        graph_results,
        [("https://example.com/video", "Video", 0.9)],
        graph_weight=12.0,
        slug_penalty=lambda slug: 0.2 if slug.startswith("http") else 1.0,
    )

    assert default_top[0][0] == "knowledge-a"
    assert graph_top[0][0] == "graph-b"
    assert graph_top[-1][0] == "https://example.com/video"


def test_graph_intent_keeps_top3_and_fills_low_trust_tail() -> None:
    retriever = HybridRetriever({})
    fused = [
        ("doc-a", "Doc A", 0.2),
        ("doc-b", "Doc B", 0.19),
        ("doc-c", "Doc C", 0.18),
        ("None", "Video", 0.17),
        ("https://example.com/ref", "External", 0.16),
    ]
    graph_results = [
        ("doc-b", "Doc B", 4),
        ("graph-d", "Graph D", 3),
        ("graph-e", "Graph E", 2),
    ]

    result = retriever._stabilize_graph_top5(fused, graph_results, "PREREQUISITE_PATH")

    assert [item[0] for item in result[:3]] == ["doc-a", "doc-b", "doc-c"]
    assert [item[0] for item in result[3:5]] == ["graph-d", "graph-e"]


def test_non_prerequisite_graph_intent_does_not_fill_tail() -> None:
    retriever = HybridRetriever({})
    fused = [
        ("doc-a", "Doc A", 0.2),
        ("doc-b", "Doc B", 0.19),
        ("doc-c", "Doc C", 0.18),
        ("None", "Video", 0.17),
        ("https://example.com/ref", "External", 0.16),
    ]
    graph_results = [("graph-d", "Graph D", 3)]

    result = retriever._stabilize_graph_top5(fused, graph_results, "COMPARISON")

    assert result == fused


def test_graph_intent_forces_grep_first_to_keep_graph_channel() -> None:
    class FakeGrep:
        def search(self, cur, query, domain):
            del cur, query, domain
            return {"priority": [("seed-doc", "Seed Doc", 0.95, ["seed"])], "normal": []}

    class FakeVector:
        def search_all(self, cur, query, top_k, domain):
            del cur, query, top_k, domain
            return [("vector-doc", "Vector Doc", 0.9, "knowledge")]

    class FakeGraph:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def expand(self, cur, seed_slugs, top_n, query=None, graph_intent=None):
            del cur
            self.calls.append({"seed_slugs": list(seed_slugs), "top_n": top_n, "query": query, "graph_intent": graph_intent})
            return [("graph-doc", "Graph Doc", 10)]

    class FakeWeb:
        def search(self, query, top_k):
            del query, top_k
            return []

    fake_graph = FakeGraph()
    retriever = HybridRetriever({}, top_k=3, graph_seed_n=1)
    retriever._initialized = True
    retriever._grep = FakeGrep()
    retriever._vector = FakeVector()
    retriever._graph = fake_graph
    retriever._web = FakeWeb()
    retriever._rrf = RRFFusion()

    plain_result = retriever.retrieve_grep_first(object(), "seed query")
    graph_result = retriever.retrieve_grep_first(
        object(),
        "seed query",
        graph_intent="PREREQUISITE_PATH",
    )

    assert plain_result["channels"]["graph"] == []
    assert graph_result["channels"]["graph"] == [("graph-doc", "Graph Doc", 10)]
    assert graph_result["grepFirstPromoted"] is True
    assert fake_graph.calls[0]["top_n"] == 8
    assert graph_result["top"][:2] == [("vector-doc", "Vector Doc", 0.082), ("seed-doc", "Seed Doc", 0.0738)]


def test_graph_benchmark_metrics_cover_primary_and_related_nodes() -> None:
    question = {
        "graphIntent": "PREREQUISITE_PATH",
        "expectedSlug": "doc-a",
        "expectedRelatedSlugs": ["doc-b", "doc-c"],
    }
    candidates = [
        {"rank": 1, "slug": "doc-a", "title": "Doc A", "score": 1.0},
        {"rank": 2, "slug": "doc-b", "title": "Doc B", "score": 0.9},
        {"rank": 3, "slug": "doc-c", "title": "Doc C", "score": 0.8},
    ]

    metrics = evaluate_graph_evidence(question, candidates)

    assert metrics["primaryTop5"] is True
    assert metrics["anyRelatedTop5"] is True
    assert metrics["partialEvidenceTop5"] is True
    assert metrics["completeEvidenceTop5"] is True
    assert metrics["evidenceNodeRecallTop5"] == 1.0
    assert metrics["missingEvidenceSlugsTop5"] == []


def test_graph_benchmark_summary_groups_evidence_metrics() -> None:
    records = [
        {
            "graphMetrics": {
                "primaryTop5": True,
                "anyRelatedTop5": True,
                "partialEvidenceTop5": True,
                "completeEvidenceTop5": True,
                "evidenceNodeRecallTop5": 1.0,
                "presentEvidenceNodesTop5": 3,
                "expectedEvidenceNodes": 3,
            }
        },
        {
            "graphMetrics": {
                "primaryTop5": False,
                "anyRelatedTop5": True,
                "partialEvidenceTop5": True,
                "completeEvidenceTop5": False,
                "evidenceNodeRecallTop5": 0.3333,
                "presentEvidenceNodesTop5": 1,
                "expectedEvidenceNodes": 3,
            }
        },
    ]

    summary = summarize_graph_records(records)

    assert summary["primaryTop5Pct"] == 50.0
    assert summary["anyRelatedTop5Pct"] == 100.0
    assert summary["completeEvidenceTop5Pct"] == 50.0
    assert summary["evidenceNodeRecallTop5Pct"] == 66.67


def test_graph_benchmark_summarizes_intent_mismatches() -> None:
    records = [
        {
            "id": "grq001",
            "graphIntent": "CROSS_LAYER_RELATION",
            "classifierGraphIntent": "PREREQUISITE_PATH",
            "retrievalGraphIntent": "CROSS_LAYER_RELATION",
        },
        {
            "id": "grq002",
            "graphIntent": "PREREQUISITE_PATH",
            "classifierGraphIntent": "PREREQUISITE_PATH",
            "retrievalGraphIntent": "PREREQUISITE_PATH",
        },
    ]

    summary = summarize_intent_mismatches(records)

    assert summary["count"] == 1
    assert summary["pct"] == 50.0
    assert summary["examples"][0]["id"] == "grq001"


def test_benchmark_diagnostics_track_low_value_sources_and_replacements() -> None:
    low_value = _summarize_low_value_sources(
        grep_results={
            "priority": [("None", "Video Doc", 1.0, [])],
            "normal": [("wiki://course/ref", "Wiki Ref", 0.8, [])],
        },
        vector_results=[("https://example.com/ref", "External Ref", 0.9)],
        graph_results=[("视频资源/图论", "Graph Video", 2.0)],
        web_results=[("https://example.com/video", "Video", 0.7)],
    )
    replacements = _fusion_replacements(
        [("doc-a", "Doc A", 0.2), ("None", "Video", 0.1)],
        [("doc-a", "Doc A", 0.2), ("graph-b", "Graph B", 0.01)],
    )

    assert low_value["byChannel"]["grepPriority"]["none"] == 1
    assert low_value["byChannel"]["grepNormal"]["wiki"] == 1
    assert low_value["byChannel"]["vector"]["http"] == 1
    assert low_value["byChannel"]["graph"]["video"] == 1
    assert low_value["byChannel"]["web"]["http"] == 1
    assert replacements == [
        {
            "rank": 2,
            "before": {"rank": 2, "slug": "None", "title": "Video", "score": 0.1, "extra": None},
            "after": {"rank": 2, "slug": "graph-b", "title": "Graph B", "score": 0.01, "extra": None},
        }
    ]


def test_graph_report_summarizes_low_value_sources_from_records() -> None:
    records = [
        {
            "id": "grq001",
            "diagnostics": {
                "lowValueSources": {
                    "byChannel": {
                        "vector": {"none": 1, "http": 2, "wiki": 0, "video": 0},
                        "graph": {"none": 0, "http": 0, "wiki": 1, "video": 0},
                    },
                    "items": [
                        {"channel": "vector", "rank": 1, "kind": "http", "slug": "https://x", "title": "X"}
                    ],
                }
            },
        }
    ]

    summary = summarize_low_value_sources(records)

    assert summary["byChannel"]["vector"]["http"] == 2
    assert summary["byChannel"]["graph"]["wiki"] == 1
    assert summary["examples"][0]["id"] == "grq001"


class RecordingRewriteAgent(PlaceholderAgent):
    def __init__(self) -> None:
        super().__init__("recording rewrite", "query_rewrite")

    async def run(self, *, task_id, trace_id, seq, params, **kwargs):
        params["rewrittenQuery"] = params["query"]
        params["keywords"] = ["graph"]
        yield ProgressSSEEvent(
            taskId=task_id,
            traceId=trace_id,
            seq=seq,
            payload=ProgressPayload(stage="query_rewrite", percent=20, message="ok"),
        )


class RecordingRetrievalAgent(PlaceholderAgent):
    def __init__(self) -> None:
        super().__init__("recording retrieval", "retrieving")
        self.seen_graph_intent = None

    async def run(self, *, task_id, trace_id, seq, params, **kwargs):
        self.seen_graph_intent = params.get("graphIntent")
        params["retrievalRawResult"] = {"graphIntent": self.seen_graph_intent}
        yield ResultChunkSSEEvent(
            taskId=task_id,
            traceId=trace_id,
            seq=seq,
            payload=ResultChunkPayload(text="retrieved"),
        )


class RecordingTutorAgent(PlaceholderAgent):
    def __init__(self) -> None:
        super().__init__("recording tutor", "tutoring")

    async def run(self, *, task_id, trace_id, seq, params, **kwargs):
        yield ResultChunkSSEEvent(
            taskId=task_id,
            traceId=trace_id,
            seq=seq,
            payload=ResultChunkPayload(text=str(params.get("graphIntent"))),
        )


@pytest.mark.asyncio
async def test_supervisor_tutoring_route_keeps_graph_intent_for_retrieval() -> None:
    supervisor = PythonAgentSupervisor()
    retrieval_agent = RecordingRetrievalAgent()
    supervisor.agent_registry["query_rewrite"] = RecordingRewriteAgent()
    supervisor.agent_registry["retrieval"] = retrieval_agent
    supervisor.agent_registry["tutor"] = RecordingTutorAgent()
    request = EngineStreamRequest(
        serviceType="TUTORING",
        params={"query": "请给出从NFA到DFA最小化的学习路径和前置知识"},
        taskId="task-graph-plumbing",
        traceId="trace-graph-plumbing",
    )

    events = [event async for event in supervisor.stream(request)]

    assert retrieval_agent.seen_graph_intent == "PREREQUISITE_PATH"
    assert any(event.event == "done" for event in events)
