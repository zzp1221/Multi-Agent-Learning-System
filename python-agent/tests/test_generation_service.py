from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.ai_modules.config import get_settings
from src.ai_modules.generation import (
    ContentGenerationChain,
    GeneratedCodeAsset,
    GenerationOutputInvalidError,
    GeneratedMindMap,
    GeneratedSection,
    GeneratedSectionBundle,
    GeneratedSlideDeck,
    GeneratedTextAsset,
    OpenAICompatibleStructuredGenerator,
    ResourceGenerationService,
)
from src.ai_modules.runtime import SystemSnapshot
from src.ai_modules.models.video import VideoScriptPayload


class FakePrimaryGenerator:
    async def generate_document_sections(self, **kwargs) -> GeneratedSectionBundle:
        del kwargs
        return GeneratedSectionBundle(
            sections=[
                GeneratedSection(
                    title="一、核心概念与学习目标",
                    body="这里是百炼生成的正文。",
                    tips=["- 用自己的话复述核心概念。"],
                    citations=["- [来源1] 数据库索引导学"],
                ),
                GeneratedSection(
                    title="二、关键原理与判断方法",
                    body="这里是百炼生成的原理分析。",
                    tips=["- 先判断条件，再套用结论。"],
                    citations=["- [来源1] B+树原理"],
                ),
                GeneratedSection(
                    title="三、典型误区与辨析",
                    body="这里是百炼生成的误区辨析。",
                    tips=["- 对比相近概念的适用边界。"],
                    citations=["- [来源1] 数据库索引导学"],
                ),
                GeneratedSection(
                    title="四、练习建议与复习路径",
                    body="这里是百炼生成的练习建议。",
                    tips=["- 先做基础题，再做综合题。"],
                    citations=["- [来源1] B+树原理"],
                ),
            ]
        )

    async def generate_reading_asset(self, **kwargs) -> GeneratedTextAsset:
        del kwargs
        return GeneratedTextAsset(
            title="联合索引延伸阅读",
            summary="百炼生成的延伸阅读",
            body="这里是百炼生成的阅读正文。",
        )

    async def generate_slides_asset(self, **kwargs) -> GeneratedSlideDeck:
        del kwargs
        return GeneratedSlideDeck.model_validate(
            {
                "title": "联合索引PPT大纲",
                "summary": "百炼生成的PPT大纲",
                "slides": [
                    {
                        "title": "联合索引概念",
                        "bullets": ["定义", "场景"],
                        "speakerNotes": "先讲概念。",
                    }
                ],
            }
        )

    async def generate_mindmap_asset(self, **kwargs) -> GeneratedMindMap:
        del kwargs
        return GeneratedMindMap.model_validate(
            {
                "title": "联合索引思维导图",
                "summary": "百炼生成的导图",
                "root": "联合索引",
                "children": [{"name": "定义", "children": [{"name": "概念"}]}],
            }
        )

    async def generate_code_asset(self, **kwargs) -> GeneratedCodeAsset:
        del kwargs
        return GeneratedCodeAsset(
            title="联合索引代码案例",
            summary="百炼生成的代码案例",
            code="def explain_topic() -> str:\n    return '百炼代码案例'",
            explanation="这里是百炼生成的代码解释。",
        )

    async def generate_video_script(self, **kwargs) -> VideoScriptPayload:
        del kwargs
        return VideoScriptPayload.model_validate(
            {
                "title": "联合索引教学视频",
                "totalDuration": 60,
                "segments": [
                    {
                        "id": 1,
                        "type": "intro",
                        "text": "今天我们用联合索引来理解最左前缀原则。",
                        "duration": 12,
                        "visualHint": "show_title_card",
                    },
                    {
                        "id": 2,
                        "type": "concept",
                        "text": "联合索引指把多个字段按顺序组织在一棵索引结构中，查询能否命中和字段顺序直接相关。",
                        "duration": 18,
                        "visualHint": "show_concept_explanation",
                    },
                    {
                        "id": 3,
                        "type": "case",
                        "text": "例如先按班级再按学号建立索引，按班级查询能走索引，直接只按学号过滤通常不能完整利用它。",
                        "duration": 18,
                        "visualHint": "show_case_demo",
                    },
                    {
                        "id": 4,
                        "type": "summary",
                        "text": "最后记住：设计联合索引时，先放筛选度高且常出现在条件最左侧的字段。",
                        "duration": 12,
                        "visualHint": "show_summary_card",
                    },
                ],
                "fullText": "今天我们用联合索引来理解最左前缀原则。联合索引指把多个字段按顺序组织在一棵索引结构中，查询能否命中和字段顺序直接相关。例如先按班级再按学号建立索引，按班级查询能走索引，直接只按学号过滤通常不能完整利用它。最后记住：设计联合索引时，先放筛选度高且常出现在条件最左侧的字段。",
                "videoStyle": "talking_head",
            }
        )

    async def generate_video_script_async(self, **kwargs) -> VideoScriptPayload:
        return await self.generate_video_script(**kwargs)


