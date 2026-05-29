from retrieval.rrf_fusion import RRFFusion
from retrieval.slug_canonicalizer import canonicalize_slug, safe_slug_key


def test_rrf_slug_penalty_does_not_demote_opt_in_web_results() -> None:
    fusion = RRFFusion(k=0, grep_weight=1.0, vector_weight=1.0, graph_weight=1.0, web_weight=10.0)

    results = fusion.fuse(
        grep_results={"priority": [], "normal": []},
        vector_results=[("local-doc", "Local Doc", 0.9)],
        graph_results=[],
        web_results=[("https://example.com/current", "Fresh Web", 1.0)],
        slug_penalty=lambda slug: 0.01 if slug.startswith("http") else 1.0,
        top_n=2,
    )

    assert results[0][0] == "https://example.com/current"


def test_rrf_fusion_keeps_grep_matches_in_ranked_results() -> None:
    fusion = RRFFusion()

    results = fusion.fuse(
        grep_results={
            "priority": [("程序设计/并发编程", "并发编程", 0.95, ["并发编程"])],
            "normal": [],
        },
        vector_results=[],
        graph_results=[],
        top_n=3,
    )

    assert results
    assert results[0][0] == "程序设计/并发编程"


def test_rrf_fusion_dedupes_canonical_slug_variants_without_changing_display_slug() -> None:
    fusion = RRFFusion(k=0, grep_weight=1.0, vector_weight=1.0)

    results = fusion.fuse(
        grep_results={"priority": [("wiki://Course/Type System", "Type System", 1.0, [])], "normal": []},
        vector_results=[("course/type-system", "Type System Variant", 1.0)],
        graph_results=[],
        slug_key=canonicalize_slug,
        top_n=3,
    )

    assert len(results) == 1
    assert results[0][0] == "wiki://Course/Type System"


def test_rrf_fusion_safe_slug_key_does_not_merge_semantic_punctuation_variants() -> None:
    fusion = RRFFusion(k=0, grep_weight=1.0, vector_weight=1.0)

    results = fusion.fuse(
        grep_results={"priority": [("数据结构/最短路径-Dijkstra", "Dijkstra", 1.0, [])], "normal": []},
        vector_results=[("数据结构/最短路径Dijkstra", "Dijkstra Variant", 1.0)],
        graph_results=[],
        slug_key=safe_slug_key,
        top_n=3,
    )

    assert [item[0] for item in results] == ["数据结构/最短路径-Dijkstra", "数据结构/最短路径Dijkstra"]


def test_rrf_fusion_safe_slug_key_merges_wiki_prefix_and_case_only() -> None:
    fusion = RRFFusion(k=0, grep_weight=1.0, vector_weight=1.0)

    results = fusion.fuse(
        grep_results={"priority": [("wiki://Course/Type System", "Type System", 1.0, [])], "normal": []},
        vector_results=[("course/type system", "Type System Variant", 1.0)],
        graph_results=[],
        slug_key=safe_slug_key,
        top_n=3,
    )

    assert len(results) == 1
    assert results[0][0] == "wiki://Course/Type System"
