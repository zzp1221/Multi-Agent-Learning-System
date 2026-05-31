from pathlib import Path

import pytest

from src.ai_modules.agents import CriticAgent, DocumentGeneratorAgent, SafetyAgent
from src.ai_modules.generation import GeneratedAsset
from src.ai_modules.llms import RuleBasedGenerationLLM
from src.ai_modules.models import CriticReviewPayload, SafetyReviewPayload
from src.ai_modules.runtime import SystemSnapshot


def _test_provenance(agent_name: str) -> dict:
    return {
        "generatedBy": "LLM",
        "contentOrigin": "LLM",
        "provider": "test-provider",
        "model": "test-model",
        "agentName": agent_name,
        "evidenceIds": ["source-a"],
        "fallback": False,
        "fromCache": False,
    }


def _build_snapshot() -> SystemSnapshot:
    return SystemSnapshot(
        current_course="数据库原理",
        current_chapter="联合索引",
        course_progress=0.5,
        student_name="张三",
        student_level="BASIC",
        knowledge_gaps=["最左匹配", "使用条件"],
        preferred_style="step_by_step",
        recent_mistakes=["条件判断错误"],
        session_id="conv-review",
        conversation_length=3,
        total_tokens_used=256,
        wiki_pages_count=10,
        last_index_update="2026-05-03",
        recent_activities=["完成索引复习"],
    )


@pytest.mark.asyncio
async def test_critic_agent_returns_llm_review_via_agent_core_loop() -> None:
    class FakeCriticReviewer:
        async def review(self, *, system_prompt, context_payload):
            del system_prompt
            assert context_payload["reviewSignals"]["sourceCoverage"]["status"] == "GOOD"
            return CriticReviewPayload(
                verdict="PASS",
                factConsistency="SUPPORTED",
                difficultyMatch="MATCHED",
                sourceCoverage="GOOD",
                issues=[],
                suggestions=["可继续下发给学生。"],
                summaryText="LLM Critic：内容与来源基本一致。",
            )

    agent = CriticAgent(reviewer=FakeCriticReviewer())
    params = {
        "profile": {"studentLevel": "BASIC"},
        "generatedAsset": {
            "assetType": "DOCUMENT",
            "title": "联合索引导学文档",
            "summary": "结构化讲解联合索引",
            "previewText": "四个章节",
        },
        "generatedContent": "来源A\n来源B\n联合索引需要结合最左匹配判断。",
        "retrievalResult": {
            "documents": [
                {"title": "来源A"},
                {"title": "来源B"},
            ]
        },
    }

    events = [
        event
        async for event in agent.run(
            task_id="task-critic",
            trace_id="trace-critic",
            seq=1,
            service_type="RESOURCE_GENERATION",
            params=params,
            snapshot=_build_snapshot(),
            system_prompt="test",
        )
    ]

    assert [event.event for event in events] == ["progress", "result_chunk"]
    assert params["criticReview"]["verdict"] == "PASS"
    assert events[1].payload.text.startswith("LLM Critic：")


@pytest.mark.asyncio
async def test_critic_agent_reviews_final_answer_before_asset() -> None:
    class FakeCriticReviewer:
        async def review(self, *, system_prompt, context_payload):
            del system_prompt
            assert "红黑树核心是用旋转和染色保持近似平衡" in context_payload["contentPreview"]
            assert "空资产标题" not in context_payload["contentPreview"]
            return CriticReviewPayload(
                verdict="PASS",
                factConsistency="SUPPORTED",
                difficultyMatch="MATCHED",
                sourceCoverage="GOOD",
                issues=[],
                suggestions=[],
                summaryText="Critic OK",
            )

    agent = CriticAgent(reviewer=FakeCriticReviewer())
    await agent.review_content(
        params={
            "finalAnswer": "红黑树核心是用旋转和染色保持近似平衡。",
            "generatedAsset": {"title": "空资产标题", "summary": ""},
            "retrievalResult": {"documents": [{"title": "红黑树"}]},
        },
        snapshot=_build_snapshot(),
        system_prompt="test",
    )