class FailingPrimaryGenerator:
    async def generate_document_sections(self, **kwargs) -> GeneratedSectionBundle:
        del kwargs
        raise RuntimeError("simulated bailian failure")

    async def generate_reading_asset(self, **kwargs) -> GeneratedTextAsset:
        del kwargs
        raise RuntimeError("simulated bailian failure")

    async def generate_slides_asset(self, **kwargs) -> GeneratedSlideDeck:
        del kwargs
        raise RuntimeError("simulated bailian failure")

    async def generate_mindmap_asset(self, **kwargs) -> GeneratedMindMap:
        del kwargs
        raise RuntimeError("simulated bailian failure")

    async def generate_code_asset(self, **kwargs) -> GeneratedCodeAsset:
        del kwargs
        raise RuntimeError("simulated bailian failure")

    async def generate_video_script(self, **kwargs) -> VideoScriptPayload:
        del kwargs
        raise RuntimeError("simulated bailian failure")

    async def generate_video_script_async(self, **kwargs) -> VideoScriptPayload:
        return await self.generate_video_script(**kwargs)


@pytest.mark.asyncio
async def test_generation_service_writes_document_asset(tmp_path: Path) -> None:
    service = ResourceGenerationService(
        sandbox_root=tmp_path,
        content_chain=ContentGenerationChain(primary_generator=FakePrimaryGenerator()),
    )
    snapshot = SystemSnapshot(
        current_course="数据库原理",
        current_chapter="索引",
        course_progress=0.3,
        student_name="张三",
        student_level="BASIC",
        knowledge_gaps=["B+树"],
        preferred_style="step_by_step",
        recent_mistakes=[],
        session_id="task-1",
        conversation_length=1,
        total_tokens_used=0,
        wiki_pages_count=10,
        last_index_update="2026-05-02",
        recent_activities=[],
    )

    asset = await service.build_asset(
        asset_type="DOCUMENT",
        params={
            "taskId": "task-doc",
            "query": "数据库原理 联合索引 中等 讲解文档",
            "keyPoints": "联合索引",
            "rewrittenQuery": "数据库原理 联合索引",
            "retrievalResult": {
                "documents": [
                    {"title": "数据库索引导学", "channel": "hybrid"},
                    {"title": "B+树原理", "channel": "hybrid"},
                ]
            },
        },
        snapshot=snapshot,
    )

    assert asset.asset_type == "DOCUMENT"
    assert asset.title == "联合索引导学文档"
    assert "中等" not in asset.title
    assert "讲解文档" not in asset.title
    assert asset.file_name == "document_guide_task-doc.md"
    assert Path(asset.local_path).exists()
    content = Path(asset.local_path).read_text(encoding="utf-8")
    assert content.startswith("# ")
    assert "## 生成大纲" in content
    assert "## 一、核心概念与学习目标" in content
    assert "这里是百炼生成的正文。" in content
    assert "课程:" not in content
    assert "章节:" not in content
    assert "学生水平:" not in content
    assert "学习风格:" not in content
    assert "### 引用依据" not in content
    assert "## 参考来源" not in content
    assert "证据说明" not in content


@pytest.mark.asyncio
async def test_generation_service_raises_when_primary_generator_fails(tmp_path: Path) -> None:
    service = ResourceGenerationService(
        sandbox_root=tmp_path,
        content_chain=ContentGenerationChain(primary_generator=FailingPrimaryGenerator()),
    )
    snapshot = SystemSnapshot(
        current_course="数据库原理",
        current_chapter="索引",
        course_progress=0.3,
        student_name="张三",
        student_level="BASIC",
        knowledge_gaps=["B+树"],
        preferred_style="step_by_step",
        recent_mistakes=[],
        session_id="task-1",
        conversation_length=1,
        total_tokens_used=0,
        wiki_pages_count=10,
        last_index_update="2026-05-02",
        recent_activities=[],
    )

    with pytest.raises(RuntimeError):
        await service.build_asset(
            asset_type="DOCUMENT",
            params={
                "taskId": "task-fallback",
                "query": "联合索引",
                "rewrittenQuery": "数据库原理 联合索引",
                "retrievalResult": {
                    "documents": [
                        {"title": "数据库索引导学", "channel": "hybrid"},
                        {"title": "B+树原理", "channel": "hybrid"},
                    ]
                },
            },
            snapshot=snapshot,
        )


