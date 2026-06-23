from src.ai_modules.memory.knowledge_graph_store import (
    LearnerKnowledgeGraphStore,
    _canonicalize,
)


class FakeCursor:
    def __init__(self, result_sets: list[list] | None = None) -> None:
        self.calls: list[tuple[str, tuple]] = []
        self.rowcount = 0
        self.result_sets = list(result_sets or [])

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def execute(self, sql: str, params: tuple) -> None:
        self.calls.append((sql, params))
        if "DELETE FROM app.learner_knowledge_node" in sql:
            keys = params[1] if len(params) > 1 else []
            self.rowcount = len(keys) if isinstance(keys, list) else 1
        elif "DELETE FROM app.learner_knowledge_edge" in sql:
            self.rowcount = 1
        else:
            self.rowcount = 0

    def fetchall(self) -> list:
        if not self.result_sets:
            return []
        return self.result_sets.pop(0)


class FakeConnection:
    def __init__(self, result_sets: list[list] | None = None) -> None:
        self.cursor_obj = FakeCursor(result_sets)
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def cursor(self):
        return self.cursor_obj

    def commit(self) -> None:
        self.committed = True


class FakePool:
    def __init__(self) -> None:
        self.connection = FakeConnection()
        self.get_count = 0
        self.put_count = 0
        self.closed = False

    def getconn(self):
        self.get_count += 1
        return self.connection

    def putconn(self, conn) -> None:
        assert conn is self.connection
        self.put_count += 1

    def closeall(self) -> None:
        self.closed = True


def test_canonicalize_merges_learning_stage_suffixes() -> None:
    assert _canonicalize("Go语言基础语法入门") == _canonicalize("Go语言基础语法")
    assert _canonicalize("Java并发编程基础巩固") == _canonicalize("Java并发编程")
    assert _canonicalize("Swift与iOS开发流程概览") == _canonicalize("Swift与iOS开发")


def test_upsert_node_keeps_best_mastery_on_conflict() -> None:
    connection = FakeConnection()

    class Store(LearnerKnowledgeGraphStore):
        def _get_conn(self):
            return connection

    Store()._upsert_node_sync(
        user_id="6ed05529-7f37-4601-865b-a942a6017c7a",
        canonical_key="Go语言基础语法入门",
        topic="Go语言基础语法入门",
        mastery_score=0.0,
        source="PROFILE",
    )

    assert connection.committed is True
    sql, params = connection.cursor_obj.calls[0]
    assert params[1] == "go语言基础语法"
    assert params[2] == "Go语言基础语法"
    assert "GREATEST(app.learner_knowledge_node.mastery_score, EXCLUDED.mastery_score)" in sql
    assert "WHEN GREATEST(app.learner_knowledge_node.mastery_score, EXCLUDED.mastery_score) >= 0.4 THEN 'IN_PROGRESS'" in sql


def test_get_conn_returns_connection_to_pool() -> None:
    pool = FakePool()
    store = LearnerKnowledgeGraphStore(db_config={"host": "db"})
    store._pool = pool

    with store._get_conn() as conn:
        assert conn is pool.connection

    assert pool.get_count == 1
    assert pool.put_count == 1

    store.close()

    assert pool.closed is True
    assert store._pool is None


def test_deduplicate_user_graph_merges_stage_nodes() -> None:
    rows = [
        ("go语言基础语法", "Go语言基础语法", 0.6, "PRACTICE"),
        ("go语言基础语法入门", "Go语言基础语法入门", 0.0, "PROFILE"),
    ]
    connection = FakeConnection([rows, []])

    class Store(LearnerKnowledgeGraphStore):
        def _get_conn(self):
            return connection

    result = Store()._deduplicate_user_graph_sync("6ed05529-7f37-4601-865b-a942a6017c7a")

    assert result["nodesMerged"] == 1
    assert result["edgesRewritten"] == 0
    assert connection.committed is True
    inserted_node = connection.cursor_obj.calls[1][1]
    assert inserted_node[1] == "go语言基础语法"
    assert inserted_node[2] == "Go语言基础语法"
    assert inserted_node[3] == 0.6
