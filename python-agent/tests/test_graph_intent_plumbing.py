import pytest
import argparse
import sys
from datetime import datetime, timedelta, timezone

import knowledge.benchmark_graph_rag_100 as graph_benchmark
from knowledge.benchmark_graph_rag_100 import (
    _failed_quality_gate_names,
    classify_low_evidence_reasons,
    evaluate_graph_evidence,
    summarize_graph_quality_gates,
    summarize_graph_records,
    summarize_intent_mismatches,
    summarize_low_evidence_by_intent,
    summarize_low_value_sources,
)
from knowledge.benchmark_rag_100 import (
    QueryEmbeddingCache,
    _failed_rag_quality_gate_names,
    _fusion_replacements,
    _judge_with_retries,
    _quality_thresholds_from_args as rag_quality_thresholds_from_args,
    _search_vector_with_retries,
    _summarize_low_value_sources,
    summarize_rag_quality_gates,
    summarize_channel_errors,
)
import knowledge.benchmark_rag_100 as rag_benchmark
from src.ai_modules.llms.errors import LLMServiceError
from knowledge.graph_low_evidence_repairs import build_repair_wikilinks, load_repair_link_records
from retrieval.graph_relation_policy import relation_base_weight, weighted_relation_score
from retrieval.hybrid_retriever import HybridRetriever
from retrieval.graph_expander import GraphExpander
from retrieval.rrf_fusion import RRFFusion
from retrieval.source_quality import low_value_source_kind
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
            "two-hop-id": ("two-hop-id", "two-hop-doc", "Two Hop Doc"),
            "low-value-id": ("low-value-id", "http://example.com/video", "Video Resource"),
            "none-id": ("none-id", "None", "None Resource"),
            "direct-id": ("direct-id", "direct-doc", "Direct Evidence Doc"),
            "seed-id": ("seed-id", "seed-doc", "Seed Doc"),
        }

    def execute(self, sql, params):
        if "FROM rag.wiki_page" in sql and "slug = ANY" in sql and "LEFT JOIN rag.wiki_page_graph_features" not in sql:
            self._rows = [("seed-id", "seed-doc", "Seed Doc")]
            return
        if "FROM rag.wiki_link l" in sql and "GROUP BY source_id, neighbor_id" in sql:
            self._rows = [
                ("strong-tag-id", "two-hop-id", 2, 0),
                ("strong-tag-id", "seed-id", 5, 0),
                ("incoming-id", "low-value-id", 3, 0),
                ("incoming-id", "incoming-id", 4, 0),
                ("incoming-id", "none-id", 4, 0),
            ]
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
            if "wp.slug = ANY" in sql:
                seed_slugs = set(params[0])
                candidate_ids = {
                    page_id
                    for page_id, row in self._pages.items()
                    if row[1] in seed_slugs
                }
            elif "wp.id::text = ANY" in sql:
                candidate_ids = set(params[0])
            else:
                pattern = str(params[0]).strip("%").lower()
                candidate_ids = {
                    page_id
                    for page_id, row in self._pages.items()
                    if pattern in row[1].lower() or pattern in row[2].lower()
                }
            self._rows = [
                (
                    *row,
                    1 if row[0] == "strong-tag-id" else 2,
                    0.9 if row[0] == "strong-tag-id" else 0.2,
                    '["direct alias"]' if row[0] == "direct-id" else '["seed alias"]' if row[0] == "seed-id" else '["alias"]',
                    '["tag"]',
                )
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


def test_graph_relation_weight_policy_keeps_existing_relation_weights() -> None:
    assert relation_base_weight("WIKILINK") == 2.0
    assert relation_base_weight("SHARED_TAG") == 1.0
    assert relation_base_weight("COMMUNITY") == 0.75
    assert weighted_relation_score("WIKILINK", 3) == 6.0
    assert weighted_relation_score("UNKNOWN", 2) == 2.0


def test_graph_expander_scores_db_edge_weight_not_just_edge_count() -> None:
    qualified = GraphExpander()._qualified_from_edge_rows(
        [
            ("plain-id", "WIKILINK", 1, 1.0),
            ("repair-id", "WIKILINK", 1, 1.4),
            ("tag-id", "SHARED_TAG", 3, 3.0),
        ],
        min_shared_tags=3,
        excluded_ids=set(),
        decay=1.0,
    )

    assert qualified[0] == ("tag-id", 3.0, 0, 3)
    assert qualified[1] == ("repair-id", 2.8, 1, 0)
    assert qualified[2] == ("plain-id", 2.0, 1, 0)


def test_graph_low_evidence_repairs_build_reproducible_wikilinks(tmp_path) -> None:
    repair_path = tmp_path / "repairs.json"
    repair_path.write_text(
        """
{
  "repairs": [
    {
      "id": "case-cross",
      "graphIntent": "CROSS_LAYER_RELATION",
      "links": [
        ["course/a", "course/b"],
        ["course/a", "course/b"],
        ["course/a", "course/a"]
      ]
    },
    {
      "id": "case-path",
      "graphIntent": "PREREQUISITE_PATH",
      "links": [["course/c", "course/d"]]
    }
  ]
}
""",
        encoding="utf-8",
    )
    records = load_repair_link_records(repair_path)
    links = build_repair_wikilinks(
        records,
        pages=[
            {"slug": "course/a", "title": "A"},
            {"slug": "course/b", "title": "B"},
            {"slug": "course/c", "title": "C"},
            {"slug": "course/d", "title": "D"},
        ],
    )

    assert {"from_title": "A", "to_title": "B", "relation": "WIKILINK", "weight": 1.0, "repair_id": "case-cross", "graph_intent": "CROSS_LAYER_RELATION"} in links
    assert {"from_title": "C", "to_title": "D", "relation": "WIKILINK", "weight": 1.0, "repair_id": "case-path", "graph_intent": "PREREQUISITE_PATH"} in links
    assert len(links) == 2
    assert all(link["from_title"] != link["to_title"] for link in links)


def test_graph_low_evidence_repairs_preserve_semantic_relation_metadata(tmp_path) -> None:
    repair_path = tmp_path / "repairs.json"
    repair_path.write_text(
        """
{
  "repairs": [
    {
      "id": "grq-cross",
      "graphIntent": "CROSS_LAYER_RELATION",
      "links": [
        ["course/a", "course/b", "CROSS_LAYER_RELATION"],
        {"from": "course/b", "to": "course/c", "relation": "UNSUPPORTED", "semanticRelation": "PREREQUISITE_OF"}
      ]
    }
  ]
}
""",
        encoding="utf-8",
    )

    records = load_repair_link_records(repair_path)
    links = build_repair_wikilinks(
        records,
        pages=[
            {"slug": "course/a", "title": "A"},
            {"slug": "course/b", "title": "B"},
            {"slug": "course/c", "title": "C"},
        ],
    )

    assert records[0]["semantic_relation"] == "CROSS_LAYER_RELATION"
    assert records[0]["relation"] == "WIKILINK"
    assert records[0]["weight"] > 1.0
    assert records[1]["relation"] == "WIKILINK"
    assert records[1]["semantic_relation"] == "PREREQUISITE_OF"
    assert {link["semantic_relation"] for link in links} == {"CROSS_LAYER_RELATION", "PREREQUISITE_OF"}
    assert {link["relation"] for link in links} == {"WIKILINK"}


def test_graph_expander_explain_candidates_keeps_candidate_rows() -> None:
    explanation = GraphExpander().explain_candidates(
        FakeGraphCursor(),
        ["seed-doc"],
        query="strong tag",
        graph_intent="PREREQUISITE_PATH",
    )

    assert [item["slug"] for item in explanation["candidates"][:3]] == ["strong-tag-doc", "incoming-doc", "two-hop-doc"]
    assert explanation["seedSlugs"] == ["seed-doc"]
    assert explanation["queryTerms"] == ["strong", "tag"]
    assert explanation["candidates"][0]["source"] == "graph_1hop"
    two_hop = next(item for item in explanation["candidates"] if item["slug"] == "two-hop-doc")
    assert two_hop["source"] == "graph_2hop"
    assert two_hop["hop"] == 2


def test_graph_expander_builds_prerequisite_direct_and_seed_evidence() -> None:
    evidence = GraphExpander().build_prerequisite_evidence(
        FakeGraphCursor(),
        ["seed-doc"],
        "请说明 seed doc 和 direct alias 的学习路径",
    )

    assert [item[0] for item in evidence["protectedSeeds"]] == ["seed-doc"]
    assert [item[0] for item in evidence["directEvidence"]] == ["direct-doc"]


def test_graph_expander_only_prerequisite_path_returns_bounded_two_hop() -> None:
    expander = GraphExpander()

    default_results = expander.expand(
        FakeGraphCursor(),
        ["seed-doc"],
        top_n=5,
        min_shared_tags=3,
        query="two hop",
    )
    prerequisite_results = expander.expand(
        FakeGraphCursor(),
        ["seed-doc"],
        top_n=5,
        min_shared_tags=3,
        query="two hop",
        graph_intent="PREREQUISITE_PATH",
    )
    assert all(len(item) == 3 for item in default_results)
    assert [item[0] for item in default_results] == ["strong-tag-doc", "incoming-doc"]
    assert any(item[0] == "two-hop-doc" and item[3] == "graph_2hop" for item in prerequisite_results)
    assert all(item[0] not in {"seed-doc", "http://example.com/video", "None"} for item in prerequisite_results)


def test_prerequisite_graph_low_value_filter_covers_video_resource_slug() -> None:
    expander = GraphExpander()
    retriever = HybridRetriever({})

    assert expander._is_low_value_resource("视频资源/离散数学-图论基础与应用", "Graph Doc") is True
    assert retriever._graph_slug_penalty("视频资源/离散数学-图论基础与应用") < 1.0
    assert expander._is_low_value_resource("knowledge-doc", None) is False


def test_graph_expander_filters_low_value_for_all_graph_intents() -> None:
    expander = GraphExpander()
    score_parts = {
        "video-id": {"base": 10.0, "wikilink": 1, "sharedTag": 0},
        "doc-id": {"base": 9.0, "wikilink": 1, "sharedTag": 0},
    }
    page_rows = [
        ("video-id", "视频资源/计算机网络-TCP拥塞控制详解", "TCP 拥塞控制详解（视频）", 1, 0.1, "[]", "[]"),
        ("doc-id", "计算机网络/TCP拥塞控制", "TCP 拥塞控制", 1, 0.1, "[]", "[]"),
    ]

    graph_candidates = expander._score_page_rows(
        page_rows,
        score_parts,
        query_terms=[],
        seed_communities=set(),
        seed_slug_set=set(),
        hop=1,
        source="graph_1hop",
        prerequisite_intent=False,
        skip_low_value=expander._filters_low_value_for_intent("CROSS_LAYER_RELATION"),
    )
    plain_candidates = expander._score_page_rows(
        page_rows,
        score_parts,
        query_terms=[],
        seed_communities=set(),
        seed_slug_set=set(),
        hop=1,
        source="graph_1hop",
        prerequisite_intent=False,
        skip_low_value=expander._filters_low_value_for_intent(None),
    )

    assert [item["slug"] for item in graph_candidates] == ["计算机网络/TCP拥塞控制"]
    assert {item["slug"] for item in plain_candidates} == {
        "视频资源/计算机网络-TCP拥塞控制详解",
        "计算机网络/TCP拥塞控制",
    }


@pytest.mark.parametrize(
    ("slug", "title", "kind"),
    [
        ("", "empty", "none"),
        ("None", "placeholder", "none"),
        ("https://example.test/ref", "External", "http"),
        ("wiki://course/topic", "Wiki mirror", "wiki"),
        ("视频资源/course/topic", "Course video", "video"),
        ("course/topic-video", "Video lesson", "video"),
        ("course/topic", "Knowledge Doc", None),
    ],
)
def test_low_value_source_classification_is_shared(slug: str, title: str, kind: str | None) -> None:
    expander = GraphExpander()
    retriever = HybridRetriever({})

    assert low_value_source_kind(slug, title) == kind
    assert expander._is_low_value_resource(slug, title) is (kind is not None)
    assert (retriever._graph_slug_penalty(slug) < 1.0) is (kind is not None)


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


def test_hybrid_retriever_graph_weight_mapping() -> None:
    retriever = HybridRetriever({})

    assert retriever._graph_weight(None) == 0.5
    assert retriever._graph_weight("COMPARISON") == 1.2
    assert retriever._graph_weight("CROSS_LAYER_RELATION") == 1.4
    assert retriever._graph_weight("MECHANISM_APPLICATION") == 1.4
    assert retriever._graph_weight("COMMON_MISTAKE") == 1.3
    assert retriever._graph_weight("COMMUNITY_SUMMARY") == 1.5
    assert retriever._graph_weight("MULTI_HOP_RELATION") == 1.6
    assert retriever._graph_weight("PREREQUISITE_PATH") == 1.8


def test_hybrid_retriever_uses_configured_vector_embedding_budget(monkeypatch) -> None:
    class FakeTokenizer:
        def load_from_db(self, cur, domain):
            del cur, domain
            return 0

    created_vectors = []
    vector_kwargs = []

    class FakeVector:
        def __init__(self, **kwargs):
            vector_kwargs.append(kwargs)
            self.request_timeout = 10.0
            self.max_embedding_retries = 3
            self.embedding_retry_backoff_seconds = 0.5
            created_vectors.append(self)

    monkeypatch.setattr("retrieval.hybrid_retriever.FMMTokenizer", FakeTokenizer)
    monkeypatch.setattr("retrieval.hybrid_retriever.VectorSearcher", FakeVector)

    retriever = HybridRetriever({})
    retriever._init(object())

    assert created_vectors[0].request_timeout == 10.0
    assert created_vectors[0].max_embedding_retries == 3
    assert created_vectors[0].embedding_retry_backoff_seconds == 0.5
    assert "max_embedding_retries" not in vector_kwargs[0]
    assert "embedding_retry_backoff_seconds" not in vector_kwargs[0]


def test_hybrid_retriever_passes_dynamic_graph_weight_to_rrf() -> None:
    class FakeGrep:
        def search(self, cur, query, domain):
            del cur, query, domain
            return {"priority": [], "normal": []}

    class FakeVector:
        def search_all(self, cur, query, top_k, domain):
            del cur, query, top_k, domain
            return []

    class FakeGraph:
        def expand(self, cur, seed_slugs, top_n, query=None, graph_intent=None):
            del cur, seed_slugs, top_n, query, graph_intent
            return [("graph-doc", "Graph Doc", 10)]

    class FakeWeb:
        def search(self, query, top_k):
            del query, top_k
            return []

    class RecordingRRF:
        def __init__(self) -> None:
            self.graph_weight = None

        def fuse(self, *args, **kwargs):
            del args
            self.graph_weight = kwargs.get("graph_weight")
            return [("graph-doc", "Graph Doc", 0.1)]

    recording_rrf = RecordingRRF()
    retriever = HybridRetriever({}, top_k=3, graph_seed_n=1)
    retriever._initialized = True
    retriever._grep = FakeGrep()
    retriever._vector = FakeVector()
    retriever._graph = FakeGraph()
    retriever._web = FakeWeb()
    retriever._rrf = recording_rrf

    result = retriever.retrieve(object(), "graph query", graph_intent="MULTI_HOP_RELATION")

    assert recording_rrf.graph_weight == 1.6
    assert result["top"] == [("graph-doc", "Graph Doc", 0.1)]


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


def test_strong_grep_top_promotion_only_reorders_existing_top3() -> None:
    retriever = HybridRetriever({})
    grep_results = {"priority": [("doc-b", "Doc B", 1.0, ["doc b"])], "normal": []}
    fused = [
        ("doc-a", "Doc A", 0.2),
        ("doc-b", "Doc B", 0.19),
        ("doc-c", "Doc C", 0.18),
        ("doc-d", "Doc D", 0.17),
    ]

    promoted, diagnostics = retriever._promote_strong_grep_top_with_diagnostics(
        fused,
        grep_results,
        "COMPARISON",
    )
    unchanged, unchanged_diagnostics = retriever._promote_strong_grep_top_with_diagnostics(
        fused,
        {"priority": [("doc-d", "Doc D", 1.0, ["doc d"])], "normal": []},
        "COMPARISON",
    )

    assert [item[0] for item in promoted[:3]] == ["doc-b", "doc-a", "doc-c"]
    assert diagnostics["reason"] == "strong_grep_top_in_top3"
    assert unchanged == fused
    assert unchanged_diagnostics["promotedSlug"] is None


def test_graph_intent_protects_strong_grep_top3_when_pushed_to_top5() -> None:
    retriever = HybridRetriever({})
    fused = [
        ("vector-a", "Vector A", 0.2),
        ("vector-b", "Vector B", 0.19),
        ("doc-c", "Doc C", 0.18),
        ("doc-a", "Doc A", 0.17),
        ("doc-b", "Doc B", 0.16),
    ]
    grep_results = {
        "priority": [
            ("doc-a", "Doc A", 1.0, ["a"]),
            ("doc-b", "Doc B", 0.98, ["b"]),
            ("doc-c", "Doc C", 0.98, ["c"]),
        ],
        "normal": [],
    }

    protected, diagnostics = retriever._protect_strong_grep_top3_with_diagnostics(
        fused,
        grep_results,
        "COMPARISON",
    )

    assert [item[0] for item in protected[:3]] == ["doc-a", "doc-b", "doc-c"]
    assert diagnostics["reason"] == "strong_grep_top3_in_top5"


def test_graph_intent_does_not_replace_protected_seed_tail() -> None:
    retriever = HybridRetriever({})
    fused = [
        ("doc-a", "Doc A", 0.2),
        ("doc-b", "Doc B", 0.19),
        ("doc-c", "Doc C", 0.18),
        ("seed-doc", "Seed Doc", 0.17),
        ("tail-doc", "Tail Doc", 0.16),
    ]
    graph_results = [("graph-d", "Graph D", 3), ("graph-e", "Graph E", 2)]

    result, diagnostics = retriever._stabilize_graph_top5_with_diagnostics(
        fused,
        graph_results,
        "PREREQUISITE_PATH",
        protected_slugs={"seed-doc"},
    )

    assert result[3][0] == "seed-doc"
    assert result[4][0] == "graph-d"
    assert diagnostics["seedProtectedTop5"] == ["seed-doc"]
    assert diagnostics["replacementReason"][0]["reason"] == "replace_unprotected_tail"


def test_graph_intent_does_not_replace_protected_direct_evidence_tail() -> None:
    retriever = HybridRetriever({})
    fused = [
        ("doc-a", "Doc A", 0.2),
        ("doc-b", "Doc B", 0.19),
        ("doc-c", "Doc C", 0.18),
        ("tail-doc", "Tail Doc", 0.17),
        ("direct-doc", "Direct Doc", 0.16),
    ]
    graph_results = [
        ("direct-doc", "Direct Doc", 40.0, "direct_evidence"),
        ("weak-doc", "Weak Doc", 33.0, "direct_evidence"),
    ]

    result, diagnostics = retriever._stabilize_graph_top5_with_diagnostics(
        fused,
        graph_results,
        "PREREQUISITE_PATH",
        protected_slugs={"direct-doc"},
    )

    assert result[4][0] == "direct-doc"
    assert diagnostics["seedProtectedTop5"] == ["direct-doc"]
    assert diagnostics["replacementReason"] == []


def test_prerequisite_tail_promotion_can_raise_existing_later_candidate() -> None:
    retriever = HybridRetriever({})
    fused = [
        ("doc-a", "Doc A", 0.2),
        ("doc-b", "Doc B", 0.19),
        ("doc-c", "Doc C", 0.18),
        ("tail-a", "Tail A", 0.17),
        ("tail-b", "Tail B", 0.16),
        ("direct-doc", "Direct Doc", 0.15),
    ]
    graph_results = [
        ("direct-doc", "Direct Doc", 37.5, "direct_evidence"),
        ("graph-doc", "Graph Doc", 8),
    ]

    result, diagnostics = retriever._stabilize_graph_top5_with_diagnostics(
        fused,
        graph_results,
        "PREREQUISITE_PATH",
    )

    assert [item[0] for item in result[:3]] == ["doc-a", "doc-b", "doc-c"]
    assert result[4][0] == "direct-doc"
    assert [item[0] for item in result].count("direct-doc") == 1
    assert diagnostics["replacementReason"][0]["insertedSlug"] == "direct-doc"


def test_prerequisite_protected_seed_is_inserted_before_plain_graph_candidate() -> None:
    retriever = HybridRetriever({})
    fused = [
        ("doc-a", "Doc A", 0.2),
        ("doc-b", "Doc B", 0.19),
        ("doc-c", "Doc C", 0.18),
        ("None", "Video", 0.17),
        ("tail-doc", "Tail Doc", 0.16),
    ]
    graph_results = retriever._merge_graph_evidence(
        direct_evidence=[],
        graph_results=[("graph-doc", "Graph Doc", 10)],
        protected_seeds=[("seed-doc", "Seed Doc", 29, "seed_protected")],
    )

    result, diagnostics = retriever._stabilize_graph_top5_with_diagnostics(
        fused,
        graph_results,
        "PREREQUISITE_PATH",
        protected_slugs={"seed-doc"},
    )

    assert result[3][0] == "seed-doc"
    assert diagnostics["seedProtectedTop5"] == ["seed-doc"]
    assert diagnostics["replacementReason"][0]["insertedSlug"] == "seed-doc"
    assert diagnostics["replacementReason"][0]["reason"] == "replace_low_value_tail"


def test_prerequisite_retrieval_keeps_seed_and_related_neighbor() -> None:
    class FakeGrep:
        def search(self, cur, query, domain):
            del cur, query, domain
            return {"priority": [("seed-doc", "Seed Doc", 1.0, ["seed"])], "normal": []}

    class FakeVector:
        def search_all(self, cur, query, top_k, domain):
            del cur, query, top_k, domain
            return []

    class FakeGraph:
        def expand(self, cur, seed_slugs, top_n, query=None, graph_intent=None):
            del cur, seed_slugs, top_n, query
            assert graph_intent == "PREREQUISITE_PATH"
            return [("neighbor-doc", "Neighbor Doc", 10, "graph_1hop")]

        def build_prerequisite_evidence(self, cur, seed_slugs, query):
            del cur, query
            return {
                "queryTerms": ["seed"],
                "protectedSeeds": [(seed_slugs[0], "Seed Doc", 30, "seed_protected")],
                "directEvidence": [],
            }

    class FakeWeb:
        def search(self, query, top_k):
            del query, top_k
            return []

    retriever = HybridRetriever({}, top_k=3, graph_seed_n=1)
    retriever._initialized = True
    retriever._grep = FakeGrep()
    retriever._vector = FakeVector()
    retriever._graph = FakeGraph()
    retriever._web = FakeWeb()
    retriever._rrf = RRFFusion()

    result = retriever.retrieve(
        object(),
        "构建一条学习路径 seed",
        graph_intent="PREREQUISITE_PATH",
    )

    top_slugs = [item[0] for item in result["top"]]
    assert "seed-doc" in top_slugs
    assert "neighbor-doc" in top_slugs
    assert result["graphDiagnostics"]["wikiTraversal"]["enabled"] is True


def test_prerequisite_direct_evidence_requires_strong_path_signal() -> None:
    retriever = HybridRetriever({})

    assert retriever._uses_prerequisite_evidence_fill(
        "PREREQUISITE_PATH",
        "请构建一条学习路径，说明 NFA 如何依赖或通向 DFA 最小化。",
    )
    assert not retriever._uses_prerequisite_evidence_fill(
        "PREREQUISITE_PATH",
        "请从知识图谱关系角度说明图着色与 NP 完全性之间的多跳联系。",
    )


def test_graph_expander_direct_evidence_terms_split_chinese_quoted_enums() -> None:
    expander = GraphExpander()

    terms = expander._extract_direct_evidence_terms("请构建一条学习路径，说明「环与域」如何依赖或通向「群、同余与模运算」。")
    bonus = expander._query_bonus(
        terms,
        "离散数学/同余与模运算",
        '"同余与模运算"',
        '["Modular Arithmetic"]',
        "[]",
    )

    assert "群" in terms
    assert "同余与模运算" in terms
    assert bonus >= 3.0


def test_benchmark_vector_search_retries_transient_errors(monkeypatch) -> None:
    monkeypatch.setattr("knowledge.benchmark_rag_100.time.sleep", lambda _seconds: None)

    class FlakyVector:
        def __init__(self) -> None:
            self.calls = 0

        def search_all(self, cur, query, top_k, domain):
            del cur, query, top_k, domain
            self.calls += 1
            if self.calls < 3:
                raise ConnectionError("temporary embedding disconnect")
            return [("doc-a", "Doc A", 0.9, "knowledge")]

    class FakeRetriever:
        def __init__(self) -> None:
            self._vector = FlakyVector()
            self.top_k = 5
            self.domain = "COMPUTER_SCIENCE"

    retriever = FakeRetriever()

    assert _search_vector_with_retries(
        retriever,
        object(),
        "query",
        max_attempts=3,
    ) == [("doc-a", "Doc A", 0.9, "knowledge")]
    assert retriever._vector.calls == 3


def test_benchmark_embedding_cache_hit_miss_ttl_and_dimension(tmp_path) -> None:
    now = datetime(2026, 6, 14, tzinfo=timezone.utc)
    cache_path = tmp_path / "embedding_cache.json"
    cache = QueryEmbeddingCache(cache_path, ttl_days=2, now_fn=lambda: now)

    assert cache.get("query", model="model-a", dimension=3) is None
    cache.set("query", model="model-a", dimension=3, embedding=[0.1, 0.2, 0.3])
    assert cache.get("query", model="model-a", dimension=3) == [0.1, 0.2, 0.3]

    expired_cache = QueryEmbeddingCache(
        cache_path,
        ttl_days=2,
        now_fn=lambda: now + timedelta(days=3, seconds=1),
    )
    assert expired_cache.get("query", model="model-a", dimension=3) is None
    assert expired_cache.stats["expired"] == 1

    payload = cache_path.read_text(encoding="utf-8")
    payload = payload.replace('"dimension": 3', '"dimension": 4', 1)
    cache_path.write_text(payload, encoding="utf-8")
    mismatched_cache = QueryEmbeddingCache(cache_path, ttl_days=2, now_fn=lambda: now)
    assert mismatched_cache.get("query", model="model-a", dimension=3) is None
    assert mismatched_cache.stats["modelDimensionMismatches"] == 1


def test_benchmark_vector_search_uses_persistent_embedding_cache(tmp_path) -> None:
    class CacheableVector:
        model = "model-a"
        dimension = 3

        def __init__(self) -> None:
            self.calls = 0

        def _embed(self, text):
            self.calls += 1
            return [0.4, 0.5, 0.6]

        def search_all(self, cur, query, top_k, domain):
            del cur, top_k, domain
            embedding = self._embed(query)
            return [("doc-a", "Doc A", embedding[0], "knowledge")]

    class FakeRetriever:
        def __init__(self) -> None:
            self._vector = CacheableVector()
            self.top_k = 5
            self.domain = "COMPUTER_SCIENCE"

    cache = QueryEmbeddingCache(tmp_path / "embedding_cache.json")
    retriever = FakeRetriever()

    assert _search_vector_with_retries(retriever, object(), "same query", embedding_cache=cache)
    assert _search_vector_with_retries(retriever, object(), "same query", embedding_cache=cache)
    assert retriever._vector.calls == 1
    assert cache.stats["hits"] == 1


def test_non_evidence_fill_graph_intent_does_not_fill_tail() -> None:
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


def test_graph_relation_intent_protects_strong_grep_evidence_tail() -> None:
    retriever = HybridRetriever({})
    fused = [
        ("doc-a", "Doc A", 0.2),
        ("doc-b", "Doc B", 0.19),
        ("doc-c", "Doc C", 0.18),
        ("other-course/noisy-d", "Noisy D", 0.17),
        ("other-course/noisy-e", "Noisy E", 0.16),
    ]
    grep_results = {
        "priority": [
            ("doc-a", "Doc A", 1.0, ["a"]),
            ("target-course/target-x", "Target X", 1.0, ["x"]),
            ("target-course/target-y", "Target Y", 1.0, ["y"]),
        ],
        "normal": [],
    }

    result, diagnostics = retriever._protect_strong_grep_evidence_with_diagnostics(
        fused,
        grep_results,
        "CROSS_LAYER_RELATION",
    )

    assert [item[0] for item in result[:3]] == ["doc-a", "doc-b", "doc-c"]
    assert [item[0] for item in result[3:5]] == ["target-course/target-y", "target-course/target-x"]
    assert diagnostics["reason"] == "strong_grep_evidence_top6"


def test_graph_intent_balances_query_objects_into_top3() -> None:
    retriever = HybridRetriever({})
    query = "请比较「顺序查找」与「二叉排序树、KMP字符串匹配算法」的区别和联系。"
    fused = [
        ("数据结构/KMP字符串匹配", "KMP字符串匹配算法", 0.2),
        ("数据结构/二叉排序树", "二叉排序树", 0.19),
        ("算法设计与分析/KMP算法", "KMP字符串匹配算法", 0.18),
        ("数据结构/KMP算法", "KMP算法", 0.17),
        ("数据结构/顺序查找", "顺序查找", 0.16),
    ]
    grep_results = {
        "priority": [
            ("数据结构/顺序查找", "顺序查找", 1.0, ["顺序查找"]),
            ("数据结构/二叉排序树", "二叉排序树", 1.0, ["二叉排序树"]),
            ("数据结构/KMP字符串匹配", "KMP字符串匹配算法", 1.0, ["KMP"]),
        ],
        "normal": [],
    }

    result, diagnostics = retriever._balance_query_object_top3_with_diagnostics(
        fused,
        grep_results,
        [],
        [],
        query,
        "COMPARISON",
    )

    assert "数据结构/顺序查找" in [item[0] for item in result[:3]]
    assert diagnostics["reason"] == "query_object_top3_balance"


def test_query_object_balance_keeps_specific_primary_when_broad_alias_overlaps() -> None:
    retriever = HybridRetriever({})
    query = "请说明「并发编程-线程池原理」在机制落地时如何连接「C语言-多线程pthread、经典同步问题-生产者消费者」。"
    fused = [
        ("操作系统/经典同步问题-生产者消费者", "经典同步问题-生产者消费者", 0.0738),
        ("程序设计/并发编程", "并发编程", 0.0726),
        ("程序设计/线程池原理", "\"并发编程-线程池原理\"", 0.3023),
        ("程序设计/并发编程模型对比", "\"并发编程模型对比（线程/协程/Actor）\"", 0.1394),
        ("C语言深入/多线程pthread", "\"C语言-多线程pthread\"", 0.1287),
    ]

    result, diagnostics = retriever._balance_query_object_top3_with_diagnostics(
        fused,
        {"priority": [], "normal": []},
        [],
        [],
        query,
        "MECHANISM_APPLICATION",
    )

    top3_slugs = [item[0] for item in result[:3]]
    assert "程序设计/线程池原理" in top3_slugs
    assert "C语言深入/多线程pthread" in top3_slugs
    assert "程序设计/并发编程" not in top3_slugs
    assert diagnostics["promotedSlugs"][0]["replacedSlug"] == "程序设计/并发编程"


def test_query_object_balance_accepts_specific_prefix_alias() -> None:
    retriever = HybridRetriever({})
    query = "请构建一条学习路径，说明「NFA」如何依赖或通向「词法分析与自动机、DFA最小化（Hopcroft算法）」。"
    fused = [
        ("编译原理/DFA最小化", "DFA最小化", 0.2369),
        ("编译原理/词法分析与DFA", "\"词法分析与自动机\"", 0.2369),
        ("编译原理/NFA", "NFA", 0.0991),
        ("编译原理/DFA", "DFA", 0.177),
        ("编译原理/NFA转DFA", "NFA转DFA", 0.1701),
        ("编译原理/DFA最小化算法", "\"DFA最小化（Hopcroft算法）\"", 0.0879),
    ]

    result, diagnostics = retriever._balance_query_object_top3_with_diagnostics(
        fused,
        {"priority": [("编译原理/DFA最小化算法", "\"DFA最小化（Hopcroft算法）\"", 1.0, ["DFA"])], "normal": []},
        [],
        [],
        query,
        "PREREQUISITE_PATH",
    )

    assert [item[0] for item in result[:3]] == [
        "编译原理/DFA最小化",
        "编译原理/词法分析与DFA",
        "编译原理/NFA",
    ]
    assert diagnostics["promotedSlugs"] == []


def test_query_object_match_requires_exact_label_for_single_chinese_character() -> None:
    retriever = HybridRetriever({})

    assert retriever._item_matches_query_object(("离散数学/群论", "群", 0.1), "群") is True
    assert retriever._item_matches_query_object(("离散数学/置换群", "置换群", 0.1), "群") is False
    assert retriever._item_matches_query_object(("数据结构/栈", "栈", 0.1), "栈") is True
    assert retriever._item_matches_query_object(("数据结构/链栈", "链栈", 0.1), "栈") is False


def test_query_object_balance_promotes_exact_single_character_concept() -> None:
    retriever = HybridRetriever({})
    query = "请构建一条学习路径，说明「群」如何依赖或通向「置换群、环与域」。"
    fused = [
        ("离散数学/置换群", "置换群", 0.26),
        ("离散数学/解释", "解释", 0.07),
        ("离散数学/环与域", "环与域", 0.18),
        ("离散数学/群论", "群", 0.13),
        ("离散数学/谓词", "谓词", 0.01),
    ]

    result, diagnostics = retriever._balance_query_object_top3_with_diagnostics(
        fused,
        {"priority": [], "normal": []},
        [],
        [("离散数学/群论", "群", 33.0, "direct_evidence")],
        query,
        "PREREQUISITE_PATH",
    )

    assert "离散数学/群论" in [item[0] for item in result[:3]]
    assert diagnostics["reason"] == "query_object_top3_balance"


def test_graph_intent_balances_query_object_from_channel_pool() -> None:
    retriever = HybridRetriever({})
    query = "请从知识图谱关系角度说明「HTTP协议详解」与「TCP三次握手详解、OAuth2与OpenID Connect」之间的多跳联系。"
    fused = [
        ("软件工程/OAuth2与认证协议", "OAuth 2.0与OpenID Connect", 0.2),
        ("信息安全/OAuth2与OpenIDConnect", "OAuth2与OpenID Connect", 0.19),
        ("计算机网络/TCP四次挥手", "TCP四次挥手", 0.18),
        ("计算机网络/TCP首部", "TCP首部格式详解", 0.17),
    ]
    grep_results = {
        "priority": [
            ("计算机网络/应用层-HTTP", "HTTP协议详解", 1.0, ["HTTP"]),
            ("计算机网络/TCP三次握手", "TCP三次握手详解", 1.0, ["TCP"]),
        ],
        "normal": [],
    }

    result, diagnostics = retriever._balance_query_object_top3_with_diagnostics(
        fused,
        grep_results,
        [],
        [],
        query,
        "CROSS_LAYER_RELATION",
    )

    assert {"计算机网络/应用层-HTTP", "计算机网络/TCP三次握手"} <= {item[0] for item in result[:3]}
    assert diagnostics["reason"] == "query_object_top3_balance"


def test_graph_intent_promotes_primary_query_object_when_already_top3() -> None:
    retriever = HybridRetriever({})
    query = "请比较「后缀数组与后缀树」与「后缀数组算法、KMP字符串匹配算法」的区别和联系。"
    fused = [
        ("算法设计与分析/后缀数组算法", "后缀数组算法", 0.31),
        ("数据结构/后缀数组与后缀树", "后缀数组与后缀树", 0.22),
        ("数据结构/KMP字符串匹配", "KMP字符串匹配算法", 0.2),
    ]

    result, diagnostics = retriever._promote_primary_query_object_top1_with_diagnostics(
        fused,
        query,
        "COMPARISON",
    )

    assert result[0][0] == "数据结构/后缀数组与后缀树"
    assert result[1][0] == "算法设计与分析/后缀数组算法"
    assert diagnostics["reason"] == "primary_query_object_already_in_top3"


def test_primary_query_object_top1_does_not_replace_non_object_leader() -> None:
    retriever = HybridRetriever({})
    query = "请比较「顺序查找」与「二叉排序树、KMP字符串匹配算法」的区别和联系。"
    fused = [
        ("数据结构/散列表", "散列表", 0.31),
        ("数据结构/顺序查找", "顺序查找", 0.22),
        ("数据结构/KMP字符串匹配", "KMP字符串匹配算法", 0.2),
    ]

    result, diagnostics = retriever._promote_primary_query_object_top1_with_diagnostics(
        fused,
        query,
        "COMPARISON",
    )

    assert result == fused
    assert diagnostics["promotedSlug"] is None


def test_query_object_top3_balance_is_graph_only() -> None:
    retriever = HybridRetriever({})
    fused = [
        ("doc-a", "Doc A", 0.2),
        ("doc-b", "Doc B", 0.19),
        ("doc-c", "Doc C", 0.18),
        ("target-doc", "Target Doc", 0.17),
    ]
    grep_results = {"priority": [("target-doc", "Target Doc", 1.0, ["target"])], "normal": []}

    result, diagnostics = retriever._balance_query_object_top3_with_diagnostics(
        fused,
        grep_results,
        [],
        [],
        "请比较「Target Doc」与「Doc A」",
        None,
    )

    assert result == fused
    assert diagnostics["promotedSlugs"] == []


def test_strong_grep_evidence_protection_is_limited_to_relation_intents() -> None:
    retriever = HybridRetriever({})
    fused = [
        ("doc-a", "Doc A", 0.2),
        ("doc-b", "Doc B", 0.19),
        ("doc-c", "Doc C", 0.18),
        ("noisy-d", "Noisy D", 0.17),
        ("noisy-e", "Noisy E", 0.16),
    ]
    grep_results = {"priority": [("target-x", "Target X", 1.0, ["x"])], "normal": []}

    result, diagnostics = retriever._protect_strong_grep_evidence_with_diagnostics(
        fused,
        grep_results,
        "PREREQUISITE_PATH",
    )

    assert result == fused
    assert diagnostics["insertedSlugs"] == []


def test_strong_grep_evidence_protection_requires_exact_coverage() -> None:
    retriever = HybridRetriever({})
    fused = [
        ("doc-a", "Doc A", 0.2),
        ("doc-b", "Doc B", 0.19),
        ("doc-c", "Doc C", 0.18),
        ("noisy-d", "Noisy D", 0.17),
        ("noisy-e", "Noisy E", 0.16),
    ]
    grep_results = {"priority": [("generic-x", "Generic X", 0.98, ["x"])], "normal": []}

    result, diagnostics = retriever._protect_strong_grep_evidence_with_diagnostics(
        fused,
        grep_results,
        "COMPARISON",
    )

    assert result == fused
    assert diagnostics["insertedSlugs"] == []


def test_strong_grep_evidence_does_not_replace_more_specific_top5_evidence() -> None:
    retriever = HybridRetriever({})
    fused = [
        ("数据结构/后缀数组与后缀树", "后缀数组与后缀树", 0.2),
        ("算法设计与分析/后缀数组算法", "后缀数组算法", 0.19),
        ("数据结构/后缀自动机与后缀数组", "后缀自动机与后缀数组", 0.18),
        ("数据结构/KMP算法", "KMP算法", 0.17),
        ("数据结构/KMP字符串匹配", "KMP字符串匹配算法", 0.16),
    ]
    grep_results = {
        "priority": [
            ("算法设计与分析/KMP算法", "KMP字符串匹配算法", 1.0, ["KMP"]),
        ],
        "normal": [],
    }

    result, diagnostics = retriever._protect_strong_grep_evidence_with_diagnostics(
        fused,
        grep_results,
        "COMPARISON",
    )

    assert result == fused
    assert diagnostics["insertedSlugs"] == []


def test_strong_grep_evidence_keeps_broader_concept_when_algorithm_variant_is_top5() -> None:
    retriever = HybridRetriever({})
    fused = [
        ("数据结构/最短路径-BellmanFord", "最短路径-Bellman-Ford算法", 0.2),
        ("数据结构/最小生成树-Prim", "最小生成树-Prim算法", 0.19),
        ("数据结构/最短路径-Dijkstra", "最短路径-Dijkstra算法", 0.18),
        ("程序设计/图算法", "图算法", 0.17),
        ("算法设计与分析/Dijkstra算法", "Dijkstra最短路径算法", 0.16),
    ]
    grep_results = {
        "priority": [
            ("算法设计与分析/最小生成树", "最小生成树", 1.0, ["最小生成树"]),
        ],
        "normal": [],
    }

    result, diagnostics = retriever._protect_strong_grep_evidence_with_diagnostics(
        fused,
        grep_results,
        "COMPARISON",
    )

    assert result[3][0] == "算法设计与分析/最小生成树"
    assert diagnostics["insertedSlugs"][0]["replacedSlug"] == "程序设计/图算法"


def test_explicit_graph_evidence_replaces_duplicate_tail() -> None:
    retriever = HybridRetriever({})
    query = "请比较「后缀数组与后缀树」与「后缀数组算法、KMP字符串匹配算法」的区别和联系。"
    fused = [
        ("算法设计与分析/后缀数组算法", "后缀数组算法", 0.2),
        ("数据结构/后缀数组与后缀树", "后缀数组与后缀树", 0.19),
        ("算法设计与分析/KMP算法", "KMP字符串匹配算法", 0.18),
        ("数据结构/KMP算法", "KMP算法", 0.17),
        ("算法设计与分析/KMP算法", "KMP字符串匹配算法", 0.16),
    ]
    graph_results = [
        ("数据结构/KMP字符串匹配", "KMP字符串匹配算法", 8.75),
        ("数据结构/KMP算法", "KMP算法", 6.0),
    ]

    result, diagnostics = retriever._protect_explicit_graph_evidence_with_diagnostics(
        fused,
        graph_results,
        query,
        "COMPARISON",
    )

    assert result[4][0] == "数据结构/KMP字符串匹配"
    assert "数据结构/KMP字符串匹配" in [item[0] for item in result[:5]]
    assert diagnostics["insertedSlugs"][0]["reason"] == "replace_duplicate_tail"


def test_explicit_graph_evidence_replaces_cross_course_tail() -> None:
    retriever = HybridRetriever({})
    query = (
        "请从知识图谱关系角度说明「光照模型（Lambert/Phong/Blinn-Phong）」与"
        "「着色频率（Flat/Gouraud/Phong Shading）、PBR材质系统」之间的多跳联系。"
    )
    fused = [
        ("计算机图形学/光照模型（Lambert/Phong/Blinn-Phong）", "光照模型", 0.2),
        ("计算机图形学/着色频率（Flat/Gouraud/Phong Shading）", "着色频率", 0.19),
        ("databases/innodb-architecture", "InnoDB存储引擎架构", 0.18),
        ("计算机图形学/PBR物理渲染原理", "PBR物理渲染原理", 0.17),
        ("数据库原理/码", "码", 0.16),
    ]
    graph_results = [
        ("计算机图形学/PBR物理渲染原理", "PBR物理渲染原理", 9.0),
        ("计算机图形学/PBR材质系统", "PBR材质系统", 8.0),
    ]

    result, diagnostics = retriever._protect_explicit_graph_evidence_with_diagnostics(
        fused,
        graph_results,
        query,
        "CROSS_LAYER_RELATION",
    )

    assert result[4][0] == "计算机图形学/PBR材质系统"
    assert "数据库原理/码" not in [item[0] for item in result[:5]]
    assert diagnostics["insertedSlugs"][0]["reason"] == "replace_cross_course_tail"


def test_explicit_graph_evidence_skips_when_top5_already_represents_it() -> None:
    retriever = HybridRetriever({})
    query = "请构建一条学习路径，说明「DFA最小化（Hopcroft算法）」如何依赖或通向「NFA、词法分析与自动机」。"
    fused = [
        ("编译原理/DFA最小化", "DFA最小化", 0.2),
        ("编译原理/词法分析与DFA", "词法分析与自动机", 0.19),
        ("编译原理/NFA", "NFA", 0.18),
        ("编译原理/NFA转DFA", "NFA转DFA", 0.17),
        ("离散数学/自动机基础", "自动机基础", 0.16),
    ]
    graph_results = [
        ("编译原理/DFA最小化算法", "DFA最小化（Hopcroft算法）", 30.0),
    ]

    result, diagnostics = retriever._protect_explicit_graph_evidence_with_diagnostics(
        fused,
        graph_results,
        query,
        "PREREQUISITE_PATH",
    )

    assert result == fused
    assert diagnostics["insertedSlugs"] == []


def test_strong_grep_top3_protection_remains_graph_only() -> None:
    retriever = HybridRetriever({})
    fused = [
        ("vector-a", "Vector A", 0.2),
        ("doc-a", "Doc A", 0.19),
        ("doc-b", "Doc B", 0.18),
    ]
    grep_results = {"priority": [("doc-a", "Doc A", 1.0, ["a"])], "normal": []}

    result, diagnostics = retriever._protect_strong_grep_top3_with_diagnostics(
        fused,
        grep_results,
        None,
    )

    assert result == fused
    assert diagnostics["promotedSlugs"] == []


def test_graph_intent_forces_grep_first_to_keep_graph_channel() -> None:
    class FakeGrep:
        def __init__(self) -> None:
            self.calls = 0

        def search(self, cur, query, domain):
            del cur, query, domain
            self.calls += 1
            return {"priority": [("seed-doc", "Seed Doc", 0.95, ["seed"])], "normal": []}

    class FakeVector:
        def search(self, cur, query, top_k, domain):
            del cur, query, top_k, domain
            return [("vector-doc", "Vector Doc", 0.9)]

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

    fake_grep = FakeGrep()
    fake_graph = FakeGraph()
    retriever = HybridRetriever({}, top_k=3, graph_seed_n=1)
    retriever._initialized = True
    retriever._grep = fake_grep
    retriever._vector = FakeVector()
    retriever._graph = fake_graph
    retriever._web = FakeWeb()
    retriever._rrf = RRFFusion()

    plain_result = retriever.retrieve_grep_first(object(), "seed query")
    graph_result = retriever.retrieve_grep_first(
        object(),
        "seed query",
        graph_intent="COMPARISON",
    )

    assert plain_result["channels"]["graph"] == []
    assert graph_result["channels"]["graph"] == [("graph-doc", "Graph Doc", 10)]
    assert graph_result["grepFirstPromoted"] is True
    assert fake_grep.calls == 2
    assert fake_graph.calls[0]["top_n"] == 5
    assert fake_graph.calls[0]["graph_intent"] == "COMPARISON"
    assert graph_result["top"][:2] == [("vector-doc", "Vector Doc", 0.082), ("seed-doc", "Seed Doc", 0.0738)]


def test_graph_seed_slugs_canonicalize_wiki_resource_slugs_only_for_graph_intents() -> None:
    retriever = HybridRetriever({}, graph_seed_n=4)
    grep_results = {
        "priority": [
            ("None", "Low Value", 1.0, []),
            ("seed-doc", "Seed Doc", 1.0, []),
        ]
    }
    vector_results = [
        ("wiki://course/wiki-doc", "Wiki Doc", 0.9),
        ("https://example.test/ref", "External Ref", 0.8),
        ("video/course/ref", "Video Ref", 0.7),
    ]

    graph_seeds = retriever._graph_seed_slugs(grep_results, vector_results, "COMPARISON")
    plain_seeds = retriever._graph_seed_slugs(grep_results, vector_results, None)

    assert graph_seeds == ["seed-doc", "course/wiki-doc"]
    assert plain_seeds == ["None", "seed-doc", "wiki://course/wiki-doc", "https://example.test/ref"]


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


def test_graph_benchmark_evidence_matches_canonical_slug_and_title_alias() -> None:
    question = {
        "graphIntent": "COMPARISON",
        "expectedSlug": "wiki://Course/Type-System",
        "expectedRelatedSlugs": ["wiki://Course/Type-System"],
    }
    candidates = [
        {"rank": 1, "slug": "Go语言/接口与类型系统", "title": '"Go语言-接口与类型系统"', "score": 1.0},
        {"rank": 2, "slug": "course/type system", "title": "Type System", "score": 0.9},
    ]

    metrics = evaluate_graph_evidence(question, candidates)

    assert metrics["primaryTop5"] is True
    assert metrics["completeEvidenceTop5"] is True
    assert metrics["evidenceNodeRecallTop5"] == 1.0
    assert metrics["missingEvidenceSlugsTop5"] == []


def test_graph_benchmark_evidence_matches_competing_resource_slug_labels() -> None:
    question = {
        "graphIntent": "CROSS_LAYER_RELATION",
        "expectedSlug": "数据结构/关键路径",
        "expectedRelatedSlugs": ["数据结构/最小生成树MST"],
    }
    candidates = [
        {"rank": 1, "slug": "数据结构/关键路径", "title": "关键路径-AOE网", "score": 1.0},
        {"rank": 2, "slug": "算法设计与分析/最小生成树", "title": "最小生成树", "score": 0.9},
    ]

    metrics = evaluate_graph_evidence(question, candidates)

    assert metrics["completeEvidenceTop5"] is True
    assert metrics["missingEvidenceSlugsTop5"] == []


def test_graph_benchmark_evidence_does_not_match_broad_tail_substrings() -> None:
    question = {
        "graphIntent": "COMPARISON",
        "expectedSlug": "程序设计/类型系统",
        "expectedRelatedSlugs": [],
    }
    candidates = [
        {"rank": 1, "slug": "Go语言/接口与类型系统", "title": '"Go语言-接口与类型系统"', "score": 1.0},
    ]

    metrics = evaluate_graph_evidence(question, candidates)

    assert metrics["primaryTop5"] is False
    assert metrics["completeEvidenceTop5"] is False
    assert metrics["missingEvidenceSlugsTop5"] == ["程序设计/类型系统"]


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


def test_graph_benchmark_summary_quality_gates() -> None:
    summary = {
        "hitAt3Pct": 93.0,
        "avgLatencyMs": 1000.0,
        "p95LatencyMs": 2000.0,
        "channelErrorCount": 0,
    }
    graph_summary = {
        "primaryTop5Pct": 95.0,
        "completeEvidenceTop5Pct": 60.0,
        "evidenceNodeRecallTop5Pct": 85.0,
    }

    gates = summarize_graph_quality_gates(summary, graph_summary)
    failed = summarize_graph_quality_gates({**summary, "hitAt3Pct": 92.0}, graph_summary)
    failed_latency = summarize_graph_quality_gates(
        summary,
        graph_summary,
        thresholds={"avgLatencyMs": 900.0, "p95LatencyMs": 1500.0},
    )

    assert gates["passHitAt3"] is True
    assert gates["passLatency"] is True
    assert gates["passEvidenceRecall"] is True
    assert gates["passCompleteEvidence"] is True
    assert gates["overallPass"] is True
    assert failed["passHitAt3"] is False
    assert failed["overallPass"] is False
    assert failed_latency["passLatency"] is False
    assert failed_latency["overallPass"] is False
    assert _failed_quality_gate_names(failed_latency) == ["passLatency"]
    assert failed_latency["thresholds"]["avgLatencyMs"] == 900.0


def test_graph_benchmark_cli_fails_when_quality_gate_fails(monkeypatch, tmp_path) -> None:
    async def fake_benchmark_graph_questions(**_kwargs):
        return {
            "summary": {"overallPass": False, "passLatency": False},
            "graphSummary": {},
            "byGraphIntent": {},
            "intentMismatchSummary": {},
            "lowValueSourceSummary": {},
            "aliasDiagnosticSummary": {},
            "focusedLowCaseDiagnostics": {},
            "lowEvidenceRecords": [],
            "lowEvidenceRecordsByIntent": {},
        }

    monkeypatch.setattr(graph_benchmark, "benchmark_graph_questions", fake_benchmark_graph_questions)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "benchmark_graph_rag_100.py",
            "--output",
            str(tmp_path / "graph_report.json"),
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        graph_benchmark.main()

    assert "passLatency" in str(exc_info.value)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "benchmark_graph_rag_100.py",
            "--output",
            str(tmp_path / "graph_report.json"),
            "--no-fail-on-gate",
        ],
    )
    graph_benchmark.main()


