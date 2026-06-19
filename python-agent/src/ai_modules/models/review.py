"""Structured models for critic and safety review agents."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _normalize_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = value.strip()
        return [value] if value else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, dict):
        return [str(item).strip() for item in value.values() if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


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

    @field_validator("issues", "suggestions", mode="before")
    @classmethod
    def _coerce_text_lists(cls, value: Any) -> list[str]:
        return _normalize_text_list(value)


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

    @field_validator("categories", "risk_tags", "suggestions", mode="before")
    @classmethod
    def _coerce_text_lists(cls, value: Any) -> list[str]:
        return _normalize_text_list(value)
