"""Persistence for autonomous learning loops and planning checkpoints."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any, Protocol
from uuid import uuid4

from src.ai_modules.config import get_settings

LOGGER = logging.getLogger(__name__)


class LearningLoopPersistenceError(RuntimeError):
    """Raised when PostgreSQL cannot persist a learning-loop state change."""


def _adapt_json_payload(payload: Any) -> Any:
    try:
        from psycopg2.extras import Json

        return Json(payload)
    except ModuleNotFoundError:
        return json.dumps(payload, ensure_ascii=False)


class LearningLoopStore(Protocol):
    """Persistence contract for goal-loop planning state."""

    async def create_loop(
        self,
        *,
        user_id: str,
        goal_text: str,
        course_id: str | None = None,
        task_id: str | None = None,
        conversation_id: str | None = None,
        planning_level: str = "goal_loop",
        loop_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    async def create_subgoals(
        self,
        *,
        loop_id: str,
        user_id: str,
        subgoals: list[dict[str, Any]],
    ) -> list[dict[str, Any]]: ...

    async def update_loop(
        self,
        *,
        loop_id: str,
        status: str,
        user_id: str | None = None,
        current_subgoal_order: int | None = None,
        loop_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    async def update_subgoal(
        self,
        *,
        subgoal_id: str,
        status: str,
        user_id: str | None = None,
        result_payload: dict[str, Any] | None = None,
        attempt_count: int | None = None,
    ) -> dict[str, Any]: ...

    async def record_checkpoint(
        self,
        *,
        user_id: str,
        checkpoint_type: str,
        trigger_reason: str,
        action: str,
        status: str = "RECORDED",
        loop_id: str | None = None,
        subgoal_id: str | None = None,
        before_payload: dict[str, Any] | None = None,
        after_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    async def record_replan(
        self,
        *,
        loop_id: str,
        user_id: str,
        reason: str,
        old_plan: dict[str, Any],
        new_plan: dict[str, Any],
        attempt_no: int,
        accepted: bool,
        subgoal_id: str | None = None,
    ) -> dict[str, Any]: ...


class InMemoryLearningLoopStore:
    """Fallback store used by tests and when PostgreSQL is unavailable."""

    def __init__(self) -> None:
        self.loops: dict[str, dict[str, Any]] = {}
        self.subgoals: dict[str, dict[str, Any]] = {}
        self.checkpoints: list[dict[str, Any]] = []
        self.replans: list[dict[str, Any]] = []

    async def create_loop(
        self,
        *,
        user_id: str,
        goal_text: str,
        course_id: str | None = None,
        task_id: str | None = None,
        conversation_id: str | None = None,
        planning_level: str = "goal_loop",
        loop_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        loop_id = str(uuid4())
        record = {
            "loopId": loop_id,
            "userId": user_id,
            "courseId": course_id,
            "taskId": task_id,
            "conversationId": conversation_id,
            "goalText": goal_text,
            "planningLevel": planning_level,
            "status": "ACTIVE",
            "currentSubgoalOrder": 1,
            "loop": loop_payload or {},
            "persistence": "memory",
        }
        self.loops[loop_id] = record
        return record

    async def create_subgoals(
        self,
        *,
        loop_id: str,
        user_id: str,
        subgoals: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for index, subgoal in enumerate(subgoals, start=1):
            subgoal_id = str(uuid4())
            record = {
                "subgoalId": subgoal_id,
                "loopId": loop_id,
                "userId": user_id,
                "orderIndex": int(subgoal.get("orderIndex") or index),
                "title": str(subgoal.get("title") or f"子目标 {index}"),
                "objective": str(subgoal.get("objective") or ""),
                "successCriteria": str(subgoal.get("successCriteria") or ""),
                "targetKnowledgePoints": list(subgoal.get("targetKnowledgePoints") or []),
                "preferredResourceTypes": list(subgoal.get("preferredResourceTypes") or []),
                "assignedPreset": str(subgoal.get("assignedPreset") or ""),
                "status": str(subgoal.get("status") or "PENDING"),
                "attemptCount": int(subgoal.get("attemptCount") or 0),
                "result": dict(subgoal.get("result") or {}),
                "persistence": "memory",
            }
            self.subgoals[subgoal_id] = record
            records.append(record)
        return records

    async def update_loop(
        self,
        *,
        loop_id: str,
        status: str,
        user_id: str | None = None,
        current_subgoal_order: int | None = None,
        loop_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        record = self.loops.setdefault(loop_id, {"loopId": loop_id, "persistence": "memory"})
        record["status"] = status
        if current_subgoal_order is not None:
            record["currentSubgoalOrder"] = current_subgoal_order
        if loop_payload is not None:
            record["loop"] = loop_payload
        return dict(record)

    async def update_subgoal(
        self,
        *,
        subgoal_id: str,
        status: str,
        user_id: str | None = None,
        result_payload: dict[str, Any] | None = None,
        attempt_count: int | None = None,
    ) -> dict[str, Any]:
        record = self.subgoals.setdefault(subgoal_id, {"subgoalId": subgoal_id, "persistence": "memory"})
        record["status"] = status
        if result_payload is not None:
            record["result"] = result_payload
        if attempt_count is not None:
            record["attemptCount"] = attempt_count
        return dict(record)

    async def record_checkpoint(
        self,
        *,
        user_id: str,
        checkpoint_type: str,
        trigger_reason: str,
        action: str,
        status: str = "RECORDED",
        loop_id: str | None = None,
        subgoal_id: str | None = None,
        before_payload: dict[str, Any] | None = None,
        after_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        record = {
            "checkpointId": str(uuid4()),
            "loopId": loop_id,
            "subgoalId": subgoal_id,
            "userId": user_id,
            "checkpointType": checkpoint_type,
            "triggerReason": trigger_reason,
            "action": action,
            "status": status,
            "before": before_payload or {},
            "after": after_payload or {},
            "persistence": "memory",
        }
        self.checkpoints.append(record)
        return record

    async def record_replan(
        self,
        *,
        loop_id: str,
        user_id: str,
        reason: str,
        old_plan: dict[str, Any],
        new_plan: dict[str, Any],
        attempt_no: int,
        accepted: bool,
        subgoal_id: str | None = None,
    ) -> dict[str, Any]:
        record = {
            "replanId": str(uuid4()),
            "loopId": loop_id,
            "subgoalId": subgoal_id,
            "userId": user_id,
            "reason": reason,
            "oldPlan": old_plan,
            "newPlan": new_plan,
            "attemptNo": attempt_no,
            "accepted": accepted,
            "persistence": "memory",
        }
        self.replans.append(record)
        return record


class PostgresLearningLoopStore:
    """PostgreSQL-backed autonomous learning-loop store."""

    def __init__(
        self,
        db_config: dict[str, Any] | None = None,
        connect_fn: Callable[..., Any] | None = None,
    ) -> None:
        settings = get_settings()
        self.db_config = db_config or {
            "host": settings.postgres_host,
            "port": settings.postgres_port,
            "dbname": settings.postgres_db,
            "user": settings.postgres_user,
            "password": settings.postgres_password,
        }
        self._connect_fn = connect_fn

    def _connect(self) -> Any:
        if self._connect_fn is not None:
            return self._connect_fn(**self.db_config)

        import psycopg2

        return psycopg2.connect(**self.db_config)

    @staticmethod
    def _set_rls_user(cur: Any, user_id: str) -> None:
        cur.execute("SELECT set_config('app.user_id', %s, true)", (user_id,))

    async def create_loop(
        self,
        *,
        user_id: str,
        goal_text: str,
        course_id: str | None = None,
        task_id: str | None = None,
        conversation_id: str | None = None,
        planning_level: str = "goal_loop",
        loop_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._create_loop_sync,
            user_id=user_id,
            goal_text=goal_text,
            course_id=course_id,
            task_id=task_id,
            conversation_id=conversation_id,
            planning_level=planning_level,
            loop_payload=loop_payload or {},
        )

    def _create_loop_sync(
        self,
        *,
        user_id: str,
        goal_text: str,
        course_id: str | None,
        task_id: str | None,
        conversation_id: str | None,
        planning_level: str,
        loop_payload: dict[str, Any],
    ) -> dict[str, Any]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                self._set_rls_user(cur, user_id)
                cur.execute(
                    """
                    INSERT INTO app.autonomous_learning_loop(
                        user_id, course_id, task_id, conversation_id,
                        goal_text, planning_level, loop_json
                    )
                    VALUES (%s::uuid, %s::uuid, %s::uuid, %s, %s, %s, %s)
                    RETURNING id, status, current_subgoal_order
                    """,
                    (
                        user_id,
                        course_id,
                        task_id,
                        conversation_id,
                        goal_text,
                        planning_level,
                        _adapt_json_payload(loop_payload),
                    ),
                )
                row = cur.fetchone()
            conn.commit()
        return {
            "loopId": str(row[0]),
            "userId": user_id,
            "courseId": course_id,
            "taskId": task_id,
            "conversationId": conversation_id,
            "goalText": goal_text,
            "planningLevel": planning_level,
            "status": str(row[1]),
            "currentSubgoalOrder": int(row[2]),
            "persistence": "postgres",
        }

    async def create_subgoals(
        self,
        *,
        loop_id: str,
        user_id: str,
        subgoals: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self._create_subgoals_sync,
            loop_id=loop_id,
            user_id=user_id,
            subgoals=subgoals,
        )

    def _create_subgoals_sync(
        self,
        *,
        loop_id: str,
        user_id: str,
        subgoals: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        with self._connect() as conn:
            with conn.cursor() as cur:
                self._set_rls_user(cur, user_id)
                for index, subgoal in enumerate(subgoals, start=1):
                    order_index = int(subgoal.get("orderIndex") or index)
                    cur.execute(
                        """
                        INSERT INTO app.autonomous_learning_subgoal(
                            loop_id, user_id, order_index, title, objective, success_criteria,
                            target_knowledge_points_json, preferred_resource_types_json,
                            assigned_preset, status, attempt_count, result_json
                        )
                        VALUES (%s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT(loop_id, order_index) DO UPDATE
                        SET title = EXCLUDED.title,
                            objective = EXCLUDED.objective,
                            success_criteria = EXCLUDED.success_criteria,
                            target_knowledge_points_json = EXCLUDED.target_knowledge_points_json,
                            preferred_resource_types_json = EXCLUDED.preferred_resource_types_json,
                            assigned_preset = EXCLUDED.assigned_preset,
                            updated_at = now()
                        RETURNING id, status, attempt_count
                        """,
                        (
                            loop_id,
                            user_id,
                            order_index,
                            str(subgoal.get("title") or f"子目标 {order_index}"),
                            str(subgoal.get("objective") or ""),
                            str(subgoal.get("successCriteria") or ""),
                            _adapt_json_payload(list(subgoal.get("targetKnowledgePoints") or [])),
                            _adapt_json_payload(list(subgoal.get("preferredResourceTypes") or [])),
                            str(subgoal.get("assignedPreset") or ""),
                            str(subgoal.get("status") or "PENDING"),
                            int(subgoal.get("attemptCount") or 0),
                            _adapt_json_payload(dict(subgoal.get("result") or {})),
                        ),
                    )
                    row = cur.fetchone()
                    records.append(
                        {
                            "subgoalId": str(row[0]),
                            "loopId": loop_id,
                            "userId": user_id,
                            "orderIndex": order_index,
                            "title": str(subgoal.get("title") or f"子目标 {order_index}"),
                            "objective": str(subgoal.get("objective") or ""),
                            "successCriteria": str(subgoal.get("successCriteria") or ""),
                            "targetKnowledgePoints": list(subgoal.get("targetKnowledgePoints") or []),
                            "preferredResourceTypes": list(subgoal.get("preferredResourceTypes") or []),
                            "assignedPreset": str(subgoal.get("assignedPreset") or ""),
                            "status": str(row[1]),
                            "attemptCount": int(row[2]),
                            "persistence": "postgres",
                        }
                    )
            conn.commit()
        return records

    async def update_loop(
        self,
        *,
        loop_id: str,
        status: str,
        user_id: str | None = None,
        current_subgoal_order: int | None = None,
        loop_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._update_loop_sync,
            user_id=user_id,
            loop_id=loop_id,
            status=status,
            current_subgoal_order=current_subgoal_order,
            loop_payload=loop_payload,
        )

    def _update_loop_sync(
        self,
        *,
        user_id: str | None,
        loop_id: str,
        status: str,
        current_subgoal_order: int | None,
        loop_payload: dict[str, Any] | None,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                if user_id:
                    self._set_rls_user(cur, user_id)
                cur.execute(
                    """
                    UPDATE app.autonomous_learning_loop
                    SET status = %s,
                        current_subgoal_order = COALESCE(%s, current_subgoal_order),
                        loop_json = COALESCE(%s, loop_json),
                        updated_at = now()
                    WHERE id = %s::uuid
                    RETURNING id, status, current_subgoal_order
                    """,
                    (
                        status,
                        current_subgoal_order,
                        _adapt_json_payload(loop_payload) if loop_payload is not None else None,
                        loop_id,
                    ),
                )
                row = cur.fetchone()
                if row is None:
                    raise LearningLoopPersistenceError(f"learning loop update matched no rows: {loop_id}")
            conn.commit()
        return {
            "loopId": str(row[0]),
            "status": str(row[1]),
            "currentSubgoalOrder": int(row[2]),
            "persistence": "postgres",
        }

    async def update_subgoal(
        self,
        *,
        subgoal_id: str,
        status: str,
        user_id: str | None = None,
        result_payload: dict[str, Any] | None = None,
        attempt_count: int | None = None,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._update_subgoal_sync,
            user_id=user_id,
            subgoal_id=subgoal_id,
            status=status,
            result_payload=result_payload,
            attempt_count=attempt_count,
        )

    def _update_subgoal_sync(
        self,
        *,
        user_id: str | None,
        subgoal_id: str,
        status: str,
        result_payload: dict[str, Any] | None,
        attempt_count: int | None,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                if user_id:
                    self._set_rls_user(cur, user_id)
                cur.execute(
                    """
                    UPDATE app.autonomous_learning_subgoal
                    SET status = %s,
                        result_json = COALESCE(%s, result_json),
                        attempt_count = COALESCE(%s, attempt_count),
                        updated_at = now()
                    WHERE id = %s::uuid
                    RETURNING id, loop_id, status, attempt_count
                    """,
                    (
                        status,
                        _adapt_json_payload(result_payload) if result_payload is not None else None,
                        attempt_count,
                        subgoal_id,
                    ),
                )
                row = cur.fetchone()
                if row is None:
                    raise LearningLoopPersistenceError(f"learning subgoal update matched no rows: {subgoal_id}")
            conn.commit()
        return {
            "subgoalId": str(row[0]),
            "loopId": str(row[1]),
            "status": str(row[2]),
            "attemptCount": int(row[3]),
            "persistence": "postgres",
        }

    async def record_checkpoint(
        self,
        *,
        user_id: str,
        checkpoint_type: str,
        trigger_reason: str,
        action: str,
        status: str = "RECORDED",
        loop_id: str | None = None,
        subgoal_id: str | None = None,
        before_payload: dict[str, Any] | None = None,
        after_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._record_checkpoint_sync,
            user_id=user_id,
            checkpoint_type=checkpoint_type,
            trigger_reason=trigger_reason,
            action=action,
            status=status,
            loop_id=loop_id,
            subgoal_id=subgoal_id,
            before_payload=before_payload or {},
            after_payload=after_payload or {},
        )

    def _record_checkpoint_sync(
        self,
        *,
        user_id: str,
        checkpoint_type: str,
        trigger_reason: str,
        action: str,
        status: str,
        loop_id: str | None,
        subgoal_id: str | None,
        before_payload: dict[str, Any],
        after_payload: dict[str, Any],
    ) -> dict[str, Any]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                self._set_rls_user(cur, user_id)
                cur.execute(
                    """
                    INSERT INTO app.autonomous_planning_checkpoint(
                        loop_id, subgoal_id, user_id, checkpoint_type, trigger_reason,
                        action, before_json, after_json, status
                    )
                    VALUES (%s::uuid, %s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        loop_id,
                        subgoal_id,
                        user_id,
                        checkpoint_type,
                        trigger_reason,
                        action,
                        _adapt_json_payload(before_payload),
                        _adapt_json_payload(after_payload),
                        status,
                    ),
                )
                checkpoint_id = str(cur.fetchone()[0])
            conn.commit()
        return {
            "checkpointId": checkpoint_id,
            "loopId": loop_id,
            "subgoalId": subgoal_id,
            "userId": user_id,
            "checkpointType": checkpoint_type,
            "triggerReason": trigger_reason,
            "action": action,
            "status": status,
            "before": before_payload,
            "after": after_payload,
            "persistence": "postgres",
        }

    async def record_replan(
        self,
        *,
        loop_id: str,
        user_id: str,
        reason: str,
        old_plan: dict[str, Any],
        new_plan: dict[str, Any],
        attempt_no: int,
        accepted: bool,
        subgoal_id: str | None = None,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._record_replan_sync,
            loop_id=loop_id,
            user_id=user_id,
            reason=reason,
            old_plan=old_plan,
            new_plan=new_plan,
            attempt_no=attempt_no,
            accepted=accepted,
            subgoal_id=subgoal_id,
        )

    def _record_replan_sync(
        self,
        *,
        loop_id: str,
        user_id: str,
        reason: str,
        old_plan: dict[str, Any],
        new_plan: dict[str, Any],
        attempt_no: int,
        accepted: bool,
        subgoal_id: str | None,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                self._set_rls_user(cur, user_id)
                cur.execute(
                    """
                    INSERT INTO app.autonomous_replan_event(
                        loop_id, subgoal_id, user_id, reason, old_plan_json,
                        new_plan_json, attempt_no, accepted
                    )
                    VALUES (%s::uuid, %s::uuid, %s::uuid, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        loop_id,
                        subgoal_id,
                        user_id,
                        reason,
                        _adapt_json_payload(old_plan),
                        _adapt_json_payload(new_plan),
                        attempt_no,
                        accepted,
                    ),
                )
                replan_id = str(cur.fetchone()[0])
            conn.commit()
        return {
            "replanId": replan_id,
            "loopId": loop_id,
            "subgoalId": subgoal_id,
            "userId": user_id,
            "reason": reason,
            "oldPlan": old_plan,
            "newPlan": new_plan,
            "attemptNo": attempt_no,
            "accepted": accepted,
            "persistence": "postgres",
        }


class ResilientLearningLoopStore:
    """Postgres-first store that never blocks the agent workflow on persistence errors."""

    def __init__(
        self,
        primary: LearningLoopStore | None = None,
        fallback: InMemoryLearningLoopStore | None = None,
    ) -> None:
        self.primary = primary or PostgresLearningLoopStore()
        self.fallback = fallback or InMemoryLearningLoopStore()

    async def create_loop(self, **kwargs: Any) -> dict[str, Any]:
        return await self._call_with_fallback("create_loop", **kwargs)

    async def create_subgoals(self, **kwargs: Any) -> list[dict[str, Any]]:
        result = await self._call_with_fallback("create_subgoals", **kwargs)
        return result if isinstance(result, list) else []

    async def update_loop(self, **kwargs: Any) -> dict[str, Any]:
        return await self._call_with_fallback("update_loop", **kwargs)

    async def update_subgoal(self, **kwargs: Any) -> dict[str, Any]:
        return await self._call_with_fallback("update_subgoal", **kwargs)

    async def record_checkpoint(self, **kwargs: Any) -> dict[str, Any]:
        return await self._call_with_fallback("record_checkpoint", **kwargs)

    async def record_replan(self, **kwargs: Any) -> dict[str, Any]:
        return await self._call_with_fallback("record_replan", **kwargs)

    async def _call_with_fallback(self, method_name: str, **kwargs: Any) -> Any:
        try:
            method = getattr(self.primary, method_name)
            return await method(**kwargs)
        except Exception as exc:
            LOGGER.warning("Learning loop store %s failed; using memory fallback", method_name, exc_info=True)
            method = getattr(self.fallback, method_name)
            result = await method(**kwargs)
            if isinstance(result, dict):
                result["persistenceFallbackReason"] = f"{type(exc).__name__}: {exc}"
            elif isinstance(result, list):
                for item in result:
                    if isinstance(item, dict):
                        item["persistenceFallbackReason"] = f"{type(exc).__name__}: {exc}"
            return result