def test_graph_benchmark_cli_passes_judge_retry_options(monkeypatch, tmp_path) -> None:
    captured = {}

    async def fake_benchmark_graph_questions(**kwargs):
        captured.update(kwargs)
        return {
            "summary": {"overallPass": True},
            "graphSummary": {},
            "byGraphIntent": {},
            "intentMismatchSummary": {},
            "lowValueSourceSummary": {},
            "aliasDiagnosticSummary": {},
            "focusedLowCaseDiagnostics": {},
            "lowEvidenceRecords": [],
            "lowEvidenceRecordsByIntent": {},
        }

    monkeypatch.setattr(graph_benchmark, "benchmark_graph_questions", fake_benchmark_graph_questions)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "benchmark_graph_rag_100.py",
            "--output",
            str(tmp_path / "graph_report.json"),
            "--judge-max-attempts",
            "7",
            "--judge-retry-base-seconds",
            "0.5",
        ],
    )

    graph_benchmark.main()

    assert captured["judge_max_attempts"] == 7
    assert captured["judge_retry_base_seconds"] == 0.5


def test_graph_benchmark_cli_passes_classifier_intent_mode(monkeypatch, tmp_path) -> None:
    captured = {}

    async def fake_benchmark_graph_questions(**kwargs):
        captured.update(kwargs)
        return {
            "summary": {"overallPass": True},
            "graphSummary": {},
            "byGraphIntent": {},
            "intentMismatchSummary": {},
            "lowValueSourceSummary": {},
            "aliasDiagnosticSummary": {},
            "focusedLowCaseDiagnostics": {},
            "lowEvidenceRecords": [],
            "lowEvidenceRecordsByIntent": {},
        }

    monkeypatch.setattr(graph_benchmark, "benchmark_graph_questions", fake_benchmark_graph_questions)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "benchmark_graph_rag_100.py",
            "--intent-mode",
            "classifier",
            "--output",
            str(tmp_path / "graph_report.json"),
        ],
    )

    graph_benchmark.main()

    assert captured["intent_mode"] == graph_benchmark.INTENT_MODE_CLASSIFIER