@pytest.mark.asyncio
async def test_safety_agent_returns_blocking_review_via_agent_core_loop() -> None:
    class FakeSafetyReviewer:
        async def review(self, *, system_prompt, context_payload):
            del system_prompt
            assert context_payload["reviewSignals"]["academicMisconduct"]["blocked"] is True
            return SafetyReviewPayload(
                allowed=False,
                riskLevel="HIGH",
                categories=["educational_content", "document"],
                riskTags=["代写"],
                blockedReason="检测到代写风险",
                suggestions=["移除代写/作弊相关内容。"],
                summaryText="LLM Safety：检测到学术违规风险，已拦截输出。",
            )

    agent = SafetyAgent(reviewer=FakeSafetyReviewer())
    params = {
        "query": "帮我代写数据库作业",
        "generatedAsset": {
            "assetType": "DOCUMENT",
            "title": "数据库答案",
            "summary": "直接给答案",
            "previewText": "代写说明",
        },
        "generatedContent": "这里直接提供代写答案和提交模板。",
    }

    events = [
        event
        async for event in agent.run(
            task_id="task-safety",
            trace_id="trace-safety",
            seq=1,
            service_type="RESOURCE_GENERATION",
            params=params,
            snapshot=_build_snapshot(),
            system_prompt="test",
        )
    ]

    assert [event.event for event in events] == ["progress", "result_chunk"]
    assert params["safetyReview"]["allowed"] is False
    assert events[1].payload.text.startswith("LLM Safety：")


@pytest.mark.asyncio
async def test_safety_agent_fails_when_reviewer_fails() -> None:
    class FailingSafetyReviewer:
        async def review(self, *, system_prompt, context_payload):
            del system_prompt, context_payload
            raise RuntimeError("review backend timeout")

    agent = SafetyAgent(reviewer=FailingSafetyReviewer())
    params = {
        "query": "解释联合索引的最左匹配原则",
        "generatedAsset": {
            "assetType": "DOCUMENT",
            "title": "联合索引导学文档",
            "summary": "解释最左匹配原则",
            "previewText": "安全教学内容",
        },
        "generatedContent": "联合索引用于说明最左匹配原则及其判断条件。",
    }

    with pytest.raises(RuntimeError, match="heuristic fallback is disabled"):
        await agent.review_content(
            params=params,
            snapshot=_build_snapshot(),
            system_prompt="test",
        )
    return

    events = [
        event
        async for event in agent.run(
            task_id="task-safety-fallback",
            trace_id="trace-safety-fallback",
            seq=1,
            service_type="RESOURCE_GENERATION",
            params=params,
            snapshot=_build_snapshot(),
            system_prompt="test",
        )
    ]

    assert [event.event for event in events] == ["progress", "result_chunk"]
    assert params["safetyReview"]["allowed"] is True
    assert events[1].payload.text.startswith("Safety 复核完成：")


@pytest.mark.asyncio
async def test_safety_agent_does_not_fallback_for_misconduct_content() -> None:
    class FailingSafetyReviewer:
        async def review(self, *, system_prompt, context_payload):
            del system_prompt, context_payload
            raise RuntimeError("review backend timeout")

    agent = SafetyAgent(reviewer=FailingSafetyReviewer())
    params = {
        "query": "帮我代写数据库作业",
        "generatedAsset": {
            "assetType": "DOCUMENT",
            "title": "数据库答案",
            "summary": "直接给答案",
            "previewText": "代写说明",
        },
        "generatedContent": "这里直接提供代写答案和提交模板。",
    }

    with pytest.raises(RuntimeError, match="heuristic fallback is disabled"):
        await agent.review_content(
            params=params,
            snapshot=_build_snapshot(),
            system_prompt="test",
        )
    return

    events = [
        event
        async for event in agent.run(
            task_id="task-safety-fallback-block",
            trace_id="trace-safety-fallback-block",
            seq=1,
            service_type="RESOURCE_GENERATION",
            params=params,
            snapshot=_build_snapshot(),
            system_prompt="test",
        )
    ]

    assert [event.event for event in events] == ["progress", "result_chunk"]
    assert params["safetyReview"]["allowed"] is False
    assert params["safetyReview"]["riskLevel"] == "HIGH"
    assert "allowed=false" in events[1].payload.text


