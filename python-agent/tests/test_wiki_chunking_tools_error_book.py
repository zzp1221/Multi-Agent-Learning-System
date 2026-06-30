from pathlib import Path

import pytest

from knowledge.apply_rag_error_book_repairs import build_repair_payload, load_wiki_slug_index
from knowledge.build_wiki_community_summaries import build_community_summaries, load_wiki_pages, write_summary_file
from knowledge.vectorize_wiki import markdown_chunks_for_page
from knowledge.wiki_file_filter import iter_content_wiki_markdown
from knowledge.rag_repair_proposals import propose_error_records_from_report
from retrieval.error_book import build_error_record, load_error_records, record_error
from retrieval.wiki_tools import WikiToolset, graph_intent_allows_wiki_tools, wiki_search_terms
from src.ai_modules.agents.tutor_agent import TutorAgent
from src.ai_modules.agents.tutor_wiki_tools import tool_wiki_read as helper_tool_wiki_read
from src.ai_modules.runtime import PermissionLevel, ToolRegistry


def test_markdown_chunking_uses_headings_content_and_metadata() -> None:
    page = {
        "wiki_id": "page-1",
        "slug": "course/topic",
        "title": "Topic Title",
        "summary": "summary",
        "content": """
# Overview
This is the overview paragraph with enough content.

## Details
First detail paragraph.

Second detail paragraph.

## Examples
```python
print("hello")
```
""",
    }

    chunks = markdown_chunks_for_page(page, max_tokens=16)

    assert len(chunks) >= 3
    assert chunks[0]["metadata"]["wiki_page_id"] == "page-1"
    assert chunks[0]["metadata"]["slug"] == "course/topic"
    assert chunks[0]["metadata"]["section_path"] == ["Overview"]
    assert chunks[0]["metadata"]["chunk_index"] == 1
    assert chunks[0]["metadata"]["content_hash"]
    assert "Topic Title" in chunks[0]["embedding_text"]
    assert "Overview" in chunks[0]["embedding_text"]
    assert "This is the overview" in chunks[0]["embedding_text"]
    assert any(chunk["metadata"]["section_path"] == ["Overview", "Details"] for chunk in chunks)


def test_wiki_community_summary_builder_groups_course_metadata(tmp_path: Path) -> None:
    wiki_root = tmp_path / "wiki"
    course_dir = wiki_root / "CourseA"
    course_dir.mkdir(parents=True)
    (course_dir / "TopicA.md").write_text(
        """---
title: Topic A
course: Course A
chapter: Chapter 1
tags: [graph, retrieval]
aliases: [A]
---

## Definition

Topic A explains graph retrieval and links to [[Topic B]].
""",
        encoding="utf-8",
    )
    (course_dir / "TopicB.md").write_text(
        """---
title: Topic B
course: Course A
chapter: Chapter 2
tags: [retrieval, ranking]
---

Topic B explains ranking.
""",
        encoding="utf-8",
    )

    pages = load_wiki_pages(wiki_root)
    payload = build_community_summaries(pages, representative_limit=2)
    output = tmp_path / "summary.json"
    write_summary_file(output, payload)

    assert payload["pageCount"] == 2
    assert payload["communityCount"] == 1
    community = payload["communities"][0]
    assert community["course"] == "Course A"
    assert community["keyTags"][:2] == ["retrieval", "graph"]
    assert community["chapters"] == ["Chapter 1", "Chapter 2"]
    assert community["representativePages"][0]["slug"] == "CourseA/TopicA"
    assert "Course A contains 2 wiki pages" in community["summaryText"]
    assert output.read_text(encoding="utf-8").startswith("{")