@pytest.mark.asyncio
async def test_graph_benchmark_classifier_strategy_reaches_retrieval(monkeypatch, tmp_path) -> None:
    captured = {}
    questions_path = tmp_path / "graph_questions.json"
    judge_cache_path = tmp_path / "judge_cache.json"
    output_path = tmp_path / "graph_report.json"
    questions_path.write_text(
        '{"seed": 1, "questionSetHash": "h", "questions": [{"id": "grq001", "question": "q", "graphIntent": "COMPARISON"}]}',
        encoding="utf-8",
    )

    class FakeJudge:
        pass

    class FakeClassification:
        graph_intent = "COMPARISON"
        retrieval_strategy = "LOCAL_GREP_FIRST"
        reason = "test"

    class FakeClassifier:
        def classify(self, params):
            del params
            return FakeClassification()

    async def fake_judge_with_retries(*_args, **_kwargs):
        return {"hitAt1": True, "hitAt3": True, "bestRank": 1, "relevanceScore": 1.0, "reason": "ok"}

    def fake_run_retrieval(question, **kwargs):
        captured["question"] = question
        captured["retrieval_strategy"] = kwargs.get("retrieval_strategy")
        return (
            {
                "top": [("course/a", "A", 1.0)],
                "diagnostics": {},
                "channelErrors": {},
            },
            {"init_ms": 0.0},
            1.0,
            None,
        )

    monkeypatch.setattr(graph_benchmark, "LLMRetrievalJudge", FakeJudge)
    monkeypatch.setattr(graph_benchmark, "QueryClassifier", FakeClassifier)
    monkeypatch.setattr(graph_benchmark, "_judge_with_retries", fake_judge_with_retries)
    monkeypatch.setattr(graph_benchmark, "run_retrieval", fake_run_retrieval)

    await graph_benchmark.benchmark_graph_questions(
        questions_path=questions_path,
        output_path=output_path,
        judge_cache_path=judge_cache_path,
        intent_mode=graph_benchmark.INTENT_MODE_CLASSIFIER,
        embedding_cache_path=None,
    )

    assert captured["retrieval_strategy"] == "LOCAL_GREP_FIRST"


