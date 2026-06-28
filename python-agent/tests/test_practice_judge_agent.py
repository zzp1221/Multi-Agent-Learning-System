import pytest

from src.ai_modules.agents.judge_agent import JudgeAgent
from src.ai_modules.agents.practice_agent import PracticeAgent
from src.ai_modules.llms.agent_models import OpenAICompatiblePracticeQuestionGenerator
from src.ai_modules.memory import InMemoryPracticeStore
from src.ai_modules.models import QuestionBatchPayload, SubjectiveJudgeEvaluation
from src.ai_modules.runtime import SystemSnapshot
from src.ai_modules.runtime.skill_loader import SkillPromptLoader


def _question_batch_event(events):
    return next(event for event in events if event.event == "question_batch")


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
        session_id="conv-practice",
        conversation_length=3,
        total_tokens_used=256,
        wiki_pages_count=10,
        last_index_update="2026-05-03",
        recent_activities=["完成索引复习"],
    )


def test_judge_agent_system_prompt_loads_skill_and_context() -> None:
    agent = JudgeAgent()

    prompt = agent.system_prompt(_build_snapshot())

    assert "# 判题智能体" in prompt
    assert "评分流程" in prompt
    assert "grade_objective" in prompt
    assert "save_practice_result" in prompt
    assert "## 当前上下文" in prompt
    assert f"课程: {_build_snapshot().current_course}" in prompt


def test_judge_skill_prompt_falls_back_when_skill_is_missing(tmp_path) -> None:
    loader = SkillPromptLoader(skills_root=tmp_path)

    prompt = loader.build_system_prompt(
        skill_name="judge",
        snapshot=_build_snapshot(),
        fallback_prompt="判题提示词兜底内容",
    )

    assert prompt == "判题提示词兜底内容"


def test_practice_agent_system_prompt_loads_skill_and_context() -> None:
    agent = PracticeAgent()

    prompt = agent.system_prompt(_build_snapshot())

    assert "# 练习智能体" in prompt
    assert "出题流程" in prompt
    assert "generate_questions" in prompt
    assert "format_question_batch" in prompt
    assert "## 当前上下文" in prompt
    assert f"课程: {_build_snapshot().current_course}" in prompt


def test_practice_skill_prompt_falls_back_when_skill_is_missing(tmp_path) -> None:
    loader = SkillPromptLoader(skills_root=tmp_path)

    prompt = loader.build_system_prompt(
        skill_name="practice",
        snapshot=_build_snapshot(),
        fallback_prompt="练习提示词兜底内容",
    )

    assert prompt == "练习提示词兜底内容"


@pytest.mark.asyncio
async def test_openai_practice_generator_uses_6000_tokens_and_retries_short_batch() -> None:
    calls: list[dict[str, object]] = []

    class FakeJsonGenerator:
        async def generate(self, *, system_prompt, user_prompt, max_tokens):
            calls.append({
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "max_tokens": max_tokens,
            })
            if len(calls) == 1:
                return {
                    "questionId": "q7",
                    "questionType": "SINGLE_CHOICE",
                    "stem": "Thread.sleep 的作用是什么？",
                    "options": ["让当前线程休眠", "启动线程", "终止 JVM", "创建锁"],
                    "answer": "A",
                    "knowledgeTags": ["Thread.sleep"],
                    "difficultyLevel": "BASIC",
                    "explanation": "sleep 会让当前线程暂停一段时间。",
                }
            return {
                "title": "Java并发 阶段测试",
                "topic": "Java并发",
                "difficulty": "MIXED",
                "questions": [
                    {
                        "questionId": f"q{index}",
                        "questionType": "SINGLE_CHOICE" if index == 1 else "SHORT_ANSWER",
                        "stem": f"题目 {index}",
                        "options": ["A", "B", "C", "D"] if index == 1 else [],
                        "answer": "A" if index == 1 else "说明理由",
                        "knowledgeTags": ["Java并发"],
                        "difficultyLevel": "BASIC",
                        "explanation": "解析",
                    }
                    for index in range(1, 4)
                ],
            }

    generator = object.__new__(OpenAICompatiblePracticeQuestionGenerator)
    generator.generator = FakeJsonGenerator()

    batch = await generator.generate_batch(
        topic="Java并发",
        difficulty="MIXED",
        count=3,
        learning_context={"chapter": "Java并发"},
    )

    assert [call["max_tokens"] for call in calls] == [6000, 6000]
    assert len(batch.questions) == 3
    assert [question.question_id for question in batch.questions] == ["q1", "q2", "q3"]


