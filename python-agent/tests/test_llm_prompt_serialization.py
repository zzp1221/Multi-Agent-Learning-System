from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from src.ai_modules.llms.agent_models import OpenAICompatibleEvaluationGenerator


class _RecordingJSONGenerator:
    def __init__(self) -> None:
        self.user_prompt = ""

    async def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        del system_prompt, max_tokens
        self.user_prompt = user_prompt
        return {
            "overallLevel": "MEDIUM",
            "strengths": ["能描述当前学习目标"],
            "weaknesses": ["需要补齐关键知识点"],
            "nextFocus": ["按知识图谱顺序巩固"],
            "dimensions": [
                {
                    "name": "学习效果",
                    "level": "MEDIUM",
                    "evidence": "上下文可解析",
                    "recommendation": "继续跟踪练习和资源使用反馈",
                }
            ],
            "summaryText": "已完成学习效果评估。",
        }


class _DimensionOnlyJSONGenerator:
    async def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        del system_prompt, user_prompt, max_tokens
        return {
            "name": "动态调整",
            "level": "BASIC",
            "evidence": "现有画像信号不足，需要先建立首版学习路径。",
            "recommendation": "从核心概念补齐开始推进。",
            "dimensions": [],
        }


@pytest.mark.asyncio
async def test_evaluation_prompt_serializes_datetime_context() -> None:
    recorder = _RecordingJSONGenerator()
    generator = object.__new__(OpenAICompatibleEvaluationGenerator)
    generator.generator = recorder

    payload = await generator.evaluate(
        system_prompt="评估学生学习效果",
        context_payload={
            "structuredConversationSummary": {
                "createdAt": datetime(2026, 6, 3, 8, 30, tzinfo=timezone.utc),
                "summaryText": "最近在学习联合索引。",
            }
        },
    )

    assert payload.summary_text == "已完成学习效果评估。"
    assert "2026-06-03T08:30:00+00:00" in recorder.user_prompt


@pytest.mark.asyncio
async def test_evaluation_generator_normalizes_dimension_only_payload() -> None:
    generator = object.__new__(OpenAICompatibleEvaluationGenerator)
    generator.generator = _DimensionOnlyJSONGenerator()

    payload = await generator.evaluate(
        system_prompt="评估学生学习效果",
        context_payload={
            "aggregatedBehavior": {
                "candidateStrengths": ["愿意继续学习"],
                "candidateWeaknesses": ["Python 基础语法"],
                "recommendedFocus": ["变量与控制流"],
            }
        },
    )

    assert payload.overall_level == "BASIC"
    assert payload.strengths == ["愿意继续学习"]
    assert payload.weaknesses == ["Python 基础语法"]
    assert payload.next_focus == ["变量与控制流"]
    assert payload.dimensions[0].name == "动态调整"
    assert payload.summary_text == "现有画像信号不足，需要先建立首版学习路径。"