def test_rag_benchmark_summary_quality_gates() -> None:
    summary = {
        "successRatePct": 100.0,
        "hitAt3Pct": 99.0,
        "avgLatencyMs": 1000.0,
        "p95LatencyMs": 2000.0,
        "channelErrorCount": 0,
    }

    gates = summarize_rag_quality_gates(summary)
    failed_success = summarize_rag_quality_gates({**summary, "successRatePct": 98.0})
    failed_latency = summarize_rag_quality_gates(
        summary,
        thresholds={"avgLatencyMs": 900.0, "p95LatencyMs": 1500.0},
    )

    assert gates["passHitAt3"] is True
    assert gates["passSuccessRate"] is True
    assert gates["passLatency"] is True
    assert gates["passChannelErrors"] is True
    assert gates["overallPass"] is True
    assert failed_success["passSuccessRate"] is False
    assert failed_success["overallPass"] is False
    assert failed_latency["passLatency"] is False
    assert _failed_rag_quality_gate_names(failed_latency) == ["passLatency"]


def test_rag_benchmark_cli_fails_when_quality_gate_fails(monkeypatch, tmp_path) -> None:
    async def fake_benchmark_questions(**_kwargs):
        return {
            "summary": {"overallPass": False, "passHitAt3": False},
        }

    monkeypatch.setattr(rag_benchmark, "benchmark_questions", fake_benchmark_questions)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "benchmark_rag_100.py",
            "--questions",
            str(tmp_path / "questions.json"),
            "--output",
            str(tmp_path / "rag_report.json"),
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        rag_benchmark.main()

    assert "passHitAt3" in str(exc_info.value)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "benchmark_rag_100.py",
            "--questions",
            str(tmp_path / "questions.json"),
            "--output",
            str(tmp_path / "rag_report.json"),
            "--no-fail-on-gate",
        ],
    )
    rag_benchmark.main()


