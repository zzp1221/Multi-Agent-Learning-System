"""基于 AgentCoreLoop 和 LLM 生成报告的评估 Agent。"""

from __future__ import annotations

from collections.abc import AsyncIterator
import logging
from typing import Any

from src.ai_modules.agents.base import PlaceholderAgent
from src.ai_modules.llms import EvaluationGenerator, PracticeQuestionGenerator
from src.ai_modules.models import (
    EvaluationPayload,
    ProgressPayload,
    ProgressSSEEvent,
    QuestionBatchPayload,
    QuestionBatchSSEEvent,
    ResourceFilePayload,
    ResourceFileSSEEvent,
    ResultChunkPayload,
    ResultChunkSSEEvent,
    SSEEvent,
)
from src.ai_modules.prompts import build_evaluation_system_prompt
from src.ai_modules.runtime import (
    SystemSnapshot,
)
from src.ai_modules.runtime.provenance import build_llm_provenance, validate_llm_provenance
from src.ai_modules.runtime.skill_loader import SkillPromptLoader

LOGGER = logging.getLogger(__name__)
INTERACTIVE_DIMENSIONS = {"案例迁移", "练习掌握"}
PROFILE_ASSESSMENT_DIMENSIONS = {"学习主动性", "复盘闭环"}


class EvaluationAgent(PlaceholderAgent):
    """评估学习者准备情况并为规划上下文提供输入。"""

    def __init__(
        self,
        llm_client: Any | None = None,
        generator: Any | None = None,
        question_generator: Any | None = None,
    ) -> None:
        super().__init__("Evaluation Agent", "evaluation")
        self.llm_client = llm_client
        self.generator = generator
        self.question_generator = question_generator or PracticeQuestionGenerator()
        self.skill_loader = SkillPromptLoader()

    def system_prompt(self, snapshot: SystemSnapshot) -> str:
        return self.skill_loader.build_system_prompt(
            skill_name="evaluation",
            snapshot=snapshot,
            fallback_prompt=build_evaluation_system_prompt(snapshot),
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
        del service_type
        payload = await self._run_agent_core_loop(
            params=params,
            snapshot=snapshot,
            system_prompt=system_prompt,
        )
        evaluation_provenance = params.get("evaluationProvenance")
        if not isinstance(evaluation_provenance, dict):
            raise RuntimeError("Evaluation LLM provenance is missing")
        primary_dimension = self._resolve_primary_dimension(params)
        params["evaluationResult"] = payload.model_dump(by_alias=True)
        params["profileSource"] = "EVALUATION"
        report_markdown = self._render_dimension_report(
            dimension=primary_dimension,
            payload=payload,
            params=params,
            snapshot=snapshot,
        )
        question_batch = await self._build_practice_question_batch(
            dimension=primary_dimension,
            payload=payload,
            params=params,
            snapshot=snapshot,
        )
        if question_batch is not None:
            params["practiceQuestionBatch"] = question_batch.model_dump(by_alias=True)
            params["practiceQuestions"] = [
                question.model_dump(by_alias=True)
                for question in question_batch.questions
            ]

        yield ProgressSSEEvent(
            taskId=task_id,
            traceId=trace_id,
            seq=seq,
            payload=ProgressPayload(
                stage=self.stage_name,
                percent=45,
                message=f"已完成{primary_dimension}专项评估",
            ),
        )
        resource_payload = ResourceFilePayload(
            assetType="DOCUMENT",
            title=f"{primary_dimension}专项评估",
            summary=payload.summary_text,
            displayMode="MARKDOWN_CARD",
            fileName="",
            localPath=None,
            mimeType="text/markdown; charset=UTF-8",
            inlineContent=report_markdown,
            **evaluation_provenance,
        )
        validate_llm_provenance(resource_payload, artifact_label=f"{self.stage_name}:evaluation_report")
        yield ResourceFileSSEEvent(
            taskId=task_id,
            traceId=trace_id,
            seq=seq + 1,
            payload=resource_payload,
        )
        yield ResultChunkSSEEvent(
            taskId=task_id,
            traceId=trace_id,
            seq=seq + 2,
            payload=ResultChunkPayload(text=self._build_dimension_summary(primary_dimension, payload, question_batch)),
        )
        if question_batch is not None:
            validate_llm_provenance(question_batch, artifact_label=f"{self.stage_name}:assessment_questions")
            yield QuestionBatchSSEEvent(
                taskId=task_id,
                traceId=trace_id,
                seq=seq + 3,
                payload=question_batch,
            )

    async def _run_agent_core_loop(
        self,
        *,
        params: dict[str, Any],
        snapshot: SystemSnapshot,
        system_prompt: str,
    ) -> EvaluationPayload:
        aggregated = self._tool_aggregate_behavior(tool_input={}, params=params, snapshot=snapshot)
        return await self._safe_evaluate(
            params=params,
            snapshot=snapshot,
            system_prompt=system_prompt,
            aggregated_behavior=aggregated,
        )

    def _tool_aggregate_behavior(
        self,
        *,
        tool_input: dict[str, Any],
        params: dict[str, Any],
        snapshot: SystemSnapshot,
    ) -> dict[str, Any]:
        del tool_input
        profile = params.get("profile", {})
        evaluation = params.get("evaluationResult", {})
        judge_result = params.get("judgeResult", {})
        messages = params.get("messages", [])
        structured_summary = params.get("structuredConversationSummary", {})

        weaknesses = self._unique_items(
            [
                *list(profile.get("knowledgeGaps", [])),
                *list(evaluation.get("weaknesses", [])),
                *list(judge_result.get("weakKnowledgeTags", [])),
                *list(snapshot.knowledge_gaps),
            ]
        )
        focus = self._unique_items(
            [
                *list(evaluation.get("nextFocus", [])),
                *weaknesses[:3],
                snapshot.current_chapter,
            ]
        )
        strengths = self._unique_items(
            [
                *list(evaluation.get("strengths", [])),
                "愿意持续练习" if params.get("practiceQuestionBatch") else "",
                "具备学习上下文" if params.get("learningContext") else "",
                "最近有复习记录" if snapshot.recent_activities else "",
            ]
        )
        learner_messages = [
            str(message.get("content", ""))
            for message in messages
            if isinstance(message, dict) and message.get("role") == "user"
        ]
        learner_questions = [
            content
            for content in learner_messages
            if self._looks_like_active_question(content)
        ]
        aggregated = {
            "profile": profile,
            "learningContext": params.get("learningContext", {}),
            "judgeResult": judge_result,
            "messages": messages,
            "structuredConversationSummary": structured_summary,
            "snapshot": {
                "studentLevel": snapshot.student_level,
                "knowledgeGaps": snapshot.knowledge_gaps,
                "recentMistakes": snapshot.recent_mistakes,
                "preferredStyle": snapshot.preferred_style,
            },
            "behaviorSignals": {
                "messageCount": len(messages),
                "learnerQuestionCount": len(learner_questions),
                "recentMistakeCount": len(snapshot.recent_mistakes),
                "practiceAccuracy": judge_result.get("accuracy"),
                "conversationKeywords": learner_questions[-3:] or learner_messages[-3:],
            },
            "candidateStrengths": strengths or ["愿意配合学习"],
            "candidateWeaknesses": weaknesses or ["薄弱点待补充"],
            "recommendedFocus": focus or ["核心概念", "适用条件"],
        }
        params["aggregatedEvaluationContext"] = aggregated
        return aggregated

    async def _tool_generate_report(
        self,
        *,
        tool_input: dict[str, Any],
        params: dict[str, Any],
        snapshot: SystemSnapshot,
        system_prompt: str,
    ) -> dict[str, Any]:
        aggregated = params.get("aggregatedEvaluationContext") or tool_input
        payload = await self._safe_evaluate(
            params=params,
            snapshot=snapshot,
            system_prompt=system_prompt,
            aggregated_behavior=aggregated if isinstance(aggregated, dict) else {},
        )
        return payload.model_dump(by_alias=True)

    async def _safe_evaluate(
        self,
        *,
        params: dict[str, Any],
        snapshot: SystemSnapshot,
        system_prompt: str,
        aggregated_behavior: dict[str, Any],
    ) -> EvaluationPayload:
        generator = self.generator or EvaluationGenerator()
        try:
            payload = await generator.evaluate(
                system_prompt=system_prompt,
                context_payload=self._build_context_payload(
                    params=params,
                    snapshot=snapshot,
                    aggregated_behavior=aggregated_behavior,
                ),
            )
            params["evaluationProvenance"] = build_llm_provenance(
                agent_name=self.stage_name,
                generator=generator,
                params=params,
            )
            return payload
        except Exception as exc:
            LOGGER.exception("Evaluation LLM failed")
            raise RuntimeError("Evaluation LLM failed") from exc

    def _build_context_payload(
        self,
        *,
        params: dict[str, Any],
        snapshot: SystemSnapshot,
        aggregated_behavior: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "profile": params.get("profile", {}),
            "learningContext": params.get("learningContext", {}),
            "assessmentDimensions": self._resolve_context_dimensions(params),
            "outputGuidance": self._build_output_guidance(params),
            "judgeResult": params.get("judgeResult", {}),
            "messages": params.get("messages", []),
            "snapshot": {
                "studentLevel": snapshot.student_level,
                "knowledgeGaps": snapshot.knowledge_gaps,
                "recentMistakes": snapshot.recent_mistakes,
                "preferredStyle": snapshot.preferred_style,
            },
            "aggregatedBehavior": aggregated_behavior,
        }

    def _resolve_primary_dimension(self, params: dict[str, Any]) -> str:
        dimensions = params.get("dimensions")
        if isinstance(dimensions, list):
            for item in dimensions:
                text = str(item).strip()
                if text:
                    return text
        text = str(params.get("assessmentDimension") or "").strip()
        return text or "知识基础"

    def _resolve_context_dimensions(self, params: dict[str, Any]) -> list[str]:
        dimensions = params.get("dimensions")
        if isinstance(dimensions, list):
            normalized = [str(item).strip() for item in dimensions if str(item).strip()]
            if normalized:
                return normalized
        text = str(params.get("assessmentDimension") or "").strip()
        return [text] if text else ["知识基础"]

    def _build_output_guidance(self, params: dict[str, Any]) -> dict[str, Any]:
        dimension = self._resolve_primary_dimension(params)
        if dimension == "学习主动性":
            return {
                "mode": "profile_behavior_assessment",
                "detailLevel": "high",
                "minSummaryCharacters": 260,
                "minDimensionEvidenceCharacters": 220,
                "minRecommendationCharacters": 180,
                "rubric": [
                    "目标拆解：是否会主动把学习任务拆成可验证的小目标。",
                    "主动追问：是否能在不确定时提出具体问题，而不是等待系统继续投喂。",
                    "自我验证：是否主动安排自测、复述、对照例题或检查点。",
                    "策略调整：效果一般时是否会主动调整资源类型、学习节奏和问题粒度。",
                ],
                "avoid": "不要写成刷题掌握度，也不要复用复盘闭环的错因追踪话术。",
            }
        if dimension == "复盘闭环":
            return {
                "mode": "profile_behavior_assessment",
                "detailLevel": "high",
                "minSummaryCharacters": 260,
                "minDimensionEvidenceCharacters": 220,
                "minRecommendationCharacters": 180,
                "rubric": [
                    "错因定位：是否能把错误归因到概念、条件、步骤或审题，而不是只写粗心。",
                    "纠错动作：是否把旧错转成下次可执行的检查清单或对照样例。",
                    "间隔复测：是否安排回看、重做、复测和错题状态更新。",
                    "迁移防错：是否能把一个错误模式迁移到新题型前主动预警。",
                ],
                "avoid": "不要写成学习主动性，也不要只评价是否愿意学习。",
            }
        return {
            "mode": "standard_assessment",
            "detailLevel": "normal",
        }

    async def _build_practice_question_batch(
        self,
        *,
        dimension: str,
        payload: EvaluationPayload,
        params: dict[str, Any],
        snapshot: SystemSnapshot,
    ) -> QuestionBatchPayload | None:
        topic = self._resolve_assessment_topic(params, snapshot, payload)
        difficulty = self._resolve_practice_difficulty(payload, snapshot)
        focus_items = self._resolve_focus_items(payload, snapshot)
        learning_context = params.get("learningContext", {})
        if dimension in INTERACTIVE_DIMENSIONS:
            try:
                generated = await self.question_generator.generate_batch(
                    topic=f"{dimension}：{topic}",
                    difficulty=difficulty,
                    count=3,
                    learning_context={
                        **(learning_context if isinstance(learning_context, dict) else {}),
                        "assessmentDimension": dimension,
                        "focusItems": focus_items[:3],
                        "evaluationSummary": payload.summary_text,
                    },
                )
            except Exception as exc:
                raise RuntimeError(
                    "Evaluation question LLM generation failed; deterministic fallback is not allowed"
                ) from exc
            batch_payload = generated.model_dump(by_alias=True)
            batch_payload.update(
                {
                    "title": f"{topic} {dimension}专项评估",
                    "topic": topic,
                    "difficulty": difficulty,
                    "description": "系统已围绕当前评估维度生成 1-3 道测评题，请直接作答后查看专项判断。",
                    "assessmentDimension": dimension,
                    "submitLabel": f"提交{dimension}评估",
                    **build_llm_provenance(
                        agent_name=self.stage_name,
                        generator=self.question_generator,
                        params=params,
                    ),
                }
            )
            return QuestionBatchPayload.model_validate(batch_payload)
        return None

    def _resolve_assessment_topic(
        self,
        params: dict[str, Any],
        snapshot: SystemSnapshot,
        payload: EvaluationPayload,
    ) -> str:
        learning_context = params.get("learningContext", {})
        if isinstance(learning_context, dict):
            for key in ("chapter", "course"):
                value = str(learning_context.get(key) or "").strip()
                if value:
                    return value
        for candidate in (
            *payload.next_focus,
            snapshot.current_chapter,
            *(snapshot.knowledge_gaps or []),
        ):
            text = str(candidate or "").strip()
            if text:
                return text
        return "当前主题"

    def _resolve_practice_difficulty(self, payload: EvaluationPayload, snapshot: SystemSnapshot) -> str:
        level = str(payload.overall_level or snapshot.student_level or "BASIC").upper()
        if level in {"BEGINNER", "BASIC"}:
            return "BASIC"
        if level in {"ADVANCED", "EXPERT"}:
            return "ADVANCED"
        return "INTERMEDIATE"

    def _build_dimension_summary(
        self,
        dimension: str,
        payload: EvaluationPayload,
        question_batch: QuestionBatchPayload | None,
    ) -> str:
        if question_batch is not None:
            return (
                f"{dimension}专项评估已完成初步诊断，并生成 {len(question_batch.questions)} 道互动评估题。"
                "请直接在页面作答，系统会结合你的作答过程给出更准确的专项判断。"
            )
        return f"{dimension}专项评估已完成。{payload.summary_text}"

    def _render_dimension_report(
        self,
        *,
        dimension: str,
        payload: EvaluationPayload,
        params: dict[str, Any],
        snapshot: SystemSnapshot,
    ) -> str:
        list_limit = 5 if dimension in PROFILE_ASSESSMENT_DIMENSIONS else 3
        strengths = payload.strengths[:list_limit] or ["愿意继续学习并完成当前评估"]
        weaknesses = payload.weaknesses[:list_limit] or ["薄弱点待结合后续作答继续细化"]
        next_focus = payload.next_focus[:list_limit] or ["核心概念", "适用条件"]
        dimension_lines = self._render_dimension_specific_lines(dimension, payload, params, snapshot)
        return "\n".join(
            [
                f"## {dimension}结果",
                f"- 当前水平：{payload.overall_level}",
                f"- 结论：{payload.summary_text}",
                "### 你目前做得好的地方",
                *[f"- {item}" for item in strengths],
                "### 当前最需要补强的点",
                *[f"- {item}" for item in weaknesses],
                "### 接下来优先做什么",
                *[f"- {item}" for item in next_focus],
                *dimension_lines,
            ]
        )

    def _render_dimension_specific_lines(
        self,
        dimension: str,
        payload: EvaluationPayload,
        params: dict[str, Any],
        snapshot: SystemSnapshot,
    ) -> list[str]:
        focus = payload.next_focus[:2] or payload.weaknesses[:2] or ["核心概念"]
        recent_mistakes = [str(item).strip() for item in snapshot.recent_mistakes if str(item).strip()]
        behavior = params.get("aggregatedEvaluationContext", {}).get("behaviorSignals", {})
        if dimension == "知识基础":
            return [
                "### 怎么理解这次结果",
                f"- 当前重点不是继续刷题数量，而是先把 {focus[0]} 的定义、作用和适用条件说清楚。",
                "- 如果一个知识点只能记住结论、不能解释为什么成立，通常说明基础还没真正稳住。",
                "### 你现在可以怎么做",
                f"- 不看资料，先用自己的话解释“{focus[0]}”是什么、什么时候用。",
                f"- 再做一道最小例题，做之前先判断 {focus[0]} 的使用前提。",
            ]
        if dimension == "案例迁移":
            return [
                "### 怎么理解这次结果",
                f"- 这次更关注你能不能把 {focus[0]} 放到新场景里继续正确使用，而不是只会复述原题做法。",
                "### 你现在可以怎么做",
                f"- 尝试换一个题目条件，重新判断 {focus[0]} 还能不能直接使用。",
                f"- 自己举一个相似但不完全相同的新案例，再说明思路哪里需要调整。",
            ]
        if dimension == "学习主动性":
            learner_questions = behavior.get("conversationKeywords") or []
            learner_question_text = "；".join(str(item) for item in learner_questions[:3] if str(item).strip())
            return [
                "### 怎么理解这次结果",
                f"- 当前记录到的主动提问线索约为 {behavior.get('learnerQuestionCount') or 0} 次；该维度重点看你会不会主动拆目标、安排验证并提出追问。",
                f"- 最近可参考的提问/表达线索：{learner_question_text or '暂无足够清晰的主动追问样本'}。",
                "- 如果学习过程主要表现为“等系统给下一步”，主动性会偏弱；如果能自己提出假设、验证方法和卡点问题，主动性会明显增强。",
                "### 本次画像判断的四个观察点",
                "- 目标拆解：是否把“学会一个主题”拆成定义、条件、例题、反例和自测标准。",
                "- 主动追问：是否能把不懂的问题问具体，例如问“哪一步失效”而不是只说“我不会”。",
                "- 自我验证：是否主动安排复述、最小例题、变式判断或阶段性自测。",
                "- 策略调整：学习效果一般时，是否会主动换资源、缩小目标或调整学习节奏。",
                "### 你现在可以怎么做",
                f"- 下一轮先围绕 {focus[0]} 写一个 15 分钟学习目标，并提前写好“验证方式”和“卡住时要问的问题”。",
                "- 学完后立刻做一次自我检查：我能不能不用资料复述？能不能举一个反例？能不能解释为什么这一步成立？",
                "- 如果检查不过，不要继续堆内容，先把目标缩小到一个条件或一个步骤，再向系统发起更具体的追问。",
                "### 下一轮可直接照做的行动脚本",
                f"- 开始前：我今天只解决“{focus[0]}”中的一个具体卡点，完成标准是能说清判断步骤。",
                "- 学习中：每 10-15 分钟停一次，写下“我现在确定了什么、还不确定什么”。",
                "- 结束后：主动提交一个追问或一个自测结论，让系统根据你的真实卡点继续调整。",
            ]
        if dimension == "复盘闭环":
            return [
                "### 怎么理解这次结果",
                f"- 最近关联到的错误线索：{', '.join(recent_mistakes[:3]) or '暂无显式错题记录'}。",
                "- 该维度不是看你有没有出错，而是看你能不能把旧错变成下次可执行的检查动作。",
                "- 复盘闭环强的学生通常会留下“错误原因 -> 修正动作 -> 下次检查点 -> 复测结果”的链条；只看答案解析但不沉淀动作，闭环会偏弱。",
                "### 本次画像判断的四个观察点",
                "- 错因定位：能否把错误归到概念混淆、条件遗漏、步骤顺序、审题误判或表达不完整。",
                "- 纠错动作：能否把一个错因改写成下次做题前/做题后的检查清单。",
                "- 间隔复测：是否安排隔天或下一轮重做，而不是当天看懂后就结束。",
                "- 迁移防错：能否把同类错误迁移到新场景前提前提醒自己。",
                "### 你现在可以怎么做",
                "- 先写出最近一次错误的真正原因，不要只写“粗心”。",
                f"- 再为 {focus[0]} 写一个做题前/做题后的检查清单，避免重复犯错。",
                "- 每次订正后补一句“下次遇到什么信号就要停下来检查”，把复盘从回忆变成动作。",
                "### 下一轮可直接照做的闭环模板",
                "- 错题记录：我错在什么条件、哪一步判断、为什么当时会这样想。",
                "- 修正动作：下次看到同类条件时，先检查哪一项，再决定是否继续计算或作答。",
                "- 复测安排：隔 1 天重做原题，隔 3 天做一道变式题，并记录是否还会犯同类错误。",
                "- 迁移提醒：把这次错因写成一句短规则，放到下一次同主题练习前先读一遍。",
            ]
        return [
            "### 怎么理解这次结果",
            f"- 当前围绕 {focus[0]}、{focus[1] if len(focus) > 1 else focus[0]} 进行专项评估。",
            "- 页面下方已生成互动题，作答后系统会结合你的回答给出更具体的专项判断。",
        ]

    def _resolve_focus_items(self, payload: EvaluationPayload, snapshot: SystemSnapshot) -> list[str]:
        candidates = self._unique_items(
            [
                *payload.next_focus,
                *payload.weaknesses,
                *snapshot.knowledge_gaps,
                snapshot.current_chapter,
                "核心概念",
            ]
        )
        return candidates or ["核心概念"]

    def _resolve_transfer_scene(self, params: dict[str, Any], snapshot: SystemSnapshot) -> str:
        learning_context = params.get("learningContext", {})
        if isinstance(learning_context, dict):
            chapter = str(learning_context.get("chapter") or "").strip()
            course = str(learning_context.get("course") or "").strip()
            if chapter and course:
                return f"{course} 的 {chapter} 变式场景"
            if chapter:
                return f"{chapter} 的实际应用场景"
            if course:
                return f"{course} 的综合应用场景"
        if snapshot.current_chapter:
            return f"{snapshot.current_chapter} 的综合应用场景"
        return "新的实际应用场景"

    def _resolve_recent_mistake(self, snapshot: SystemSnapshot, fallback: str) -> str:
        for item in snapshot.recent_mistakes:
            text = str(item or "").strip()
            if text:
                return text
        return fallback

    def _markdown_list(self, items: list[str]) -> list[str]:
        normalized = [f"- {item}" for item in items if str(item).strip()]

    def _looks_like_active_question(self, text: str) -> bool:
        normalized = str(text).strip()
        if not normalized:
            return False
        return any(
            token in normalized
            for token in ["?", "？", "怎么", "为什么", "如何", "吗", "能否", "可不可以", "区别", "是否"]
        )
        return normalized or ["- 暂无明显信号"]

    def _unique_items(self, items: list[Any]) -> list[str]:
        seen: set[str] = set()
        normalized: list[str] = []
        for item in items:
            text = str(item).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            normalized.append(text)
        return normalized
