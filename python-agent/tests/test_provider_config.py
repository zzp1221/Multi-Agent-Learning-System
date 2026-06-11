from pathlib import Path

import pytest

from src.ai_modules.config import Settings
from src.ai_modules.llms import agent_models, review_llm, tutor_llm, workflow_llm
from src.ai_modules.llms.agent_models import (
    JudgeLLMClientFactory,
    OpenAICompatibleEvaluationGenerator,
    OpenAICompatibleProfileAnalyzer,
    OpenAICompatibleQueryRewriteGenerator,
    PracticeLLMClientFactory,
    TutorToolLLMClientFactory,
)
from src.ai_modules.llms import judge_subjective_evaluator
from src.ai_modules.llms.judge_subjective_evaluator import OpenAICompatibleSubjectiveJudgeEvaluator
from src.ai_modules.llms.openai_compatible import OpenAICompatibleToolCallingLLM
from src.ai_modules.llms.practice_llm import RuleBasedJudgeLLM, RuleBasedPracticeLLM
from src.ai_modules.llms.review_llm import ReviewLLMClientFactory
from src.ai_modules.llms.spark_compatible import SparkCompatibleToolCallingLLM
from src.ai_modules.llms.tutor_llm import RuleBasedTutorLLM, TutorLLMClientFactory
from src.ai_modules.llms import user_runtime_config
from src.ai_modules.llms.user_runtime_config import UserLlmRuntimeConfig
from src.ai_modules.llms.workflow_llm import (
    GenerationToolLLMClientFactory,
    QueryRewriteToolLLMClientFactory,
    RuleBasedGenerationLLM,
    RuleBasedQueryRewriteLLM,
)
from src.ai_modules.runtime import SystemSnapshot
from src.ai_modules.runtime.skill_loader import SkillPromptLoader, append_user_skill_to_prompt


def test_settings_build_default_model_routing_config() -> None:
    settings = Settings(
        ACTIVE_PROVIDER="openai_compatible",
        MODEL_NAME="qwen3.6-plus",
        FAST_MODEL_NAME="qwen3.6-flash",
        REASONING_MODEL_NAME="qwen3.6-max-preview",
        CODE_MODEL_NAME="qwen3-coder-plus",
        CODE_FAST_MODEL_NAME="qwen3-coder-next",
        OMNI_MODEL_NAME="qwen3.5-omni-plus",
        OMNI_REALTIME_MODEL_NAME="qwen3.5-omni-plus-realtime",
        EMBEDDING_MODEL_NAME="text-embedding-v4",
        RERANK_MODEL_NAME="qwen3-rerank",
        SAFETY_MODEL_NAME="qwen3.6-flash",
        SPARK_MODEL_NAME="Spark Ultra",
        SPARK_FAST_MODEL_NAME="Spark X2-Flash",
    )

    routing = settings.build_default_model_routing_config()

    assert routing.active_provider == "openai_compatible"
    assert routing.resolve_model("main_chat_model") == "qwen3.6-plus"
    assert routing.resolve_model("fast_model", "spark") == "Spark X2-Flash"
    assert routing.providers["openai_compatible"].structured_output_mode == "json_object"
    assert routing.providers["spark"].structured_output_mode == "json_object"
    assert routing.resolve_reasoning_config("qwen3.6-max-preview", "openai_compatible") is not None
    assert routing.resolve_reasoning_config("qwen3.6-plus", "openai_compatible") is None


def test_settings_accepts_ai_openai_compatible_base_url_alias() -> None:
    settings = Settings(
        AI_OPENAI_COMPATIBLE_BASE_URL="https://token-plan-cn.xiaomimimo.com/v1",
        OPENAI_COMPATIBLE_API_KEY="key",
    )

    assert settings.openai_compatible_base_url == "https://token-plan-cn.xiaomimimo.com/v1"


def test_settings_resolves_explicit_embedding_api_key() -> None:
    settings = Settings(
        EMBEDDING_API_KEY="embedding-key",
        OPENAI_COMPATIBLE_API_KEY="chat-key",
    )

    assert settings.embedding_api_key == "embedding-key"
    assert settings.effective_embedding_api_key == "embedding-key"


def test_settings_accepts_dashscope_embedding_api_key_alias() -> None:
    settings = Settings(
        DASHSCOPE_API_KEY="dashscope-key",
        OPENAI_COMPATIBLE_API_KEY="chat-key",
    )

    assert settings.embedding_api_key == "dashscope-key"
    assert settings.effective_embedding_api_key == "dashscope-key"


