from pathlib import Path
from types import SimpleNamespace
import threading

import httpx
import pytest

from src.ai_modules.agents.generation.generators import SlideGeneratorAgent
from src.ai_modules.generation.content_chain import GeneratedSectionBundle, OpenAICompatibleStructuredGenerator
from src.ai_modules.generation.resource_builder import GeneratedAsset
from src.ai_modules.llms.user_runtime_config import UserLlmRuntimeConfig
from src.ai_modules.runtime import provenance
from src.ai_modules.runtime import SystemSnapshot


def _build_snapshot() -> SystemSnapshot:
    return SystemSnapshot(
        current_course="Java 程序设计",
        current_chapter="并发编程",
        course_progress=0.3,
        student_name="张三",
        student_level="INTERMEDIATE",
        knowledge_gaps=["线程同步"],
        preferred_style="visual_first",
        recent_mistakes=[],
        session_id="task-generation",
        conversation_length=1,
        total_tokens_used=0,
        wiki_pages_count=10,
        last_index_update="2026-05-02",
        recent_activities=[],
    )


def test_structured_generator_uses_generation_component_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_settings = SimpleNamespace(
        normalize_provider_name=lambda provider_name: provider_name,
        resolve_component_provider=lambda component_name: "mimo",
        provider_endpoint_config=lambda provider_name: SimpleNamespace(
            name=provider_name,
            base_url="https://api.xiaomimimo.com/v1",
        ),
        provider_api_key=lambda provider_name: "fake-mimo-key",
        resolve_component_model=lambda component_name, default_logical_model, provider_name: "mimo-v2.5-pro",
    )
    monkeypatch.setattr("src.ai_modules.generation.content_chain.get_settings", lambda: fake_settings)

    generator = OpenAICompatibleStructuredGenerator()

    assert generator.provider_name == "mimo"
    assert generator.base_url == "https://api.xiaomimimo.com/v1"
    assert generator.api_key == "fake-mimo-key"
    assert generator.model_name == "mimo-v2.5-pro"