def test_rag_benchmark_cli_threshold_overrides() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hit-at3-min-pct", type=float, default=99.0)
    parser.add_argument("--success-rate-min-pct", type=float, default=99.0)
    parser.add_argument("--avg-latency-max-ms", type=float, default=1651.99)
    parser.add_argument("--p95-latency-max-ms", type=float, default=4049.05)
    parser.add_argument("--channel-error-max", type=int, default=0)
    args = parser.parse_args(
        [
            "--hit-at3-min-pct",
            "90",
            "--success-rate-min-pct",
            "99",
            "--avg-latency-max-ms",
            "2500",
            "--p95-latency-max-ms",
            "9000",
            "--channel-error-max",
            "1",
        ]
    )

    thresholds = rag_quality_thresholds_from_args(args)

    assert thresholds == {
        "hitAt3Pct": 90.0,
        "successRatePct": 99.0,
        "avgLatencyMs": 2500.0,
        "p95LatencyMs": 9000.0,
        "channelErrorCount": 1,
    }


def test_rag_benchmark_default_cache_paths_are_project_scoped() -> None:
    assert rag_benchmark.DEFAULT_JUDGE_CACHE.is_absolute()
    assert rag_benchmark.DEFAULT_EMBEDDING_CACHE.is_absolute()
    assert rag_benchmark.DEFAULT_JUDGE_CACHE.parent.name == "reports"
    assert rag_benchmark.DEFAULT_JUDGE_CACHE.parent.parent.name == "python-agent"
    assert rag_benchmark.DEFAULT_EMBEDDING_CACHE.parent == rag_benchmark.DEFAULT_JUDGE_CACHE.parent