def test_settings_loads_model_routing_config_from_yaml(tmp_path: Path) -> None:
    config_path = tmp_path / "model-provider.yaml"
    config_path.write_text(
        "\n".join(
            [
                "activeProvider: spark",
                "fallbackProvider: openai_compatible",
                "ttsProvider: xfyun_tts",
                "avatarProvider: xfyun_avatar",
                "providers:",
                "  spark:",
                "    name: spark",
                "    protocol: spark_compatible",
                "    baseUrl: https://spark-api.xf-yun.com",
                "    apiKeyEnv: SPARK_API_KEY",
                "    models:",
                "      main_chat_model: Spark Ultra",
                "  openai_compatible:",
                "    name: openai_compatible",
                "    protocol: openai_compatible",
                "    baseUrl: https://dashscope.aliyuncs.com/compatible-mode/v1",
                "    apiKeyEnv: OPENAI_COMPATIBLE_API_KEY",
                "    models:",
                "      main_chat_model: qwen3.6-plus",
                "      reasoning_model: qwen3.6-max-preview",
                "    reasoningModels:",
                "      qwen3.6-max-preview:",
                "        request:",
                "          thinking:",
                "            type: enabled",
                "        streamFields:",
                "          - reasoning_content",
                "        messageFields:",
                "          - reasoning_content",
            ]
        ),
        encoding="utf-8",
    )
    settings = Settings(
        ACTIVE_PROVIDER="spark",
        FALLBACK_PROVIDER="openai_compatible",
        MODEL_ROUTING_CONFIG_PATH=str(config_path),
        SPARK_API_KEY="spark-key",
        OPENAI_COMPATIBLE_API_KEY="openai-key",
        MIMO_API_KEY="",
    )

    routing = settings.model_routing_config()

    assert routing.active_provider == "spark"
    assert routing.fallback_provider == "openai_compatible"
    assert settings.resolve_logical_model("main_chat_model") == "Spark Ultra"
    reasoning_config = routing.resolve_reasoning_config("qwen3.6-max-preview", "openai_compatible")
    assert reasoning_config is not None
    assert reasoning_config.request == {"thinking": {"type": "enabled"}}
    assert reasoning_config.stream_fields == ["reasoning_content"]


def test_settings_provider_ready_for_spark_requires_api_key() -> None:
    settings = Settings(
        ACTIVE_PROVIDER="spark",
        FALLBACK_PROVIDER="",
        SPARK_API_KEY="",
        OPENAI_COMPATIBLE_API_KEY="test",
        MIMO_API_KEY="",
    )

    assert settings.selected_provider_name() == "spark"
    assert settings.provider_ready("spark") is False


def test_settings_resolve_unknown_model_raises_key_error() -> None:
    settings = Settings()

    with pytest.raises(KeyError):
        settings.resolve_logical_model("unknown_model")


def test_settings_component_override_resolves_provider_and_model() -> None:
    settings = Settings(
        ACTIVE_PROVIDER="openai_compatible",
        OPENAI_COMPATIBLE_API_KEY="openai-key",
        MIMO_API_KEY="",
        SPARK_API_KEY="spark-key",
        QUERY_REWRITE_LLM={"provider": "spark", "model": "fast_model"},
    )

    provider_name = settings.resolve_component_provider("query_rewrite_llm")
    model_name = settings.resolve_component_model(
        "query_rewrite_llm",
        default_logical_model="main_chat_model",
        provider_name=provider_name,
    )

    assert provider_name == "spark"
    assert model_name == settings.spark_fast_model_name


def test_settings_component_override_accepts_literal_model_name() -> None:
    settings = Settings(
        ACTIVE_PROVIDER="openai_compatible",
        OPENAI_COMPATIBLE_API_KEY="openai-key",
        MIMO_API_KEY="",
        EVALUATION_LLM={"model": "custom-eval-model"},
    )

    assert settings.resolve_component_model("evaluation_llm", default_logical_model="main_chat_model") == "custom-eval-model"


