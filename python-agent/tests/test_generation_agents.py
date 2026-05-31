from pathlib import Path
from types import SimpleNamespace
import threading

import pytest

from src.ai_modules.agents.generation.generators import SlideGeneratorAgent
from src.ai_modules.generation.content_chain import GeneratedSectionBundle, OpenAICompatibleStructuredGenerator
from src.ai_modules.generation.resource_builder import GeneratedAsset
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


@pytest.mark.asyncio
async def test_expand_content_uses_preview_text_for_binary_assets(tmp_path: Path) -> None:
    pptx_path = tmp_path / "slides.pptx"
    pptx_path.write_bytes(b"PK\x03\x04binary-pptx")

    class FakeGenerationService:
        def build_asset(self, *, asset_type, params, snapshot):
            del asset_type, params, snapshot
            return GeneratedAsset(
                assetType="SLIDES",
                title="并发编程PPT大纲",
                summary="PPT 生成成功",
                displayMode="DOWNLOAD_CARD",
                fileName="slides.pptx",
                localPath=str(pptx_path),
                previewText="PPT 演示文稿 · 6 页 · 并发编程",
                mimeType="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            )

    agent = SlideGeneratorAgent(generation_service=FakeGenerationService())

    result = await agent._tool_expand_content(
        tool_input={},
        task_id="task-slides",
        params={"query": "并发编程"},
        snapshot=_build_snapshot(),
    )

    assert result["generatedContent"] == "PPT 演示文稿 · 6 页 · 并发编程"
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