def test_rag_benchmark_run_retrieval_uses_production_grep_first(monkeypatch) -> None:
    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def cursor(self):
            return self

    class FakeRetriever:
        def __init__(self, db_config, top_k):
            del db_config, top_k
            self.called = None

        def initialize(self, cur):
            del cur

        def retrieve(self, cur, query, **kwargs):
            del cur, kwargs
            self.called = "retrieve"
            return {"query": query, "channels": {}, "top": [("hybrid", "Hybrid", 1.0)]}

        def retrieve_grep_first(self, cur, query, **kwargs):
            del cur
            self.called = "retrieve_grep_first"
            return {
                "query": query,
                "graphIntent": kwargs.get("graph_intent"),
                "retrievalStrategy": "LOCAL_GREP_FIRST",
                "channels": {"grep": {"priority": [("grep", "Grep", 1.0)]}, "vector": [], "graph": []},
                "top": [("grep", "Grep", 1.0)],
            }

    monkeypatch.setattr(rag_benchmark.psycopg2, "connect", lambda **_kwargs: FakeConnection())
    monkeypatch.setattr(rag_benchmark, "HybridRetriever", FakeRetriever)

    result, timings, _total_ms, error = rag_benchmark.run_retrieval(
        "query",
        retrieval_strategy="LOCAL_GREP_FIRST",
        graph_intent="COMPARISON",
    )

    assert error is None
    assert result["retrievalStrategy"] == "LOCAL_GREP_FIRST"
    assert result["top"][0][0] == "grep"
    assert timings["init_ms"] >= 0.0


