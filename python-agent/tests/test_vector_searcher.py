from types import SimpleNamespace

import pytest

from retrieval import vector_searcher
from retrieval.vector_searcher import VectorSearcher
from src.ai_modules.config import get_settings


def test_vector_searcher_uses_configured_retry_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KNOWLEDGE_EMBEDDING_MAX_RETRIES", "2")
    monkeypatch.setenv("KNOWLEDGE_EMBEDDING_RETRY_BACKOFF_SECONDS", "0.25")
    monkeypatch.setenv("KNOWLEDGE_EMBEDDING_TIMEOUT_SECONDS", "7.5")
    get_settings.cache_clear()
    try:
        searcher = VectorSearcher()
    finally:
        get_settings.cache_clear()

    assert searcher.max_embedding_retries == 2
    assert searcher.embedding_retry_backoff_seconds == 0.25
    assert searcher.request_timeout == 7.5


def test_vector_searcher_retries_transient_embedding_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"count": 0}

    def flaky_call(**kwargs):
        calls["count"] += 1
        assert kwargs["model"] == "qwen3-vl-embedding"
        assert kwargs["dimension"] == 1024
        assert kwargs["request_timeout"] == 10.0
        if calls["count"] == 1:
            raise ConnectionError("temporary tls eof")
        return SimpleNamespace(
            status_code=200,
            output={"embeddings": [{"embedding": [0.1] * 1024}]},
        )

    monkeypatch.setattr(vector_searcher.MultiModalEmbedding, "call", flaky_call)
    monkeypatch.setattr(vector_searcher.time, "sleep", lambda _: None)
    searcher = VectorSearcher(
        dimension=1024,
        model="qwen3-vl-embedding",
        max_embedding_retries=2,
        embedding_retry_backoff_seconds=0,
    )

    embedding = searcher._embed("死锁")

    assert calls["count"] == 2
    assert len(embedding) == 1024


def test_vector_searcher_keeps_non_200_embedding_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        vector_searcher.MultiModalEmbedding,
        "call",
        lambda **kwargs: SimpleNamespace(status_code=401, code="InvalidApiKey", message="denied"),
    )
    searcher = VectorSearcher(max_embedding_retries=2, embedding_retry_backoff_seconds=0)

    with pytest.raises(RuntimeError, match="Embedding API error: InvalidApiKey denied"):
        searcher._embed("死锁")


def test_search_all_excludes_low_confidence_dropped_resources(monkeypatch: pytest.MonkeyPatch) -> None:
    executed = {}

    class FakeCursor:
        def execute(self, sql, params):
            executed["sql"] = sql
            executed["params"] = params

        def fetchall(self):
            return []

    monkeypatch.setattr(VectorSearcher, "_embed", lambda self, query: [0.1] * self.dimension)
    searcher = VectorSearcher(dimension=1024, model="fake-model")

    rows = searcher.search_all(FakeCursor(), "dynamic programming", top_k=5, domain="COMPUTER_SCIENCE")

    assert rows == []
    assert "wikiBindingStatus" in executed["sql"]
    assert "LOW_CONFIDENCE_DROPPED" in executed["sql"]
    assert executed["params"][-1] == 5