@pytest.mark.asyncio
async def test_practice_agent_generates_question_batch() -> None:
    class FakeQuestionGenerator:
        provider_name = "unit-test-provider"
        model_name = "unit-test-practice-model"

        async def generate_batch(self, *, topic, difficulty, count, learning_context):
            del learning_context
            return QuestionBatchPayload.model_validate(
                {
                    "title": f"{topic} 练习题",
                    "topic": topic,
                    "difficulty": difficulty,
                    "questions": [
                        {
                            "questionId": f"q{index}",
                            "questionType": "SINGLE_CHOICE" if index < count else "SHORT_ANSWER",
                            "stem": f"LLM 生成题目 {index}",
                            "options": ["A", "B", "C", "D"] if index < count else [],
                            "answer": "C" if index < count else "说明理由",
                            "knowledgeTags": [topic, f"标签{index}"],
                            "difficultyLevel": difficulty,
                            "explanation": f"LLM 生成解析 {index}",
                        }
                        for index in range(1, count + 1)
                    ],
                }
            )

    agent = PracticeAgent(
        practice_store=InMemoryPracticeStore(),
        question_generator=FakeQuestionGenerator(),
    )
    params = {
        "topic": "联合索引",
        "difficulty": "BASIC",
        "count": 5,
    }

    events = [
        event
        async for event in agent.run(
            task_id="task-practice",
            trace_id="trace-practice",
            seq=1,
            service_type="PRACTICE_JUDGE",
            params=params,
            snapshot=_build_snapshot(),
            system_prompt="test",
        )
    ]

    assert _question_batch_event(events).event == "question_batch"
    assert params["practiceQuestionBatch"]["topic"] == "联合索引"
    assert len(params["practiceQuestionBatch"]["questions"]) == 5
    assert "practiceSetId" in params["practicePersistence"]
    assert _question_batch_event(events).payload.questions[0].stem == "LLM 生成题目 1"


@pytest.mark.asyncio
async def test_practice_agent_prefers_semantic_scope_topic_for_ambiguous_stage_title() -> None:
    class CapturingQuestionGenerator:
        provider_name = "unit-test-provider"
        model_name = "unit-test-practice-model"

        def __init__(self) -> None:
            self.topic = ""
            self.learning_context = {}

        async def generate_batch(self, *, topic, difficulty, count, learning_context):
            self.topic = topic
            self.learning_context = learning_context
            return QuestionBatchPayload.model_validate(
                {
                    "title": f"{topic} questions",
                    "topic": topic,
                    "difficulty": difficulty,
                    "questions": [
                        {
                            "questionId": f"q{index}",
                            "questionType": "SINGLE_CHOICE" if index == 1 else "SHORT_ANSWER",
                            "stem": f"{topic} prompt {index}",
                            "options": ["A", "B", "C", "D"] if index == 1 else [],
                            "answer": "A" if index == 1 else "call by function name",
                            "knowledgeTags": [topic, "Shell function"],
                            "difficultyLevel": difficulty,
                            "explanation": "scoped",
                        }
                        for index in range(1, count + 1)
                    ],
                }
            )

    generator = CapturingQuestionGenerator()
    agent = PracticeAgent(
        practice_store=InMemoryPracticeStore(),
        question_generator=generator,
    )
    params = {
        "topic": "function definition reinforcement",
        "count": 2,
        "learningContext": {
            "activeLearningStepTitle": "function definition reinforcement",
            "semanticScope": {
                "domain": "Linux Shell",
                "topic": "Linux Shell shell function definition and invocation",
                "rawTopic": "function definition reinforcement",
                "knowledgeTags": ["shell function definition and invocation"],
            },
        },
    }

    events = [
        event
        async for event in agent.run(
            task_id="task-semantic-practice",
            trace_id="trace-semantic-practice",
            seq=1,
            service_type="PRACTICE_JUDGE",
            params=params,
            snapshot=_build_snapshot(),
            system_prompt="test",
        )
    ]

    assert generator.topic == "Linux Shell shell function definition and invocation"
    assert generator.learning_context["semanticScope"]["rawTopic"] == "function definition reinforcement"
    assert params["practiceQuestionBatch"]["topic"] == "Linux Shell shell function definition and invocation"
    assert any(event.event == "question_batch" for event in events)