@pytest.mark.asyncio
async def test_generation_service_writes_non_document_assets_from_llm_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ResourceGenerationService(
        sandbox_root=tmp_path,
        content_chain=ContentGenerationChain(primary_generator=FakePrimaryGenerator()),
    )
    snapshot = SystemSnapshot(
        current_course="数据库原理",
        current_chapter="索引",
        course_progress=0.3,
        student_name="张三",
        student_level="BASIC",
        knowledge_gaps=["B+树"],
        preferred_style="step_by_step",
        recent_mistakes=[],
        session_id="task-1",
        conversation_length=1,
        total_tokens_used=0,
        wiki_pages_count=10,
        last_index_update="2026-05-02",
        recent_activities=[],
    )
    params = {
        "taskId": "task-multi",
        "query": "联合索引",
        "rewrittenQuery": "数据库原理 联合索引",
        "retrievalResult": {"documents": [{"title": "数据库索引导学", "channel": "hybrid"}]},
    }

    class FakeMiMoClient:
        def omni_chat_sync(self, **kwargs):
            del kwargs
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"slides":['
                                '{"slideTitle":"联合索引概念","bullets":["定义","场景","限制"],"speakerNotes":"先讲联合索引的定义、典型使用场景和边界限制，帮助学生建立整体认识。"},'
                                '{"slideTitle":"最左前缀","bullets":["顺序","过滤","排序"],"speakerNotes":"再讲最左前缀的判断方式，说明过滤条件和排序条件如何影响索引利用。"},'
                                '{"slideTitle":"范围条件","bullets":["截断","回表","覆盖"],"speakerNotes":"说明范围条件后的列为何可能无法继续用于精确匹配，并结合回表和覆盖索引判断。"},'
                                '{"slideTitle":"执行计划","bullets":["key","rows","extra"],"speakerNotes":"引导学生阅读执行计划中的 key、rows 和 extra 字段，验证联合索引是否被正确使用。"},'
                                '{"slideTitle":"设计策略","bullets":["选择性","频率","排序"],"speakerNotes":"从选择性、查询频率和排序需求三个角度解释联合索引列顺序的设计策略。"},'
                                '{"slideTitle":"练习复盘","bullets":["条件","顺序","结论"],"speakerNotes":"最后通过练习复盘，让学生先列条件、再看列顺序，最后给出是否命中的结论。"}'
                                ']}'
                            )
                        }
                    }
                ]
            }

        def extract_json(self, response):
            import json

            return json.loads(response["choices"][0]["message"]["content"])

    monkeypatch.setenv("MIMO_API_KEY", "unit-test-mimo-key")
    get_settings.cache_clear()
    monkeypatch.setattr("src.ai_modules.llms.mimo_client.MiMoClient", FakeMiMoClient)

    reading_asset = await service.build_asset(asset_type="READING", params=params, snapshot=snapshot)
    pending_slides_asset = await service.build_asset(asset_type="SLIDES", params=params, snapshot=snapshot)
    confirmed_params = {
        **params,
        "confirmedSlideOutline": True,
        "confirmedSlideOutlineText": pending_slides_asset.inline_content,
    }
    mindmap_asset = await service.build_asset(asset_type="MINDMAP", params=params, snapshot=snapshot)
    code_asset = await service.build_asset(asset_type="CODE", params=params, snapshot=snapshot)

    assert "这里是百炼生成的阅读正文。" in Path(reading_asset.local_path).read_text(encoding="utf-8")
    assert pending_slides_asset.display_mode == "SLIDE_OUTLINE_CONFIRMATION"
    assert pending_slides_asset.local_path is None
    assert "联合索引PPT大纲" in pending_slides_asset.inline_content
    slides_asset = await service.build_asset(asset_type="SLIDES", params=confirmed_params, snapshot=snapshot)
    assert slides_asset.display_mode == "DOWNLOAD_CARD"
    assert slides_asset.file_name == "slides_task-multi.html"
    assert slides_asset.mime_type == "text/html; charset=UTF-8"
    html = Path(slides_asset.local_path).read_text(encoding="utf-8")
    assert html.startswith("<!DOCTYPE html>")
    assert 'data-generated-by="zhixue-html-ppt"' in html
    assert html.count('<section class="slide') >= 9
    assert "<script" in html
    assert "application/vnd.openxmlformats" not in slides_asset.mime_type
    assert mindmap_asset.display_mode == "INLINE_MERMAID"
    assert mindmap_asset.file_name == "mindmap_task-multi.mmd"
    assert Path(mindmap_asset.local_path).exists()
    assert Path(mindmap_asset.local_path).read_text(encoding="utf-8") == mindmap_asset.inline_content
    assert "mindmap" in mindmap_asset.inline_content
    assert 'root["联合索引"]' in mindmap_asset.inline_content
    assert 'node_1["定义"]' in mindmap_asset.inline_content
    assert code_asset.display_mode == "INLINE_CODE"
    assert code_asset.file_name == "code_case_task-multi.py"
    assert Path(code_asset.local_path).exists()
    assert Path(code_asset.local_path).read_text(encoding="utf-8") == code_asset.inline_content
    assert "百炼代码案例" in code_asset.inline_content
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_generation_service_requires_confirmed_slide_outline_text(tmp_path: Path) -> None:
    service = ResourceGenerationService(
        sandbox_root=tmp_path,
        content_chain=ContentGenerationChain(primary_generator=FakePrimaryGenerator()),
    )
    snapshot = SystemSnapshot(
        current_course="数据库原理",
        current_chapter="索引",
        course_progress=0.3,
        student_name="张三",
        student_level="BASIC",
        knowledge_gaps=["B+树"],
        preferred_style="step_by_step",
        recent_mistakes=[],
        session_id="task-1",
        conversation_length=1,
        total_tokens_used=0,
        wiki_pages_count=10,
        last_index_update="2026-05-02",
        recent_activities=[],
    )

    asset = await service.build_asset(
        asset_type="SLIDES",
        params={
            "taskId": "task-confirm-only",
            "query": "联合索引",
            "confirmedSlideOutline": True,
        },
        snapshot=snapshot,
    )

    assert asset.display_mode == "SLIDE_OUTLINE_CONFIRMATION"
    assert asset.local_path is None


