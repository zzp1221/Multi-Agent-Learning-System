import pytest

from src.ai_modules.memory import InMemoryLearningLoopStore, ResilientLearningLoopStore
from src.ai_modules.models.profile import LearnerProfileDimensions
from src.ai_modules.retrieval.query_classifier import (
    QUERY_TYPE_NEW_CONCEPT,
    QUERY_TYPE_SMALL_TALK,
    RETRIEVAL_DEEP_EVIDENCE,
    RETRIEVAL_LOCAL_HYBRID,
    RETRIEVAL_NONE,
    QueryClassification,
)
from src.ai_modules.runtime.autonomous_planning import (
    AutonomousPresetRouter,
    GoalCritic,
    GoalLoopPlanner,
    LearningLoopOrchestrator,
    PlanningCheckpointManager,
    PROFILE_COMPLETENESS_FIELDS,
    PRESET_RAG_DEEP_TUTOR,
    PRESET_RAG_TUTOR,
    PRESET_RAG_TUTOR_WITH_PRACTICE,
    profile_alias,
)
from src.ai_modules.runtime.planning_contract import PlanningParamKeys
from src.ai_modules.runtime.resource_bundle_workflow import RESOURCE_AGENT_BY_TYPE, ResourceBundleWorkflow
from src.ai_modules.supervisor import PythonAgentSupervisor


def _resource_type_added_by_practice_preset(router: AutonomousPresetRouter) -> str:
    resource_types = router._resource_types_for_preset_delta(
        preset=PRESET_RAG_TUTOR_WITH_PRACTICE,
        base_preset=PRESET_RAG_TUTOR,
    )
    assert resource_types
    return sorted(resource_types)[0]


def _classification(
    *,
    query_type: str = QUERY_TYPE_NEW_CONCEPT,
    retrieval_strategy: str = RETRIEVAL_LOCAL_HYBRID,
    confidence: float = 0.76,
) -> QueryClassification:
    return QueryClassification(
        query_type=query_type,
        retrieval_strategy=retrieval_strategy,
        confidence=confidence,
        reason="test",
    )


def _checkpoint_manager() -> PlanningCheckpointManager:
    fallback = InMemoryLearningLoopStore()
    return PlanningCheckpointManager(
        store=ResilientLearningLoopStore(primary=fallback, fallback=fallback),
    )


def test_calculus_question_without_image_routes_to_plain_rag_tutor() -> None:
    router = AutonomousPresetRouter()

    decision = router.route(
        service_type="TUTORING",
        params={"query": "Explain the derivative of x squared."},
        classification=_classification(),
    )

    assert decision is not None
    assert decision.preset == PRESET_RAG_TUTOR


def test_quiz_preset_requires_structured_resource_type_not_text_terms() -> None:
    router = AutonomousPresetRouter()
    practice_resource_type = _resource_type_added_by_practice_preset(router)

    text_only = router.route(
        service_type="TUTORING",
        params={"query": "Give me several similar exercises about derivatives."},
        classification=_classification(),
    )
    structured = router.route(
        service_type="TUTORING",
        params={"query": "Derivatives", PlanningParamKeys.RESOURCE_TYPES: [practice_resource_type]},
        classification=_classification(),
    )

    assert text_only is not None
    assert text_only.preset == PRESET_RAG_TUTOR
    assert structured is not None
    assert structured.preset == PRESET_RAG_TUTOR_WITH_PRACTICE


def test_quiz_preset_does_not_use_resource_type_resolver_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    router = AutonomousPresetRouter()
    practice_resource_type = _resource_type_added_by_practice_preset(router)

    monkeypatch.setattr(ResourceBundleWorkflow, "resolve_resource_types", lambda params: [practice_resource_type])

    decision = router.route(
        service_type="TUTORING",
        params={"query": "Give me several similar exercises about derivatives."},
        classification=_classification(),
    )

    assert decision is not None
    assert decision.preset == PRESET_RAG_TUTOR


def test_structured_quiz_resource_type_overrides_classifier_metadata() -> None:
    router = AutonomousPresetRouter()
    practice_resource_type = _resource_type_added_by_practice_preset(router)

    decision = router.route(
        service_type="TUTORING",
        params={"query": "hi", PlanningParamKeys.RESOURCE_TYPES: [practice_resource_type]},
        classification=_classification(
            query_type=QUERY_TYPE_SMALL_TALK,
            retrieval_strategy=RETRIEVAL_NONE,
            confidence=0.95,
        ),
    )

    assert decision is not None
    assert decision.preset == PRESET_RAG_TUTOR_WITH_PRACTICE


def test_low_confidence_classifier_does_not_emit_preset_decision() -> None:
    router = AutonomousPresetRouter()

    decision = router.route(
        service_type="TUTORING",
        params={"query": "hi"},
        classification=_classification(
            query_type=QUERY_TYPE_SMALL_TALK,
            retrieval_strategy=RETRIEVAL_NONE,
            confidence=0.2,
        ),
    )

    assert decision is None


def test_deep_rag_preset_uses_classifier_or_structured_mode() -> None:
    router = AutonomousPresetRouter()

    by_classifier = router.route(
        service_type="TUTORING",
        params={"query": "Prove the theorem."},
        classification=_classification(retrieval_strategy=RETRIEVAL_DEEP_EVIDENCE),
    )
    by_param = router.route(
        service_type="TUTORING",
        params={"query": "Prove the theorem.", "reasoningMode": "DEEP"},
        classification=None,
    )

    assert by_classifier is not None
    assert by_classifier.preset == PRESET_RAG_DEEP_TUTOR
    assert by_param is not None
    assert by_param.preset == PRESET_RAG_DEEP_TUTOR


