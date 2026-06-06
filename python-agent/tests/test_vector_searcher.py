from retrieval.vector_searcher import VectorSearcher


def test_search_all_filters_resource_chunks_by_domain(monkeypatch) -> None:
    class RecordingCursor:
        def __init__(self) -> None:
            self.sql = ""
            self.params = ()

        def execute(self, sql, params):
            self.sql = sql
            self.params = params

        def fetchall(self):
            return []

    cursor = RecordingCursor()
    monkeypatch.setattr(VectorSearcher, "_embed", lambda self, query: [0.1] * 1024)

    searcher = VectorSearcher(dimension=1024, model="test-model")
    searcher.search_all(cursor, "dynamic programming", top_k=5, domain="COMPUTER_SCIENCE")

    assert "FROM rag.resource_chunk rc" in cursor.sql
    assert "WHERE rc.domain = %s" in cursor.sql
    assert "JOIN app.learning_resource lr ON lr.id = rc.resource_id" in cursor.sql
    assert "lr.status = 'ACTIVE'" in cursor.sql
    assert "rc.access_scope::text = 'GLOBAL'" in cursor.sql
    assert cursor.params == (
        "[" + ",".join(["0.1"] * 1024) + "]",
        "COMPUTER_SCIENCE",
        "[" + ",".join(["0.1"] * 1024) + "]",
        "COMPUTER_SCIENCE",
        5,
    )
