import pytest

from src.ai_modules.agents.evaluation_agent import EvaluationAgent
from src.ai_modules.agents.path_planning_agent import PathPlanningAgent
from src.ai_modules.agents.query_rewrite_agent import QueryRewriteAgent
from src.ai_modules.agents.retrieval_agent import RetrievalAgent
from src.ai_modules.llms import RuleBasedQueryRewriteLLM
from src.ai_modules.memory import InMemoryLearningPlanStore
from src.ai_modules.models import EvaluationPayload, LearningPlanPayload, QueryRewriteResult, QuestionBatchPayload
from src.ai_modules.retrieval import HybridRetrievalService
from src.ai_modules.runtime import SystemSnapshot
from src.ai_modules.runtime.skill_loader import SkillPromptLoader


class _UnusedPlanningLLM:
    async def complete(self, **kwargs):  # pragma: no cover - direct-generator tests.
        del kwargs
        raise AssertionError("planning LLM should not be called in this test")


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
        session_id="conv-routing",
        conversation_length=3,
        total_tokens_used=256,
        wiki_pages_count=10,
        last_index_update="2026-05-03",
        recent_activities=["完成索引复习"],
    )


def test_evaluation_agent_system_prompt_loads_skill_and_context() -> None:
    agent = EvaluationAgent(llm_client=_UnusedPlanningLLM())

    prompt = agent.system_prompt(_build_snapshot())

    assert "# 评估智能体" in prompt
    assert "输出契约" in prompt
    assert "EvaluationPayload" in prompt
    assert "## 当前上下文" in prompt
    assert f"课程: {_build_snapshot().current_course}" in prompt


def test_evaluation_agent_uses_profile_analysis_in_aggregated_context() -> None:
    agent = EvaluationAgent(llm_client=_UnusedPlanningLLM())
    params = {
        "profile": {"knowledgeGaps": ["old-gap"]},
        "profileAnalysis": {
            "weakPoints": ["new-gap"],
            "studentLevel": "ADVANCED",
        },
    }

    aggregated = agent._tool_aggregate_behavior(
        tool_input={},
        params=params,
        snapshot=_build_snapshot(),
    )
    llm_context = agent._build_context_payload(
        params=params,
        snapshot=_build_snapshot(),
        aggregated_behavior=aggregated,
    )

    assert "new-gap" in aggregated["candidateWeaknesses"]
    assert llm_context["profile"]["studentLevel"] == "ADVANCED"


def test_evaluation_agent_ignores_empty_profile_analysis_values() -> None:
    agent = EvaluationAgent(llm_client=_UnusedPlanningLLM())
    params = {
        "profile": {
            "knowledgeGaps": ["persisted-gap"],
            "preferredResourceTypes": ["READING"],
            "learningPreference": "example_first",
        },
        "profileAnalysis": {
            "weakPoints": [],
            "preferredResourceTypes": [],
            "learningPreference": None,
        },
    }

    profile = agent._resolve_profile_context(params)

    assert profile["knowledgeGaps"] == ["persisted-gap"]
    assert profile["preferredResourceTypes"] == ["READING"]
    assert profile["learningPreference"] == "example_first"


def test_evaluation_skill_prompt_falls_back_when_skill_is_missing(tmp_path) -> None:
    loader = SkillPromptLoader(skills_root=tmp_path)

    prompt = loader.build_system_prompt(
        skill_name="evaluation",
        snapshot=_build_snapshot(),
        fallback_prompt="评估提示词兜底内容",
    )

    assert prompt == "评估提示词兜底内容"


def test_path_planning_agent_system_prompt_loads_skill_and_context() -> None:
    agent = PathPlanningAgent(
        llm_client=_UnusedPlanningLLM(),
        learning_plan_store=InMemoryLearningPlanStore(),
    )

    prompt = agent.system_prompt(_build_snapshot())

    assert "# 路径规划智能体" in prompt
    assert "输出契约" in prompt
    assert "LearningPlanPayload" in prompt
    assert "## 当前上下文" in prompt
    assert f"课程: {_build_snapshot().current_course}" in prompt


def test_path_planning_skill_prompt_falls_back_when_skill_is_missing(tmp_path) -> None:
    loader = SkillPromptLoader(skills_root=tmp_path)

    prompt = loader.build_system_prompt(
        skill_name="path_planning",
        snapshot=_build_snapshot(),
        fallback_prompt="路径规划提示词兜底内容",
    )

    assert prompt == "路径规划提示词兜底内容"