@pytest.mark.asyncio
async def test_generation_service_accepts_nested_confirmed_slide_outline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ResourceGenerationService(
        sandbox_root=tmp_path,
        content_chain=ContentGenerationChain(primary_generator=FakePrimaryGenerator()),
    )
    snapshot = SystemSnapshot(
        current_course="Java 程序设计",
        current_chapter="并发编程",
        course_progress=0.3,
        student_name="张三",
        student_level="BASIC",
        knowledge_gaps=["线程创建"],
        preferred_style="step_by_step",
        recent_mistakes=[],
        session_id="task-nested-confirm",
        conversation_length=1,
        total_tokens_used=0,
        wiki_pages_count=10,
        last_index_update="2026-05-02",
        recent_activities=[],
    )

    class FakeMiMoClient:
        def omni_chat_sync(self, **kwargs):
            prompt = kwargs["messages"][-1]["content"]
            assert "用户已确认以下 PPT 大纲" in prompt
            assert "# Java线程创建基础概念学习PPT大纲" in prompt
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"slides":['
                                '{"slideTitle":"线程创建概览","bullets":["Thread","Runnable","适用场景"],"speakerNotes":"先说明 Java 中线程创建的整体方式，帮助学生建立 Thread 与 Runnable 的基本边界。"},'
                                '{"slideTitle":"Thread 类方式","bullets":["继承","重写 run","启动"],"speakerNotes":"讲解继承 Thread 的写法，强调 run 方法承载任务逻辑，start 方法才会创建新线程。"},'
                                '{"slideTitle":"Runnable 接口方式","bullets":["实现接口","传入任务","复用性"],"speakerNotes":"说明 Runnable 把任务与线程对象分离，适合更灵活地复用任务逻辑和组合执行器。"},'
                                '{"slideTitle":"两种方式对比","bullets":["耦合度","继承限制","推荐场景"],"speakerNotes":"对比两种创建方式的设计差异，让学生理解为什么实际开发更常选择 Runnable 或更高层封装。"},'
                                '{"slideTitle":"常见误区","bullets":["直接调用 run","重复 start","忽略异常"],"speakerNotes":"列出初学者常犯问题，特别强调直接调用 run 不会启动新线程，重复 start 会抛出异常。"},'
                                '{"slideTitle":"课堂练习","bullets":["补全代码","判断输出","解释原因"],"speakerNotes":"最后用小练习复盘线程创建流程，让学生通过代码判断和解释巩固核心概念。"}'
                                ']}'
                            )
                        }
                    }
                ]
            }

        def extract_json(self, response):
            import json

            return json.loads(response["choices"][0]["message"]["content"])

    monkeypatch.setenv("MIMO_API_KEY", "unit-test-mimo-key")
    get_settings.cache_clear()
    monkeypatch.setattr("src.ai_modules.llms.mimo_client.MiMoClient", FakeMiMoClient)

    asset = await service.build_asset(
        asset_type="SLIDES",
        params={
            "taskId": "task-nested-confirm",
            "query": "确认此大纲并生成 PPT 文件",
            "learningContext": {
                "activeLearningStepTitle": "Java线程创建基础概念学习",
                "confirmedSlideOutline": "true",
                "confirmedSlideOutlineText": "# Java线程创建基础概念学习PPT大纲\n## 线程创建概览",
            },
        },
        snapshot=snapshot,
    )

    assert asset.display_mode == "DOWNLOAD_CARD"
    assert asset.file_name == "slides_task-nested-confirm.html"
    assert Path(asset.local_path).exists()
    html = Path(asset.local_path).read_text(encoding="utf-8")
    assert "<title>Java线程创建基础概念学习PPT大纲</title>" in html
    assert "线程创建概览" in html
    assert html.count('<section class="slide') == 9
    get_settings.cache_clear()


def test_generation_service_rejects_incomplete_slide_deck() -> None:
    with pytest.raises(RuntimeError, match="empty slide list"):
        ResourceGenerationService._validate_slide_deck([])

    with pytest.raises(RuntimeError, match="6-10 content slides"):
        ResourceGenerationService._validate_slide_deck(
            [
                {
                    "slideTitle": "联合索引概念",
                    "bullets": ["定义", "场景", "限制"],
                    "speakerNotes": "先讲概念。",
                }
            ]
        )


