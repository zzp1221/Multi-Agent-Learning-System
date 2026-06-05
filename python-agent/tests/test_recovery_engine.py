import pytest

from src.ai_modules.runtime import (
    AgentCoreLoop,
    AssistantTurn,
    RecoveryEngine,
    RecoveryFailureType,
    ToolCall,
    ToolRegistry,
)


class TimeoutThenSuccessLLM:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, *, system_prompt, messages, tools) -> AssistantTurn:
        del system_prompt, messages, tools
        self.calls += 1
        if self.calls == 1:
            raise TimeoutError("llm timeout")
        return AssistantTurn(content="重试后成功。")


class ToolCallThenFinishLLM:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, *, system_prompt, messages, tools) -> AssistantTurn:
        del system_prompt, messages, tools
        self.calls += 1
        if self.calls == 1:
            return AssistantTurn(
                content="先调用工具",
                tool_calls=[
                    ToolCall(id="call_tool", name="unstable_tool", input={"x": 1})
                ],
            )
        return AssistantTurn(content="工具失败后继续完成。")


@pytest.mark.asyncio
async def test_recovery_engine_retries_llm_timeout_once() -> None:
    registry = ToolRegistry()
    engine = RecoveryEngine()
    loop = AgentCoreLoop(
        llm_client=TimeoutThenSuccessLLM(),
        tool_registry=registry,
        recovery_engine=engine,
        max_iterations=3,
    )

    result = await loop.run(
        system_prompt="test",
        messages=[{"role": "user", "content": "hello"}],
    )

    assert result.final_text == "重试后成功。"
    assert engine.audit_log[0]["failure_type"] == "LLM_API_TIMEOUT"


@pytest.mark.asyncio
async def test_recovery_engine_injects_tool_error_and_continues() -> None:
    registry = ToolRegistry()

    def unstable_tool(tool_input: dict) -> dict:
        del tool_input
        raise RuntimeError("vector db timeout")

    registry.register(
        name="unstable_tool",
        fn=unstable_tool,
        permission_level=1,
        description="Tool that fails once and should be recovered",
    )
    engine = RecoveryEngine()
    loop = AgentCoreLoop(
        llm_client=ToolCallThenFinishLLM(),
        tool_registry=registry,
        recovery_engine=engine,
        max_iterations=3,
        agent_level=1,
    )

    result = await loop.run(
        system_prompt="test",
        messages=[{"role": "user", "content": "run"}],
    )

    assert result.final_text == "工具失败后继续完成。"
    assert result.tool_results[0].is_error is True
    assert result.tool_results[0].output["recovered"] is True
    assert engine.audit_log[-1]["failure_type"] == "TOOL_EXECUTION_ERROR"


@pytest.mark.asyncio
async def test_recovery_engine_falls_back_for_retrieval_unavailable() -> None:
    engine = RecoveryEngine()

    async def operation() -> dict:
        raise RuntimeError("retrieval unavailable")

    async def fallback_operation() -> dict:
        return {"mode": "grep_only"}

    payload = await engine.call_with_recovery(
        failure_type=RecoveryFailureType.RETRIEVAL_UNAVAILABLE,
        operation=operation,
        fallback_operation=fallback_operation,
    )

    assert payload["mode"] == "grep_only"
    assert engine.audit_log[0]["failure_type"] == "RETRIEVAL_UNAVAILABLE"


@pytest.mark.asyncio
async def test_recovery_engine_falls_back_for_profile_update_failed() -> None:
    engine = RecoveryEngine()

    async def operation() -> dict:
        raise RuntimeError("profile update failed")

    async def fallback_operation() -> dict:
        return {"version": 1, "summaryText": "fallback profile"}

    payload = await engine.call_with_recovery(
        failure_type=RecoveryFailureType.PROFILE_UPDATE_FAILED,
        operation=operation,
        fallback_operation=fallback_operation,
    )

    assert payload["summaryText"] == "fallback profile"
    assert engine.audit_log[-1]["failure_type"] == "PROFILE_UPDATE_FAILED"