@pytest.mark.asyncio
async def test_rag_benchmark_judge_retries_retryable_errors(monkeypatch) -> None:
    class FlakyJudge:
        def __init__(self) -> None:
            self.calls = 0

        async def judge(self, question_item, candidates):
            del question_item, candidates
            self.calls += 1
            if self.calls == 1:
                raise LLMServiceError(code="LLM_RATE_LIMITED", message="rate limited", retryable=True)
            return {"hitAt3": True}

    async def fake_sleep(_seconds):
        return None

    judge = FlakyJudge()
    monkeypatch.setattr(rag_benchmark.asyncio, "sleep", fake_sleep)

    result = await _judge_with_retries(judge, {}, [], max_attempts=2)

    assert result == {"hitAt3": True}
    assert judge.calls == 2


@pytest.mark.asyncio
async def test_rag_benchmark_judge_does_not_retry_non_retryable_errors(monkeypatch) -> None:
    class FailingJudge:
        def __init__(self) -> None:
            self.calls = 0

        async def judge(self, question_item, candidates):
            del question_item, candidates
            self.calls += 1
            raise LLMServiceError(code="LLM_AUTH_INVALID", message="auth", retryable=False)

    async def fake_sleep(_seconds):
        raise AssertionError("sleep should not be called")

    judge = FailingJudge()
    monkeypatch.setattr(rag_benchmark.asyncio, "sleep", fake_sleep)

    with pytest.raises(LLMServiceError):
        await _judge_with_retries(judge, {}, [], max_attempts=3)

    assert judge.calls == 1


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


