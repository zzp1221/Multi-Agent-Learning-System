"""基于 AgentCoreLoop 和画像持久化工具的画像 Agent。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

from src.ai_modules.agents.base import PlaceholderAgent
from src.ai_modules.async_utils import cancel_and_await
from src.ai_modules.llms import ProfileAnalyzer, ProfileLLMClientFactory
from src.ai_modules.memory import (
    InMemoryProfileStore,
    LearnerKnowledgeGraphStore,
    MongoConversationSummaryStore,
    PostgresProfileStore,
    ProfileStore,
)
from src.ai_modules.models import (
    LearnerProfileSnapshot,
    ProgressPayload,
    ProgressSSEEvent,
    ResultChunkPayload,
    ResultChunkSSEEvent,
    SSEEvent,
)
from src.ai_modules.models.profile import (
    CurrentGoalProfile,
    ErrorPattern,
    LearnerProfileDimensions,
    LearningHabitsProfile,
    WeakPointDetail,
)
from src.ai_modules.prompts import build_profile_system_prompt
from src.ai_modules.runtime import (
    RecoveryEngine,
    RecoveryFailureType,
    SystemSnapshot,
)
from src.ai_modules.runtime.skill_loader import SkillPromptLoader

LOGGER = logging.getLogger(__name__)


class ProfileAgent(PlaceholderAgent):
    """从对话中提取并持久化学习者画像维度。"""

    def __init__(
        self,
        profile_store: ProfileStore | None = None,
        summary_store: Any | None = None,
        llm_client: Any | None = None,
        profile_analyzer: Any | None = None,
        heartbeat_interval_seconds: float = 15.0,
        knowledge_graph_store: LearnerKnowledgeGraphStore | None = None,
    ) -> None:
        super().__init__("Profile Agent", "profiling")
        self.profile_store = profile_store or PostgresProfileStore()
        self.summary_store = summary_store or MongoConversationSummaryStore()
        self.fallback_profile_store = InMemoryProfileStore()
        self.llm_client = llm_client or ProfileLLMClientFactory.create()
        self.profile_analyzer = profile_analyzer or ProfileAnalyzer()
        self.recovery_engine = RecoveryEngine()
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.knowledge_graph_store = knowledge_graph_store or LearnerKnowledgeGraphStore()
        self.skill_loader = SkillPromptLoader()

    def system_prompt(self, snapshot: SystemSnapshot) -> str:
        return self.skill_loader.build_system_prompt(
            skill_name="profile",
            snapshot=snapshot,
            fallback_prompt=build_profile_system_prompt(snapshot),
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
        core_loop_result: dict[str, Any] | None = None
        profile_task = asyncio.create_task(
            self._run_agent_core_loop(
                user_id=user_id,
                params=params,
                system_prompt=system_prompt,
            )
        )
        try:
            while not profile_task.done():
                try:
                    core_loop_result = await asyncio.wait_for(
                        asyncio.shield(profile_task),
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
                            percent=88,
                            message="画像更新仍在执行中，请稍候",
                        ),
                    )
                    next_seq += 1
            else:
                core_loop_result = await profile_task
        except asyncio.CancelledError:
            await cancel_and_await(profile_task)
            raise

        if core_loop_result is None:
            core_loop_result = await profile_task
        params["profileUpdate"] = core_loop_result
        params["profileAnalysis"] = self._build_profile_analysis(core_loop_result)

        yield ProgressSSEEvent(
            taskId=task_id,
            traceId=trace_id,
            seq=next_seq,
            payload=ProgressPayload(
                stage=self.stage_name,
                percent=90,
                message="已完成画像分析并写入快照",
            ),
        )
        yield ResultChunkSSEEvent(
            taskId=task_id,
            traceId=trace_id,
            seq=next_seq + 1,
            payload=ResultChunkPayload(
                text=core_loop_result["summaryText"],
            ),
        )

    def _build_profile_analysis(self, core_loop_result: dict[str, Any]) -> dict[str, Any]:
        dimensions = core_loop_result.get("dimensions")
        if not isinstance(dimensions, dict):
            dimensions = {}
        return {
            "summaryText": str(core_loop_result.get("summaryText") or "").strip(),
            "professionalBackground": dimensions.get("professionalBackground"),
            "studentLevel": (
                dimensions.get("knowledgeFoundation")
                or dimensions.get("knowledgeBase")
                or dimensions.get("studentLevel")
            ),
            "learningPreference": dimensions.get("learningPreference"),
            "explanationPreference": dimensions.get("explanationPreference"),
            "preferredResourceTypes": list(dimensions.get("preferredResourceTypes") or []),
            "weakPoints": list(dimensions.get("weakPoints") or dimensions.get("knowledgeGaps") or []),
            "weakPointDetails": list(dimensions.get("weakPointDetails") or []),
            "skillMastery": dimensions.get("skillMastery") if isinstance(dimensions.get("skillMastery"), dict) else {},
            "currentGoal": dimensions.get("currentGoal") if isinstance(dimensions.get("currentGoal"), dict) else {},
            "inferredRecommendations": list(dimensions.get("inferredRecommendations") or []),
            "source": dimensions.get("source"),
        }

    async def _run_agent_core_loop(
        self,
        *,
        user_id: str,
        params: dict[str, Any],
        system_prompt: str,
    ) -> dict[str, Any]:
        # 步骤 1: 读取画像（数据库读取，确定性操作）
        await self._tool_read_profile(tool_input={}, user_id=user_id)

        # 步骤 2: 分析对话（1 次 LLM 调用）
        await self._tool_analyze_dialogue(tool_input={}, params=params)

        # 步骤 3: 更新画像（数据库写入，确定性操作）
        result = await self._tool_update_profile(tool_input={}, user_id=user_id, params=params)

        # 步骤 4: 同步掌握度到知识图谱节点（后台，失败不影响主链路）
        skill_mastery = (result.get("dimensions") or {}).get("skillMastery") or {}
        if skill_mastery:
            await self._sync_mastery_to_graph(user_id=user_id, skill_mastery=skill_mastery)

        return result

    async def _tool_read_profile(
        self,
        *,
        tool_input: dict[str, Any],
        user_id: str,
    ) -> dict[str, Any]:
        del tool_input
        snapshot = await self._safe_read_profile(user_id)
        if snapshot is None:
            return {"exists": False}
        return {
            "exists": True,
            "userId": snapshot.user_id,
            "version": snapshot.version,
            "profile": snapshot.profile.model_dump(by_alias=True, mode="json"),
        }

    async def _tool_analyze_dialogue(
        self,
        *,
        tool_input: dict[str, Any],
        params: dict[str, Any],
    ) -> dict[str, Any]:
        del tool_input
        messages = params.get("messages") or params.get("conversation") or []
        structured_summary = params.get("structuredConversationSummary", {})
        persisted_summary = await self._safe_read_conversation_summary(
            conversation_id=str(params.get("conversationId") or ""),
            user_id=str(params.get("userId")) if params.get("userId") else None,
        )
        if persisted_summary:
            params["persistedConversationSummary"] = persisted_summary
            structured_summary = self._merge_summary_context(structured_summary, persisted_summary)
            params["structuredConversationSummary"] = structured_summary
        profile_context = params.get("profile", {})
        judge_result = params.get("judgeResult", {})
        evaluation_result = params.get("evaluationResult", {})
        practice_batch = params.get("practiceQuestionBatch", {})
        profile_source = str(params.get("profileSource") or "CONVERSATION")
        joined_user_content = " ".join(
            str(item.get("content", ""))
            for item in messages
            if isinstance(item, dict) and item.get("role") == "user"
        )
        practice_context_text = self._build_practice_context_text(
            judge_result=judge_result,
            practice_batch=practice_batch,
        )
        combined_text = " ".join(part for part in [joined_user_content, practice_context_text] if part)
        context_payload = {
            "messages": messages,
            "structuredConversationSummary": structured_summary,
            "persistedConversationSummary": persisted_summary or {},
            "profile": profile_context,
            "judgeResult": judge_result,
            "evaluationResult": evaluation_result,
            "practiceQuestionBatch": practice_batch,
            "combinedUserText": combined_text,
            "profileSource": profile_source,
        }
        try:
            dimensions = await self.profile_analyzer.analyze(context_payload=context_payload)
        except Exception as exc:
            LOGGER.warning("画像 LLM 分析失败，使用规则兜底画像: %s", exc)
            dimensions = self._build_fallback_dimensions(
                structured_summary=structured_summary,
                profile_context=profile_context,
                combined_text=combined_text,
                profile_source=profile_source,
            )
        dimensions = self._finalize_dimensions(
            dimensions=dimensions,
            messages=messages,
            structured_summary=structured_summary,
            profile_context=profile_context,
            judge_result=judge_result,
            practice_batch=practice_batch,
            combined_text=combined_text,
            profile_source=profile_source,
        )
        serialized_dimensions = dimensions.model_dump(by_alias=True, mode="json")
        params["analyzedProfileDimensions"] = serialized_dimensions
        return serialized_dimensions

    def _build_fallback_dimensions(
        self,
        *,
        structured_summary: dict[str, Any],
        profile_context: dict[str, Any],
        combined_text: str,
        profile_source: str,
    ) -> LearnerProfileDimensions:
        return LearnerProfileDimensions(
            knowledge_foundation=self._infer_knowledge_foundation(combined_text, profile_context),
            learning_goal=self._infer_learning_goal(structured_summary, combined_text),
            professional_background=str(
                profile_context.get("professionalBackground") or profile_context.get("major") or ""
            ).strip(),
            learning_preference=self._infer_learning_preference(structured_summary, combined_text),
            cognitive_style=self._infer_cognitive_style(combined_text),
            weak_points=self._infer_weak_points(structured_summary, combined_text),
            learning_pace=self._infer_learning_pace(combined_text),
            confidence_level=self._infer_confidence(combined_text),
            source=profile_source,
            summary_text="",
        )

    async def _tool_update_profile(
        self,
        *,
        tool_input: dict[str, Any],
        user_id: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        analyzed_dimensions = self._latest_analyzed_dimensions(params)
        dimensions = LearnerProfileDimensions.model_validate(analyzed_dimensions or tool_input)
        snapshot = await self._safe_update_profile(
            user_id=user_id,
            dimensions=dimensions,
            source_session_id=params.get("conversationId"),
        )
        return {
            "userId": snapshot.user_id,
            "version": snapshot.version,
            "summaryText": snapshot.profile.summary_text,
            "dimensions": snapshot.profile.model_dump(by_alias=True, mode="json"),
        }

    def _latest_analyzed_dimensions(self, params: dict[str, Any]) -> dict[str, Any] | None:
        return params.get("analyzedProfileDimensions")

    async def _safe_read_conversation_summary(
        self,
        *,
        conversation_id: str,
        user_id: str | None,
    ) -> dict[str, Any] | None:
        if not conversation_id:
            return None
        try:
            document = await self.summary_store.get_latest_summary(
                conversation_id=conversation_id,
                user_id=user_id,
            )
            if document is None and user_id is not None:
                document = await self.summary_store.get_latest_summary(
                    conversation_id=conversation_id,
                    user_id=None,
                )
        except Exception:
            return None
        if document is None:
            return None
        return document.model_dump(by_alias=True, mode="json")

    def _merge_summary_context(
        self,
        current_summary: dict[str, Any],
        persisted_summary: dict[str, Any],
    ) -> dict[str, Any]:
        current = current_summary if isinstance(current_summary, dict) else {}
        persisted = persisted_summary if isinstance(persisted_summary, dict) else {}
        merged = {**persisted, **current}
        for key in ("topicFocus", "canonicalTopicKeys", "knownGaps", "unresolvedQuestions", "recentProgress"):
            merged[key] = self._merge_unique_strings(
                list(persisted.get(key, []) or []),
                list(current.get(key, []) or []),
                limit=8,
            )
        merged["aliases"] = self._merge_aliases(
            persisted.get("aliases", {}),
            current.get("aliases", {}),
        )
        merged["learnerGoal"] = current.get("learnerGoal") or persisted.get("learnerGoal")
        merged["preferredHelpStyle"] = current.get("preferredHelpStyle") or persisted.get("preferredHelpStyle")
        merged["lastUserMessage"] = current.get("lastUserMessage") or persisted.get("lastUserMessage")
        merged["summaryText"] = current.get("summaryText") or persisted.get("summaryText") or ""
        return merged

    def _merge_unique_strings(self, older: list[Any], newer: list[Any], *, limit: int) -> list[str]:
        merged: list[str] = []
        for item in [*older, *newer]:
            normalized = str(item).strip()
            if normalized and normalized not in merged:
                merged.append(normalized)
            if len(merged) >= limit:
                break
        return merged

    def _merge_aliases(self, older: Any, newer: Any) -> dict[str, list[str]]:
        merged: dict[str, list[str]] = {}
        for source in (older, newer):
            if not isinstance(source, dict):
                continue
            for key, values in source.items():
                normalized_key = str(key).strip()
                if not normalized_key:
                    continue
                if isinstance(values, list):
                    aliases = values
                else:
                    aliases = [values]
                merged[normalized_key] = self._merge_unique_strings(
                    merged.get(normalized_key, []),
                    aliases,
                    limit=8,
                )
        return merged

    def _build_practice_context_text(
        self,
        *,
        judge_result: dict[str, Any],
        practice_batch: dict[str, Any],
    ) -> str:
        if not judge_result:
            return ""
        topic = str(practice_batch.get("topic") or "当前主题")
        summary = str(judge_result.get("summary") or "")
        weak_tags = ", ".join(str(tag) for tag in judge_result.get("weakKnowledgeTags", []))
        return " ".join(part for part in [topic, summary, weak_tags] if part)

    def _finalize_dimensions(
        self,
        *,
        dimensions: LearnerProfileDimensions,
        messages: list[dict[str, Any]],
        structured_summary: dict[str, Any],
        profile_context: dict[str, Any],
        judge_result: dict[str, Any],
        practice_batch: dict[str, Any],
        combined_text: str,
        profile_source: str,
    ) -> LearnerProfileDimensions:
        weak_point_details = self._build_weak_point_details(
            dimensions=dimensions,
            judge_result=judge_result,
        )
        confidence_score = self._derive_confidence_score(
            dimensions=dimensions,
            judge_result=judge_result,
            weak_point_details=weak_point_details,
        )
        professional_background = (
            dimensions.professional_background
            or str(profile_context.get("professionalBackground") or profile_context.get("major") or "").strip()
        )
        current_goal = self._derive_current_goal(
            dimensions=dimensions,
            structured_summary=structured_summary,
            practice_batch=practice_batch,
            combined_text=combined_text,
            judge_result=judge_result,
        )
        learning_habits = self._derive_learning_habits(
            messages=messages,
            combined_text=combined_text,
        )
        error_patterns = self._derive_error_patterns(
            weak_point_details=weak_point_details,
            judge_result=judge_result,
            combined_text=combined_text,
        )
        preferred_resource_types = self._derive_preferred_resource_types(
            dimensions=dimensions,
            combined_text=combined_text,
        )
        explanation_preference = (
            dimensions.explanation_preference or self._derive_explanation_preference(combined_text)
        )
        skill_mastery = self._derive_skill_mastery(
            dimensions=dimensions,
            judge_result=judge_result,
            practice_batch=practice_batch,
            weak_point_details=weak_point_details,
        )
        confidence_level = self._derive_confidence_level(
            dimensions=dimensions,
            judge_result=judge_result,
            confidence_score=confidence_score,
        )
        evidence = self._derive_evidence(
            structured_summary=structured_summary,
            judge_result=judge_result,
            weak_point_details=weak_point_details,
            combined_text=combined_text,
        )
        dimensions = dimensions.model_copy(
            update={
                "professional_background": professional_background,
                "weak_points": [item.topic for item in weak_point_details] or dimensions.weak_points,
                "weak_point_details": weak_point_details,
                "confidence_score": confidence_score,
                "confidence_level": confidence_level,
                "learning_habits": learning_habits,
                "error_patterns": error_patterns,
                "current_goal": current_goal,
                "preferred_resource_types": preferred_resource_types,
                "explanation_preference": explanation_preference,
                "skill_mastery": skill_mastery,
                "evidence": evidence,
                "source": profile_source or dimensions.source,
                "inferred_recommendations": dimensions.inferred_recommendations
                or self._infer_recommendations(
                    weak_point_details=weak_point_details,
                    preferred_resource_types=preferred_resource_types,
                    explanation_preference=explanation_preference,
                ),
            }
        )
        if not dimensions.summary_text.strip():
            dimensions.summary_text = self._build_summary_text(dimensions)
        return dimensions

    async def _safe_read_profile(self, user_id: str) -> LearnerProfileSnapshot | None:
        try:
            return await self.profile_store.read_profile(user_id)
        except Exception:
            return await self.fallback_profile_store.read_profile(user_id)

    async def _safe_update_profile(
        self,
        *,
        user_id: str,
        dimensions: LearnerProfileDimensions,
        source_session_id: str | None,
    ) -> LearnerProfileSnapshot:
        async def operation() -> LearnerProfileSnapshot:
            snapshot = await self.profile_store.update_profile(
                user_id=user_id,
                dimensions=dimensions,
                source_session_id=source_session_id,
            )
            self.fallback_profile_store.snapshots[user_id] = snapshot
            return snapshot

        return await self.recovery_engine.call_with_recovery(
            failure_type=RecoveryFailureType.PROFILE_UPDATE_FAILED,
            operation=operation,
        )

    def _infer_knowledge_foundation(self, text: str, profile_context: dict[str, Any]) -> str:
        for key in ("knowledgeFoundation", "knowledgeBase", "foundationLevel", "studentLevel"):
            value = str(profile_context.get(key) or "").strip()
            if value and value.upper() not in {"UNKNOWN", "待分析", "--"}:
                return value
        if any(
            keyword in text
            for keyword in (
                "不太懂",
                "不懂",
                "刚学",
                "入门",
                "新手",
                "零基础",
                "从零",
                "什么是",
                "是什么",
                "是什么意思",
                "解释一下",
                "介绍一下",
                "基础概念",
                "基本概念",
            )
        ):
            return "BASIC"
        if any(keyword in text for keyword in ("熟悉", "会做", "复习")):
            return "INTERMEDIATE"
        return "UNKNOWN"

    def _infer_learning_goal(self, structured_summary: dict[str, Any], text: str) -> str:
        goal = structured_summary.get("learnerGoal")
        if goal:
            return str(goal)
        for keyword in ("掌握", "复习", "理解", "提高"):
            if keyword in text:
                return text[:32]
        return "夯实当前主题的核心概念与应用"

    def _infer_learning_preference(self, structured_summary: dict[str, Any], text: str) -> str:
        preferred = structured_summary.get("preferredHelpStyle")
        if preferred:
            return str(preferred)
        if "例子" in text:
            return "example_first"
        if "一步步" in text or "详细" in text:
            return "step_by_step"
        if "画个图" in text or "导图" in text:
            return "visual_first"
        return "concept_then_question"

    def _infer_cognitive_style(self, text: str) -> str:
        if "为什么" in text or "原理" in text:
            return "reasoning_oriented"
        if "怎么做" in text or "步骤" in text:
            return "procedural_oriented"
        return "mixed"

    def _infer_weak_points(self, structured_summary: dict[str, Any], text: str) -> list[str]:
        weak_points = list(structured_summary.get("knownGaps", []))
        if weak_points:
            return weak_points
        if "分不清" in text:
            return ["概念区分不清"]
        if "总错" in text:
            return ["题目条件判断不稳"]
        if "不会" in text:
            return ["关键步骤不会落地"]
        return []

    def _infer_learning_pace(self, text: str) -> str:
        if "慢一点" in text or "一步步" in text:
            return "steady"
        if "尽快" in text:
            return "fast"
        return "normal"

    def _infer_confidence(self, text: str) -> str:
        if any(keyword in text for keyword in ("不太懂", "不会", "总错")):
            return "LOW"
        if any(keyword in text for keyword in ("会", "熟悉", "掌握")):
            return "MEDIUM"
        return "MEDIUM"

    def _build_summary_text(self, dimensions: LearnerProfileDimensions) -> str:
        weak_topics = [item.topic for item in dimensions.weak_point_details] or dimensions.weak_points
        skill_mastery = sorted(
            dimensions.skill_mastery.items(),
            key=lambda item: item[1],
        )
        weakest_skills = "、".join(name for name, score in skill_mastery[:2] if score < 0.65)
        preferred_resources = "、".join(
            self._localize_resource_type(item) for item in dimensions.preferred_resource_types[:3]
        ) or "讲解文档"
        short_goal = dimensions.current_goal.short_term or dimensions.learning_goal
        return (
            f"画像更新完成：当前知识基础为 {self._localize_knowledge_foundation(dimensions.knowledge_foundation)}，"
            f"短期目标聚焦 \"{short_goal}\"；"
            f"主要薄弱点为 {', '.join(weak_topics[:3]) or '暂无明确薄弱点'}，"
            f"高频易错模式为 {dimensions.error_patterns[0].pattern if dimensions.error_patterns else '概念理解待巩固'}；"
            f"学习偏好偏向 {self._localize_learning_preference(dimensions.learning_preference or dimensions.explanation_preference or 'step_by_step')}，"
            f"认知风格为 {self._localize_cognitive_style(dimensions.cognitive_style)}，"
            f"建议优先生成 {preferred_resources} 类型资源"
            f"{f'，并优先补强 {weakest_skills}' if weakest_skills else ''}。"
        )

    def _localize_knowledge_foundation(self, value: str) -> str:
        mapping = {
            "BEGINNER": "入门",
            "BASIC": "基础",
            "INTERMEDIATE": "进阶",
            "ADVANCED": "熟练",
            "UNKNOWN": "待分析",
        }
        return mapping.get(str(value or "").strip().upper(), str(value or "").strip() or "待分析")

    def _localize_learning_preference(self, value: str) -> str:
        mapping = {
            "step_by_step": "循序渐进",
            "concept_then_question": "先概念后练习",
            "example_first": "先例子后原理",
            "visual_first": "先图示后讲解",
        }
        normalized = str(value or "").strip()
        return mapping.get(normalized, normalized or "循序渐进")

    def _localize_cognitive_style(self, value: str) -> str:
        mapping = {
            "reasoning_oriented": "偏原理推导",
            "procedural_oriented": "偏步骤实操",
            "mixed": "混合型",
        }
        normalized = str(value or "").strip()
        return mapping.get(normalized, normalized or "混合型")

    def _localize_resource_type(self, value: str) -> str:
        mapping = {
            "DOCUMENT": "讲解文档",
            "READING": "拓展阅读",
            "MINDMAP": "思维导图",
            "CODE": "代码案例",
            "CODE_CASE": "代码案例",
            "QUIZ": "练习题",
            "VIDEO": "数字人视频",
        }
        normalized = str(value or "").strip().upper()
        return mapping.get(normalized, str(value or "").strip())

    def _derive_confidence_score(
        self,
        *,
        dimensions: LearnerProfileDimensions,
        judge_result: dict[str, Any],
        weak_point_details: list[WeakPointDetail],
    ) -> float:
        score = float(dimensions.confidence_score or 0.0)
        accuracy = self._judge_accuracy(judge_result)
        if judge_result:
            practice_score = 0.42 + accuracy * 0.38
            if weak_point_details:
                practice_score -= 0.06
            practice_score = max(0.35, min(0.95, practice_score))
            return min(max(0.35, min(0.95, score)), practice_score) if score > 0 else practice_score
        if score > 0:
            return max(0.35, min(0.95, score))
        if accuracy > 0:
            return max(0.35, min(0.95, 0.45 + accuracy * 0.4))
        if dimensions.confidence_level == "HIGH":
            return 0.82
        if dimensions.confidence_level == "LOW":
            return 0.48
        if weak_point_details:
            return 0.58
        return 0.65

    def _build_weak_point_details(
        self,
        *,
        dimensions: LearnerProfileDimensions,
        judge_result: dict[str, Any],
    ) -> list[WeakPointDetail]:
        merged: dict[str, WeakPointDetail] = {}

        def upsert(topic: str, *, severity: float, last_error: str) -> None:
            normalized = str(topic).strip()
            if not normalized:
                return
            existing = merged.get(normalized)
            if existing is None:
                merged[normalized] = WeakPointDetail(
                    topic=normalized,
                    severity=max(0.0, min(1.0, severity)),
                    lastError=last_error,
                )
                return
            existing.severity = max(existing.severity, max(0.0, min(1.0, severity)))
            if last_error:
                existing.last_error = last_error

        for item in dimensions.weak_point_details:
            upsert(item.topic, severity=float(item.severity), last_error=item.last_error)
        for topic in dimensions.weak_points:
            upsert(str(topic), severity=0.72, last_error=str(judge_result.get("summary") or ""))

        judge_summary = str(judge_result.get("summary") or "")
        practice_severity = max(0.82, min(0.98, 1.0 - self._judge_accuracy(judge_result) * 0.35))
        judge_tags = list(dict.fromkeys(self._judge_weak_tags(judge_result)))
        for topic in judge_tags:
            upsert(topic, severity=practice_severity, last_error=judge_summary)

        return sorted(merged.values(), key=lambda item: (-item.severity, item.topic))

    def _derive_skill_mastery(
        self,
        *,
        dimensions: LearnerProfileDimensions,
        judge_result: dict[str, Any],
        practice_batch: dict[str, Any],
        weak_point_details: list[WeakPointDetail],
    ) -> dict[str, float]:
        skills = {
            name: max(0.0, min(1.0, float(score)))
            for name, score in (dimensions.skill_mastery or {}).items()
        }
        if not judge_result:
            if skills:
                return skills
            topic = str(practice_batch.get("topic") or "当前主题").strip() or "当前主题"
            mastery = 0.42
            skills = {topic: mastery}
            for item in weak_point_details[:3]:
                skills.setdefault(item.topic, max(0.2, round(1 - item.severity * 0.55, 2)))
            return skills

        topic = str(practice_batch.get("topic") or "当前主题").strip() or "当前主题"
        accuracy = self._judge_accuracy(judge_result)
        practice_mastery = max(0.05, min(0.92, round(accuracy, 2)))
        skills[topic] = min(skills.get(topic, 1.0), practice_mastery)
        for item in weak_point_details[:3]:
            degraded_mastery = max(0.05, round(1 - item.severity * 0.75, 2))
            skills[item.topic] = min(skills.get(item.topic, 1.0), degraded_mastery)
        return skills

    def _derive_learning_habits(
        self,
        *,
        messages: list[dict[str, Any]],
        combined_text: str,
    ) -> LearningHabitsProfile:
        user_messages = [
            str(item.get("content", ""))
            for item in messages
            if isinstance(item, dict) and item.get("role") == "user"
        ]
        avg_len = 0
        if user_messages:
            avg_len = int(sum(len(msg) for msg in user_messages) / len(user_messages))
        return LearningHabitsProfile(
            studyFrequency="高频互动" if len(user_messages) >= 4 else "阶段性学习",
            preferredTime="晚上" if "晚上" in combined_text or "今晚" in combined_text else "",
            avgSessionDuration=max(10, min(45, avg_len // 3 if avg_len else 20)),
            noteTaking=("总结" in combined_text or "记一下" in combined_text),
            selfTesting=("出题" in combined_text or "测一下" in combined_text or "练习" in combined_text),
        )

    def _derive_error_patterns(
        self,
        *,
        weak_point_details: list[WeakPointDetail],
        judge_result: dict[str, Any],
        combined_text: str,
    ) -> list[ErrorPattern]:
        patterns: list[ErrorPattern] = []
        if any(keyword in combined_text for keyword in ("分不清", "混淆", "区别")):
            patterns.append(
                ErrorPattern(pattern="概念混淆", frequency=0.72, examples=[item.topic for item in weak_point_details[:2]])
            )
        if any(keyword in combined_text for keyword in ("条件", "适用", "边界")):
            patterns.append(
                ErrorPattern(
                    pattern="条件遗漏",
                    frequency=0.58,
                    examples=list(judge_result.get("weakKnowledgeTags", []))[:2],
                )
            )
        if not patterns and weak_point_details:
            patterns.append(
                ErrorPattern(pattern="知识点掌握不稳", frequency=0.52, examples=[weak_point_details[0].topic])
            )
        return patterns

    def _derive_current_goal(
        self,
        *,
        dimensions: LearnerProfileDimensions,
        structured_summary: dict[str, Any],
        practice_batch: dict[str, Any],
        combined_text: str,
        judge_result: dict[str, Any],
    ) -> CurrentGoalProfile:
        practice_topic = str(practice_batch.get("topic") or "").strip()
        if judge_result and practice_topic:
            accuracy = self._judge_accuracy(judge_result)
            urgency = "HIGH" if accuracy <= 0.4 or any(keyword in combined_text for keyword in ("考试", "尽快", "马上", "冲刺")) else "MEDIUM"
            return CurrentGoalProfile(
                shortTerm=f"掌握{practice_topic}",
                midTerm=f"完成{practice_topic}相关迁移练习",
                context="基于最新练习判题结果更新",
                urgency=urgency,
            )
        current_goal = dimensions.current_goal
        if current_goal.short_term:
            return current_goal
        short_term = (
            structured_summary.get("learnerGoal")
            or dimensions.learning_goal
            or f"掌握{practice_batch.get('topic')}" if practice_batch.get("topic") else ""
        )
        urgency = "HIGH" if any(keyword in combined_text for keyword in ("考试", "尽快", "马上", "冲刺")) else "MEDIUM"
        return CurrentGoalProfile(
            shortTerm=str(short_term or "掌握当前主题核心概念"),
            midTerm=f"完成{practice_batch.get('topic')}相关迁移练习" if practice_batch.get("topic") else "",
            context="对话学习目标抽取",
            urgency=urgency,
        )

    def _derive_confidence_level(
        self,
        *,
        dimensions: LearnerProfileDimensions,
        judge_result: dict[str, Any],
        confidence_score: float,
    ) -> str:
        if judge_result:
            accuracy = self._judge_accuracy(judge_result)
            if accuracy <= 0.4:
                return "LOW"
            if accuracy >= 0.8 and confidence_score >= 0.75:
                return "HIGH"
            return "MEDIUM"
        if dimensions.confidence_level in {"LOW", "MEDIUM", "HIGH"}:
            return dimensions.confidence_level
        if confidence_score >= 0.78:
            return "HIGH"
        if confidence_score >= 0.58:
            return "MEDIUM"
        return "LOW"

    def _judge_accuracy(self, judge_result: dict[str, Any]) -> float:
        try:
            return max(0.0, min(1.0, float(judge_result.get("accuracy", 0.0) or 0.0)))
        except (TypeError, ValueError):
            return 0.0

    def _judge_weak_tags(self, judge_result: dict[str, Any]) -> list[str]:
        tags = [
            str(tag).strip()
            for tag in judge_result.get("weakKnowledgeTags", [])
            if str(tag).strip()
        ]
        if tags:
            return tags
        inferred: list[str] = []
        for item in judge_result.get("items", []):
            if not isinstance(item, dict):
                continue
            if bool(item.get("isCorrect")):
                continue
            for tag in item.get("knowledgeTags", []) or []:
                normalized = str(tag).strip()
                if normalized:
                    inferred.append(normalized)
        return list(dict.fromkeys(inferred))

    def _derive_preferred_resource_types(
        self,
        *,
        dimensions: LearnerProfileDimensions,
        combined_text: str,
    ) -> list[str]:
        if dimensions.preferred_resource_types:
            return dimensions.preferred_resource_types
        resource_types: list[str] = []
        if any(keyword in combined_text for keyword in ("画个图", "导图", "图示", "流程图")):
            resource_types.append("MINDMAP")
        if any(keyword in combined_text for keyword in ("代码", "案例", "示例")):
            resource_types.append("CODE")
        if any(keyword in combined_text for keyword in ("阅读", "资料", "讲义")):
            resource_types.append("READING")
        if "视频" in combined_text:
            resource_types.append("VIDEO")
        resource_types.append("DOCUMENT")
        return list(dict.fromkeys(resource_types))

    def _derive_explanation_preference(self, text: str) -> str:
        if "为什么" in text or "原理" in text:
            return "先原理后例子"
        if "例子" in text or "案例" in text:
            return "先例子后原理"
        return "step_by_step"

    def _derive_evidence(
        self,
        *,
        structured_summary: dict[str, Any],
        judge_result: dict[str, Any],
        weak_point_details: list[WeakPointDetail],
        combined_text: str,
    ) -> list[str]:
        evidence: list[str] = []
        last_user_message = str(structured_summary.get("lastUserMessage") or "").strip()
        if last_user_message:
            evidence.append(last_user_message[:120])
        summary = str(judge_result.get("summary") or "").strip()
        if summary:
            evidence.append(summary[:120])
        evidence.extend(item.last_error[:120] for item in weak_point_details if item.last_error)
        if not evidence and combined_text:
            evidence.append(combined_text[:120])
        return list(dict.fromkeys(filter(None, evidence)))[:5]

    def _infer_recommendations(
        self,
        *,
        weak_point_details: list[WeakPointDetail],
        preferred_resource_types: list[str],
        explanation_preference: str,
    ) -> list[str]:
        recommendations: list[str] = []
        if weak_point_details:
            recommendations.append(f"优先攻克 \"{weak_point_details[0].topic}\" 相关薄弱点")
        if preferred_resource_types:
            recommendations.append(f"优先推送 {preferred_resource_types[0]} 类型资源")
        recommendations.append(f"讲解顺序建议采用 \"{explanation_preference}\"")
        return recommendations[:3]

    async def _sync_mastery_to_graph(
        self,
        *,
        user_id: str,
        skill_mastery: dict[str, Any],
    ) -> None:
        """把 LLM 分析出的技能掌握度同步更新到知识图谱节点。"""
        try:
            for topic, score in skill_mastery.items():
                topic_str = str(topic).strip()
                if not topic_str:
                    continue
                try:
                    mastery = max(0.0, min(1.0, float(score)))
                except (TypeError, ValueError):
                    continue
                await self.knowledge_graph_store.upsert_node(
                    user_id=user_id,
                    canonical_key=topic_str,
                    topic=topic_str,
                    mastery_score=mastery,
                    source="PRACTICE",
                )
        except Exception as exc:
            LOGGER.warning("_sync_mastery_to_graph failed user=%s: %s", user_id, exc)