@pytest.mark.asyncio
async def test_practice_agent_conversation_triggered_batch_persists_without_task_id() -> None:
    class CapturingPracticeStore(InMemoryPracticeStore):
        def __init__(self) -> None:
            super().__init__()
            self.saved_task_id = "not-called"

        async def save_question_batch(self, *, user_id, batch, task_id=None):
            self.saved_task_id = task_id
            return await super().save_question_batch(
                user_id=user_id,
                batch=batch,
                task_id=task_id,
            )

    class FakeQuestionGenerator:
        provider_name = "unit-test-provider"
        model_name = "unit-test-practice-model"

        async def generate_batch(self, *, topic, difficulty, count, learning_context):
            del learning_context
            return QuestionBatchPayload.model_validate(
                {
                    "title": f"{topic} 练习题",
                    "topic": topic,
                    "difficulty": difficulty,
                    "questions": [
                        {
                            "questionId": f"conv-q{index}",
                            "questionType": "SINGLE_CHOICE" if index < count else "SHORT_ANSWER",
                            "stem": f"对话触发题目 {index}",
                            "options": ["A", "B", "C", "D"] if index < count else [],
                            "answer": "A" if index < count else "说明理由",
                            "knowledgeTags": [topic, f"标签{index}"],
                            "difficultyLevel": difficulty,
                            "explanation": f"对话触发解析 {index}",
                        }
                        for index in range(1, count + 1)
                    ],
                }
            )

    practice_store = CapturingPracticeStore()
    agent = PracticeAgent(
        practice_store=practice_store,
        question_generator=FakeQuestionGenerator(),
    )
    params = {
        "topic": "联合索引",
        "difficulty": "BASIC",
        "count": 5,
        "conversationTriggeredResourceGeneration": True,
    }

    events = [
        event
        async for event in agent.run(
            task_id="task-conversation-practice",
            trace_id="trace-conversation-practice",
            seq=1,
            service_type="PRACTICE_JUDGE",
            params=params,
            snapshot=_build_snapshot(),
            system_prompt="test",
        )
    ]

    assert practice_store.saved_task_id is None
    assert params["practicePersistence"]["taskId"] is None
    assert _question_batch_event(events).event == "question_batch"
    assert _question_batch_event(events).payload.topic == "联合索引"


@pytest.mark.asyncio
async def test_practice_agent_passes_question_type_preference_to_generator() -> None:
    captured: dict[str, str | None] = {}

    class FakeQuestionGenerator:
        provider_name = "unit-test-provider"
        model_name = "unit-test-practice-model"

        async def generate_batch(self, *, topic, difficulty, count, learning_context, question_type_preference):
            del learning_context
            captured["question_type_preference"] = question_type_preference
            return QuestionBatchPayload.model_validate(
                {
                    "title": f"{topic} 练习题",
                    "topic": topic,
                    "difficulty": difficulty,
                    "questions": [
                        {
                            "questionId": f"q{index}",
                            "questionType": "SINGLE_CHOICE",
                            "stem": f"LLM 生成选择题 {index}",
                            "options": ["A", "B", "C", "D"],
                            "answer": "C",
                            "knowledgeTags": [topic, f"标签{index}"],
                            "difficultyLevel": difficulty,
                            "explanation": f"LLM 解析 {index}",
                        }
                        for index in range(1, count + 1)
                    ],
                }
            )

    agent = PracticeAgent(
        practice_store=InMemoryPracticeStore(),
        question_generator=FakeQuestionGenerator(),
    )
    params = {
        "topic": "联合索引",
        "difficulty": "BASIC",
        "count": 3,
        "questionTypePreference": "SINGLE_CHOICE",
    }

    events = [
        event
        async for event in agent.run(
            task_id="task-practice-choice",
            trace_id="trace-practice-choice",
            seq=1,
            service_type="PRACTICE_JUDGE",
            params=params,
            snapshot=_build_snapshot(),
            system_prompt="test",
        )
    ]

    assert _question_batch_event(events).event == "question_batch"
    assert captured["question_type_preference"] == "SINGLE_CHOICE"
    assert {question["questionType"] for question in params["practiceQuestionBatch"]["questions"]} == {"SINGLE_CHOICE"}


@pytest.mark.asyncio
async def test_practice_agent_normalizes_single_question_object_from_llm() -> None:
    class SingleQuestionGenerator:
        provider_name = "unit-test-provider"
        model_name = "unit-test-practice-model"

        async def generate_batch(self, *, topic, difficulty, count, learning_context):
            del topic, difficulty, count, learning_context
            return {
                "questionId": "single-q1",
                "questionType": "SINGLE_CHOICE",
                "stem": "联合索引最左匹配原则主要要求什么？",
                "options": ["只看第一个字段", "按索引字段顺序匹配查询条件", "只对主键生效", "只影响排序"],
                "answer": "B",
                "knowledgeTags": ["联合索引", "最左匹配"],
                "difficultyLevel": "BASIC",
                "explanation": "联合索引需要按字段顺序命中查询条件。",
            }

    agent = PracticeAgent(
        practice_store=InMemoryPracticeStore(),
        question_generator=SingleQuestionGenerator(),
    )
    params = {"topic": "联合索引", "difficulty": "BASIC", "count": 1}

    events = [
        event
        async for event in agent.run(
            task_id="task-single-question",
            trace_id="trace-single-question",
            seq=1,
            service_type="PRACTICE_JUDGE",
            params=params,
            snapshot=_build_snapshot(),
            system_prompt="test",
        )
    ]

    assert _question_batch_event(events).event == "question_batch"
    assert params["practiceQuestionBatch"]["topic"] == "联合索引"
    assert params["practiceQuestionBatch"]["questions"][0]["questionId"] == "single-q1"


