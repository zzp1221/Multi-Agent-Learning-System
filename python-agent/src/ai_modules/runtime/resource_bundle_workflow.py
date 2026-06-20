"""LangGraph workflow for real LLM resource bundle generation."""

from __future__ import annotations

import copy
import operator
import time
from collections.abc import AsyncIterator, Callable, Container
from dataclasses import dataclass
from typing import Annotated, Any, TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field

from src.ai_modules.models import (
    EngineStreamRequest,
    ProgressPayload,
    ProgressSSEEvent,
    QuestionBatchSSEEvent,
    ReasoningChunkPayload,
    ReasoningChunkSSEEvent,
    ResourceFileSSEEvent,
    SSEEvent,
)
from src.ai_modules.runtime.context_snapshot import SnapshotBuilder, SystemSnapshot
from src.ai_modules.runtime.provenance import ProvenanceError, validate_llm_provenance


DEFAULT_RESOURCE_TYPES: tuple[str, ...] = (
    "DOCUMENT",
)

RESOURCE_AGENT_BY_TYPE: dict[str, str] = {
    "DOCUMENT": "document_generator",
    "SLIDES": "slide_generator",
    "MINDMAP": "mindmap_generator",
    "CODE": "code_generator",
    "QUIZ": "practice",
    "VIDEO": "video_generator",
}

RESOURCE_NODE_BY_TYPE: dict[str, str] = {
    "DOCUMENT": "document_agent",
    "SLIDES": "slides_agent",
    "MINDMAP": "mindmap_agent",
    "QUIZ": "practice_agent",
    "CODE": "code_case_agent",
    "VIDEO": "video_agent",
}

RESOURCE_AGENT_DISPLAY_NAME_BY_TYPE: dict[str, str] = {
    "DOCUMENT": "Document",
    "SLIDES": "Slides",
    "MINDMAP": "MindMap",
    "QUIZ": "Practice",
    "CODE": "Code",
    "VIDEO": "Video",
}


@dataclass(slots=True)
class ResourceAgentResult:
    resource_type: str
    agent_name: str
    params: dict[str, Any]
    events: list[SSEEvent]
    published_at_ns: int


class WorkflowGraphState(TypedDict, total=False):
    request: EngineStreamRequest
    params: dict[str, Any]
    snapshot: SystemSnapshot
    seq: int
    events: list[SSEEvent]
    resource_types: list[str]
    generated_assets: list[dict[str, Any]]
    resource_results: Annotated[list[ResourceAgentResult], operator.add]
    resource_failures: Annotated[list[dict[str, str]], operator.add]


class WorkflowState(BaseModel):
    """Explicit state passed between resource bundle graph nodes."""

    request: EngineStreamRequest
    params: dict[str, Any]
    snapshot: SystemSnapshot
    seq: int = 1
    events: list[SSEEvent] = Field(default_factory=list)
    resource_types: list[str] = Field(default_factory=list)
    generated_assets: list[dict[str, Any]] = Field(default_factory=list)
    resource_results: list[ResourceAgentResult] = Field(default_factory=list)
    resource_failures: list[dict[str, str]] = Field(default_factory=list)

    model_config = ConfigDict(arbitrary_types_allowed=True)


