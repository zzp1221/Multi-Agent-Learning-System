"""基于 AgentCoreLoop 和 LLM 生成报告的评估 Agent。"""

from __future__ import annotations

from collections.abc import AsyncIterator
import logging
from typing import Any

from src.ai_modules.agents.base import PlaceholderAgent
from src.ai_modules.llms import EvaluationGenerator
from src.ai_modules.models import (
    EvaluationPayload,
    MasteryDiagnosisPayload,
    ProgressPayload,
    ProgressSSEEvent,
    ResultChunkPayload,
    ResultChunkSSEEvent,
    SSEEvent,
)
from src.ai_modules.prompts import build_evaluation_system_prompt
from src.ai_modules.runtime import (
    SystemSnapshot,
)
from src.ai_modules.runtime.skill_loader import SkillPromptLoader

LOGGER = logging.getLogger(__name__)
LEARNING_EFFECT_DIMENSION = "学习效果评估"
SUPPORTED_DIAGNOSIS_RESOURCE_TYPES = {"DOCUMENT", "VIDEO", "QUIZ", "CODE", "SLIDES", "MINDMAP", "READING"}
RESOURCE_TYPE_ALIASES = {
    "EXPLANATION": "DOCUMENT",
    "CODE_CASE": "CODE",
    "PRACTICAL_CASE": "CODE",
    "PPT": "SLIDES",
}