@pytest.mark.asyncio
async def test_practice_agent_normalizes_question_array_from_llm() -> None:
    class QuestionArrayGenerator:
        provider_name = "unit-test-provider"
        model_name = "unit-test-practice-model"

        async def generate_batch(self, *, topic, difficulty, count, learning_context):
            del learning_context
            return [
                {
                    "questionId": f"array-q{index}",
                    "questionType": "SINGLE_CHOICE",
                    "stem": f"{topic} 客观题 {index}",
                    "options": ["A", "B", "C", "D"],
                    "answer": "A",
                    "knowledgeTags": [topic, f"标签{index}"],
                    "difficultyLevel": difficulty,
                    "explanation": f"解析 {index}",
                }
                for index in range(1, count + 1)
            ]

    agent = PracticeAgent(
        practice_store=InMemoryPracticeStore(),
        question_generator=QuestionArrayGenerator(),
    )
    params = {
        "topic": "联合索引",
        "difficulty": "BASIC",
        "count": 3,
        "questionTypePreference": "SINGLE_CHOICE",
    }

    events = [
        event
        async for event in agent.run(
            task_id="task-question-array",
            trace_id="trace-question-array",
            seq=1,
            service_type="PRACTICE_JUDGE",
            params=params,
            snapshot=_build_snapshot(),
            system_prompt="test",
        )
    ]

    assert _question_batch_event(events).event == "question_batch"
    assert [item["questionId"] for item in params["practiceQuestionBatch"]["questions"]] == [
        "array-q1",
        "array-q2",
        "array-q3",
    ]


@pytest.mark.asyncio
async def test_practice_agent_reuses_existing_question_batch() -> None:
    class FakeQuestionGenerator:
        async def generate_batch(self, *, topic, difficulty, count, learning_context):
            del topic, difficulty, count, learning_context
            raise AssertionError("should not regenerate when practiceQuestionBatch is provided")

    agent = PracticeAgent(
        practice_store=InMemoryPracticeStore(),
        question_generator=FakeQuestionGenerator(),
    )
    params = {
        "topic": "学习主动性：并发编程",
        "practiceQuestionBatch": {
            "title": "并发编程 旧版行为测评",
            "topic": "并发编程",
            "difficulty": "BASIC",
            "assessmentDimension": "学习主动性",
            "submitLabel": "提交主动性计划",
            "questions": [
                {
                    "questionId": "initiative-q1",
                    "questionType": "SHORT_ANSWER",
                    "stem": "请写出下一轮学习计划。",
                    "answer": "先补知识点，再自测，卡住就提问。",
                    "knowledgeTags": ["并发编程", "学习主动性"],
                    "difficultyLevel": "BASIC",
                    "explanation": "回答需包含目标、验证和提问。",
                }
            ],
        },
    }

    events = [
        event
        async for event in agent.run(
            task_id="task-practice-existing",
            trace_id="trace-practice-existing",
            seq=1,
            service_type="PRACTICE_JUDGE",
            params=params,
            snapshot=_build_snapshot(),
            system_prompt="test",
        )
    ]

    assert _question_batch_event(events).event == "question_batch"
    assert _question_batch_event(events).payload.title == "并发编程 练习题"
    assert _question_batch_event(events).payload.assessment_dimension is None
    assert _question_batch_event(events).payload.submit_label is None
    assert _question_batch_event(events).payload.questions[0].question_id == "initiative-q1"
    assert _question_batch_event(events).payload.questions[0].knowledge_tags == ["并发编程", "学习主动性"]
    assert params["practiceQuestionBatch"]["title"] == "并发编程 练习题"
    assert params["practiceQuestionBatch"]["assessmentDimension"] is None


