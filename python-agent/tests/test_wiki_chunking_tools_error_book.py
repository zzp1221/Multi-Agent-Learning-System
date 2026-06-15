from pathlib import Path

import pytest

from knowledge.vectorize_wiki import markdown_chunks_for_page
from retrieval.error_book import build_error_record, record_error
from retrieval.wiki_tools import WikiToolset, graph_intent_allows_wiki_tools
from src.ai_modules.agents.tutor_agent import TutorAgent
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


class FakeWikiCursor:
    def __init__(self) -> None:
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
                "Seed body",
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
        if "FROM rag.wiki_page" in sql and "ORDER BY score DESC" in sql:
            query = str(params[0]).lower()
            self.rows = [
                (row[1], row[2], row[3], row[5], row[6], 70)
                for row in self.pages.values()
                if query in row[2].lower() or query in row[5].lower() or query in row[6].lower()
            ]
            return
        if "FROM rag.wiki_page" in sql and "WHERE slug = %s" in sql:
            self.one = self.pages.get(params[0])
            return
        if "FROM rag.knowledge_chunk" in sql:
            self.rows = [
                (1, "Seed chunk body", {"section_path": ["Overview"]}),
                (2, "Seed second chunk", {"section_path": ["Details"]}),
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


def test_wiki_tools_are_limited_to_graph_intents() -> None:
    assert graph_intent_allows_wiki_tools("PREREQUISITE_PATH")
    assert graph_intent_allows_wiki_tools("MULTI_HOP_RELATION")
    assert graph_intent_allows_wiki_tools("COMPARISON")
    assert not graph_intent_allows_wiki_tools(None)
    assert not graph_intent_allows_wiki_tools("COMMON_MISTAKE")


def test_tutor_wiki_tool_wrapper_is_disabled_for_plain_qa(monkeypatch) -> None:
    agent = TutorAgent(llm_client=object(), resource_intent_extractor=None)
    monkeypatch.setattr(agent, "_wiki_db_config", lambda: (_ for _ in ()).throw(AssertionError("db should not open")))

    result = agent._tool_wiki_search(tool_input={"query": "seed"}, params={"graphIntent": None})

    assert result["enabled"] is False


def test_tutor_wiki_tools_are_not_registered_for_plain_intent() -> None:
    agent = TutorAgent(llm_client=object(), resource_intent_extractor=None)
    registry = ToolRegistry()

    agent._register_wiki_tools(tool_registry=registry, params={"graphIntent": None})

    assert registry.list_tool_schemas(PermissionLevel.READ_ONLY) == []


@pytest.mark.parametrize("graph_intent", ["PREREQUISITE_PATH", "MULTI_HOP_RELATION", "COMPARISON"])
def test_tutor_wiki_tools_are_registered_for_graph_intents(graph_intent: str) -> None:
    agent = TutorAgent(llm_client=object(), resource_intent_extractor=None)
    registry = ToolRegistry()

    agent._register_wiki_tools(tool_registry=registry, params={"graphIntent": graph_intent})

    tool_names = {
        schema["function"]["name"]
        for schema in registry.list_tool_schemas(PermissionLevel.READ_ONLY)
    }
    assert tool_names == {"wiki_search", "wiki_read", "wiki_neighbors"}


def test_tutor_wiki_tool_wrapper_limits_steps(monkeypatch) -> None:
    agent = TutorAgent(llm_client=object(), resource_intent_extractor=None)
    params = {"graphIntent": "COMPARISON", "wikiToolStepCount": 3}
    monkeypatch.setattr(agent, "_wiki_db_config", lambda: (_ for _ in ()).throw(AssertionError("db should not open")))

    result = agent._tool_wiki_neighbors(tool_input={"slug": "seed"}, params=params)

    assert result["enabled"] is False
    assert "3 steps" in result["reason"]


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

        def wiki_read(self, slug, *, chunk_limit):
            del chunk_limit
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
