"""Supervisor that resolves routes and streams agent execution results."""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
from collections.abc import AsyncIterator, Container
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.ai_modules.agents import (
    CodeGeneratorAgent,
    CriticAgent,
    DocumentGeneratorAgent,
    EvaluationAgent,
    ImageAnalysisAgent,
    JudgeAgent,
    MindMapGeneratorAgent,
    PathPlanningAgent,
    PracticeAgent,
    ProfileAgent,
    QueryRewriteAgent,
    ReadingGeneratorAgent,
    ResourcePushAgent,
    RetrievalAgent,
    SlideGeneratorAgent,
    TutorAgent,
    VideoGenerationAgent,
)
from src.ai_modules.models import DonePayload, DoneSSEEvent, EngineStreamRequest, ErrorPayload, ErrorSSEEvent, SSEEvent
from src.ai_modules.models import ProgressPayload, ProgressSSEEvent
from src.ai_modules.retrieval.query_classifier import (
    QUERY_TYPE_ANSWER_PREVIOUS,
    QUERY_TYPE_FOLLOW_UP,
    QUERY_TYPE_IMAGE_QUESTION,
    QUERY_TYPE_SMALL_TALK,
    QueryClassifier,
)
from src.ai_modules.runtime import SnapshotBuilder, SystemSnapshot
from src.ai_modules.runtime.autonomous_planning import (
    AutonomousPresetRouter,
    LearningLoopOrchestrator,
    PlanningCheckpointManager,
    PRESET_PERSONALIZED_LEARNING_WORKFLOW,
)
from src.ai_modules.runtime.planning_contract import PlanningParamKeys
from src.ai_modules.runtime.resource_bundle_workflow import DEFAULT_RESOURCE_TYPES, ResourceBundleWorkflow

LOGGER = logging.getLogger(__name__)

REVIEW_REQUIRED_SERVICE_TYPES = {
    "PERSONALIZED_LEARNING",
    "RESOURCE_GENERATION",
    "VIDEO_GENERATION",
    "PATH_PLANNING",
    "EVALUATION",
    "LEARNING_EVALUATION",
}
EVALUATION_PROFILE_SERVICE_TYPES = {"EVALUATION", "LEARNING_EVALUATION"}


@dataclass
class ExecutionState:
    """Mutable state shared by streaming route helpers."""

    request: EngineStreamRequest
    params: dict[str, Any]
    snapshot: SystemSnapshot
    agent_registry: dict[str, Any] | None = None
    seq: int = 1


