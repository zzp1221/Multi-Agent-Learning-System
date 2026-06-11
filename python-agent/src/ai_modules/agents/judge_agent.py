"""基于 AgentCoreLoop 和结构化评分输出的判题 Agent。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from src.ai_modules.agents.base import PlaceholderAgent
from src.ai_modules.async_utils import cancel_and_await
from src.ai_modules.llms import (
    JudgeFeedbackGenerator,
    SubjectiveJudgeEvaluatorFactory,
)
from src.ai_modules.memory import InMemoryPracticeStore, PostgresPracticeStore, PracticeStore
from src.ai_modules.models import (
    JudgeItemResult,
    JudgeResultPayload,
    JudgeResultSSEEvent,
    PracticeQuestion,
    ProgressPayload,
    ProgressSSEEvent,
    SSEEvent,
    SubjectiveJudgeEvaluation,
)
from src.ai_modules.prompts import build_judge_system_prompt
from src.ai_modules.runtime import SystemSnapshot
from src.ai_modules.runtime.provenance import ProvenanceError, validate_llm_provenance
from src.ai_modules.runtime.skill_loader import SkillPromptLoader


class JudgeAgent(PlaceholderAgent):
    """评判学习者答案并总结影响画像的差异。"""

    def __init__(
        self,
        practice_store: PracticeStore | None = None,
        subjective_evaluator: Any | None = None,
        feedback_generator: Any | None = None,
        heartbeat_interval_seconds: float = 15.0,
    ) -> None:
        super().__init__("Judge Agent", "judge")
        self.practice_store = practice_store or PostgresPracticeStore()
        self.fallback_practice_store = InMemoryPracticeStore()
        self.subjective_evaluator = subjective_evaluator
        self.feedback_generator = feedback_generator or JudgeFeedbackGenerator()
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.skill_loader = SkillPromptLoader()

    def system_prompt(self, snapshot: SystemSnapshot) -> str:
        return self.skill_loader.build_system_prompt(
            skill_name="judge",
            snapshot=snapshot,
            fallback_prompt=build_judge_system_prompt(snapshot),
            component_name="judge_llm",
            ability_key="ability:assessment",
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
        judge_result: dict[str, Any] | None = None
        judge_result_task = asyncio.create_task(
            self._run_agent_core_loop(
                task_id=task_id,
                user_id=user_id,
                params=params,
                system_prompt=system_prompt,
            )
        )
        try:
            while not judge_result_task.done():
                try:
                    judge_result = await asyncio.wait_for(
                        asyncio.shield(judge_result_task),
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
                            percent=70,
                            message="判题仍在执行中，请稍候",
                        ),
                    )
                    next_seq += 1
            else:
                judge_result = await judge_result_task
        except asyncio.CancelledError:
            await cancel_and_await(judge_result_task)
            raise

        if judge_result is None:
            judge_result = await judge_result_task
        params["judgeResult"] = judge_result
        params["profileSource"] = "PRACTICE"

        yield ProgressSSEEvent(
            taskId=task_id,
            traceId=trace_id,
            seq=next_seq,
            payload=ProgressPayload(
                stage=self.stage_name,
                percent=80,
                message="已完成判题并生成反馈",
            ),
        )
        yield JudgeResultSSEEvent(
            taskId=task_id,
            traceId=trace_id,
            seq=next_seq + 1,
            payload=JudgeResultPayload.model_validate(judge_result),
        )

    async def _run_agent_core_loop(
        self,
        *,
        task_id: str,
        user_id: str,
        params: dict[str, Any],
        system_prompt: str,
    ) -> dict[str, Any]:
        del system_prompt
        return await self._run_direct_judge_pipeline(
            task_id=task_id,
            user_id=user_id,
            params=params,
        )

    async def _run_direct_judge_pipeline(
        self,
        *,
        task_id: str,
        user_id: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        self._validate_reused_question_batch_provenance(params)
        objective = await self._tool_grade_objective(tool_input={}, params=params)
        judged = await self._tool_evaluate_subjective(tool_input=objective, params=params)
        self._validate_complete_judge_items(params=params, judge_items=judged.get("items", []))
        feedback = await self._tool_generate_feedback(tool_input=judged, params=params)
        return await self._tool_save_practice_result(
            tool_input=feedback,
            task_id=task_id,
            user_id=user_id,
            params=params,
        )

    async def _tool_grade_objective(
        self,
        *,
        tool_input: dict[str, Any],
        params: dict[str, Any],
    ) -> dict[str, Any]:
        del tool_input
        questions = self._questions(params)
        answers = self._answers(params)
        objective_results: list[dict[str, Any]] = []
        subjective_questions: list[dict[str, Any]] = []
        for question in questions:
            answer = answers.get(question.question_id, "")
            if question.question_type == "SHORT_ANSWER":
                subjective_questions.append(question.model_dump(by_alias=True))
                continue
            is_correct = self._is_objective_answer_correct(question=question, learner_answer=answer)
            score_unit = self._question_score_unit(params)
            objective_results.append(
                JudgeItemResult(
                    questionId=question.question_id,
                    questionType=question.question_type,
                    learnerAnswer=answer,
                    correctAnswer=question.answer,
                    isCorrect=is_correct,
                    score=score_unit if is_correct else 0.0,
                    knowledgeTags=question.knowledge_tags,
                    reason="答案匹配标准答案" if is_correct else "答案与标准答案不一致",
                    feedback="这道客观题判断正确。" if is_correct else "先回到题目条件，确认再作答。",
                    profileDelta=self._build_profile_delta(question=question, is_correct=is_correct),
                ).model_dump(by_alias=True)
            )
        return {"items": objective_results, "pendingSubjective": subjective_questions}

    async def _tool_evaluate_subjective(
        self,
        *,
        tool_input: dict[str, Any],
        params: dict[str, Any],
    ) -> dict[str, Any]:
        judged_items = list(tool_input.get("items", []))
        for question_payload in tool_input.get("pendingSubjective", []):
            question = PracticeQuestion.model_validate(question_payload)
            learner_answer = self._answers(params).get(question.question_id, "")
            evaluation = await self._evaluate_subjective(
                question=question,
                learner_answer=learner_answer,
            )
            score_unit = self._question_score_unit(params)
            judged_items.append(
                JudgeItemResult(
                    questionId=question.question_id,
                    questionType=question.question_type,
                    learnerAnswer=learner_answer,
                    correctAnswer=question.answer,
                    isCorrect=evaluation.is_correct,
                    score=self._normalize_subjective_score(evaluation.score, score_unit),
                    knowledgeTags=question.knowledge_tags,
                    reason=evaluation.reason,
                    feedback=evaluation.feedback,
                    profileDelta=self._build_profile_delta(
                        question=question,
                        is_correct=evaluation.is_correct,
                        confidence_level=evaluation.confidence_level,
                    ),
                ).model_dump(by_alias=True)
            )
        return {"items": judged_items}

    def _question_score_unit(self, params: dict[str, Any]) -> float:
        questions = self._questions(params)
        if self._is_stage_test(params):
            return round(100.0 / max(len(questions), 1), 2)
        return 20.0

    def _normalize_subjective_score(self, score: float, score_unit: float) -> float:
        if score_unit == 20.0:
            return score
        return round(max(0.0, min(float(score), 20.0)) / 20.0 * score_unit, 2)

    def _is_stage_test(self, params: dict[str, Any]) -> bool:
        return str(params.get("purpose") or "").strip().upper() == "STAGE_TEST"

    async def _tool_generate_feedback(
        self,
        *,
        tool_input: dict[str, Any],
        params: dict[str, Any],
    ) -> dict[str, Any]:
        items = [JudgeItemResult.model_validate(item) for item in tool_input.get("items", [])]
        total_score = sum(item.score for item in items)
        accuracy = sum(1 for item in items if item.is_correct) / max(len(items), 1)
        incorrect_tags = [
            tag
            for item in items
            if not item.is_correct
            for tag in item.knowledge_tags
        ]
        try:
            topic = (
                params.get("topic")
                or params.get("query")
                or tool_input.get("topic")
                or "当前主题"
            )
            feedback = await self.feedback_generator.summarize(items=items, topic=str(topic))
            if not isinstance(feedback, dict):
                raise TypeError("judge feedback must be a dict")
            feedback["items"] = [item.model_dump(by_alias=True) for item in items]
            feedback["totalScore"] = round(total_score, 2)
            feedback["accuracy"] = round(accuracy, 4)
            feedback["weakKnowledgeTags"] = list(dict.fromkeys(incorrect_tags))
            return feedback
        except Exception:
            full_score = 20.0 * len(items) if items else 1.0
            summary = (
                f"本次共判定 {len(items)} 题，得分 {total_score:.1f} / {full_score:.1f}，"
                f"正确率 {accuracy:.0%}。"
            )
            return {
                "summary": summary,
                "totalScore": round(total_score, 2),
                "accuracy": round(accuracy, 4),
                "items": [item.model_dump(by_alias=True) for item in items],
                "weakKnowledgeTags": list(dict.fromkeys(incorrect_tags)),
            }

    async def _tool_save_practice_result(
        self,
        *,
        tool_input: dict[str, Any],
        task_id: str,
        user_id: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        batch = params.get("practiceQuestionBatch", {})
        topic = str(batch.get("topic") or params.get("topic") or params.get("query") or "当前主题")
        judge_payload = JudgeResultPayload(
            title=f"{topic} 判题结果",
            summary=str(tool_input.get("summary", "判题完成。")),
            totalScore=float(tool_input.get("totalScore", 0.0)),
            accuracy=float(tool_input.get("accuracy", 0.0)),
            items=[
                JudgeItemResult.model_validate(item)
                for item in tool_input.get("items", [])
            ],
        )
        payload = judge_payload.model_dump(by_alias=True)
        payload["taskId"] = task_id
        payload["weakKnowledgeTags"] = list(tool_input.get("weakKnowledgeTags", []))
        persistence_metadata = await self._safe_save_judge_result(
            user_id=user_id,
            answers=self._answers(params),
            judge_result=judge_payload,
            persistence_metadata=params.get("practicePersistence"),
        )
        params["practiceJudgePersistence"] = persistence_metadata
        payload["persistence"] = persistence_metadata
        return payload

    def _questions(self, params: dict[str, Any]) -> list[PracticeQuestion]:
        raw_questions = (
            params.get("practiceQuestions")
            or params.get("practiceQuestionBatch", {}).get("questions", [])
        )
        return [PracticeQuestion.model_validate(question) for question in raw_questions]

    def _answers(self, params: dict[str, Any]) -> dict[str, str]:
        raw_answers = params.get("answers", {})
        if isinstance(raw_answers, dict):
            return {str(key): str(value) for key, value in raw_answers.items()}
        if isinstance(raw_answers, list):
            normalized: dict[str, str] = {}
            for item in raw_answers:
                if not isinstance(item, dict):
                    continue
                question_id = item.get("questionId") or item.get("id")
                if question_id is None:
                    continue
                normalized[str(question_id)] = str(item.get("answer", ""))
            return normalized
        return {}

    def _validate_reused_question_batch_provenance(self, params: dict[str, Any]) -> None:
        raw_batch = params.get("practiceQuestionBatch")
        if not isinstance(raw_batch, dict):
            return
        try:
            validate_llm_provenance(raw_batch, artifact_label="practiceQuestionBatch")
        except ProvenanceError as exc:
            raise RuntimeError(f"题批缺少 LLM 来源元数据: {exc}") from exc

    def _validate_complete_judge_items(
        self,
        *,
        params: dict[str, Any],
        judge_items: list[Any],
    ) -> None:
        questions = self._questions(params)
        judged_question_ids = {
            str(JudgeItemResult.model_validate(item).question_id)
            for item in judge_items
        }
        expected_question_ids = {question.question_id for question in questions}
        if len(judge_items) != len(questions) or judged_question_ids != expected_question_ids:
            missing_ids = sorted(expected_question_ids - judged_question_ids)
            extra_ids = sorted(judged_question_ids - expected_question_ids)
            raise RuntimeError(
                "判题结果不完整"
                f"：缺少 {missing_ids or '无'}，多余 {extra_ids or '无'}"
            )

    def _is_objective_answer_correct(
        self,
        *,
        question: PracticeQuestion,
        learner_answer: str,
    ) -> bool:
        learner_candidates = self._answer_candidates(learner_answer, question.options)
        correct_candidates = self._answer_candidates(question.answer, question.options)
        return bool(learner_candidates & correct_candidates)

    def _answer_candidates(self, value: str, options: list[str]) -> set[str]:
        text = str(value or "").strip()
        if not text:
            return {""}
        candidates = {self._normalize_text(text), self._normalize_option_label(text)}
        label = self._extract_option_label(text)
        if label:
            candidates.add(label)
            option_index = ord(label) - ord("A")
            if 0 <= option_index < len(options):
                candidates.add(self._normalize_text(options[option_index]))
                candidates.add(self._normalize_option_label(options[option_index]))
        for index, option in enumerate(options):
            option_label = chr(ord("A") + index)
            normalized_option = self._normalize_text(option)
            normalized_option_label = self._normalize_option_label(option)
            if self._normalize_text(text) in {normalized_option, normalized_option_label}:
                candidates.add(option_label)
                candidates.add(normalized_option)
                candidates.add(normalized_option_label)
        return {candidate for candidate in candidates if candidate}

    def _extract_option_label(self, value: str) -> str:
        normalized = str(value or "").strip().lstrip("(（").lstrip().upper()
        if not normalized:
            return ""
        label = normalized[0]
        if not "A" <= label <= "Z":
            return ""
        if len(normalized) == 1:
            return label
        delimiter = normalized[1]
        if delimiter.isspace() or delimiter in {".", "．", "、", ":", "：", ")", "）"}:
            return label
        return ""

    def _normalize_option_label(self, value: str) -> str:
        text = str(value or "").strip()
        label = self._extract_option_label(text)
        if not label:
            return self._normalize_text(text)
        rest = text.lstrip()
        if rest[:1].upper() != label:
            return self._normalize_text(text)
        rest = rest[1:].lstrip(" .．、:：)）（(")
        return self._normalize_text(rest or label)

    def _build_profile_delta(
        self,
        *,
        question: PracticeQuestion,
        is_correct: bool,
        confidence_level: str | None = None,
    ) -> dict[str, str | list[str]]:
        if is_correct:
            return {"confidenceLevel": confidence_level or "MEDIUM"}
        return {
            "confidenceLevel": confidence_level or "LOW",
            "weakPoints": question.knowledge_tags,
        }

    async def _evaluate_subjective(
        self,
        *,
        question: PracticeQuestion,
        learner_answer: str,
    ) -> SubjectiveJudgeEvaluation:
        if self.subjective_evaluator is None:
            self.subjective_evaluator = SubjectiveJudgeEvaluatorFactory.create()
        return await self.subjective_evaluator.evaluate(
            question=question,
            learner_answer=learner_answer,
        )

    def _normalize_text(self, value: str) -> str:
        text = "".join(str(value).strip().upper().split())
        punctuation = ".,，。:：;；、()（）[]【】{}《》<>"
        return "".join(char for char in text if char not in punctuation)

    async def _safe_save_judge_result(
        self,
        *,
        user_id: str,
        answers: dict[str, str],
        judge_result: JudgeResultPayload,
        persistence_metadata: dict[str, Any] | None,
    ) -> dict[str, Any]:
        try:
            return await self.practice_store.save_judge_result(
                user_id=user_id,
                answers=answers,
                judge_result=judge_result,
                persistence_metadata=persistence_metadata,
            )
        except Exception:
            return await self.fallback_practice_store.save_judge_result(
                user_id=user_id,
                answers=answers,
                judge_result=judge_result,
                persistence_metadata=persistence_metadata,
            )
