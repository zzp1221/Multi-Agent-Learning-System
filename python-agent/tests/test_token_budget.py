"""测试动态Token预算分配器"""

import pytest
from src.ai_modules.runtime.token_budget import TokenBudgetAllocator, TokenBudget, create_allocator_for_model


def test_allocator_default_model():
    """测试默认模型的上下文窗口"""
    allocator = TokenBudgetAllocator(model_name="default")
    assert allocator.context_window == 16384
    assert allocator.available_budget == int(16384 * 0.8)


def test_allocator_claude_opus():
    """测试Claude Opus大窗口模型"""
    allocator = TokenBudgetAllocator(model_name="claude-3-opus")
    assert allocator.context_window == 200000
    assert allocator.available_budget == int(200000 * 0.8)


def test_allocator_qwen():
    """测试Qwen模型"""
    allocator = TokenBudgetAllocator(model_name="qwen3.6-plus")
    assert allocator.context_window == 30000
    assert allocator.available_budget == int(30000 * 0.8)


def test_allocate_simple():
    """测试简化版分配"""
    allocator = TokenBudgetAllocator(model_name="gpt-4")
    budget = allocator.allocate_simple(used_tokens=2000)

    assert isinstance(budget, TokenBudget)
    assert budget.conversation_history > 0
    assert budget.retrieval_evidence > 0
    assert budget.tool_results > 0
    assert budget.reserve > 0

    # 验证总和接近可用预算
    expected_remaining = allocator.available_budget - 2000
    assert abs(budget.total_allocated - expected_remaining) < 10


def test_allocate_with_components():
    """测试详细分配"""
    allocator = TokenBudgetAllocator(model_name="claude-3-opus")
    budget = allocator.allocate(
        system_prompt_tokens=500,
        tool_schemas_tokens=300,
        user_query_tokens=200,
    )

    assert isinstance(budget, TokenBudget)
    # 检索证据应该占最大比例(45%)
    assert budget.retrieval_evidence > budget.conversation_history
    assert budget.retrieval_evidence > budget.tool_results


def test_estimate_tokens_chinese():
    """测试中文token估算"""
    chinese_text = "这是一个测试文本"
    tokens = TokenBudgetAllocator.estimate_tokens(chinese_text)
    assert tokens > 0
    # 中文约1.5字符/token
    expected = int(len(chinese_text) / 1.5)
    assert abs(tokens - expected) < 2


def test_estimate_tokens_english():
    """测试英文token估算"""
    english_text = "This is a test text"
    tokens = TokenBudgetAllocator.estimate_tokens(english_text)
    assert tokens > 0
    # 英文约4字符/token
    expected = int(len(english_text) / 4)
    assert abs(tokens - expected) < 2


def test_estimate_tokens_mixed():
    """测试中英文混合token估算"""
    mixed_text = "这是test文本"
    tokens = TokenBudgetAllocator.estimate_tokens(mixed_text)
    assert tokens > 0


def test_create_allocator_factory():
    """测试工厂函数"""
    allocator = create_allocator_for_model("gpt-4")
    assert allocator.model_name == "gpt-4"
    assert allocator.context_window == 8192


def test_model_name_prefix_matching():
    """测试模型名称前缀匹配"""
    # 测试部分匹配
    allocator = TokenBudgetAllocator(model_name="claude-3-opus-20240229")
    assert allocator.context_window == 200000

    allocator = TokenBudgetAllocator(model_name="qwen3.6-plus-0125")
    assert allocator.context_window == 30000


def test_budget_total_allocated():
    """测试TokenBudget的total_allocated属性"""
    budget = TokenBudget(
        conversation_history=1000,
        retrieval_evidence=2000,
        tool_results=500,
        reserve=100,
    )
    assert budget.total_allocated == 3600


def test_zero_used_tokens():
    """测试零token使用的情况"""
    allocator = TokenBudgetAllocator(model_name="gpt-4")
    budget = allocator.allocate_simple(used_tokens=0)

    # 应该分配所有可用预算（允许小的舍入误差）
    assert abs(budget.total_allocated - allocator.available_budget) < 5


def test_excessive_used_tokens():
    """测试token使用超出预算的情况"""
    allocator = TokenBudgetAllocator(model_name="gpt-4")
    # 使用超过可用预算
    budget = allocator.allocate_simple(used_tokens=allocator.available_budget + 1000)

    # 应该返回0或很小的值
    assert budget.total_allocated == 0
    assert budget.conversation_history == 0
    assert budget.retrieval_evidence == 0


def test_custom_weights():
    """测试自定义权重分配"""
    allocator = TokenBudgetAllocator(model_name="gpt-4")
    budget = allocator.allocate_simple(
        used_tokens=2000,
        conversation_weight=0.5,  # 增加对话历史权重
        evidence_weight=0.3,
        tool_result_weight=0.1,
        reserve_weight=0.1,
    )

    # 对话历史应该比检索证据多
    assert budget.conversation_history > budget.retrieval_evidence
