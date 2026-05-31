import pytest

from src.ai_modules.config import Settings
from src.ai_modules.runtime import SystemSnapshot
from src.ai_modules.runtime import conversation_planner as planner_module
from src.ai_modules.runtime.conversation_planner import ConversationPlanner


class _FakeGenerator:
    async def generate(self, **kwargs):
        del kwargs
        return {
            "goal": "系统学习动态规划",
            "steps": [
                {
                    "stepId": "retrieve",
                    "title": "检索证据",
                    "intent": "检索动态规划相关证据",
                    "agentName": "retrieval",
                },
                {
                    "stepId": "resource",
                    "title": "生成学习包",
                    "intent": "生成专项资源",
                    "serviceType": "RESOURCE_GENERATION",
                    "qualityGate": "critic",
                },
            ],
        }


def _snapshot() -> SystemSnapshot:
    return SystemSnapshot(
        current_course="数据结构",
        current_chapter="动态规划",
        course_progress=0.4,
        student_name="张三",
        student_level="BASIC",
        knowledge_gaps=["状态转移"],
        preferred_style="step_by_step",
        recent_mistakes=[],
        session_id="conv-plan",
        conversation_length=2,
        total_tokens_used=128,
        wiki_pages_count=5,
        last_index_update="2026-05-30",
        recent_activities=[],
    )


@pytest.mark.asyncio
async def test_conversation_planner_accepts_real_llm_json(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(OPENAI_COMPATIBLE_API_KEY="test-key", PLANNING_LLM={"model": "test-model"})
    monkeypatch.setattr(planner_module, "get_settings", lambda: settings)
    planner = ConversationPlanner(
        allowed_agent_names={"retrieval", "tutor", "critic"},
        generator=_FakeGenerator(),
    )

    plan = await planner.plan(
        service_type="TUTORING",
        params={"query": "我想系统学习动态规划"},
        snapshot=_snapshot(),
        route_agent_names=["query_rewrite", "retrieval", "tutor"],
    )

    assert plan.created_by == "llm_planner"
    assert plan.provider == "openai_compatible"
    assert plan.model == "test-model"
    assert plan.steps[1].service_type == "RESOURCE_GENERATION"


def test_conversation_planner_fails_when_provider_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        planner_module,
        "get_settings",
        lambda: Settings(
            ACTIVE_PROVIDER="openai_compatible",
            FALLBACK_PROVIDER="",
            OPENAI_COMPATIBLE_API_KEY="",
            MIMO_API_KEY="",
            SPARK_API_KEY="",
        ),
    )

    with pytest.raises(RuntimeError, match="planning_llm provider is not ready"):
        ConversationPlanner(allowed_agent_names={"retrieval"}, generator=_FakeGenerator())


@pytest.mark.asyncio
async def test_conversation_planner_rejects_illegal_step(monkeypatch: pytest.MonkeyPatch) -> None:
    class BadGenerator:
        async def generate(self, **kwargs):
            del kwargs
            return {
                "goal": "非法计划",
                "steps": [
                    {
                        "stepId": "shell",
                        "title": "执行命令",
                        "intent": "不允许的工具",
                        "serviceType": "RUN_SHELL",
                    }
                ],
            }

    settings = Settings(OPENAI_COMPATIBLE_API_KEY="test-key")
    monkeypatch.setattr(planner_module, "get_settings", lambda: settings)
    planner = ConversationPlanner(
        allowed_agent_names={"retrieval"},
        generator=BadGenerator(),
    )

    with pytest.raises(ValueError, match="unsupported serviceType"):
        await planner.plan(
            service_type="TUTORING",
            params={"query": "非法计划"},
            snapshot=_snapshot(),
            route_agent_names=["retrieval"],
        )