def test_wiki_file_filter_excludes_llm_wiki_meta_files(tmp_path: Path) -> None:
    wiki_root = tmp_path / "wiki"
    course_dir = wiki_root / "CourseA"
    course_dir.mkdir(parents=True)
    (course_dir / "TopicA.md").write_text("---\ntitle: Topic A\n---\nA", encoding="utf-8")
    (wiki_root / "index.md").write_text("---\ntitle: Index\n---\nIndex", encoding="utf-8")
    (wiki_root / "schema.md").write_text("---\ntitle: Schema\n---\nSchema", encoding="utf-8")
    for dirname in ["raw", "maintenance", "templates", "assets"]:
        target_dir = wiki_root / dirname
        target_dir.mkdir(parents=True)
        (target_dir / "ignored.md").write_text("---\ntitle: Ignored\n---\nIgnored", encoding="utf-8")

    pages = load_wiki_pages(wiki_root)
    slugs = [page["slug"] for page in pages]

    assert [path.relative_to(wiki_root).as_posix() for path in iter_content_wiki_markdown(wiki_root)] == [
        "CourseA/TopicA.md"
    ]
    assert slugs == ["CourseA/TopicA"]


class FakeWikiCursor:
    def __init__(self) -> None:
        self.execute_count = 0
        self.wiki_page_query_count = 0
        self.rows = []
        self.one = None
        self.pages = {
            "seed": (
                "seed-id",
                "seed",
                "Seed Page",
                "Seed summary",
                "BASIC",
                '["Seed Alias"]',
                '["graph", "path"]',
                {"course": "CS"},
                "Seed body " * 900,
            ),
            "target": (
                "target-id",
                "target",
                "Target Page",
                "Target summary",
                "BASIC",
                '["Target Alias"]',
                '["graph"]',
                {},
                "Target body",
            ),
        }

    def execute(self, sql, params):
        self.execute_count += 1
        if "FROM rag.wiki_page" in sql and "ORDER BY score DESC" in sql:
            self.wiki_page_query_count += 1
            query = str(params[0]).lower()
            patterns = [str(value).strip("%").lower() for value in params[10]]
            self.rows = [
                (row[1], row[2], row[3], row[5], row[6], 70)
                for row in self.pages.values()
                if query in row[2].lower()
                or query in row[5].lower()
                or query in row[6].lower()
                or any(
                    pattern
                    and (
                        pattern in row[1].lower()
                        or pattern in row[2].lower()
                        or pattern in row[3].lower()
                        or pattern in row[5].lower()
                        or pattern in row[6].lower()
                    )
                    for pattern in patterns
                )
            ]
            return
        if "FROM rag.wiki_page" in sql and "slug = %s" in sql:
            self.wiki_page_query_count += 1
            slug = str(params[0])
            self.one = self.pages.get(slug) or next(
                (page for page in self.pages.values() if str(page[1]).lower() == slug.lower()),
                None,
            )
            return
        if "FROM rag.knowledge_chunk" in sql:
            chunks = [
                (1, "Seed chunk body", {"section_path": ["Overview"]}),
                (2, "Seed second chunk with target keyword", {"section_path": ["Details"]}),
                (3, "Seed third chunk", {"section_path": ["Tail"]}),
            ]
            if "query_score" in sql:
                patterns = [str(params[0]).strip("%").lower()] + [
                    str(value).strip("%").lower() for value in params[2]
                ]
                chunks = sorted(
                    chunks,
                    key=lambda row: (
                        -sum(
                            1
                            for pattern in patterns
                            if pattern
                            and (pattern in row[1].lower() or pattern in str(row[2]).lower())
                        ),
                        row[0],
                    ),
                )
                self.rows = chunks[: params[-1]]
                return
            self.rows = [
                chunks[0],
                chunks[1],
            ]
            return
        if "FROM rag.wiki_link l" in sql and "WHERE l.from_page_id::text" in sql:
            self.rows = [("target", "Target Page", "WIKILINK", 2.0)]
            return
        if "FROM rag.wiki_link l" in sql and "WHERE l.to_page_id::text" in sql:
            self.rows = [("target", "Target Page", "SHARED_TAG", 1.0)]
            return
        raise AssertionError(f"unexpected SQL: {sql}")

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.rows


def test_wiki_tools_search_read_and_neighbors() -> None:
    cur = FakeWikiCursor()
    tools = WikiToolset()

    search = tools.search(cur, "Seed Alias")
    read = tools.read(cur, "seed")
    neighbors = tools.neighbors(cur, "seed", relation_type="WIKILINK")

    assert search["results"][0]["slug"] == "seed"
    assert read["found"] is True
    assert read["page"]["title"] == "Seed Page"
    assert read["chunks"][0]["content"] == "Seed chunk body"
    assert read["outgoing"][0]["slug"] == "target"
    assert neighbors["incoming"][0]["relationType"] == "SHARED_TAG"
    assert "wiki_read_ms" in read["diagnostics"]
    assert "wiki_neighbors_ms" in neighbors["diagnostics"]


