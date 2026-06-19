import pytest

from src.ai_modules.runtime import (
    AgentCoreLoop,
    AssistantTurn,
    HookChain,
    KnowledgeGuardHook,
    MaxIterationsExceededError,
    ToolCall,
    ToolRegistry,
)


class MockLLM:
    def __init__(self, turns: list[AssistantTurn]) -> None:
        self.turns = turns
        self.calls = 0

    async def complete(self, *, system_prompt, messages, tools) -> AssistantTurn:
        del system_prompt, messages, tools
        turn = self.turns[self.calls]
        self.calls += 1
        return turn


class CapturingLLM:
    def __init__(self) -> None:
        self.seen_messages: list[dict] = []
        self.seen_tools: list[dict] = []

    async def complete(self, *, system_prompt, messages, tools) -> AssistantTurn:
        del system_prompt
        self.seen_messages = messages
        self.seen_tools = tools
        return AssistantTurn(content="完成")


async def async_expand_outline(tool_input: dict) -> dict:
    return {
        "title": tool_input["topic"],
        "sections": ["定义", "原理", "例题"],
    }


def passthrough_generation_tool(tool_input: dict) -> dict:
    return tool_input


def test_tool_registry_filters_tools_by_permission_level() -> None:
    registry = ToolRegistry()
    registry.register(
        name="read_knowledge",
        fn=lambda tool_input: {"query": tool_input["query"]},
        permission_level=1,
        description="Read-only retrieval tool",
    )
    registry.register(
        name="write_sandbox",
        fn=lambda tool_input: {"file": tool_input["file"]},
        permission_level=3,
        description="Sandbox writer",
    )

    visible_tools = registry.list_for_agent(agent_level=1)

    assert [tool.name for tool in visible_tools] == ["read_knowledge"]


@pytest.mark.asyncio
async def test_agent_core_loop_executes_multi_turn_tool_calls() -> None:
    registry = ToolRegistry()
    registry.register(
        name="retrieve_context",
        fn=lambda tool_input: {
            "query": tool_input["query"],
            "docs": ["B+树是数据库索引常用结构"],
        },
        permission_level=1,
        description="Retrieve supporting knowledge",
    )
    registry.register(
        name="expand_outline",
        fn=async_expand_outline,
        permission_level=2,
        description="Expand a document outline",
    )

    llm = MockLLM(
        turns=[
            AssistantTurn(
                content="先检索相关知识",
                tool_calls=[
                    ToolCall(
                        id="call_1",
                        name="retrieve_context",
                        input={"query": "数据库索引"},
                    )
                ],
            ),
            AssistantTurn(
                content="继续生成大纲",
                tool_calls=[
                    ToolCall(
                        id="call_2",
                        name="expand_outline",
                        input={"topic": "数据库索引导学"},
                    )
                ],
            ),
            AssistantTurn(content="已根据检索结果生成文档大纲。"),
        ]
    )

    loop = AgentCoreLoop(
        llm_client=llm,
        tool_registry=registry,
        max_iterations=5,
        agent_level=2,
    )

    result = await loop.run(
        system_prompt="你是一个教学 Agent。",
        messages=[{"role": "user", "content": "帮我生成数据库索引导学"}],
    )

    assert result.final_text == "已根据检索结果生成文档大纲。"
    assert result.iterations == 3
    assert len(result.tool_results) == 2
    assert result.tool_results[0].tool_name == "retrieve_context"
    assert result.tool_results[1].output["sections"] == ["定义", "原理", "例题"]


@pytest.mark.asyncio
async def test_agent_core_loop_raises_when_iterations_exceeded() -> None:
    registry = ToolRegistry()
    registry.register(
        name="loop_tool",
        fn=lambda tool_input: tool_input,
        permission_level=1,
        description="A tool that never leads to termination",
    )
    llm = MockLLM(
        turns=[
            AssistantTurn(
                content="继续",
                tool_calls=[ToolCall(id="call_loop", name="loop_tool", input={})],
            ),
            AssistantTurn(
                content="继续",
                tool_calls=[ToolCall(id="call_loop_2", name="loop_tool", input={})],
            ),
        ]
    )

    loop = AgentCoreLoop(
        llm_client=llm,
        tool_registry=registry,
        max_iterations=2,
        agent_level=1,
    )

    with pytest.raises(MaxIterationsExceededError):
        await loop.run(
            system_prompt="test",
            messages=[{"role": "user", "content": "hi"}],
        )


