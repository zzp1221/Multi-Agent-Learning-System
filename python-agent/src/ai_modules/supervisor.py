"""Supervisor that resolves routes and streams agent execution results."""

from __future__ import annotations

import asyncio
import copy
import json
import logging
from collections.abc import AsyncIterator, Container
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.ai_modules.agents import (
    CodeGeneratorAgent,
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
from src.ai_modules.retrieval.query_classifier import (
    QUERY_TYPE_ANSWER_PREVIOUS,
    QUERY_TYPE_DEEP_REASONING,
    QUERY_TYPE_FOLLOW_UP,
    QUERY_TYPE_IMAGE_QUESTION,
    QUERY_TYPE_SMALL_TALK,
    QueryClassifier,
)
from src.ai_modules.runtime import SnapshotBuilder, SystemSnapshot
from src.ai_modules.runtime.resource_bundle_workflow import ResourceBundleWorkflow

LOGGER = logging.getLogger(__name__)


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
        }
        self.route_templates = self._load_route_templates()
        self.query_classifier = QueryClassifier()

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
        snapshot = await self.build_snapshot(request)
        seq = 1

        if route_plan.service_type == "RESOURCE_GENERATION":
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
                    request=request,
                    params=current_params,
                    snapshot=snapshot,
                    seq=seq,
                    cancelled=cancelled,
                ):
                    yield event
                final_state = workflow.last_state
                if final_state is None:
                    raise RuntimeError("Resource bundle workflow finished without final state")
            except Exception as exc:
                message = f"Resource bundle generation failed: {type(exc).__name__}: {exc}"
                LOGGER.exception(message)
                error_seq = workflow.last_state.seq if workflow.last_state is not None else seq
                yield ErrorSSEEvent(
                    taskId=request.task_id,
                    traceId=request.trace_id,
                    seq=error_seq,
                    payload=ErrorPayload(
                        code="RESOURCE_BUNDLE_FAILED",
                        message=message,
                    ),
                )
                yield DoneSSEEvent(
                    taskId=request.task_id,
                    traceId=request.trace_id,
                    seq=error_seq + 1,
                    payload=DonePayload(
                        status="FAILED",
                        summary=message,
                    ),
                )
                return
            current_params = final_state.params
            seq = final_state.seq
            yield DoneSSEEvent(
                taskId=request.task_id,
                traceId=request.trace_id,
                seq=seq,
                payload=self._build_done_payload(
                    service_type=route_plan.service_type,
                    agent_names=route_plan.agent_names,
                    params=current_params,
                ),
            )
            return

        agent_names = list(route_plan.agent_names)
        i = 0
        while i < len(agent_names):
            if cancelled and request.task_id in cancelled:
                yield ErrorSSEEvent(
                    taskId=request.task_id,
                    traceId=request.trace_id,
                    seq=seq,
                    payload=ErrorPayload(
                        code="TASK_CANCELLED",
                        message="任务已被取消",
                    ),
                )
                yield DoneSSEEvent(
                    taskId=request.task_id,
                    traceId=request.trace_id,
                    seq=seq + 1,
                    payload=DonePayload(
                        status="FAILED",
                        summary="任务已被取消",
                    ),
                )
                return

            agent_name = agent_names[i]

            # Retrieval must consume rewritten query/keywords, otherwise high-precision wiki matches are lost.
            if (
                agent_name == "query_rewrite"
                and i + 1 < len(agent_names)
                and agent_names[i + 1] == "retrieval"
            ):
                rewrite_agent = self.agent_registry["query_rewrite"]
                rewrite_prompt = self.build_agent_system_prompt(agent_name="query_rewrite", snapshot=snapshot)
                rewrite_params = copy.deepcopy(current_params)

                async for event in rewrite_agent.run(
                    task_id=request.task_id,
                    trace_id=request.trace_id,
                    seq=0,
                    service_type=request.service_type,
                    params=rewrite_params,
                    snapshot=snapshot,
                    system_prompt=rewrite_prompt,
                ):
                    yield event.model_copy(update={"seq": seq})
                    seq += 1

                current_params.update(rewrite_params)
                snapshot = await self.snapshot_builder.build(
                    user_id=request.user_id,
                    task_id=request.task_id,
                    conversation_id=request.conversation_id,
                    params=current_params,
                )

                retrieval_agent = self.agent_registry["retrieval"]
                retrieval_prompt = self.build_agent_system_prompt(agent_name="retrieval", snapshot=snapshot)
                retrieval_params = copy.deepcopy(current_params)

                async for event in retrieval_agent.run(
                    task_id=request.task_id,
                    trace_id=request.trace_id,
                    seq=0,
                    service_type=request.service_type,
                    params=retrieval_params,
                    snapshot=snapshot,
                    system_prompt=retrieval_prompt,
                ):
                    yield event.model_copy(update={"seq": seq})
                    seq += 1

                current_params.update(retrieval_params)

                i += 2
                snapshot = await self.snapshot_builder.build(
                    user_id=request.user_id,
                    task_id=request.task_id,
                    conversation_id=request.conversation_id,
                    params=current_params,
                )
                continue

            agent = self.agent_registry[agent_name]
            agent_params = copy.deepcopy(current_params)
            system_prompt = self.build_agent_system_prompt(
                agent_name=agent_name,
                snapshot=snapshot,
            )
            async for event in agent.run(
                task_id=request.task_id,
                trace_id=request.trace_id,
                seq=seq,
                service_type=request.service_type,
                params=agent_params,
                snapshot=snapshot,
                system_prompt=system_prompt,
            ):
                yield event
                seq += 1
            current_params = agent_params
            snapshot = await self.snapshot_builder.build(
                user_id=request.user_id,
                task_id=request.task_id,
                conversation_id=request.conversation_id,
                params=current_params,
            )
            i += 1

        if self._should_schedule_tutoring_profile(
            service_type=route_plan.service_type,
            params=current_params,
        ):
            profile_agent = self.agent_registry["profile"]
            profile_prompt = self.build_agent_system_prompt(agent_name="profile", snapshot=snapshot)
            self._schedule_background_agent(
                agent=profile_agent,
                agent_name="profile",
                task_id=request.task_id,
                trace_id=request.trace_id,
                service_type=request.service_type,
                params=copy.deepcopy(current_params),
                snapshot=snapshot,
                system_prompt=profile_prompt,
            )

        yield DoneSSEEvent(
            taskId=request.task_id,
            traceId=request.trace_id,
            seq=seq,
            payload=self._build_done_payload(
                service_type=route_plan.service_type,
                agent_names=route_plan.agent_names,
                params=current_params,
            ),
        )

    def _build_done_payload(self, *, service_type: str, agent_names: list[str], params: dict) -> DonePayload:
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
                    resourceFailures=resource_failures,
                )
            return DonePayload(
                status="SUCCESS",
                summary=f"资源包生成完成，共 {len(generated_assets)} 个真实 LLM 产物：{titles}",
                resourceFailures=[],
            )
        generated_asset = params.get("generatedAsset")
        if service_type in {"RESOURCE_GENERATION", "VIDEO_GENERATION"} and isinstance(generated_asset, dict):
            title = str(generated_asset.get("title") or "资源")
            summary = str(generated_asset.get("summary") or "").strip()
            return DonePayload(status="SUCCESS", summary=f"{title} 生成完成：{summary}" if summary else f"{title} 生成完成")
        pushed_resources = params.get("pushedResources")
        if service_type == "RESOURCE_PUSH" and isinstance(pushed_resources, list):
            if not pushed_resources:
                return DonePayload(status="SUCCESS", summary="资源推送未命中可直接分发的现成资源")
            titles = "、".join(
                str(item.get("title") or "资源")
                for item in pushed_resources[:3]
                if isinstance(item, dict)
            )
            return DonePayload(status="SUCCESS", summary=f"资源推送完成，已匹配 {len(pushed_resources)} 个现成资源：{titles}")
        learning_path = params.get("learningPath")
        if service_type == "PATH_PLANNING" and isinstance(learning_path, dict):
            summary = str(learning_path.get("summaryText") or "").strip()
            if not summary:
                summary = f"{service_type} 路由完成，执行链路: {' -> '.join(agent_names)}"
            return DonePayload(
                status="SUCCESS",
                summary=summary,
                learningPath=learning_path,
            )
        return DonePayload(
            status="SUCCESS",
            summary=f"{service_type} 路由完成，执行链路: {' -> '.join(agent_names)}",
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

    def _should_schedule_tutoring_profile(self, *, service_type: str, params: dict) -> bool:
        if service_type != "TUTORING":
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