def test_wiki_search_terms_are_bounded_and_prefer_explicit_concepts() -> None:
    terms = wiki_search_terms(
        '请比较 "Seed Alias"、Target Page 与 graph path 的关系，并说明它们如何影响检索。'
    )

    assert terms[:2] == ["Seed Alias", "Target Page"]
    assert "graph" in terms
    assert len(terms) <= 6


def test_wiki_search_uses_term_fallback_for_long_natural_query() -> None:
    cur = FakeWikiCursor()
    tools = WikiToolset()

    search = tools.search(cur, "请比较 Seed Alias 和 Target Page 在 graph path 中的关系")

    slugs = [item["slug"] for item in search["results"]]
    assert "seed" in slugs
    assert "target" in slugs
    assert search["diagnostics"]["fallbackTerms"][:2] == ["Seed Alias", "Target Page"]


def test_wiki_tools_normalize_slug_and_bound_page_markdown() -> None:
    cur = FakeWikiCursor()
    tools = WikiToolset()

    read = tools.read(cur, " wiki://SEED ", chunk_limit=99, neighbor_limit=99)
    neighbors = tools.neighbors(cur, '"wiki://SEED"', relation_type="wikilink", limit=99)

    assert read["found"] is True
    assert read["slug"] == "seed"
    assert read["page"]["markdownTruncated"] is True
    assert len(read["page"]["markdown"]) <= 3020
    assert len(read["chunks"]) == 2
    assert neighbors["found"] is True
    assert neighbors["slug"] == "seed"


def test_wiki_read_can_prioritize_query_relevant_chunks() -> None:
    cur = FakeWikiCursor()
    tools = WikiToolset()

    read = tools.read(cur, "seed", query="target keyword", chunk_limit=2)

    assert read["found"] is True
    assert read["chunks"][0]["chunkNo"] == 2
    assert "target keyword" in read["chunks"][0]["content"]
    assert read["diagnostics"]["chunkQuery"] == "target keyword"


def test_wiki_read_without_query_keeps_chunk_order() -> None:
    cur = FakeWikiCursor()
    tools = WikiToolset()

    read = tools.read(cur, "seed", chunk_limit=2)

    assert [chunk["chunkNo"] for chunk in read["chunks"]] == [1, 2]
    assert read["diagnostics"]["chunkQuery"] == ""


def test_wiki_read_reuses_loaded_page_for_neighbors() -> None:
    cur = FakeWikiCursor()
    tools = WikiToolset()

    read = tools.read(cur, "seed", chunk_limit=2, neighbor_limit=3)

    assert read["found"] is True
    assert cur.wiki_page_query_count == 1
    assert cur.execute_count == 4


def test_wiki_tools_empty_inputs_short_circuit_without_db() -> None:
    cur = FakeWikiCursor()
    tools = WikiToolset()

    search = tools.search(cur, "   ")
    read = tools.read(cur, "wiki://")
    neighbors = tools.neighbors(cur, None)

    assert search["results"] == []
    assert read["found"] is False
    assert neighbors["outgoing"] == []
    assert cur.execute_count == 0


def test_wiki_toolset_public_empty_inputs_do_not_open_connection(monkeypatch) -> None:
    tools = WikiToolset({"dbname": "test"})
    monkeypatch.setattr(tools, "_with_connection", lambda fn: (_ for _ in ()).throw(AssertionError("db should not open")))

    search = tools.wiki_search(" ")
    read = tools.wiki_read("wiki://")
    neighbors = tools.wiki_neighbors("none")

    assert search["diagnostics"]["reason"] == "empty query"
    assert read["diagnostics"]["reason"] == "empty or invalid slug"
    assert neighbors["diagnostics"]["reason"] == "empty or invalid slug"


