import asyncio

import pytest

from src.ai_modules.agents.base import PlaceholderAgent
from src.ai_modules.agents.practice_agent import PracticeAgent
from src.ai_modules.agents.query_rewrite_agent import QueryRewriteAgent
from src.ai_modules.models import (
    CriticReviewPayload,
    EngineStreamRequest,
    QuestionBatchPayload,
    QuestionBatchSSEEvent,
    ResourceFilePayload,
    ResourceFileSSEEvent,
    ResultChunkPayload,
    ResultChunkSSEEvent,
)
from src.ai_modules.runtime import SystemSnapshot
from src.ai_modules.runtime.resource_bundle_workflow import ResourceBundleWorkflow
from src.ai_modules.supervisor import PythonAgentSupervisor


def _snapshot() -> SystemSnapshot:
    return SystemSnapshot(
        current_course="数据库原理",
        current_chapter="索引",
        course_progress=0.3,
        student_name="张三",
        student_level="BASIC",
        knowledge_gaps=["联合索引"],
    )


def _provenance(agent_name: str) -> dict:
    return {
        "generatedBy": "LLM",
        "contentOrigin": "LLM",
        "provider": "test-provider",
        "model": "test-model",
        "agentName": agent_name,
        "evidenceIds": ["doc-1"],
        "fallback": False,
        "fromCache": False,
    }


class _StubRewriteAgent(PlaceholderAgent):
    def __init__(self) -> None:
        super().__init__("rewrite", "query_rewrite")

    async def run(self, *, params, **kwargs):
        params["rewrittenQuery"] = "数据库原理 联合索引"
        if False:
            yield


class _StubRetrievalAgent(PlaceholderAgent):
    def __init__(self) -> None:
        super().__init__("retrieval", "retrieval")

    async def run(self, *, params, **kwargs):
        params["retrievalResult"] = {
            "documents": [{"id": "doc-1", "title": "联合索引导学", "channel": "wiki"}],
            "sourcesSummary": "联合索引导学",
        }
        if False:
            yield


class _StubResourceAgent(PlaceholderAgent):
    def __init__(self, asset_type: str, stage_name: str) -> None:
        super().__init__(asset_type, stage_name)
        self.asset_type = asset_type

    async def run(self, *, task_id, trace_id, seq, params, **kwargs):
        provenance = _provenance(self.stage_name)
        if self.asset_type == "QUIZ":
            payload = QuestionBatchPayload.model_validate(
                {
                    "title": "联合索引练习",
                    "topic": "联合索引",
                    "difficulty": "BASIC",
                    "questions": [
                        {
                            "questionId": "q1",
                            "questionType": "SHORT_ANSWER",
                            "stem": "说明联合索引最左匹配规则。",
                            "answer": "按索引列顺序从左到右匹配。",
                            "knowledgeTags": ["联合索引"],
                            "difficultyLevel": "BASIC",
                            "explanation": "联合索引需要从索引定义的最左列开始连续匹配。",
                        }
                    ],
                    **provenance,
                }
            )
            params["practiceQuestionBatch"] = payload.model_dump(by_alias=True)
            yield QuestionBatchSSEEvent(taskId=task_id, traceId=trace_id, seq=seq, payload=payload)
            return

        payload = ResourceFilePayload(
            assetType=self.asset_type,
            title=f"联合索引 {self.asset_type}",
            summary="LLM 生成资源",
            displayMode="download",
            fileName=f"{self.asset_type.lower()}.md",
            localPath=f"sandbox/{self.asset_type.lower()}.md",
            mimeType="text/markdown",
            **provenance,
        )
        params["generatedAsset"] = payload.model_dump(by_alias=True)
        yield ResultChunkSSEEvent(
            taskId=task_id,
            traceId=trace_id,
            seq=seq,
            payload=ResultChunkPayload(text=f"{self.asset_type} done"),
        )
        yield ResourceFileSSEEvent(taskId=task_id, traceId=trace_id, seq=seq + 1, payload=payload)


class _StubCriticAgent(PlaceholderAgent):
    def __init__(self) -> None:
        super().__init__("critic", "critic")

    async def run(self, *, params, **kwargs):
        del kwargs
        review = CriticReviewPayload(
            verdict="PASS",
            factConsistency="SUPPORTED",
            difficultyMatch="MATCHED",
            sourceCoverage="GOOD",
            issues=[],
            suggestions=["可发布。"],
            summaryText="Critic OK",
        )
        params["criticReview"] = review.model_dump(by_alias=True)
        if False:
            yield