@pytest.mark.asyncio
async def test_generation_service_normalizes_short_slide_bullets_to_download_card(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ResourceGenerationService(
        sandbox_root=tmp_path,
        content_chain=ContentGenerationChain(primary_generator=FakePrimaryGenerator()),
    )
    snapshot = SystemSnapshot(
        current_course="Java",
        current_chapter="Thread",
        course_progress=0.3,
        student_name="student",
        student_level="BASIC",
        knowledge_gaps=["thread"],
        preferred_style="step_by_step",
        recent_mistakes=[],
        session_id="task-short-bullets",
        conversation_length=1,
        total_tokens_used=0,
        wiki_pages_count=10,
        last_index_update="2026-05-02",
        recent_activities=[],
    )

    class FakeMiMoClient:
        def omni_chat_sync(self, **kwargs):
            del kwargs
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"slides":['
                                '{"slideTitle":"Thread 概览","bullets":["定义","启动"],"speakerNotes":"说明线程的基本定义。再解释 start 与 run 的区别，帮助学生建立第一印象。"},'
                                '{"slideTitle":"创建方式","bullets":["继承","接口","任务"],"speakerNotes":"说明常见线程创建方式，并提示实际项目更推荐任务与执行器分离。"},'
                                '{"slideTitle":"生命周期","bullets":["新建","就绪","运行"],"speakerNotes":"讲解线程从创建到运行再到结束的状态变化，帮助学生理解调度过程。"},'
                                '{"slideTitle":"常见误区","bullets":["run","start","异常"],"speakerNotes":"强调直接调用 run 不会创建新线程，重复 start 会触发运行时异常。"},'
                                '{"slideTitle":"实践案例","bullets":["代码","输出","分析"],"speakerNotes":"通过简短代码观察输出顺序，让学生把概念和真实执行联系起来。"},'
                                '{"slideTitle":"总结复盘","bullets":["概念","方式","边界"],"speakerNotes":"回顾线程创建的核心概念、常用方式和容易混淆的边界条件。"}'
                                ']}'
                            )
                        }
                    }
                ]
            }

        def extract_json(self, response):
            import json

            return json.loads(response["choices"][0]["message"]["content"])

    captured: dict[str, list[dict[str, object]]] = {}

    def fake_render_deck(self, **kwargs):
        del self
        captured["slides"] = kwargs["slides"]
        return "<!DOCTYPE html><html><body>deck</body></html>"

    monkeypatch.setenv("MIMO_API_KEY", "unit-test-mimo-key")
    get_settings.cache_clear()
    monkeypatch.setattr("src.ai_modules.llms.mimo_client.MiMoClient", FakeMiMoClient)
    monkeypatch.setattr("src.ai_modules.generation.html_ppt_builder.HtmlPptDeckBuilder.render", fake_render_deck)

    asset = await service.build_asset(
        asset_type="SLIDES",
        params={
            "taskId": "task-short-bullets",
            "query": "确认并生成 PPT",
            "confirmedSlideOutline": True,
            "confirmedSlideOutlineText": "# Thread PPT 大纲",
        },
        snapshot=snapshot,
    )

    assert asset.display_mode == "DOWNLOAD_CARD"
    assert asset.file_name == "slides_task-short-bullets.html"
    assert len(captured["slides"][0]["bullets"]) == 3
    get_settings.cache_clear()


def test_generation_service_truncates_long_slide_bullets() -> None:
    slides = ResourceGenerationService._validate_slide_deck(
        [
            {
                "slideTitle": f"slide {index}",
                "bullets": ["one", "two", "three", "four", "five", "six"],
                "speakerNotes": "speaker notes",
            }
            for index in range(1, 7)
        ]
    )

    assert [len(slide["bullets"]) for slide in slides] == [5, 5, 5, 5, 5, 5]


@pytest.mark.parametrize(
    ("slide_patch", "message"),
    [
        ({"slideTitle": ""}, "missing a title"),
        ({"speakerNotes": ""}, "missing speaker notes"),
    ],
)
def test_generation_service_rejects_core_invalid_slide_fields(slide_patch: dict[str, object], message: str) -> None:
    slides = [
        {
            "slideTitle": f"slide {index}",
            "bullets": ["one", "two", "three"],
            "speakerNotes": "speaker notes",
        }
        for index in range(1, 7)
    ]
    slides[0].update(slide_patch)

    with pytest.raises(RuntimeError, match=message):
        ResourceGenerationService._validate_slide_deck(slides)


