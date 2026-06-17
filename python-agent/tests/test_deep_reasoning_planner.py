import pytest

from src.ai_modules.agents.deep_reasoning_planner import DeepReasoningPlanner
from src.ai_modules.runtime import SystemSnapshot


def _snapshot() -> SystemSnapshot:
    return SystemSnapshot(
        current_course="数据库原理",
        current_chapter="索引",
        course_progress=0.3,
        student_name="张三",
        student_level="BASIC",
    )


class _FakePlannerGenerator:
    async def generate(self, **kwargs):
        assert "联合索引" in kwargs["user_prompt"]
        return {
            "problemFrame": "解释联合索引最左匹配的原因和边界。",
            "assumptions": ["用户已了解基本索引概念"],
            "evidenceIds": [],
            "missingInfo": ["具体数据库版本未知"],
            "reasoningPlan": ["先讲 B+ 树顺序", "再讲失效边界"],
            "critiqueChecks": ["确认是否回答了为什么"],
            "answerConstraints": ["不要泄露内部推理"],
        }


class _FailingPlannerGenerator:
    async def generate(self, **kwargs):
        del kwargs
        raise RuntimeError("planner unavailable")


@pytest.mark.asyncio
async def test_deep_reasoning_planner_writes_normalized_context() -> None:
    planner = DeepReasoningPlanner(generator=_FakePlannerGenerator())
    params = {
        "query": "联合索引为什么遵循最左匹配?",
        "retrievalResult": {
            "documents": [
                {"title": "联合索引导学", "slug": "idx-leftmost", "evidence": "索引按字段顺序组织。"}
            ],
            "sourcesSummary": "命中联合索引导学。",
        },
    }

    events = [
        event
        async for event in planner.run(
            task_id="task-deep-plan",
            trace_id="trace-deep-plan",
            seq=3,
            service_type="TUTORING",
            params=params,
            snapshot=_snapshot(),
            system_prompt=planner.system_prompt(_snapshot()),
        )
    ]

    assert [event.event for event in events] == ["progress"]
    assert params["deepReasoningContext"]["problemFrame"] == "解释联合索引最左匹配的原因和边界。"
    assert params["deepReasoningContext"]["evidenceIds"] == ["idx-leftmost"]
    assert params["deepReasoningContext"]["reasoningPlan"] == ["先讲 B+ 树顺序", "再讲失效边界"]


@pytest.mark.asyncio
async def test_deep_reasoning_planner_degrades_to_empty_context_on_failure() -> None:
    planner = DeepReasoningPlanner(generator=_FailingPlannerGenerator())
    params = {"query": "联合索引为什么遵循最左匹配?"}

    events = [
        event
        async for event in planner.run(
            task_id="task-deep-plan-fallback",
            trace_id="trace-deep-plan-fallback",
            seq=1,
            service_type="TUTORING",
            params=params,
            snapshot=_snapshot(),
            system_prompt=planner.system_prompt(_snapshot()),
        )
    ]

    assert [event.event for event in events] == ["progress"]
    assert params["deepReasoningContext"] == {}