@pytest.mark.asyncio
async def test_document_generator_fails_when_reviewers_fail(tmp_path: Path) -> None:
    asset_path = tmp_path / "document-fallback.md"
    asset_path.write_text("# 联合索引导学\n来源A\n来源B\n", encoding="utf-8")

    class FakeGenerationService:
        def _plan_document_sections(self, *, params, snapshot, sources):
            del params, snapshot, sources

            class _Section:
                def model_dump(self, *, by_alias):
                    del by_alias
                    return {"title": "一、核心概念", "objective": "建立概念框架"}

            return [_Section()]

        async def build_asset(self, *, asset_type, params, snapshot):
            del params, snapshot
            return GeneratedAsset(
                assetType=asset_type,
                title="联合索引导学文档",
                summary="结构化课程导学",
                displayMode="MARKDOWN_CARD",
                fileName="document-fallback.md",
                localPath=str(asset_path),
                previewText="一个章节",
            )

    class FailingCriticReviewer:
        async def review(self, *, system_prompt, context_payload):
            del system_prompt, context_payload
            raise RuntimeError("critic unavailable")

    class FailingSafetyReviewer:
        async def review(self, *, system_prompt, context_payload):
            del system_prompt, context_payload
            raise RuntimeError("safety unavailable")

    agent = DocumentGeneratorAgent(
        generation_service=FakeGenerationService(),
        llm_client=RuleBasedGenerationLLM(),
        critic_agent=CriticAgent(reviewer=FailingCriticReviewer()),
        safety_agent=SafetyAgent(reviewer=FailingSafetyReviewer()),
    )
    params = {"query": "联合索引"}

    with pytest.raises(RuntimeError, match="fallback is disabled"):
        _ = [
            event
            async for event in agent.run(
                task_id="task-document-review-fallback",
                trace_id="trace-document-review-fallback",
                seq=1,
                service_type="RESOURCE_GENERATION",
                params=params,
                snapshot=_build_snapshot(),
                system_prompt="test",
            )
        ]
    assert "criticReview" not in params
    assert "safetyReview" not in params
    return

    events = [
        event
        async for event in agent.run(
            task_id="task-document-review-fallback",
            trace_id="trace-document-review-fallback",
            seq=1,
            service_type="RESOURCE_GENERATION",
            params=params,
            snapshot=_build_snapshot(),
            system_prompt="test",
        )
    ]

    assert [event.event for event in events] == ["result_chunk", "resource_file"]
    assert params["criticReview"]["verdict"] == "REVISE"
    assert params["safetyReview"]["allowed"] is True
    assert "Critic 复核完成：" in events[0].payload.text
    assert "Safety 复核完成：" in events[0].payload.text