def test_wiki_toolset_public_read_passes_query_to_read(monkeypatch) -> None:
    captured = {}
    tools = WikiToolset({"dbname": "test"})

    def fake_with_connection(fn):
        class DummyCursor:
            pass

        return fn(DummyCursor())

    def fake_read(cur, slug, *, chunk_limit, neighbor_limit, query):
        del cur
        captured.update(
            {
                "slug": slug,
                "chunk_limit": chunk_limit,
                "neighbor_limit": neighbor_limit,
                "query": query,
            }
        )
        return {"slug": slug, "found": True}

    monkeypatch.setattr(tools, "_with_connection", fake_with_connection)
    monkeypatch.setattr(tools, "read", fake_read)

    result = tools.wiki_read("wiki://seed", chunk_limit=99, neighbor_limit=99, query="  target   keyword  ")

    assert result["found"] is True
    assert captured == {
        "slug": "seed",
        "chunk_limit": 5,
        "neighbor_limit": 12,
        "query": "target keyword",
    }


def test_wiki_tools_reject_invalid_relation_type_without_edge_queries() -> None:
    cur = FakeWikiCursor()
    tools = WikiToolset()

    result = tools.neighbors(cur, "seed", relation_type="DROP TABLE")

    assert result["found"] is True
    assert result["outgoing"] == []
    assert result["incoming"] == []
    assert result["diagnostics"]["reason"] == "invalid relationType"
    assert cur.execute_count == 1


def test_wiki_tools_are_limited_to_graph_intents() -> None:
    assert graph_intent_allows_wiki_tools("PREREQUISITE_PATH")
    assert graph_intent_allows_wiki_tools("MULTI_HOP_RELATION")
    assert graph_intent_allows_wiki_tools("COMPARISON")
    assert graph_intent_allows_wiki_tools("CROSS_LAYER_RELATION")
    assert not graph_intent_allows_wiki_tools(None)
    assert not graph_intent_allows_wiki_tools("COMMON_MISTAKE")


def test_tutor_wiki_tool_wrapper_is_disabled_for_plain_qa(monkeypatch) -> None:
    agent = TutorAgent(llm_client=object(), resource_intent_extractor=None)
    monkeypatch.setattr(agent, "_wiki_db_config", lambda: (_ for _ in ()).throw(AssertionError("db should not open")))

    result = agent._tool_wiki_search(tool_input={"query": "seed"}, params={"graphIntent": None})

    assert result["enabled"] is False


def test_tutor_wiki_tool_wrapper_empty_input_still_respects_plain_intent(monkeypatch) -> None:
    agent = TutorAgent(llm_client=object(), resource_intent_extractor=None)
    monkeypatch.setattr(agent, "_wiki_db_config", lambda: (_ for _ in ()).throw(AssertionError("db should not open")))

    result = agent._tool_wiki_read(tool_input={"slug": "wiki://"}, params={"graphIntent": None})

    assert result["enabled"] is False
    assert "graph-aware intents" in result["reason"]


def test_tutor_wiki_tools_are_not_registered_for_plain_intent() -> None:
    agent = TutorAgent(llm_client=object(), resource_intent_extractor=None)
    registry = ToolRegistry()

    agent._register_wiki_tools(tool_registry=registry, params={"graphIntent": None})

    assert registry.list_tool_schemas(PermissionLevel.READ_ONLY) == []


@pytest.mark.parametrize("graph_intent", ["PREREQUISITE_PATH", "MULTI_HOP_RELATION", "COMPARISON", "CROSS_LAYER_RELATION"])
def test_tutor_wiki_tools_are_registered_for_graph_intents(graph_intent: str) -> None:
    agent = TutorAgent(llm_client=object(), resource_intent_extractor=None)
    registry = ToolRegistry()

    agent._register_wiki_tools(tool_registry=registry, params={"graphIntent": graph_intent})

    tool_names = {
        schema["function"]["name"]
        for schema in registry.list_tool_schemas(PermissionLevel.READ_ONLY)
    }
    assert tool_names == {"wiki_search", "wiki_read", "wiki_neighbors"}


