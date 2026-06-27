"""测试 AgentCoreLoop 的结构感知压缩集成"""

import json
import pytest
from src.ai_modules.runtime.agent_core_loop import AgentCoreLoop
from src.ai_modules.runtime.tool_registry import ToolRegistry


class MockLLM:
    """Mock LLM for testing"""

    async def complete(self, *, system_prompt: str, messages: list, tools: list):
        from src.ai_modules.runtime.agent_core_loop import AssistantTurn
        return AssistantTurn(content="test response", tool_calls=[])


def test_compact_tool_content_json():
    """测试JSON内容压缩 - 移除低优先级字段"""
    registry = ToolRegistry()
    loop = AgentCoreLoop(
        llm_client=MockLLM(),
        tool_registry=registry,
        max_tool_content_chars=100,
    )

    data = {
        "name": "测试",
        "value": 123,
        "metadata": {"debug": "info"},
        "_debug": "应该被移除",
        "timestamp": "2024-01-01",
    }
    json_str = json.dumps(data, ensure_ascii=False)

    result = loop._compact_tool_content(json_str)

    # 应该移除低优先级字段
    assert "name" in result
    assert "value" in result
    assert "metadata" not in result or "_debug" not in result


def test_compact_tool_content_code():
    """测试代码内容压缩 - 保留函数签名"""
    registry = ToolRegistry()
    loop = AgentCoreLoop(
        llm_client=MockLLM(),
        tool_registry=registry,
        max_tool_content_chars=150,
    )

    code = """def hello():
    print("hello")
    x = 1
    return x

class Test:
    pass
"""

    result = loop._compact_tool_content(code)

    # 应该保留函数和类定义
    assert "def hello" in result
    assert "class Test" in result


def test_compact_tool_content_prose():
    """测试散文内容压缩 - 提取关键句"""
    registry = ToolRegistry()
    loop = AgentCoreLoop(
        llm_client=MockLLM(),
        tool_registry=registry,
        max_tool_content_chars=80,
    )

    text = "第一句话。第二句话是一个很长很长很长很长的句子。第三句话。最后一句话。"

    result = loop._compact_tool_content(text)

    # 应该保留首尾句子
    assert "第一句话" in result
    assert "最后一句话" in result
    assert len(result) <= 150


def test_compact_tool_content_short_string():
    """测试短字符串不压缩"""
    registry = ToolRegistry()
    loop = AgentCoreLoop(
        llm_client=MockLLM(),
        tool_registry=registry,
        max_tool_content_chars=100,
    )

    text = "短文本"
    result = loop._compact_tool_content(text)

    assert result == text


def test_compact_tool_content_dict():
    """测试字典压缩"""
    registry = ToolRegistry()
    loop = AgentCoreLoop(
        llm_client=MockLLM(),
        tool_registry=registry,
        max_tool_content_chars=100,
    )

    data = {
        "name": "测试",
        "metadata": {"should_remove": True},
        "_debug": "remove",
        "value": 123,
    }

    result = loop._compact_tool_content(data)

    assert isinstance(result, dict)
    assert "name" in result
    assert "metadata" not in result


def test_compact_tool_content_list():
    """测试列表压缩"""
    registry = ToolRegistry()
    loop = AgentCoreLoop(
        llm_client=MockLLM(),
        tool_registry=registry,
        max_tool_content_chars=100,
    )

    data = list(range(20))
    result = loop._compact_tool_content(data)

    assert isinstance(result, list)
    assert len(result) <= 11


def test_compact_tool_content_none():
    """测试None值"""
    registry = ToolRegistry()
    loop = AgentCoreLoop(
        llm_client=MockLLM(),
        tool_registry=registry,
        max_tool_content_chars=100,
    )

    result = loop._compact_tool_content(None)
    assert result is None


def test_compact_tool_content_empty_string():
    """测试空字符串"""
    registry = ToolRegistry()
    loop = AgentCoreLoop(
        llm_client=MockLLM(),
        tool_registry=registry,
        max_tool_content_chars=100,
    )

    result = loop._compact_tool_content("")
    assert result == ""