def test_document_generation_uses_higher_max_tokens_and_deterministic_temperature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generator = OpenAICompatibleStructuredGenerator()
    captured: dict[str, object] = {}

    def fake_post_chat_completion(*, messages, temperature=0.3, max_tokens=None, response_format=None):
        captured["messages"] = messages
        captured["temperature"] = temperature
        captured["max_tokens"] = max_tokens
        captured["response_format"] = response_format
        return {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"sections":[{"title":"一、核心概念与学习目标","body":"完整正文","tips":["可执行建议"],'
                            '"citations":["来源1"]}]}'
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr(generator, "_post_chat_completion", fake_post_chat_completion)

    bundle = generator.generate_document_sections(
        title="并发编程讲解文档",
        topic="Java并发编程课程中等难度讲解文档",
        snapshot={
            "current_course": "Java 程序设计",
            "current_chapter": "并发编程",
            "student_level": "INTERMEDIATE",
            "preferred_style": "step_by_step",
            "knowledge_gaps": ["线程同步"],
        },
        section_plans=[
            {
                "title": "一、核心概念与学习目标",
                "objective": "帮助学生建立概念框架",
                "sourceTitles": ["Java并发编程"],
            }
        ],
        sources=[{"title": "Java并发编程", "evidence": "介绍线程、同步与锁机制"}],
    )

    assert isinstance(bundle, GeneratedSectionBundle)
    assert captured["temperature"] == 0.0
    assert captured["max_tokens"] == 4200
    assert captured["response_format"] == {"type": "json_object"}


def test_structured_generator_uses_generation_timeout_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_settings = SimpleNamespace(
        normalize_provider_name=lambda provider_name: provider_name,
        resolve_component_provider=lambda component_name: "mimo",
        provider_endpoint_config=lambda provider_name: SimpleNamespace(
            name=provider_name,
            base_url="https://api.xiaomimimo.com/v1",
        ),
        provider_api_key=lambda provider_name: "fake-mimo-key",
        resolve_component_model=lambda component_name, default_logical_model, provider_name: "mimo-v2.5-pro",
        generation_llm_timeout_seconds=240.0,
    )
    monkeypatch.setattr("src.ai_modules.generation.content_chain.get_settings", lambda: fake_settings)

    generator = OpenAICompatibleStructuredGenerator()

    assert generator.timeout_seconds == 240.0
    assert "240.0" in f"{generator.provider_name}:{generator.base_url}:{generator.timeout_seconds}"


def test_structured_generator_builds_strict_json_schema_when_provider_supports_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_settings = SimpleNamespace(
        normalize_provider_name=lambda provider_name: provider_name,
        resolve_component_provider=lambda component_name: "openai_compatible",
        provider_endpoint_config=lambda provider_name: SimpleNamespace(
            name=provider_name,
            base_url="https://api.openai.com/v1",
            structured_output_mode="json_schema",
        ),
        provider_api_key=lambda provider_name: "fake-openai-key",
        resolve_component_model=lambda component_name, default_logical_model, provider_name: "gpt-4.1-mini",
    )
    monkeypatch.setattr("src.ai_modules.generation.content_chain.get_settings", lambda: fake_settings)

    generator = OpenAICompatibleStructuredGenerator()
    response_format = generator._structured_response_format(GeneratedSectionBundle)

    assert response_format is not None
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["name"] == "GeneratedSectionBundle"
    assert response_format["json_schema"]["strict"] is True
    assert "sections" in response_format["json_schema"]["schema"]["properties"]


def test_structured_generator_formats_empty_httpx_exception() -> None:
    assert OpenAICompatibleStructuredGenerator._format_exception(httpx.ReadTimeout("")) == "ReadTimeout"


@pytest.mark.asyncio
async def test_expand_content_uses_preview_text_for_downloadable_slides(tmp_path: Path) -> None:
    html_path = tmp_path / "slides.html"
    html_path.write_text("<!DOCTYPE html><html><body>deck</body></html>", encoding="utf-8")

    class FakeGenerationService:
        def build_asset(self, *, asset_type, params, snapshot):
            del asset_type, params, snapshot
            return GeneratedAsset(
                assetType="SLIDES",
                title="并发编程PPT大纲",
                summary="PPT 生成成功",
                displayMode="DOWNLOAD_CARD",
                fileName="slides.html",
                localPath=str(html_path),
                previewText="HTML PPT 课件 · 9 页 · 并发编程",
                mimeType="text/html; charset=UTF-8",
            )

    agent = SlideGeneratorAgent(generation_service=FakeGenerationService())

    result = await agent._tool_expand_content(
        tool_input={},
        task_id="task-slides",
        params={"query": "并发编程"},
        snapshot=_build_snapshot(),
    )

    assert result["generatedContent"] == "HTML PPT 课件 · 9 页 · 并发编程"
    assert result["asset"]["assetType"] == "SLIDES"


@pytest.mark.asyncio
async def test_expand_content_runs_sync_builder_off_event_loop() -> None:
    event_loop_thread = threading.get_ident()
    builder_thread: int | None = None

    class FakeGenerationService:
        def build_asset(self, *, asset_type, params, snapshot):
            nonlocal builder_thread
            del asset_type, params, snapshot
            builder_thread = threading.get_ident()
            return GeneratedAsset(
                assetType="SLIDES",
                title="Threaded slides",
                summary="Generated",
                displayMode="DOWNLOAD_CARD",
                fileName="slides.pptx",
                localPath=None,
                previewText="Slides preview",
                mimeType="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            )

    agent = SlideGeneratorAgent(generation_service=FakeGenerationService())

    result = await agent._tool_expand_content(
        tool_input={},
        task_id="task-slides-threaded",
        params={"query": "threaded slides"},
        snapshot=_build_snapshot(),
    )

    assert result["asset"]["assetType"] == "SLIDES"
    assert builder_thread is not None
    assert builder_thread != event_loop_thread


def test_build_llm_provenance_prefers_user_runtime_generation_config() -> None:
    runtime_config = UserLlmRuntimeConfig.model_validate(
        {
            "enabled": True,
            "activeProvider": "deepseek",
            "providers": {
                "deepseek": {
                    "provider": "deepseek",
                    "apiKey": "user-key",
                    "modelOverrides": {"main_chat_model": "user-deepseek-chat"},
                }
            },
            "componentOverrides": {
                "generation_llm": {"provider": "deepseek", "model": "main_chat_model"}
            },
        }
    )
    from src.ai_modules.llms import user_runtime_config

    config_token = user_runtime_config._CURRENT_CONFIG.set(runtime_config)
    try:
        payload = provenance.build_llm_provenance(
            agent_name="document_generation",
            generator=SimpleNamespace(provider_name="env-provider", model_name="env-model"),
            params={"retrievalResult": {"documents": [{"id": "doc-1"}]}},
        )
    finally:
        user_runtime_config._CURRENT_CONFIG.reset(config_token)

    assert payload["provider"] == "deepseek"
    assert payload["model"] == "user-deepseek-chat"
    assert payload["evidenceIds"] == ["doc-1"]
