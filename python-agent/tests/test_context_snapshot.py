import pytest

from src.ai_modules.models import EngineStreamRequest
from src.ai_modules.runtime import SnapshotBuilder
from src.ai_modules.supervisor import PythonAgentSupervisor


@pytest.mark.asyncio
async def test_snapshot_builder_extracts_context_from_request_params() -> None:
    builder = SnapshotBuilder()

    snapshot = await builder.build(
        user_id="user-001",
        task_id="task-001",
        conversation_id="conv-001",
        params={
            "learningContext": {
                "course": "数据库原理",
                "chapter": "索引",
                "progress": 0.35,
            },
            "profile": {
                "studentName": "张三",
                "studentLevel": "BASIC",
                "knowledgeGaps": ["B+树", "联合索引"],
                "preferredStyle": "example_first",
            },
            "recentActivities": ["完成索引练习 1"],
            "wikiPagesCount": 270,
        },
    )

    assert snapshot.current_course == "数据库原理"
    assert snapshot.student_name == "张三"
    assert snapshot.knowledge_gaps == ["B+树", "联合索引"]


@pytest.mark.asyncio
async def test_snapshot_builder_prefers_profile_analysis_from_current_route() -> None:
    builder = SnapshotBuilder()

    snapshot = await builder.build(
        user_id="user-001",
        task_id="task-001",
        conversation_id="conv-001",
        params={
            "learningContext": {"course": "course", "chapter": "chapter"},
            "profile": {
                "studentName": "student",
                "studentLevel": "BASIC",
                "knowledgeGaps": ["old-gap"],
                "preferredStyle": "old-style",
            },
            "profileAnalysis": {
                "studentLevel": "ADVANCED",
                "weakPoints": ["new-gap"],
                "learningPreference": "new-style",
            },
        },
    )

    assert snapshot.student_level == "ADVANCED"
    assert snapshot.knowledge_gaps == ["new-gap"]
    assert snapshot.preferred_style == "new-style"


@pytest.mark.asyncio
async def test_snapshot_builder_ignores_empty_profile_analysis_values() -> None:
    builder = SnapshotBuilder()

    snapshot = await builder.build(
        user_id="user-001",
        task_id="task-001",
        conversation_id="conv-001",
        params={
            "learningContext": {"course": "course", "chapter": "chapter"},
            "profile": {
                "studentName": "student",
                "studentLevel": "BASIC",
                "knowledgeGaps": ["old-gap"],
                "preferredStyle": "old-style",
            },
            "profileAnalysis": {
                "studentLevel": None,
                "weakPoints": [],
                "learningPreference": "",
            },
        },
    )

    assert snapshot.student_level == "BASIC"
    assert snapshot.knowledge_gaps == ["old-gap"]
    assert snapshot.preferred_style == "old-style"


@pytest.mark.asyncio
async def test_supervisor_builds_prompt_with_snapshot_context() -> None:
    supervisor = PythonAgentSupervisor()
    request = EngineStreamRequest(
        serviceType="RESOURCE_GENERATION",
        params={
            "resourceType": "DOCUMENT",
            "learningContext": {
                "course": "数据库原理",
                "chapter": "索引",
                "progress": 0.35,
            },
            "profile": {
                "studentName": "张三",
                "studentLevel": "BASIC",
                "knowledgeGaps": ["B+树"],
            },
        },
        taskId="task-ctx",
        traceId="trace-ctx",
    )

    snapshot = await supervisor.build_snapshot(request)
    prompt = supervisor.build_agent_system_prompt(
        agent_name="document_generator",
        snapshot=snapshot,
    )

    assert "## 当前上下文" in prompt
    assert "课程: 数据库原理" in prompt
    assert "章节: 索引" in prompt
    assert "学生: 张三" in prompt
