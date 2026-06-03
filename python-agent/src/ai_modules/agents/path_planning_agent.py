"""基于 AgentCoreLoop 和学习计划持久化的路径规划 Agent。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from src.ai_modules.agents.base import PlaceholderAgent
from src.ai_modules.llms import LearningPathGenerator
from src.ai_modules.memory import (
    InMemoryLearningPlanStore,
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
    ) -> None:
        super().__init__("Path Planning Agent", "path_planning")
        self.llm_client = llm_client
        self.learning_plan_store = learning_plan_store or PostgresLearningPlanStore()
        self.fallback_learning_plan_store = InMemoryLearningPlanStore()
        self.generator = generator
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
        profile = profile_analysis or self._safe_dict(params.get("profile")) or {}
        judge_result = params.get("judgeResult", {})
        retrieval_evidence = params.get("retrievalEvidence")
        if not isinstance(retrieval_evidence, list):
            retrieval_evidence = []
        weak_point_details = [
            item for item in profile.get("weakPointDetails", [])
            if isinstance(item, dict)
        ]
        skill_mastery = profile.get("skillMastery", {})
        current_goal = profile.get("currentGoal", {})
        focus = self._unique_items(
            [
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
            "preferredResourceTypes": list(profile.get("preferredResourceTypes", [])),
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
        except Exception:
            return await self.fallback_learning_plan_store.save_plan(
                user_id=user_id,
                course_id=course_id,
                plan=plan,
                trigger_source=trigger_source,
            )

    def _resolve_goal(self, params: dict[str, Any]) -> str:
        evaluation = self._safe_dict(params.get("masteryDiagnosis")) or self._safe_dict(params.get("evaluationResult")) or {}
        profile = self._safe_dict(params.get("profileAnalysis")) or self._safe_dict(params.get("profile")) or {}
        current_goal = profile.get("currentGoal", {}) if isinstance(profile.get("currentGoal", {}), dict) else {}
        return str(
            params.get("goal")
            or params.get("currentProgress")
            or current_goal.get("shortTerm")
            or profile.get("learningGoal")
            or (params.get("pathPlanningContext") or {}).get("goal")
            or (evaluation.get("nextFocus") or ["提升当前薄弱点"])[0]
            or "提升当前薄弱点"
        )

    def _resolve_trigger_source(self, params: dict[str, Any]) -> str:
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