def test_tutor_wiki_read_schema_accepts_optional_query() -> None:
    agent = TutorAgent(llm_client=object(), resource_intent_extractor=None)
    registry = ToolRegistry()

    agent._register_wiki_tools(tool_registry=registry, params={"graphIntent": "COMPARISON"})

    schemas = registry.list_tool_schemas(PermissionLevel.READ_ONLY)
    wiki_read_schema = next(schema for schema in schemas if schema["function"]["name"] == "wiki_read")
    parameters = wiki_read_schema["function"]["parameters"]
    assert parameters["properties"]["query"] == {"type": "string"}
    assert parameters["required"] == ["slug"]


def test_tutor_wiki_tool_wrapper_limits_steps(monkeypatch) -> None:
    agent = TutorAgent(llm_client=object(), resource_intent_extractor=None)
    params = {"graphIntent": "COMPARISON", "wikiToolStepCount": 3}
    monkeypatch.setattr(agent, "_wiki_db_config", lambda: (_ for _ in ()).throw(AssertionError("db should not open")))

    result = agent._tool_wiki_neighbors(tool_input={"slug": "seed"}, params=params)

    assert result["enabled"] is False
    assert "3 steps" in result["reason"]


def test_tutor_wiki_tool_wrapper_empty_slug_does_not_open_db_or_claim_step(monkeypatch) -> None:
    agent = TutorAgent(llm_client=object(), resource_intent_extractor=None)
    params = {"graphIntent": "COMPARISON", "wikiToolStepCount": 0, "retrievalRawResult": {"graphDiagnostics": {}}}
    monkeypatch.setattr(agent, "_wiki_db_config", lambda: (_ for _ in ()).throw(AssertionError("db should not open")))

    result = agent._tool_wiki_read(tool_input={"slug": "wiki://"}, params=params)

    assert result["enabled"] is True
    assert result["found"] is False
    assert params["wikiToolStepCount"] == 0
    assert params["wikiToolCalls"][0]["hitCount"] == 0
    assert params["retrievalRawResult"]["graphDiagnostics"]["wikiTraversal"]["stepCount"] == 0


def test_tutor_wiki_tool_wrapper_normalizes_slug_before_call(monkeypatch) -> None:
    captured = {}

    class FakeWikiToolset:
        def __init__(self, db_config):
            del db_config

        def wiki_neighbors(self, slug, *, relation_type, limit):
            captured["slug"] = slug
            captured["relation_type"] = relation_type
            captured["limit"] = limit
            return {
                "slug": slug,
                "found": True,
                "incoming": [],
                "outgoing": [],
                "diagnostics": {"wiki_neighbors_ms": 1.0},
            }

    agent = TutorAgent(llm_client=object(), resource_intent_extractor=None)
    params = {"graphIntent": "COMPARISON"}
    monkeypatch.setattr("src.ai_modules.agents.tutor_agent.WikiToolset", FakeWikiToolset)
    monkeypatch.setattr(agent, "_wiki_db_config", lambda: {"dbname": "test"})

    result = agent._tool_wiki_neighbors(
        tool_input={"slug": " wiki://SEED ", "relationType": "wikilink", "limit": 99},
        params=params,
    )

    assert result["found"] is True
    assert captured == {"slug": "SEED", "relation_type": "WIKILINK", "limit": 12}
    assert params["wikiToolStepCount"] == 1


def test_tutor_wiki_read_passes_query_and_records_diagnostics(monkeypatch) -> None:
    captured = {}

    class FakeWikiToolset:
        def __init__(self, db_config):
            del db_config

        def wiki_read(self, slug, *, chunk_limit, query):
            captured.update({"slug": slug, "chunk_limit": chunk_limit, "query": query})
            return {
                "slug": slug,
                "found": True,
                "chunks": [{"content": "chunk"}],
                "incoming": [],
                "outgoing": [],
                "diagnostics": {"wiki_read_ms": 2.0, "chunkQuery": query},
            }

    agent = TutorAgent(llm_client=object(), resource_intent_extractor=None)
    params = {"graphIntent": "COMPARISON", "rewrittenQuery": "fallback query", "retrievalRawResult": {"graphDiagnostics": {}}}
    monkeypatch.setattr("src.ai_modules.agents.tutor_agent.WikiToolset", FakeWikiToolset)
    monkeypatch.setattr(agent, "_wiki_db_config", lambda: {"dbname": "test"})

    result = agent._tool_wiki_read(
        tool_input={"slug": "wiki://seed", "query": " target   keyword ", "chunkLimit": 99},
        params=params,
    )

    assert result["found"] is True
    assert captured == {"slug": "seed", "chunk_limit": 5, "query": "target keyword"}
    assert params["wikiToolCalls"][0]["query"] == "target keyword"
    assert params["retrievalRawResult"]["graphDiagnostics"]["wikiTraversal"]["calls"][0]["query"] == "target keyword"