def test_query_rewrite_agent_system_prompt_loads_skill_and_context() -> None:
    agent = QueryRewriteAgent(llm_client=RuleBasedQueryRewriteLLM())

    prompt = agent.system_prompt(_build_snapshot())

    assert "# 查询改写智能体" in prompt
    assert "输出契约" in prompt
    assert "QueryRewriteResult" in prompt
    assert "## 当前上下文" in prompt
    assert f"课程: {_build_snapshot().current_course}" in prompt


def test_query_rewrite_skill_prompt_falls_back_when_skill_is_missing(tmp_path) -> None:
    loader = SkillPromptLoader(skills_root=tmp_path)

    prompt = loader.build_system_prompt(
        skill_name="query_rewrite",
        snapshot=_build_snapshot(),
        fallback_prompt="查询改写提示词兜底内容",
    )

    assert prompt == "查询改写提示词兜底内容"


@pytest.mark.asyncio
async def test_query_rewrite_agent_accepts_llm_rewrite_result() -> None:
    class FakeRewriteGenerator:
        async def rewrite(self, *, system_prompt, original_query, learning_context):
            assert "# 查询改写智能体" in system_prompt
            assert original_query == "联合索引"
            assert learning_context["course"] == "数据库原理"
            assert learning_context["diagnosisWeaknesses"][:2] == ["最左匹配", "跳过前导列"]
            assert learning_context["profileWeakPoints"] == ["覆盖索引"]
            return QueryRewriteResult(
                originalQuery="联合索引",
                rewrittenQuery="数据库原理 联合索引 最左匹配",
                keywords=["数据库原理", "联合索引", "最左匹配"],
            )

    agent = QueryRewriteAgent(
        llm_client=RuleBasedQueryRewriteLLM(),
        llm_generator=FakeRewriteGenerator(),
    )
    params = {
        "query": "联合索引",
        "learningContext": {"course": "数据库原理", "chapter": "索引"},
        "profileAnalysis": {"weakPoints": ["覆盖索引"]},
        "masteryDiagnosis": {
            "knowledgeDiagnoses": [
                {
                    "knowledgePoint": "最左匹配",
                    "nextFocus": "跳过前导列",
                    "masteryScore": 0.42,
                    "status": "weak",
                    "priority": 1,
                }
            ],
            "targetScope": {"knowledgePoints": ["联合索引"]},
        },
    }

    events = [
        event
        async for event in agent.run(
            task_id="task-query",
            trace_id="trace-query",
            seq=1,
            service_type="RESOURCE_GENERATION",
            params=params,
            snapshot=_build_snapshot(),
            system_prompt=agent.system_prompt(_build_snapshot()),
        )
    ]

    assert [event.event for event in events] == ["progress", "result_chunk"]
    assert params["rewrittenQuery"] == "数据库原理 联合索引 最左匹配"
    assert params["keywords"] == ["数据库原理", "联合索引", "最左匹配"]
    assert params["queryRewriteContext"]["originalQuery"] == "联合索引"
    assert params["queryRewriteContext"]["masteryDiagnosis"]["diagnosisWeaknesses"][0] == "最左匹配"
    assert params["rewrittenQueryPayload"]["rewrittenQuery"] == "数据库原理 联合索引 最左匹配"


@pytest.mark.asyncio
async def test_query_rewrite_fallback_uses_mastery_diagnosis_context() -> None:
    class FailingRewriteGenerator:
        async def rewrite(self, *, system_prompt, original_query, learning_context):
            del system_prompt, original_query, learning_context
            raise RuntimeError("rewrite failed")

    agent = QueryRewriteAgent(
        llm_client=RuleBasedQueryRewriteLLM(),
        llm_generator=FailingRewriteGenerator(),
    )
    params = {
        "query": "联合索引",
        "learningContext": {"course": "数据库原理", "chapter": "索引"},
        "profile": {"weakPoints": ["覆盖索引"]},
        "masteryDiagnosis": {
            "knowledgeDiagnoses": [
                {
                    "knowledgePoint": "最左匹配",
                    "nextFocus": "跳过前导列",
                    "masteryScore": 0.3,
                    "status": "weak",
                    "priority": 1,
                }
            ]
        },
    }

    events = [
        event
        async for event in agent.run(
            task_id="task-query-fallback",
            trace_id="trace-query-fallback",
            seq=1,
            service_type="PERSONALIZED_LEARNING",
            params=params,
            snapshot=_build_snapshot(),
            system_prompt=agent.system_prompt(_build_snapshot()),
        )
    ]

    assert [event.event for event in events] == ["progress", "result_chunk"]
    assert "最左匹配" in params["rewrittenQuery"]
    assert "跳过前导列" in params["rewrittenQuery"]
    assert "覆盖索引" in params["rewrittenQuery"]