class SupervisorExecutionError(RuntimeError):
    """Route failure with a stable SSE error code."""

    def __init__(self, *, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class RoutePlan(BaseModel):
    """Resolved service route plan."""

    service_type: str = Field(alias="serviceType")
    agent_names: list[str] = Field(alias="agentNames")
    query_type: str | None = Field(default=None, alias="queryType")
    retrieval_strategy: str | None = Field(default=None, alias="retrievalStrategy")
    graph_intent: str | None = Field(default=None, alias="graphIntent")
    classification_confidence: float | None = Field(default=None, alias="classificationConfidence")
    classification_reason: str | None = Field(default=None, alias="classificationReason")
    planning_preset: str | None = Field(default=None, alias="planningPreset")
    planning_level: str = Field(default="static", alias="planningLevel")
    planner_reason: str | None = Field(default=None, alias="plannerReason")
    planner_confidence: float | None = Field(default=None, alias="plannerConfidence")

    model_config = ConfigDict(populate_by_name=True)


class PythonAgentSupervisor:
    """Resolve service routes and execute agents sequentially."""

    def __init__(self) -> None:
        self.snapshot_builder = SnapshotBuilder()
        self._background_tasks: set[asyncio.Task[None]] = set()
        self.route_templates = self._load_route_templates()
        self.query_classifier = QueryClassifier()
        self.preset_router = AutonomousPresetRouter()
        self.checkpoint_manager = PlanningCheckpointManager()
        self.learning_loop_orchestrator = LearningLoopOrchestrator(store=self.checkpoint_manager.store)
        self.agent_registry = self._build_agent_registry()

    def _build_agent_registry(self) -> dict[str, Any]:
        registry = {
            "query_rewrite": QueryRewriteAgent(),
            "retrieval": RetrievalAgent(),
            "document_generator": DocumentGeneratorAgent(),
            "slide_generator": SlideGeneratorAgent(),
            "reading_generator": ReadingGeneratorAgent(),
            "mindmap_generator": MindMapGeneratorAgent(),
            "code_generator": CodeGeneratorAgent(),
            "video_generator": VideoGenerationAgent(),
            "profile": ProfileAgent(),
            "practice": PracticeAgent(),
            "judge": JudgeAgent(),
            "path_planning": PathPlanningAgent(),
            "evaluation": EvaluationAgent(),
            "image_analysis": ImageAnalysisAgent(),
            "resource_push": ResourcePushAgent(),
            "critic": CriticAgent(),
        }

        async def run_resource_bundle(**kwargs: Any) -> AsyncIterator[SSEEvent]:
            async for event in self._run_tutoring_resource_bundle(agent_registry=registry, **kwargs):
                yield event

        registry["tutor"] = TutorAgent(resource_bundle_runner=run_resource_bundle)
        return registry

    def resolve_route(self, service_type: str, params: dict) -> RoutePlan:
        route_template = self.route_templates.get(service_type)
        if route_template is None:
            raise ValueError(f"Unsupported serviceType: {service_type}")
        query_type = None
        retrieval_strategy = None
        graph_intent = None
        classification_confidence = None
        classification_reason = None
        if service_type == "TUTORING":
            classification = self.query_classifier.classify(params)
            query_type = classification.query_type
            retrieval_strategy = classification.retrieval_strategy
            graph_intent = classification.graph_intent
            classification_confidence = classification.confidence
            classification_reason = classification.reason
            route_template = self._resolve_tutoring_route(classification, params=params)
        else:
            classification = None
        if service_type == "RESOURCE_GENERATION" and self._is_single_video_generation_request(params):
            resolved_route = ["query_rewrite", "retrieval", "video_generator"]
        elif service_type == "RESOURCE_GENERATION":
            resolved_route = ["query_rewrite", "retrieval", "resource_bundle"]
        elif service_type == "PRACTICE_JUDGE":
            resolved_route = self._resolve_practice_judge_route(params, route_template)
        else:
            resolved_route = list(route_template)
        planning_preset = None
        planning_level = "static"
        planner_reason = None
        planner_confidence = None
        try:
            decision = self.preset_router.route(
                service_type=service_type,
                params=params,
                classification=classification,
            )
            if (
                decision is not None
                and decision.confidence >= self.preset_router.min_confidence
                and not (service_type == "RESOURCE_GENERATION" and self._is_single_video_generation_request(params))
            ):
                preset_route = self.preset_router.expand(decision)
                resolved_route = list(preset_route.agent_names)
                retrieval_strategy = preset_route.retrieval_strategy or retrieval_strategy
                planning_preset = decision.preset
                planning_level = preset_route.planning_level
                planner_reason = decision.reason
                planner_confidence = decision.confidence
                if preset_route.param_updates:
                    params.update(preset_route.param_updates)
        except Exception:
            LOGGER.warning("Preset routing failed; falling back to static route", exc_info=True)

        return RoutePlan(
            serviceType=service_type,
            agentNames=resolved_route,
            queryType=query_type,
            retrievalStrategy=retrieval_strategy,
            graphIntent=graph_intent,
            classificationConfidence=classification_confidence,
            classificationReason=classification_reason,
            planningPreset=planning_preset,
            planningLevel=planning_level,
            plannerReason=planner_reason,
            plannerConfidence=planner_confidence,
        )

    def _load_route_templates(self) -> dict[str, list[str]]:
        config_path = Path(__file__).with_name("supervisor_routes.json")
        with config_path.open("r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        route_templates: dict[str, list[str]] = {}
        for service_type, agent_names in loaded.items():
            if not isinstance(service_type, str) or not isinstance(agent_names, list):
                continue
            route_templates[service_type.strip().upper()] = [str(agent_name) for agent_name in agent_names]
        return route_templates

    def _resolve_practice_judge_route(self, params: dict, route_template: list[str]) -> list[str]:
        answers = params.get("answers")
        has_answers = (
            isinstance(answers, dict)
            and any(str(value).strip() for value in answers.values())
        ) or (
            isinstance(answers, list)
            and any(
                isinstance(item, dict) and str(item.get("answer", "")).strip()
                for item in answers
            )
        )
        if has_answers:
            return ["judge", "profile"]
        return [agent_name for agent_name in route_template if agent_name == "practice"]

    def _is_single_video_generation_request(self, params: dict[str, Any]) -> bool:
        resource_types = ResourceBundleWorkflow.resolve_resource_types(params)
        raw_types = params.get("resourceTypes")
        has_resource_types_list = isinstance(raw_types, list) and bool(raw_types)
        return not has_resource_types_list and resource_types == ["VIDEO"]

    def _resolve_tutoring_route(self, classification, *, params: dict | None = None) -> list[str]:
        if self._has_conversational_resource_generation_intent(params or {}):
            return ["query_rewrite", "retrieval", "tutor"]
        if classification.confidence < self.query_classifier.low_confidence_threshold:
            return ["query_rewrite", "retrieval", "tutor"]
        if classification.query_type in {
            QUERY_TYPE_SMALL_TALK,
            QUERY_TYPE_FOLLOW_UP,
            QUERY_TYPE_ANSWER_PREVIOUS,
        }:
            return ["tutor"]
        if classification.query_type == QUERY_TYPE_IMAGE_QUESTION:
            return ["image_analysis", "query_rewrite", "retrieval", "tutor"]
        return ["query_rewrite", "retrieval", "tutor"]

    def _has_conversational_resource_generation_intent(self, params: dict[str, Any]) -> bool:
        return params.get(PlanningParamKeys.CONVERSATION_TRIGGERED_RESOURCE_GENERATION) is True

    async def build_snapshot(self, request: EngineStreamRequest) -> SystemSnapshot:
        return await self.snapshot_builder.build(
            user_id=request.user_id,
            task_id=request.task_id,
            conversation_id=request.conversation_id,
            params=request.params,
        )

    def build_agent_system_prompt(
        self,
        *,
        agent_name: str,
        snapshot: SystemSnapshot,
        agent_registry: dict[str, Any] | None = None,
    ) -> str:
        if agent_registry is None:
            agent_registry = self.agent_registry
        return agent_registry[agent_name].system_prompt(snapshot)

    async def stream(self, request: EngineStreamRequest, cancelled: Container[str] | None = None) -> AsyncIterator[SSEEvent]:
        route_plan = self.resolve_route(request.service_type, request.params)
        current_params = self._seed_request_params(request)
        self._seed_query_routing_params(current_params, route_plan)
        snapshot = await self.snapshot_builder.build(
            user_id=request.user_id,
            task_id=request.task_id,
            conversation_id=request.conversation_id,
            params=current_params,
        )
        state = ExecutionState(
            request=request,
            params=current_params,
            snapshot=snapshot,
            agent_registry=self.agent_registry,
        )

        try:
            if self._should_start_goal_loop(route_plan=route_plan, params=state.params):
                loop_payload = await self.learning_loop_orchestrator.start_loop(
                    params=state.params,
                    user_id=self._effective_user_id(state),
                    task_id=state.request.task_id,
                    conversation_id=state.request.conversation_id,
                )
                yield self._progress_event(
                    state=state,
                    stage="goal_planning",
                    percent=8,
                    message=f"Level 3 goal loop planned {len(loop_payload.get('goals', []))} subgoals",
                    agent_name="goal_planner",
                    phase="decompose",
                    status="DONE",
                )
            async for event in self._execute_service_route(
                state=state,
                route_plan=route_plan,
                cancelled=cancelled,
            ):
                yield event
            if self._should_review_route(route_plan=route_plan, params=state.params):
                async for event in self._run_critic_review(state=state, service_type=route_plan.service_type):
                    yield event
                async for event in self._run_post_agent_checkpoints(
                    state=state,
                    route_plan=route_plan,
                    agent_name="critic",
                ):
                    yield event
            if self._should_close_goal_loop(route_plan=route_plan, params=state.params):
                loop_payload = await self.learning_loop_orchestrator.close_loop(
                    params=state.params,
                    user_id=self._effective_user_id(state),
                )
                if loop_payload:
                    yield self._progress_event(
                        state=state,
                        stage="goal_critic",
                        percent=96,
                        message=f"Level 3 goal loop {loop_payload.get('status')}",
                        agent_name="goal_critic",
                        phase="verify",
                        status=str(loop_payload.get("status") or "DONE"),
                    )
            if self._should_schedule_background_profile(
                service_type=route_plan.service_type,
                params=state.params,
            ):
                self._schedule_background_profile(state=state, service_type=request.service_type)

            yield DoneSSEEvent(
                taskId=request.task_id,
                traceId=request.trace_id,
                seq=state.seq,
                payload=self._build_done_payload(
                    service_type=route_plan.service_type,
                    agent_names=route_plan.agent_names,
                    params=state.params,
                ),
            )
        except Exception as exc:
            code = exc.code if isinstance(exc, SupervisorExecutionError) else "SUPERVISOR_FAILED"
            message = (
                exc.message
                if isinstance(exc, SupervisorExecutionError)
                else f"Supervisor execution failed: {type(exc).__name__}: {exc}"
            )
            LOGGER.exception(message)
            yield ErrorSSEEvent(
                taskId=request.task_id,
                traceId=request.trace_id,
                seq=state.seq,
                payload=ErrorPayload(code=code, message=message),
            )
            state.seq += 1
            yield DoneSSEEvent(
                taskId=request.task_id,
                traceId=request.trace_id,
                seq=state.seq,
                payload=DonePayload(
                    status="FAILED",
                    summary=message,
                    masteryDiagnosis=self._safe_dict(state.params.get("masteryDiagnosis")),
                    learningPath=self._safe_dict(state.params.get("learningPath")),
                    learningPlan=self._safe_dict(state.params.get("learningPlan")),
                    resourcePushPlan=self._safe_dict(state.params.get("resourcePushPlan")),
                    pushedResources=state.params.get("pushedResources") if isinstance(state.params.get("pushedResources"), list) else [],
                    agentTrace=state.params.get("agentTrace") if isinstance(state.params.get("agentTrace"), list) else [],
                    criticReview=self._safe_dict(state.params.get("criticReview")),
                    planning=self._safe_dict(state.params.get("planning")),
                    checkpointActions=state.params.get(PlanningParamKeys.CHECKPOINT_ACTIONS)
                    if isinstance(state.params.get(PlanningParamKeys.CHECKPOINT_ACTIONS), list)
                    else [],
                    learningLoop=self._safe_dict(state.params.get(PlanningParamKeys.LEARNING_LOOP)),
                ),
            )
            return

    async def _execute_service_route(
        self,
        *,
        state: ExecutionState,
        route_plan: RoutePlan,
        cancelled: Container[str] | None = None,
    ) -> AsyncIterator[SSEEvent]:
        if route_plan.service_type == "RESOURCE_GENERATION" and "resource_bundle" in route_plan.agent_names:
            async for event in self._execute_resource_bundle_route(
                state=state,
                route_plan=route_plan,
                cancelled=cancelled,
            ):
                yield event
            return

        agent_names = list(route_plan.agent_names)
        i = 0
        while i < len(agent_names):
            self._raise_if_cancelled(state=state, cancelled=cancelled)
            agent_name = agent_names[i]
            if (
                agent_name == "query_rewrite"
                and i + 1 < len(agent_names)
                and agent_names[i + 1] == "retrieval"
            ):
                async for event in self._run_agent_pair_rewrite_retrieval(state=state, service_type=route_plan.service_type):
                    yield event
                async for event in self._run_post_agent_checkpoints(
                    state=state,
                    route_plan=route_plan,
                    agent_name="retrieval",
                ):
                    yield event
                i += 2
                continue
            if agent_name == "resource_bundle":
                self._prepare_resource_bundle_params(state.params)
                async for event in self._execute_resource_bundle_route(
                    state=state,
                    route_plan=route_plan,
                    cancelled=cancelled,
                ):
                    yield event
                self._append_agent_trace(state.params, agent_name="resource_bundle", status="DONE")
                i += 1
                continue

            async for event in self._run_single_agent(state=state, agent_name=agent_name, service_type=route_plan.service_type):
                self._collect_final_answer_from_event(state=state, agent_name=agent_name, event=event)
                yield event
            async for event in self._run_post_agent_checkpoints(
                state=state,
                route_plan=route_plan,
                agent_name=agent_name,
            ):
                yield event
            i += 1

    async def _execute_resource_bundle_route(
        self,
        *,
        state: ExecutionState,
        route_plan: RoutePlan,
        cancelled: Container[str] | None = None,
    ) -> AsyncIterator[SSEEvent]:
        workflow_request = state.request.model_copy(update={"service_type": route_plan.service_type})
        agent_registry = self._agent_registry(state)
        workflow = ResourceBundleWorkflow(
            agent_registry=agent_registry,
            snapshot_builder=self.snapshot_builder,
            system_prompt_builder=lambda agent_name, snapshot: self.build_agent_system_prompt(
                agent_registry=agent_registry,
                agent_name=agent_name,
                snapshot=snapshot,
            ),
        )
        try:
            async for event in workflow.stream(
                request=workflow_request,
                params=state.params,
                snapshot=state.snapshot,
                seq=state.seq,
                cancelled=cancelled,
            ):
                yield event
            final_state = workflow.last_state
            if final_state is None:
                raise RuntimeError("Resource bundle workflow finished without final state")
        except Exception as exc:
            message = self._resource_bundle_error_message(exc)
            LOGGER.exception(message)
            state.seq = workflow.last_state.seq if workflow.last_state is not None else state.seq
            raise SupervisorExecutionError(code="RESOURCE_BUNDLE_FAILED", message=message) from exc
        state.params.update(final_state.params)
        state.seq = final_state.seq
        state.snapshot = final_state.snapshot
        async for event in self._run_post_agent_checkpoints(
            state=state,
            route_plan=route_plan,
            agent_name="resource_bundle",
        ):
            yield event

    async def _run_tutoring_resource_bundle(
        self,
        *,
        agent_registry: dict[str, Any],
        task_id: str,
        trace_id: str,
        seq: int,
        params: dict[str, Any],
        snapshot: SystemSnapshot,
    ) -> AsyncIterator[SSEEvent]:
        workflow_request = EngineStreamRequest(
            serviceType="RESOURCE_GENERATION",
            params=params,
            userId=str(params.get("userId")) if params.get("userId") else None,
            taskId=task_id,
            traceId=trace_id,
            conversationId=str(params.get("conversationId")) if params.get("conversationId") else None,
        )
        workflow = ResourceBundleWorkflow(
            agent_registry=agent_registry,
            snapshot_builder=self.snapshot_builder,
            system_prompt_builder=lambda agent_name, current_snapshot: self.build_agent_system_prompt(
                agent_registry=agent_registry,
                agent_name=agent_name,
                snapshot=current_snapshot,
            ),
        )
        try:
            async for event in workflow.stream(
                request=workflow_request,
                params=params,
                snapshot=snapshot,
                seq=seq,
            ):
                yield event
            final_state = workflow.last_state
            if final_state is None:
                raise RuntimeError("Resource bundle workflow finished without final state")
        except Exception as exc:
            message = self._resource_bundle_error_message(exc)
            LOGGER.exception(message)
            raise SupervisorExecutionError(code="RESOURCE_BUNDLE_FAILED", message=message) from exc
        params.update(final_state.params)

    @staticmethod
    def _resource_bundle_error_message(exc: Exception) -> str:
        message = str(exc).strip()
        lowered = message.lower()
        if "resource bundle generation failed" in lowered:
            return "Resource bundle generation failed: no requested resource could be generated; please retry with a clearer topic or fewer resource types"
        if "no supported resource types requested" in lowered:
            return "Resource bundle generation failed: no supported resource types were requested"
        if "cancelled" in lowered or "鍙栨秷" in message:
            return "Resource bundle generation was cancelled"
        return "Resource bundle generation failed; please retry later"

    def _prepare_resource_bundle_params(self, params: dict[str, Any]) -> None:
        if not isinstance(params.get("learningPath"), dict):
            return
        params["skipResourceBundlePrelude"] = True
        if not params.get("resourceTypes"):
            params["resourceTypes"] = self._resource_types_from_learning_path(params["learningPath"])
        if not params.get("topic"):
            params["topic"] = self._topic_from_learning_path(params["learningPath"])

    def _resource_types_from_learning_path(self, learning_path: dict[str, Any]) -> list[str]:
        resolved: list[str] = []
        for step in learning_path.get("steps", []):
            if not isinstance(step, dict):
                continue
            for raw_type in step.get("preferredResourceTypes", []):
                resource_type = str(raw_type).strip().upper()
                if resource_type and resource_type not in resolved:
                    resolved.append(resource_type)
        return resolved[:4] or list(DEFAULT_RESOURCE_TYPES)

    def _topic_from_learning_path(self, learning_path: dict[str, Any]) -> str:
        for step in learning_path.get("steps", []):
            if not isinstance(step, dict):
                continue
            for point in step.get("targetKnowledgePoints", []):
                if str(point).strip():
                    return str(point).strip()
            if str(step.get("title") or "").strip():
                return str(step["title"]).strip()
        return str(learning_path.get("goal") or "个性化学习方案").strip()

    async def _run_agent_pair_rewrite_retrieval(
        self,
        *,
        state: ExecutionState,
        service_type: str,
    ) -> AsyncIterator[SSEEvent]:
        async for event in self._run_single_agent(state=state, agent_name="query_rewrite", service_type=service_type):
            yield event
        async for event in self._run_single_agent(state=state, agent_name="retrieval", service_type=service_type):
            yield event

    async def _run_post_agent_checkpoints(
        self,
        *,
        state: ExecutionState,
        route_plan: RoutePlan,
        agent_name: str,
    ) -> AsyncIterator[ProgressSSEEvent]:
        if not self._checkpoint_enabled(route_plan=route_plan):
            return
        before_count = (
            len(state.params.get(PlanningParamKeys.CHECKPOINT_ACTIONS, []))
            if isinstance(state.params.get(PlanningParamKeys.CHECKPOINT_ACTIONS), list)
            else 0
        )
        user_id = self._effective_user_id(state)
        loop_id, subgoal_id = self._current_loop_ids(state.params)
        if agent_name == "profile":
            await self.checkpoint_manager.check_profile_completeness(
                params=state.params,
                user_id=user_id,
                loop_id=loop_id,
                subgoal_id=subgoal_id,
            )
        elif agent_name == "retrieval":
            max_attempts = max(1, len(self.checkpoint_manager.retrieval_upgrade_order))
            for _ in range(max_attempts):
                action = await self.checkpoint_manager.check_retrieval_evidence(
                    params=state.params,
                    user_id=user_id,
                    loop_id=loop_id,
                    subgoal_id=subgoal_id,
                )
                if not action or action.status != "APPLIED":
                    break
                reran = await self._rerun_retrieval_after_checkpoint(state=state, route_plan=route_plan)
                if not reran:
                    break
        elif agent_name in {"resource_bundle", "critic"} and self._resource_coverage_checkpoint_enabled(
            route_plan=route_plan,
            params=state.params,
        ):
            action = await self.checkpoint_manager.check_resource_coverage(
                params=state.params,
                user_id=user_id,
                loop_id=loop_id,
                subgoal_id=subgoal_id,
            )
        actions = state.params.get(PlanningParamKeys.CHECKPOINT_ACTIONS)
        if not isinstance(actions, list) or len(actions) <= before_count:
            return
        for action in actions[before_count:]:
            if not isinstance(action, dict):
                continue
            yield self._progress_event(
                state=state,
                stage="planning_checkpoint",
                percent=88,
                message=f"{action.get('checkpointType')}: {action.get('action')}",
                agent_name="planning_checkpoint",
                phase=str(action.get("checkpointType") or ""),
                status=str(action.get("status") or "RECORDED"),
            )
        if (
            agent_name == "resource_bundle"
            and route_plan.service_type == "RESOURCE_GENERATION"
            and isinstance(actions, list)
            and any(
                isinstance(action, dict)
                and action.get("checkpointType") == "RESOURCE_COVERAGE"
                and action.get("status") == "APPLIED"
                for action in actions[before_count:]
            )
        ):
            async for event in self._rerun_resource_bundle_after_coverage_checkpoint(
                state=state,
                route_plan=route_plan,
            ):
                yield event

    async def _rerun_retrieval_after_checkpoint(self, *, state: ExecutionState, route_plan: RoutePlan) -> bool:
        state.params.setdefault(PlanningParamKeys.PLANNING_TRACE, []).append(
            {
                "agentName": "retrieval",
                "status": "RETRY",
                "retrievalStrategy": state.params.get(PlanningParamKeys.RETRIEVAL_STRATEGY),
            }
        )
        retry_count = int(state.params.get(PlanningParamKeys.CHECKPOINT_RETRIEVAL_RERUN_COUNT) or 0)
        state.params[PlanningParamKeys.CHECKPOINT_RETRIEVAL_RERUN_COUNT] = retry_count + 1
        agent_registry = self._agent_registry(state)
        agent = agent_registry["retrieval"]
        agent_params = copy.deepcopy(state.params)
        system_prompt = self.build_agent_system_prompt(
            agent_registry=agent_registry,
            agent_name="retrieval",
            snapshot=state.snapshot,
        )
        async for _ in agent.run(
            task_id=state.request.task_id,
            trace_id=state.request.trace_id,
            seq=state.seq,
            service_type=route_plan.service_type,
            params=agent_params,
            snapshot=state.snapshot,
            system_prompt=system_prompt,
        ):
            pass
        state.params.update(agent_params)
        await self._refresh_snapshot(state)
        return True

    async def _rerun_resource_bundle_after_coverage_checkpoint(
        self,
        *,
        state: ExecutionState,
        route_plan: RoutePlan,
    ) -> AsyncIterator[SSEEvent]:
        if state.params.get(PlanningParamKeys.CHECKPOINT_RESOURCE_COVERAGE_RERUN_DONE) is True:
            return
        supplement_types = state.params.get(PlanningParamKeys.RESOURCE_COVERAGE_SUPPLEMENT_TYPES)
        if not isinstance(supplement_types, list) or not supplement_types:
            return
        state.params[PlanningParamKeys.CHECKPOINT_RESOURCE_COVERAGE_RERUN_DONE] = True
        existing_assets = copy.deepcopy(state.params.get("generatedAssets"))
        existing_assets = existing_assets if isinstance(existing_assets, list) else []
        existing_failures = copy.deepcopy(state.params.get("resourceFailures"))
        existing_failures = existing_failures if isinstance(existing_failures, list) else []
        planned_resource_types = copy.deepcopy(state.params.get(PlanningParamKeys.RESOURCE_TYPES))
        planned_resource_types = planned_resource_types if isinstance(planned_resource_types, list) else []
        state.params.setdefault(PlanningParamKeys.PLANNING_TRACE, []).append(
            {
                "agentName": "resource_bundle",
                "status": "RETRY",
                "reason": "resource_coverage_supplement",
                PlanningParamKeys.RESOURCE_TYPES: supplement_types,
            }
        )
        supplement_params = copy.deepcopy(state.params)
        supplement_params[PlanningParamKeys.RESOURCE_TYPES] = supplement_types
        supplement_params["skipResourceBundlePrelude"] = True
        workflow_request = state.request.model_copy(update={"service_type": route_plan.service_type})
        agent_registry = self._agent_registry(state)
        workflow = ResourceBundleWorkflow(
            agent_registry=agent_registry,
            snapshot_builder=self.snapshot_builder,
            system_prompt_builder=lambda agent_name, snapshot: self.build_agent_system_prompt(
                agent_registry=agent_registry,
                agent_name=agent_name,
                snapshot=snapshot,
            ),
        )
        async for event in workflow.stream(
            request=workflow_request,
            params=supplement_params,
            snapshot=state.snapshot,
            seq=state.seq,
        ):
            yield event
        final_state = workflow.last_state
        if final_state is None:
            raise SupervisorExecutionError(
                code="RESOURCE_BUNDLE_FAILED",
                message="Resource coverage supplement finished without final state",
            )
        state.params.update(final_state.params)
        state.params[PlanningParamKeys.RESOURCE_TYPES] = planned_resource_types
        state.params["generatedAssets"] = self._merge_resource_payload_lists(
            existing_assets,
            final_state.params.get("generatedAssets"),
        )
        state.params.pop("pendingSlideOutlines", None)
        state.params["resourceFailures"] = self._merge_resource_payload_lists(
            existing_failures,
            final_state.params.get("resourceFailures"),
        )
        if state.params["generatedAssets"]:
            state.params["generatedAsset"] = state.params["generatedAssets"][0]
        state.params[PlanningParamKeys.RESOURCE_COVERAGE_STATUS] = "SUPPLEMENTED"
        state.seq = final_state.seq
        state.snapshot = final_state.snapshot

    @staticmethod
    def _merge_resource_payload_lists(existing: list[Any], new_value: Any) -> list[Any]:
        merged: list[Any] = []
        seen: set[tuple[str, str, str]] = set()
        for item in [*existing, *(new_value if isinstance(new_value, list) else [])]:
            if not isinstance(item, dict):
                continue
            identity = (
                str(item.get("assetType") or item.get("resourceType") or ""),
                str(item.get("title") or ""),
                str(item.get("fileName") or item.get("downloadUrl") or item.get("error") or ""),
            )
            if identity in seen:
                continue
            seen.add(identity)
            merged.append(item)
        return merged

    def _collect_final_answer_from_event(self, *, state: ExecutionState, agent_name: str, event: SSEEvent) -> None:
        if agent_name != "tutor" or event.event != "result_chunk":
            return
        payload = getattr(event, "payload", None)
        text = str(getattr(payload, "text", "") or "")
        if not text.strip():
            return
        if getattr(payload, "stage", None) not in (None, "", "tutoring"):
            return
        previous = str(state.params.get("finalAnswer") or "")
        state.params["finalAnswer"] = f"{previous}{text}"
        state.params["generatedContent"] = state.params["finalAnswer"]

    async def _run_single_agent(
        self,
        *,
        state: ExecutionState,
        agent_name: str,
        service_type: str,
    ) -> AsyncIterator[SSEEvent]:
        agent_registry = self._agent_registry(state)
        agent = agent_registry[agent_name]
        agent_params = copy.deepcopy(state.params)
        system_prompt = self.build_agent_system_prompt(
            agent_registry=agent_registry,
            agent_name=agent_name,
            snapshot=state.snapshot,
        )
        async for event in agent.run(
            task_id=state.request.task_id,
            trace_id=state.request.trace_id,
            seq=state.seq,
            service_type=service_type,
            params=agent_params,
            snapshot=state.snapshot,
            system_prompt=system_prompt,
        ):
            yield self._normalize_agent_event(agent_name=agent_name, event=event).model_copy(update={"seq": state.seq})
            state.seq += 1
        state.params.update(agent_params)
        self._append_agent_trace(state.params, agent_name=agent_name, status="DONE")
        await self._refresh_snapshot(state)

    def _normalize_agent_event(self, *, agent_name: str, event: SSEEvent) -> SSEEvent:
        if event.event != "result_chunk":
            return event
        payload = getattr(event, "payload", None)
        if getattr(payload, "stage", None):
            return event
        stage = "tutoring" if agent_name == "tutor" else agent_name
        return event.model_copy(update={"payload": payload.model_copy(update={"stage": stage})})

    async def _run_critic_review(self, *, state: ExecutionState, service_type: str) -> AsyncIterator[SSEEvent]:
        agent_registry = self._agent_registry(state)
        critic_agent = agent_registry["critic"]
        critic_prompt = self.build_agent_system_prompt(
            agent_registry=agent_registry,
            agent_name="critic",
            snapshot=state.snapshot,
        )
        critic_params = copy.deepcopy(state.params)
        async for event in critic_agent.run(
            task_id=state.request.task_id,
            trace_id=state.request.trace_id,
            seq=state.seq,
            service_type=service_type,
            params=critic_params,
            snapshot=state.snapshot,
            system_prompt=critic_prompt,
        ):
            yield self._normalize_agent_event(agent_name="critic", event=event).model_copy(update={"seq": state.seq})
            state.seq += 1
        state.params.update(critic_params)
        self._append_agent_trace(state.params, agent_name="critic", status="DONE")
        await self._refresh_snapshot(state)

    def _progress_event(
        self,
        *,
        state: ExecutionState,
        stage: str,
        percent: int,
        message: str,
        agent_name: str,
        phase: str,
        status: str,
    ) -> ProgressSSEEvent:
        event = ProgressSSEEvent(
            taskId=state.request.task_id,
            traceId=state.request.trace_id,
            seq=state.seq,
            payload=ProgressPayload(
                stage=stage,
                percent=percent,
                message=message,
                agentName=agent_name,
                phase=phase,
                status=status,
            ),
        )
        state.seq += 1
        return event

    def _agent_registry(self, state: ExecutionState) -> dict[str, Any]:
        if state.agent_registry is None:
            state.agent_registry = self.agent_registry
        return state.agent_registry

    def _build_done_payload(self, *, service_type: str, agent_names: list[str], params: dict) -> DonePayload:
        mastery_diagnosis = self._safe_dict(params.get("masteryDiagnosis"))
        learning_plan = self._safe_dict(params.get("learningPlan"))
        critic_review = self._safe_dict(params.get("criticReview"))
        judge_result = self._safe_dict(params.get("judgeResult"))
        learning_path = params.get("learningPath")
        resource_push_plan = self._safe_dict(params.get("resourcePushPlan"))
        pushed_resources = params.get("pushedResources")
        if not isinstance(pushed_resources, list):
            pushed_resources = []
        agent_trace = params.get("agentTrace")
        if not isinstance(agent_trace, list):
            agent_trace = []
        generated_assets = params.get("generatedAssets")
        resource_failures = params.get("resourceFailures")
        if not isinstance(resource_failures, list):
            resource_failures = []
        def done(**kwargs: Any) -> DonePayload:
            return self._attach_planning_payload(DonePayload(**kwargs), params=params)

        if service_type == "RESOURCE_GENERATION" and isinstance(generated_assets, list) and generated_assets:
            if resource_failures:
                failed_types = "、".join(
                    str(item.get("resourceType") or "UNKNOWN")
                    for item in resource_failures[:3]
                    if isinstance(item, dict)
                )
                return done(
                    status="PARTIAL_FAILED",
                    summary=(
                        f"资源包部分完成，共生成 {len(generated_assets)} 个资源；"
                        f"{len(resource_failures)} 个资源失败：{failed_types}"
                    ),
                    masteryDiagnosis=mastery_diagnosis,
                    learningPlan=learning_plan,
                    criticReview=critic_review,
                    resourceFailures=resource_failures,
                )
            return done(
                status="SUCCESS",
                summary=f"资源包生成完成，共生成 {len(generated_assets)} 个资源",
                masteryDiagnosis=mastery_diagnosis,
                learningPlan=learning_plan,
                criticReview=critic_review,
                resourceFailures=[],
            )
        generated_asset = params.get("generatedAsset")
        if service_type in {"RESOURCE_GENERATION", "VIDEO_GENERATION"} and isinstance(generated_asset, dict):
            title = str(generated_asset.get("title") or "资源")
            summary = str(generated_asset.get("summary") or "").strip()
            return done(
                status="SUCCESS",
                summary=f"{title} 生成完成：{summary}" if summary else f"{title} 生成完成",
                masteryDiagnosis=mastery_diagnosis,
                learningPlan=learning_plan,
                criticReview=critic_review,
            )
        if service_type == "RESOURCE_PUSH" and isinstance(pushed_resources, list):
            if not pushed_resources:
                return done(
                    status="SUCCESS",
                    summary="资源推送未命中可直接分发的现成资源",
                    masteryDiagnosis=mastery_diagnosis,
                    learningPlan=learning_plan,
                    resourcePushPlan=resource_push_plan,
                    pushedResources=pushed_resources,
                    criticReview=critic_review,
                )
            titles = "、".join(
                str(item.get("title") or "资源")
                for item in pushed_resources[:3]
                if isinstance(item, dict)
            )
            return done(
                status="SUCCESS",
                summary=f"资源推送完成，已匹配 {len(pushed_resources)} 个现成资源：{titles}",
                masteryDiagnosis=mastery_diagnosis,
                learningPlan=learning_plan,
                resourcePushPlan=resource_push_plan,
                pushedResources=pushed_resources,
                criticReview=critic_review,
            )
        if service_type == "PATH_PLANNING" and isinstance(learning_path, dict):
            summary = str(learning_path.get("summaryText") or "").strip()
            if not summary:
                summary = f"{service_type} 路由完成，执行链路: {' -> '.join(agent_names)}"
            return done(
                status="SUCCESS",
                summary=summary,
                masteryDiagnosis=mastery_diagnosis,
                learningPath=learning_path,
                learningPlan=learning_plan,
                criticReview=critic_review,
            )
        if service_type == "PERSONALIZED_LEARNING":
            summary = ""
            if isinstance(learning_path, dict):
                summary = str(learning_path.get("summaryText") or "").strip()
            if not summary:
                summary = f"个性化学习方案已生成，执行链路: {' -> '.join(agent_names)}"
            return done(
                status="SUCCESS",
                summary=summary,
                masteryDiagnosis=mastery_diagnosis,
                learningPath=learning_path if isinstance(learning_path, dict) else None,
                learningPlan=learning_plan,
                resourcePushPlan=resource_push_plan,
                pushedResources=pushed_resources,
                agentTrace=agent_trace,
                criticReview=critic_review,
            )
        return done(
            status="SUCCESS",
            summary=f"{service_type} 路由完成，执行链路: {' -> '.join(agent_names)}",
            masteryDiagnosis=mastery_diagnosis,
            learningPlan=learning_plan,
            judgeResult=judge_result,
            agentTrace=agent_trace,
            criticReview=critic_review,
        )

    def _seed_request_params(self, request: EngineStreamRequest) -> dict:
        seeded_params = copy.deepcopy(request.params)
        if request.user_id and not seeded_params.get("userId"):
            seeded_params["userId"] = request.user_id
        if request.conversation_id and not seeded_params.get("conversationId"):
            seeded_params["conversationId"] = request.conversation_id
        return seeded_params

    def _seed_query_routing_params(self, params: dict, route_plan: RoutePlan) -> None:
        if route_plan.query_type:
            params["queryType"] = route_plan.query_type
        if route_plan.retrieval_strategy:
            params["retrievalStrategy"] = route_plan.retrieval_strategy
        if route_plan.graph_intent:
            params["graphIntent"] = route_plan.graph_intent
        if route_plan.query_type or route_plan.retrieval_strategy or route_plan.graph_intent:
            params["queryClassification"] = {
                "queryType": route_plan.query_type,
                "retrievalStrategy": route_plan.retrieval_strategy,
                "graphIntent": route_plan.graph_intent,
                "confidence": route_plan.classification_confidence,
                "reason": route_plan.classification_reason,
            }
        params["planning"] = {
            "preset": route_plan.planning_preset,
            "level": route_plan.planning_level,
            "reason": route_plan.planner_reason,
            "confidence": route_plan.planner_confidence,
            "fallback": route_plan.planning_preset is None,
            "plannedAgents": list(route_plan.agent_names),
        }

    def _attach_planning_payload(self, payload: DonePayload, *, params: dict) -> DonePayload:
        planning = self._safe_dict(params.get("planning"))
        if planning is not None and isinstance(params.get(PlanningParamKeys.PLANNING_TRACE), list):
            planning = {**planning, "trace": params[PlanningParamKeys.PLANNING_TRACE]}
        return payload.model_copy(
            update={
                "planning": planning,
                "checkpoint_actions": params.get(PlanningParamKeys.CHECKPOINT_ACTIONS)
                if isinstance(params.get(PlanningParamKeys.CHECKPOINT_ACTIONS), list)
                else [],
                "learning_loop": self._safe_dict(params.get(PlanningParamKeys.LEARNING_LOOP)),
            }
        )

    def _should_start_goal_loop(self, *, route_plan: RoutePlan, params: dict) -> bool:
        return (
            route_plan.planning_level == "goal_loop"
            and route_plan.planning_preset == PRESET_PERSONALIZED_LEARNING_WORKFLOW
            and not isinstance(params.get(PlanningParamKeys.LEARNING_LOOP), dict)
        )

    def _should_close_goal_loop(self, *, route_plan: RoutePlan, params: dict) -> bool:
        return route_plan.planning_level == "goal_loop" and isinstance(params.get(PlanningParamKeys.LEARNING_LOOP), dict)

    def _checkpoint_enabled(self, *, route_plan: RoutePlan) -> bool:
        return route_plan.planning_level in {"checkpoint_replan", "goal_loop"}

    def _resource_coverage_checkpoint_enabled(self, *, route_plan: RoutePlan, params: dict) -> bool:
        if route_plan.planning_level == "goal_loop":
            return True
        if route_plan.service_type != "RESOURCE_GENERATION":
            return False
        return not self._has_explicit_resource_type_selection(params)

    @staticmethod
    def _has_explicit_resource_type_selection(params: dict[str, Any]) -> bool:
        raw_types = params.get(PlanningParamKeys.RESOURCE_TYPES)
        if isinstance(raw_types, list) and bool(raw_types):
            return True
        raw_type = params.get(PlanningParamKeys.RESOURCE_TYPE)
        return isinstance(raw_type, str) and bool(raw_type.strip())

    def _effective_user_id(self, state: ExecutionState) -> str:
        value = state.request.user_id or state.params.get("userId")
        return str(value or "00000000-0000-0000-0000-000000000001")

    def _current_loop_ids(self, params: dict) -> tuple[str | None, str | None]:
        loop = params.get(PlanningParamKeys.LEARNING_LOOP)
        if not isinstance(loop, dict):
            return None, None
        loop_id = str(loop.get("loopId") or "") or None
        current_index = int(loop.get("currentGoalIndex") or 1)
        subgoal_id = None
        goals = loop.get("goals")
        if isinstance(goals, list):
            for goal in goals:
                if not isinstance(goal, dict):
                    continue
                if int(goal.get("orderIndex") or 0) == current_index:
                    subgoal_id = str(goal.get("subgoalId") or "") or None
                    break
        return loop_id, subgoal_id

    def _should_schedule_background_profile(self, *, service_type: str, params: dict) -> bool:
        normalized_service_type = service_type.strip().upper()
        if normalized_service_type in EVALUATION_PROFILE_SERVICE_TYPES:
            evaluation_result = params.get("evaluationResult")
            return isinstance(evaluation_result, dict) and bool(evaluation_result)
        if normalized_service_type != "TUTORING":
            return False
        if params.get("forceProfileUpdate") is True:
            return True
        user_turn_count = self._count_user_turns(params)
        return user_turn_count > 0 and user_turn_count % 3 == 0

    def _count_user_turns(self, params: dict) -> int:
        messages = params.get("messages") or params.get("conversation")
        if not isinstance(messages, list):
            return 1 if self._current_user_query(params) else 0
        count = 0
        last_user_content = ""
        for item in messages:
            if not isinstance(item, dict) or item.get("role") != "user":
                continue
            content = str(item.get("content") or "").strip()
            if content:
                count += 1
                last_user_content = content
        current_query = self._current_user_query(params)
        if current_query and self._normalize_turn_text(current_query) != self._normalize_turn_text(last_user_content):
            count += 1
        return count

    def _current_user_query(self, params: dict) -> str:
        for key in ("query", "message", "userInput", "question"):
            value = params.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    def _normalize_turn_text(self, text: str) -> str:
        return "".join(str(text).split())

    async def _refresh_snapshot(self, state: ExecutionState) -> None:
        state.snapshot = await self.snapshot_builder.build(
            user_id=state.request.user_id,
            task_id=state.request.task_id,
            conversation_id=state.request.conversation_id,
            params=state.params,
        )

    def _raise_if_cancelled(self, *, state: ExecutionState, cancelled: Container[str] | None) -> None:
        if cancelled and state.request.task_id in cancelled:
            raise RuntimeError("任务已被取消")

    def _has_explicit_response_length_limit(self, params: dict) -> bool:
        text = str(
            params.get("query")
            or params.get("message")
            or params.get("userInput")
            or params.get("question")
            or ""
        )
        return re.search(r"\d{2,4}\s*(?:字|个字|字符)\s*(?:以内|内|之内|以下|左右)?", text) is not None

    def _should_review_route(self, *, route_plan: RoutePlan, params: dict) -> bool:
        if "critic" in route_plan.agent_names:
            return False
        if route_plan.service_type in REVIEW_REQUIRED_SERVICE_TYPES:
            return True
        return False

    def _append_agent_trace(self, params: dict, *, agent_name: str, status: str) -> None:
        trace = params.get("agentTrace")
        if not isinstance(trace, list):
            trace = []
            params["agentTrace"] = trace
        trace.append({"agentName": agent_name, "status": status})

    @staticmethod
    def _safe_dict(value: Any) -> dict[str, Any] | None:
        return value if isinstance(value, dict) else None

    def _schedule_background_profile(self, *, state: ExecutionState, service_type: str) -> None:
        profile_agent = state.agent_registry["profile"]
        profile_prompt = self.build_agent_system_prompt(
            agent_registry=state.agent_registry,
            agent_name="profile",
            snapshot=state.snapshot,
        )
        profile_params = copy.deepcopy(state.params)
        if service_type.strip().upper() in EVALUATION_PROFILE_SERVICE_TYPES and not profile_params.get("profileSource"):
            profile_params["profileSource"] = "EVALUATION"
        self._schedule_background_agent(
            agent=profile_agent,
            agent_name="profile",
            task_id=state.request.task_id,
            trace_id=state.request.trace_id,
            service_type=service_type,
            params=profile_params,
            snapshot=state.snapshot,
            system_prompt=profile_prompt,
        )

    def _schedule_background_agent(
        self,
        *,
        agent: Any,
        agent_name: str,
        task_id: str,
        trace_id: str,
        service_type: str,
        params: dict,
        snapshot: SystemSnapshot,
        system_prompt: str,
    ) -> None:
        task = asyncio.create_task(
            self._drain_background_agent(
                agent=agent,
                agent_name=agent_name,
                task_id=task_id,
                trace_id=trace_id,
                service_type=service_type,
                params=params,
                snapshot=snapshot,
                system_prompt=system_prompt,
            ),
            name=f"background-{agent_name}:{task_id}",
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _drain_background_agent(
        self,
        *,
        agent: Any,
        agent_name: str,
        task_id: str,
        trace_id: str,
        service_type: str,
        params: dict,
        snapshot: SystemSnapshot,
        system_prompt: str,
    ) -> None:
        try:
            async for _ in agent.run(
                task_id=task_id,
                trace_id=trace_id,
                seq=1,
                service_type=service_type,
                params=params,
                snapshot=snapshot,
                system_prompt=system_prompt,
            ):
                pass
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("后台画像构建失败，已与 Tutor 主链路隔离: task_id=%s agent=%s", task_id, agent_name)