@pytest.mark.asyncio
async def test_practice_agent_golden_eval_preserves_question_batch_contract() -> None:
    class GoldenQuestionBatch:
        def model_dump(self, *, by_alias: bool):
            assert by_alias is True
            return {
                "title": "联合索引 黄金练习题",
                "topic": "联合索引",
                "difficulty": "BASIC",
                "questions": [
                    {
                        "questionId": "golden-q1",
                        "questionType": "SINGLE_CHOICE",
                        "stem": "联合索引最左匹配原则主要要求什么？",
                        "options": ["只看第一个字段", "按索引字段顺序匹配查询条件", "只对主键生效", "只影响排序"],
                        "answer": "B",
                        "knowledgeTags": ["联合索引", "最左匹配"],
                        "difficultyLevel": "BASIC",
                        "explanation": "联合索引需要关注查询条件是否按索引字段顺序命中。",
                    },
                    {
                        "questionId": "golden-q2",
                        "questionType": "SHORT_ANSWER",
                        "stem": "请说明一个联合索引容易失效的场景。",
                        "options": [],
                        "answer": "跳过最左字段或在关键字段上使用不合适的条件，可能导致索引效果下降。",
                        "knowledgeTags": ["联合索引", "使用条件", "易错点"],
                        "difficultyLevel": "BASIC",
                        "explanation": "回答应体现条件判断和具体场景。",
                    },
                    {
                        "questionId": "golden-q3",
                        "questionType": "SINGLE_CHOICE",
                        "stem": "做联合索引题时第一步更适合做什么？",
                        "options": ["直接背答案", "先判断查询条件与索引顺序", "忽略 WHERE 条件", "只看表名"],
                        "answer": "B",
                        "knowledgeTags": ["联合索引", "解题步骤"],
                        "difficultyLevel": "BASIC",
                        "explanation": "先判断条件和索引顺序，再分析是否命中。",
                    },
                ],
            }

    class GoldenQuestionGenerator:
        async def generate_batch(self, *, topic, difficulty, count, learning_context):
            assert topic == "联合索引"
            assert difficulty == "BASIC"
            assert count == 3
            assert learning_context["course"] == "数据库原理"
            return GoldenQuestionBatch()

    agent = PracticeAgent(
        practice_store=InMemoryPracticeStore(),
        question_generator=GoldenQuestionGenerator(),
    )
    params = {
        "topic": "联合索引",
        "difficulty": "BASIC",
        "count": 3,
        "learningContext": {"course": "数据库原理", "chapter": "联合索引"},
    }

    events = [
        event
        async for event in agent.run(
            task_id="task-golden-practice",
            trace_id="trace-golden-practice",
            seq=1,
            service_type="PRACTICE_JUDGE",
            params=params,
            snapshot=_build_snapshot(),
            system_prompt=agent.system_prompt(_build_snapshot()),
        )
    ]

    assert _question_batch_event(events).event == "question_batch"
    batch = params["practiceQuestionBatch"]
    assert batch["title"] == "联合索引 练习题"
    assert batch["topic"] == "联合索引"
    assert batch["difficulty"] == "BASIC"
    assert [item["questionId"] for item in batch["questions"]] == [
        "golden-q1",
        "golden-q2",
        "golden-q3",
    ]
    assert batch["questions"][0]["questionType"] == "SINGLE_CHOICE"
    assert batch["questions"][1]["questionType"] == "SHORT_ANSWER"
    assert batch["questions"][1]["knowledgeTags"] == ["联合索引", "使用条件", "易错点"]
    assert "practiceSetId" in params["practicePersistence"]


@pytest.mark.asyncio
async def test_judge_agent_scores_answers_and_marks_profile_source() -> None:
    class FakeQuestionGenerator:
        provider_name = "unit-test-provider"
        model_name = "unit-test-practice-model"

        async def generate_batch(self, *, topic, difficulty, count, learning_context):
            del learning_context
            return QuestionBatchPayload.model_validate(
                {
                    "title": f"{topic} 练习题",
                    "topic": topic,
                    "difficulty": difficulty,
                    "questions": [
                        {
                            "questionId": f"q{index}",
                            "questionType": "SINGLE_CHOICE" if index < count else "SHORT_ANSWER",
                            "stem": f"LLM 生成题目 {index}",
                            "options": ["A", "B", "C", "D"] if index < count else [],
                            "answer": "C" if index < count else "需要先判断条件",
                            "knowledgeTags": [topic, f"标签{index}"],
                            "difficultyLevel": difficulty,
                            "explanation": f"LLM 解析 {index}",
                        }
                        for index in range(1, count + 1)
                    ],
                }
            )

    class FakeFeedbackGenerator:
        async def summarize(self, *, items, topic):
            return {
                "summary": f"LLM 汇总：{topic} 还需加强条件判断。",
                "totalScore": 20.0,
                "accuracy": 1 / 3,
                "items": [
                    item.model_dump(by_alias=True) for item in items
                ],
                "weakKnowledgeTags": ["易错点", "使用条件"],
            }

    class FakeSubjectiveEvaluator:
        async def evaluate(self, *, question, learner_answer):
            del question, learner_answer
            return SubjectiveJudgeEvaluation(
                score=8.0,
                isCorrect=False,
                reason="LLM 判断主观题要点不足。",
                feedback="补充条件判断和错因分析。",
                confidenceLevel="LOW",
            )

    practice_agent = PracticeAgent(
        practice_store=InMemoryPracticeStore(),
        question_generator=FakeQuestionGenerator(),
    )
    params = {
        "topic": "联合索引",
        "difficulty": "BASIC",
        "count": 5,
    }
    practice_events = [
        event
        async for event in practice_agent.run(
            task_id="task-practice",
            trace_id="trace-practice",
            seq=1,
            service_type="PRACTICE_JUDGE",
            params=params,
            snapshot=_build_snapshot(),
            system_prompt="test",
        )
    ]
    assert _question_batch_event(practice_events).event == "question_batch"

    params["answers"] = {
        "q1": "C",
        "q2": "B",
        "q3": "需要先判断条件，但是我还不会分析错因。",
        "q4": "B",
        "q5": "",
    }
    judge_agent = JudgeAgent(
        subjective_evaluator=FakeSubjectiveEvaluator(),
        feedback_generator=FakeFeedbackGenerator(),
    )
    judge_events = [
        event
        async for event in judge_agent.run(
            task_id="task-judge",
            trace_id="trace-judge",
            seq=3,
            service_type="PRACTICE_JUDGE",
            params=params,
            snapshot=_build_snapshot(),
            system_prompt="test",
        )
    ]

    assert judge_events[-1].event == "judge_result"
    assert params["profileSource"] == "PRACTICE"
    assert params["judgeResult"]["accuracy"] < 1.0
    assert params["judgeResult"]["items"][1]["isCorrect"] is False
    assert "submissionMappings" in params["practiceJudgePersistence"]
    assert "weakKnowledgeTags" in params["judgeResult"]
    assert params["judgeResult"]["summary"].startswith("LLM 汇总：")


