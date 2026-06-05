"""基于 AgentCoreLoop 和结构化题批输出的练习 Agent。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import inspect
from typing import Any

from src.ai_modules.agents.base import PlaceholderAgent
from src.ai_modules.async_utils import cancel_and_await
from src.ai_modules.generation.resource_builder import ResourceGenerationService
from src.ai_modules.llms import PracticeQuestionGenerator
from src.ai_modules.memory import PostgresPracticeStore, PracticeStore
from src.ai_modules.models import (
    ProgressPayload,
    ProgressSSEEvent,
    PracticeQuestion,
    QuestionBatchPayload,
    QuestionBatchSSEEvent,
    SSEEvent,
)
from src.ai_modules.prompts import build_practice_system_prompt
from src.ai_modules.runtime import (
    SystemSnapshot,
)
from src.ai_modules.runtime.provenance import build_llm_provenance
from src.ai_modules.runtime.skill_loader import SkillPromptLoader


class PracticeAgent(PlaceholderAgent):
    """根据学习者上下文生成练习题。"""

    def __init__(
        self,
        practice_store: PracticeStore | None = None,
        question_generator: Any | None = None,
        heartbeat_interval_seconds: float = 15.0,
    ) -> None:
        super().__init__("Practice Agent", "practice")
        self.practice_store = practice_store or PostgresPracticeStore()
        self.question_generator = question_generator or PracticeQuestionGenerator()
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.skill_loader = SkillPromptLoader()

    def system_prompt(self, snapshot: SystemSnapshot) -> str:
        return self.skill_loader.build_system_prompt(
            skill_name="practice",
            snapshot=snapshot,
            fallback_prompt=build_practice_system_prompt(snapshot),
        )

    async def run(
        self,
        *,
        task_id: str,
        trace_id: str,
        seq: int,
        service_type: str,
        params: dict,
        snapshot: SystemSnapshot,
        system_prompt: str,
    ) -> AsyncIterator[SSEEvent]:
        del service_type, snapshot
        user_id = str(params.get("userId") or "00000000-0000-0000-0000-000000000001")
        next_seq = seq
        question_batch: dict[str, Any] | None = None
        question_batch_task = asyncio.create_task(
            self._run_agent_core_loop(
                params=params,
                system_prompt=system_prompt,
            )
        )
        try:
            while not question_batch_task.done():
                try:
                    question_batch = await asyncio.wait_for(
                        asyncio.shield(question_batch_task),
                        timeout=self.heartbeat_interval_seconds,
                    )
                    break
                except TimeoutError:
                    yield ProgressSSEEvent(
                        taskId=task_id,
                        traceId=trace_id,
                        seq=next_seq,
                        payload=ProgressPayload(
                            stage=self.stage_name,
                            percent=35,
                            message="练习题仍在生成中，请稍候",
                        ),
                    )
                    next_seq += 1
            else:
                question_batch = await question_batch_task
        except asyncio.CancelledError:
            await cancel_and_await(question_batch_task)
            raise

        if question_batch is None:
            question_batch = await question_batch_task
        params["practiceQuestionBatch"] = question_batch
        params["practiceQuestions"] = question_batch["questions"]
        persistence_task_id = (
            None
            if params.get("conversationTriggeredResourceGeneration") is True
            else task_id
        )
        params["practicePersistence"] = await self._safe_save_question_batch(
            user_id=user_id,
            task_id=persistence_task_id,
            question_batch=QuestionBatchPayload.model_validate(question_batch),
        )

        yield ProgressSSEEvent(
            taskId=task_id,
            traceId=trace_id,
            seq=next_seq,
            payload=ProgressPayload(
                stage=self.stage_name,
                percent=55,
                message="已生成练习题批次",
            ),
        )
        yield QuestionBatchSSEEvent(
            taskId=task_id,
            traceId=trace_id,
            seq=next_seq + 1,
            payload=QuestionBatchPayload.model_validate(question_batch),
        )

    async def _run_agent_core_loop(
        self,
        *,
        params: dict[str, Any],
        system_prompt: str,
    ) -> dict[str, Any]:
        existing_batch = self._existing_question_batch(params)
        if existing_batch is not None:
            return existing_batch

        # 步骤 1: 生成题目（1 次 LLM 调用）
        raw_batch = await self._tool_generate_questions(tool_input={}, params=params)

        # 步骤 2: 验证（确定性操作）
        validated = self._tool_validate_question(raw_batch, params=params)

        # 步骤 3: 格式化（确定性操作）
        formatted = self._tool_format_question_batch(tool_input=validated, params=params)
        formatted.update(self._build_question_provenance(params=params))
        return formatted

    async def _tool_generate_questions(
        self,
        *,
        tool_input: dict[str, Any],
        params: dict[str, Any],
    ) -> dict[str, Any]:
        del tool_input
        topic = self._resolve_topic(params)
        difficulty = self._resolve_difficulty(params)
        count = self._question_count(params)
        kwargs = {
            "topic": topic,
            "difficulty": difficulty,
            "count": count,
            "learning_context": params.get("learningContext", {}),
        }
        if self._supports_question_type_preference():
            kwargs["question_type_preference"] = self._question_type_preference(params)
        try:
            question_batch = await self.question_generator.generate_batch(**kwargs)
            return self._normalize_generated_question_batch(question_batch, params=params)
        except Exception as exc:
            raise RuntimeError(
                "Practice question LLM generation failed; template fallback is not allowed"
            ) from exc

    def _normalize_generated_question_batch(self, raw_output: Any, *, params: dict[str, Any]) -> dict[str, Any]:
        if hasattr(raw_output, "model_dump"):
            raw_output = raw_output.model_dump(by_alias=True)
        if isinstance(raw_output, list):
            raw_output = {"questions": raw_output}
        elif isinstance(raw_output, dict) and not isinstance(raw_output.get("questions"), list):
            if self._looks_like_question(raw_output):
                raw_output = {"questions": [raw_output]}
        if not isinstance(raw_output, dict):
            raise RuntimeError("练习题生成结果不是可识别的题批结构")
        topic = str(raw_output.get("topic") or self._resolve_topic(params))
        difficulty = str(raw_output.get("difficulty") or self._resolve_difficulty(params))
        return {
            **raw_output,
            "topic": topic,
            "difficulty": difficulty,
        }

    @staticmethod
    def _looks_like_question(payload: dict[str, Any]) -> bool:
        return any(key in payload for key in ("stem", "questionId", "questionType", "answer"))

    def _tool_validate_question(self, tool_input: dict[str, Any], *, params: dict[str, Any]) -> dict[str, Any]:
        questions = [
            PracticeQuestion.model_validate(question)
            for question in tool_input.get("questions", [])
        ]
        validated_questions = [
            question.model_dump(by_alias=True)
            for question in questions
            if question.stem and question.answer and question.knowledge_tags
        ]
        expected_count = self._question_count(params)
        if len(validated_questions) != expected_count:
            raise RuntimeError(
                f"练习题生成数量不符合要求：期望 {expected_count} 道，实际 {len(validated_questions)} 道"
            )
        self._validate_question_type_mix(validated_questions, params=params)
        return {
            "topic": tool_input.get("topic") or "",
            "difficulty": tool_input.get("difficulty", "MIXED"),
            "questions": validated_questions,
        }

    def _tool_format_question_batch(
        self,
        *,
        tool_input: dict[str, Any],
        params: dict[str, Any],
    ) -> dict[str, Any]:
        topic = str(tool_input.get("topic") or self._resolve_topic(params))
        difficulty = str(tool_input.get("difficulty") or self._resolve_difficulty(params))
        return QuestionBatchPayload(
            title=f"{topic} 练习题",
            topic=topic,
            difficulty=difficulty,
            questions=[
                PracticeQuestion.model_validate(question)
                for question in tool_input.get("questions", [])
            ],
        ).model_dump(by_alias=True)

    def _existing_question_batch(self, params: dict[str, Any]) -> dict[str, Any] | None:
        raw_batch = params.get("practiceQuestionBatch")
        if not isinstance(raw_batch, dict):
            return None
        questions = raw_batch.get("questions")
        if not isinstance(questions, list) or not questions:
            return None
        batch = QuestionBatchPayload.model_validate(raw_batch).model_dump(by_alias=True)
        return self._sanitize_existing_question_batch(batch)

    def _sanitize_existing_question_batch(self, batch: dict[str, Any]) -> dict[str, Any]:
        topic = str(batch.get("topic") or batch.get("title") or "").strip()
        if not topic:
            raise RuntimeError("练习题批次缺少真实主题")
        batch["title"] = f"{topic} 练习题"
        batch["submitLabel"] = None
        batch["assessmentDimension"] = None
        return batch

    def _build_question_provenance(self, *, params: dict[str, Any]) -> dict[str, Any]:
        return build_llm_provenance(
            agent_name=self.stage_name,
            generator=self.question_generator,
            params=params,
        )

    def _resolve_topic(self, params: dict[str, Any]) -> str:
        learning_context = params.get("learningContext", {})
        strict_candidates = [
            params.get("explicitUserTopic"),
            learning_context.get("explicitUserTopic") if isinstance(learning_context, dict) else None,
            params.get("activeLearningStepTitle"),
            learning_context.get("activeLearningStepTitle") if isinstance(learning_context, dict) else None,
            learning_context.get("activeLearningStep") if isinstance(learning_context, dict) else None,
            params.get("topic"),
            params.get("keyPoints"),
            params.get("knowledgePoint"),
            learning_context.get("knowledgePoint") if isinstance(learning_context, dict) else None,
            learning_context.get("chapter") if isinstance(learning_context, dict) else None,
            learning_context.get("course") if isinstance(learning_context, dict) else None,
        ]
        for candidate in strict_candidates:
            value = ResourceGenerationService._normalize_topic_candidate(candidate)
            if ResourceGenerationService._is_real_topic(value):
                return value
        for candidate in (params.get("rewrittenQuery"), params.get("query")):
            value = ResourceGenerationService._normalize_topic_candidate(candidate)
            if ResourceGenerationService._is_real_topic(value) and not ResourceGenerationService._looks_like_resource_command(value):
                return value
        raise RuntimeError("缺少练习题真实主题，禁止生成模板题")

    def _resolve_difficulty(self, params: dict[str, Any]) -> str:
        learning_context = params.get("learningContext", {})
        difficulty = params.get("difficulty")
        if not difficulty and isinstance(learning_context, dict):
            difficulty = learning_context.get("difficultyPreference")
        return str(difficulty or "MIXED")

    def _question_count(self, params: dict[str, Any]) -> int:
        learning_context = params.get("learningContext", {})
        raw_count = params.get("questionCount") or params.get("count")
        if raw_count is None and isinstance(learning_context, dict):
            raw_count = learning_context.get("questionCount")
        try:
            count = int(raw_count or 5)
        except (TypeError, ValueError):
            count = 5
        return max(1, min(count, 20))

    def _question_type_preference(self, params: dict[str, Any]) -> str | None:
        learning_context = params.get("learningContext", {})
        preference = params.get("questionTypePreference")
        if not preference and isinstance(learning_context, dict):
            preference = learning_context.get("questionTypePreference")
        normalized = str(preference or "").strip().upper()
        return normalized or None

    def _validate_question_type_mix(self, questions: list[dict[str, Any]], *, params: dict[str, Any]) -> None:
        preference = self._question_type_preference(params)
        question_types = {str(question.get("questionType") or "").strip().upper() for question in questions}
        objective_types = {"SINGLE_CHOICE"}
        subjective_types = {"SHORT_ANSWER"}
        unknown_types = question_types - objective_types - subjective_types
        if unknown_types:
            raise RuntimeError(f"练习题包含不支持的题型：{sorted(unknown_types)}")
        if preference in {"SINGLE_CHOICE", "OBJECTIVE", "CHOICE"}:
            if question_types - objective_types:
                raise RuntimeError("用户要求客观题，但生成结果包含主观题")
            return
        if preference in {"SHORT_ANSWER", "SUBJECTIVE"}:
            if question_types - subjective_types:
                raise RuntimeError("用户要求主观题，但生成结果包含客观题")
            return
        if len(questions) >= 2 and not (question_types & objective_types and question_types & subjective_types):
            raise RuntimeError("默认练习题必须混合客观题和主观题")

    def _supports_question_type_preference(self) -> bool:
        try:
            signature = inspect.signature(self.question_generator.generate_batch)
        except (TypeError, ValueError):
            return True
        return "question_type_preference" in signature.parameters

    async def _safe_save_question_batch(
        self,
        *,
        user_id: str,
        task_id: str | None,
        question_batch: QuestionBatchPayload,
    ) -> dict[str, Any]:
        try:
            metadata = await self.practice_store.save_question_batch(
                user_id=user_id,
                batch=question_batch,
                task_id=task_id,
            )
            return metadata
        except Exception as exc:
            raise RuntimeError("Practice question persistence failed; in-memory fallback is not allowed") from exc