class _FailingResourceAgent(PlaceholderAgent):
    def __init__(self) -> None:
        super().__init__("failing", "failing_generation")

    async def run(self, **kwargs):
        raise RuntimeError("llm unavailable")
        yield  # pragma: no cover


class _FailingPracticeResourceAgent(PlaceholderAgent):
    def __init__(self) -> None:
        super().__init__("failing practice", "practice")

    async def run(self, **kwargs):
        raise RuntimeError("Practice question LLM generation failed; template fallback is not allowed")
        yield  # pragma: no cover


class _QualityGateFailingResourceAgent(PlaceholderAgent):
    def __init__(self) -> None:
        super().__init__("quality gate failing", "quality_gate_generation")

    async def run(self, **kwargs):
        raise RuntimeError("Critic review LLM failed; heuristic fallback is disabled")
        yield  # pragma: no cover


class _ResourceStartProbe:
    def __init__(self, expected_count: int = 0) -> None:
        self.started: list[str] = []
        self.expected_count = expected_count
        self._all_started = asyncio.Event()

    async def record_start(self, agent_name: str) -> None:
        self.started.append(agent_name)
        if self.expected_count and len(self.started) >= self.expected_count:
            self._all_started.set()

    async def wait_for_all_started(self) -> None:
        await asyncio.wait_for(self._all_started.wait(), timeout=1)


class _OrderedResourceAgent(PlaceholderAgent):
    def __init__(self, asset_type: str, stage_name: str, probe: _ResourceStartProbe) -> None:
        super().__init__(asset_type, stage_name)
        self.asset_type = asset_type
        self.probe = probe

    async def run(self, *, task_id, trace_id, seq, params, **kwargs):
        await self.probe.record_start(self.stage_name)
        await self.probe.wait_for_all_started()
        payload = ResourceFilePayload(
            assetType=self.asset_type,
            title=f"Ordered {self.asset_type}",
            summary="LLM generated ordered resource",
            displayMode="download",
            fileName=f"{self.asset_type.lower()}.md",
            localPath=f"sandbox/{self.asset_type.lower()}.md",
            mimeType="text/markdown",
            **_provenance(self.stage_name),
        )
        yield ResourceFileSSEEvent(taskId=task_id, traceId=trace_id, seq=seq, payload=payload)


class _DelayedResourceAgent(PlaceholderAgent):
    def __init__(self, asset_type: str, stage_name: str, delay_seconds: float) -> None:
        super().__init__(asset_type, stage_name)
        self.asset_type = asset_type
        self.delay_seconds = delay_seconds

    async def run(self, *, task_id, trace_id, seq, params, **kwargs):
        await asyncio.sleep(self.delay_seconds)
        payload = ResourceFilePayload(
            assetType=self.asset_type,
            title=f"Delayed {self.asset_type}",
            summary="LLM generated delayed resource",
            displayMode="download",
            fileName=f"{self.asset_type.lower()}.md",
            localPath=f"sandbox/{self.asset_type.lower()}.md",
            mimeType="text/markdown",
            **_provenance(self.stage_name),
        )
        yield ResourceFileSSEEvent(taskId=task_id, traceId=trace_id, seq=seq, payload=payload)


class _WrongTypeResourceAgent(PlaceholderAgent):
    def __init__(self, emitted_asset_type: str, stage_name: str) -> None:
        super().__init__(emitted_asset_type, stage_name)
        self.emitted_asset_type = emitted_asset_type

    async def run(self, *, task_id, trace_id, seq, params, **kwargs):
        payload = ResourceFilePayload(
            assetType=self.emitted_asset_type,
            title=f"Wrong {self.emitted_asset_type}",
            summary="LLM generated wrong resource type",
            displayMode="download",
            fileName=f"{self.emitted_asset_type.lower()}.md",
            localPath=f"sandbox/{self.emitted_asset_type.lower()}.md",
            mimeType="text/markdown",
            **_provenance(self.stage_name),
        )
        yield ResourceFileSSEEvent(taskId=task_id, traceId=trace_id, seq=seq, payload=payload)


class _FailingQuestionGenerator:
    provider_name = "test-provider"
    model_name = "test-model"

    async def generate_batch(self, **kwargs):
        raise RuntimeError("llm unavailable")


class _FailingRewriteGenerator:
    async def rewrite(self, **kwargs):
        raise RuntimeError("llm unavailable")