@pytest.mark.asyncio
async def test_grade_objective_processes_all_questions() -> None:
    agent = JudgeAgent()
    params = {
        "practiceQuestions": [
            {
                "questionId": "q1",
                "questionType": "SINGLE_CHOICE",
                "stem": "题目 1",
                "options": ["A", "B", "C", "D"],
                "answer": "B",
                "knowledgeTags": ["并发编程"],
                "difficultyLevel": "INTERMEDIATE",
            },
            {
                "questionId": "q2",
                "questionType": "SINGLE_CHOICE",
                "stem": "题目 2",
                "options": ["A", "B", "C", "D"],
                "answer": "C",
                "knowledgeTags": ["线程池"],
                "difficultyLevel": "INTERMEDIATE",
            },
            {
                "questionId": "q3",
                "questionType": "SHORT_ANSWER",
                "stem": "题目 3",
                "answer": "给出解释",
                "knowledgeTags": ["锁"],
                "difficultyLevel": "INTERMEDIATE",
            },
        ],
        "answers": {
            "q1": "B",
            "q2": "A",
            "q3": "我的解释",
        },
    }

    result = await agent._tool_grade_objective(tool_input={}, params=params)

    assert len(result["items"]) == 2
    assert result["items"][0]["isCorrect"] is True
    assert result["items"][1]["isCorrect"] is False
    assert len(result["pendingSubjective"]) == 1
    assert result["pendingSubjective"][0]["questionId"] == "q3"


@pytest.mark.asyncio
async def test_grade_objective_matches_option_labels_and_text() -> None:
    agent = JudgeAgent()
    params = {
        "practiceQuestions": [
            {
                "questionId": "q1",
                "questionType": "SINGLE_CHOICE",
                "stem": "?? 1",
                "options": ["A. ???", "B. ???", "C. ???"],
                "answer": "B",
                "knowledgeTags": ["??1"],
                "difficultyLevel": "BASIC",
            },
            {
                "questionId": "q2",
                "questionType": "SINGLE_CHOICE",
                "stem": "?? 2",
                "options": ["A. ???", "B. B+ ?", "C. ?????"],
                "answer": "C. ?????",
                "knowledgeTags": ["??2"],
                "difficultyLevel": "BASIC",
            },
            {
                "questionId": "q3",
                "questionType": "SINGLE_CHOICE",
                "stem": "?? 3",
                "options": ["?-?-?", "?-?-?", "?-?-?"],
                "answer": "?-?-?",
                "knowledgeTags": ["??3"],
                "difficultyLevel": "BASIC",
            },
            {
                "questionId": "q4",
                "questionType": "SINGLE_CHOICE",
                "stem": "?? 4",
                "options": ["Alpha", "Beta", "Gamma"],
                "answer": " beta ",
                "knowledgeTags": ["??4"],
                "difficultyLevel": "BASIC",
            },
        ],
        "answers": {
            "q1": " b ",
            "q2": "??? ??",
            "q3": "B. ?-?-?",
            "q4": "B",
        },
    }

    result = await agent._tool_grade_objective(tool_input={}, params=params)

    assert len(result["items"]) == 4
    assert all(item["isCorrect"] for item in result["items"])
    assert result["pendingSubjective"] == []


