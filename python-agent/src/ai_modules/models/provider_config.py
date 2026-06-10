"""Provider and logical-model routing configuration models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


StructuredOutputMode = Literal["json_object", "json_schema", "none"]


class ProviderEndpointConfig(BaseModel):
    """Connection and model mapping metadata for a single provider."""

    name: str
    protocol: str
    base_url: str = Field(alias="baseUrl")
    api_key_env: str = Field(alias="apiKeyEnv")
    timeout_ms: int = Field(default=60000, alias="timeoutMs")
    app_id_env: str | None = Field(default=None, alias="appIdEnv")
    api_secret_env: str | None = Field(default=None, alias="apiSecretEnv")
    structured_output_mode: StructuredOutputMode = Field(default="json_object", alias="structuredOutputMode")
    models: dict[str, str] = Field(default_factory=dict)

    model_config = ConfigDict(populate_by_name=True)


class ModelRoutingConfig(BaseModel):
    """Routing configuration across active/fallback providers."""

    active_provider: str = Field(alias="activeProvider")
    fallback_provider: str | None = Field(default=None, alias="fallbackProvider")
    tts_provider: str | None = Field(default=None, alias="ttsProvider")
    avatar_provider: str | None = Field(default=None, alias="avatarProvider")
    providers: dict[str, ProviderEndpointConfig]

    model_config = ConfigDict(populate_by_name=True)

    def resolve_model(
        self,
        logical_model_name: str,
        provider_name: str | None = None,
    ) -> str:
        provider_key = provider_name or self.active_provider
        provider = self.providers[provider_key]
        if logical_model_name not in provider.models:
            raise KeyError(f"unknown logical model: {logical_model_name}")
        return provider.models[logical_model_name]