def test_tutor_wiki_read_uses_rewritten_query_fallback(monkeypatch) -> None:
    captured = {}

    class FakeWikiToolset:
        def __init__(self, db_config):
            del db_config

        def wiki_read(self, slug, *, chunk_limit, query):
            del slug, chunk_limit
            captured["query"] = query
            return {"found": True, "chunks": [], "incoming": [], "outgoing": [], "diagnostics": {}}

    agent = TutorAgent(llm_client=object(), resource_intent_extractor=None)
    monkeypatch.setattr("src.ai_modules.agents.tutor_agent.WikiToolset", FakeWikiToolset)
    monkeypatch.setattr(agent, "_wiki_db_config", lambda: {"dbname": "test"})

    agent._tool_wiki_read(tool_input={"slug": "seed"}, params={"graphIntent": "COMPARISON", "rewrittenQuery": "fallback query"})

    assert captured["query"] == "fallback query"


def test_standalone_tutor_wiki_read_helper_passes_query(monkeypatch) -> None:
    captured = {}

    class FakeWikiToolset:
        def __init__(self, db_config):
            del db_config

        def wiki_read(self, slug, *, chunk_limit, query):
            captured.update({"slug": slug, "chunk_limit": chunk_limit, "query": query})
            return {"found": True, "chunks": [], "incoming": [], "outgoing": [], "diagnostics": {}}

    params = {"graphIntent": "COMPARISON", "query": "fallback query"}
    result = helper_tool_wiki_read(
        tool_input={"slug": "wiki://seed", "query": "target keyword", "chunkLimit": 9},
        params=params,
        wiki_toolset_cls=FakeWikiToolset,
        wiki_db_config=lambda: {"dbname": "test"},
        tools_enabled=lambda value: graph_intent_allows_wiki_tools(value.get("graphIntent")),
    )

    assert result["found"] is True
    assert captured == {"slug": "seed", "chunk_limit": 5, "query": "target keyword"}
    assert params["wikiToolCalls"][0]["query"] == "target keyword"


def test_tutor_wiki_tool_wrapper_invalid_relation_type_does_not_open_db_or_claim_step(monkeypatch) -> None:
    agent = TutorAgent(llm_client=object(), resource_intent_extractor=None)
    params = {"graphIntent": "COMPARISON", "wikiToolStepCount": 0}
    monkeypatch.setattr(agent, "_wiki_db_config", lambda: (_ for _ in ()).throw(AssertionError("db should not open")))

    result = agent._tool_wiki_neighbors(tool_input={"slug": "seed", "relationType": "bad relation"}, params=params)

    assert result["enabled"] is True
    assert result["outgoing"] == []
    assert result["diagnostics"]["reason"] == "invalid relationType"
    assert params["wikiToolStepCount"] == 0