@pytest.mark.asyncio
async def test_generation_service_rebuilds_safe_mermaid_mindmap(tmp_path: Path) -> None:
    class BrokenMindmapGenerator(FakePrimaryGenerator):
        async def generate_mindmap_asset(self, **kwargs) -> GeneratedMindMap:
            del kwargs
            return GeneratedMindMap.model_validate(
                {
                    "title": "并发编程思维导图",
                    "summary": "修复 Mermaid 语法问题",
                    "root": '线程池 "核心"',
                    "children": [
                        {
                            "name": '阻塞队列(BlockingQueue)',
                            "children": [{"name": '拒绝策略 "CallerRuns"'}],
                        }
                    ],
                    "mermaid": 'mindmap\n  root((线程池 "核心"))\n    阻塞队列(BlockingQueue)\n',
                }
            )

    service = ResourceGenerationService(
        sandbox_root=tmp_path,
        content_chain=ContentGenerationChain(primary_generator=BrokenMindmapGenerator()),
    )
    snapshot = SystemSnapshot(
        current_course="Java 程序设计",
        current_chapter="并发编程",
        course_progress=0.3,
        student_name="张三",
        student_level="INTERMEDIATE",
        knowledge_gaps=["线程池"],
        preferred_style="visual_first",
        recent_mistakes=[],
        session_id="task-mindmap-safe",
        conversation_length=1,
        total_tokens_used=0,
        wiki_pages_count=10,
        last_index_update="2026-05-02",
        recent_activities=[],
    )

    asset = await service.build_asset(
        asset_type="MINDMAP",
        params={
            "taskId": "task-mindmap-safe",
            "query": "并发编程",
            "rewrittenQuery": "Java 并发编程",
            "retrievalResult": {"documents": [{"title": "线程池", "channel": "hybrid"}]},
        },
        snapshot=snapshot,
    )

    assert asset.display_mode == "INLINE_MERMAID"
    assert asset.inline_content.startswith("mindmap\n")
    assert asset.file_name == "mindmap_task-mindmap-safe.mmd"
    assert Path(asset.local_path).read_text(encoding="utf-8") == asset.inline_content
    assert 'root["线程池 \\"核心\\""]' in asset.inline_content
    assert 'node_1["阻塞队列(BlockingQueue)"]' in asset.inline_content
    assert 'node_2["拒绝策略 \\"CallerRuns\\""]' in asset.inline_content


@pytest.mark.asyncio
async def test_generation_service_requires_tts_audio_for_video_asset(tmp_path: Path) -> None:
    class FailingMimoClient:
        def __init__(self, **kwargs) -> None:
            del kwargs

        async def synthesize_speech(self, **kwargs) -> bytes:
            raise RuntimeError("tts unavailable")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("src.ai_modules.llms.mimo_client.MiMoClient", FailingMimoClient)

    service = ResourceGenerationService(
        sandbox_root=tmp_path,
        content_chain=ContentGenerationChain(primary_generator=FakePrimaryGenerator()),
    )
    snapshot = SystemSnapshot(
        current_course="数据库原理",
        current_chapter="索引",
        course_progress=0.3,
        student_name="张三",
        student_level="BASIC",
        knowledge_gaps=["B+树"],
        preferred_style="visual",
        recent_mistakes=[],
        session_id="task-video",
        conversation_length=1,
        total_tokens_used=0,
        wiki_pages_count=10,
        last_index_update="2026-05-02",
        recent_activities=[],
    )

    with pytest.raises(RuntimeError, match="Video TTS generation failed"):
        await service.build_asset(
            asset_type="VIDEO",
            params={
                "taskId": "task-video",
                "query": "联合索引",
                "topic": "联合索引",
                "style": "hybrid",
                "duration": 60,
            },
            snapshot=snapshot,
        )
    monkeypatch.undo()


@pytest.mark.asyncio
async def test_generation_service_writes_video_asset(
    tmp_path: Path,
) -> None:
    service = ResourceGenerationService(sandbox_root=tmp_path)
    snapshot = SystemSnapshot(
        current_course="数据结构",
        current_chapter="快速排序",
        course_progress=0.3,
        student_name="张三",
        student_level="BASIC",
        knowledge_gaps=["递归"],
        preferred_style="visual",
        recent_mistakes=[],
        session_id="task-video",
        conversation_length=1,
        total_tokens_used=0,
        wiki_pages_count=10,
        last_index_update="2026-05-02",
        recent_activities=[],
    )

    params = {
        "taskId": "task-video",
        "query": "快速排序",
        "topic": "快速排序算法",
        "style": "hybrid",
        "tts_audio_bytes": b"x" * 256,
    }
    asset = await service.build_asset(asset_type="VIDEO", params=params, snapshot=snapshot)

    assert asset.asset_type == "VIDEO"
    assert asset.file_name == "browser-rendered.webm"
    assert asset.local_path is None
    assert Path(asset.thumbnail_path).exists()
    task_payload = params["videoGenerationTask"]
    assert task_payload["videoStyle"] == "hybrid"
    assert Path(params["videoSandboxArtifact"]["scriptJsonPath"]).exists()
    assert params["videoSandboxArtifact"]["audioBase64"]
    assert params["videoSandboxArtifact"]["avatarDataUrl"] == "/dh_live/assets/combined_data.json.gz"


