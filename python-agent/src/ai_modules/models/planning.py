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


class DiagnosisTargetScope(BaseModel):
    """Learning scope covered by a mastery diagnosis."""

    course: str | None = None
    chapter: str | None = None
    knowledge_points: list[str] = Field(default_factory=list, alias="knowledgePoints")

    model_config = ConfigDict(populate_by_name=True)


class KnowledgeDiagnosis(BaseModel):
    """Mastery diagnosis for one knowledge point."""

    knowledge_point: str = Field(alias="knowledgePoint")
    mastery_score: float = Field(alias="masteryScore")
    status: str
    priority: int
    evidence: list[str] = Field(default_factory=list)
    error_patterns: list[str] = Field(default_factory=list, alias="errorPatterns")
    next_focus: str = Field(default="", alias="nextFocus")
    recommended_resource_types: list[str] = Field(default_factory=list, alias="recommendedResourceTypes")

    model_config = ConfigDict(populate_by_name=True)


class DiagnosisBehaviorSignals(BaseModel):
    """Aggregated behavior signals used by a mastery diagnosis."""

    practice_accuracy: float | None = Field(default=None, alias="practiceAccuracy")
    recent_question_count: int = Field(default=0, alias="recentQuestionCount")
    review_count: int = Field(default=0, alias="reviewCount")
    resource_downloads: int = Field(default=0, alias="resourceDownloads")
    message_count: int = Field(default=0, alias="messageCount")
    recent_mistake_count: int = Field(default=0, alias="recentMistakeCount")

    model_config = ConfigDict(populate_by_name=True)


class PlanAdjustmentHints(BaseModel):
    """Non-mutating hints for downstream path planning and resource push."""

    should_refresh_plan: bool = Field(alias="shouldRefreshPlan")
    refresh_reason: str = Field(default="", alias="refreshReason")
    strategy: str = ""

    model_config = ConfigDict(populate_by_name=True)


class MasteryDiagnosisPayload(BaseModel):
    """Structured mastery diagnosis shared by personalized learning agents."""

    diagnosis_source: str = Field(alias="diagnosisSource")
    primary_dimension: str | None = Field(default=None, alias="primaryDimension")
    overall_level: str = Field(alias="overallLevel")
    overall_mastery_score: float = Field(alias="overallMasteryScore")
    confidence: float
    target_scope: DiagnosisTargetScope = Field(alias="targetScope")
    knowledge_diagnoses: list[KnowledgeDiagnosis] = Field(default_factory=list, alias="knowledgeDiagnoses")
    behavior_signals: DiagnosisBehaviorSignals = Field(alias="behaviorSignals")
    plan_adjustment_hints: PlanAdjustmentHints = Field(alias="planAdjustmentHints")
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
