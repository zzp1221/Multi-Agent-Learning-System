"""Structured models for critic and safety review agents."""

from __future__ import annotations

from typing import Any

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator


PASSING_CRITIC_VERDICTS = {"PASS", "PASSED", "GOOD", "APPROVED", "OK", "SUCCESS"}
SOFT_PASSING_CRITIC_VERDICTS = {
    "PASS_WITH_ISSUES",
    "NEEDS_MINOR_REVISION",
    "MINOR_ISSUES",
}
PUBLISHABLE_CRITIC_VERDICTS = PASSING_CRITIC_VERDICTS | SOFT_PASSING_CRITIC_VERDICTS

CRITIC_VERDICT_ALIASES = {
    "PASS WITH ISSUES": "PASS_WITH_ISSUES",
    "PASS-WITH-ISSUES": "PASS_WITH_ISSUES",
    "PASSED WITH ISSUES": "PASS_WITH_ISSUES",
    "PASSED-WITH-ISSUES": "PASS_WITH_ISSUES",
    "NEEDS MINOR REVISION": "NEEDS_MINOR_REVISION",
    "NEEDS-MINOR-REVISION": "NEEDS_MINOR_REVISION",
    "MINOR ISSUE": "MINOR_ISSUES",
    "MINOR ISSUES": "MINOR_ISSUES",
}


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


def normalize_critic_verdict(value: Any) -> str:
    verdict = str(value or "").strip().upper()
    if not verdict:
        return ""
    verdict = CRITIC_VERDICT_ALIASES.get(verdict, verdict)
    return re.sub(r"[\s-]+", "_", verdict)


def is_publishable_critic_verdict(value: Any) -> bool:
    verdict = normalize_critic_verdict(value)
    return not verdict or verdict in PUBLISHABLE_CRITIC_VERDICTS


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

    @field_validator("verdict", mode="before")
    @classmethod
    def _normalize_verdict(cls, value: Any) -> str:
        return normalize_critic_verdict(value)


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
