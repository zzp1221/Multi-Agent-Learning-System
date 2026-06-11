"""支持提供商路由和启发式回退的主观题判题评估器。"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Protocol

import asyncio
from opentelemetry import trace
from pydantic import ValidationError

from src.ai_modules.config import get_settings
from src.ai_modules.llms.agent_models import create_compatible_client
from src.ai_modules.models import PracticeQuestion, SubjectiveJudgeEvaluation
from src.ai_modules.runtime.skill_loader import append_user_skill_to_prompt

LOGGER = logging.getLogger(__name__)
TRACER = trace.get_tracer(__name__)


class SupportsSubjectiveJudgeEvaluator(Protocol):
    """主观题评估客户端的协议。"""

    async def evaluate(
        self,
        *,
        question: PracticeQuestion,
        learner_answer: str,
    ) -> SubjectiveJudgeEvaluation: ...


class HeuristicSubjectiveJudgeEvaluator:
    """基于关键词覆盖的回退评估器。"""

    async def evaluate(
        self,
        *,
        question: PracticeQuestion,
        learner_answer: str,
    ) -> SubjectiveJudgeEvaluation:
        answer = self._normalize_text(learner_answer)
        if not answer:
            return SubjectiveJudgeEvaluation(
                score=0.0,
                isCorrect=False,
                reason="未作答或答案为空",
                feedback="请先按“定义 -> 条件 -> 例子”结构补全答案。",
                confidenceLevel="LOW",
            )
        keywords = [
            keyword
            for keyword in question.answer.replace("->", " ").replace("/", " ").split("，")
            if keyword.strip()
        ]
        matched = sum(
            1
            for keyword in keywords
            if self._normalize_text(keyword) and self._normalize_text(keyword) in answer
        )
        coverage = matched / max(len(keywords), 1)
        if coverage >= 0.8:
            return SubjectiveJudgeEvaluation(
                score=20.0,
                isCorrect=True,
                reason="回答覆盖了关键要点",
                feedback="主观题回答较完整。",
                confidenceLevel="MEDIUM",
            )
        if coverage >= 0.4:
            return SubjectiveJudgeEvaluation(
                score=12.0,
                isCorrect=True,
                reason="回答部分覆盖了关键要点，但例子或错因说明不够完整",
                feedback="建议补充更具体的误判场景或条件说明。",
                confidenceLevel="MEDIUM",
            )
        return SubjectiveJudgeEvaluation(
            score=6.0,
            isCorrect=False,
            reason="回答未完整覆盖使用前提或错因分析",
            feedback="建议按“定义 -> 条件 -> 例子”结构重答。",
            confidenceLevel="LOW",
        )

    def _normalize_text(self, value: str) -> str:
        return "".join(str(value).strip().upper().split())


class OpenAICompatibleSubjectiveJudgeEvaluator:
    """支持多提供商的评估器，返回经过验证的结构化判题输出。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model_name: str | None = None,
        max_retries: int = 2,
        backoff_seconds: float = 0.5,
    ) -> None:
        settings = get_settings()
        self.provider_name = settings.resolve_component_provider("judge_llm")
        self.api_key = api_key or settings.provider_api_key(self.provider_name)
        self.model_name = model_name or settings.resolve_component_model(
            "judge_llm",
            default_logical_model="fast_model",
            provider_name=self.provider_name,
        )
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self.client = create_compatible_client(model_name=self.model_name, provider_name=self.provider_name)

    async def evaluate(
        self,
        *,
        question: PracticeQuestion,
        learner_answer: str,
    ) -> SubjectiveJudgeEvaluation:
        if not self.api_key:
            raise RuntimeError(f"missing {self.provider_name} api key")

        system_prompt = (
            "你是教育系统中的判题器，负责根据标准答案和评分要求评估主观题。"
            "请只返回 JSON 对象，字段必须为：score, isCorrect, reason, feedback, confidenceLevel。"
            "score 范围 0-20，confidenceLevel 只能是 LOW 或 MEDIUM。"
        )
        system_prompt = append_user_skill_to_prompt(
            system_prompt,
            component_name="judge_llm",
            ability_key="ability:assessment",
        )
        user_prompt = "\n".join(
            [
                f"题目: {question.stem}",
                f"标准答案: {question.answer}",
                f"知识点: {', '.join(question.knowledge_tags) or '暂无'}",
                f"参考说明: {question.explanation}",
                f"学生答案: {learner_answer or '未作答'}",
            ]
        )

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with TRACER.start_as_current_span(f"{self.provider_name}.evaluate_subjective"):
                    response = await self.client.chat_completion(
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        temperature=0.1,
                        max_tokens=320,
                    )
                message = self.client.extract_message(response)
                payload = self.client.extract_content(message)
                data = self._extract_json(payload)
                evaluation = SubjectiveJudgeEvaluation.model_validate(data)
                evaluation.score = max(0.0, min(float(evaluation.score), 20.0))
                return evaluation
            except (ValidationError, ValueError, RuntimeError, KeyError, TypeError) as exc:
                last_error = exc
                LOGGER.warning(
                    "%s subjective judging attempt %s failed: %s",
                    self.provider_name,
                    attempt + 1,
                    exc,
                )
                if attempt >= self.max_retries:
                    break
                await asyncio.sleep(self.backoff_seconds * (2**attempt))

        raise RuntimeError(f"{self.provider_name} subjective judging failed: {last_error}")

    def _extract_json(self, content: str) -> dict[str, Any]:
        fenced_match = re.search(r"```json\s*(\{[\s\S]*\})\s*```", content)
        raw_json = fenced_match.group(1) if fenced_match else content.strip()
        start = raw_json.find("{")
        end = raw_json.rfind("}")
        if start == -1 or end == -1:
            raise ValueError(f"no json object found in {self.provider_name} output")
        return json.loads(raw_json[start : end + 1])


class SubjectiveJudgeEvaluatorFactory:
    """创建支持提供商路由和启发式回退的主观题评估器。"""

    @staticmethod
    def create() -> SupportsSubjectiveJudgeEvaluator:
        settings = get_settings()
        if settings.enable_local_judge:
            from src.ai_modules.llms.local_subjective_evaluator import (
                LocalSubjectiveJudgeEvaluator,
            )
            return LocalSubjectiveJudgeEvaluator(model_path=settings.local_judge_model_path)
        provider_name = settings.resolve_component_provider("judge_llm")
        if settings.provider_ready(provider_name):
            return OpenAICompatibleSubjectiveJudgeEvaluator()
        raise RuntimeError("judge_llm provider is not ready; subjective questions require a real LLM judge")


SubjectiveJudgeEvaluator = OpenAICompatibleSubjectiveJudgeEvaluator
