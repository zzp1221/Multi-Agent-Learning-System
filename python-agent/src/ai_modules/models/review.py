"""Structured models for critic and safety review agents."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CriticReviewPayload(BaseModel):
    """Structured output returned by the Critic Agent."""

    verdict: str
    fact_consistency: str = Field(alias="factConsistency")
    difficulty_match: str = Field(alias="difficultyMatch")
    source_coverage: str = Field(alias="sourceCoverage")
    issues: list[str]
    suggestions: list[str]
    summary_text: str = Field(alias="summaryText")
    coverage_score: float | None = Field(default=None, alias="coverageScore")
    path_order_score: float | None = Field(default=None, alias="pathOrderScore")
    resource_match_score: float | None = Field(default=None, alias="resourceMatchScore")

    model_config = ConfigDict(populate_by_name=True)


class SafetyReviewPayload(BaseModel):
    """Structured output returned by the Safety Agent."""

    allowed: bool
    risk_level: str = Field(alias="riskLevel")
    categories: list[str]
    risk_tags: list[str] = Field(alias="riskTags")
    blocked_reason: str | None = Field(default=None, alias="blockedReason")
    suggestions: list[str]
    summary_text: str = Field(alias="summaryText")

    model_config = ConfigDict(populate_by_name=True)
