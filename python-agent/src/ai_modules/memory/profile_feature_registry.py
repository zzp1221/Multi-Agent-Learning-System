"""Learner profile feature dimension registry."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FeatureDimensionSpec:
    dimension: str
    stability_period_days: int
    decay_rate: float
    exclusive: bool = False
    canonical_match: bool = False
    resolved_by_skill_mastery: bool = False


FEATURE_DIMENSION_SPECS: tuple[FeatureDimensionSpec, ...] = (
    FeatureDimensionSpec("knowledge_foundation", 45, 0.02, exclusive=True),
    FeatureDimensionSpec("professional_background", 90, 0.01, exclusive=True),
    FeatureDimensionSpec("learning_preference", 35, 0.03, exclusive=True),
    FeatureDimensionSpec("cognitive_style", 45, 0.03, exclusive=True),
    FeatureDimensionSpec("learning_pace", 20, 0.05, exclusive=True),
    FeatureDimensionSpec("confidence_level", 20, 0.05, exclusive=True),
    FeatureDimensionSpec("current_goal", 18, 0.06, exclusive=True),
    FeatureDimensionSpec("learning_habits", 14, 0.07, exclusive=True),
    FeatureDimensionSpec("explanation_preference", 30, 0.03, exclusive=True),
    FeatureDimensionSpec("skill_mastery", 21, 0.04, canonical_match=True),
    FeatureDimensionSpec("weak_points", 20, 0.05, canonical_match=True, resolved_by_skill_mastery=True),
    FeatureDimensionSpec("error_patterns", 18, 0.06, canonical_match=True),
    FeatureDimensionSpec("preferred_resource_type", 35, 0.03),
    FeatureDimensionSpec("inferred_recommendation", 10, 0.08),
)

FEATURE_DIMENSION_REGISTRY = {spec.dimension: spec for spec in FEATURE_DIMENSION_SPECS}


def get_feature_dimension_spec(dimension: str) -> FeatureDimensionSpec:
    return FEATURE_DIMENSION_REGISTRY.get(dimension, FeatureDimensionSpec(dimension, 30, 0.05))


def exclusive_dimensions() -> set[str]:
    return {spec.dimension for spec in FEATURE_DIMENSION_SPECS if spec.exclusive}


def canonical_match_dimensions() -> set[str]:
    return {spec.dimension for spec in FEATURE_DIMENSION_SPECS if spec.canonical_match}


def resolved_by_skill_mastery_dimensions() -> set[str]:
    return {spec.dimension for spec in FEATURE_DIMENSION_SPECS if spec.resolved_by_skill_mastery}
