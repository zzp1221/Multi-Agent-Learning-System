"""Bounded autonomous planning for preset routing, checkpoints, and goal loops."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from pathlib import Path
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.ai_modules.memory import ResilientLearningLoopStore
from src.ai_modules.memory.profile_feature_registry import FEATURE_DIMENSION_SPECS
from src.ai_modules.models.profile import LearnerProfileDimensions
from src.ai_modules.retrieval.query_classifier import (
    QUERY_TYPE_ANSWER_PREVIOUS,
    QUERY_TYPE_CURRENT_INFO,
    QUERY_TYPE_FOLLOW_UP,
    QUERY_TYPE_IMAGE_QUESTION,
    QUERY_TYPE_SMALL_TALK,
    RETRIEVAL_CONTEXT_ONLY,
    RETRIEVAL_DEEP_EVIDENCE,
    RETRIEVAL_LOCAL_HYBRID,
    RETRIEVAL_NONE,
    RETRIEVAL_WEB_AUGMENTED,
)
from src.ai_modules.runtime.planning_contract import PlanningParamKeys, profile_alias
from src.ai_modules.runtime.resource_bundle_workflow import RESOURCE_AGENT_BY_TYPE, ResourceBundleWorkflow

LOGGER = logging.getLogger(__name__)

PlanningLevel = Literal["static", "preset_router", "checkpoint_replan", "goal_loop"]

PRESET_DIRECT_TUTOR = "DIRECT_TUTOR"
PRESET_CONTEXT_TUTOR = "CONTEXT_TUTOR"
PRESET_RAG_TUTOR = "RAG_TUTOR"
PRESET_RAG_DEEP_TUTOR = "RAG_DEEP_TUTOR"
PRESET_IMAGE_RAG_TUTOR = "IMAGE_RAG_TUTOR"
PRESET_RAG_TUTOR_WITH_PRACTICE = "RAG_TUTOR_WITH_PRACTICE"
PRESET_RESOURCE_BUNDLE_WORKFLOW = "RESOURCE_BUNDLE_WORKFLOW"
PRESET_PERSONALIZED_LEARNING_WORKFLOW = "PERSONALIZED_LEARNING_WORKFLOW"

CHECKPOINT_PROFILE_COMPLETENESS = "PROFILE_COMPLETENESS"
CHECKPOINT_RETRIEVAL_EVIDENCE = "RETRIEVAL_EVIDENCE"
CHECKPOINT_RESOURCE_COVERAGE = "RESOURCE_COVERAGE"
CHECKPOINT_GOAL_CRITIC = "GOAL_CRITIC"

RULES_PATH = Path(__file__).with_name("autonomous_planning_rules.json")


def _load_rules() -> dict[str, Any]:
    try:
        with RULES_PATH.open("r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        if isinstance(loaded, dict):
            return loaded
    except Exception:
        LOGGER.warning("Failed to load autonomous planning rules: %s", RULES_PATH, exc_info=True)
    return {}


PLANNING_RULES = _load_rules()


def _rules_value(path: tuple[str, ...], default: Any = None) -> Any:
    value: Any = PLANNING_RULES
    for key in path:
        value = value.get(key) if isinstance(value, dict) else None
    return default if value is None else value


def _rules_list(path: tuple[str, ...], default: list[Any] | None = None) -> list[Any]:
    value = _rules_value(path, default or [])
    return list(value) if isinstance(value, list) else list(default or [])


def _rules_int(path: tuple[str, ...], default: int) -> int:
    try:
        return int(_rules_value(path, default))
    except (TypeError, ValueError):
        return default


def _rules_float(path: tuple[str, ...], default: float) -> float:
    try:
        return float(_rules_value(path, default))
    except (TypeError, ValueError):
        return default


def _rules_dict(path: tuple[str, ...]) -> dict[str, Any]:
    value = _rules_value(path, {})
    return dict(value) if isinstance(value, dict) else {}


def _dimension_matches_registry(field_name: str, registry_dimensions: set[str]) -> bool:
    if field_name in registry_dimensions:
        return True
    return field_name.endswith("s") and field_name[:-1] in registry_dimensions


def _profile_completeness_fields() -> tuple[str, ...]:
    excluded = {str(item) for item in _rules_list(("profileCompleteness", "excludedModelFields"))}
    registry_dimensions = {spec.dimension for spec in FEATURE_DIMENSION_SPECS}
    return tuple(
        field_name
        for field_name in LearnerProfileDimensions.model_fields
        if field_name not in excluded and _dimension_matches_registry(field_name, registry_dimensions)
    )


PROFILE_COMPLETENESS_FIELDS = _profile_completeness_fields()
RESOURCE_TYPE_ORDER = tuple(RESOURCE_AGENT_BY_TYPE.keys())
DEFAULT_PROFILE_VALUES = LearnerProfileDimensions().model_dump(by_alias=True)


class PresetDecision(BaseModel):
    """A bounded planner decision; arbitrary agent chains are never accepted."""

    preset: str
    reason: str = ""
    confidence: float = 0.75
    retrieval_strategy: str | None = Field(default=None, alias="retrievalStrategy")
    planning_level: PlanningLevel | None = Field(default=None, alias="planningLevel")
    fallback: bool = False

    model_config = ConfigDict(populate_by_name=True)


class CheckpointAction(BaseModel):
    """Serializable checkpoint decision shared with SSE and persistence."""

    checkpoint_type: str = Field(alias="checkpointType")
    trigger_reason: str = Field(alias="triggerReason")
    action: str
    status: str = "RECORDED"
    before: dict[str, Any] = Field(default_factory=dict)
    after: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(populate_by_name=True)


@dataclass(frozen=True)
class PresetRoute:
    agent_names: tuple[str, ...]
    retrieval_strategy: str | None = None
    planning_level: PlanningLevel = "preset_router"
    param_updates: dict[str, Any] | None = None


def _load_preset_routes() -> dict[str, PresetRoute]:
    routes: dict[str, PresetRoute] = {}
    for preset, raw_route in _rules_dict(("presetRoutes",)).items():
        if not isinstance(preset, str) or not isinstance(raw_route, dict):
            continue
        raw_agents = raw_route.get("agents")
        if not isinstance(raw_agents, list) or not raw_agents:
            continue
        planning_level = str(raw_route.get("planningLevel") or "preset_router")
        if planning_level not in {"static", "preset_router", "checkpoint_replan", "goal_loop"}:
            planning_level = "preset_router"
        param_updates = raw_route.get("paramUpdates")
        routes[preset] = PresetRoute(
            agent_names=tuple(str(agent_name) for agent_name in raw_agents if str(agent_name).strip()),
            retrieval_strategy=str(raw_route["retrievalStrategy"]).strip().upper()
            if raw_route.get("retrievalStrategy")
            else None,
            planning_level=planning_level,  # type: ignore[arg-type]
            param_updates=dict(param_updates) if isinstance(param_updates, dict) else {},
        )
    return routes


PRESET_ROUTES = _load_preset_routes()


class AutonomousPresetRouter:
    """Choose a safe preset from structured signals and existing classifier metadata."""

    def __init__(self, preset_routes: dict[str, PresetRoute] | None = None) -> None:
        self.preset_routes = preset_routes or PRESET_ROUTES
        self.min_confidence = _rules_float(("router", "minConfidence"), 0.6)

    def route(
        self,
        *,
        service_type: str,
        params: dict[str, Any],
        classification: Any | None = None,
    ) -> PresetDecision | None:
        normalized_service_type = service_type.strip().upper()
        if normalized_service_type == "RESOURCE_GENERATION":
            return self._decision(PRESET_RESOURCE_BUNDLE_WORKFLOW, "resource_generation_service", 0.98)
        if normalized_service_type == "PERSONALIZED_LEARNING":
            return self._decision(
                PRESET_PERSONALIZED_LEARNING_WORKFLOW,
                "personalized_learning_goal_loop",
                0.95,
                planning_level="goal_loop",
            )
        if normalized_service_type != "TUTORING":
            return None

        if self._has_image(params):
            return self._decision(PRESET_IMAGE_RAG_TUTOR, "structured:image_input", 0.9)
        if self._explicit_practice_resource_requested(params):
            return self._decision(PRESET_RAG_TUTOR_WITH_PRACTICE, "structured:quiz_resource_requested", 0.82)

        if classification is not None:
            query_type = str(getattr(classification, "query_type", "") or "")
            strategy = getattr(classification, "retrieval_strategy", None)
            confidence = float(getattr(classification, "confidence", 0.7) or 0.7)
            if confidence < self.min_confidence:
                return None
            if query_type == QUERY_TYPE_IMAGE_QUESTION:
                return self._decision(
                    PRESET_IMAGE_RAG_TUTOR,
                    "classifier:image_question",
                    confidence,
                    retrieval_strategy=strategy or RETRIEVAL_LOCAL_HYBRID,
                )
            if query_type == QUERY_TYPE_SMALL_TALK:
                return self._decision(
                    PRESET_DIRECT_TUTOR,
                    "classifier:small_talk",
                    confidence,
                    retrieval_strategy=RETRIEVAL_NONE,
                )
            if query_type in {QUERY_TYPE_FOLLOW_UP, QUERY_TYPE_ANSWER_PREVIOUS}:
                return self._decision(
                    PRESET_CONTEXT_TUTOR,
                    "classifier:context_follow_up",
                    confidence,
                    retrieval_strategy=RETRIEVAL_CONTEXT_ONLY,
                )
            if strategy == RETRIEVAL_DEEP_EVIDENCE:
                return self._decision(
                    PRESET_RAG_DEEP_TUTOR,
                    "classifier:deep_evidence",
                    confidence,
                    retrieval_strategy=RETRIEVAL_DEEP_EVIDENCE,
                )
            if query_type == QUERY_TYPE_CURRENT_INFO:
                return self._decision(
                    PRESET_RAG_TUTOR,
                    "classifier:current_info",
                    confidence,
                    retrieval_strategy=RETRIEVAL_WEB_AUGMENTED,
                )

        if str(params.get("reasoningMode") or "").strip().upper() == "DEEP" or params.get("deepReasoning") is True:
            return self._decision(
                PRESET_RAG_DEEP_TUTOR,
                "structured:deep_reasoning_mode",
                0.82,
                retrieval_strategy=RETRIEVAL_DEEP_EVIDENCE,
            )
        return self._decision(PRESET_RAG_TUTOR, "default_course_question", 0.74)

    def expand(self, decision: PresetDecision) -> PresetRoute:
        route = self.preset_routes.get(decision.preset)
        if route is None:
            raise ValueError(f"Unsupported planning preset: {decision.preset}")
        retrieval_strategy = decision.retrieval_strategy or route.retrieval_strategy
        return PresetRoute(
            agent_names=tuple(route.agent_names),
            retrieval_strategy=retrieval_strategy,
            planning_level=decision.planning_level or route.planning_level,
            param_updates=dict(route.param_updates or {}),
        )

    def _decision(
        self,
        preset: str,
        reason: str,
        confidence: float,
        *,
        retrieval_strategy: str | None = None,
        planning_level: PlanningLevel | None = None,
    ) -> PresetDecision | None:
        if preset not in self.preset_routes:
            return None
        return PresetDecision(
            preset=preset,
            reason=reason,
            confidence=confidence,
            retrievalStrategy=retrieval_strategy,
            planningLevel=planning_level,
        )

    def _explicit_practice_resource_requested(self, params: dict[str, Any]) -> bool:
        target_resource_types = self._resource_types_for_preset_delta(
            preset=PRESET_RAG_TUTOR_WITH_PRACTICE,
            base_preset=PRESET_RAG_TUTOR,
        )
        return any(
            resource_type in target_resource_types
            for resource_type in self._explicit_resource_types(params)
        )

    def _resource_types_for_preset_delta(self, *, preset: str, base_preset: str) -> set[str]:
        preset_route = self.preset_routes.get(preset)
        base_route = self.preset_routes.get(base_preset)
        if preset_route is None:
            return set()
        base_agents = set(base_route.agent_names) if base_route is not None else set()
        added_agents = set(preset_route.agent_names) - base_agents
        return {
            resource_type
            for resource_type, agent_name in RESOURCE_AGENT_BY_TYPE.items()
            if agent_name in added_agents
        }

    @staticmethod
    def _explicit_resource_types(params: dict[str, Any]) -> list[str]:
        resolved: list[str] = []
        for value in AutonomousPresetRouter._raw_requested_resource_types(params):
            text = str(value).strip()
            if not text:
                continue
            normalized = ResourceBundleWorkflow.normalize_resource_type(text)
            if normalized in RESOURCE_AGENT_BY_TYPE and normalized not in resolved:
                resolved.append(normalized)
        return resolved

    @staticmethod
    def _raw_requested_resource_types(params: dict[str, Any]) -> list[Any]:
        raw_types = params.get(PlanningParamKeys.RESOURCE_TYPES)
        if isinstance(raw_types, list):
            return raw_types
        raw_type = params.get(PlanningParamKeys.RESOURCE_TYPE)
        return [raw_type] if raw_type else []

    @staticmethod
    def _has_image(params: dict[str, Any]) -> bool:
        for key in PlanningParamKeys.IMAGE_INPUTS:
            value = params.get(key)
            if isinstance(value, (list, dict)) and bool(value):
                return True
        return False


class PlanningCheckpointManager:
    """Evaluate bounded checkpoints and record planning actions."""

    def __init__(self, store: ResilientLearningLoopStore | None = None) -> None:
        self.store = store or ResilientLearningLoopStore()
        self.min_profile_dimensions = _rules_int(
            ("profileCompleteness", "minDimensionCount"),
            min(6, max(1, len(PROFILE_COMPLETENESS_FIELDS))),
        )
        self.empty_profile_values = {
            str(item).strip().upper()
            for item in _rules_list(("profileCompleteness", "emptyStringValues"), ["", "UNKNOWN"])
        }
        self.min_retrieval_documents = _rules_int(("retrievalEvidence", "minDocumentCount"), 2)
        self.min_top_score = _rules_float(("retrievalEvidence", "minTopScore"), 0.25)
        self.retrieval_upgrade_order = [
            str(item).strip().upper()
            for item in _rules_list(("retrievalEvidence", "upgradeOrder"), [RETRIEVAL_LOCAL_HYBRID, RETRIEVAL_DEEP_EVIDENCE])
            if str(item).strip()
        ]
        self.min_resource_types = _rules_int(("resourceCoverage", "minTypeCount"), 5)
        self.passing_verdicts = {
            str(item).strip().upper()
            for item in _rules_list(("critic", "passingVerdicts"), ["PASS", "PASSED", "GOOD", "APPROVED", "OK", "SUCCESS"])
        }
        self.min_critic_score = _rules_float(("critic", "minScore"), 0.8)

    async def check_profile_completeness(
        self,
        *,
        params: dict[str, Any],
        user_id: str,
        loop_id: str | None = None,
        subgoal_id: str | None = None,
    ) -> CheckpointAction | None:
        profile = self._profile_context(params)
        missing = self._missing_profile_dimensions(profile)
        if not missing:
            return None
        action = CheckpointAction(
            checkpointType=CHECKPOINT_PROFILE_COMPLETENESS,
            triggerReason=f"missing_profile_dimensions:{','.join(missing)}",
            action="continue_with_profile_gap_recorded",
            status="APPLIED",
            before={"profileDimensionCount": len(PROFILE_COMPLETENESS_FIELDS)},
            after={"profileCompleteness": "LOW", "missingDimensions": missing},
        )
        params[PlanningParamKeys.PROFILE_COMPLETENESS] = "LOW"
        params[PlanningParamKeys.PROFILE_MISSING_DIMENSIONS] = missing
        await self._record_checkpoint(params=params, action=action, user_id=user_id, loop_id=loop_id, subgoal_id=subgoal_id)
        return action

    async def check_retrieval_evidence(
        self,
        *,
        params: dict[str, Any],
        user_id: str,
        loop_id: str | None = None,
        subgoal_id: str | None = None,
    ) -> CheckpointAction | None:
        retrieval_result = params.get("retrievalResult")
        if not isinstance(retrieval_result, dict):
            return None
        documents = retrieval_result.get("documents")
        documents = documents if isinstance(documents, list) else []
        weak_reason = self._retrieval_weak_reason(documents)
        if not weak_reason:
            return None
        current_strategy = str(params.get(PlanningParamKeys.RETRIEVAL_STRATEGY) or RETRIEVAL_LOCAL_HYBRID).strip().upper()
        next_strategy = self.next_retrieval_strategy(current_strategy)
        status = "APPLIED" if next_strategy and next_strategy != current_strategy else "RECORDED"
        if status == "APPLIED":
            params[PlanningParamKeys.RETRIEVAL_STRATEGY] = next_strategy
            if next_strategy == RETRIEVAL_WEB_AUGMENTED:
                params[PlanningParamKeys.WEB_SEARCH_ENABLED] = True
        action = CheckpointAction(
            checkpointType=CHECKPOINT_RETRIEVAL_EVIDENCE,
            triggerReason=weak_reason,
            action=f"upgrade_retrieval:{current_strategy}->{next_strategy or current_strategy}",
            status=status,
            before={"retrievalStrategy": current_strategy, "documentCount": len(documents)},
            after={
                "retrievalStrategy": params.get(PlanningParamKeys.RETRIEVAL_STRATEGY),
                "webSearchEnabled": params.get(PlanningParamKeys.WEB_SEARCH_ENABLED),
            },
        )
        await self._record_checkpoint(params=params, action=action, user_id=user_id, loop_id=loop_id, subgoal_id=subgoal_id)
        return action

    async def check_resource_coverage(
        self,
        *,
        params: dict[str, Any],
        user_id: str,
        loop_id: str | None = None,
        subgoal_id: str | None = None,
    ) -> CheckpointAction | None:
        existing_resources = self._resource_records(params)
        existing_types = self._resource_record_types(existing_resources)
        critic_reason = self._critic_resource_issue(params)
        if len(existing_types) >= self.min_resource_types and not critic_reason:
            return None
        missing_types = [resource_type for resource_type in RESOURCE_TYPE_ORDER if resource_type not in existing_types]
        preferred = self._preferred_resource_types(params)
        required_count = max(0, self.min_resource_types - len(existing_types))
        supplement_types = self._rank_resource_types(missing_types, preferred)[: required_count or 1]
        if supplement_types:
            params[PlanningParamKeys.RESOURCE_TYPES] = self._unique([*self._raw_resource_types(params), *supplement_types])
            params[PlanningParamKeys.RESOURCE_COVERAGE_SUPPLEMENT_TYPES] = supplement_types
        reason = critic_reason or f"resource_type_count:{len(existing_types)}<{self.min_resource_types}"
        coverage_gap = {
            "existingTypes": sorted(existing_types),
            "minTypeCount": self.min_resource_types,
            "missingTypes": missing_types,
            "supplementTypes": supplement_types,
            "reason": reason,
        }
        params[PlanningParamKeys.RESOURCE_COVERAGE_GAP] = coverage_gap
        action = CheckpointAction(
            checkpointType=CHECKPOINT_RESOURCE_COVERAGE,
            triggerReason=reason,
            action="supplement_missing_resource_types",
            status="APPLIED" if supplement_types else "RECORDED",
            before={"resourceTypes": sorted(existing_types), "resourceCount": len(existing_resources)},
            after={"supplementTypes": supplement_types, "resourceTypes": params.get(PlanningParamKeys.RESOURCE_TYPES)},
        )
        await self._record_checkpoint(params=params, action=action, user_id=user_id, loop_id=loop_id, subgoal_id=subgoal_id)
        return action

    def next_retrieval_strategy(self, current_strategy: str) -> str | None:
        strategy = current_strategy.strip().upper() or RETRIEVAL_LOCAL_HYBRID
        if strategy not in self.retrieval_upgrade_order:
            return self.retrieval_upgrade_order[0] if self.retrieval_upgrade_order else None
        index = self.retrieval_upgrade_order.index(strategy)
        if index + 1 >= len(self.retrieval_upgrade_order):
            return None
        return self.retrieval_upgrade_order[index + 1]

    async def _record_checkpoint(
        self,
        *,
        params: dict[str, Any],
        action: CheckpointAction,
        user_id: str,
        loop_id: str | None,
        subgoal_id: str | None,
    ) -> None:
        action_dict = action.model_dump(by_alias=True)
        params.setdefault(PlanningParamKeys.CHECKPOINT_ACTIONS, []).append(action_dict)
        params.setdefault(PlanningParamKeys.PLANNING_TRACE, []).append(
            {
                "agentName": "planning_checkpoint",
                "status": action.status,
                "checkpointType": action.checkpoint_type,
                "reason": action.trigger_reason,
                "action": action.action,
            }
        )
        record = await self.store.record_checkpoint(
            user_id=user_id,
            loop_id=loop_id,
            subgoal_id=subgoal_id,
            checkpoint_type=action.checkpoint_type,
            trigger_reason=action.trigger_reason,
            action=action.action,
            status=action.status,
            before_payload=action.before,
            after_payload=action.after,
        )
        if isinstance(record, dict) and record.get("persistenceFallbackReason"):
            params.setdefault("planningWarnings", []).append(record["persistenceFallbackReason"])

    def _profile_context(self, params: dict[str, Any]) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        for key in PlanningParamKeys.PROFILE_CONTEXTS:
            value = params.get(key)
            if isinstance(value, dict):
                merged.update(value)
        profile_update = params.get("profileUpdate")
        if isinstance(profile_update, dict) and isinstance(profile_update.get("dimensions"), dict):
            merged.update(profile_update["dimensions"])
        return merged

    def _missing_profile_dimensions(self, profile: dict[str, Any]) -> list[str]:
        if not profile:
            return [profile_alias(field_name) for field_name in PROFILE_COMPLETENESS_FIELDS]
        explicit_keys = self._explicit_profile_keys(profile)
        present: list[str] = []
        missing: list[str] = []
        for field_name in PROFILE_COMPLETENESS_FIELDS:
            alias = profile_alias(field_name)
            if alias not in explicit_keys and field_name not in explicit_keys:
                missing.append(alias)
                continue
            value = profile.get(alias, profile.get(field_name))
            if self._is_default_profile_value(field_name=field_name, alias=alias, value=value):
                missing.append(alias)
                continue
            if self._has_profile_value(value):
                present.append(alias)
            else:
                missing.append(alias)
        if len(present) >= self.min_profile_dimensions:
            return []
        return missing

    def _explicit_profile_keys(self, profile: dict[str, Any]) -> set[str]:
        profile_keys = set(profile.keys())
        try:
            model = LearnerProfileDimensions.model_validate(profile)
        except Exception:
            return profile_keys
        provided = set(model.model_fields_set)
        return profile_keys | {profile_alias(field_name) for field_name in provided}

    @staticmethod
    def _is_default_profile_value(*, field_name: str, alias: str, value: Any) -> bool:
        default_value = DEFAULT_PROFILE_VALUES.get(alias)
        if default_value is None and alias != field_name:
            default_value = DEFAULT_PROFILE_VALUES.get(field_name)
        return value == default_value

    def _has_profile_value(self, value: Any) -> bool:
        if isinstance(value, str):
            text = value.strip()
            return bool(text) and text.upper() not in self.empty_profile_values
        if isinstance(value, list):
            return any(self._has_profile_value(item) for item in value)
        if isinstance(value, dict):
            return any(self._has_profile_value(item) for item in value.values())
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        return value is not None

    def _retrieval_weak_reason(self, documents: list[Any]) -> str:
        if len(documents) < self.min_retrieval_documents:
            return f"document_count:{len(documents)}<{self.min_retrieval_documents}"
        scores: list[float] = []
        evidence_count = 0
        for item in documents:
            if not isinstance(item, dict):
                continue
            try:
                scores.append(float(item.get("score") or 0.0))
            except (TypeError, ValueError):
                pass
            if str(item.get("evidence") or item.get("snippet") or "").strip():
                evidence_count += 1
            channel = str(item.get("channel") or "").lower()
            match_type = str(item.get("matchType") or item.get("match_type") or "").lower()
            slug = str(item.get("slug") or "").lower()
            if channel == "fallback" or match_type == "fallback" or slug.startswith("fallback-"):
                return "fallback_evidence"
        if evidence_count == 0:
            return "empty_evidence_text"
        if scores and max(scores) < self.min_top_score:
            return f"low_top_score:{max(scores):.2f}"
        return ""

    def _resource_records(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for key in ("generatedAssets", "pushedResources"):
            value = params.get(key)
            if isinstance(value, list):
                records.extend(item for item in value if isinstance(item, dict))
        push_plan = params.get("resourcePushPlan")
        if isinstance(push_plan, dict):
            for step in push_plan.get("stepResources", []):
                if not isinstance(step, dict):
                    continue
                resources = step.get("resources")
                if isinstance(resources, list):
                    records.extend(item for item in resources if isinstance(item, dict))
        return records

    def _resource_record_types(self, records: list[dict[str, Any]]) -> set[str]:
        resolved: set[str] = set()
        for item in records:
            resource_type = str(item.get("assetType") or item.get("resourceType") or "").strip()
            normalized = ResourceBundleWorkflow.normalize_resource_type(resource_type) if resource_type else ""
            if normalized in RESOURCE_AGENT_BY_TYPE:
                resolved.add(normalized)
        return resolved

    def _critic_resource_issue(self, params: dict[str, Any]) -> str:
        review = params.get("criticReview")
        if not isinstance(review, dict):
            return ""
        verdict = str(review.get("verdict") or "").strip().upper()
        for key in ("coverageScore", "resourceMatchScore", "pathOrderScore"):
            value = review.get(key)
            try:
                if value is not None and float(value) < self.min_critic_score:
                    return f"critic_{key}:{float(value):.2f}"
            except (TypeError, ValueError):
                continue
        if verdict and verdict not in self.passing_verdicts:
            return f"critic_verdict:{verdict}"
        return ""

    def _preferred_resource_types(self, params: dict[str, Any]) -> list[str]:
        values: list[Any] = []
        values.extend(self._raw_resource_types(params))
        profile = self._profile_context(params)
        profile_model = self._safe_profile_model(profile)
        if profile_model is not None:
            values.extend(profile_model.preferred_resource_types)
        diagnosis = params.get("masteryDiagnosis")
        if isinstance(diagnosis, dict):
            for item in diagnosis.get("knowledgeDiagnoses", []):
                if isinstance(item, dict) and isinstance(item.get("recommendedResourceTypes"), list):
                    values.extend(item["recommendedResourceTypes"])
        return self._unique(values)

    @staticmethod
    def _safe_profile_model(profile: dict[str, Any]) -> LearnerProfileDimensions | None:
        try:
            return LearnerProfileDimensions.model_validate(profile)
        except Exception:
            return None

    @staticmethod
    def _raw_resource_types(params: dict[str, Any]) -> list[Any]:
        values: list[Any] = []
        raw_types = params.get(PlanningParamKeys.RESOURCE_TYPES)
        if isinstance(raw_types, list):
            values.extend(raw_types)
        raw_type = params.get(PlanningParamKeys.RESOURCE_TYPE)
        if raw_type:
            values.append(raw_type)
        return values

    def _rank_resource_types(self, candidates: list[str], preferred: list[str]) -> list[str]:
        preferred_order = [item for item in preferred if item in candidates]
        return self._unique([*preferred_order, *candidates])

    @staticmethod
    def _unique(values: list[Any]) -> list[str]:
        result: list[str] = []
        for value in values:
            text = str(value).strip().upper()
            if not text:
                continue
            normalized = ResourceBundleWorkflow.normalize_resource_type(text)
            if normalized in RESOURCE_AGENT_BY_TYPE and normalized not in result:
                result.append(normalized)
        return result


class GoalLoopPlanner:
    """Decompose a learning goal into bounded subgoals from configured templates."""

    def __init__(self) -> None:
        self.templates = [item for item in _rules_list(("goalLoop", "subgoalTemplates")) if isinstance(item, dict)]
        self.min_profile_dimensions = _rules_int(("profileCompleteness", "minDimensionCount"), 6)
        self.min_resource_types = _rules_int(("resourceCoverage", "minTypeCount"), 5)

    def decompose(self, *, params: dict[str, Any]) -> list[dict[str, Any]]:
        goal = self.goal_text(params)
        focus = self._focus_points(params) or [goal]
        preferred = self._preferred_resource_types(params)
        subgoals: list[dict[str, Any]] = []
        for index, template in enumerate(self.templates, start=1):
            configured_types = template.get("resourceTypes")
            resource_types = self._normalize_resource_types(configured_types if isinstance(configured_types, list) else [])
            subgoals.append(
                {
                    "orderIndex": index,
                    "title": str(template.get("title") or f"Goal {index}"),
                    "objective": self._format_template(str(template.get("objectiveTemplate") or ""), goal=goal, focus=focus[0]),
                    "successCriteria": self._format_template(str(template.get("successCriteria") or ""), goal=goal, focus=focus[0]),
                    "targetKnowledgePoints": focus[: self.min_resource_types],
                    PlanningParamKeys.PREFERRED_RESOURCE_TYPES: resource_types or preferred[: self.min_resource_types],
                    "assignedPreset": str(template.get("assignedPreset") or PRESET_PERSONALIZED_LEARNING_WORKFLOW),
                    "status": "PENDING",
                }
            )
        return subgoals

    def goal_text(self, params: dict[str, Any]) -> str:
        for key in PlanningParamKeys.GOAL_QUERY:
            value = params.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        messages = params.get("messages")
        if isinstance(messages, list):
            for item in reversed(messages):
                if isinstance(item, dict) and item.get("role") == "user":
                    content = item.get("content")
                    if isinstance(content, str) and content.strip():
                        return content.strip()
        return "personalized learning goal"

    def _focus_points(self, params: dict[str, Any]) -> list[str]:
        values: list[Any] = []
        diagnosis = params.get("masteryDiagnosis")
        if isinstance(diagnosis, dict):
            for item in diagnosis.get("knowledgeDiagnoses", []):
                if isinstance(item, dict):
                    values.extend([item.get("knowledgePoint"), item.get("nextFocus")])
            target_scope = diagnosis.get("targetScope")
            if isinstance(target_scope, dict):
                values.extend(target_scope.get("knowledgePoints") or [])
        profile = PlanningCheckpointManager()._profile_context(params)
        profile_model = PlanningCheckpointManager._safe_profile_model(profile)
        if profile_model is not None:
            values.extend(profile_model.weak_points)
            values.extend(profile_model.skill_mastery.keys())
            if profile_model.learning_goal:
                values.append(profile_model.learning_goal)
        key_points = params.get("keyPoints")
        if isinstance(key_points, str):
            values.extend(re.split(r"[,，、\s]+", key_points))
        elif isinstance(key_points, list):
            values.extend(key_points)
        topic = params.get("topic")
        if isinstance(topic, str):
            values.append(topic)
        return self._unique_text(values)[: max(self.min_resource_types, 6)]

    def _preferred_resource_types(self, params: dict[str, Any]) -> list[str]:
        values = PlanningCheckpointManager._raw_resource_types(params)
        profile = PlanningCheckpointManager()._profile_context(params)
        profile_model = PlanningCheckpointManager._safe_profile_model(profile)
        if profile_model is not None:
            values.extend(profile_model.preferred_resource_types)
        normalized = self._normalize_resource_types(values)
        return normalized or list(RESOURCE_TYPE_ORDER[: self.min_resource_types])

    def _format_template(self, template: str, *, goal: str, focus: str) -> str:
        if not template.strip():
            return goal
        try:
            return template.format(
                goal=goal,
                focus=focus,
                minProfileDimensions=self.min_profile_dimensions,
                minResourceTypes=self.min_resource_types,
            )
        except Exception:
            return template

    @staticmethod
    def _normalize_resource_types(values: list[Any]) -> list[str]:
        result: list[str] = []
        for value in values:
            text = str(value).strip().upper()
            normalized = ResourceBundleWorkflow.normalize_resource_type(text)
            if normalized in RESOURCE_AGENT_BY_TYPE and normalized not in result:
                result.append(normalized)
        return result

    @staticmethod
    def _unique_text(values: list[Any]) -> list[str]:
        result: list[str] = []
        for value in values:
            text = str(value or "").strip()
            if text and text not in result:
                result.append(text)
        return result


class GoalCritic:
    """Heuristic goal critic that uses existing structured outputs."""

    def __init__(self) -> None:
        self.min_score = _rules_float(("critic", "minScore"), 0.8)
        self.min_mastery = _rules_float(("critic", "minMastery"), 0.6)
        self.passing_verdicts = {
            str(item).strip().upper()
            for item in _rules_list(("critic", "passingVerdicts"), ["PASS", "PASSED", "GOOD", "APPROVED", "OK", "SUCCESS"])
        }

    def verdict(self, *, params: dict[str, Any]) -> dict[str, Any]:
        review = params.get("criticReview") if isinstance(params.get("criticReview"), dict) else {}
        issues: list[str] = []
        status = "ACHIEVED"
        for key in ("coverageScore", "resourceMatchScore", "pathOrderScore"):
            value = review.get(key)
            try:
                if value is not None and float(value) < self.min_score:
                    issues.append(f"{key}={float(value):.2f}")
            except (TypeError, ValueError):
                continue
        verdict = str(review.get("verdict") or "").strip().upper()
        if verdict and verdict not in self.passing_verdicts:
            issues.append(f"criticVerdict={verdict}")
        diagnosis = params.get("masteryDiagnosis") if isinstance(params.get("masteryDiagnosis"), dict) else {}
        try:
            mastery = float(diagnosis.get("overallMasteryScore"))
            if mastery < self.min_mastery:
                issues.append(f"mastery={mastery:.2f}")
        except (TypeError, ValueError):
            pass
        resource_gap = params.get(PlanningParamKeys.RESOURCE_COVERAGE_GAP)
        if isinstance(resource_gap, dict) and resource_gap.get("missingTypes"):
            missing_types = [
                str(item)
                for item in resource_gap.get("missingTypes", [])
                if str(item).strip()
            ]
            if missing_types:
                issues.append(f"resourceCoverageMissing={','.join(missing_types)}")
        if issues:
            status = "NEEDS_REPLAN"
        if params.get(PlanningParamKeys.PROFILE_COMPLETENESS) == "LOW" and not params.get("learningPath"):
            status = "BLOCKED"
            issues.append("profile_incomplete_without_learning_path")
        return {
            "status": status,
            "issues": issues,
            "summary": "goal_achieved" if status == "ACHIEVED" else "local_replan_required",
        }


class LearningLoopOrchestrator:
    """Create, persist, and close a Level 3 learning loop around existing agents."""

    def __init__(
        self,
        *,
        store: ResilientLearningLoopStore | None = None,
        planner: GoalLoopPlanner | None = None,
        critic: GoalCritic | None = None,
    ) -> None:
        self.store = store or ResilientLearningLoopStore()
        self.planner = planner or GoalLoopPlanner()
        self.critic = critic or GoalCritic()

    async def start_loop(
        self,
        *,
        params: dict[str, Any],
        user_id: str,
        task_id: str,
        conversation_id: str | None,
    ) -> dict[str, Any]:
        goal_text = self.planner.goal_text(params)
        course_id = self._course_id(params)
        subgoals = self.planner.decompose(params=params)
        loop_record = await self.store.create_loop(
            user_id=user_id,
            goal_text=goal_text,
            course_id=course_id,
            task_id=task_id,
            conversation_id=conversation_id,
            planning_level="goal_loop",
            loop_payload={"subgoals": subgoals},
        )
        subgoal_records = await self.store.create_subgoals(
            loop_id=str(loop_record["loopId"]),
            user_id=user_id,
            subgoals=subgoals,
        )
        loop_payload = {
            **loop_record,
            "goals": subgoal_records,
            "currentGoalIndex": 1,
            "verdicts": [],
            "replans": [],
            "completedGoals": [],
        }
        params[PlanningParamKeys.LEARNING_LOOP] = loop_payload
        params.setdefault(PlanningParamKeys.PLANNING_TRACE, []).append(
            {
                "agentName": "goal_planner",
                "status": "DONE",
                "planningLevel": "goal_loop",
                "goalCount": len(subgoal_records),
                "reason": goal_text,
            }
        )
        return loop_payload

    async def close_loop(
        self,
        *,
        params: dict[str, Any],
        user_id: str,
        allow_replan: bool = True,
    ) -> dict[str, Any]:
        loop = params.get(PlanningParamKeys.LEARNING_LOOP)
        if not isinstance(loop, dict) or not loop.get("loopId"):
            return {}
        verdict = self.critic.verdict(params=params)
        loop.setdefault("verdicts", []).append(verdict)
        final_status = "COMPLETED" if verdict["status"] == "ACHIEVED" else "PARTIAL_FAILED"
        if verdict["status"] == "BLOCKED":
            final_status = "BLOCKED"
        if verdict["status"] == "NEEDS_REPLAN" and allow_replan:
            replan = await self._record_local_replan(loop=loop, params=params, user_id=user_id, verdict=verdict)
            loop.setdefault("replans", []).append(replan)
        await self._update_subgoals(loop=loop, verdict=verdict, user_id=user_id)
        completed = [
            goal
            for goal in loop.get("goals", [])
            if isinstance(goal, dict) and goal.get("status") == "ACHIEVED"
        ]
        loop["completedGoals"] = [goal.get("subgoalId") for goal in completed if goal.get("subgoalId")]
        loop["status"] = final_status
        await self.store.update_loop(
            user_id=user_id,
            loop_id=str(loop["loopId"]),
            status=final_status,
            current_subgoal_order=int(loop.get("currentGoalIndex") or 1),
            loop_payload=loop,
        )
        params[PlanningParamKeys.LEARNING_LOOP] = loop
        params.setdefault(PlanningParamKeys.PLANNING_TRACE, []).append(
            {
                "agentName": "goal_critic",
                "status": final_status,
                "planningLevel": "goal_loop",
                "reason": verdict.get("summary"),
                "issues": verdict.get("issues", []),
            }
        )
        await self.store.record_checkpoint(
            user_id=user_id,
            loop_id=str(loop["loopId"]),
            checkpoint_type=CHECKPOINT_GOAL_CRITIC,
            trigger_reason=";".join(verdict.get("issues", [])) or "goal_achieved",
            action="close_goal_loop" if final_status == "COMPLETED" else "local_replan_or_partial_fail",
            status="APPLIED",
            before_payload={"criticReview": params.get("criticReview")},
            after_payload={"learningLoopStatus": final_status, "verdict": verdict},
        )
        self._attach_loop_summary(params, loop=loop, status=final_status)
        return loop

    async def _update_subgoals(self, *, loop: dict[str, Any], verdict: dict[str, Any], user_id: str) -> None:
        goals = loop.get("goals")
        if not isinstance(goals, list):
            return
        current_index = max(1, int(loop.get("currentGoalIndex") or 1))
        for index, goal in enumerate(goals):
            if not isinstance(goal, dict):
                continue
            existing_status = str(goal.get("status") or "PENDING")
            if verdict.get("status") == "ACHIEVED":
                status = "ACHIEVED"
            elif index + 1 == current_index:
                status = "NEEDS_REPLAN"
            else:
                status = existing_status
            goal["status"] = status
            subgoal_id = goal.get("subgoalId")
            if subgoal_id:
                await self.store.update_subgoal(
                    user_id=user_id,
                    subgoal_id=str(subgoal_id),
                    status=status,
                    result_payload={"verdict": verdict},
                    attempt_count=int(goal.get("attemptCount") or 0) + 1,
                )

    async def _record_local_replan(
        self,
        *,
        loop: dict[str, Any],
        params: dict[str, Any],
        user_id: str,
        verdict: dict[str, Any],
    ) -> dict[str, Any]:
        old_plan = params.get("learningPath") if isinstance(params.get("learningPath"), dict) else {}
        new_plan = dict(old_plan)
        if new_plan:
            previous_summary = str(new_plan.get("summaryText") or "").strip()
            issue_text = "; ".join(str(item) for item in verdict.get("issues", []))
            suffix = f"Level 3 local replan suggested by goal critic: {issue_text}".strip()
            new_plan["summaryText"] = f"{previous_summary}\n{suffix}".strip() if previous_summary else suffix
        replan = await self.store.record_replan(
            loop_id=str(loop["loopId"]),
            user_id=user_id,
            reason=";".join(verdict.get("issues", [])) or "goal_critic_replan",
            old_plan=old_plan,
            new_plan=new_plan,
            attempt_no=len(loop.get("replans", [])) + 1,
            accepted=True,
        )
        if new_plan:
            params["learningPath"] = new_plan
        return replan

    def _attach_loop_summary(self, params: dict[str, Any], *, loop: dict[str, Any], status: str) -> None:
        learning_path = params.get("learningPath")
        if not isinstance(learning_path, dict):
            return
        summary = str(learning_path.get("summaryText") or "").strip()
        suffix = f"Level 3 learning loop status={status}; subgoals={len(loop.get('goals', []))}"
        learning_path["summaryText"] = f"{summary}\n{suffix}".strip() if summary else suffix

    def _course_id(self, params: dict[str, Any]) -> str | None:
        for key in ("courseId", "course_id"):
            value = params.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None