@pytest.mark.asyncio
async def test_retrieval_agent_uses_summary_generator_when_available() -> None:
    class FakeSummaryGenerator:
        async def summarize(self, *, system_prompt, retrieval_response):
            del system_prompt, retrieval_response
            return "优先参考联合索引与索引导学两类来源。"

    class FakeRetriever:
        def retrieve(self, query: str) -> dict:
            return {
                "query": query,
                "channels": {
                    "grep": {"priority": [("composite-index", "联合索引", 1.0, ["联合索引"])]},
                    "vector": [("db-index", "数据库索引导学", 0.91)],
                    "graph": [],
                },
                "top": [
                    ("db-index", "数据库索引导学", 0.91),
                    ("composite-index", "联合索引", 0.8),
                ],
            }

    agent = RetrievalAgent(
        service=HybridRetrievalService(retriever=FakeRetriever()),
        summary_generator=FakeSummaryGenerator(),
    )
    params = {
        "query": "联合索引",
        "rewrittenQuery": "数据库原理 联合索引",
        "keywords": ["数据库原理", "联合索引"],
        "llmRetrievalSummaryEnabled": True,
    }

    events = [
        event
        async for event in agent.run(
            task_id="task-retrieval",
            trace_id="trace-retrieval",
            seq=1,
            service_type="RESOURCE_GENERATION",
            params=params,
            snapshot=_build_snapshot(),
            system_prompt="test",
        )
    ]

    assert [event.event for event in events] == ["progress", "result_chunk"]
    assert "优先参考联合索引与索引导学两类来源" in events[1].payload.text
    assert params["grepRetrievalResult"]["priority"][0][0] == "composite-index"
    assert params["vectorRetrievalResult"]["results"][0][0] == "db-index"
    assert params["mergedRetrievalResult"].documents[0].slug in {"db-index", "composite-index"}


@pytest.mark.asyncio
async def test_retrieval_agent_defaults_to_sources_summary_without_llm() -> None:
    class FailingSummaryGenerator:
        async def summarize(self, *, system_prompt, retrieval_response):
            del system_prompt, retrieval_response
            raise AssertionError("summary LLM should be opt-in")

    class FakeRetriever:
        def retrieve(self, query: str) -> dict:
            return {
                "query": query,
                "channels": {
                    "grep": {"priority": [("composite-index", "联合索引", 1.0, ["联合索引"])]},
                    "vector": [],
                    "graph": [],
                },
                "top": [("composite-index", "联合索引", 1.0)],
            }

    agent = RetrievalAgent(
        service=HybridRetrievalService(retriever=FakeRetriever()),
        summary_generator=FailingSummaryGenerator(),
    )
    params = {
        "query": "联合索引",
        "rewrittenQuery": "数据库原理 联合索引",
        "keywords": ["数据库原理", "联合索引"],
    }

    events = [
        event
        async for event in agent.run(
            task_id="task-retrieval-no-llm-summary",
            trace_id="trace-retrieval-no-llm-summary",
            seq=1,
            service_type="RESOURCE_GENERATION",
            params=params,
            snapshot=_build_snapshot(),
            system_prompt="test",
        )
    ]

    assert [event.event for event in events] == ["progress", "result_chunk"]
    assert params["retrievalSummaryText"] == params["retrievalResult"]["sourcesSummary"]


@pytest.mark.asyncio
async def test_retrieval_agent_skips_external_retrieval_for_none_strategy() -> None:
    class FailingRetriever:
        def retrieve(self, query: str) -> dict:
            raise AssertionError(f"retrieval should be skipped: {query}")

    agent = RetrievalAgent(
        service=HybridRetrievalService(retriever=FailingRetriever()),
    )
    params = {
        "query": "你好",
        "retrievalStrategy": "NONE",
    }

    events = [
        event
        async for event in agent.run(
            task_id="task-retrieval-none",
            trace_id="trace-retrieval-none",
            seq=1,
            service_type="TUTORING",
            params=params,
            snapshot=_build_snapshot(),
            system_prompt="test",
        )
    ]

    assert [event.event for event in events] == ["progress", "result_chunk"]
    assert params["retrievalResult"]["documents"] == []


