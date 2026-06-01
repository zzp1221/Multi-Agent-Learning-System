"""智能体运行时的故障恢复策略。"""

from __future__ import annotations

import asyncio
from collections import deque
from enum import Enum
from typing import Any, Awaitable, Callable


class RecoveryFailureType(str, Enum):
    """支持的恢复场景。"""

    LLM_API_TIMEOUT = "LLM_API_TIMEOUT"
    LLM_API_RATE_LIMIT = "LLM_API_RATE_LIMIT"
    RETRIEVAL_UNAVAILABLE = "RETRIEVAL_UNAVAILABLE"
    VECTOR_DB_TIMEOUT = "VECTOR_DB_TIMEOUT"
    CONTENT_GENERATION_FAILED = "CONTENT_GENERATION_FAILED"
    PROFILE_UPDATE_FAILED = "PROFILE_UPDATE_FAILED"
    TOOL_EXECUTION_ERROR = "TOOL_EXECUTION_ERROR"


class LLMRateLimitError(RuntimeError):
    """当上游 LLM 提供商对请求进行速率限制时抛出。"""


AsyncOperation = Callable[[], Awaitable[Any]]


class RecoveryEngine:
    """对可预期的运行时故障应用有限重试和降级策略。"""

    def __init__(self, sleep_seconds: float = 0.0) -> None:
        self.sleep_seconds = sleep_seconds
        self.audit_log: deque[dict[str, Any]] = deque(maxlen=200)

    async def call_with_recovery(
        self,
        *,
        failure_type: RecoveryFailureType,
        operation: AsyncOperation,
        fallback_operation: AsyncOperation | None = None,
    ) -> Any:
        retries = {
            RecoveryFailureType.LLM_API_TIMEOUT: 1,
            RecoveryFailureType.LLM_API_RATE_LIMIT: 2,
            RecoveryFailureType.RETRIEVAL_UNAVAILABLE: 1,
            RecoveryFailureType.VECTOR_DB_TIMEOUT: 2,
            RecoveryFailureType.CONTENT_GENERATION_FAILED: 1,
            RecoveryFailureType.PROFILE_UPDATE_FAILED: 2,
        }.get(failure_type, 0)
        last_error: Exception | None = None

        for attempt in range(retries + 1):
            try:
                return await operation()
            except Exception as exc:
                last_error = exc
                self.audit_log.append(
                    {
                        "failure_type": failure_type.value,
                        "attempt": attempt + 1,
                        "error": str(exc),
                    }
                )
                if attempt >= retries:
                    break
                if self.sleep_seconds > 0:
                    await asyncio.sleep(self.sleep_seconds)

        if fallback_operation is not None:
            return await fallback_operation()
        if last_error is not None:
            raise last_error
        raise RuntimeError("unreachable recovery state")  # pragma: no cover - 不可到达的恢复状态

    async def recover_tool_execution_error(
        self,
        *,
        tool_name: str,
        error: Exception,
    ) -> dict[str, Any]:
        payload = {
            "tool_name": tool_name,
            "error": str(error),
            "recovered": True,
            "failure_type": RecoveryFailureType.TOOL_EXECUTION_ERROR.value,
        }
        self.audit_log.append(payload)
        return payload

    async def recover_retrieval_unavailable(
        self,
        *,
        query: str,
        fallback_payload: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "query": query,
            "fallback_payload": fallback_payload,
            "recovered": True,
            "failure_type": RecoveryFailureType.RETRIEVAL_UNAVAILABLE.value,
        }
        self.audit_log.append(payload)
        return payload

    async def recover_vector_db_timeout(
        self,
        *,
        query: str,
    ) -> dict[str, Any]:
        payload = {
            "query": query,
            "recovered": True,
            "failure_type": RecoveryFailureType.VECTOR_DB_TIMEOUT.value,
        }
        self.audit_log.append(payload)
        return payload

    async def recover_content_generation_failed(
        self,
        *,
        asset_type: str,
        fallback_payload: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "asset_type": asset_type,
            "fallback_payload": fallback_payload,
            "recovered": True,
            "failure_type": RecoveryFailureType.CONTENT_GENERATION_FAILED.value,
        }
        self.audit_log.append(payload)
        return payload

    async def recover_profile_update_failed(
        self,
        *,
        user_id: str,
        fallback_payload: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "user_id": user_id,
            "fallback_payload": fallback_payload,
            "recovered": True,
            "failure_type": RecoveryFailureType.PROFILE_UPDATE_FAILED.value,
        }
        self.audit_log.append(payload)
        return payload
