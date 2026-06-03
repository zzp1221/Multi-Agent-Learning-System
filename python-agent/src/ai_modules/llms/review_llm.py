"""LLM adapters for critic and safety review agents."""

from __future__ import annotations

import json
from typing import Any

from src.ai_modules.config import get_settings
from src.ai_modules.llms.agent_models import OpenAICompatibleJSONGenerator, create_tool_calling_llm
from src.ai_modules.llms.json_utils import dumps_json
from src.ai_modules.models import CriticReviewPayload, SafetyReviewPayload


class OpenAICompatibleCriticReviewer:
    """Generate structured critic reviews through an active LLM provider."""

    def __init__(self) -> None:
        settings = get_settings()
        provider_name = settings.resolve_component_provider("review_llm")
        if not settings.provider_ready(provider_name):
            raise RuntimeError("review_llm provider is not ready")
        model_name = settings.resolve_component_model(
            "review_llm",
            default_logical_model="main_chat_model",
            provider_name=provider_name,
        )
        self.generator = OpenAICompatibleJSONGenerator(
            model_name=model_name,
            provider_name=provider_name,
            temperature=0.1,
        )

    async def review(
        self,
        *,
        system_prompt: str,
        context_payload: dict[str, Any],
    ) -> CriticReviewPayload:
        payload = await self.generator.generate(
            system_prompt=system_prompt,
            user_prompt=(
                "Generate a structured Critic review for the following educational artifact. "
                "Return JSON only.\n"
                f"{dumps_json(context_payload, ensure_ascii=False)}"
            ),
            max_tokens=1200,
        )
        payload = _normalize_critic_payload(payload)
        return CriticReviewPayload.model_validate(payload)


class OpenAICompatibleSafetyReviewer:
    """Generate structured safety reviews through an active LLM provider."""

    def __init__(self) -> None:
        settings = get_settings()
        provider_name = settings.resolve_component_provider("safety_llm")
        if not settings.provider_ready(provider_name):
            raise RuntimeError("safety_llm provider is not ready")
        model_name = settings.resolve_component_model(
            "safety_llm",
            default_logical_model="safety_model",
            provider_name=provider_name,
        )
        self.generator = OpenAICompatibleJSONGenerator(
            model_name=model_name,
            provider_name=provider_name,
            temperature=0.1,
        )

    async def review(
        self,
        *,
        system_prompt: str,
        context_payload: dict[str, Any],
    ) -> SafetyReviewPayload:
        payload = await self.generator.generate(
            system_prompt=system_prompt,
            user_prompt=(
                "Generate a structured Safety review for the following educational artifact. "
                "Return JSON only.\n"
                f"{dumps_json(context_payload, ensure_ascii=False)}"
            ),
            max_tokens=1200,
        )
        return SafetyReviewPayload.model_validate(payload)


def _normalize_critic_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    for field in ("factConsistency", "difficultyMatch", "sourceCoverage"):
        value = normalized.get(field)
        if isinstance(value, dict):
            normalized[field] = _stringify_review_signal(value)
    for field in ("coverageScore", "pathOrderScore", "resourceMatchScore"):
        score = _normalize_score(normalized.get(field))
        if score is not None:
            normalized[field] = score
    return normalized


def _stringify_review_signal(value: dict[str, Any]) -> str:
    parts: list[str] = []
    status = value.get("status")
    if isinstance(status, str) and status.strip():
        parts.append(f"status: {status.strip()}")
    issues = value.get("issues")
    if isinstance(issues, list):
        normalized_issues = [str(item).strip() for item in issues if str(item).strip()]
        if normalized_issues:
            parts.append(f"issues: {'; '.join(normalized_issues[:3])}")
    evidence = value.get("evidence")
    if isinstance(evidence, dict):
        evidence_parts = [f"{key}={evidence[key]}" for key in evidence if evidence[key] is not None]
        if evidence_parts:
            parts.append(f"evidence: {', '.join(evidence_parts[:4])}")
    if not parts:
        return dumps_json(value, ensure_ascii=False)
    return "; ".join(parts)


def _normalize_score(value: Any) -> float | None:
    if value is None:
        return None
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if score > 1:
        score = score / 100
    return max(0.0, min(score, 1.0))


class ReviewLLMClientFactory:
    """Create a real review LLM client; fail when no provider is available."""

    @staticmethod
    def create() -> Any:
        settings = get_settings()
        provider_name = settings.resolve_component_provider("review_llm")
        if settings.provider_ready(provider_name):
            model_name = settings.resolve_component_model(
                "review_llm",
                default_logical_model="reasoning_model",
                provider_name=provider_name,
            )
            return create_tool_calling_llm(model_name=model_name, provider_name=provider_name)
        raise RuntimeError("review_llm provider is not ready; review fallback is disabled")


CriticReviewer = OpenAICompatibleCriticReviewer
SafetyReviewer = OpenAICompatibleSafetyReviewer

BailianCriticReviewer = OpenAICompatibleCriticReviewer
BailianSafetyReviewer = OpenAICompatibleSafetyReviewer