def _install_success_bundle(supervisor: PythonAgentSupervisor) -> None:
    supervisor.agent_registry["query_rewrite"] = _StubRewriteAgent()
    supervisor.agent_registry["retrieval"] = _StubRetrievalAgent()
    supervisor.agent_registry["document_generator"] = _StubResourceAgent("DOCUMENT", "document_generation")
    supervisor.agent_registry["slide_generator"] = _StubResourceAgent("SLIDES", "slide_generation")
    supervisor.agent_registry["mindmap_generator"] = _StubResourceAgent("MINDMAP", "mindmap_generation")
    supervisor.agent_registry["practice"] = _StubResourceAgent("QUIZ", "practice")
    supervisor.agent_registry["critic"] = _StubCriticAgent()


def test_resource_types_honor_requested_arbitrary_agents() -> None:
    assert ResourceBundleWorkflow.resolve_resource_types(
        {"resourceTypes": ["DOCUMENT", "SLIDES"]}
    ) == ["DOCUMENT", "SLIDES"]
    assert ResourceBundleWorkflow.resolve_resource_types({"resourceTypes": ["QUIZ"]}) == ["QUIZ"]
    assert ResourceBundleWorkflow.resolve_resource_types({"resourceType": "EXPLANATION"}) == ["DOCUMENT"]
    assert ResourceBundleWorkflow.resolve_resource_types({"query": "联合索引"}) == ["DOCUMENT"]
    assert ResourceBundleWorkflow.resolve_resource_types({"resourceTypes": ["DOCUMENT", "READING", "VIDEO"]}) == ["DOCUMENT", "VIDEO"]
    assert ResourceBundleWorkflow.resolve_resource_types({"resourceTypes": ["UNKNOWN"]}) == []


@pytest.mark.asyncio
async def test_resource_bundle_publishes_only_provenance_checked_outputs() -> None:
    supervisor = PythonAgentSupervisor()
    _install_success_bundle(supervisor)
    request = EngineStreamRequest(
        serviceType="RESOURCE_GENERATION",
        params={"resourceTypes": ["DOCUMENT", "SLIDES"], "query": "联合索引"},
        taskId="task-bundle",
        traceId="trace-bundle",
    )

    events = [event async for event in supervisor.stream(request)]

    published = [event for event in events if event.event in {"resource_file", "question_batch"}]
    assert len(published) == 2
    assert all(event.payload.generated_by == "LLM" for event in published)
    assert all(event.payload.provider == "test-provider" for event in published)
    assert all(event.payload.model == "test-model" for event in published)
    assert all(event.payload.content_origin == "LLM" for event in published)
    assert all(event.payload.fallback is False for event in published)
    assert events[-1].event == "done"


@pytest.mark.asyncio
async def test_resource_bundle_partial_failed_publishes_only_successful_real_outputs() -> None:
    supervisor = PythonAgentSupervisor()
    _install_success_bundle(supervisor)
    supervisor.agent_registry["slide_generator"] = _FailingResourceAgent()
    request = EngineStreamRequest(
        serviceType="RESOURCE_GENERATION",
        params={"resourceTypes": ["DOCUMENT", "SLIDES", "MINDMAP", "QUIZ", "READING"], "query": "联合索引"},
        taskId="task-partial-bundle",
        traceId="trace-partial-bundle",
    )

    events = [event async for event in supervisor.stream(request)]

    published = [event for event in events if event.event in {"resource_file", "question_batch"}]
    assert len(published) == 3
    assert all(event.payload.generated_by == "LLM" for event in published)
    assert not any(getattr(event.payload, "asset_type", "") == "SLIDES" for event in published)
    assert any(
        event.event == "progress"
        and event.payload.status == "FAILED"
        and event.payload.artifact_type == "SLIDES"
        for event in events
    )
    assert events[-1].event == "done"
    assert events[-1].payload.status == "PARTIAL_FAILED"
    assert events[-1].payload.resource_failures[0]["resourceType"] == "SLIDES"