def test_user_runtime_config_overlays_provider_and_component_settings() -> None:
    settings = Settings(
        ACTIVE_PROVIDER="openai_compatible",
        OPENAI_COMPATIBLE_API_KEY="env-openai-key",
        MIMO_API_KEY="",
    )
    runtime_config = UserLlmRuntimeConfig.model_validate(
        {
            "enabled": False,
            "activeProvider": "deepseek",
            "fallbackProvider": "",
            "providers": {
                "deepseek": {
                    "provider": "deepseek",
                    "baseUrl": "https://example.deepseek.test/v1",
                    "apiKey": "user-deepseek-key",
                    "modelOverrides": {
                        "main_chat_model": "deepseek-chat-custom",
                        "fast_model": "deepseek-fast-custom",
                    },
                }
            },
            "componentOverrides": {
                "query_rewrite_llm": {
                    "provider": "deepseek",
                    "model": "fast_model",
                }
            },
        }
    )

    token = user_runtime_config._CURRENT_CONFIG.set(runtime_config)
    try:
        routing = settings.model_routing_config()
        assert settings.runtime_provider_name() == "deepseek"
        assert settings.provider_ready("deepseek") is True
        assert settings.provider_api_key("deepseek") == "user-deepseek-key"
        assert routing.providers["deepseek"].base_url == "https://example.deepseek.test/v1"
        assert settings.resolve_logical_model("main_chat_model", "deepseek") == "deepseek-chat-custom"
        assert settings.resolve_component_provider("query_rewrite_llm") == "deepseek"
        assert settings.resolve_component_model(
            "query_rewrite_llm",
            default_logical_model="main_chat_model",
            provider_name="deepseek",
        ) == "deepseek-fast-custom"
    finally:
        user_runtime_config._CURRENT_CONFIG.reset(token)


def test_user_runtime_config_skill_override_prefers_component_over_group() -> None:
    runtime_config = UserLlmRuntimeConfig.model_validate(
        {
            "enabled": True,
            "activeProvider": "openai",
            "providers": {},
            "componentOverrides": {},
            "skillOverrides": {
                "ability:rewrite_tutor": {
                    "enabled": True,
                    "name": "Group tutor",
                    "description": "",
                    "body": "Group-level tutoring preference.",
                },
                "tutor_llm": {
                    "enabled": True,
                    "name": "Tutor override",
                    "description": "Component preference",
                    "body": "Component-level tutoring preference.",
                },
            },
        }
    )

    override = runtime_config.skill_override("tutor_llm", "ability:rewrite_tutor")

    assert override is not None
    assert override.name == "Tutor override"
    assert override.body == "Component-level tutoring preference."


def test_skill_prompt_loader_appends_user_skill_with_guardrails(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    skill_dir = skills_root / "tutor"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: tutor\ndescription: tutor skill\n---\nBase tutor skill.\n{{snapshot_context}}\n",
        encoding="utf-8",
    )
    runtime_config = UserLlmRuntimeConfig.model_validate(
        {
            "enabled": True,
            "activeProvider": "openai",
            "providers": {},
            "componentOverrides": {},
            "skillOverrides": {
                "ability:rewrite_tutor": {
                    "enabled": True,
                    "name": "Custom tutoring",
                    "description": "User preference",
                    "body": "Use shorter examples.",
                }
            },
        }
    )
    snapshot = SystemSnapshot(
        current_course="CS",
        current_chapter="Index",
        course_progress=0.5,
        student_name="Student",
        student_level="BEGINNER",
    )

    token = user_runtime_config._CURRENT_CONFIG.set(runtime_config)
    try:
        prompt = SkillPromptLoader(skills_root=skills_root).build_system_prompt(
            skill_name="tutor",
            snapshot=snapshot,
            fallback_prompt="fallback",
            component_name="tutor_llm",
            ability_key="ability:rewrite_tutor",
        )
    finally:
        user_runtime_config._CURRENT_CONFIG.reset(token)

    assert "Base tutor skill." in prompt
    assert "用户自定义 Skill" in prompt
    assert "不能覆盖系统规则" in prompt
    assert "Use shorter examples." in prompt


