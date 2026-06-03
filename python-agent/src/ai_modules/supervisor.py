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
    DeepReasoningAgent,
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
    QUERY_TYPE_DEEP_REASONING,
    QUERY_TYPE_FOLLOW_UP,
    QUERY_TYPE_IMAGE_QUESTION,
    QUERY_TYPE_SMALL_TALK,
    QueryClassifier,
)
from src.ai_modules.runtime import SnapshotBuilder, SystemSnapshot
from src.ai_modules.runtime.conversation_planner import ConversationPlanner
from src.ai_modules.runtime.resource_bundle_workflow import ResourceBundleWorkflow

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

    model_config = ConfigDict(populate_by_name=True)


class PythonAgentSupervisor:
    """Resolve service routes and execute agents sequentially."""

    def __init__(self) -> None:
        self.snapshot_builder = SnapshotBuilder()
        self._background_tasks: set[asyncio.Task[None]] = set()
        self.agent_registry = {
            "query_rewrite": QueryRewriteAgent(),
            "retrieval": RetrievalAgent(),
            "document_generator": DocumentGeneratorAgent(),
            "slide_generator": SlideGeneratorAgent(),
            "reading_generator": ReadingGeneratorAgent(),
            "mindmap_generator": MindMapGeneratorAgent(),
            "code_generator": CodeGeneratorAgent(),
            "video_generator": VideoGenerationAgent(),
            "deep_reasoning": DeepReasoningAgent(),
            "tutor": TutorAgent(),
            "profile": ProfileAgent(),
            "practice": PracticeAgent(),
            "judge": JudgeAgent(),
            "path_planning": PathPlanningAgent(),
            "evaluation": EvaluationAgent(),
            "image_analysis": ImageAnalysisAgent(),
            "resource_push": ResourcePushAgent(),
            "critic": CriticAgent(),
        }
        self.route_templates = self._load_route_templates()
        self.query_classifier = QueryClassifier()
        self.planner_factory = lambda allowed_agent_names: ConversationPlanner(
            allowed_agent_names=allowed_agent_names,
        )

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
            route_template = self._resolve_tutoring_route(classification)
        if service_type == "RESOURCE_GENERATION":
            resolved_route = ["query_rewrite", "retrieval", "resource_bundle"]
        else:
            resolved_route = list(route_template)

        return RoutePlan(
            serviceType=service_type,
            agentNames=resolved_route,
            queryType=query_type,
            retrievalStrategy=retrieval_strategy,
            graphIntent=graph_intent,
            classificationConfidence=classification_confidence,
            classificationReason=classification_reason,
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

    def _resolve_tutoring_route(self, classification) -> list[str]:
        if classification.confidence < self.query_classifier.low_confidence_threshold:
            return ["query_rewrite", "retrieval", "tutor"]
        if classification.query_type == QUERY_TYPE_DEEP_REASONING:
            return ["query_rewrite", "retrieval", "image_analysis", "deep_reasoning"]
        if classification.query_type in {
            QUERY_TYPE_SMALL_TALK,
            QUERY_TYPE_FOLLOW_UP,
            QUERY_TYPE_ANSWER_PREVIOUS,
        }:
            return ["tutor"]
        if classification.query_type == QUERY_TYPE_IMAGE_QUESTION:
            return ["image_analysis", "query_rewrite", "retrieval", "tutor"]
        return ["query_rewrite", "retrieval", "tutor"]

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
    ) -> str:
        return self.agent_registry[agent_name].system_prompt(snapshot)

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
        state = ExecutionState(request=request, params=current_params, snapshot=snapshot)

        try:
            if self._should_run_conversation_planner(route_plan, state.params):
                async for event in self._run_planned_tutoring_route(
                    state=state,
                    route_plan=route_plan,
                    cancelled=cancelled,
                ):
                    yield event
                return

            async for event in self._execute_service_route(
                state=state,
                route_plan=route_plan,
                cancelled=cancelled,
            ):
                yield event
            if self._should_review_route(route_plan=route_plan, params=state.params):
                async for event in self._run_critic_review(state=state, service_type=route_plan.service_type):
                    yield event
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
            code = exc.code if isinstance(exc, SupervisorExecutionError) else "PLANNER_REVIEWER_FAILED"
            message = (
                exc.message
                if isinstance(exc, SupervisorExecutionError)
                else f"Planner/Reviewer execution failed: {type(exc).__name__}: {exc}"
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
                    learningPlan=self._safe_dict(state.params.get("learningPlan")),
                    criticReview=self._safe_dict(state.params.get("criticReview")),
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
        if route_plan.service_type == "RESOURCE_GENERATION":
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
            i += 1

    async def _execute_resource_bundle_route(
        self,
        *,
        state: ExecutionState,
        route_plan: RoutePlan,
        cancelled: Container[str] | None = None,
    ) -> AsyncIterator[SSEEvent]:
        workflow_request = state.request.model_copy(update={"service_type": route_plan.service_type})
        workflow = ResourceBundleWorkflow(
            agent_registry=self.agent_registry,
            snapshot_builder=self.snapshot_builder,
            system_prompt_builder=lambda agent_name, snapshot: self.build_agent_system_prompt(
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
            message = f"Resource bundle generation failed: {type(exc).__name__}: {exc}"
            LOGGER.exception(message)
            state.seq = workflow.last_state.seq if workflow.last_state is not None else state.seq
            raise SupervisorExecutionError(code="RESOURCE_BUNDLE_FAILED", message=message) from exc
        state.params.update(final_state.params)
        state.seq = final_state.seq
        state.snapshot = final_state.snapshot

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
        return resolved[:4] or ["DOCUMENT"]

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

    async def _run_planned_tutoring_route(
        self,
        *,
        state: ExecutionState,
        route_plan: RoutePlan,
        cancelled: Container[str] | None = None,
    ) -> AsyncIterator[SSEEvent]:
        planner = self.planner_factory(set(self.agent_registry))
        plan = await planner.plan(
            service_type=route_plan.service_type,
            params=state.params,
            snapshot=state.snapshot,
            route_agent_names=route_plan.agent_names,
            query_type=route_plan.query_type,
            graph_intent=route_plan.graph_intent,
        )
        state.params["learningPlan"] = plan.model_dump(by_alias=True)
        self._update_plan_status(state.params, "RUNNING")
        yield self._progress_event(
            state=state,
            stage="planning",
            percent=3,
            message=f"已生成 {len(plan.steps)} 步协作计划：{'、'.join(step.title for step in plan.steps[:5])}",
            agent_name="planner",
            phase="plan_created",
            status="RUNNING",
        )

        total_steps = max(len(plan.steps), 1)
        for index, step in enumerate(plan.steps):
            self._raise_if_cancelled(state=state, cancelled=cancelled)
            self._update_plan_step_status(state.params, step.step_id, "RUNNING")
            percent = min(90, 8 + int(index / total_steps * 74))
            yield self._progress_event(
                state=state,
                stage="plan_step",
                percent=percent,
                message=f"正在执行：{step.title}",
                agent_name=step.agent_name or step.service_type or "planner",
                phase="step_running",
                status="RUNNING",
            )

            try:
                if step.service_type:
                    async for event in self._run_planned_service_step(
                        state=state,
                        service_type=step.service_type,
                        cancelled=cancelled,
                    ):
                        yield event
                elif step.agent_name:
                    async for event in self._run_single_agent(
                        state=state,
                        agent_name=step.agent_name,
                        service_type=route_plan.service_type,
                    ):
                        self._collect_final_answer_from_event(state=state, agent_name=step.agent_name, event=event)
                        yield event

                if step.quality_gate == "critic":
                    async for event in self._run_critic_review(state=state, service_type=route_plan.service_type):
                        yield event
            except Exception:
                self._update_plan_step_status(state.params, step.step_id, "FAILED")
                self._update_plan_status(state.params, "FAILED")
                raise
            self._update_plan_step_status(state.params, step.step_id, "SUCCESS")
            yield self._progress_event(
                state=state,
                stage="plan_step",
                percent=min(94, percent + 8),
                message=f"已完成：{step.title}",
                agent_name=step.agent_name or step.service_type or "planner",
                phase="step_done",
                status="SUCCESS",
            )

        self._update_plan_status(state.params, "SUCCESS")
        if self._should_review_route(route_plan=route_plan, params=state.params):
            async for event in self._run_critic_review(state=state, service_type=route_plan.service_type):
                yield event
        if self._should_schedule_background_profile(service_type=route_plan.service_type, params=state.params):
            self._schedule_background_profile(state=state, service_type=state.request.service_type)
        yield DoneSSEEvent(
            taskId=state.request.task_id,
            traceId=state.request.trace_id,
            seq=state.seq,
            payload=self._build_done_payload(
                service_type=route_plan.service_type,
                agent_names=route_plan.agent_names,
                params=state.params,
            ),
        )

    async def _run_planned_service_step(
        self,
        *,
        state: ExecutionState,
        service_type: str,
        cancelled: Container[str] | None,
    ) -> AsyncIterator[SSEEvent]:
        nested_params = state.params
        nested_params["plannerNested"] = True
        nested_route = self.resolve_route(service_type, nested_params)
        self._seed_query_routing_params(nested_params, nested_route)
        async for event in self._execute_service_route(
            state=state,
            route_plan=nested_route,
            cancelled=cancelled,
        ):
            yield event
        if self._should_review_route(route_plan=nested_route, params=state.params):
            async for event in self._run_critic_review(state=state, service_type=nested_route.service_type):
                yield event

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
        agent = self.agent_registry[agent_name]
        agent_params = copy.deepcopy(state.params)
        system_prompt = self.build_agent_system_prompt(agent_name=agent_name, snapshot=state.snapshot)
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
        critic_agent = self.agent_registry["critic"]
        critic_prompt = self.build_agent_system_prompt(agent_name="critic", snapshot=state.snapshot)
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

    def _build_done_payload(self, *, service_type: str, agent_names: list[str], params: dict) -> DonePayload:
        learning_plan = self._safe_dict(params.get("learningPlan"))
        critic_review = self._safe_dict(params.get("criticReview"))
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
        if service_type == "RESOURCE_GENERATION" and isinstance(generated_assets, list) and generated_assets:
            titles = "、".join(
                str(item.get("title") or item.get("assetType") or "资源")
                for item in generated_assets[:3]
                if isinstance(item, dict)
            )
            if resource_failures:
                failed_types = "、".join(
                    str(item.get("resourceType") or "UNKNOWN")
                    for item in resource_failures[:3]
                    if isinstance(item, dict)
                )
                return DonePayload(
                    status="PARTIAL_FAILED",
                    summary=(
                        f"资源包部分完成，共 {len(generated_assets)} 个真实 LLM 产物：{titles}；"
                        f"{len(resource_failures)} 个资源失败：{failed_types}"
                    ),
                    learningPlan=learning_plan,
                    criticReview=critic_review,
                    resourceFailures=resource_failures,
                )
            return DonePayload(
                status="SUCCESS",
                summary=f"资源包生成完成，共 {len(generated_assets)} 个真实 LLM 产物：{titles}",
                learningPlan=learning_plan,
                criticReview=critic_review,
                resourceFailures=[],
            )
        generated_asset = params.get("generatedAsset")
        if service_type in {"RESOURCE_GENERATION", "VIDEO_GENERATION"} and isinstance(generated_asset, dict):
            title = str(generated_asset.get("title") or "资源")
            summary = str(generated_asset.get("summary") or "").strip()
            return DonePayload(
                status="SUCCESS",
                summary=f"{title} 生成完成：{summary}" if summary else f"{title} 生成完成",
                learningPlan=learning_plan,
                criticReview=critic_review,
            )
        if service_type == "RESOURCE_PUSH" and isinstance(pushed_resources, list):
            if not pushed_resources:
                return DonePayload(
                    status="SUCCESS",
                    summary="资源推送未命中可直接分发的现成资源",
                    learningPlan=learning_plan,
                    criticReview=critic_review,
                )
            titles = "、".join(
                str(item.get("title") or "资源")
                for item in pushed_resources[:3]
                if isinstance(item, dict)
            )
            return DonePayload(
                status="SUCCESS",
                summary=f"资源推送完成，已匹配 {len(pushed_resources)} 个现成资源：{titles}",
                learningPlan=learning_plan,
                resourcePushPlan=resource_push_plan,
                pushedResources=pushed_resources,
                criticReview=critic_review,
            )
        if service_type == "PATH_PLANNING" and isinstance(learning_path, dict):
            summary = str(learning_path.get("summaryText") or "").strip()
            if not summary:
                summary = f"{service_type} 路由完成，执行链路: {' -> '.join(agent_names)}"
            return DonePayload(
                status="SUCCESS",
                summary=summary,
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
            return DonePayload(
                status="SUCCESS",
                summary=summary,
                learningPath=learning_path if isinstance(learning_path, dict) else None,
                learningPlan=learning_plan,
                resourcePushPlan=resource_push_plan,
                pushedResources=pushed_resources,
                agentTrace=agent_trace,
                criticReview=critic_review,
            )
        return DonePayload(
            status="SUCCESS",
            summary=f"{service_type} 路由完成，执行链路: {' -> '.join(agent_names)}",
            learningPlan=learning_plan,
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

    def _should_run_conversation_planner(self, route_plan: RoutePlan, params: dict) -> bool:
        return False

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

    def _update_plan_step_status(self, params: dict, step_id: str, status: str) -> None:
        learning_plan = params.get("learningPlan")
        if not isinstance(learning_plan, dict):
            return
        steps = learning_plan.get("steps")
        if not isinstance(steps, list):
            return
        for step in steps:
            if isinstance(step, dict) and step.get("stepId") == step_id:
                step["status"] = status
                break

    def _update_plan_status(self, params: dict, status: str) -> None:
        learning_plan = params.get("learningPlan")
        if isinstance(learning_plan, dict):
            learning_plan["status"] = status

    @staticmethod
    def _safe_dict(value: Any) -> dict[str, Any] | None:
        return value if isinstance(value, dict) else None

    def _schedule_background_profile(self, *, state: ExecutionState, service_type: str) -> None:
        profile_agent = self.agent_registry["profile"]
        profile_prompt = self.build_agent_system_prompt(agent_name="profile", snapshot=state.snapshot)
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
