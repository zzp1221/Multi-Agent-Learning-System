"""基于 AgentCoreLoop 和学习计划持久化的路径规划 Agent。"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

LOGGER = logging.getLogger(__name__)

from src.ai_modules.agents.base import PlaceholderAgent
from src.ai_modules.llms import LearningPathGenerator
from src.ai_modules.memory import (
    InMemoryLearningPlanStore,
    LearnerKnowledgeGraphStore,
    LearningPlanStore,
    PostgresLearningPlanStore,
)
from src.ai_modules.models import (
    LearningPlanPayload,
    ProgressPayload,
    ProgressSSEEvent,
    ResultChunkPayload,
    ResultChunkSSEEvent,
    SSEEvent,
)
from src.ai_modules.prompts import build_path_planning_system_prompt
from src.ai_modules.runtime import (
    SystemSnapshot,
)
from src.ai_modules.runtime.skill_loader import SkillPromptLoader


class PathPlanningAgent(PlaceholderAgent):
    """根据评估和画像上下文生成有序学习计划。"""

    def __init__(
        self,
        llm_client: Any | None = None,
        learning_plan_store: LearningPlanStore | None = None,
        generator: Any | None = None,
        knowledge_graph_store: LearnerKnowledgeGraphStore | None = None,
    ) -> None:
        super().__init__("Path Planning Agent", "path_planning")
        self.llm_client = llm_client
        self.learning_plan_store = learning_plan_store or PostgresLearningPlanStore()
        self.fallback_learning_plan_store = InMemoryLearningPlanStore()
        self.generator = generator
        self.knowledge_graph_store = knowledge_graph_store or LearnerKnowledgeGraphStore()
        self.skill_loader = SkillPromptLoader()

    def system_prompt(self, snapshot: SystemSnapshot) -> str:
        return self.skill_loader.build_system_prompt(
            skill_name="path_planning",
            snapshot=snapshot,
            fallback_prompt=build_path_planning_system_prompt(snapshot),
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
        user_id = str(params.get("userId") or "00000000-0000-0000-0000-000000000001")
        core_loop_result = await self._run_agent_core_loop(
            user_id=user_id,
            params=params,
            snapshot=snapshot,
            system_prompt=system_prompt,
        )
        payload = LearningPlanPayload.model_validate(core_loop_result["learningPath"])
        params["learningPath"] = payload.model_dump(by_alias=True)
        params["learningPlanPersistence"] = core_loop_result["persistence"]

        yield ProgressSSEEvent(
            taskId=task_id,
            traceId=trace_id,
            seq=seq,
            payload=ProgressPayload(
                stage=self.stage_name,
                percent=75,
                message="已生成学习路径",
            ),
        )
        yield ResultChunkSSEEvent(
            taskId=task_id,
            traceId=trace_id,
            seq=seq + 1,
            payload=ResultChunkPayload(text=payload.summary_text),
        )

    async def _run_agent_core_loop(
        self,
        *,
        user_id: str,
        params: dict[str, Any],
        snapshot: SystemSnapshot,
        system_prompt: str,
    ) -> dict[str, Any]:
        planning_context = self._tool_analyze_profile(tool_input={}, params=params, snapshot=snapshot)
        plan = await self._safe_plan(
            params=params,
            snapshot=snapshot,
            system_prompt=system_prompt,
            planning_context=planning_context,
        )
        plan = self._enrich_learning_plan(plan=plan, planning_context=planning_context)
        metadata = await self._safe_save_learning_plan(
            user_id=user_id,
            course_id=self._resolve_course_id(params),
            plan=plan,
            trigger_source=self._resolve_trigger_source(params),
        )
        await self._sync_plan_to_graph(user_id=user_id, plan=plan)
        return {
            "learningPath": plan.model_dump(by_alias=True),
            "persistence": metadata,
            "summaryText": plan.summary_text,
        }

    def _tool_analyze_profile(
        self,
        *,
        tool_input: dict[str, Any],
        params: dict[str, Any],
        snapshot: SystemSnapshot,
    ) -> dict[str, Any]:
        del tool_input
        evaluation = self._safe_dict(params.get("masteryDiagnosis")) or self._safe_dict(params.get("evaluationResult")) or {}
        profile_analysis = self._safe_dict(params.get("profileAnalysis")) or {}
        profile = self._merge_non_empty(self._safe_dict(params.get("profile")) or {}, profile_analysis)
        judge_result = params.get("judgeResult", {})
        retrieval_evidence = params.get("retrievalEvidence")
        if not isinstance(retrieval_evidence, list):
            retrieval_evidence = []
        mastery_focus = self._extract_mastery_focus(evaluation)
        mastery_weaknesses = self._extract_mastery_weaknesses(evaluation)
        mastery_resource_types = self._extract_mastery_resource_types(evaluation)
        profile_resource_types = profile.get("preferredResourceTypes", [])
        if not isinstance(profile_resource_types, list):
            profile_resource_types = []
        weak_point_details = [
            item for item in profile.get("weakPointDetails", [])
            if isinstance(item, dict)
        ]
        skill_mastery = profile.get("skillMastery", {})
        current_goal = profile.get("currentGoal", {})
        focus = self._unique_items(
            [
                *mastery_focus,
                *list(evaluation.get("nextFocus", [])),
                *list(judge_result.get("weakKnowledgeTags", [])),
                *list(profile.get("knowledgeGaps", [])),
                *list(profile.get("weakPoints", [])),
                *[str(item.get("topic", "")) for item in weak_point_details],
                *list(snapshot.knowledge_gaps),
            ]
        )
        weakest_skills = self._lowest_mastery_skills(skill_mastery)
        context = {
            "goal": self._resolve_goal(params),
            "targetPeriod": str(params.get("targetPeriod") or "").strip() or "7天",
            "weeklyHours": str(params.get("weeklyHours") or "").strip() or "6",
            "currentProgress": str(params.get("currentProgress") or "").strip() or "已完成基础概念，准备进入案例训练",
            "studentLevel": str(
                profile.get("studentLevel")
                or profile.get("knowledgeFoundation")
                or evaluation.get("overallLevel")
                or snapshot.student_level
                or "BASIC"
            ),
            "weaknesses": self._unique_items(
                [
                    *mastery_weaknesses,
                    *list(evaluation.get("weaknesses", [])),
                    *list(profile.get("knowledgeGaps", [])),
                    *list(profile.get("weakPoints", [])),
                    *[str(item.get("topic", "")) for item in weak_point_details],
                    *list(snapshot.knowledge_gaps),
                ]
            ),
            "nextFocus": focus or ["核心概念", "适用条件"],
            "preferredStyle": str(
                profile.get("learningPreference")
                or profile.get("preferredStyle")
                or snapshot.preferred_style
                or "step_by_step"
            ),
            "explanationPreference": str(profile.get("explanationPreference") or ""),
            "preferredResourceTypes": self._unique_items([*profile_resource_types, *mastery_resource_types]),
            "skillMastery": skill_mastery if isinstance(skill_mastery, dict) else {},
            "weakPointDetails": weak_point_details,
            "currentGoal": current_goal if isinstance(current_goal, dict) else {},
            "weakestSkills": weakest_skills,
            "retrievalEvidence": retrieval_evidence[:8],
            "profileAnalysis": profile_analysis,
            "masteryDiagnosis": evaluation,
            "recentMistakes": list(snapshot.recent_mistakes),
            "triggerSource": self._resolve_trigger_source(params),
        }
        params["pathPlanningContext"] = context
        return context

    async def _tool_generate_path(
        self,
        *,
        tool_input: dict[str, Any],
        params: dict[str, Any],
        snapshot: SystemSnapshot,
        system_prompt: str,
    ) -> dict[str, Any]:
        analysis = params.get("pathPlanningContext") or tool_input
        payload = await self._safe_plan(
            params=params,
            snapshot=snapshot,
            system_prompt=system_prompt,
            planning_context=analysis if isinstance(analysis, dict) else {},
        )
        serialized = payload.model_dump(by_alias=True)
        params["draftLearningPath"] = serialized
        return serialized

    async def _tool_update_path_plan(
        self,
        *,
        tool_input: dict[str, Any],
        user_id: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        draft_payload = params.get("draftLearningPath") or tool_input
        plan = LearningPlanPayload.model_validate(draft_payload)
        metadata = await self._safe_save_learning_plan(
            user_id=user_id,
            course_id=self._resolve_course_id(params),
            plan=plan,
            trigger_source=self._resolve_trigger_source(params),
        )
        return {
            "learningPath": plan.model_dump(by_alias=True),
            "persistence": metadata,
            "summaryText": plan.summary_text,
        }

    async def _safe_plan(
        self,
        *,
        params: dict[str, Any],
        snapshot: SystemSnapshot,
        system_prompt: str,
        planning_context: dict[str, Any],
    ) -> LearningPlanPayload:
        generator = self.generator or LearningPathGenerator()
        try:
            return await generator.plan(
                system_prompt=system_prompt,
                context_payload=self._build_context_payload(
                    params=params,
                    snapshot=snapshot,
                    planning_context=planning_context,
                ),
            )
        except Exception as exc:
            raise RuntimeError("Path planning LLM failed") from exc

    def _build_context_payload(
        self,
        *,
        params: dict[str, Any],
        snapshot: SystemSnapshot,
        planning_context: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "evaluationResult": params.get("evaluationResult", {}),
            "masteryDiagnosis": params.get("masteryDiagnosis", {}),
            "profile": params.get("profile", {}),
            "profileAnalysis": params.get("profileAnalysis", {}),
            "retrievalEvidence": params.get("retrievalEvidence", []),
            "learningContext": params.get("learningContext", {}),
            "plannerInputs": {
                "targetPeriod": str(params.get("targetPeriod") or "").strip(),
                "weeklyHours": str(params.get("weeklyHours") or "").strip(),
                "currentProgress": str(params.get("currentProgress") or "").strip(),
            },
            "judgeResult": params.get("judgeResult", {}),
            "snapshot": {
                "studentLevel": snapshot.student_level,
                "knowledgeGaps": snapshot.knowledge_gaps,
                "preferredStyle": snapshot.preferred_style,
            },
            "planningContext": planning_context,
        }

    def _enrich_learning_plan(
        self,
        *,
        plan: LearningPlanPayload,
        planning_context: dict[str, Any],
    ) -> LearningPlanPayload:
        serialized = plan.model_dump(by_alias=True)
        preferred_types = self._resolve_preferred_resource_types(planning_context)
        focus_points = self._unique_items(
            [
                *list(planning_context.get("nextFocus", [])),
                *list(planning_context.get("weaknesses", [])),
            ]
        )
        enriched_steps: list[dict[str, Any]] = []
        for index, raw_step in enumerate(serialized.get("steps") or [], start=1):
            if not isinstance(raw_step, dict):
                continue
            target_points = raw_step.get("targetKnowledgePoints")
            if not isinstance(target_points, list) or not target_points:
                target_points = focus_points[:2]
            step_resource_types = raw_step.get("preferredResourceTypes")
            if not isinstance(step_resource_types, list) or not step_resource_types:
                step_resource_types = preferred_types
            enriched_steps.append(
                {
                    **raw_step,
                    "stepId": raw_step.get("stepId") or f"step-{index}",
                    "order": raw_step.get("order") or index,
                    "targetKnowledgePoints": target_points,
                    "preferredResourceTypes": step_resource_types,
                    "checkpoint": raw_step.get("checkpoint") or raw_step.get("successCriteria") or raw_step.get("objective"),
                }
            )
        serialized["steps"] = enriched_steps
        return LearningPlanPayload.model_validate(serialized)

    def _resolve_preferred_resource_types(self, planning_context: dict[str, Any]) -> list[str]:
        raw_types = planning_context.get("preferredResourceTypes")
        normalized = [str(item).strip().upper() for item in raw_types if str(item).strip()] if isinstance(raw_types, list) else []
        for required in ("DOCUMENT", "VIDEO", "QUIZ", "CODE"):
            if required not in normalized:
                normalized.append(required)
        return normalized[:4]

    async def _safe_save_learning_plan(
        self,
        *,
        user_id: str,
        course_id: str | None,
        plan: LearningPlanPayload,
        trigger_source: str,
    ) -> dict[str, Any]:
        try:
            metadata = await self.learning_plan_store.save_plan(
                user_id=user_id,
                course_id=course_id,
                plan=plan,
                trigger_source=trigger_source,
            )
            self.fallback_learning_plan_store.active_plans_by_user[user_id] = {
                **metadata,
                "learningPath": plan.model_dump(by_alias=True),
                "summaryText": plan.summary_text,
            }
            return metadata
        except Exception as exc:
            LOGGER.warning("Failed to persist learning plan user_id=%s: %s", user_id, exc)
            return await self.fallback_learning_plan_store.save_plan(
                user_id=user_id,
                course_id=course_id,
                plan=plan,
                trigger_source=trigger_source,
            )

    def _resolve_goal(self, params: dict[str, Any]) -> str:
        evaluation = self._safe_dict(params.get("masteryDiagnosis")) or self._safe_dict(params.get("evaluationResult")) or {}
        profile = self._merge_non_empty(
            self._safe_dict(params.get("profile")) or {},
            self._safe_dict(params.get("profileAnalysis")) or {},
        )
        current_goal = profile.get("currentGoal", {}) if isinstance(profile.get("currentGoal", {}), dict) else {}
        mastery_goal = self._resolve_mastery_goal(evaluation)
        return str(
            params.get("goal")
            or params.get("currentProgress")
            or current_goal.get("shortTerm")
            or profile.get("learningGoal")
            or (params.get("pathPlanningContext") or {}).get("goal")
            or mastery_goal
            or (evaluation.get("nextFocus") or ["提升当前薄弱点"])[0]
            or "提升当前薄弱点"
        )

    def _resolve_trigger_source(self, params: dict[str, Any]) -> str:
        explicit_source = str(params.get("triggerSource") or "").strip()
        if explicit_source:
            return self._normalize_trigger_source(explicit_source, params=params)
        if params.get("manualRefresh"):
            return "MANUAL_REFRESH"
        if params.get("judgeResult"):
            return "PRACTICE_RESULT"
        if params.get("evaluationResult"):
            return "EVALUATION"
        if params.get("profileAnalysis"):
            return "PROFILE_ANALYSIS"
        if params.get("profileUpdate"):
            return "PROFILE_UPDATE"
        return "INITIAL"

    def _normalize_trigger_source(self, trigger_source: str, *, params: dict[str, Any]) -> str:
        normalized = trigger_source.strip().upper()
        if normalized in {"INITIAL", "PROFILE_UPDATE", "PRACTICE_RESULT", "EVALUATION", "MANUAL_REFRESH"}:
            return normalized
        if normalized in {"INITIAL_PROFILE", "PROFILE_ONBOARDING"}:
            return "PROFILE_UPDATE"
        if normalized in {"PRACTICE_PROGRESS", "PRACTICE_REFRESH"}:
            return "PRACTICE_RESULT"
        if normalized in {"MANUAL_ADJUSTMENT", "RESOURCE_RECOMMENDATION_REFRESH"}:
            return "MANUAL_REFRESH"
        if params.get("manualRefresh"):
            return "MANUAL_REFRESH"
        if params.get("profileUpdate"):
            return "PROFILE_UPDATE"
        return "INITIAL"

    def _resolve_course_id(self, params: dict[str, Any]) -> str | None:
        return params.get("courseId")

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

    def _safe_dict(self, value: Any) -> dict[str, Any] | None:
        return value if isinstance(value, dict) else None

    def _merge_non_empty(self, base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
        merged = dict(base)
        for key, value in incoming.items():
            if value is None or value == "" or value == [] or value == {}:
                continue
            merged[key] = value
        return merged

    def _extract_mastery_focus(self, evaluation: dict[str, Any]) -> list[str]:
        target_scope = self._safe_dict(evaluation.get("targetScope")) or {}
        focus_items: list[Any] = []
        for diagnosis in self._sorted_knowledge_diagnoses(evaluation):
            focus_items.extend([diagnosis.get("nextFocus"), diagnosis.get("knowledgePoint")])
        knowledge_points = target_scope.get("knowledgePoints")
        if isinstance(knowledge_points, list):
            focus_items.extend(knowledge_points)
        return self._unique_items(focus_items)

    def _extract_mastery_weaknesses(self, evaluation: dict[str, Any]) -> list[str]:
        weakness_items: list[Any] = []
        for diagnosis in self._sorted_knowledge_diagnoses(evaluation):
            score = self._safe_float(diagnosis.get("masteryScore"))
            status = str(diagnosis.get("status") or "").strip().upper()
            is_weak = score is None or score < 0.75 or status in {"WEAK", "AT_RISK", "NOT_MASTERED", "LOW"}
            if not is_weak:
                continue
            weakness_items.extend([diagnosis.get("knowledgePoint"), diagnosis.get("nextFocus")])
            error_patterns = diagnosis.get("errorPatterns")
            if isinstance(error_patterns, list):
                weakness_items.extend(error_patterns)
        return self._unique_items(weakness_items)

    def _extract_mastery_resource_types(self, evaluation: dict[str, Any]) -> list[str]:
        raw_types: list[Any] = []
        for diagnosis in self._sorted_knowledge_diagnoses(evaluation):
            recommended_types = diagnosis.get("recommendedResourceTypes")
            if isinstance(recommended_types, list):
                raw_types.extend(recommended_types)
        return self._unique_items([str(item).strip().upper() for item in raw_types if str(item).strip()])

    def _resolve_mastery_goal(self, evaluation: dict[str, Any]) -> str:
        hints = self._safe_dict(evaluation.get("planAdjustmentHints")) or {}
        target_scope = self._safe_dict(evaluation.get("targetScope")) or {}
        for key in ("refreshReason", "strategy"):
            text = str(hints.get(key) or "").strip()
            if text:
                return text
        knowledge_points = target_scope.get("knowledgePoints")
        if isinstance(knowledge_points, list):
            first_point = next((str(item).strip() for item in knowledge_points if str(item).strip()), "")
            if first_point:
                return f"提升{first_point}掌握度"
        focus = self._extract_mastery_focus(evaluation)
        if focus:
            return f"提升{focus[0]}掌握度"
        return ""

    def _sorted_knowledge_diagnoses(self, evaluation: dict[str, Any]) -> list[dict[str, Any]]:
        diagnoses = evaluation.get("knowledgeDiagnoses")
        if not isinstance(diagnoses, list):
            return []
        normalized = [item for item in diagnoses if isinstance(item, dict)]
        normalized.sort(
            key=lambda item: (
                self._safe_int(item.get("priority"), default=999),
                self._safe_float(item.get("masteryScore"), default=1.0),
            )
        )
        return normalized

    def _safe_float(self, value: Any, default: float | None = None) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _safe_int(self, value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _lowest_mastery_skills(self, skill_mastery: Any) -> list[str]:
        if not isinstance(skill_mastery, dict):
            return []
        normalized: list[tuple[str, float]] = []
        for key, value in skill_mastery.items():
            try:
                normalized.append((str(key).strip(), float(value)))
            except (TypeError, ValueError):
                continue
        normalized = [item for item in normalized if item[0]]
        normalized.sort(key=lambda item: item[1])
        return [name for name, score in normalized[:3] if score < 0.75]

    async def _sync_plan_to_graph(self, *, user_id: str, plan: LearningPlanPayload) -> None:
        """把 LLM 生成的学习计划步骤写入用户知识图谱节点和 PREREQUISITE 边。"""
        if not plan.steps:
            LOGGER.warning("_sync_plan_to_graph: plan.steps is empty, skipping user=%s", user_id)
            return
        LOGGER.info("_sync_plan_to_graph: writing %d steps for user=%s", len(plan.steps), user_id)
        try:
            prev_key: str | None = None
            for step in plan.steps:
                topic = step.title.strip()
                if not topic:
                    continue
                await self.knowledge_graph_store.upsert_node(
                    user_id=user_id,
                    canonical_key=topic,
                    topic=topic,
                    mastery_score=0.0,
                    source="PROFILE",
                )
                if prev_key:
                    await self.knowledge_graph_store.upsert_edge(
                        user_id=user_id,
                        from_key=prev_key,
                        to_key=topic,
                        relation_type="PREREQUISITE",
                    )
                prev_key = topic
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("_sync_plan_to_graph failed user=%s: %s", user_id, exc)