@pytest.mark.asyncio
async def test_resource_bundle_quiz_llm_failure_is_partial_when_other_resources_succeed() -> None:
    supervisor = PythonAgentSupervisor()
    _install_success_bundle(supervisor)
    supervisor.agent_registry["practice"] = _FailingPracticeResourceAgent()
    request = EngineStreamRequest(
        serviceType="RESOURCE_GENERATION",
        params={"resourceTypes": ["DOCUMENT", "QUIZ", "MINDMAP"], "query": "联合索引"},
        taskId="task-quiz-partial-bundle",
        traceId="trace-quiz-partial-bundle",
    )

    events = [event async for event in supervisor.stream(request)]

    assert [event.event for event in events].count("question_batch") == 0
    assert [
        event.payload.asset_type
        for event in events
        if event.event == "resource_file"
    ] == ["DOCUMENT", "MINDMAP"]
    assert any(
        event.event == "progress"
        and event.payload.status == "FAILED"
        and event.payload.artifact_type == "QUIZ"
        for event in events
    )
    assert events[-1].event == "done"
    assert events[-1].payload.status == "PARTIAL_FAILED"
    assert events[-1].payload.resource_failures[0]["resourceType"] == "QUIZ"
    assert "template fallback is not allowed" in events[-1].payload.resource_failures[0]["error"]


@pytest.mark.asyncio
async def test_resource_bundle_runs_requested_generation_agents_concurrently() -> None:
    supervisor = PythonAgentSupervisor()
    probe = _ResourceStartProbe(expected_count=3)
    supervisor.agent_registry["query_rewrite"] = _StubRewriteAgent()
    supervisor.agent_registry["retrieval"] = _StubRetrievalAgent()
    supervisor.agent_registry["critic"] = _StubCriticAgent()
    supervisor.agent_registry["document_generator"] = _OrderedResourceAgent("DOCUMENT", "document_generation", probe)
    supervisor.agent_registry["slide_generator"] = _OrderedResourceAgent("SLIDES", "slide_generation", probe)
    supervisor.agent_registry["mindmap_generator"] = _OrderedResourceAgent("MINDMAP", "mindmap_generation", probe)
    request = EngineStreamRequest(
        serviceType="RESOURCE_GENERATION",
        params={"resourceTypes": ["DOCUMENT", "SLIDES", "MINDMAP"], "query": "联合索引"},
        taskId="task-ordered-bundle",
        traceId="trace-ordered-bundle",
    )

    events = [event async for event in supervisor.stream(request)]

    assert set(probe.started) == {
        "document_generation",
        "slide_generation",
        "mindmap_generation",
    }
    assert len([event for event in events if event.event == "resource_file"]) == 3


@pytest.mark.asyncio
async def test_resource_bundle_reports_assets_in_completion_order() -> None:
    supervisor = PythonAgentSupervisor()
    supervisor.agent_registry["query_rewrite"] = _StubRewriteAgent()
    supervisor.agent_registry["retrieval"] = _StubRetrievalAgent()
    supervisor.agent_registry["critic"] = _StubCriticAgent()
    supervisor.agent_registry["document_generator"] = _DelayedResourceAgent("DOCUMENT", "document_generation", 0.05)
    supervisor.agent_registry["slide_generator"] = _DelayedResourceAgent("SLIDES", "slide_generation", 0.0)
    request = EngineStreamRequest(
        serviceType="RESOURCE_GENERATION",
        params={"resourceTypes": ["DOCUMENT", "SLIDES"], "query": "鑱斿悎绱㈠紩"},
        taskId="task-completion-order-bundle",
        traceId="trace-completion-order-bundle",
    )

    events = [event async for event in supervisor.stream(request)]
    published = [event.payload.asset_type for event in events if event.event == "resource_file"]

    assert published == ["SLIDES", "DOCUMENT"]
    assert supervisor.resolve_route("RESOURCE_GENERATION", request.params).agent_names[-1] == "resource_bundle"

    workflow = ResourceBundleWorkflow(
        agent_registry=supervisor.agent_registry,
        snapshot_builder=supervisor.snapshot_builder,
        system_prompt_builder=lambda agent_name, snapshot: supervisor.build_agent_system_prompt(
            agent_name=agent_name,
            snapshot=snapshot,
        ),
    )
    final_state = await workflow.run(
        request=request,
        params=request.params,
        snapshot=_snapshot(),
    )
    final_asset_types = [
        item.get("assetType")
        for item in final_state.params.get("generatedAssets", [])
    ]

    assert final_asset_types == ["SLIDES", "DOCUMENT"]