@pytest.mark.asyncio
async def test_retrieval_agent_uses_grep_first_strategy() -> None:
    class GrepFirstRetriever:
        def __init__(self) -> None:
            self.retrieve_calls = 0
            self.grep_first_calls = 0

        def retrieve(self, query: str) -> dict:
            self.retrieve_calls += 1
            return {"query": query, "channels": {}, "top": []}

        def retrieve_grep_first(self, query: str, *, web_search_enabled: bool = False) -> dict:
            del web_search_enabled
            self.grep_first_calls += 1
            return {
                "query": query,
                "retrievalStrategy": "LOCAL_GREP_FIRST",
                "grepFirstPromoted": False,
                "channels": {
                    "grep": {"priority": [("grep-doc", "联合索引", 0.96, ["联合索引"])]},
                    "vector": [],
                    "graph": [],
                    "web": [],
                },
                "top": [("grep-doc", "联合索引", 0.96)],
            }

    retriever = GrepFirstRetriever()
    agent = RetrievalAgent(
        service=HybridRetrievalService(retriever=retriever),
    )
    params = {
        "query": "联合索引怎么用",
        "rewrittenQuery": "数据库原理 联合索引 怎么用",
        "keywords": ["数据库原理", "联合索引"],
        "retrievalStrategy": "LOCAL_GREP_FIRST",
    }

    _ = [
        event
        async for event in agent.run(
            task_id="task-retrieval-grep-first",
            trace_id="trace-retrieval-grep-first",
            seq=1,
            service_type="TUTORING",
            params=params,
            snapshot=_build_snapshot(),
            system_prompt="test",
        )
    ]

    assert retriever.grep_first_calls == 1
    assert retriever.retrieve_calls == 0
    assert params["vectorRetrievalResult"]["results"] == []


@pytest.mark.asyncio
async def test_evaluation_and_path_planning_agents_raise_when_llm_fails() -> None:
    class FailingEvaluationGenerator:
        async def evaluate(self, *, system_prompt, context_payload):
            del system_prompt, context_payload
            raise RuntimeError("eval llm down")

    class FailingPathGenerator:
        async def plan(self, *, system_prompt, context_payload):
            del system_prompt, context_payload
            raise RuntimeError("plan llm down")

    evaluation_agent = EvaluationAgent(generator=FailingEvaluationGenerator())
    planning_agent = PathPlanningAgent(generator=FailingPathGenerator())
    params = {
        "profile": {"studentLevel": "BASIC", "knowledgeGaps": ["最左匹配", "使用条件"]},
        "learningContext": {"course": "数据库原理", "chapter": "联合索引"},
    }

    with pytest.raises(RuntimeError, match="Evaluation LLM failed"):
        _ = [
            event
            async for event in evaluation_agent.run(
                task_id="task-eval",
                trace_id="trace-eval",
                seq=1,
                service_type="EVALUATION",
                params=params,
                snapshot=_build_snapshot(),
                system_prompt="test",
            )
        ]

    with pytest.raises(RuntimeError, match="Path planning LLM failed"):
        _ = [
            event
            async for event in planning_agent.run(
                task_id="task-plan",
                trace_id="trace-plan",
                seq=3,
                service_type="PATH_PLANNING",
                params=params,
                snapshot=_build_snapshot(),
                system_prompt="test",
            )
        ]


@pytest.mark.asyncio
async def test_evaluation_agent_uses_llm_generated_report_via_agent_core_loop() -> None:
    class FakeEvaluationGenerator:
        provider_name = "test-provider"
        model_name = "test-eval-model"

        async def evaluate(self, *, system_prompt, context_payload):
            del system_prompt
            assert context_payload["aggregatedBehavior"]["behaviorSignals"]["practiceAccuracy"] == 0.5
            return EvaluationPayload.model_validate(
                {
                    "overallLevel": "INTERMEDIATE",
                    "strengths": ["LLM 识别出学生愿意练习"],
                    "weaknesses": ["最左匹配", "使用条件"],
                    "nextFocus": ["最左匹配", "条件判断"],
                    "dimensions": [
                        {
                            "name": "knowledge_foundation",
                            "level": "INTERMEDIATE",
                            "evidence": "LLM 综合画像与练习结果后认为基础可继续提升。",
                            "recommendation": "围绕最左匹配补例题训练。",
                        }
                    ],
                    "summaryText": "LLM 评估：学生基础接近中等，但条件判断仍不稳定。",
                }
            )

    agent = EvaluationAgent(
        llm_client=_UnusedPlanningLLM(),
        generator=FakeEvaluationGenerator(),
    )
    params = {
        "profile": {"studentLevel": "BASIC", "knowledgeGaps": ["最左匹配", "使用条件"]},
        "judgeResult": {"accuracy": 0.5, "weakKnowledgeTags": ["条件判断"]},
        "messages": [{"role": "user", "content": "我总是搞不清最左匹配什么时候失效。"}],
        "learningContext": {"course": "数据库原理", "chapter": "联合索引"},
    }

    events = [
        event
        async for event in agent.run(
            task_id="task-eval-llm",
            trace_id="trace-eval-llm",
            seq=1,
            service_type="EVALUATION",
            params=params,
            snapshot=_build_snapshot(),
            system_prompt="test",
        )
    ]

    assert [event.event for event in events] == ["progress", "resource_file", "result_chunk"]
    assert params["aggregatedEvaluationContext"]["candidateWeaknesses"][0] == "最左匹配"
    assert params["evaluationResult"]["overallLevel"] == "INTERMEDIATE"
    diagnosis = params["masteryDiagnosis"]
    assert diagnosis["diagnosisSource"] == "evaluation"
    assert diagnosis["behaviorSignals"]["practiceAccuracy"] == 0.5
    assert diagnosis["knowledgeDiagnoses"][0]["knowledgePoint"] == "最左匹配"
    assert diagnosis["knowledgeDiagnoses"][0]["recommendedResourceTypes"]
    assert events[1].payload.asset_type == "DOCUMENT"
    assert events[1].payload.generated_by == "LLM"
    assert events[1].payload.provider == "test-provider"
    assert events[1].payload.inline_content is not None
    assert "LLM 评估：" in events[1].payload.inline_content
    assert "LLM 评估：" in events[2].payload.text