@pytest.mark.asyncio
async def test_generation_service_synthesizes_video_audio_from_final_script(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeMimoClient:
        def __init__(self, **kwargs) -> None:
            captured["timeout_seconds"] = kwargs["timeout_seconds"]

        async def synthesize_speech(self, **kwargs) -> bytes:
            captured["text"] = kwargs["text"]
            return b"y" * 512

    from src.ai_modules.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("TTS_TIMEOUT_SECONDS", "180")
    monkeypatch.setenv("VIDEO_TTS_MAX_CHARS", "260")
    monkeypatch.setattr("src.ai_modules.llms.mimo_client.MiMoClient", FakeMimoClient)

    service = ResourceGenerationService(
        sandbox_root=tmp_path,
        content_chain=ContentGenerationChain(primary_generator=FakePrimaryGenerator()),
    )
    snapshot = SystemSnapshot(
        current_course="Java 程序设计",
        current_chapter="并发编程",
        course_progress=0.3,
        student_name="张三",
        student_level="BASIC",
        knowledge_gaps=["线程同步"],
        preferred_style="visual",
        recent_mistakes=[],
        session_id="task-video",
        conversation_length=1,
        total_tokens_used=0,
        wiki_pages_count=10,
        last_index_update="2026-05-02",
        recent_activities=[],
    )

    params = {
        "taskId": "task-video",
        "query": "并发编程",
        "topic": "并发编程",
        "style": "talking_head",
    }
    asset = await service.build_asset(asset_type="VIDEO", params=params, snapshot=snapshot)

    assert asset.asset_type == "VIDEO"
    assert len(captured["text"]) <= 260
    assert captured["timeout_seconds"] == 180.0
    get_settings.cache_clear()
    assert "回退候选" not in captured["text"]
    assert captured["text"].startswith("今天我们用联合索引来理解最左前缀原则")


@pytest.mark.asyncio
async def test_generation_service_rejects_unknown_asset_type(tmp_path: Path) -> None:
    service = ResourceGenerationService(sandbox_root=tmp_path)
    snapshot = SystemSnapshot(
        current_course="数据库原理",
        current_chapter="索引",
        course_progress=0.3,
        student_name="张三",
        student_level="BASIC",
        knowledge_gaps=["B+树"],
        preferred_style="step_by_step",
        recent_mistakes=[],
        session_id="task-1",
        conversation_length=1,
        total_tokens_used=0,
        wiki_pages_count=10,
        last_index_update="2026-05-02",
        recent_activities=[],
    )

    with pytest.raises(ValueError, match="Unsupported assetType"):
        await service.build_asset(
            asset_type="UNKNOWN",
            params={"taskId": "task-unknown"},
            snapshot=snapshot,
        )


@pytest.mark.asyncio
async def test_structured_generator_uses_spark_openai_compatible_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MODEL_PROVIDER", "spark")
    monkeypatch.setenv("ACTIVE_PROVIDER", "spark")
    monkeypatch.setenv("GENERATION_LLM__PROVIDER", "spark")
    monkeypatch.setenv("GENERATION_LLM__MODEL", "")
    monkeypatch.setenv("SPARK_API_KEY", "spark-test-key")
    monkeypatch.setenv("SPARK_BASE_URL", "https://spark-api-open.xf-yun.com/v1")
    monkeypatch.setenv("SPARK_MODEL_NAME", "generalv3.5")
    get_settings.cache_clear()

    captured: dict[str, object] = {}

    async def fake_post_chat_completion_async(
        self,
        *,
        messages,
        temperature=0.3,
        max_tokens=None,
        response_format=None,
    ):
        captured["provider_name"] = self.provider_name
        captured["base_url"] = self.base_url
        captured["model_name"] = self.model_name
        captured["messages"] = messages
        captured["temperature"] = temperature
        captured["max_tokens"] = max_tokens
        captured["response_format"] = response_format
        return {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"title":"星火阅读","summary":"星火摘要","body":"星火正文"}'
                        )
                    }
                }
            ],
            "usage": {"prompt_tokens": 128, "completion_tokens": 32},
        }

    monkeypatch.setattr(
        OpenAICompatibleStructuredGenerator,
        "_post_chat_completion_async",
        fake_post_chat_completion_async,
    )

    generator = OpenAICompatibleStructuredGenerator()
    asset = await generator.generate_reading_asset(
        title="联合索引延伸阅读",
        topic="联合索引",
        snapshot={"current_course": "数据库原理"},
        sources=[{"title": "数据库索引导学"}],
    )

    assert asset.title == "星火阅读"
    assert asset.summary == "星火摘要"
    assert asset.body == "星火正文"
    assert captured["provider_name"] == "spark"
    assert captured["base_url"] == "https://spark-api-open.xf-yun.com/v1"
    assert captured["model_name"] == "generalv3.5"
    assert captured["max_tokens"] == 1600
    assert captured["response_format"] == {"type": "json_object"}
    assert isinstance(captured["messages"], list)

    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_reading_generation_retries_invalid_schema_with_llm(monkeypatch) -> None:
    calls: list[list[dict[str, str]]] = []

    async def fake_post_chat_completion_async(
        self,
        *,
        messages,
        temperature=0.3,
        max_tokens=None,
        response_format=None,
    ):
        del self, temperature, max_tokens, response_format
        calls.append(messages)
        content = "{}"
        if len(calls) == 2:
            content = '{"title":"LLM fixed reading","summary":"LLM summary","body":"LLM body"}'
        return {"choices": [{"message": {"content": content}}], "usage": {}}

    monkeypatch.setattr(
        OpenAICompatibleStructuredGenerator,
        "_post_chat_completion_async",
        fake_post_chat_completion_async,
    )

    generator = OpenAICompatibleStructuredGenerator(api_key="test-key", max_retries=1, backoff_seconds=0)
    asset = await generator.generate_reading_asset(
        title="reading",
        topic="Java",
        snapshot={"current_course": "Java"},
        sources=[{"title": "Java source"}],
    )

    assert asset.title == "LLM fixed reading"
    assert asset.summary == "LLM summary"
    assert asset.body == "LLM body"
    assert len(calls) == 2
    assert "previous LLM output was invalid".lower() in calls[1][1]["content"].lower()


