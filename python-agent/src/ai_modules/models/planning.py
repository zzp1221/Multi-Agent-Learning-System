"""Structured models for evaluation and learning-path planning."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class EvaluationDimension(BaseModel):
    """A single evaluated learner dimension."""

    name: str
    level: str
    evidence: str
    recommendation: str


class EvaluationPayload(BaseModel):
    """Structured evaluation summary returned by the Evaluation Agent."""

    overall_level: str = Field(alias="overallLevel")
    strengths: list[str]
    weaknesses: list[str]
    next_focus: list[str] = Field(alias="nextFocus")
    dimensions: list[EvaluationDimension]
    summary_text: str = Field(alias="summaryText")

    model_config = ConfigDict(populate_by_name=True)


class LearningPlanStep(BaseModel):
    """A single actionable step in the learning plan."""

    title: str
    objective: str
    activities: list[str]
    success_criteria: str = Field(alias="successCriteria")
    step_id: str | None = Field(default=None, alias="stepId")
    order: int | None = None
    target_knowledge_points: list[str] = Field(default_factory=list, alias="targetKnowledgePoints")
    reason: str | None = None
    preferred_resource_types: list[str] = Field(default_factory=list, alias="preferredResourceTypes")
    estimated_minutes: int | None = Field(default=None, alias="estimatedMinutes")
    checkpoint: str | None = None

    model_config = ConfigDict(populate_by_name=True)


class LearningPlanPayload(BaseModel):
    """Structured learning path returned by the PathPlanning Agent."""

    goal: str
    duration: str
    milestones: list[str]
    steps: list[LearningPlanStep]
    summary_text: str = Field(alias="summaryText")

    model_config = ConfigDict(populate_by_name=True)