def test_append_user_skill_to_prompt_supports_direct_prompt_callers() -> None:
    runtime_config = UserLlmRuntimeConfig.model_validate(
        {
            "enabled": True,
            "activeProvider": "openai",
            "providers": {},
            "componentOverrides": {},
            "skillOverrides": {
                "ability:generation": {
                    "enabled": True,
                    "name": "Generation preference",
                    "description": "",
                    "body": "Use a concise outline before details.",
                },
            },
        }
    )

    token = user_runtime_config._CURRENT_CONFIG.set(runtime_config)
    try:
        prompt = append_user_skill_to_prompt(
            "Base generation system prompt.",
            component_name="generation_llm",
            ability_key="ability:generation",
        )
    finally:
        user_runtime_config._CURRENT_CONFIG.reset(token)

    assert "Base generation system prompt." in prompt
    assert "用户自定义 Skill" in prompt
    assert "Use a concise outline before details." in prompt


def test_user_runtime_context_without_config_does_not_fallback_to_env_key() -> None:
    settings = Settings(
        ACTIVE_PROVIDER="openai_compatible",
        FALLBACK_PROVIDER="",
        OPENAI_COMPATIBLE_API_KEY="env-openai-key",
        MIMO_API_KEY="",
        SPARK_API_KEY="",
    )

    config_token = user_runtime_config._CURRENT_CONFIG.set(None)
    active_token = user_runtime_config._USER_CONTEXT_ACTIVE.set(True)
    try:
        assert settings.runtime_provider_name() == "openai_compatible"
        assert settings.provider_ready("openai_compatible") is False
        assert settings.provider_api_key("openai_compatible") == ""
    finally:
        user_runtime_config._CURRENT_CONFIG.reset(config_token)
        user_runtime_config._USER_CONTEXT_ACTIVE.reset(active_token)


def test_user_runtime_config_missing_key_does_not_fallback_to_env_key() -> None:
    settings = Settings(
        ACTIVE_PROVIDER="openai_compatible",
        OPENAI_COMPATIBLE_API_KEY="env-openai-key",
        MIMO_API_KEY="",
    )
    runtime_config = UserLlmRuntimeConfig.model_validate(
        {
            "enabled": True,
            "activeProvider": "openai",
            "fallbackProvider": "",
            "providers": {
                "openai": {
                    "provider": "openai",
                    "baseUrl": "https://api.openai.com/v1",
                    "apiKey": "",
                    "modelOverrides": {"main_chat_model": "gpt-4.1-mini"},
                }
            },
            "componentOverrides": {},
        }
    )

    token = user_runtime_config._CURRENT_CONFIG.set(runtime_config)
    try:
        assert settings.provider_ready("openai") is False
        assert settings.provider_api_key("openai") == ""
    finally:
        user_runtime_config._CURRENT_CONFIG.reset(token)


@pytest.mark.asyncio
async def test_user_runtime_context_allows_env_fallback_for_test_accounts(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(
        ACTIVE_PROVIDER="openai_compatible",
        OPENAI_COMPATIBLE_API_KEY="env-openai-key",
        MIMO_API_KEY="",
    )
    runtime_config = UserLlmRuntimeConfig.model_validate(
        {
            "enabled": False,
            "allowEnvironmentFallback": True,
            "activeProvider": "",
            "fallbackProvider": "",
            "providers": {},
            "componentOverrides": {},
        }
    )

    async def fake_fetch_user_llm_runtime_config(**_: object) -> UserLlmRuntimeConfig:
        return runtime_config

    monkeypatch.setattr(user_runtime_config, "fetch_user_llm_runtime_config", fake_fetch_user_llm_runtime_config)
    async with user_runtime_config.user_llm_runtime_context(
        settings=settings,
        user_id="00000000-0000-0000-0000-000000000001",
        internal_token="test-token",
    ):
        assert user_runtime_config.is_user_llm_context_active() is False
        assert settings.provider_ready("openai_compatible") is True
        assert settings.provider_api_key("openai_compatible") == "env-openai-key"


def test_tool_orchestration_factories_handle_missing_provider_key(monkeypatch: pytest.MonkeyPatch) -> None:
    unavailable_settings = Settings(
        ACTIVE_PROVIDER="openai_compatible",
        FALLBACK_PROVIDER="",
        OPENAI_COMPATIBLE_API_KEY="",
        MIMO_API_KEY="",
        SPARK_API_KEY="",
    )
    monkeypatch.setattr(workflow_llm, "get_settings", lambda: unavailable_settings)
    monkeypatch.setattr(tutor_llm, "get_settings", lambda: unavailable_settings)
    monkeypatch.setattr(review_llm, "get_settings", lambda: unavailable_settings)
    monkeypatch.setattr(agent_models, "get_settings", lambda: unavailable_settings)

    assert isinstance(QueryRewriteToolLLMClientFactory.create(), RuleBasedQueryRewriteLLM)
    assert isinstance(GenerationToolLLMClientFactory.create(), RuleBasedGenerationLLM)
    assert isinstance(PracticeLLMClientFactory.create(), RuleBasedPracticeLLM)
    assert isinstance(JudgeLLMClientFactory.create(), RuleBasedJudgeLLM)
    assert isinstance(TutorToolLLMClientFactory.create(), RuleBasedTutorLLM)
    assert isinstance(TutorLLMClientFactory.create(), RuleBasedTutorLLM)
    with pytest.raises(RuntimeError, match="review fallback is disabled"):
        ReviewLLMClientFactory.create()


def test_tutor_runtime_candidates_exclude_rule_based_without_provider_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        tutor_llm,
        "get_settings",
        lambda: Settings(
            ACTIVE_PROVIDER="openai_compatible",
            FALLBACK_PROVIDER="",
            OPENAI_COMPATIBLE_API_KEY="",
            MIMO_API_KEY="",
            SPARK_API_KEY="",
        ),
    )

    assert TutorLLMClientFactory.create_llm_candidates() == []


