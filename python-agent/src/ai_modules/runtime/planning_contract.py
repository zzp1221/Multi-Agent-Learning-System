"""Shared parameter contract keys used by routing, tutoring, and planning."""

from __future__ import annotations

from src.ai_modules.models.profile import LearnerProfileDimensions


def profile_alias(field_name: str) -> str:
    field = LearnerProfileDimensions.model_fields.get(field_name)
    return str(field.alias or field_name) if field is not None else field_name


class PlanningParamKeys:
    """Centralized request/payload keys consumed by autonomous planning."""

    GOAL_QUERY = ("goal", profile_alias("learning_goal"), "topic", "query", "message", "userInput", "question", "prompt")
    PROFILE_CONTEXTS = ("profile", "profileAnalysis", "analyzedProfileDimensions", "currentProfile")
    IMAGE_INPUTS = ("imageUrls", "images", "imageFiles", "attachments")
    RESOURCE_TYPES = "resourceTypes"
    RESOURCE_TYPE = "resourceType"
    PREFERRED_RESOURCE_TYPES = profile_alias("preferred_resource_types")
    CHECKPOINT_ACTIONS = "checkpointActions"
    PLANNING_TRACE = "planningTrace"
    PROFILE_COMPLETENESS = "profileCompleteness"
    PROFILE_MISSING_DIMENSIONS = "profileMissingDimensions"
    RETRIEVAL_STRATEGY = "retrievalStrategy"
    WEB_SEARCH_ENABLED = "webSearchEnabled"
    RESOURCE_COVERAGE_GAP = "resourceCoverageGap"
    RESOURCE_COVERAGE_SUPPLEMENT_TYPES = "resourceCoverageSupplementTypes"
    RESOURCE_COVERAGE_STATUS = "resourceCoverageStatus"
    LEARNING_LOOP = "learningLoop"
    CONVERSATION_TRIGGERED_RESOURCE_GENERATION = "conversationTriggeredResourceGeneration"
    CHECKPOINT_RETRIEVAL_RERUN_COUNT = "_checkpointRetrievalRerunCount"
    CHECKPOINT_RESOURCE_COVERAGE_RERUN_DONE = "_checkpointResourceCoverageRerunDone"