@pytest.mark.asyncio
async def test_retrieval_checkpoint_upgrades_by_configured_strategy_order() -> None:
    manager = _checkpoint_manager()
    params = {
        "retrievalStrategy": RETRIEVAL_LOCAL_HYBRID,
        "retrievalResult": {"documents": []},
    }

    action = await manager.check_retrieval_evidence(
        params=params,
        user_id="00000000-0000-0000-0000-000000000001",
    )

    assert action is not None
    assert action.status == "APPLIED"
    assert params["retrievalStrategy"] != RETRIEVAL_LOCAL_HYBRID
    assert params["checkpointActions"][0]["checkpointType"] == "RETRIEVAL_EVIDENCE"


@pytest.mark.asyncio
async def test_profile_checkpoint_uses_profile_model_registry_fields() -> None:
    manager = _checkpoint_manager()
    params = {"profileAnalysis": {"knowledgeFoundation": "BASIC"}}

    action = await manager.check_profile_completeness(
        params=params,
        user_id="00000000-0000-0000-0000-000000000001",
    )

    assert action is not None
    assert action.status == "APPLIED"
    assert "profileMissingDimensions" in params
    assert "profileCompleteness" in params


@pytest.mark.asyncio
async def test_profile_checkpoint_does_not_count_profile_model_defaults_as_complete() -> None:
    manager = _checkpoint_manager()
    profile = LearnerProfileDimensions(knowledgeFoundation="BASIC", weakPoints=["derivatives"]).model_dump(by_alias=True)
    params = {"profileAnalysis": profile}

    action = await manager.check_profile_completeness(
        params=params,
        user_id="00000000-0000-0000-0000-000000000001",
    )

    assert action is not None
    default_aliases = {
        profile_alias(field_name)
        for field_name in PROFILE_COMPLETENESS_FIELDS
        if field_name not in {"knowledge_foundation", "weak_points"}
    }
    assert default_aliases.intersection(params["profileMissingDimensions"])


@pytest.mark.asyncio
async def test_resource_coverage_checkpoint_feeds_goal_critic_replan() -> None:
    manager = _checkpoint_manager()
    params = {
        "generatedAssets": [{"assetType": "DOCUMENT", "title": "Derivative guide"}],
        "resourceTypes": ["DOCUMENT"],
    }

    action = await manager.check_resource_coverage(
        params=params,
        user_id="00000000-0000-0000-0000-000000000001",
    )
    verdict = GoalCritic().verdict(params=params)

    assert action is not None
    assert action.status == "APPLIED"
    assert params["resourceCoverageGap"]["missingTypes"]
    assert params["resourceCoverageSupplementTypes"]
    assert verdict["status"] == "NEEDS_REPLAN"
    assert any("resourceCoverageMissing" in issue for issue in verdict["issues"])


def test_goal_loop_planner_uses_templates_and_resource_registry() -> None:
    planner = GoalLoopPlanner()
    practice_resource_type = _resource_type_added_by_practice_preset(AutonomousPresetRouter())

    subgoals = planner.decompose(
        params={
            "goal": "Learn calculus foundations",
            PlanningParamKeys.RESOURCE_TYPES: ["DOCUMENT", practice_resource_type],
        }
    )

    assert subgoals
    assert all("assignedPreset" in subgoal for subgoal in subgoals)
    assert all(
        resource_type in RESOURCE_AGENT_BY_TYPE
        for subgoal in subgoals
        for resource_type in subgoal["preferredResourceTypes"]
    )


@pytest.mark.asyncio
async def test_learning_loop_orchestrator_records_local_replan() -> None:
    store = InMemoryLearningLoopStore()
    orchestrator = LearningLoopOrchestrator(
        store=ResilientLearningLoopStore(primary=store, fallback=store),
    )
    params = {
        "goal": "Learn calculus foundations",
        "learningPath": {"summaryText": "Initial plan"},
        "criticReview": {"verdict": "REVISE", "coverageScore": 0.4},
    }

    await orchestrator.start_loop(
        params=params,
        user_id="00000000-0000-0000-0000-000000000001",
        task_id="00000000-0000-0000-0000-000000000002",
        conversation_id="conv-1",
    )
    loop = await orchestrator.close_loop(
        params=params,
        user_id="00000000-0000-0000-0000-000000000001",
    )

    assert loop["status"] == "PARTIAL_FAILED"
    assert loop["replans"]
    assert params["learningLoop"]["verdicts"]
    assert loop["goals"][0]["status"] == "NEEDS_REPLAN"
    assert all(goal["status"] == "PENDING" for goal in loop["goals"][1:])


def test_supervisor_done_payload_includes_planning_metadata() -> None:
    supervisor = PythonAgentSupervisor()
    route = supervisor.resolve_route("TUTORING", {"query": "What is a derivative?"})
    params: dict = {}
    supervisor._seed_query_routing_params(params, route)

    payload = supervisor._build_done_payload(
        service_type="TUTORING",
        agent_names=route.agent_names,
        params=params,
    )

    assert payload.planning is not None
    assert payload.planning["preset"] == PRESET_RAG_TUTOR