@pytest.mark.asyncio
async def test_evaluation_agent_golden_eval_preserves_interactive_question_batch() -> None:
    class GoldenEvaluationGenerator:
        provider_name = "test-provider"
        model_name = "test-eval-model"

        async def evaluate(self, *, system_prompt, context_payload):
            assert "# 评估智能体" in system_prompt
            assert context_payload["assessmentDimensions"] == ["练习掌握"]
            return EvaluationPayload.model_validate(
                {
                    "overallLevel": "BASIC",
                    "strengths": ["愿意复盘错题", "能说出部分使用条件"],
                    "weaknesses": ["最左匹配", "索引条件"],
                    "nextFocus": ["最左匹配", "条件判断"],
                    "dimensions": [
                        {
                            "name": "practice_mastery",
                            "level": "BASIC",
                            "evidence": "正确率 0.5，仍需要围绕最左匹配做专项练习。",
                            "recommendation": "先判断索引字段顺序，再解释失效条件。",
                        }
                    ],
                    "summaryText": "练习掌握评估：学生能识别联合索引主题，但最左匹配和索引条件仍需巩固。",
                }
            )

    class FakeQuestionGenerator:
        provider_name = "test-provider"
        model_name = "test-practice-model"

        async def generate_batch(self, *, topic, difficulty, count, learning_context):
            assert topic == "练习掌握：联合索引"
            assert count == 3
            assert learning_context["assessmentDimension"] == "练习掌握"
            return QuestionBatchPayload.model_validate(
                {
                    "title": "LLM 生成的练习掌握题",
                    "topic": "联合索引",
                    "difficulty": difficulty,
                    "questions": [
                        {
                            "questionId": "q1",
                            "questionType": "SHORT_ANSWER",
                            "stem": "请说明联合索引最左匹配的判断步骤。",
                            "answer": "先看索引列顺序，再判断条件是否连续命中。",
                            "knowledgeTags": ["联合索引", "最左匹配"],
                            "difficultyLevel": difficulty,
                            "explanation": "重点是按索引定义顺序判断。",
                        }
                    ],
                }
            )

    agent = EvaluationAgent(
        llm_client=_UnusedPlanningLLM(),
        generator=GoldenEvaluationGenerator(),
        question_generator=FakeQuestionGenerator(),
    )
    params = {
        "dimensions": ["练习掌握"],
        "profile": {"studentLevel": "BASIC", "knowledgeGaps": ["最左匹配"]},
        "judgeResult": {"accuracy": 0.5, "weakKnowledgeTags": ["最左匹配", "索引条件"]},
        "messages": [{"role": "user", "content": "我想测一下联合索引掌握得怎么样。"}],
        "learningContext": {"course": "数据库原理", "chapter": "联合索引"},
    }

    events = [
        event
        async for event in agent.run(
            task_id="task-eval-golden",
            trace_id="trace-eval-golden",
            seq=1,
            service_type="EVALUATION",
            params=params,
            snapshot=_build_snapshot(),
            system_prompt=agent.system_prompt(_build_snapshot()),
        )
    ]

    result = params["evaluationResult"]

    assert [event.event for event in events] == ["progress", "resource_file", "result_chunk", "question_batch"]
    assert result["overallLevel"] == "BASIC"
    assert result["weaknesses"] == ["最左匹配", "索引条件"]
    assert result["nextFocus"] == ["最左匹配", "条件判断"]
    assert result["dimensions"][0]["name"] == "practice_mastery"
    assert events[1].payload.asset_type == "DOCUMENT"
    assert events[1].payload.inline_content is not None
    assert "练习掌握评估" in events[1].payload.inline_content
    assert "练习掌握专项评估已完成" in events[2].payload.text
    assert events[3].payload.assessment_dimension == "练习掌握"
    assert events[3].payload.generated_by == "LLM"
    assert events[3].payload.provider == "test-provider"
    assert events[3].payload.fallback is False
    assert len(events[3].payload.questions) == 1
    assert params["profileSource"] == "EVALUATION"
    assert params["practiceQuestionBatch"]["assessmentDimension"] == "练习掌握"