def test_tutor_wiki_tool_wrapper_records_diagnostics(monkeypatch) -> None:
    class FakeWikiToolset:
        def __init__(self, db_config):
            del db_config

        def wiki_search(self, query, *, limit):
            return {
                "query": query,
                "results": [{"slug": "seed", "title": "Seed"}],
                "diagnostics": {"wiki_search_ms": 1.0},
            }

    agent = TutorAgent(llm_client=object(), resource_intent_extractor=None)
    params = {"graphIntent": "COMPARISON", "retrievalRawResult": {"graphDiagnostics": {}}}
    monkeypatch.setattr("src.ai_modules.agents.tutor_agent.WikiToolset", FakeWikiToolset)
    monkeypatch.setattr(agent, "_wiki_db_config", lambda: {"dbname": "test"})

    result = agent._tool_wiki_search(tool_input={"query": "seed"}, params=params)

    assert result["enabled"] is True
    assert params["wikiToolStepCount"] == 1
    assert params["wikiToolCalls"][0]["tool"] == "wiki_search"
    assert params["wikiToolCalls"][0]["hitCount"] == 1
    assert params["wikiTraversal"]["enabled"] is True
    assert params["wikiTraversal"]["stepCount"] == 1
    assert params["wikiTraversal"]["wiki_search_ms"] >= 0
    assert params["wikiTraversal"]["errors"] == []
    assert params["wikiTraversal"]["calls"] == [
        {
            "tool": "wiki_search",
            "query": "seed",
            "slug": None,
            "relationType": None,
            "enabled": True,
            "hitCount": 1,
        }
    ]
    assert params["retrievalRawResult"]["graphDiagnostics"]["wikiTraversal"]["stepCount"] == 1


def test_tutor_wiki_traversal_records_all_tool_summaries(monkeypatch) -> None:
    class FakeWikiToolset:
        def __init__(self, db_config):
            del db_config

        def wiki_search(self, query, *, limit):
            del limit
            return {
                "query": query,
                "results": [{"slug": "seed", "title": "Seed"}],
                "diagnostics": {"wiki_search_ms": 1.0},
            }

        def wiki_read(self, slug, *, chunk_limit, query):
            del chunk_limit, query
            return {
                "slug": slug,
                "found": True,
                "chunks": [{"content": "chunk"}],
                "incoming": [],
                "outgoing": [{"slug": "target"}],
                "diagnostics": {"wiki_read_ms": 2.0},
            }

        def wiki_neighbors(self, slug, *, relation_type, limit):
            del slug, relation_type, limit
            return {
                "found": True,
                "incoming": [{"slug": "source"}],
                "outgoing": [{"slug": "target"}],
                "diagnostics": {"wiki_neighbors_ms": 3.0},
            }

    agent = TutorAgent(llm_client=object(), resource_intent_extractor=None)
    params = {"graphIntent": "MULTI_HOP_RELATION", "retrievalRawResult": {"graphDiagnostics": {}}}
    monkeypatch.setattr("src.ai_modules.agents.tutor_agent.WikiToolset", FakeWikiToolset)
    monkeypatch.setattr(agent, "_wiki_db_config", lambda: {"dbname": "test"})

    agent._tool_wiki_search(tool_input={"query": "seed"}, params=params)
    agent._tool_wiki_read(tool_input={"slug": "seed"}, params=params)
    agent._tool_wiki_neighbors(tool_input={"slug": "seed"}, params=params)

    traversal = params["retrievalRawResult"]["graphDiagnostics"]["wikiTraversal"]
    assert traversal["stepCount"] == 3
    assert [call["tool"] for call in traversal["calls"]] == ["wiki_search", "wiki_read", "wiki_neighbors"]
    assert [call["hitCount"] for call in traversal["calls"]] == [1, 2, 2]


def test_error_book_builds_and_appends_structured_record(tmp_path: Path) -> None:
    path = tmp_path / "rag_error_book.yaml"

    record = record_error(
        path=path,
        query="alias query",
        expected={"slug": "seed"},
        top_results=[{"slug": "other", "rank": 1}],
        failure_type="missing_alias",
        root_cause="alias not indexed",
        constraint_rule="add alias before widening top-k",
    )

    text = path.read_text(encoding="utf-8")
    assert record["failure_type"] == "missing_alias"
    assert "errors:" in text
    assert "alias query" in text
    assert "add alias before widening top-k" in text
    loaded = load_error_records(path)
    assert loaded[0]["failure_type"] == "missing_alias"
    assert loaded[0]["expected"] == {"slug": "seed"}


def test_error_book_rejects_unknown_failure_type() -> None:
    with pytest.raises(ValueError):
        build_error_record(
            query="q",
            expected={},
            top_results=[],
            failure_type="unknown",
            root_cause="",
            constraint_rule="",
        )


