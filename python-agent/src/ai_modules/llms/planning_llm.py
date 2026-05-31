"""LLM adapter factory for planning-capable agents."""

from __future__ import annotations

from typing import Any

from src.ai_modules.config import get_settings
from src.ai_modules.llms.agent_models import create_tool_calling_llm


class PlanningLLMClientFactory:
    """Create a real planning LLM client; fail when no provider is available."""

    @staticmethod
    def create() -> Any:
        settings = get_settings()
        provider_name = settings.resolve_component_provider("planning_llm")
        if settings.provider_ready(provider_name):
            model_name = settings.resolve_component_model(
                "planning_llm",
                default_logical_model="reasoning_model",
                provider_name=provider_name,
            )
            return create_tool_calling_llm(model_name=model_name, provider_name=provider_name)
        raise RuntimeError("planning_llm provider is not ready; planning fallback is disabled")
