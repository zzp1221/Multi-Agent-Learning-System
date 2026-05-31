"""基于 AgentCoreLoop 和结构化评分输出的判题 Agent。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from src.ai_modules.agents.base import PlaceholderAgent
from src.ai_modules.async_utils import cancel_and_await
from src.ai_modules.llms import (
    JudgeLLMClientFactory,
    JudgeFeedbackGenerator,
    ObjectiveJudgeGenerator,
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
    SpecializedAnalysisPayload,
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
        llm_client: Any | None = None,
        practice_store: PracticeStore | None = None,
        subjective_evaluator: Any | None = None,
        objective_judge_generator: Any | None = None,
        feedback_generator: Any | None = None,
        heartbeat_interval_seconds: float = 15.0,
    ) -> None:
        super().__init__("Judge Agent", "judge")
        self.llm_client = llm_client or JudgeLLMClientFactory.create()
        self.practice_store = practice_store or PostgresPracticeStore()
        self.fallback_practice_store = InMemoryPracticeStore()
        self.subjective_evaluator = subjective_evaluator
        self.objective_judge_generator = objective_judge_generator or ObjectiveJudgeGenerator()
        self.feedback_generator = feedback_generator or JudgeFeedbackGenerator()
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.skill_loader = SkillPromptLoader()

    def system_prompt(self, snapshot: SystemSnapshot) -> str:
        return self.skill_loader.build_system_prompt(
            skill_name="judge",
            snapshot=snapshot,
            fallback_prompt=build_judge_system_prompt(snapshot),
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
            objective_results.append(
                JudgeItemResult(
                    questionId=question.question_id,
                    questionType=question.question_type,
                    learnerAnswer=answer,
                    correctAnswer=question.answer,
                    isCorrect=is_correct,
                    score=20.0 if is_correct else 0.0,
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
            judged_items.append(
                JudgeItemResult(
                    questionId=question.question_id,
                    questionType=question.question_type,
                    learnerAnswer=learner_answer,
                    correctAnswer=question.answer,
                    isCorrect=evaluation.is_correct,
                    score=evaluation.score,
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
        assessment_dimension = str(
            params.get("assessmentDimension")
            or batch.get("assessmentDimension")
            or ""
        ).strip()
        specialized_analysis = self._build_specialized_analysis(
            tool_input=tool_input,
            params=params,
            topic=topic,
            assessment_dimension=assessment_dimension,
        )
        judge_payload = JudgeResultPayload(
            title=f"{topic} 判题结果",
            summary=str(tool_input.get("summary", "判题完成。")),
            totalScore=float(tool_input.get("totalScore", 0.0)),
            accuracy=float(tool_input.get("accuracy", 0.0)),
            assessmentDimension=assessment_dimension,
            specializedAnalysis=specialized_analysis,
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

    def _build_specialized_analysis(
        self,
        *,
        tool_input: dict[str, Any],
        params: dict[str, Any],
        topic: str,
        assessment_dimension: str,
    ) -> SpecializedAnalysisPayload | None:
        if not assessment_dimension:
            return None
        items = [JudgeItemResult.model_validate(item) for item in tool_input.get("items", [])]
        answers = self._answers(params)
        answer_text = "\n".join(
            answer.strip()
            for answer in answers.values()
            if str(answer).strip()
        )
        accuracy = float(tool_input.get("accuracy", 0.0) or 0.0)
        weak_tags = list(dict.fromkeys(
            tag
            for item in items
            if not item.is_correct
            for tag in item.knowledge_tags
        ))

        if assessment_dimension == "学习主动性":
            return self._build_initiative_analysis(topic=topic, accuracy=accuracy, answer_text=answer_text)
        if assessment_dimension == "复盘闭环":
            return self._build_review_analysis(topic=topic, accuracy=accuracy, answer_text=answer_text)
        if assessment_dimension == "知识基础":
            return self._build_foundation_analysis(topic=topic, accuracy=accuracy, weak_tags=weak_tags, answer_text=answer_text)
        return self._build_generic_assessment_analysis(
            topic=topic,
            assessment_dimension=assessment_dimension,
            accuracy=accuracy,
            weak_tags=weak_tags,
        )

    def _build_initiative_analysis(
        self,
        *,
        topic: str,
        accuracy: float,
        answer_text: str,
    ) -> SpecializedAnalysisPayload:
        has_goal = self._contains_any(answer_text, ["先", "第一步", "目标", "计划", "补"])
        has_validation = self._contains_any(answer_text, ["验证", "自测", "检查", "练习", "测试"])
        has_question = self._contains_any(answer_text, ["问", "请教", "为什么", "如何", "哪里", "卡住"])
        has_adjustment = self._contains_any(answer_text, ["调整", "更换", "增加", "缩小", "补做", "复习"])
        signal_count = sum([has_goal, has_validation, has_question, has_adjustment])

        if signal_count >= 4:
            summary = "你的回答已经体现出较强的学习主动性，能够主动拆解目标、设计验证方式，并预留卡住后的追问与调整动作。"
        elif signal_count >= 2:
            summary = "你的回答已经有主动学习意识，但计划还不够完整，尤其需要把验证动作和卡住后的追问写得更具体。"
        else:
            summary = "当前回答更像是笼统想法，还没有形成清晰的主动学习计划，后续容易重新回到被动等待内容的状态。"

        strengths = [
            item
            for item, present in [
                ("能先说明下一轮要补什么", has_goal),
                ("有意识安排验证或自测动作", has_validation),
                ("已经考虑主动提问或追问", has_question),
                ("知道效果不佳时要调整策略", has_adjustment),
            ]
            if present
        ] or ["愿意配合作答并思考下一步学习动作"]
        weaknesses = [
            item
            for item, present in [
                ("计划步骤还不够具体，执行时可能落空", not has_goal),
                ("缺少明确验证动作，难以判断是否真的学会", not has_validation),
                ("卡住后的追问意识不足，容易再次被动等待答案", not has_question),
                ("对效果不佳后的调整预案不足", not has_adjustment),
            ]
            if present
        ]
        next_actions = [
            "把下一轮学习计划固定写成“先补什么 -> 怎么验证 -> 卡住问什么”三行",
            "每次学习后至少做一次自测或口头复述，避免只看不验",
            "如果效果仍不理想，优先调整资源类型或缩小目标范围再学一轮",
        ]
        markdown = "\n".join(
            [
                f"## {topic} 学习主动性分析",
                f"- 当前判断：{summary}",
                f"- 作答表现：正确率 {accuracy:.0%}，重点看计划是否完整，而不是只看对错。",
                "### 你已经做到的",
                *[f"- {item}" for item in strengths],
                "### 当前短板",
                *[f"- {item}" for item in (weaknesses or ['主动性结构已较完整，可继续保持'])],
                "### 下一步建议",
                *[f"- {item}" for item in next_actions],
            ]
        )
        return SpecializedAnalysisPayload(
            title=f"{topic} 学习主动性分析",
            summary=summary,
            dimension="学习主动性",
            strengths=strengths,
            weaknesses=weaknesses,
            nextActions=next_actions,
            markdown=markdown,
        )

    def _build_review_analysis(
        self,
        *,
        topic: str,
        accuracy: float,
        answer_text: str,
    ) -> SpecializedAnalysisPayload:
        has_real_cause = not self._contains_any(answer_text, ["粗心", "马虎"]) or self._contains_any(
            answer_text,
            ["忽略", "混淆", "顺序", "条件", "边界", "验证", "理解不清"],
        )
        has_fix_action = self._contains_any(answer_text, ["改正", "检查", "清单", "重做", "复习", "标记"])
        has_recheck = self._contains_any(answer_text, ["再次验证", "复查", "下次", "做题前", "做题后", "验证"])
        signal_count = sum([has_real_cause, has_fix_action, has_recheck])

        if signal_count >= 3:
            summary = "你的复盘已经比较接近完整闭环，能够从具体错因出发，落到改正动作，并考虑下一次如何验证自己不再犯同类错误。"
        elif signal_count == 2:
            summary = "你的复盘已经有雏形，但闭环还差一步，通常是“错因写了、动作也写了，但缺少下次验证机制”。"
        else:
            summary = "当前回答还停留在泛泛复述，复盘没有形成“错因 -> 改正动作 -> 再验证”的完整闭环。"

        strengths = [
            item
            for item, present in [
                ("能定位到较具体的错误原因", has_real_cause),
                ("已经提出纠错动作或检查清单", has_fix_action),
                ("考虑了下次做题前后的验证方式", has_recheck),
            ]
            if present
        ] or ["愿意回顾最近一次错误"]
        weaknesses = [
            item
            for item, present in [
                ("错因仍偏笼统，建议明确到概念、条件或步骤层面", not has_real_cause),
                ("纠错动作还不够可执行，建议写成检查清单", not has_fix_action),
                ("缺少再验证动作，闭环还没有真正形成", not has_recheck),
            ]
            if present
        ]
        next_actions = [
            "每次复盘都至少写清一个具体错因，避免只写“粗心”",
            "把改正动作写成 2-3 步检查清单，做到下次可直接照着执行",
            "下次做同类题时，先按清单自检，再在做题后复查一遍",
        ]
        markdown = "\n".join(
            [
                f"## {topic} 复盘闭环分析",
                f"- 当前判断：{summary}",
                f"- 作答表现：正确率 {accuracy:.0%}，该维度更关注复盘闭环质量。",
                "### 你已经做到的",
                *[f"- {item}" for item in strengths],
                "### 当前短板",
                *[f"- {item}" for item in (weaknesses or ['复盘闭环已较完整，可继续保持'])],
                "### 下一步建议",
                *[f"- {item}" for item in next_actions],
            ]
        )
        return SpecializedAnalysisPayload(
            title=f"{topic} 复盘闭环分析",
            summary=summary,
            dimension="复盘闭环",
            strengths=strengths,
            weaknesses=weaknesses,
            nextActions=next_actions,
            markdown=markdown,
        )

    def _build_foundation_analysis(
        self,
        *,
        topic: str,
        accuracy: float,
        weak_tags: list[str],
        answer_text: str,
    ) -> SpecializedAnalysisPayload:
        has_own_explanation = len(answer_text.strip()) >= 20
        if accuracy >= 0.75:
            summary = "你对当前知识点已经有基础理解，下一步重点是把概念解释、适用条件和错因修正进一步说清楚。"
        elif accuracy >= 0.4:
            summary = "你对当前知识点有部分理解，但基础还不稳，尤其需要补齐概念解释和适用条件判断。"
        else:
            summary = "当前更像是基础概念尚未建立完成，建议先回到定义、作用和适用场景，再进入做题。"

        strengths = []
        if accuracy >= 0.6:
            strengths.append("基础概念已有初步掌握")
        if has_own_explanation:
            strengths.append("能够尝试用自己的话说明知识点")
        if not strengths:
            strengths.append("愿意完成基础评估作答")

        weaknesses = []
        if accuracy < 0.75:
            weaknesses.append("对关键概念或适用条件的判断还不够稳定")
        if weak_tags:
            weaknesses.append(f"当前薄弱点集中在：{'、'.join(weak_tags[:3])}")
        if not has_own_explanation:
            weaknesses.append("回答偏短，知识解释还不够展开")

        next_actions = [
            f"先用自己的话解释“{topic}”是什么、有什么作用、什么时候用",
            "补一个最小例题，并在做题前先判断适用条件",
            "做完题后复述一次错因，避免把概念问题误以为只是粗心",
        ]
        markdown = "\n".join(
            [
                f"## {topic} 知识基础分析",
                f"- 当前判断：{summary}",
                f"- 作答表现：正确率 {accuracy:.0%}。",
                "### 你已经做到的",
                *[f"- {item}" for item in strengths],
                "### 当前短板",
                *[f"- {item}" for item in weaknesses],
                "### 下一步建议",
                *[f"- {item}" for item in next_actions],
            ]
        )
        return SpecializedAnalysisPayload(
            title=f"{topic} 知识基础分析",
            summary=summary,
            dimension="知识基础",
            strengths=strengths,
            weaknesses=weaknesses,
            nextActions=next_actions,
            markdown=markdown,
        )

    def _build_generic_assessment_analysis(
        self,
        *,
        topic: str,
        assessment_dimension: str,
        accuracy: float,
        weak_tags: list[str],
    ) -> SpecializedAnalysisPayload:
        summary = (
            f"{assessment_dimension}专项作答已完成，当前正确率为 {accuracy:.0%}。"
            "系统已根据作答情况整理出下一步改进方向。"
        )
        weaknesses = [f"需继续关注：{'、'.join(weak_tags[:3])}"] if weak_tags else []
        next_actions = ["结合逐题反馈复盘一次，再进行下一轮专项练习"]
        markdown = "\n".join(
            [
                f"## {topic} {assessment_dimension}分析",
                f"- 当前判断：{summary}",
                *[f"- {item}" for item in weaknesses],
                "### 下一步建议",
                *[f"- {item}" for item in next_actions],
            ]
        )
        return SpecializedAnalysisPayload(
            title=f"{topic} {assessment_dimension}分析",
            summary=summary,
            dimension=assessment_dimension,
            strengths=["已完成专项作答"],
            weaknesses=weaknesses,
            nextActions=next_actions,
            markdown=markdown,
        )

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

    def _contains_any(self, text: str, keywords: list[str]) -> bool:
        lowered = str(text).lower()
        return any(str(keyword).lower() in lowered for keyword in keywords)

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
