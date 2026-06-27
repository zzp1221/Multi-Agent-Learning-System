"""测试Prompt Caching功能"""

import pytest
from src.ai_modules.llms.openai_compatible import OpenAICompatibleClient


def test_apply_prompt_caching_claude():
    """测试Claude模型的缓存标记"""
    client = OpenAICompatibleClient(
        api_key="test-key",
        base_url="https://api.example.com",
        model_name="claude-3-opus",
    )

    messages = [
        {
            "role": "system",
            "content": "你是一个AI助手。" * 500,  # 长系统提示
        },
        {
            "role": "user",
            "content": "## 检索证据\n" + "这是检索证据内容。" * 300,  # 确保超过2048字符
        },
    ]

    cached = client._apply_prompt_caching(messages, "claude-3-opus")

    # 验证system消息被标记缓存
    assert cached[0]["role"] == "system"
    assert isinstance(cached[0]["content"], list)
    assert cached[0]["content"][0]["cache_control"] == {"type": "ephemeral"}

    # 验证检索证据被标记缓存
    assert cached[1]["role"] == "user"
    assert isinstance(cached[1]["content"], list)
    assert cached[1]["content"][0]["cache_control"] == {"type": "ephemeral"}


def test_apply_prompt_caching_short_content():
    """测试短内容不被缓存"""
    client = OpenAICompatibleClient(
        api_key="test-key",
        base_url="https://api.example.com",
        model_name="claude-3-opus",
    )

    messages = [
        {
            "role": "system",
            "content": "短提示",
        },
        {
            "role": "user",
            "content": "短查询",
        },
    ]

    cached = client._apply_prompt_caching(messages, "claude-3-opus")

    # 短内容不应该被标记缓存
    assert cached[0]["content"] == "短提示"
    assert cached[1]["content"] == "短查询"


def test_apply_prompt_caching_non_claude():
    """测试非Claude模型不启用缓存"""
    client = OpenAICompatibleClient(
        api_key="test-key",
        base_url="https://api.example.com",
        model_name="qwen-max",
    )

    messages = [
        {
            "role": "system",
            "content": "你是一个AI助手。" * 500,
        },
    ]

    cached = client._apply_prompt_caching(messages, "qwen-max")

    # Qwen模型不应该被修改
    assert cached[0]["content"] == messages[0]["content"]


def test_apply_prompt_caching_gpt4():
    """测试GPT-4模型的缓存（自动缓存，不需要显式标记）"""
    client = OpenAICompatibleClient(
        api_key="test-key",
        base_url="https://api.example.com",
        model_name="gpt-4-turbo",
    )

    messages = [
        {
            "role": "system",
            "content": "你是一个AI助手。" * 500,
        },
    ]

    cached = client._apply_prompt_caching(messages, "gpt-4-turbo")

    # GPT-4自动缓存，返回原始消息
    assert cached[0]["content"] == messages[0]["content"]


def test_apply_prompt_caching_preserves_other_fields():
    """测试缓存标记不影响其他字段"""
    client = OpenAICompatibleClient(
        api_key="test-key",
        base_url="https://api.example.com",
        model_name="claude-3-opus",
    )

    messages = [
        {
            "role": "system",
            "content": "你是一个AI助手。" * 500,
            "name": "system_prompt",
            "metadata": {"version": "1.0"},
        },
    ]

    cached = client._apply_prompt_caching(messages, "claude-3-opus")

    # 验证其他字段被保留
    assert cached[0]["role"] == "system"
    assert cached[0].get("name") == "system_prompt"
    assert cached[0].get("metadata") == {"version": "1.0"}


def test_apply_prompt_caching_graph_evidence():
    """测试图谱证据缓存"""
    client = OpenAICompatibleClient(
        api_key="test-key",
        base_url="https://api.example.com",
        model_name="claude-3-opus",
    )

    messages = [
        {
            "role": "user",
            "content": "## 图谱关联概念\n" + "概念节点" * 600,  # 确保超过2048字符
        },
    ]

    cached = client._apply_prompt_caching(messages, "claude-3-opus")

    # 验证图谱证据被缓存
    assert isinstance(cached[0]["content"], list)
    assert cached[0]["content"][0]["cache_control"] == {"type": "ephemeral"}


def test_apply_prompt_caching_mixed_messages():
    """测试混合消息的缓存"""
    client = OpenAICompatibleClient(
        api_key="test-key",
        base_url="https://api.example.com",
        model_name="claude-3-opus",
    )

    messages = [
        {"role": "system", "content": "你是AI助手。" * 500},
        {"role": "user", "content": "短查询"},
        {"role": "assistant", "content": "短回答"},
        {"role": "user", "content": "## 检索证据\n" + "证据内容。" * 500},  # 确保超过2048字符
    ]

    cached = client._apply_prompt_caching(messages, "claude-3-opus")

    # 验证只有长的system和检索证据被缓存
    assert isinstance(cached[0]["content"], list)  # system缓存
    assert isinstance(cached[1]["content"], str)  # 短查询不缓存
    assert isinstance(cached[2]["content"], str)  # assistant不缓存
    assert isinstance(cached[3]["content"], list)  # 检索证据缓存