@pytest.mark.asyncio
async def test_judge_agent_rejects_missing_question_batch_provenance() -> None:
    params = {
        "topic": "联合索引",
        "practiceQuestionBatch": {
            "title": "联合索引 练习题",
            "topic": "联合索引",
            "difficulty": "BASIC",
            "questions": [
                {
                    "questionId": "q1",
                    "questionType": "SINGLE_CHOICE",
                    "stem": "联合索引最左匹配原则是什么？",
                    "options": ["A", "B", "C", "D"],
                    "answer": "B",
                    "knowledgeTags": ["联合索引"],
                    "difficultyLevel": "BASIC",
                },
            ],
        },
        "answers": {"q1": "B"},
    }
    agent = JudgeAgent()

    with pytest.raises(RuntimeError, match="题批缺少 LLM 来源元数据"):
        await agent._run_direct_judge_pipeline(
            task_id="task-missing-provenance",
            user_id="user-1",
            params=params,
        )


def test_validate_complete_judge_items_rejects_empty_result() -> None:
    agent = JudgeAgent()
    params = {
        "practiceQuestions": [
            {
                "questionId": "q1",
                "questionType": "SINGLE_CHOICE",
                "stem": "联合索引最左匹配原则是什么？",
                "options": ["A", "B", "C", "D"],
                "answer": "B",
                "knowledgeTags": ["联合索引"],
                "difficultyLevel": "BASIC",
            },
        ],
    }

    with pytest.raises(RuntimeError, match="判题结果不完整"):
        agent._validate_complete_judge_items(params=params, judge_items=[])


@pytest.mark.asyncio
async def test_judge_agent_golden_eval_preserves_scoring_contract() -> None:
    class GoldenSubjectiveEvaluator:
        async def evaluate(self, *, question, learner_answer):
            del question
            if "条件" in learner_answer and "场景" in learner_answer:
                return SubjectiveJudgeEvaluation(
                    score=18.0,
                    isCorrect=True,
                    reason="覆盖了条件判断和场景解释。",
                    feedback="继续补充一个边界反例会更完整。",
                    confidenceLevel="MEDIUM",
                )
            return SubjectiveJudgeEvaluation(
                score=0.0,
                isCorrect=False,
                reason="未作答或缺少关键解释。",
                feedback="先写出适用条件，再给出一个具体场景。",
                confidenceLevel="LOW",
            )

    class GoldenFeedbackGenerator:
        async def summarize(self, *, items, topic):
            del topic
            return {
                "summary": f"golden eval judged {len(items)} items",
            }

    params = {
        "topic": "联合索引",
        "practiceQuestions": [
            {
                "questionId": "q1",
                "questionType": "SINGLE_CHOICE",
                "stem": "联合索引最左匹配原则是什么？",
                "options": ["A", "B", "C", "D"],
                "answer": "B",
                "knowledgeTags": ["联合索引", "最左匹配"],
                "difficultyLevel": "BASIC",
            },
            {
                "questionId": "q2",
                "questionType": "SINGLE_CHOICE",
                "stem": "下面哪个条件会削弱索引效果？",
                "options": ["A", "B", "C", "D"],
                "answer": "C",
                "knowledgeTags": ["联合索引", "索引条件"],
                "difficultyLevel": "BASIC",
            },
            {
                "questionId": "q3",
                "questionType": "SHORT_ANSWER",
                "stem": "说明联合索引适用条件并举例。",
                "answer": "需要判断查询条件是否符合最左匹配，并结合场景解释。",
                "knowledgeTags": ["联合索引", "使用条件"],
                "difficultyLevel": "BASIC",
            },
            {
                "questionId": "q4",
                "questionType": "SHORT_ANSWER",
                "stem": "写出一次错题复盘动作。",
                "answer": "定位错因并安排复查。",
                "knowledgeTags": ["联合索引", "复盘"],
                "difficultyLevel": "BASIC",
            },
        ],
        "answers": {
            "q1": " b ",
            "q2": "A",
            "q3": "需要先判断条件，再结合实际场景解释原因。",
            "q4": "",
        },
    }
    agent = JudgeAgent(
        practice_store=InMemoryPracticeStore(),
        subjective_evaluator=GoldenSubjectiveEvaluator(),
        feedback_generator=GoldenFeedbackGenerator(),
    )

    events = [
        event
        async for event in agent.run(
            task_id="task-golden-judge",
            trace_id="trace-golden-judge",
            seq=1,
            service_type="PRACTICE_JUDGE",
            params=params,
            snapshot=_build_snapshot(),
            system_prompt=agent.system_prompt(_build_snapshot()),
        )
    ]

    assert [event.event for event in events] == ["progress", "judge_result"]
    result = params["judgeResult"]
    assert result["summary"] == "golden eval judged 4 items"
    assert result["totalScore"] == 38.0
    assert result["accuracy"] == 0.5
    assert result["weakKnowledgeTags"] == ["联合索引", "索引条件", "复盘"]
    assert [item["questionId"] for item in result["items"]] == ["q1", "q2", "q3", "q4"]
    assert result["items"][0]["isCorrect"] is True
    assert result["items"][1]["isCorrect"] is False
    assert result["items"][2]["score"] == 18.0
    assert result["items"][3]["score"] == 0.0
    assert "persistence" in result