@pytest.mark.asyncio
@pytest.mark.parametrize("dimension", ["学习主动性", "复盘闭环"])
async def test_evaluation_agent_profile_dimensions_skip_question_batch(dimension: str) -> None:
    class GoldenEvaluationGenerator:
        provider_name = "test-provider"
        model_name = "test-eval-model"

        async def evaluate(self, *, system_prompt, context_payload):
            assert "# 评估智能体" in system_prompt
            assert context_payload["assessmentDimensions"] == [dimension]
            assert context_payload["outputGuidance"]["detailLevel"] == "high"
            assert context_payload["outputGuidance"]["minSummaryCharacters"] == 260
            assert len(context_payload["outputGuidance"]["rubric"]) == 4
            return EvaluationPayload.model_validate(
                {
                    "overallLevel": "INTERMEDIATE",
                    "strengths": ["能结合画像持续调整学习动作"],
                    "weaknesses": ["主动追问记录仍偏少"],
                    "nextFocus": ["设定下一轮学习验证点"],
                    "dimensions": [
                        {
                            "name": dimension,
                            "level": "INTERMEDIATE",
                            "evidence": "LLM 综合学生画像、对话行为和近期活动后给出判断。",
                            "recommendation": "继续根据画像信号做学习行为调整。",
                        }
                    ],
                    "summaryText": f"{dimension}评估：根据学生画像判断，无需生成练习题。",
                }
            )

    class FailingQuestionGenerator:
        provider_name = "test-provider"
        model_name = "test-practice-model"

        async def generate_batch(self, *, topic, difficulty, count, learning_context):
            del topic, difficulty, count, learning_context
            raise AssertionError("画像类评估不应进入出题路径")

    agent = EvaluationAgent(
        llm_client=_UnusedPlanningLLM(),
        generator=GoldenEvaluationGenerator(),
        question_generator=FailingQuestionGenerator(),
    )
    params = {
        "dimensions": [dimension],
        "profile": {
            "studentLevel": "INTERMEDIATE",
            "knowledgeGaps": ["最左匹配"],
            "learningHabits": {"selfTesting": True, "noteTaking": True},
        },
        "messages": [{"role": "user", "content": "我想根据画像看看自己的学习行为表现。"}],
        "learningContext": {"course": "数据库原理", "chapter": "联合索引"},
    }

    events = [
        event
        async for event in agent.run(
            task_id=f"task-eval-{dimension}",
            trace_id=f"trace-eval-{dimension}",
            seq=1,
            service_type="EVALUATION",
            params=params,
            snapshot=_build_snapshot(),
            system_prompt=agent.system_prompt(_build_snapshot()),
        )
    ]

    assert [event.event for event in events] == ["progress", "resource_file", "result_chunk"]
    assert "practiceQuestionBatch" not in params
    assert "practiceQuestions" not in params
    assert params["evaluationResult"]["summaryText"] == f"{dimension}评估：根据学生画像判断，无需生成练习题。"
    assert events[1].payload.generated_by == "LLM"
    assert events[1].payload.inline_content is not None
    assert f"{dimension}评估" in events[1].payload.inline_content
    if dimension == "学习主动性":
        assert "目标拆解" in events[1].payload.inline_content
        assert "主动追问" in events[1].payload.inline_content
        assert "学习主动性强调主动发起" not in events[1].payload.inline_content
    else:
        assert "错因定位" in events[1].payload.inline_content
        assert "间隔复测" in events[1].payload.inline_content
        assert "目标拆解" not in events[1].payload.inline_content


def test_evaluation_agent_context_dimension_falls_back_to_assessment_dimension() -> None:
    agent = EvaluationAgent(
        llm_client=_UnusedPlanningLLM(),
        generator=None,
        question_generator=None,
    )

    payload = agent._build_context_payload(
        params={"assessmentDimension": "学习主动性"},
        snapshot=_build_snapshot(),
        aggregated_behavior={},
    )

    assert payload["assessmentDimensions"] == ["学习主动性"]
    assert payload["outputGuidance"]["detailLevel"] == "high"

    default_payload = agent._build_context_payload(
        params={},
        snapshot=_build_snapshot(),
        aggregated_behavior={},
    )

    assert default_payload["assessmentDimensions"] == ["知识基础"]