@pytest.mark.asyncio
async def test_resource_bundle_rejects_mismatched_resource_type_events() -> None:
    supervisor = PythonAgentSupervisor()
    supervisor.agent_registry["query_rewrite"] = _StubRewriteAgent()
    supervisor.agent_registry["retrieval"] = _StubRetrievalAgent()
    supervisor.agent_registry["critic"] = _StubCriticAgent()
    supervisor.agent_registry["document_generator"] = _WrongTypeResourceAgent("CODE", "document_generation")
    supervisor.agent_registry["mindmap_generator"] = _StubResourceAgent("MINDMAP", "mindmap_generation")
    request = EngineStreamRequest(
        serviceType="RESOURCE_GENERATION",
        params={"resourceTypes": ["DOCUMENT", "MINDMAP"], "query": "联合索引"},
        taskId="task-mismatched-resource-type",
        traceId="trace-mismatched-resource-type",
    )

    events = [event async for event in supervisor.stream(request)]
    published = [
        event.payload.asset_type
        for event in events
        if event.event == "resource_file"
    ]
    failed = [
        event
        for event in events
        if event.event == "progress"
        and event.payload.status == "FAILED"
        and event.payload.artifact_type == "DOCUMENT"
    ]

    assert published == ["MINDMAP"]
    assert failed
    assert events[-1].event == "done"
    assert events[-1].payload.status == "PARTIAL_FAILED"
    assert events[-1].payload.resource_failures[0]["resourceType"] == "DOCUMENT"


@pytest.mark.asyncio
async def test_resource_bundle_fails_without_resource_file_when_llm_unavailable() -> None:
    supervisor = PythonAgentSupervisor()
    supervisor.agent_registry["query_rewrite"] = _StubRewriteAgent()
    supervisor.agent_registry["retrieval"] = _StubRetrievalAgent()
    for agent_name in ("document_generator", "slide_generator", "mindmap_generator", "reading_generator", "practice"):
        supervisor.agent_registry[agent_name] = _FailingResourceAgent()
    request = EngineStreamRequest(
        serviceType="RESOURCE_GENERATION",
        params={"query": "联合索引"},
        taskId="task-llm-down",
        traceId="trace-llm-down",
    )
    events = []

    async for event in supervisor.stream(request):
        events.append(event)

    assert not any(event.event in {"resource_file", "question_batch"} for event in events)
    assert events[-2].event == "error"
    assert any(
        event.event == "progress"
        and event.payload.status == "FAILED"
        and event.payload.artifact_type == "DOCUMENT"
        for event in events
    )
    assert events[-1].payload.status == "FAILED"


@pytest.mark.asyncio
async def test_resource_bundle_quality_gate_failure_fails_whole_task() -> None:
    supervisor = PythonAgentSupervisor()
    _install_success_bundle(supervisor)
    supervisor.agent_registry["slide_generator"] = _QualityGateFailingResourceAgent()
    request = EngineStreamRequest(
        serviceType="RESOURCE_GENERATION",
        params={"resourceTypes": ["DOCUMENT", "SLIDES"], "query": "联合索引"},
        taskId="task-quality-gate-fail",
        traceId="trace-quality-gate-fail",
    )

    events = [event async for event in supervisor.stream(request)]

    assert events[-2].event == "error"
    assert events[-2].payload.code == "RESOURCE_BUNDLE_FAILED"
    assert events[-1].event == "done"
    assert events[-1].payload.status == "FAILED"
    assert [event.seq for event in events] == sorted({event.seq for event in events})


@pytest.mark.asyncio
async def test_practice_agent_disables_template_fallback_when_llm_fails() -> None:
    agent = PracticeAgent(question_generator=_FailingQuestionGenerator())

    with pytest.raises(RuntimeError, match="template fallback is not allowed"):
        _ = [
            event
            async for event in agent.run(
                task_id="task-practice",
                trace_id="trace-practice",
                seq=1,
                service_type="PRACTICE_JUDGE",
                params={"topic": "联合索引", "count": 5},
                snapshot=_snapshot(),
                system_prompt="practice",
            )
        ]


@pytest.mark.asyncio
async def test_query_rewrite_resource_generation_uses_direct_fallback_when_llm_fails() -> None:
    agent = QueryRewriteAgent(llm_client=object(), llm_generator=_FailingRewriteGenerator())

    params = {"query": "联合索引"}
    events = [
        event
        async for event in agent.run(
            task_id="task-rewrite",
            trace_id="trace-rewrite",
            seq=1,
            service_type="RESOURCE_GENERATION",
            params=params,
            snapshot=_snapshot(),
            system_prompt="rewrite",
        )
    ]

    assert [event.event for event in events] == ["progress", "result_chunk"]
    assert params["rewrittenQuery"]
    assert params["keywords"]