@pytest.mark.asyncio
async def test_reading_generation_raises_when_llm_schema_remains_invalid(monkeypatch) -> None:
    calls: list[list[dict[str, str]]] = []

    async def fake_post_chat_completion_async(
        self,
        *,
        messages,
        temperature=0.3,
        max_tokens=None,
        response_format=None,
    ):
        del self, temperature, max_tokens, response_format
        calls.append(messages)
        return {"choices": [{"message": {"content": "{}"}}], "usage": {}}

    monkeypatch.setattr(
        OpenAICompatibleStructuredGenerator,
        "_post_chat_completion_async",
        fake_post_chat_completion_async,
    )

    generator = OpenAICompatibleStructuredGenerator(api_key="test-key", max_retries=1, backoff_seconds=0)

    with pytest.raises(GenerationOutputInvalidError):
        await generator.generate_reading_asset(
            title="reading",
            topic="Java",
            snapshot={"current_course": "Java"},
            sources=[{"title": "Java source"}],
        )

    assert len(calls) == 2


@pytest.mark.asyncio
async def test_document_generation_retries_invalid_sections_with_llm(monkeypatch) -> None:
    calls: list[list[dict[str, str]]] = []

    async def fake_post_chat_completion_async(
        self,
        *,
        messages,
        temperature=0.3,
        max_tokens=None,
        response_format=None,
    ):
        del self, temperature, max_tokens, response_format
        calls.append(messages)
        content = "{}"
        if len(calls) == 2:
            content = (
                '{"sections":[{"title":"LLM section","body":"LLM body",'
                '"tips":["LLM tip"],"citations":["LLM citation"]}]}'
            )
        return {"choices": [{"message": {"content": content}}], "usage": {}}

    monkeypatch.setattr(
        OpenAICompatibleStructuredGenerator,
        "_post_chat_completion_async",
        fake_post_chat_completion_async,
    )

    generator = OpenAICompatibleStructuredGenerator(api_key="test-key", max_retries=1, backoff_seconds=0)
    bundle = await generator.generate_document_sections(
        title="document",
        topic="Java",
        snapshot={"current_course": "Java"},
        section_plans=[{"title": "section", "objective": "learn", "sourceTitles": ["Java source"]}],
        sources=[{"title": "Java source"}],
    )

    assert len(calls) == 2
    assert bundle.sections[0].title == "LLM section"
    assert bundle.sections[0].body == "LLM body"


@pytest.mark.asyncio
async def test_document_summary_does_not_claim_retrieval_evidence_without_sources(tmp_path: Path) -> None:
    class FakeContentChain:
        generate_document_sections = AsyncMock(
            return_value=GeneratedSectionBundle.model_validate(
                {
                    "sections": [
                        {
                            "title": "section",
                            "body": "body",
                            "tips": ["tip"],
                            "citations": [],
                        }
                    ]
                }
            )
        )

    service = ResourceGenerationService(sandbox_root=tmp_path, content_chain=FakeContentChain())

    asset = await service._build_document(
        params={"topic": "topic", "taskId": "no-source", "retrievalResult": {"documents": []}},
        snapshot=SystemSnapshot(
            current_course="course",
            current_chapter="chapter",
            course_progress=0.2,
            student_name="student",
            student_level="BASIC",
            knowledge_gaps=[],
        ),
    )

    assert "检索证据" not in asset.summary
    assert asset.summary == "结构化课程导学文档"