@pytest.mark.asyncio
async def test_evaluation_agent_fails_when_assessment_question_llm_fails() -> None:
    class GoldenEvaluationGenerator:
        provider_name = "test-provider"
        model_name = "test-eval-model"

        async def evaluate(self, *, system_prompt, context_payload):
            del system_prompt, context_payload
            return EvaluationPayload.model_validate(
                {
                    "overallLevel": "BASIC",
                    "strengths": ["愿意复盘错题"],
                    "weaknesses": ["最左匹配"],
                    "nextFocus": ["最左匹配"],
                    "dimensions": [],
                    "summaryText": "LLM 评估：需要继续练习。",
                }
            )

    class FailingQuestionGenerator:
        provider_name = "test-provider"
        model_name = "test-practice-model"

        async def generate_batch(self, *, topic, difficulty, count, learning_context):
            del topic, difficulty, count, learning_context
            raise RuntimeError("practice llm down")

    agent = EvaluationAgent(
        generator=GoldenEvaluationGenerator(),
        question_generator=FailingQuestionGenerator(),
    )

    with pytest.raises(RuntimeError, match="deterministic fallback is not allowed"):
        _ = [
            event
            async for event in agent.run(
                task_id="task-eval-question-fail",
                trace_id="trace-eval-question-fail",
                seq=1,
                service_type="EVALUATION",
                params={
                    "dimensions": ["练习掌握"],
                    "learningContext": {"course": "数据库原理", "chapter": "联合索引"},
                },
                snapshot=_build_snapshot(),
                system_prompt=agent.system_prompt(_build_snapshot()),
            )
        ]


@pytest.mark.asyncio
async def test_path_planning_agent_golden_eval_preserves_learning_path_contract() -> None:
    class FakePathGenerator:
        async def plan(self, *, system_prompt, context_payload):
            assert "# 路径规划智能体" in system_prompt
            assert context_payload["planningContext"]["triggerSource"] == "EVALUATION"
            assert context_payload["planningContext"]["nextFocus"][:2] == ["最左匹配", "使用条件"]
            return LearningPlanPayload.model_validate(
                {
                    "goal": "掌握联合索引的最左匹配规则",
                    "duration": "4天",
                    "milestones": ["理解规则", "判断条件", "解释场景"],
                    "steps": [
                        {
                            "title": "规则回顾",
                            "objective": "LLM 先带你回顾最左匹配。",
                            "activities": ["复述定义", "对照反例"],
                            "successCriteria": "能说清失效条件",
                        },
                        {
                            "title": "场景练习",
                            "objective": "用题目判断联合索引是否命中。",
                            "activities": ["先判条件", "再解释理由"],
                            "successCriteria": "连续 3 题判断正确",
                        },
                    ],
                    "summaryText": "LLM 路径：先掌握最左匹配，再做条件判断训练。",
                }
            )

    store = InMemoryLearningPlanStore()
    agent = PathPlanningAgent(
        llm_client=_UnusedPlanningLLM(),
        learning_plan_store=store,
        generator=FakePathGenerator(),
    )
    params = {
        "userId": "00000000-0000-0000-0000-000000000777",
        "profile": {"studentLevel": "BASIC", "knowledgeGaps": ["最左匹配", "使用条件"]},
        "evaluationResult": {
            "overallLevel": "BASIC",
            "weaknesses": ["最左匹配", "使用条件"],
            "nextFocus": ["最左匹配"],
            "strengths": ["愿意练习"],
            "dimensions": [],
            "summaryText": "待规划",
        },
        "learningContext": {"course": "数据库原理", "chapter": "联合索引"},
    }

    events = [
        event
        async for event in agent.run(
            task_id="task-plan-llm",
            trace_id="trace-plan-llm",
            seq=3,
            service_type="PATH_PLANNING",
            params=params,
            snapshot=_build_snapshot(),
            system_prompt=agent.system_prompt(_build_snapshot()),
        )
    ]

    stored_record = store.active_plans_by_user["00000000-0000-0000-0000-000000000777"]
    learning_path = params["learningPath"]

    assert [event.event for event in events] == ["progress", "result_chunk"]
    assert set(learning_path.keys()) >= {"goal", "duration", "milestones", "steps", "summaryText"}
    assert learning_path["goal"] == "掌握联合索引的最左匹配规则"
    assert learning_path["duration"] == "4天"
    assert learning_path["milestones"] == ["理解规则", "判断条件", "解释场景"]
    assert learning_path["steps"][0]["successCriteria"] == "能说清失效条件"
    assert params["learningPlanPersistence"]["version"] == 1
    assert stored_record["summaryText"].startswith("LLM 路径：")
    assert events[1].payload.text.startswith("LLM 路径：")