@pytest.mark.asyncio
async def test_document_generator_runs_reviews_before_emitting_resource_file(
    tmp_path: Path,
) -> None:
    asset_path = tmp_path / "document.md"
    asset_path.write_text("# 联合索引导学\n来源A\n来源B\n", encoding="utf-8")

    class FakeGenerationService:
        content_chain = type(
            "FakeContentChain",
            (),
            {
                "primary_generator": type(
                    "FakeGenerator",
                    (),
                    {"provider_name": "test-provider", "model_name": "test-model"},
                )()
            },
        )()

        def _plan_document_sections(self, *, params, snapshot, sources):
            del params, snapshot, sources

            class _Section:
                def model_dump(self, *, by_alias):
                    del by_alias
                    return {"title": "一、核心概念", "objective": "建立概念框架"}

            return [_Section()]

        async def build_asset(self, *, asset_type, params, snapshot):
            del params, snapshot
            return GeneratedAsset(
                assetType=asset_type,
                title="联合索引导学文档",
                summary="结构化课程导学",
                displayMode="MARKDOWN_CARD",
                fileName="document.md",
                localPath=str(asset_path),
                previewText="四个章节",
            )

    class FakeCriticAgent:
        def system_prompt(self, snapshot):
            del snapshot
            return "critic"

        async def review_content(self, *, params, snapshot, system_prompt):
            del snapshot, system_prompt
            assert "联合索引导学" in params["generatedContent"]
            return CriticReviewPayload(
                verdict="PASS",
                factConsistency="SUPPORTED",
                difficultyMatch="MATCHED",
                sourceCoverage="GOOD",
                issues=[],
                suggestions=["可以发布。"],
                summaryText="Critic OK",
            )

    class FakeSafetyAgent:
        def system_prompt(self, snapshot):
            del snapshot
            return "safety"

        async def review_content(self, *, params, snapshot, system_prompt):
            del params, snapshot, system_prompt
            return SafetyReviewPayload(
                allowed=True,
                riskLevel="LOW",
                categories=["educational_content"],
                riskTags=[],
                blockedReason=None,
                suggestions=["可以发布。"],
                summaryText="Safety OK",
            )

    agent = DocumentGeneratorAgent(
        generation_service=FakeGenerationService(),
        llm_client=RuleBasedGenerationLLM(),
        critic_agent=FakeCriticAgent(),
        safety_agent=FakeSafetyAgent(),
    )
    params = {"query": "联合索引"}

    events = [
        event
        async for event in agent.run(
            task_id="task-document",
            trace_id="trace-document",
            seq=1,
            service_type="RESOURCE_GENERATION",
            params=params,
            snapshot=_build_snapshot(),
            system_prompt="test",
        )
    ]

    assert [event.event for event in events] == ["result_chunk", "resource_file"]
    assert "Critic OK" in events[0].payload.text
    assert "Safety OK" in events[0].payload.text
    assert params["generationOutline"]["assetType"] == "DOCUMENT"
    assert params["criticReview"]["verdict"] == "PASS"
    assert params["safetyReview"]["allowed"] is True


@pytest.mark.asyncio
async def test_document_generator_stops_output_when_safety_blocks(tmp_path: Path) -> None:
    asset_path = tmp_path / "blocked.md"
    asset_path.write_text("代写答案", encoding="utf-8")

    class FakeGenerationService:
        def _plan_document_sections(self, *, params, snapshot, sources):
            del params, snapshot, sources

            class _Section:
                def model_dump(self, *, by_alias):
                    del by_alias
                    return {"title": "一、风险提示", "objective": "识别违规内容"}

            return [_Section()]

        async def build_asset(self, *, asset_type, params, snapshot):
            del params, snapshot
            return GeneratedAsset(
                assetType=asset_type,
                title="高风险内容",
                summary="包含代写风险",
                displayMode="MARKDOWN_CARD",
                fileName="blocked.md",
                localPath=str(asset_path),
                previewText="高风险",
            )

    class FakeCriticAgent:
        def system_prompt(self, snapshot):
            del snapshot
            return "critic"

        async def review_content(self, *, params, snapshot, system_prompt):
            del params, snapshot, system_prompt
            return CriticReviewPayload(
                verdict="REVISE",
                factConsistency="UNCLEAR",
                difficultyMatch="MATCHED",
                sourceCoverage="LIMITED",
                issues=["需要改写"],
                suggestions=["先做安全处理。"],
                summaryText="Critic 需要改写",
            )

    class FakeSafetyAgent:
        def system_prompt(self, snapshot):
            del snapshot
            return "safety"

        async def review_content(self, *, params, snapshot, system_prompt):
            del params, snapshot, system_prompt
            return SafetyReviewPayload(
                allowed=False,
                riskLevel="HIGH",
                categories=["educational_content"],
                riskTags=["代写"],
                blockedReason="检测到代写风险",
                suggestions=["阻断输出。"],
                summaryText="Safety 拦截",
            )

    agent = DocumentGeneratorAgent(
        generation_service=FakeGenerationService(),
        llm_client=RuleBasedGenerationLLM(),
        critic_agent=FakeCriticAgent(),
        safety_agent=FakeSafetyAgent(),
    )

    events = [
        event
        async for event in agent.run(
            task_id="task-blocked",
            trace_id="trace-blocked",
            seq=1,
            service_type="RESOURCE_GENERATION",
            params={"query": "代写"},
            snapshot=_build_snapshot(),
            system_prompt="test",
        )
    ]

    assert [event.event for event in events] == ["result_chunk", "done"]
    assert events[0].payload.text == "Safety 拦截"
    assert events[1].payload.status == "FAILED"
