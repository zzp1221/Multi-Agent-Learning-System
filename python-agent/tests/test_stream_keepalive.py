import asyncio

import pytest

from src.ai_modules.agents import DocumentGeneratorAgent, JudgeAgent
from src.ai_modules.generation import GeneratedAsset
from src.ai_modules.models import CriticReviewPayload, SafetyReviewPayload
from src.ai_modules.runtime import SystemSnapshot


def _build_snapshot() -> SystemSnapshot:
    return SystemSnapshot(
        current_course="数据库原理",
        current_chapter="联合索引",
        course_progress=0.5,
        student_name="张三",
        student_level="BASIC",
        knowledge_gaps=["最左匹配"],
        preferred_style="step_by_step",
        recent_mistakes=["条件判断错误"],
        session_id="conv-keepalive",
        conversation_length=3,
        total_tokens_used=256,
        wiki_pages_count=10,
        last_index_update="2026-05-03",
        recent_activities=["完成索引复习"],
    )


@pytest.mark.asyncio
async def test_document_generator_agent_emits_keepalive_progress_when_generation_is_slow(monkeypatch) -> None:
    agent = DocumentGeneratorAgent()
    agent.heartbeat_interval_seconds = 0.01

    async def slow_generation(**kwargs):
        del kwargs
        await asyncio.sleep(0.03)
        return {
            "asset": GeneratedAsset(
                assetType="DOCUMENT",
                title="联合索引导学文档",
                summary="文档已生成",
                displayMode="MARKDOWN_CARD",
                fileName="document_guide_task.md",
                localPath="sandbox-temp/document_guide_task.md",
                previewText="联合索引导学文档预览",
                generatedBy="LLM",
                contentOrigin="LLM",
                provider="test-provider",
                model="test-model",
                agentName="document_generation",
                evidenceIds=["test-evidence"],
                fallback=False,
                fromCache=False,
            ),
            "criticReview": CriticReviewPayload(
                verdict="PASS",
                factConsistency="SUPPORTED",
                difficultyMatch="MATCHED",
                sourceCoverage="GOOD",
                issues=[],
                suggestions=["内容整体可用"],
                summaryText="Critic 复核完成。",
            ),
            "safetyReview": SafetyReviewPayload(
                allowed=True,
                riskLevel="LOW",
                categories=["educational_content"],
                riskTags=[],
                suggestions=["内容整体安全"],
                summaryText="Safety 复核完成。",
            ),
        }

    monkeypatch.setattr(agent, "_run_agent_core_loop", slow_generation)

    events = [
        event
        async for event in agent.run(
            task_id="task-resource-keepalive",
            trace_id="trace-resource-keepalive",
            seq=1,
            service_type="RESOURCE_GENERATION",
            params={"query": "联合索引"},
            snapshot=_build_snapshot(),
            system_prompt="test",
        )
    ]

    assert any(
        event.event == "progress" and event.payload.message == "资源生成仍在执行中，请稍候"
        for event in events
    )
    assert any(event.event == "result_chunk" for event in events)
    assert events[-1].event == "resource_file"


@pytest.mark.asyncio
async def test_judge_agent_emits_keepalive_progress_when_judging_is_slow(monkeypatch) -> None:
    agent = JudgeAgent(heartbeat_interval_seconds=0.01)

    async def slow_judge(**kwargs):
        del kwargs
        await asyncio.sleep(0.03)
        return {
            "title": "联合索引判题结果",
            "summary": "判题完成，仍需加强条件判断。",
            "totalScore": 40.0,
            "accuracy": 0.4,
            "items": [],
            "weakKnowledgeTags": ["最左匹配", "使用条件"],
        }

    monkeypatch.setattr(agent, "_run_agent_core_loop", slow_judge)
    params = {
        "topic": "联合索引",
        "answers": {"q1": "C"},
    }

    events = [
        event
        async for event in agent.run(
            task_id="task-judge-keepalive",
            trace_id="trace-judge-keepalive",
            seq=1,
            service_type="PRACTICE_JUDGE",
            params=params,
            snapshot=_build_snapshot(),
            system_prompt="test",
        )
    ]

    assert any(
        event.event == "progress" and event.payload.message == "判题仍在执行中，请稍候"
        for event in events
    )
    assert events[-2].event == "progress"
    assert events[-2].payload.message == "已完成判题并生成反馈"
    assert events[-1].event == "judge_result"
