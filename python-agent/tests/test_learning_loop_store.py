import pytest

from src.ai_modules.memory.learning_loop_store import (
    InMemoryLearningLoopStore,
    LearningLoopPersistenceError,
    PostgresLearningLoopStore,
    ResilientLearningLoopStore,
)


USER_ID = "00000000-0000-0000-0000-000000000001"


class _FakeCursor:
    def __init__(self, rows: list[tuple] | None = None) -> None:
        self.rows = list(rows or [])
        self.executed: list[tuple[str, tuple | None]] = []

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def execute(self, sql: str, params: tuple | None = None) -> None:
        self.executed.append((sql, params))

    def fetchone(self) -> tuple | None:
        if self.rows:
            return self.rows.pop(0)
        return None


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor) -> None:
        self.cursor_obj = cursor
        self.committed = False

    def __enter__(self) -> "_FakeConnection":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def cursor(self) -> _FakeCursor:
        return self.cursor_obj

    def commit(self) -> None:
        self.committed = True


def _store_with_cursor(cursor: _FakeCursor) -> tuple[PostgresLearningLoopStore, _FakeConnection]:
    connection = _FakeConnection(cursor)
    store = PostgresLearningLoopStore(
        db_config={},
        connect_fn=lambda **kwargs: connection,
    )
    return store, connection


def test_postgres_store_sets_rls_user_before_create_loop() -> None:
    cursor = _FakeCursor(rows=[("11111111-1111-1111-1111-111111111111", "ACTIVE", 1)])
    store, connection = _store_with_cursor(cursor)

    result = store._create_loop_sync(
        user_id=USER_ID,
        goal_text="learn indexes",
        course_id=None,
        task_id=None,
        conversation_id="conv-1",
        planning_level="goal_loop",
        loop_payload={},
    )

    assert result["persistence"] == "postgres"
    assert connection.committed is True
    assert cursor.executed[0] == ("SELECT set_config('app.user_id', %s, true)", (USER_ID,))


def test_postgres_store_raises_when_loop_update_matches_no_rows() -> None:
    cursor = _FakeCursor(rows=[])
    store, _ = _store_with_cursor(cursor)

    with pytest.raises(LearningLoopPersistenceError):
        store._update_loop_sync(
            user_id=USER_ID,
            loop_id="22222222-2222-2222-2222-222222222222",
            status="COMPLETED",
            current_subgoal_order=1,
            loop_payload={},
        )

    assert cursor.executed[0] == ("SELECT set_config('app.user_id', %s, true)", (USER_ID,))


@pytest.mark.asyncio
async def test_resilient_store_fallback_records_update_failure_reason() -> None:
    class _FailingPrimary:
        async def update_loop(self, **kwargs):
            raise LearningLoopPersistenceError("no rows")

    fallback = InMemoryLearningLoopStore()
    store = ResilientLearningLoopStore(primary=_FailingPrimary(), fallback=fallback)

    result = await store.update_loop(
        user_id=USER_ID,
        loop_id="33333333-3333-3333-3333-333333333333",
        status="COMPLETED",
    )

    assert result["persistence"] == "memory"
    assert "LearningLoopPersistenceError" in result["persistenceFallbackReason"]
