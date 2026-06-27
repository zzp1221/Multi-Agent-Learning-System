"""从环境变量加载的应用配置。"""

from functools import lru_cache
from pathlib import Path
from typing import Any
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.ai_modules.models import ModelRoutingConfig, ProviderEndpointConfig, ReasoningStreamConfig

PYTHON_AGENT_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = PYTHON_AGENT_ROOT.parent


class LLMComponentOverride(BaseModel):
    """单个 LLM 组件的可选提供商/模型覆盖。"""

    provider: str = ""
    model: str = ""

    model_config = ConfigDict(extra="ignore")


class Settings(BaseSettings):
    """Python Agent 服务的运行时设置。"""

    model_config = SettingsConfigDict(
        env_file=(
            str(PROJECT_ROOT / ".env"),
            str(PYTHON_AGENT_ROOT / ".env"),
        ),
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    app_name: str = Field(default="zhixue-python-agent", alias="APP_NAME")
    app_env: str = Field(default="development", alias="APP_ENV")
    app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
    app_port: int = Field(default=8000, alias="APP_PORT")
    python_agent_internal_token: str = Field(default="", alias="PYTHON_AGENT_INTERNAL_TOKEN")
    control_plane_base_url: str = Field(default="http://localhost:8081", alias="CONTROL_PLANE_BASE_URL")

    model_provider: str = Field(default="openai_compatible", alias="MODEL_PROVIDER")
    active_provider: str = Field(default="", alias="ACTIVE_PROVIDER")
    fallback_provider: str = Field(default="", alias="FALLBACK_PROVIDER")
    model_routing_config_path: str = Field(default="", alias="MODEL_ROUTING_CONFIG_PATH")
    model_name: str = Field(default="qwen3.6-plus", alias="MODEL_NAME")
    fast_model_name: str = Field(default="qwen3.6-flash", alias="FAST_MODEL_NAME")
    reasoning_model_name: str = Field(default="qwen3.6-max-preview", alias="REASONING_MODEL_NAME")
    code_model_name: str = Field(default="qwen3-coder-plus", alias="CODE_MODEL_NAME")
    code_fast_model_name: str = Field(default="qwen3-coder-next", alias="CODE_FAST_MODEL_NAME")
    omni_model_name: str = Field(default="qwen3.5-omni-plus", alias="OMNI_MODEL_NAME")
    omni_realtime_model_name: str = Field(
        default="qwen3.5-omni-plus-realtime",
        alias="OMNI_REALTIME_MODEL_NAME",
    )
    embedding_model_name: str = Field(default="text-embedding-v4", alias="EMBEDDING_MODEL_NAME")
    rerank_model_name: str = Field(default="qwen3-rerank", alias="RERANK_MODEL_NAME")
    safety_model_name: str = Field(default="qwen3.6-flash", alias="SAFETY_MODEL_NAME")
    openai_compatible_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("OPENAI_COMPATIBLE_API_KEY", "BAILIAN_API_KEY"),
    )
    openai_compatible_base_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        validation_alias=AliasChoices(
            "OPENAI_COMPATIBLE_BASE_URL",
            "AI_OPENAI_COMPATIBLE_BASE_URL",
            "BAILIAN_BASE_URL",
        ),
    )
    embedding_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("EMBEDDING_API_KEY", "DASHSCOPE_API_KEY"),
    )
    spark_api_key: str = Field(default="", alias="SPARK_API_KEY")
    spark_app_id: str = Field(default="", alias="SPARK_APP_ID")
    spark_api_secret: str = Field(default="", alias="SPARK_API_SECRET")
    spark_base_url: str = Field(
        default="https://spark-api-open.xf-yun.com/v1",
        alias="SPARK_BASE_URL",
    )
    spark_model_name: str = Field(default="4.0Ultra", alias="SPARK_MODEL_NAME")
    spark_fast_model_name: str = Field(
        default="generalv3.5",
        alias="SPARK_FAST_MODEL_NAME",
    )
    spark_reasoning_model_name: str = Field(default="Spark X2", alias="SPARK_REASONING_MODEL_NAME")
    spark_code_model_name: str = Field(default="Spark X2-Flash", alias="SPARK_CODE_MODEL_NAME")
    spark_code_fast_model_name: str = Field(
        default="Spark X2-Flash",
        alias="SPARK_CODE_FAST_MODEL_NAME",
    )
    spark_omni_model_name: str = Field(default="Spark Ultra", alias="SPARK_OMNI_MODEL_NAME")
    spark_omni_realtime_model_name: str = Field(
        default="Spark X2-Flash",
        alias="SPARK_OMNI_REALTIME_MODEL_NAME",
    )
    spark_embedding_model_name: str = Field(
        default="spark-embedding",
        alias="SPARK_EMBEDDING_MODEL_NAME",
    )
    spark_rerank_model_name: str = Field(default="spark-rerank", alias="SPARK_RERANK_MODEL_NAME")
    spark_safety_model_name: str = Field(
        default="Spark X2-Flash",
        alias="SPARK_SAFETY_MODEL_NAME",
    )
    tts_provider: str = Field(default="mimo_tts", alias="TTS_PROVIDER")
    avatar_provider: str = Field(default="sadtalker", alias="AVATAR_PROVIDER")
    mimo_api_key: str = Field(default="", alias="MIMO_API_KEY")
    mimo_base_url: str = Field(
        default="https://api.xiaomimimo.com/v1",
        alias="MIMO_BASE_URL",
    )
    tts_timeout_seconds: float = Field(default=180.0, alias="TTS_TIMEOUT_SECONDS")
    generation_llm_timeout_seconds: float = Field(
        default=180.0,
        alias="GENERATION_LLM_TIMEOUT_SECONDS",
    )
    video_tts_max_chars: int = Field(default=260, alias="VIDEO_TTS_MAX_CHARS")
    llm_tool_content_max_string_chars: int = Field(
        default=600,
        alias="LLM_TOOL_CONTENT_MAX_STRING_CHARS",
    )
    llm_tool_content_max_list_items: int = Field(
        default=4,
        alias="LLM_TOOL_CONTENT_MAX_LIST_ITEMS",
    )
    llm_tool_content_max_dict_items: int = Field(
        default=12,
        alias="LLM_TOOL_CONTENT_MAX_DICT_ITEMS",
    )
    enable_llm_result_cache: bool = Field(default=True, alias="ENABLE_LLM_RESULT_CACHE")
    llm_result_cache_ttl_seconds: int = Field(
        default=300,
        alias="LLM_RESULT_CACHE_TTL_SECONDS",
    )
    retrieval_result_cache_ttl_seconds: int = Field(
        default=60,
        alias="RETRIEVAL_RESULT_CACHE_TTL_SECONDS",
    )
    runtime_cache_max_entries: int = Field(default=256, alias="RUNTIME_CACHE_MAX_ENTRIES")
    cache_adaptive_enabled: bool = Field(default=True, alias="CACHE_ADAPTIVE_ENABLED")
    cache_adaptive_window_size: int = Field(default=100, alias="CACHE_ADAPTIVE_WINDOW_SIZE")
    cache_adaptive_min_samples: int = Field(default=50, alias="CACHE_ADAPTIVE_MIN_SAMPLES")
    cache_adaptive_min_hit_rate: float = Field(default=0.10, alias="CACHE_ADAPTIVE_MIN_HIT_RATE")
    cache_adaptive_bypass_seconds: int = Field(default=60, alias="CACHE_ADAPTIVE_BYPASS_SECONDS")
    cache_adaptive_probe_interval: int = Field(default=20, alias="CACHE_ADAPTIVE_PROBE_INTERVAL")
    enable_semantic_reranking: bool = Field(default=False, alias="ENABLE_SEMANTIC_RERANKING")
    semantic_reranker_use_api: bool = Field(default=False, alias="SEMANTIC_RERANKER_USE_API")
    cohere_api_key: str = Field(default="", alias="COHERE_API_KEY")
    cache_max_value_bytes: int = Field(default=262144, alias="CACHE_MAX_VALUE_BYTES")
    query_rewrite_llm: LLMComponentOverride = Field(
        default_factory=LLMComponentOverride,
        alias="QUERY_REWRITE_LLM",
    )
    retrieval_llm: LLMComponentOverride = Field(
        default_factory=LLMComponentOverride,
        alias="RETRIEVAL_LLM",
    )
    generation_llm: LLMComponentOverride = Field(
        default_factory=LLMComponentOverride,
        alias="GENERATION_LLM",
    )
    practice_llm: LLMComponentOverride = Field(
        default_factory=LLMComponentOverride,
        alias="PRACTICE_LLM",
    )
    judge_llm: LLMComponentOverride = Field(
        default_factory=LLMComponentOverride,
        alias="JUDGE_LLM",
    )
    profile_llm: LLMComponentOverride = Field(
        default_factory=LLMComponentOverride,
        alias="PROFILE_LLM",
    )
    tutor_llm: LLMComponentOverride = Field(
        default_factory=LLMComponentOverride,
        alias="TUTOR_LLM",
    )
    conversation_summary_llm: LLMComponentOverride = Field(
        default_factory=LLMComponentOverride,
        alias="CONVERSATION_SUMMARY_LLM",
    )
    planning_llm: LLMComponentOverride = Field(
        default_factory=LLMComponentOverride,
        alias="PLANNING_LLM",
    )
    review_llm: LLMComponentOverride = Field(
        default_factory=LLMComponentOverride,
        alias="REVIEW_LLM",
    )
    safety_llm: LLMComponentOverride = Field(
        default_factory=LLMComponentOverride,
        alias="SAFETY_LLM",
    )
    evaluation_llm: LLMComponentOverride = Field(
        default_factory=LLMComponentOverride,
        alias="EVALUATION_LLM",
    )
    path_planning_llm: LLMComponentOverride = Field(
        default_factory=LLMComponentOverride,
        alias="PATH_PLANNING_LLM",
    )
    resource_push_llm: LLMComponentOverride = Field(
        default_factory=LLMComponentOverride,
        alias="RESOURCE_PUSH_LLM",
    )
    postgres_host: str = Field(default="localhost", alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, alias="POSTGRES_PORT")
    postgres_db: str = Field(default="zhixue", alias="POSTGRES_DB")
    postgres_user: str = Field(default="postgres", alias="POSTGRES_USER")
    postgres_password: str = Field(default="", alias="POSTGRES_PASSWORD")
    mongo_uri: str = Field(default="mongodb://localhost:27017/zhixue", alias="MONGO_URI")
    mongo_db: str = Field(default="zhixue", alias="MONGO_DB")
    redis_host: str = Field(default="redis", alias="REDIS_HOST")
    redis_port: int = Field(default=6379, alias="REDIS_PORT")
    redis_password: str = Field(default="", alias="REDIS_PASSWORD")
    smart_engine_stream_key: str = Field(
        default="zhixue:smart-engine:tasks",
        alias="SMART_ENGINE_TASK_STREAM_KEY",
    )
    smart_engine_dlq_key: str = Field(
        default="zhixue:smart-engine:tasks:dlq",
        alias="SMART_ENGINE_TASK_DLQ_KEY",
    )
    smart_engine_consumer_group: str = Field(
        default="smart-engine-python",
        alias="SMART_ENGINE_TASK_CONSUMER_GROUP",
    )
    smart_engine_consumer_name: str = Field(
        default="smart-engine-python-1",
        alias="SMART_ENGINE_TASK_CONSUMER_NAME",
    )
    smart_engine_block_ms: int = Field(default=5000, alias="SMART_ENGINE_TASK_BLOCK_MS")
    smart_engine_max_retries: int = Field(default=3, alias="SMART_ENGINE_TASK_MAX_RETRIES")
    smart_engine_retry_backoff_seconds: float = Field(
        default=2.0,
        alias="SMART_ENGINE_TASK_RETRY_BACKOFF_SECONDS",
    )
    smart_engine_cancel_key_prefix: str = Field(
        default="zhixue:smart-engine:cancel:",
        alias="SMART_ENGINE_TASK_CANCEL_KEY_PREFIX",
    )
    smart_engine_callback_timeout_seconds: float = Field(
        default=10.0,
        alias="SMART_ENGINE_CALLBACK_TIMEOUT_SECONDS",
    )
    smart_engine_callback_retries: int = Field(default=2, alias="SMART_ENGINE_CALLBACK_RETRIES")
    smart_engine_worker_concurrency: int = Field(default=2, alias="SMART_ENGINE_WORKER_CONCURRENCY")
    retrieval_domain: str = Field(
        default="COMPUTER_SCIENCE",
        alias="RETRIEVAL_DOMAIN",
    )
    knowledge_embedding_model_name: str = Field(
        default="qwen3-vl-embedding",
        alias="KNOWLEDGE_EMBEDDING_MODEL_NAME",
    )
    knowledge_embedding_dimension: int = Field(
        default=1024,
        alias="KNOWLEDGE_EMBEDDING_DIMENSION",
    )
    knowledge_embedding_timeout_seconds: float = Field(
        default=10.0,
        alias="KNOWLEDGE_EMBEDDING_TIMEOUT_SECONDS",
    )
    knowledge_embedding_max_retries: int = Field(
        default=2,
        alias="KNOWLEDGE_EMBEDDING_MAX_RETRIES",
    )
    knowledge_embedding_retry_backoff_seconds: float = Field(
        default=0.5,
        alias="KNOWLEDGE_EMBEDDING_RETRY_BACKOFF_SECONDS",
    )
    sandbox_root: str = Field(default="sandbox-temp", alias="SANDBOX_ROOT")
    web_search_provider: str = Field(default="builtin", alias="WEB_SEARCH_PROVIDER")
    web_search_api_key: str = Field(default="", alias="WEB_SEARCH_API_KEY")
    web_search_base_url: str = Field(default="", alias="WEB_SEARCH_BASE_URL")
    web_search_timeout_seconds: float = Field(default=10.0, alias="WEB_SEARCH_TIMEOUT_SECONDS")
    web_search_cache_ttl_seconds: int = Field(default=300, alias="WEB_SEARCH_CACHE_TTL_SECONDS")
    web_fetch_timeout_seconds: float = Field(default=10.0, alias="WEB_FETCH_TIMEOUT_SECONDS")
    web_fetch_max_bytes: int = Field(default=1_000_000, alias="WEB_FETCH_MAX_BYTES")
    tavily_api_key: str = Field(default="", alias="TAVILY_API_KEY")
    tavily_base_url: str = Field(default="https://api.tavily.com/search", alias="TAVILY_BASE_URL")

    enable_local_judge: bool = Field(default=False, alias="ENABLE_LOCAL_JUDGE")
    local_judge_model_path: str = Field(
        default="/app/models/judge_model.gguf",
        alias="LOCAL_JUDGE_MODEL_PATH",
    )

    otel_exporter_otlp_endpoint: str = Field(
        default="",
        alias="OTEL_EXPORTER_OTLP_ENDPOINT",
    )
    otel_service_name: str = Field(
        default="zhixue-python-agent",
        alias="OTEL_SERVICE_NAME",
    )

    @staticmethod
    def normalize_provider_name(provider_name: str | None) -> str:
        normalized = (provider_name or "").strip().lower()
        if normalized == "bailian":
            return "openai_compatible"
        if normalized in {"dashscope", "aliyun", "dashscope_bailian"}:
            return "dashscope"
        if normalized in {"custom", "custom_openai"}:
            return "custom_openai_compatible"
        if normalized in {"glm", "bigmodel"}:
            return "zhipu"
        return normalized

    @property
    def bailian_api_key(self) -> str:
        return self.openai_compatible_api_key

    @property
    def bailian_base_url(self) -> str:
        return self.openai_compatible_base_url

    @property
    def effective_embedding_api_key(self) -> str:
        return self.embedding_api_key or self.openai_compatible_api_key

    def selected_provider_name(self) -> str:
        """返回活跃提供商，兼容回退到 MODEL_PROVIDER。"""

        return self.normalize_provider_name(self.active_provider or self.model_provider or "openai_compatible")

    def selected_fallback_provider_name(self) -> str | None:
        fallback = self.normalize_provider_name(self.fallback_provider)
        return fallback or None

    def any_provider_ready(self) -> bool:
        """返回是否有任何已配置的提供商拥有可用凭证。"""

        return any(
            self.provider_ready(provider_name)
            for provider_name in self.model_routing_config().providers
        )

    def runtime_provider_name(self) -> str:
        """返回运行时实际应使用的提供商。"""

        user_config = self._current_user_llm_config()
        if user_config is not None:
            return user_config.runtime_provider_name("")
        if self._user_llm_context_active():
            return self.selected_provider_name()

        selected = self.selected_provider_name()
        fallback = self.selected_fallback_provider_name()

        if self.provider_ready(selected):
            return selected
        if fallback and self.provider_ready(fallback):
            return fallback

        for provider_name in self.model_routing_config().providers:
            if self.provider_ready(provider_name):
                return provider_name
        return selected

    def build_default_model_routing_config(self) -> ModelRoutingConfig:
        """从环境变量构建默认的逻辑模型路由配置。"""

        return ModelRoutingConfig(
            activeProvider=self.selected_provider_name(),
            fallbackProvider=self.selected_fallback_provider_name(),
            ttsProvider=self.tts_provider,
            avatarProvider=self.avatar_provider,
            providers={
                "openai_compatible": ProviderEndpointConfig(
                    name="openai_compatible",
                    protocol="openai_compatible",
                    baseUrl=self.openai_compatible_base_url,
                    apiKeyEnv="OPENAI_COMPATIBLE_API_KEY",
                    timeoutMs=60000,
                    models={
                        "main_chat_model": self.model_name,
                        "fast_model": self.fast_model_name,
                        "reasoning_model": self.reasoning_model_name,
                        "code_model": self.code_model_name,
                        "code_fast_model": self.code_fast_model_name,
                        "omni_model": self.omni_model_name,
                        "omni_realtime_model": self.omni_realtime_model_name,
                        "embedding_model": self.embedding_model_name,
                        "rerank_model": self.rerank_model_name,
                        "safety_model": self.safety_model_name,
                    },
                    reasoningModels=self._default_openai_compatible_reasoning_models(),
                ),
                "mimo": ProviderEndpointConfig(
                    name="mimo",
                    protocol="openai_compatible",
                    baseUrl=self.mimo_base_url,
                    apiKeyEnv="MIMO_API_KEY",
                    timeoutMs=120000,
                    models={
                        "main_chat_model": "mimo-v2-omni",
                        "fast_model": "mimo-v2-flash",
                        "reasoning_model": "mimo-v2-omni",
                        "code_model": "mimo-v2-omni",
                        "code_fast_model": "mimo-v2-flash",
                        "omni_model": "mimo-v2-omni",
                        "omni_realtime_model": "mimo-v2-omni",
                        "embedding_model": "text-embedding-v4",
                        "rerank_model": "qwen3-rerank",
                        "safety_model": "mimo-v2-flash",
                    },
                    reasoningModels=self._default_openai_compatible_reasoning_models(
                        model_names=("mimo-v2-omni", "mimo-v2.5-pro", self.reasoning_model_name),
                    ),
                ),
                "spark": ProviderEndpointConfig(
                    name="spark",
                    protocol="spark_compatible",
                    baseUrl=self.spark_base_url,
                    apiKeyEnv="SPARK_API_KEY",
                    appIdEnv="SPARK_APP_ID",
                    apiSecretEnv="SPARK_API_SECRET",
                    timeoutMs=60000,
                    models={
                        "main_chat_model": self.spark_model_name,
                        "fast_model": self.spark_fast_model_name,
                        "reasoning_model": self.spark_reasoning_model_name,
                        "code_model": self.spark_code_model_name,
                        "code_fast_model": self.spark_code_fast_model_name,
                        "omni_model": self.spark_omni_model_name,
                        "omni_realtime_model": self.spark_omni_realtime_model_name,
                        "embedding_model": self.spark_embedding_model_name,
                        "rerank_model": self.spark_rerank_model_name,
                        "safety_model": self.spark_safety_model_name,
                    },
                ),
            },
        )

    def _default_openai_compatible_reasoning_models(
        self,
        model_names: tuple[str, ...] | None = None,
    ) -> dict[str, ReasoningStreamConfig]:
        """Return conservative raw-reasoning settings for compatible chat APIs."""

        names = model_names or (self.reasoning_model_name,)
        config = ReasoningStreamConfig(
            request={"thinking": {"type": "enabled"}},
            streamFields=["reasoning_content", "reasoning", "reasoningContent"],
            messageFields=["reasoning_content", "reasoning", "reasoningContent"],
        )
        return {name: config for name in names if name.strip()}

    def model_routing_config(self) -> ModelRoutingConfig:
        """可用时从 YAML 加载提供商路由配置，否则使用默认值。"""

        def with_user_runtime_config(base: ModelRoutingConfig) -> ModelRoutingConfig:
            user_config = self._current_user_llm_config()
            if user_config is None:
                return base
            return user_config.routing_config(base)

        config_path = self.model_routing_config_path.strip()
        if not config_path:
            return with_user_runtime_config(self.build_default_model_routing_config())

        path = Path(config_path)
        if not path.exists():
            return with_user_runtime_config(self.build_default_model_routing_config())

        try:
            import yaml
        except ModuleNotFoundError:
            return with_user_runtime_config(self.build_default_model_routing_config())

        raw_payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        base = ModelRoutingConfig.model_validate(self._normalize_model_routing_payload(raw_payload))
        return with_user_runtime_config(base)

    def _normalize_model_routing_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized_payload = dict(payload)
        normalized_payload["activeProvider"] = self.normalize_provider_name(normalized_payload.get("activeProvider"))
        normalized_payload["fallbackProvider"] = self.normalize_provider_name(normalized_payload.get("fallbackProvider"))

        raw_providers = normalized_payload.get("providers", {})
        if not isinstance(raw_providers, dict):
            return normalized_payload

        providers: dict[str, Any] = {}
        for raw_name, raw_config in raw_providers.items():
            normalized_name = self.normalize_provider_name(str(raw_name))
            provider_config = dict(raw_config) if isinstance(raw_config, dict) else raw_config
            if isinstance(provider_config, dict):
                provider_config["name"] = normalized_name
                if normalized_name == "openai_compatible":
                    provider_config["apiKeyEnv"] = "OPENAI_COMPATIBLE_API_KEY"
            providers[normalized_name] = provider_config
        normalized_payload["providers"] = providers
        return normalized_payload

    def resolve_logical_model(
        self,
        logical_model_name: str,
        provider_name: str | None = None,
    ) -> str:
        """为选定的提供商解析逻辑模型名称。"""

        routing = self.model_routing_config()
        return routing.resolve_model(
            logical_model_name,
            self.normalize_provider_name(provider_name or self.runtime_provider_name()),
        )

    def provider_endpoint_config(self, provider_name: str | None = None) -> ProviderEndpointConfig:
        """返回选定或指定提供商的端点配置。"""

        routing = self.model_routing_config()
        return routing.providers[self.normalize_provider_name(provider_name or self.runtime_provider_name())]

    def reasoning_stream_config(
        self,
        *,
        provider_name: str | None = None,
        model_name: str,
    ) -> ReasoningStreamConfig | None:
        """Return raw-reasoning stream config for the actual provider/model if enabled."""

        provider = self.normalize_provider_name(provider_name or self.runtime_provider_name())
        routing = self.model_routing_config()
        if provider not in routing.providers:
            return None
        return routing.resolve_reasoning_config(model_name, provider)

    def provider_api_key(self, provider_name: str | None = None) -> str:
        """返回提供商配置的 API 密钥字符串。"""

        provider = self.normalize_provider_name(provider_name or self.runtime_provider_name())
        user_config = self._current_user_llm_config()
        if user_config is not None:
            return user_config.provider_api_key(provider)
        if self._user_llm_context_active():
            return ""
        if provider == "spark":
            return self.spark_api_key
        if provider == "mimo":
            return self.mimo_api_key or self.openai_compatible_api_key
        return self.openai_compatible_api_key

    def provider_ready(self, provider_name: str | None = None) -> bool:
        """检查提供商所需的凭证是否存在。"""

        provider = self.normalize_provider_name(provider_name or self.runtime_provider_name())
        user_config = self._current_user_llm_config()
        if user_config is not None:
            return user_config.provider_ready(provider)
        if self._user_llm_context_active():
            return False
        if provider == "spark":
            return bool(self.spark_api_key)
        if provider == "mimo":
            return bool(self.mimo_api_key or self.openai_compatible_api_key)
        return bool(self.openai_compatible_api_key)

    def llm_component_override(self, component_name: str) -> LLMComponentOverride:
        """返回指定 LLM 组件的覆盖配置块。"""

        user_config = self._current_user_llm_config()
        if user_config is not None:
            user_override = user_config.component_override(component_name)
            if user_override is not None:
                return LLMComponentOverride(provider=user_override.provider, model=user_override.model)
        override = getattr(self, component_name, None)
        if isinstance(override, LLMComponentOverride):
            return override
        return LLMComponentOverride()

    def _current_user_llm_config(self) -> Any | None:
        from src.ai_modules.llms.user_runtime_config import current_user_llm_config

        return current_user_llm_config()

    def _user_llm_context_active(self) -> bool:
        from src.ai_modules.llms.user_runtime_config import is_user_llm_context_active

        return is_user_llm_context_active()

    def resolve_component_provider(self, component_name: str) -> str:
        """解析特定 LLM 组件应使用的提供商。"""

        override = self.llm_component_override(component_name)
        requested_provider = self.normalize_provider_name(override.provider)
        if requested_provider and self.provider_ready(requested_provider):
            return requested_provider
        return self.runtime_provider_name()

    def resolve_component_model(
        self,
        component_name: str,
        *,
        default_logical_model: str,
        provider_name: str | None = None,
    ) -> str:
        """解析组件模型覆盖，接受逻辑名称或字面名称。"""

        override = self.llm_component_override(component_name)
        resolved_provider = self.normalize_provider_name(provider_name or self.resolve_component_provider(component_name))
        configured_model = override.model.strip()
        if not configured_model:
            return self.resolve_logical_model(default_logical_model, resolved_provider)

        provider_config = self.provider_endpoint_config(resolved_provider)
        if configured_model in provider_config.models:
            return provider_config.models[configured_model]
        return configured_model

    def postgres_connect_kwargs(self) -> dict[str, Any]:
        """返回 psycopg2 的 PostgreSQL 连接参数。"""

        return {
            "dbname": self.postgres_db,
            "user": self.postgres_user,
            "password": self.postgres_password,
            "host": self.postgres_host,
            "port": self.postgres_port,
        }

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """返回缓存的配置实例。"""

    return Settings()


def get_sandbox_root() -> Path:
    """返回配置的沙箱目录路径。"""

    return Path(get_settings().sandbox_root)