@pytest.mark.asyncio
async def test_path_planning_agent_maps_mastery_diagnosis_to_planning_context() -> None:
    class FakePathGenerator:
        async def plan(self, *, system_prompt, context_payload):
            del system_prompt
            planning_context = context_payload["planningContext"]
            assert planning_context["goal"] == "优先补齐最左匹配薄弱点"
            assert planning_context["nextFocus"][:2] == ["最左匹配判定", "最左匹配"]
            assert "跳过前导列" in planning_context["weaknesses"]
            assert planning_context["preferredResourceTypes"][:2] == ["VIDEO", "QUIZ"]
            return LearningPlanPayload.model_validate(
                {
                    "goal": "优先补齐最左匹配薄弱点",
                    "duration": "3天",
                    "milestones": ["看讲解", "做测验"],
                    "steps": [
                        {
                            "title": "诊断薄弱点复盘",
                            "objective": "围绕最左匹配错误模式复盘。",
                            "activities": ["看讲解", "标注错误条件"],
                            "successCriteria": "能解释跳过前导列为何失效",
                        }
                    ],
                    "summaryText": "基于掌握度诊断生成路径。",
                }
            )

    agent = PathPlanningAgent(
        llm_client=_UnusedPlanningLLM(),
        learning_plan_store=InMemoryLearningPlanStore(),
        generator=FakePathGenerator(),
    )
    params = {
        "userId": "00000000-0000-0000-0000-000000000778",
        "profileAnalysis": {"studentLevel": "BASIC", "learningPreference": "video_first"},
        "masteryDiagnosis": {
            "diagnosisSource": "EVALUATION_AGENT",
            "overallLevel": "BASIC",
            "overallMasteryScore": 0.42,
            "confidence": 0.8,
            "targetScope": {"course": "数据库原理", "chapter": "联合索引", "knowledgePoints": ["最左匹配"]},
            "knowledgeDiagnoses": [
                {
                    "knowledgePoint": "最左匹配",
                    "masteryScore": 0.35,
                    "status": "WEAK",
                    "priority": 1,
                    "errorPatterns": ["跳过前导列"],
                    "nextFocus": "最左匹配判定",
                    "recommendedResourceTypes": ["VIDEO", "QUIZ"],
                }
            ],
            "behaviorSignals": {},
            "planAdjustmentHints": {
                "shouldRefreshPlan": True,
                "refreshReason": "优先补齐最左匹配薄弱点",
                "strategy": "先讲解再练习",
            },
            "summaryText": "最左匹配掌握不足。",
        },
    }

    events = [
        event
        async for event in agent.run(
            task_id="task-plan-mastery",
            trace_id="trace-plan-mastery",
            seq=1,
            service_type="PATH_PLANNING",
            params=params,
            snapshot=_build_snapshot(),
            system_prompt=agent.system_prompt(_build_snapshot()),
        )
    ]

    assert [event.event for event in events] == ["progress", "result_chunk"]
    assert params["pathPlanningContext"]["masteryDiagnosis"] == params["masteryDiagnosis"]
    assert params["learningPath"]["steps"][0]["targetKnowledgePoints"][:2] == ["最左匹配判定", "最左匹配"]
    assert params["learningPath"]["steps"][0]["preferredResourceTypes"][:2] == ["VIDEO", "QUIZ"]


@pytest.mark.asyncio
async def test_in_memory_learning_plan_store_versions_snapshots() -> None:
    store = InMemoryLearningPlanStore()
    user_id = "00000000-0000-0000-0000-000000000888"
    first_plan = LearningPlanPayload.model_validate(
        {
            "goal": "先掌握定义",
            "duration": "2天",
            "milestones": ["理解概念"],
            "steps": [
                {
                    "title": "看定义",
                    "objective": "理解联合索引的定义。",
                    "activities": ["看讲义"],
                    "successCriteria": "能复述定义",
                }
            ],
            "summaryText": "第一版学习路径",
        }
    )
    second_plan = first_plan.model_copy(
        update={
            "summary_text": "第二版学习路径",
            "milestones": ["理解概念", "补充练习"],
        }
    )

    first_metadata = await store.save_plan(
        user_id=user_id,
        plan=first_plan,
        trigger_source="INITIAL",
    )
    second_metadata = await store.save_plan(
        user_id=user_id,
        plan=second_plan,
        trigger_source="EVALUATION",
    )

    assert first_metadata["version"] == 1
    assert second_metadata["version"] == 2
    assert first_metadata["planId"] == second_metadata["planId"]
    assert len(store.snapshots_by_plan[first_metadata["planId"]]) == 2