@pytest.mark.asyncio
async def test_agent_core_loop_iteration_error_keeps_partial_tool_results() -> None:
    registry = ToolRegistry()
    registry.register(
        name="read_retrieval_evidence",
        fn=lambda tool_input: {"documents": [{"title": "B+ tree"}], **tool_input},
        permission_level=1,
        description="Read evidence",
    )
    llm = MockLLM(
        turns=[
            AssistantTurn(
                content="read first",
                tool_calls=[ToolCall(id="call_1", name="read_retrieval_evidence", input={})],
            ),
        ]
    )
    loop = AgentCoreLoop(
        llm_client=llm,
        tool_registry=registry,
        max_iterations=1,
        agent_level=1,
    )

    with pytest.raises(MaxIterationsExceededError) as exc_info:
        await loop.run(
            system_prompt="test",
            messages=[{"role": "user", "content": "hi"}],
        )

    assert exc_info.value.tool_results[0].tool_name == "read_retrieval_evidence"
    assert exc_info.value.tool_results[0].output["documents"][0]["title"] == "B+ tree"
    assert exc_info.value.messages[-1]["role"] == "tool"


@pytest.mark.asyncio
async def test_knowledge_guard_injects_retrieved_context_into_generation_tool() -> None:
    registry = ToolRegistry()
    registry.register(
        name="generate_document",
        fn=passthrough_generation_tool,
        permission_level=2,
        description="Generate a course document",
    )
    llm = MockLLM(
        turns=[
            AssistantTurn(
                content="先生成文档",
                tool_calls=[
                    ToolCall(
                        id="call_generate",
                        name="generate_document",
                        input={"topic": "数据库索引"},
                    )
                ],
            ),
            AssistantTurn(content="文档生成完成。"),
        ]
    )
    hook_chain = HookChain(
        pre_hooks=[
            KnowledgeGuardHook(
                retrieval_provider=lambda tool_input: {
                    "query": tool_input["topic"],
                    "docs": ["B+树索引适合范围查询"],
                }
            )
        ]
    )
    loop = AgentCoreLoop(
        llm_client=llm,
        tool_registry=registry,
        hook_chain=hook_chain,
        max_iterations=3,
        agent_level=2,
    )

    result = await loop.run(
        system_prompt="test",
        messages=[{"role": "user", "content": "生成数据库索引文档"}],
    )

    assert result.tool_results[0].is_error is False
    assert result.tool_results[0].output["retrieved_context"]["docs"] == [
        "B+树索引适合范围查询"
    ]


@pytest.mark.asyncio
async def test_knowledge_guard_denies_generation_without_context() -> None:
    registry = ToolRegistry()
    registry.register(
        name="generate_document",
        fn=passthrough_generation_tool,
        permission_level=2,
        description="Generate a course document",
    )
    llm = MockLLM(
        turns=[
            AssistantTurn(
                content="尝试生成文档",
                tool_calls=[
                    ToolCall(
                        id="call_denied",
                        name="generate_document",
                        input={"topic": "空上下文"},
                    )
                ],
            ),
            AssistantTurn(content="因为没有证据，所以终止。"),
        ]
    )
    hook_chain = HookChain(
        pre_hooks=[KnowledgeGuardHook(retrieval_provider=lambda tool_input: None)]
    )
    loop = AgentCoreLoop(
        llm_client=llm,
        tool_registry=registry,
        hook_chain=hook_chain,
        max_iterations=3,
        agent_level=2,
    )

    result = await loop.run(
        system_prompt="test",
        messages=[{"role": "user", "content": "生成数据库索引文档"}],
    )

    assert result.tool_results[0].is_error is True
    assert result.tool_results[0].output == "无相关知识依据"


@pytest.mark.asyncio
async def test_agent_core_loop_compacts_large_tool_messages_before_llm_call() -> None:
    registry = ToolRegistry()
    registry.register(
        name="retrieve_context",
        fn=lambda tool_input: tool_input,
        permission_level=1,
        description="Retrieve supporting knowledge",
    )
    llm = CapturingLLM()
    loop = AgentCoreLoop(
        llm_client=llm,
        tool_registry=registry,
        max_iterations=1,
        agent_level=1,
        max_tool_content_chars=32,
        max_tool_list_items=2,
        max_tool_dict_items=2,
    )

    result = await loop.run(
        system_prompt="test",
        messages=[
            {"role": "user", "content": "hi"},
            {
                "role": "tool",
                "name": "retrieve_context",
                "tool_call_id": "call_1",
                "content": {
                    "summary": "A" * 80,
                    "documents": ["doc1", "doc2", "doc3"],
                    "metadata": {
                        "topic": "索引",
                        "difficulty": "medium",
                        "extra": "ignored",
                    },
                },
            },
        ],
    )

    assert result.final_text == "完成"
    compacted_tool_message = llm.seen_messages[1]
    assert compacted_tool_message["role"] == "tool"
    assert compacted_tool_message["content"]["summary"].endswith("...[truncated]")
    assert compacted_tool_message["content"]["documents"][-1] == {"_truncated_items": 1}
    assert compacted_tool_message["content"]["_truncated_keys"] == 1
    assert len(llm.seen_tools) == 1
