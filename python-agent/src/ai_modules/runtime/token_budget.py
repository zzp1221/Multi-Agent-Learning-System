"""动态 Token 预算分配器 - 根据模型上下文窗口智能分配"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TokenBudget:
    """各组件的 Token 预算分配"""

    conversation_history: int
    retrieval_evidence: int
    tool_results: int
    reserve: int

    @property
    def total_allocated(self) -> int:
        return (
            self.conversation_history
            + self.retrieval_evidence
            + self.tool_results
            + self.reserve
        )


class TokenBudgetAllocator:
    """根据模型上下文窗口动态分配 Token 预算"""

    # 模型上下文窗口配置 (tokens)
    MODEL_CONTEXT_WINDOWS = {
        "gpt-4": 8192,
        "gpt-4-32k": 32768,
        "gpt-4-turbo": 128000,
        "gpt-4o": 128000,
        "gpt-3.5-turbo": 16384,
        "gpt-3.5-turbo-16k": 16384,
        "claude-3-opus": 200000,
        "claude-3-sonnet": 200000,
        "claude-3-haiku": 200000,
        "claude-3.5-sonnet": 200000,
        "claude-3.5-haiku": 200000,
        "qwen-max": 30000,
        "qwen-plus": 30000,
        "qwen-turbo": 8000,
        "qwen2.5-72b": 30000,
        "qwen2.5-32b": 30000,
        "qwen3.6-plus": 30000,
        "qwen3.6-max-preview": 30000,
        "qwen3.6-flash": 30000,
        "deepseek-chat": 64000,
        "deepseek-coder": 64000,
        "default": 16384,
    }

    def __init__(
        self,
        model_name: str = "default",
        output_reserve_ratio: float = 0.2,
    ):
        """
        Args:
            model_name: 模型名称，用于查询上下文窗口
            output_reserve_ratio: 为输出、工具定义、系统提示预留的比例
        """
        self.model_name = model_name
        self.context_window = self._get_context_window(model_name)
        self.output_reserve_ratio = output_reserve_ratio

        # 可用预算 = 总窗口 * (1 - 预留比例)
        self.available_budget = int(self.context_window * (1 - output_reserve_ratio))

    def allocate(
        self,
        *,
        system_prompt_tokens: int,
        tool_schemas_tokens: int,
        user_query_tokens: int,
        conversation_weight: float = 0.35,
        evidence_weight: float = 0.45,
        tool_result_weight: float = 0.15,
        reserve_weight: float = 0.05,
    ) -> TokenBudget:
        """
        分配 Token 预算

        Args:
            system_prompt_tokens: 系统提示词的 token 数
            tool_schemas_tokens: 工具定义的 token 数
            user_query_tokens: 用户查询的 token 数
            conversation_weight: 对话历史权重
            evidence_weight: 检索证据权重
            tool_result_weight: 工具结果权重
            reserve_weight: 预留权重

        Returns:
            TokenBudget: 各组件的预算分配
        """
        # 已使用的固定 token
        used = system_prompt_tokens + tool_schemas_tokens + user_query_tokens

        # 剩余可分配预算
        remaining = max(0, self.available_budget - used)

        # 确保权重和为 1
        total_weight = (
            conversation_weight
            + evidence_weight
            + tool_result_weight
            + reserve_weight
        )

        # 归一化权重
        conv_w = conversation_weight / total_weight
        evid_w = evidence_weight / total_weight
        tool_w = tool_result_weight / total_weight
        rsv_w = reserve_weight / total_weight

        return TokenBudget(
            conversation_history=int(remaining * conv_w),
            retrieval_evidence=int(remaining * evid_w),
            tool_results=int(remaining * tool_w),
            reserve=int(remaining * rsv_w),
        )

    def allocate_simple(
        self,
        *,
        used_tokens: int,
        conversation_weight: float = 0.35,
        evidence_weight: float = 0.45,
        tool_result_weight: float = 0.15,
        reserve_weight: float = 0.05,
    ) -> TokenBudget:
        """简化版分配 - 直接传入已使用 token 总数"""
        remaining = max(0, self.available_budget - used_tokens)

        total_weight = (
            conversation_weight
            + evidence_weight
            + tool_result_weight
            + reserve_weight
        )

        conv_w = conversation_weight / total_weight
        evid_w = evidence_weight / total_weight
        tool_w = tool_result_weight / total_weight
        rsv_w = reserve_weight / total_weight

        return TokenBudget(
            conversation_history=int(remaining * conv_w),
            retrieval_evidence=int(remaining * evid_w),
            tool_results=int(remaining * tool_w),
            reserve=int(remaining * rsv_w),
        )

    def _get_context_window(self, model_name: str) -> int:
        """获取模型的上下文窗口大小"""
        # 标准化模型名称
        normalized = model_name.lower().strip()

        # 精确匹配
        if normalized in self.MODEL_CONTEXT_WINDOWS:
            return self.MODEL_CONTEXT_WINDOWS[normalized]

        # 前缀匹配
        for key, window in self.MODEL_CONTEXT_WINDOWS.items():
            if key != "default" and normalized.startswith(key):
                return window

        # 默认值
        return self.MODEL_CONTEXT_WINDOWS["default"]

    @classmethod
    def estimate_tokens(cls, text: str) -> int:
        """粗略估算 token 数 - 中文约 1.5 chars/token, 英文约 4 chars/token"""
        chinese_chars = sum(1 for c in text if '一' <= c <= '鿿')
        other_chars = len(text) - chinese_chars

        # 中文: 1.5 chars/token, 英文: 4 chars/token
        estimated = int(chinese_chars / 1.5 + other_chars / 4)
        return max(1, estimated)


def create_allocator_for_model(model_name: str) -> TokenBudgetAllocator:
    """工厂函数 - 为指定模型创建预算分配器"""
    return TokenBudgetAllocator(model_name=model_name)