def test_graph_benchmark_low_evidence_reason_candidates() -> None:
    alias_record = {
        "id": "grq001",
        "graphIntent": "COMPARISON",
        "classifierGraphIntent": "COMPARISON",
        "top": [{"slug": "course/near-match"}],
        "graphMetrics": {"missingEvidenceSlugsTop5": ["course/target"]},
        "aliasDiagnostics": {"course/target": {"likelyFalseNegative": True}},
        "diagnostics": {"channelsTopN": {}, "graphCandidateExplainTop50": {"candidates": [], "seedSlugs": []}},
    }
    edge_record = {
        "id": "grq002",
        "graphIntent": "PREREQUISITE_PATH",
        "classifierGraphIntent": "PREREQUISITE_PATH",
        "top": [{"slug": "course/other"}],
        "graphMetrics": {"missingEvidenceSlugsTop5": ["course/missing"]},
        "diagnostics": {"channelsTopN": {}, "graphCandidateExplainTop50": {"candidates": [], "seedSlugs": []}},
    }
    resource_record = {
        "id": "grq003",
        "graphIntent": "CROSS_LAYER_RELATION",
        "classifierGraphIntent": "MULTI_HOP_RELATION",
        "top": [{"slug": "course/other"}],
        "graphMetrics": {"missingEvidenceSlugsTop5": ["course/target"]},
        "diagnostics": {
            "channelsTopN": {"vector": [{"slug": "wiki://course/target", "title": "External"}]},
            "graphCandidateExplainTop50": {"candidates": [{"slug": "course/target"}], "seedSlugs": []},
        },
    }

    assert classify_low_evidence_reasons(alias_record)["missingAlias"] is True
    assert classify_low_evidence_reasons(edge_record)["missingGraphEdge"] is True
    resource_reasons = classify_low_evidence_reasons(resource_record)
    assert resource_reasons["resourceSlugCompeting"] is True
    assert resource_reasons["classifierMismatch"] is True


def test_graph_benchmark_low_evidence_groups_by_intent() -> None:
    records = [
        {
            "id": "grq001",
            "graphIntent": "COMPARISON",
            "classifierGraphIntent": "COMPARISON",
            "retrievalGraphIntent": "COMPARISON",
            "top": [{"slug": "doc-a"}],
            "graphMetrics": {
                "completeEvidenceTop5": False,
                "evidenceNodeRecallTop5": 0.5,
                "missingEvidenceSlugsTop5": ["doc-b"],
            },
            "diagnostics": {},
        },
        {
            "id": "grq002",
            "graphIntent": "PREREQUISITE_PATH",
            "classifierGraphIntent": "PREREQUISITE_PATH",
            "retrievalGraphIntent": "PREREQUISITE_PATH",
            "top": [{"slug": "doc-c"}],
            "graphMetrics": {
                "completeEvidenceTop5": True,
                "evidenceNodeRecallTop5": 1.0,
                "missingEvidenceSlugsTop5": [],
            },
            "diagnostics": {},
        },
    ]

    grouped = summarize_low_evidence_by_intent(records)

    assert list(grouped) == ["COMPARISON"]
    assert grouped["COMPARISON"][0]["reasonCandidates"]["missingGraphEdge"] is True


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


def test_benchmark_summary_lifts_channel_errors_to_top_level() -> None:
    records = [
        {
            "id": "grq001",
            "diagnostics": {
                "channelErrors": {
                    "vector": "ProxyError: temporary disconnect",
                    "graphExplain": "TimeoutError: slow explain",
                }
            },
        },
        {"id": "grq002", "channelErrors": {"grep": "RuntimeError: failed"}},
    ]

    summary = summarize_channel_errors(records)

    assert summary["channelErrorCount"] == 3
    assert summary["channelErrorQuestions"] == ["grq001", "grq002"]
    assert summary["channelErrorByChannel"] == {"graphExplain": 1, "grep": 1, "vector": 1}


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