class EvaluationAgent(PlaceholderAgent):
    """评估学习者准备情况并为规划上下文提供输入。"""

    def __init__(
        self,
        generator: Any | None = None,
    ) -> None:
        super().__init__("Evaluation Agent", "evaluation")
        self.generator = generator
        self.skill_loader = SkillPromptLoader()

    def system_prompt(self, snapshot: SystemSnapshot) -> str:
        return self.skill_loader.build_system_prompt(
            skill_name="evaluation",
            snapshot=snapshot,
            fallback_prompt=build_evaluation_system_prompt(snapshot),
            component_name="evaluation_llm",
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
        del service_type
        payload = await self._run_agent_core_loop(
            params=params,
            snapshot=snapshot,
            system_prompt=system_prompt,
        )
        primary_dimension = LEARNING_EFFECT_DIMENSION
        evaluation_result = payload.model_dump(by_alias=True)
        params["evaluationResult"] = evaluation_result
        params["masteryDiagnosis"] = self._build_mastery_diagnosis(
            payload=payload,
            primary_dimension=primary_dimension,
            params=params,
            snapshot=snapshot,
        ).model_dump(by_alias=True)
        params["profileSource"] = "EVALUATION"

        yield ProgressSSEEvent(
            taskId=task_id,
            traceId=trace_id,
            seq=seq,
            payload=ProgressPayload(
                stage=self.stage_name,
                percent=45,
                message=f"已完成{primary_dimension}",
            ),
        )
        yield ResultChunkSSEEvent(
            taskId=task_id,
            traceId=trace_id,
            seq=seq + 1,
            payload=ResultChunkPayload(text=self._build_dimension_summary(primary_dimension, payload)),
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
        profile = self._resolve_profile_context(params)
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
            "profile": self._resolve_profile_context(params),
            "learningContext": params.get("learningContext", {}),
            "assessmentDimensions": [LEARNING_EFFECT_DIMENSION],
            "outputGuidance": self._build_output_guidance(),
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

    def _resolve_profile_context(self, params: dict[str, Any]) -> dict[str, Any]:
        profile = params.get("profile") if isinstance(params.get("profile"), dict) else {}
        profile_analysis = params.get("profileAnalysis") if isinstance(params.get("profileAnalysis"), dict) else {}
        if not profile_analysis:
            return dict(profile)
        merged = self._merge_non_empty(profile, profile_analysis)
        if profile_analysis.get("weakPoints") and not profile_analysis.get("knowledgeGaps"):
            merged["knowledgeGaps"] = profile_analysis["weakPoints"]
        if profile_analysis.get("learningPreference") and not profile_analysis.get("preferredStyle"):
            merged["preferredStyle"] = profile_analysis["learningPreference"]
        return merged

    def _build_output_guidance(self) -> dict[str, Any]:
        return {
            "mode": "learning_effect_evaluation",
            "detailLevel": "high",
            "minSummaryCharacters": 260,
            "minDimensionEvidenceCharacters": 220,
            "minRecommendationCharacters": 180,
            "rubric": [
                "学习行为：结合对话、学习节奏、复习记录判断学生是否按计划推进。",
                "练习测试：结合正确率、薄弱标签、作答反馈判断知识掌握变化。",
                "资源反馈：结合资源类型偏好、使用频次、下载和完成情况判断推送是否有效。",
                "动态调整：根据评估结果给出学习计划和资源推送策略的下一轮调整建议。",
            ],
            "avoid": "不要暴露历史拆分维度名称。",
        }

    def _build_dimension_summary(
        self,
        dimension: str,
        payload: EvaluationPayload,
    ) -> str:
        return f"{dimension}已完成。{payload.summary_text}"

    def _build_mastery_diagnosis(
        self,
        *,
        payload: EvaluationPayload,
        primary_dimension: str,
        params: dict[str, Any],
        snapshot: SystemSnapshot,
    ) -> MasteryDiagnosisPayload:
        aggregated = params.get("aggregatedEvaluationContext")
        if not isinstance(aggregated, dict):
            aggregated = {}
        behavior = aggregated.get("behaviorSignals") if isinstance(aggregated.get("behaviorSignals"), dict) else {}
        judge_result = params.get("judgeResult") if isinstance(params.get("judgeResult"), dict) else {}
        profile = self._resolve_profile_context(params)
        learning_context = params.get("learningContext") if isinstance(params.get("learningContext"), dict) else {}
        knowledge_points = self._unique_items(
            [
                *payload.weaknesses,
                *payload.next_focus,
                *list(judge_result.get("weakKnowledgeTags", [])),
                *list(profile.get("weakPoints", [])),
                *list(profile.get("knowledgeGaps", [])),
                *list(snapshot.knowledge_gaps),
                snapshot.current_chapter,
            ]
        )
        behavior_signals = {
            "practiceAccuracy": self._safe_float(judge_result.get("accuracy")),
            "recentQuestionCount": self._count_recent_questions(params),
            "reviewCount": self._count_recent_reviews(params, snapshot),
            "resourceDownloads": self._count_resource_downloads(params),
            "messageCount": int(behavior.get("messageCount") or 0),
            "recentMistakeCount": len(snapshot.recent_mistakes),
        }
        diagnoses = [
            self._build_knowledge_diagnosis(
                knowledge_point=knowledge_point,
                index=index,
                payload=payload,
                params=params,
                snapshot=snapshot,
                behavior_signals=behavior_signals,
            )
            for index, knowledge_point in enumerate(knowledge_points[:5], start=1)
        ]
        if not diagnoses:
            diagnoses = [
                self._build_knowledge_diagnosis(
                    knowledge_point="核心概念",
                    index=1,
                    payload=payload,
                    params=params,
                    snapshot=snapshot,
                    behavior_signals=behavior_signals,
                )
            ]
        average_score = sum(item["masteryScore"] for item in diagnoses) / max(len(diagnoses), 1)
        confidence = self._diagnosis_confidence(
            has_judge_result=bool(judge_result),
            has_profile=bool(profile),
            has_snapshot=bool(snapshot.knowledge_gaps or snapshot.recent_mistakes),
            behavior_signals=behavior_signals,
        )
        weak_items = [item["knowledgePoint"] for item in diagnoses if item["status"] == "weak"]
        return MasteryDiagnosisPayload.model_validate(
            {
                "diagnosisSource": "evaluation",
                "primaryDimension": primary_dimension,
                "overallLevel": payload.overall_level,
                "overallMasteryScore": round(average_score, 2),
                "confidence": confidence,
                "targetScope": {
                    "course": learning_context.get("course") or snapshot.current_course,
                    "chapter": learning_context.get("chapter") or snapshot.current_chapter,
                    "knowledgePoints": [item["knowledgePoint"] for item in diagnoses],
                },
                "knowledgeDiagnoses": diagnoses,
                "behaviorSignals": behavior_signals,
                "planAdjustmentHints": {
                    "shouldRefreshPlan": bool(weak_items or self._low_accuracy(behavior_signals.get("practiceAccuracy"))),
                    "refreshReason": "、".join(weak_items[:3]) if weak_items else "评估结果显示可继续优化学习方案",
                    "strategy": self._plan_adjustment_strategy(weak_items, payload),
                },
                "summaryText": payload.summary_text,
            }
        )

    def _build_knowledge_diagnosis(
        self,
        *,
        knowledge_point: str,
        index: int,
        payload: EvaluationPayload,
        params: dict[str, Any],
        snapshot: SystemSnapshot,
        behavior_signals: dict[str, Any],
    ) -> dict[str, Any]:
        evidence = self._collect_diagnosis_evidence(
            knowledge_point=knowledge_point,
            payload=payload,
            params=params,
            snapshot=snapshot,
            behavior_signals=behavior_signals,
        )
        error_patterns = self._collect_error_patterns(knowledge_point=knowledge_point, params=params, snapshot=snapshot)
        mastery_score = self._estimate_mastery_score(
            knowledge_point=knowledge_point,
            payload=payload,
            behavior_signals=behavior_signals,
            evidence_count=len(evidence),
            error_count=len(error_patterns),
        )
        status = "weak" if mastery_score < 0.6 else "developing" if mastery_score < 0.8 else "stable"
        return {
            "knowledgePoint": knowledge_point,
            "masteryScore": mastery_score,
            "status": status,
            "priority": index,
            "evidence": evidence,
            "errorPatterns": error_patterns,
            "nextFocus": self._next_focus_for_knowledge_point(knowledge_point, payload),
            "recommendedResourceTypes": self._recommended_resource_types(params, payload),
        }

    def _collect_diagnosis_evidence(
        self,
        *,
        knowledge_point: str,
        payload: EvaluationPayload,
        params: dict[str, Any],
        snapshot: SystemSnapshot,
        behavior_signals: dict[str, Any],
    ) -> list[str]:
        judge_result = params.get("judgeResult") if isinstance(params.get("judgeResult"), dict) else {}
        profile = self._resolve_profile_context(params)
        evidence: list[str] = []
        accuracy = behavior_signals.get("practiceAccuracy")
        if knowledge_point in judge_result.get("weakKnowledgeTags", []):
            if isinstance(accuracy, float):
                evidence.append(f"练习判题标记为薄弱知识点，正确率 {accuracy:.0%}")
            else:
                evidence.append("练习判题标记为薄弱知识点")
        for dimension in payload.dimensions:
            text = str(dimension.evidence or "").strip()
            dimension_text = f"{dimension.name} {dimension.recommendation} {text}"
            if text and knowledge_point in dimension_text:
                evidence.append(f"{dimension.name}维度证据：{text}")
        if knowledge_point in profile.get("knowledgeGaps", []) or knowledge_point in profile.get("weakPoints", []):
            evidence.append("学习画像记录为薄弱点")
        if knowledge_point in snapshot.knowledge_gaps:
            evidence.append("运行时画像快照记录为知识缺口")
        for mistake in snapshot.recent_mistakes:
            text = str(mistake or "").strip()
            if text and knowledge_point in text:
                evidence.append(f"最近错因记录提到：{text}")
                break
        return self._unique_items(evidence)[:4]

    def _collect_error_patterns(
        self,
        *,
        knowledge_point: str,
        params: dict[str, Any],
        snapshot: SystemSnapshot,
    ) -> list[str]:
        profile = self._resolve_profile_context(params)
        patterns: list[str] = []
        for item in profile.get("errorPatterns", []):
            if isinstance(item, dict):
                topic = str(item.get("topic") or "").strip()
                description = str(item.get("pattern") or item.get("description") or "").strip()
                if knowledge_point in topic or knowledge_point in description:
                    patterns.append(description or topic)
            else:
                text = str(item or "").strip()
                if text and knowledge_point in text:
                    patterns.append(text)
        for mistake in snapshot.recent_mistakes:
            text = str(mistake or "").strip()
            if text and knowledge_point in text:
                patterns.append(text)
        return self._unique_items(patterns)[:3]

    def _estimate_mastery_score(
        self,
        *,
        knowledge_point: str,
        payload: EvaluationPayload,
        behavior_signals: dict[str, Any],
        evidence_count: int,
        error_count: int,
    ) -> float:
        level = str(payload.overall_level or "").upper()
        base_by_level = {
            "BEGINNER": 0.35,
            "BASIC": 0.48,
            "INTERMEDIATE": 0.65,
            "ADVANCED": 0.82,
            "EXPERT": 0.9,
        }
        score = base_by_level.get(level, 0.55)
        accuracy = behavior_signals.get("practiceAccuracy")
        if isinstance(accuracy, float):
            score = (score + max(0.0, min(1.0, accuracy))) / 2
        if knowledge_point in payload.weaknesses:
            score -= 0.12
        if knowledge_point in payload.next_focus:
            score -= 0.06
        score -= min(evidence_count, 3) * 0.03
        score -= min(error_count, 2) * 0.04
        return round(max(0.0, min(1.0, score)), 2)

    def _recommended_resource_types(self, params: dict[str, Any], payload: EvaluationPayload) -> list[str]:
        raw_types: list[Any] = []
        resource_types = params.get("resourceTypes")
        if isinstance(resource_types, list):
            raw_types.extend(resource_types)
        resource_type = params.get("resourceType")
        if resource_type:
            raw_types.append(resource_type)
        raw_types.extend(["DOCUMENT", "QUIZ"])
        if payload.overall_level.upper() in {"BEGINNER", "BASIC"}:
            raw_types.append("VIDEO")
        normalized = []
        for raw in raw_types:
            resource_type_text = str(raw or "").strip().upper()
            resource_type_text = RESOURCE_TYPE_ALIASES.get(resource_type_text, resource_type_text)
            if resource_type_text in SUPPORTED_DIAGNOSIS_RESOURCE_TYPES:
                normalized.append(resource_type_text)
        return self._unique_items(normalized)[:4]

    def _next_focus_for_knowledge_point(self, knowledge_point: str, payload: EvaluationPayload) -> str:
        for focus in payload.next_focus:
            if knowledge_point in focus or focus in knowledge_point:
                return focus
        return f"先补齐 {knowledge_point} 的关键概念，再完成针对性练习"

    def _plan_adjustment_strategy(self, weak_items: list[str], payload: EvaluationPayload) -> str:
        if weak_items:
            return f"优先补齐 {weak_items[0]}，再用练习题校准掌握度"
        if payload.next_focus:
            return f"围绕 {payload.next_focus[0]} 做巩固和迁移训练"
        return "保持当前路径，增加阶段性检查点"

    def _diagnosis_confidence(
        self,
        *,
        has_judge_result: bool,
        has_profile: bool,
        has_snapshot: bool,
        behavior_signals: dict[str, Any],
    ) -> float:
        confidence = 0.45
        if has_judge_result:
            confidence += 0.2
        if has_profile:
            confidence += 0.12
        if has_snapshot:
            confidence += 0.1
        if behavior_signals.get("resourceDownloads"):
            confidence += 0.05
        if behavior_signals.get("messageCount"):
            confidence += 0.05
        return round(min(confidence, 0.9), 2)

    def _count_recent_questions(self, params: dict[str, Any]) -> int:
        batch = params.get("practiceQuestionBatch")
        if isinstance(batch, dict) and isinstance(batch.get("questions"), list):
            return len(batch["questions"])
        questions = params.get("practiceQuestions")
        return len(questions) if isinstance(questions, list) else 0

    def _count_recent_reviews(self, params: dict[str, Any], snapshot: SystemSnapshot) -> int:
        candidates = params.get("reviewRecords")
        count = len(candidates) if isinstance(candidates, list) else 0
        return count + sum(1 for item in snapshot.recent_activities if "复盘" in str(item))

    def _count_resource_downloads(self, params: dict[str, Any]) -> int:
        candidates = params.get("resourceUsageFeedback")
        if isinstance(candidates, list):
            return sum(1 for item in candidates if isinstance(item, dict) and item.get("downloaded") is True)
        downloads = params.get("resourceDownloads")
        if isinstance(downloads, list):
            return len(downloads)
        if isinstance(downloads, int):
            return max(downloads, 0)
        return 0

    def _low_accuracy(self, accuracy: Any) -> bool:
        return isinstance(accuracy, float) and accuracy < 0.75

    def _safe_float(self, value: Any) -> float | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return max(0.0, min(1.0, float(value)))
        if isinstance(value, str):
            try:
                parsed = float(value)
            except ValueError:
                return None
            return max(0.0, min(1.0, parsed))
        return None

    def _looks_like_active_question(self, text: str) -> bool:
        normalized = str(text).strip()
        if not normalized:
            return False
        return any(
            token in normalized
            for token in ["?", "？", "怎么", "为什么", "如何", "吗", "能否", "可不可以", "区别", "是否"]
        )

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

    def _merge_non_empty(self, base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
        merged = dict(base)
        for key, value in incoming.items():
            if value is None or value == "" or value == [] or value == {}:
                continue
            merged[key] = value
        return merged
