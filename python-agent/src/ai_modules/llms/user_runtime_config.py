"""Request-scoped user LLM runtime configuration."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from contextvars import ContextVar
from time import monotonic
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from src.ai_modules.config import Settings
from src.ai_modules.models import ModelRoutingConfig, ProviderEndpointConfig, ReasoningStreamConfig

LOGGER = logging.getLogger(__name__)

_CURRENT_CONFIG: ContextVar["UserLlmRuntimeConfig | None"] = ContextVar(
    "current_user_llm_runtime_config",
    default=None,
)
_USER_CONTEXT_ACTIVE: ContextVar[bool] = ContextVar("user_llm_runtime_context_active", default=False)
_CACHE: dict[str, tuple[float, "UserLlmRuntimeConfig"]] = {}
_LOCK = asyncio.Lock()
_REASONING_STREAM_FIELDS = ["reasoning_content", "reasoning", "reasoningContent"]
_THINKING_ENABLED_REQUEST = {"thinking": {"type": "enabled"}}

PROVIDER_DEFAULTS: dict[str, dict[str, Any]] = {
    "openai": {
        "protocol": "openai_compatible",
        "baseUrl": "https://api.openai.com/v1",
        "structuredOutputMode": "json_schema",
        "models": {
            "main_chat_model": "gpt-4.1-mini",
            "fast_model": "gpt-4.1-mini",
            "reasoning_model": "o4-mini",
            "code_model": "gpt-4.1",
            "code_fast_model": "gpt-4.1-mini",
            "omni_model": "gpt-4.1-mini",
            "omni_realtime_model": "gpt-4o-realtime-preview",
            "safety_model": "gpt-4.1-mini",
        },
    },
    "dashscope": {
        "protocol": "openai_compatible",
        "baseUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": {
            "main_chat_model": "qwen-plus",
            "fast_model": "qwen-turbo",
            "reasoning_model": "qwen-max",
            "code_model": "qwen-coder-plus",
            "code_fast_model": "qwen-coder-turbo",
            "omni_model": "qwen-omni-turbo",
            "omni_realtime_model": "qwen-omni-turbo-realtime",
            "safety_model": "qwen-turbo",
        },
    },
    "deepseek": {
        "protocol": "openai_compatible",
        "baseUrl": "https://api.deepseek.com",
        "models": {
            "main_chat_model": "deepseek-chat",
            "fast_model": "deepseek-chat",
            "reasoning_model": "deepseek-reasoner",
            "code_model": "deepseek-chat",
            "code_fast_model": "deepseek-chat",
            "omni_model": "deepseek-chat",
            "omni_realtime_model": "deepseek-chat",
            "safety_model": "deepseek-chat",
        },
    },
    "moonshot": {
        "protocol": "openai_compatible",
        "baseUrl": "https://api.moonshot.cn/v1",
        "models": {
            "main_chat_model": "moonshot-v1-32k",
            "fast_model": "moonshot-v1-8k",
            "reasoning_model": "moonshot-v1-32k",
            "code_model": "moonshot-v1-32k",
            "code_fast_model": "moonshot-v1-8k",
            "omni_model": "moonshot-v1-32k",
            "omni_realtime_model": "moonshot-v1-32k",
            "safety_model": "moonshot-v1-8k",
        },
    },
    "zhipu": {
        "protocol": "openai_compatible",
        "baseUrl": "https://open.bigmodel.cn/api/paas/v4",
        "models": {
            "main_chat_model": "glm-4-plus",
            "fast_model": "glm-4-flash",
            "reasoning_model": "glm-4-plus",
            "code_model": "glm-4-plus",
            "code_fast_model": "glm-4-flash",
            "omni_model": "glm-4-plus",
            "omni_realtime_model": "glm-4-plus",
            "safety_model": "glm-4-flash",
        },
    },
    "spark": {
        "protocol": "spark_compatible",
        "baseUrl": "https://spark-api-open.xf-yun.com/v1",
        "models": {
            "main_chat_model": "4.0Ultra",
            "fast_model": "generalv3.5",
            "reasoning_model": "Spark X2",
            "code_model": "Spark X2-Flash",
            "code_fast_model": "Spark X2-Flash",
            "omni_model": "Spark Ultra",
            "omni_realtime_model": "Spark X2-Flash",
            "safety_model": "Spark X2-Flash",
        },
    },
    "mimo": {
        "protocol": "openai_compatible",
        "baseUrl": "https://api.xiaomimimo.com/v1",
        "models": {
            "main_chat_model": "mimo-v2-omni",
            "fast_model": "mimo-v2-flash",
            "reasoning_model": "mimo-v2-omni",
            "code_model": "mimo-v2-omni",
            "code_fast_model": "mimo-v2-flash",
            "omni_model": "mimo-v2-omni",
            "omni_realtime_model": "mimo-v2-omni",
            "safety_model": "mimo-v2-flash",
        },
    },
    "custom_openai_compatible": {
        "protocol": "openai_compatible",
        "baseUrl": "",
        "models": {
            "main_chat_model": "model",
            "fast_model": "model",
            "reasoning_model": "model",
            "code_model": "model",
            "code_fast_model": "model",
            "omni_model": "model",
            "omni_realtime_model": "model",
            "safety_model": "model",
        },
    },
}


class RuntimeProvider(BaseModel):
    provider: str = ""
    base_url: str = Field(default="", alias="baseUrl")
    api_key: str = Field(default="", alias="apiKey")
    api_secret: str = Field(default="", alias="apiSecret")
    app_id: str = Field(default="", alias="appId")
    model_overrides: dict[str, str] = Field(default_factory=dict, alias="modelOverrides")

    model_config = ConfigDict(populate_by_name=True)


class RuntimeComponentOverride(BaseModel):
    provider: str = ""
    model: str = ""


class RuntimeSkillOverride(BaseModel):
    enabled: bool = False
    name: str = ""
    description: str = ""
    body: str = ""


def _reasoning_stream_config_for_model(
    *,
    provider_name: str,
    model_name: str,
) -> ReasoningStreamConfig | None:
    normalized_model = model_name.strip()
    if not normalized_model:
        return None
    lowered_model = normalized_model.lower()
    if provider_name == "deepseek" or "deepseek-reasoner" in lowered_model:
        return ReasoningStreamConfig(
            request={},
            streamFields=_REASONING_STREAM_FIELDS,
            messageFields=_REASONING_STREAM_FIELDS,
        )
    if (
        provider_name == "mimo"
        or lowered_model.startswith("mimo-v2-omni")
        or lowered_model in {"mimo-v2.5-pro", "mimo-v2-pro"}
        or "qwen3" in lowered_model
        or "qwq" in lowered_model
    ):
        return ReasoningStreamConfig(
            request=_THINKING_ENABLED_REQUEST,
            streamFields=_REASONING_STREAM_FIELDS,
            messageFields=_REASONING_STREAM_FIELDS,
        )
    return None


class UserLlmRuntimeConfig(BaseModel):
    enabled: bool = False
    allow_environment_fallback: bool = Field(default=False, alias="allowEnvironmentFallback")
    active_provider: str = Field(default="", alias="activeProvider")
    fallback_provider: str = Field(default="", alias="fallbackProvider")
    providers: dict[str, RuntimeProvider] = Field(default_factory=dict)
    component_overrides: dict[str, RuntimeComponentOverride] = Field(default_factory=dict, alias="componentOverrides")
    skill_overrides: dict[str, RuntimeSkillOverride] = Field(default_factory=dict, alias="skillOverrides")

    model_config = ConfigDict(populate_by_name=True)

    def normalized_provider(self, provider_name: str | None) -> str:
        normalized = (provider_name or "").strip().lower()
        if normalized in {"bailian", "aliyun", "dashscope_bailian"}:
            return "dashscope"
        if normalized in {"custom", "custom_openai"}:
            return "custom_openai_compatible"
        if normalized in {"glm", "bigmodel"}:
            return "zhipu"
        return normalized

    def provider_ready(self, provider_name: str | None = None) -> bool:
        provider = self.normalized_provider(provider_name or self.active_provider)
        config = self.providers.get(provider)
        return bool(config and config.api_key.strip())

    def runtime_provider_name(self, fallback: str) -> str:
        active = self.normalized_provider(self.active_provider)
        fallback_provider = self.normalized_provider(self.fallback_provider)
        if active and self.provider_ready(active):
            return active
        if fallback_provider and self.provider_ready(fallback_provider):
            return fallback_provider
        return active or fallback_provider or fallback

    def provider_api_key(self, provider_name: str | None = None) -> str:
        provider = self.normalized_provider(provider_name or self.active_provider)
        config = self.providers.get(provider)
        return config.api_key if config else ""

    def routing_config(self, base: ModelRoutingConfig) -> ModelRoutingConfig:
        providers = dict(base.providers)
        for provider_name, runtime_provider in self.providers.items():
            normalized_name = self.normalized_provider(provider_name)
            defaults = PROVIDER_DEFAULTS.get(normalized_name, PROVIDER_DEFAULTS["custom_openai_compatible"])
            base_url = runtime_provider.base_url.strip() or str(defaults.get("baseUrl") or "")
            models = dict(defaults.get("models") or {})
            models.update({key: value for key, value in runtime_provider.model_overrides.items() if value.strip()})
            reasoning_models = self._build_reasoning_models(
                provider_name=normalized_name,
                models=models,
            )
            providers[normalized_name] = ProviderEndpointConfig.model_validate(
                {
                    "name": normalized_name,
                    "protocol": defaults.get("protocol", "openai_compatible"),
                    "baseUrl": base_url,
                    "apiKeyEnv": f"USER_LLM_{normalized_name.upper()}_API_KEY",
                    "appIdEnv": f"USER_LLM_{normalized_name.upper()}_APP_ID",
                    "apiSecretEnv": f"USER_LLM_{normalized_name.upper()}_API_SECRET",
                    "timeoutMs": 60000,
                    "structuredOutputMode": defaults.get("structuredOutputMode", "json_object"),
                    "models": models,
                    "reasoningModels": reasoning_models,
                }
            )
        active = self.normalized_provider(self.active_provider) or base.active_provider
        fallback = self.normalized_provider(self.fallback_provider) or base.fallback_provider
        return ModelRoutingConfig(
            activeProvider=active,
            fallbackProvider=fallback,
            ttsProvider=base.tts_provider,
            avatarProvider=base.avatar_provider,
            providers=providers,
        )

    def _build_reasoning_models(
        self,
        *,
        provider_name: str,
        models: dict[str, str],
    ) -> dict[str, ReasoningStreamConfig]:
        reasoning_models: dict[str, ReasoningStreamConfig] = {}
        for model_name in self._candidate_reasoning_model_names(models):
            config = _reasoning_stream_config_for_model(
                provider_name=provider_name,
                model_name=model_name,
            )
            if config is not None:
                reasoning_models[model_name] = config
        return reasoning_models

    def _candidate_reasoning_model_names(self, models: dict[str, str]) -> list[str]:
        candidates: list[str] = []
        for logical_name in ("reasoning_model", "main_chat_model"):
            self._append_unique(candidates, models.get(logical_name))
        for override in self.component_overrides.values():
            model_name = override.model.strip()
            if model_name in models:
                model_name = models[model_name]
            self._append_unique(candidates, model_name)
        return candidates

    @staticmethod
    def _append_unique(candidates: list[str], value: str | None) -> None:
        model_name = (value or "").strip()
        if model_name and model_name not in candidates:
            candidates.append(model_name)

    def component_override(self, component_name: str) -> RuntimeComponentOverride | None:
        override = self.component_overrides.get(component_name)
        if override is None:
            return None
        return RuntimeComponentOverride(
            provider=self.normalized_provider(override.provider),
            model=override.model.strip(),
        )

    def skill_override(self, component_name: str, ability_key: str | None = None) -> RuntimeSkillOverride | None:
        for key in (component_name, ability_key):
            if not key:
                continue
            override = self.skill_overrides.get(key)
            if override is not None and override.enabled and override.body.strip():
                return RuntimeSkillOverride(
                    enabled=True,
                    name=override.name.strip(),
                    description=override.description.strip(),
                    body=override.body.strip(),
                )
        return None


def current_user_llm_config() -> UserLlmRuntimeConfig | None:
    config = _CURRENT_CONFIG.get()
    return config


def is_user_llm_context_active() -> bool:
    return _USER_CONTEXT_ACTIVE.get()


@asynccontextmanager
async def user_llm_runtime_context(
    *,
    settings: Settings,
    user_id: str | None,
    internal_token: str,
):
    config = await fetch_user_llm_runtime_config(settings=settings, user_id=user_id, internal_token=internal_token)
    allow_environment_fallback = bool(config and config.allow_environment_fallback)
    context_active = bool((user_id or "").strip()) and not allow_environment_fallback
    effective_config = None if allow_environment_fallback and not (config and config.providers) else config
    token = _CURRENT_CONFIG.set(effective_config)
    active_token = _USER_CONTEXT_ACTIVE.set(context_active)
    try:
        yield
    finally:
        _CURRENT_CONFIG.reset(token)
        _USER_CONTEXT_ACTIVE.reset(active_token)


async def fetch_user_llm_runtime_config(
    *,
    settings: Settings,
    user_id: str | None,
    internal_token: str,
    ttl_seconds: int = 30,
) -> UserLlmRuntimeConfig | None:
    normalized_user = (user_id or "").strip()
    if not normalized_user:
        return None
    now = monotonic()
    async with _LOCK:
        cached = _CACHE.get(normalized_user)
        if cached and cached[0] > now:
            return cached[1]
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                f"{settings.control_plane_base_url.rstrip('/')}/internal/users/{normalized_user}/llm-runtime-config",
                headers={"X-Zhixue-Internal-Token": internal_token.strip()},
            )
            response.raise_for_status()
            config = UserLlmRuntimeConfig.model_validate(response.json())
    except Exception as exc:
        LOGGER.warning("Failed to fetch user LLM runtime config user=%s: %s", normalized_user, exc)
        return None
    async with _LOCK:
        _CACHE[normalized_user] = (now + ttl_seconds, config)
    return config