def test_tool_orchestration_factories_use_provider_aware_clients_when_provider_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    ready_settings = Settings(OPENAI_COMPATIBLE_API_KEY="test-key", MODEL_NAME="qwen3.6-plus")
    monkeypatch.setattr(workflow_llm, "get_settings", lambda: ready_settings)
    monkeypatch.setattr(tutor_llm, "get_settings", lambda: ready_settings)
    monkeypatch.setattr(review_llm, "get_settings", lambda: ready_settings)
    monkeypatch.setattr(agent_models, "get_settings", lambda: ready_settings)

    assert isinstance(QueryRewriteToolLLMClientFactory.create(), OpenAICompatibleToolCallingLLM)
    assert isinstance(GenerationToolLLMClientFactory.create(), OpenAICompatibleToolCallingLLM)
    assert isinstance(PracticeLLMClientFactory.create(), OpenAICompatibleToolCallingLLM)
    assert isinstance(JudgeLLMClientFactory.create(), OpenAICompatibleToolCallingLLM)
    assert isinstance(TutorToolLLMClientFactory.create(), OpenAICompatibleToolCallingLLM)
    assert isinstance(TutorLLMClientFactory.create(), OpenAICompatibleToolCallingLLM)
    assert isinstance(ReviewLLMClientFactory.create(), OpenAICompatibleToolCallingLLM)


def test_component_factories_support_component_level_provider_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    spark_settings = Settings(
        ACTIVE_PROVIDER="openai_compatible",
        OPENAI_COMPATIBLE_API_KEY="openai-key",
        MIMO_API_KEY="",
        SPARK_API_KEY="spark-key",
        QUERY_REWRITE_LLM={"provider": "spark", "model": "fast_model"},
        JUDGE_LLM={"provider": "spark", "model": "fast_model"},
    )
    monkeypatch.setattr(workflow_llm, "get_settings", lambda: spark_settings)
    monkeypatch.setattr(agent_models, "get_settings", lambda: spark_settings)
    monkeypatch.setattr(judge_subjective_evaluator, "get_settings", lambda: spark_settings)

    query_rewrite_llm = QueryRewriteToolLLMClientFactory.create()
    judge_client = JudgeLLMClientFactory.create()
    query_rewrite_generator = OpenAICompatibleQueryRewriteGenerator()
    evaluation_generator = OpenAICompatibleEvaluationGenerator()
    profile_analyzer = OpenAICompatibleProfileAnalyzer()
    subjective_evaluator = OpenAICompatibleSubjectiveJudgeEvaluator()

    assert isinstance(query_rewrite_llm, SparkCompatibleToolCallingLLM)
    assert isinstance(judge_client, SparkCompatibleToolCallingLLM)
    assert query_rewrite_llm.client.model_name == spark_settings.spark_fast_model_name
    assert judge_client.client.model_name == spark_settings.spark_fast_model_name
    assert query_rewrite_generator.generator.client.provider_name == "spark"
    assert evaluation_generator.generator.client.provider_name == "openai_compatible"
    assert profile_analyzer.generator.client.provider_name == "openai_compatible"
    assert subjective_evaluator.provider_name == "spark"