@pytest.mark.asyncio
async def test_judge_agent_uses_subjective_evaluator_result_when_available() -> None:
    class FakeSubjectiveEvaluator:
        async def evaluate(self, *, question, learner_answer):
            del question, learner_answer
            return SubjectiveJudgeEvaluation(
                score=18.0,
                isCorrect=True,
                reason="百炼评估认为答案基本完整。",
                feedback="补一个更具体的例子会更好。",
                confidenceLevel="MEDIUM",
            )

    class FakeQuestionGenerator:
        provider_name = "unit-test-provider"
        model_name = "unit-test-practice-model"

        async def generate_batch(self, *, topic, difficulty, count, learning_context, question_type_preference):
            del learning_context
            assert question_type_preference is None
            return QuestionBatchPayload.model_validate(
                {
                    "title": f"{topic} 练习题",
                    "topic": topic,
                    "difficulty": difficulty,
                    "questions": [
                        {
                            "questionId": f"q{index}",
                            "questionType": "SINGLE_CHOICE" if index < count else "SHORT_ANSWER",
                            "stem": f"LLM 生成题目 {index}",
                            "options": ["A", "B", "C", "D"] if index < count else [],
                            "answer": "C" if index < count else "需要先判断条件",
                            "knowledgeTags": [topic, f"标签{index}"],
                            "difficultyLevel": difficulty,
                            "explanation": f"LLM 解析 {index}",
                        }
                        for index in range(1, count + 1)
                    ],
                }
            )

    practice_agent = PracticeAgent(
        practice_store=InMemoryPracticeStore(),
        question_generator=FakeQuestionGenerator(),
    )
    params = {
        "topic": "联合索引",
        "difficulty": "BASIC",
        "count": 5,
    }
    _ = [
        event
        async for event in practice_agent.run(
            task_id="task-practice",
            trace_id="trace-practice",
            seq=1,
            service_type="PRACTICE_JUDGE",
            params=params,
            snapshot=_build_snapshot(),
            system_prompt="test",
        )
    ]
    params["answers"] = {
        "q1": "C",
        "q2": "A",
        "q3": "我会先判断条件，再结合场景解释。",
        "q4": "B",
        "q5": "先看定义。",
    }

    judge_agent = JudgeAgent(
        subjective_evaluator=FakeSubjectiveEvaluator(),
    )
    events = [
        event
        async for event in judge_agent.run(
            task_id="task-judge-llm",
            trace_id="trace-judge-llm",
            seq=3,
            service_type="PRACTICE_JUDGE",
            params=params,
            snapshot=_build_snapshot(),
            system_prompt="test",
        )
    ]

    judge_event = next(event for event in events if event.event == "judge_result")
    subjective_items = [
        item for item in judge_event.payload.items if item.question_type == "SHORT_ANSWER"
    ]
    assert subjective_items
    assert any(item.score == 18.0 for item in subjective_items)
    assert any(item.reason == "百炼评估认为答案基本完整。" for item in subjective_items)


@pytest.mark.asyncio
async def test_judge_agent_fails_when_subjective_evaluator_fails() -> None:
    class BrokenSubjectiveEvaluator:
        async def evaluate(self, *, question, learner_answer):
            del question, learner_answer
            raise RuntimeError("rate limit")

    params = {
        "topic": "联合索引",
        "practiceQuestions": [
            {
                "questionId": "q1",
                "questionType": "SINGLE_CHOICE",
                "stem": "联合索引最左匹配原则是什么？",
                "options": ["A", "B", "C", "D"],
                "answer": "C",
                "knowledgeTags": ["联合索引"],
                "difficultyLevel": "BASIC",
            },
            {
                "questionId": "q2",
                "questionType": "SHORT_ANSWER",
                "stem": "说明联合索引失效场景。",
                "answer": "需要先判断条件。",
                "knowledgeTags": ["联合索引", "使用条件"],
                "difficultyLevel": "BASIC",
            },
        ],
    }
    params["answers"] = {
        "q1": "C",
        "q2": "我不确定。",
    }

    judge_agent = JudgeAgent(
        subjective_evaluator=BrokenSubjectiveEvaluator(),
    )
    with pytest.raises(RuntimeError, match="rate limit"):
        _ = [
            event
            async for event in judge_agent.run(
                task_id="task-judge-fallback",
                trace_id="trace-judge-fallback",
                seq=3,
                service_type="PRACTICE_JUDGE",
                params=params,
                snapshot=_build_snapshot(),
                system_prompt="test",
            )
        ]