class ResourceBundleWorkflow:
    """Graph orchestration for resource packages with no pseudo-generation."""

    def __init__(
        self,
        *,
        agent_registry: dict[str, Any],
        snapshot_builder: SnapshotBuilder,
        system_prompt_builder: Callable[[str, SystemSnapshot], str],
    ) -> None:
        self.agent_registry = agent_registry
        self.snapshot_builder = snapshot_builder
        self.system_prompt_builder = system_prompt_builder
        self._graph = self._compile_graph()
        self.last_state: WorkflowState | None = None

    async def run(
        self,
        *,
        request: EngineStreamRequest,
        params: dict[str, Any],
        snapshot: SystemSnapshot,
        seq: int = 1,
        cancelled: Container[str] | None = None,
    ) -> WorkflowState:
        if cancelled and request.task_id in cancelled:
            raise RuntimeError("Task was cancelled before resource bundle workflow started")
        state = WorkflowState(
            request=request,
            params=copy.deepcopy(params),
            snapshot=snapshot,
            seq=seq,
        )
        async for _ in self.stream(
            request=request,
            params=params,
            snapshot=snapshot,
            seq=seq,
            cancelled=cancelled,
            initial_state=state,
        ):
            pass
        return state

    async def stream(
        self,
        *,
        request: EngineStreamRequest,
        params: dict[str, Any],
        snapshot: SystemSnapshot,
        seq: int = 1,
        cancelled: Container[str] | None = None,
        initial_state: WorkflowState | None = None,
    ) -> AsyncIterator[SSEEvent]:
        if cancelled and request.task_id in cancelled:
            raise RuntimeError("Task was cancelled before resource bundle workflow started")
        state = initial_state or WorkflowState(
            request=request,
            params=copy.deepcopy(params),
            snapshot=snapshot,
            seq=seq,
        )
        graph_event_count = len(state.events)
        try:
            async for stream_mode, item in self._graph.astream(
                self._state_to_dict(state),
                stream_mode=["updates", "custom"],
            ):
                if cancelled and request.task_id in cancelled:
                    raise RuntimeError("Task was cancelled during resource bundle generation")
                if stream_mode == "custom":
                    event = item.get("event") if isinstance(item, dict) else None
                    if isinstance(event, SSEEvent):
                        event = event.model_copy(update={"seq": state.seq})
                        state.events.append(event)
                        state.seq += 1
                        yield event
                    continue
                if stream_mode != "updates" or not isinstance(item, dict):
                    continue
                for update in item.values():
                    if not isinstance(update, dict):
                        continue
                    next_state = WorkflowState.model_validate({**self._state_to_dict(state), **update})
                    new_events: list[SSEEvent] = []
                    if "events" in update:
                        new_events = next_state.events[graph_event_count:]
                        graph_event_count = len(next_state.events)
                    state.params = next_state.params
                    state.snapshot = next_state.snapshot
                    state.resource_types = next_state.resource_types
                    state.generated_assets = next_state.generated_assets
                    state.resource_results = next_state.resource_results
                    state.resource_failures = next_state.resource_failures
                    for event in new_events:
                        event = event.model_copy(update={"seq": state.seq})
                        state.events.append(event)
                        state.seq += 1
                        yield event
        except Exception:
            if initial_state is not None:
                self._copy_state(initial_state, state)
            self.last_state = state
            raise
        if state.resource_failures and not state.generated_assets:
            if initial_state is not None:
                self._copy_state(initial_state, state)
            self.last_state = state
            raise RuntimeError(f"Resource bundle generation failed: {state.resource_failures}")
        if initial_state is not None:
            self._copy_state(initial_state, state)
        self.last_state = state

    def _compile_graph(self):
        graph = StateGraph(WorkflowGraphState)
        graph.add_node("query_rewrite", self._query_rewrite_node)
        graph.add_node("retrieval", self._retrieval_node)
        graph.add_node("resource_selector", self._resource_selector_node)
        for resource_type, node_name in RESOURCE_NODE_BY_TYPE.items():
            graph.add_node(node_name, self._make_resource_node(resource_type))
        graph.add_node("bundle_synthesizer", self._bundle_synthesizer_node)
        graph.add_conditional_edges(
            START,
            self._select_start_node,
            {
                "query_rewrite": "query_rewrite",
                "resource_selector": "resource_selector",
            },
        )
        graph.add_edge("query_rewrite", "retrieval")
        graph.add_edge("retrieval", "resource_selector")
        for node_name in RESOURCE_NODE_BY_TYPE.values():
            graph.add_edge("resource_selector", node_name)
        graph.add_edge(list(RESOURCE_NODE_BY_TYPE.values()), "bundle_synthesizer")
        graph.add_edge("bundle_synthesizer", END)
        return graph.compile()

    def _select_start_node(self, raw_state: dict[str, Any]) -> str:
        state = WorkflowState.model_validate(raw_state)
        if state.params.get("skipResourceBundlePrelude"):
            return "resource_selector"
        return "query_rewrite"

    async def _query_rewrite_node(self, raw_state: dict[str, Any]) -> dict[str, Any]:
        state = WorkflowState.model_validate(raw_state)
        return await self._run_linear_agent(state, "query_rewrite")

    async def _retrieval_node(self, raw_state: dict[str, Any]) -> dict[str, Any]:
        state = WorkflowState.model_validate(raw_state)
        next_state = WorkflowState.model_validate(await self._run_linear_agent(state, "retrieval"))
        self._reject_fallback_evidence(next_state.params)
        return self._state_to_dict(next_state)

    def _make_resource_node(self, resource_type: str):
        async def resource_node(raw_state: WorkflowGraphState) -> WorkflowGraphState:
            return await self._resource_agent_node(raw_state, resource_type)

        return resource_node

    async def _resource_selector_node(self, raw_state: dict[str, Any]) -> dict[str, Any]:
        state = WorkflowState.model_validate(raw_state)
        resource_types = self.resolve_resource_types(state.params)
        if not resource_types:
            raise RuntimeError("No supported resource types requested")
        state.resource_types = resource_types
        state.events.append(
            self._public_trace_event(
                state=state,
                agent_name="Resource Bundle",
                phase="select",
                text=f"已确认需要生成 {len(resource_types)} 类资源，准备分派给对应 Agent。",
                status="RUNNING",
                percent=48,
            )
        )
        state.seq += 1
        state.events.append(
            ProgressSSEEvent(
                taskId=state.request.task_id,
                traceId=state.request.trace_id,
                seq=state.seq,
                payload=ProgressPayload(
                    stage="resource_bundle",
                    percent=50,
                    message=f"Starting explicit resource graph for {len(resource_types)} agents",
                    agentName="resource_bundle",
                    phase="fan_out",
                    status="RUNNING",
                ),
            )
        )
        state.seq += 1
        return self._state_to_dict(state)

    async def _resource_agent_node(
        self,
        raw_state: WorkflowGraphState,
        resource_type: str,
    ) -> WorkflowGraphState:
        state = WorkflowState.model_validate(raw_state)
        if resource_type not in state.resource_types:
            return {"resource_results": [], "resource_failures": []}
        agent_name = RESOURCE_AGENT_BY_TYPE[resource_type]
        display_agent_name = RESOURCE_AGENT_DISPLAY_NAME_BY_TYPE.get(resource_type, agent_name)
        writer = get_stream_writer()
        try:
            writer(
                {
                    "event": self._public_trace_event(
                        state=state,
                        agent_name=display_agent_name,
                        phase="generate",
                        text=f"{display_agent_name} Agent 已接收 {resource_type} 资源生成任务。",
                        artifact_type=resource_type,
                        status="RUNNING",
                        percent=58,
                    )
                }
            )
            writer(
                {
                    "event": ProgressSSEEvent(
                        taskId=state.request.task_id,
                        traceId=state.request.trace_id,
                        seq=state.seq,
                        payload=ProgressPayload(
                            stage="resource_bundle",
                            percent=60,
                            message=f"Generating {resource_type} resource",
                            agentName=agent_name,
                            phase="generate",
                            artifactType=resource_type,
                            status="RUNNING",
                        ),
                    )
                }
            )
            result = await self._run_resource_agent(
                state=state,
                resource_type=resource_type,
                agent_name=agent_name,
                writer=writer,
            )
            self._validate_resource_events(result)
            return {"resource_results": [result], "resource_failures": []}
        except Exception as exc:
            if self._is_provenance_failure(exc):
                raise
            summary = self._friendly_resource_failure(exc)
            return {
                "resource_results": [],
                "resource_failures": [
                    {
                        "resourceType": resource_type,
                        "agentName": agent_name,
                        "error": summary,
                    }
                ],
            }

    async def _bundle_synthesizer_node(self, raw_state: WorkflowGraphState) -> WorkflowGraphState:
        state = WorkflowState.model_validate(raw_state)
        resource_order = {resource_type: index for index, resource_type in enumerate(state.resource_types)}
        results = sorted(
            state.resource_results,
            key=lambda item: (
                item.published_at_ns,
                resource_order.get(item.resource_type, len(resource_order)),
            ),
        )
        failures = sorted(
            state.resource_failures,
            key=lambda item: resource_order.get(str(item.get("resourceType") or ""), len(resource_order)),
        )

        generated_assets: list[dict[str, Any]] = []
        for result in results:
            state.params = self._merge_agent_params(state.params, result.params)
            for event in result.events:
                if isinstance(event, ResourceFileSSEEvent):
                    payload = event.payload.model_dump(by_alias=True)
                    generated_assets.append(payload)
                elif isinstance(event, QuestionBatchSSEEvent):
                    payload = event.payload.model_dump(by_alias=True)
                    generated_assets.append({"assetType": "QUIZ", **payload})

        for failure in failures:
            resource_type = str(failure.get("resourceType") or "UNKNOWN")
            state.events.append(
                self._public_trace_event(
                    state=state,
                    agent_name=str(failure.get("agentName") or "Resource Bundle"),
                    phase="failed",
                    text=f"{resource_type} 资源未能完成，已记录为失败项。",
                    artifact_type=resource_type,
                    status="FAILED",
                    percent=90,
                )
            )
            state.seq += 1
            state.events.append(
                ProgressSSEEvent(
                    taskId=state.request.task_id,
                    traceId=state.request.trace_id,
                    seq=state.seq,
                    payload=ProgressPayload(
                        stage="resource_bundle",
                        percent=90,
                        message=f"{resource_type} generation failed: {failure.get('error')}",
                        agentName=str(failure.get("agentName") or "resource_bundle"),
                        phase="fan_in",
                        artifactType=resource_type,
                        status="FAILED",
                    ),
                )
            )
            state.seq += 1
        state.generated_assets = generated_assets
        state.resource_failures = failures
        state.params["generatedAssets"] = generated_assets
        state.params.pop("pendingSlideOutlines", None)
        state.params["resourceFailures"] = failures
        if generated_assets:
            state.params["generatedAsset"] = generated_assets[0]
            status = "PARTIAL_FAILED" if failures else "SUCCESS"
            state.events.append(
                self._public_trace_event(
                    state=state,
                    agent_name="Resource Bundle",
                    phase="done",
                    text="资源包已完成汇总，正在发布可用资源。",
                    status=status,
                    percent=96,
                )
            )
            state.seq += 1
        else:
            state.params.pop("generatedAsset", None)
            state.params.pop("generatedContent", None)
        return self._state_to_dict(state)

    async def _run_linear_agent(self, state: WorkflowState, agent_name: str) -> dict[str, Any]:
        agent = self.agent_registry[agent_name]
        agent_params = copy.deepcopy(state.params)
        system_prompt = self.system_prompt_builder(agent_name, state.snapshot)
        async for event in agent.run(
            task_id=state.request.task_id,
            trace_id=state.request.trace_id,
            seq=0,
            service_type=state.request.service_type,
            params=agent_params,
            snapshot=state.snapshot,
            system_prompt=system_prompt,
        ):
            state.events.append(event.model_copy(update={"seq": state.seq}))
            state.seq += 1

        state.params = agent_params
        state.snapshot = await self._build_snapshot(state.request, state.params)
        return self._state_to_dict(state)

    async def _run_resource_agent(
        self,
        *,
        state: WorkflowState,
        resource_type: str,
        agent_name: str,
        writer: Callable[[Any], None] | None = None,
    ) -> ResourceAgentResult:
        agent = self.agent_registry[agent_name]
        agent_params = copy.deepcopy(state.params)
        agent_params["resourceType"] = resource_type
        agent_params["artifactType"] = resource_type
        system_prompt = self.system_prompt_builder(agent_name, state.snapshot)
        events: list[SSEEvent] = []
        first_publish_at_ns: int | None = None
        async for event in agent.run(
            task_id=state.request.task_id,
            trace_id=state.request.trace_id,
            seq=0,
            service_type=state.request.service_type,
            params=agent_params,
            snapshot=state.snapshot,
            system_prompt=system_prompt,
        ):
            if event.event in {"done", "error"}:
                raise RuntimeError(
                    f"{agent_name} emitted terminal {event.event}: {self._terminal_event_reason(event)}"
                )
            if isinstance(event, ResourceFileSSEEvent):
                if event.payload.asset_type != resource_type:
                    raise RuntimeError(
                        f"{agent_name} emitted {event.payload.asset_type} for requested {resource_type}"
                    )
                validate_llm_provenance(
                    event.payload,
                    artifact_label=f"{agent_name}:{resource_type}",
                )
                first_publish_at_ns = first_publish_at_ns or time.monotonic_ns()
            elif isinstance(event, QuestionBatchSSEEvent):
                if resource_type != "QUIZ":
                    raise RuntimeError(
                        f"{agent_name} emitted question_batch for requested {resource_type}"
                    )
                validate_llm_provenance(
                    event.payload,
                    artifact_label=f"{agent_name}:{resource_type}",
                )
                first_publish_at_ns = first_publish_at_ns or time.monotonic_ns()
            events.append(event)
            if writer is not None:
                writer({"event": event})
        return ResourceAgentResult(
            resource_type=resource_type,
            agent_name=agent_name,
            params=agent_params,
            events=events,
            published_at_ns=first_publish_at_ns or time.monotonic_ns(),
        )

    async def _build_snapshot(self, request: EngineStreamRequest, params: dict[str, Any]) -> SystemSnapshot:
        return await self.snapshot_builder.build(
            user_id=request.user_id,
            task_id=request.task_id,
            conversation_id=request.conversation_id,
            params=params,
        )

    def _validate_resource_events(self, result: ResourceAgentResult) -> None:
        produced = 0
        for event in result.events:
            if isinstance(event, ResourceFileSSEEvent):
                produced += 1
                validate_llm_provenance(
                    event.payload,
                    artifact_label=f"{result.agent_name}:{result.resource_type}",
                )
            elif isinstance(event, QuestionBatchSSEEvent):
                produced += 1
                validate_llm_provenance(
                    event.payload,
                    artifact_label=f"{result.agent_name}:{result.resource_type}",
                )
        if produced == 0:
            raise RuntimeError(f"{result.agent_name} produced no publishable resource event")

    @staticmethod
    def _is_provenance_failure(exc: Exception) -> bool:
        if isinstance(exc, ProvenanceError):
            return True
        message = str(exc).lower()
        return any(
            marker in message
            for marker in (
                "missing generatedby=llm",
                "missing contentorigin=llm",
                "missing llm provider",
                "missing llm model",
                "must declare fallback=false",
            )
        )

    @staticmethod
    def _friendly_resource_failure(exc: Exception) -> str:
        message = str(exc).strip()
        lowered = message.lower()
        if "critic review blocked resource publication" in lowered:
            return "quality review did not approve this resource; please retry with a clearer topic or more context"
        if "review llm failed" in lowered or "safety review llm failed" in lowered or "fallback is disabled" in lowered:
            return "review service was temporarily unavailable; other resources were still generated"
        if "template fallback is not allowed" in lowered or "llm generation failed" in lowered or "llm unavailable" in lowered:
            return "generation service was temporarily unavailable; please retry this resource"
        if "emitted terminal error" in lowered or "emitted terminal done" in lowered:
            reason = message.split(":", 1)[-1].strip()
            return reason or "resource generation did not produce a publishable result"
        if "produced no publishable resource event" in lowered:
            return "resource generation did not produce a publishable result"
        if "emitted" in lowered and "requested" in lowered:
            return "resource generation returned an unexpected artifact type"
        return message or "resource generation failed"

    @staticmethod
    def _terminal_event_reason(event: SSEEvent) -> str:
        payload = event.payload
        if hasattr(payload, "message"):
            return str(getattr(payload, "message") or "")
        if hasattr(payload, "summary"):
            return str(getattr(payload, "summary") or "")
        if isinstance(payload, dict):
            return str(payload.get("message") or payload.get("summary") or "")
        return ""

    def _merge_agent_params(self, base_params: dict[str, Any], agent_params: dict[str, Any]) -> dict[str, Any]:
        merged = copy.deepcopy(base_params)
        for key in (
            "generatedAsset",
            "generatedContent",
            "criticReview",
            "safetyReview",
            "practiceQuestionBatch",
            "practiceQuestions",
            "practicePersistence",
            "videoGenerationTask",
            "videoSandboxArtifact",
        ):
            if key in agent_params:
                merged[key] = agent_params[key]
        return merged

    @staticmethod
    def _state_to_dict(state: WorkflowState) -> dict[str, Any]:
        return {
            "request": state.request,
            "params": state.params,
            "snapshot": state.snapshot,
            "seq": state.seq,
            "events": state.events,
            "resource_types": state.resource_types,
            "generated_assets": state.generated_assets,
            "resource_results": state.resource_results,
            "resource_failures": state.resource_failures,
        }

    @staticmethod
    def _public_trace_event(
        *,
        state: WorkflowState,
        agent_name: str,
        phase: str,
        text: str,
        artifact_type: str | None = None,
        status: str | None = None,
        percent: int | None = None,
    ) -> ReasoningChunkSSEEvent:
        return ReasoningChunkSSEEvent(
            taskId=state.request.task_id,
            traceId=state.request.trace_id,
            seq=state.seq,
            payload=ReasoningChunkPayload(
                text=text,
                stage="resource_generation",
                publicTrace=True,
                agentName=agent_name,
                phase=phase,
                artifactType=artifact_type,
                status=status,
                percent=percent,
                provider="system",
                model="public-trace",
            ),
        )

    @staticmethod
    def _copy_state(target: WorkflowState, source: WorkflowState) -> None:
        target.request = source.request
        target.params = source.params
        target.snapshot = source.snapshot
        target.seq = source.seq
        target.events = source.events
        target.resource_types = source.resource_types
        target.generated_assets = source.generated_assets
        target.resource_results = source.resource_results
        target.resource_failures = source.resource_failures

    def _reject_fallback_evidence(self, params: dict[str, Any]) -> None:
        retrieval_result = params.get("retrievalResult", {})
        documents = retrieval_result.get("documents", []) if isinstance(retrieval_result, dict) else []
        for document in documents:
            if not isinstance(document, dict):
                continue
            channel = str(document.get("channel") or "").lower()
            match_type = str(document.get("matchType") or document.get("match_type") or "").lower()
            slug = str(document.get("slug") or "").lower()
            if channel == "fallback" or match_type == "fallback" or slug.startswith("fallback-"):
                raise RuntimeError("Retrieval fallback evidence is disabled for resource bundle generation")

    @classmethod
    def resolve_resource_types(cls, params: dict[str, Any]) -> list[str]:
        requested: list[str] = []
        raw_resource_types = params.get("resourceTypes")
        if isinstance(raw_resource_types, list):
            requested.extend(str(item) for item in raw_resource_types if str(item).strip())
        if not requested:
            raw_resource_type = params.get("resourceType")
            if isinstance(raw_resource_type, str) and raw_resource_type.strip():
                requested.append(raw_resource_type)

        normalized = [cls.normalize_resource_type(item) for item in requested]
        resolved = cls._unique_supported_types(normalized)
        if requested and not resolved:
            return []
        return resolved or list(DEFAULT_RESOURCE_TYPES)

    @staticmethod
    def normalize_resource_type(resource_type: str) -> str:
        normalized = resource_type.strip().upper()
        return {
            "EXPLANATION": "DOCUMENT",
            "CODE_CASE": "CODE",
            "PRACTICAL_CASE": "CODE",
            "PPT": "SLIDES",
        }.get(normalized, normalized)

    @staticmethod
    def _unique_supported_types(resource_types: list[str]) -> list[str]:
        resolved: list[str] = []
        for resource_type in resource_types:
            if resource_type not in RESOURCE_AGENT_BY_TYPE or resource_type in resolved:
                continue
            resolved.append(resource_type)
        return resolved
