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
from src.ai_modules.llms.workflow_llm import (
    GenerationToolLLMClientFactory,
    QueryRewriteToolLLMClientFactory,
    RuleBasedGenerationLLM,
    RuleBasedQueryRewriteLLM,
)


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
