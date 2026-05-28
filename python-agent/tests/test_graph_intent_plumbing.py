import pytest

from knowledge.benchmark_graph_rag_100 import evaluate_graph_evidence, summarize_graph_records
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