def test_error_book_records_low_evidence_repair_proposals() -> None:
    report = {
        "lowEvidenceRecordsByIntent": {
            "CROSS_LAYER_RELATION": [
                {
                    "id": "grq030",
                    "question": "connect async runtime concepts",
                    "graphIntent": "CROSS_LAYER_RELATION",
                    "evidenceNodeRecallTop5": 0.3333,
                    "missingEvidenceSlugsTop5": ["python/generator", "javascript/promise"],
                    "topSlugs": ["python/context-manager"],
                    "graphSeedSlugs": ["python/context-manager"],
                    "reasonCandidates": {
                        "missingAlias": False,
                        "missingGraphEdge": True,
                        "resourceSlugCompeting": True,
                        "classifierMismatch": False,
                    },
                }
            ]
        }
    }

    records = propose_error_records_from_report(report, source_report="graph_report.json")

    assert len(records) == 1
    record = records[0]
    assert record["id"] == "rag-grq030-dangling_link"
    assert record["source_report"] == "graph_report.json"
    assert record["status"] == "proposed"
    assert record["failure_type"] == "dangling_link"
    assert record["proposal"]["type"] == "add_wikilink"
    assert record["proposal"]["candidateLinks"] == [
        ["python/context-manager", "python/generator"],
        ["python/context-manager", "javascript/promise"],
    ]
    assert all(left != right for left, right in record["proposal"]["candidateLinks"])


def test_error_book_proposals_enrich_low_evidence_from_full_records() -> None:
    report = {
        "lowEvidenceRecordsByIntent": {
            "COMPARISON": [
                {
                    "id": "grq089",
                    "graphIntent": "COMPARISON",
                    "missingEvidenceSlugsTop5": ["python/generator"],
                    "topSlugs": [],
                    "reasonCandidates": {
                        "missingGraphEdge": True,
                        "resourceSlugCompeting": True,
                    },
                }
            ]
        },
        "records": [
            {
                "id": "grq089",
                "question": "compare generator and thread pool",
                "top": [{"slug": "python/context-manager"}],
                "diagnostics": {"graphSeedSlugs": ["python/context-manager"]},
            }
        ],
    }

    records = propose_error_records_from_report(report, source_report="graph_report.json")

    record = records[0]
    assert record["query"] == "compare generator and thread pool"
    assert record["top_results"] == [{"slug": "python/context-manager", "rank": 1}]
    assert record["proposal"]["candidateLinks"] == [["python/context-manager", "python/generator"]]


def test_apply_rag_error_book_repairs_keeps_only_existing_wiki_slugs(tmp_path: Path) -> None:
    wiki_root = tmp_path / "wiki"
    (wiki_root / "course").mkdir(parents=True)
    (wiki_root / "course" / "A.md").write_text("---\ntitle: A\n---\nA", encoding="utf-8")
    (wiki_root / "course" / "B.md").write_text("---\ntitle: B\n---\nB", encoding="utf-8")
    (wiki_root / "index.md").write_text("---\ntitle: Index\n---\nIndex", encoding="utf-8")
    (wiki_root / "maintenance").mkdir()
    (wiki_root / "maintenance" / "C.md").write_text("---\ntitle: C\n---\nC", encoding="utf-8")
    wiki_index = load_wiki_slug_index(wiki_root)
    records = [
        {
            "id": "rag-grq001-dangling_link",
            "question_id": "grq001",
            "graph_intent": "COMPARISON",
            "proposal": {
                "type": "add_wikilink",
                "candidateLinks": [
                    ["course/A", "course/B"],
                    ["wiki://course/A", "course/B"],
                    ["course/A", "course/missing"],
                    ["course/A", "course/A"],
                ],
            },
        }
    ]

    payload = build_repair_payload(records, wiki_slug_index=wiki_index, source_report="book.yaml")

    assert "index" not in wiki_index
    assert "maintenance/c" not in wiki_index
    assert payload["sourceReport"] == "book.yaml"
    assert payload["repairs"] == [
        {
            "id": "grq001",
            "graphIntent": "COMPARISON",
            "links": [["course/A", "course/B", "WIKILINK"]],
        }
    ]
