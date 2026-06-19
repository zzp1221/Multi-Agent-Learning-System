import pytest
from types import SimpleNamespace

from src.ai_modules.agents.tutor_agent import TutorAgent
from src.ai_modules.llms import RuleBasedTutorLLM
from src.ai_modules.memory import (
    ConversationSummaryDocument,
    InMemoryConversationSummaryStore,
    MongoConversationSummaryStore,
)
from src.ai_modules.runtime import (
    AssistantTurn,
    ConversationCompactor,
    StructuredConversationSummary,
    SystemSnapshot,
    ToolCall,
)
from src.ai_modules.runtime.skill_loader import SkillPromptLoader
from src.ai_modules.models import (
    ProgressPayload,
    ProgressSSEEvent,
    ResourceFilePayload,
    ResourceFileSSEEvent,
)


class _StreamChunk:
    def __init__(self, kind: str, text: str, provider: str = "secondary", model: str = "secondary-model") -> None:
        self.kind = kind
        self.text = text
        self.provider = provider
        self.model = model


def _build_snapshot() -> SystemSnapshot:
    return SystemSnapshot(
        current_course="数据库原理",
        current_chapter="索引",
        course_progress=0.3,
        student_name="张三",
        student_level="BASIC",
        knowledge_gaps=["联合索引"],
        preferred_style="step_by_step",
        recent_mistakes=["范围查询条件判断错误"],
        session_id="conv-001",
        conversation_length=10,
        total_tokens_used=1500,
        wiki_pages_count=20,
        last_index_update="2026-05-02",
        recent_activities=["完成索引练习"],
    )


class _FailingTutorClient:
    provider_name = "primary"
    model_name = "primary-model"
    base_url = "https://primary.invalid/v1"

    async def chat_completion(self, **kwargs):
        del kwargs
        raise RuntimeError("primary chat failed")

    async def chat_completion_stream(self, **kwargs):
        del kwargs
        for chunk in ():
            yield chunk
        raise RuntimeError("primary stream failed")


class _FailingTutorLLM:
    def __init__(self) -> None:
        self.client = _FailingTutorClient()

    async def complete(self, **kwargs):
        del kwargs
        raise RuntimeError("primary core loop failed")


class _StreamingTutorClient:
    provider_name = "secondary"
    model_name = "secondary-model"
    base_url = "https://secondary.invalid/v1"

    def __init__(self) -> None:
        self.stream_calls = 0
        self.stream_max_tokens: list[int | None] = []

    async def chat_completion(self, **kwargs):
        del kwargs
        raise AssertionError("secondary stream should satisfy the tutor response")

    async def chat_completion_stream(self, **kwargs):
        self.stream_calls += 1
        self.stream_max_tokens.append(kwargs.get("max_tokens"))
        for token in ["LLM ", "generated ", "answer"]:
            yield token


class _StreamingTutorLLM:
    def __init__(self) -> None:
        self.client = _StreamingTutorClient()


class _ReasoningStreamingTutorClient(_StreamingTutorClient):
    def __init__(self) -> None:
        super().__init__()
        self.include_reasoning_calls: list[bool] = []

    async def chat_completion_stream_events(self, **kwargs):
        self.stream_calls += 1
        self.include_reasoning_calls.append(bool(kwargs.get("include_reasoning")))
        self.stream_max_tokens.append(kwargs.get("max_tokens"))
        yield _StreamChunk("reasoning", "先分析问题")
        yield _StreamChunk("answer", "最终")
        yield _StreamChunk("reasoning", "再检查")
        yield _StreamChunk("answer", "答案")


class _ReasoningStreamingTutorLLM:
    def __init__(self) -> None:
        self.client = _ReasoningStreamingTutorClient()


class _LengthAwareTutorClient:
    provider_name = "length-aware"
    model_name = "length-aware-model"
    base_url = "https://length-aware.invalid/v1"

    def __init__(self) -> None:
        self.stream_calls = 0
        self.chat_calls = 0

    async def chat_completion(self, *, messages, **kwargs):
        del kwargs
        self.chat_calls += 1
        prompt = messages[-1]["content"]
        if "待压缩答案" in prompt:
            return {"choices": [{"message": {"content": "红黑树靠旋转和染色维持近似平衡，插入后按父叔颜色分情况修复，保证查找仍接近 O(log n)。"}}]}
        return {"choices": [{"message": {"content": "红黑树是一种自平衡二叉搜索树。它通过节点染色、左旋、右旋维持树高稳定。插入后如果父节点和叔节点颜色不同，需要按局部结构旋转；如果叔节点为红，则通常先变色再继续向上修复。这个回答故意很长，用来触发压缩。"}}]}

    async def chat_completion_stream(self, **kwargs):
        del kwargs
        self.stream_calls += 1
        yield "不应走流式"

    def extract_message(self, response_json):
        return response_json["choices"][0]["message"]

    def extract_content(self, message):
        return message["content"]


class _LengthAwareTutorLLM:
    def __init__(self) -> None:
        self.client = _LengthAwareTutorClient()


class _LoopingToolTutorLLM:
    provider_name = "looping"
    model_name = "looping-model"

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, *, system_prompt, messages, tools):
        del system_prompt, messages, tools
        self.calls += 1
        return AssistantTurn(
            content="need more tools",
            tool_calls=[
                ToolCall(
                    id=f"call_{self.calls}",
                    name="read_retrieval_evidence",
                    input={},
                )
            ],
        )


class _RecordingResourceBundleRunner:
    def __init__(self, display_mode: str = "INLINE") -> None:
        self.calls: list[dict] = []
        self.display_mode = display_mode

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        yield ProgressSSEEvent(
            taskId=kwargs["task_id"],
            traceId=kwargs["trace_id"],
            seq=kwargs["seq"],
            payload=ProgressPayload(
                stage="resource_bundle",
                percent=50,
                message="resource bundle running",
            ),
        )
        yield ResourceFileSSEEvent(
            taskId=kwargs["task_id"],
            traceId=kwargs["trace_id"],
            seq=kwargs["seq"] + 1,
            payload=ResourceFilePayload(
                assetType=kwargs["params"]["resourceTypes"][0],
                title=f"{kwargs['params']['topic']} resource",
                summary="generated",
                displayMode=self.display_mode,
                fileName="resource.md",
                inlineContent="# generated",
                generatedBy="llm",
                contentOrigin="llm",
                provider="test",
                model="test-model",
                agentName="test-agent",
                fallback=False,
            ),
        )


class _FakeResourceIntentExtractor:
    def __init__(
        self,
        *,
        should_generate: bool,
        resource_types: list[str] | None = None,
        topic: str = "",
        confidence: float = 0.95,
        missing_slots: list[str] | None = None,
        question_count: int | None = None,
        question_type_preference: str = "",
        difficulty_preference: str = "",
    ) -> None:
        self.should_generate = should_generate
        self.resource_types = resource_types or []
        self.topic = topic
        self.confidence = confidence
        self.missing_slots = missing_slots or []
        self.question_count = question_count
        self.question_type_preference = question_type_preference
        self.difficulty_preference = difficulty_preference
        self.calls: list[dict] = []

    async def extract(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            should_generate=self.should_generate,
            resource_types=self.resource_types,
            topic=self.topic,
            question_count=self.question_count,
            question_type_preference=self.question_type_preference,
            difficulty_preference=self.difficulty_preference,
            confidence=self.confidence,
            missing_slots=self.missing_slots,
            rationale="test fixture",
        )


def _resource_intent(
    resource_types: list[str],
    topic: str = "",
    *,
    missing_topic: bool = False,
) -> _FakeResourceIntentExtractor:
    return _FakeResourceIntentExtractor(
        should_generate=True,
        resource_types=resource_types,
        topic=topic,
        missing_slots=["topic"] if missing_topic else [],
    )


@pytest.mark.asyncio
async def test_tutor_resource_intent_does_not_match_plain_question() -> None:
    extractor = _FakeResourceIntentExtractor(should_generate=False)
    tutor = TutorAgent(
        summary_store=InMemoryConversationSummaryStore(),
        llm_client=RuleBasedTutorLLM(),
        resource_intent_extractor=extractor,
    )

    intent = await tutor._detect_resource_generation_intent(
        user_query="Explain how a B+ tree handles range queries.",
        conversation=[],
        params={},
    )

    assert intent is None


@pytest.mark.asyncio
async def test_tutor_resource_intent_defaults_bundle_types() -> None:
    extractor = _FakeResourceIntentExtractor(
        should_generate=True,
        resource_types=["DOCUMENT", "SLIDES", "MINDMAP", "QUIZ", "VIDEO", "CODE"],
        topic="联合索引",
    )
    tutor = TutorAgent(
        summary_store=InMemoryConversationSummaryStore(),
        llm_client=RuleBasedTutorLLM(),
        resource_intent_extractor=extractor,
    )

    intent = await tutor._detect_resource_generation_intent(
        user_query="请围绕联合索引生成一套学习资源",
        conversation=[],
        params={},
    )

    assert intent is not None
    assert intent.topic == "联合索引"
    assert intent.resource_types == [
        "DOCUMENT",
        "SLIDES",
        "MINDMAP",
        "QUIZ",
        "VIDEO",
        "CODE",
    ]


@pytest.mark.asyncio
async def test_tutor_resource_intent_accepts_llm_specific_types() -> None:
    cases = [
        ("帮我做数据库索引的 PPT", ["SLIDES"], "数据库索引"),
        ("给我围绕死锁出 3 道练习题", ["QUIZ"], "死锁"),
        ("请生成 B+ 树思维导图", ["MINDMAP"], "B+ 树"),
        ("制作 Java 并发短视频", ["VIDEO"], "Java 并发"),
        ("整理 Redis 缓存穿透代码案例", ["CODE"], "Redis 缓存穿透"),
    ]

    for query, expected_types, topic in cases:
        tutor = TutorAgent(
            summary_store=InMemoryConversationSummaryStore(),
            llm_client=RuleBasedTutorLLM(),
            resource_intent_extractor=_FakeResourceIntentExtractor(
                should_generate=True,
                resource_types=expected_types,
                topic=topic,
            ),
        )
        intent = await tutor._detect_resource_generation_intent(user_query=query, conversation=[], params={})
        assert intent is not None, query
        assert intent.resource_types == expected_types


@pytest.mark.asyncio
async def test_tutor_resource_intent_rejects_long_answer_with_embedded_path_and_quiz() -> None:
    extractor = _FakeResourceIntentExtractor(
        should_generate=True,
        resource_types=["DOCUMENT", "QUIZ"],
        topic="数据库索引结构比较",
    )
    tutor = TutorAgent(
        summary_store=InMemoryConversationSummaryStore(),
        llm_client=RuleBasedTutorLLM(),
        resource_intent_extractor=extractor,
    )

    intent = await tutor._detect_resource_generation_intent(
        user_query=(
            "请用较长回答系统比较数据库中的 B 树、B+ 树、哈希索引和 LSM-tree。"
            "要求分 6 个小节：核心结构、等值查询、范围查询、写入放大、缓存友好性、崩溃恢复。"
            "请结合 PostgreSQL、MySQL InnoDB、Redis 或 RocksDB 举例，最后给我一个 3 天学习路径和 5 道自测题。"
        ),
        conversation=[],
        params={},
    )

    assert intent is None
    assert extractor.calls == []


@pytest.mark.asyncio
async def test_tutor_resource_intent_rejects_video_link_request() -> None:
    extractor = _FakeResourceIntentExtractor(should_generate=False)
    tutor = TutorAgent(
        summary_store=InMemoryConversationSummaryStore(),
        llm_client=RuleBasedTutorLLM(),
        resource_intent_extractor=extractor,
    )

    intent = await tutor._detect_resource_generation_intent(
        user_query="请给我视频链接",
        conversation=[],
        params={},
    )

    assert intent is None
    assert extractor.calls[0]["user_query"] == "请给我视频链接"


def test_tutor_question_count_requires_explicit_question_unit() -> None:
    tutor = TutorAgent(
        summary_store=InMemoryConversationSummaryStore(),
        llm_client=RuleBasedTutorLLM(),
    )

    assert tutor._extract_question_count("给我出 Java 8 Stream 练习题") == 5
    assert tutor._extract_question_count("给我出 8 道 Java Stream 练习题") == 8
    assert tutor._extract_question_count("围绕死锁出五道题") == 5


@pytest.mark.parametrize("query", ["生成一份PPT", "生成5道题目给我", "给我一份文档"])
@pytest.mark.asyncio
async def test_tutor_agent_prompts_for_topic_when_resource_request_has_no_context(query: str) -> None:
    runner = _RecordingResourceBundleRunner()
    llm = _StreamingTutorLLM()
    tutor = TutorAgent(
        compactor=ConversationCompactor(token_budget=1000, keep_recent_turns=4),
        summary_store=InMemoryConversationSummaryStore(),
        llm_client=llm,
        resource_intent_extractor=_resource_intent(["SLIDES"], missing_topic=True),
        resource_bundle_runner=runner,
    )
    params = {
        "conversationId": "conv-topicless-resource",
        "messages": [{"role": "user", "content": query}],
        "query": query,
    }

    events = [
        event
        async for event in tutor.run(
            task_id="task-topicless-resource",
            trace_id="trace-topicless-resource",
            seq=1,
            service_type="TUTORING",
            params=params,
            snapshot=_build_snapshot(),
            system_prompt="tutor prompt",
        )
    ]

    assert runner.calls == []
    assert llm.client.stream_calls == 0
    assert [event.event for event in events] == ["progress", "result_chunk"]
    assert "补充" in events[-1].payload.text
    assert "主题" in events[-1].payload.text


@pytest.mark.asyncio
async def test_tutor_agent_uses_active_learning_step_for_topicless_resource_request() -> None:
    runner = _RecordingResourceBundleRunner()
    tutor = TutorAgent(
        compactor=ConversationCompactor(token_budget=1000, keep_recent_turns=4),
        summary_store=InMemoryConversationSummaryStore(),
        llm_client=RuleBasedTutorLLM(),
        resource_intent_extractor=_resource_intent(["SLIDES"]),
        resource_bundle_runner=runner,
    )
    params = {
        "conversationId": "conv-active-step-resource",
        "messages": [{"role": "user", "content": "根据当前阶段生成PPT"}],
        "query": "根据当前阶段生成PPT",
        "learningContext": {"activeLearningStepTitle": "联合索引"},
    }

    events = [
        event
        async for event in tutor.run(
            task_id="task-active-step-resource",
            trace_id="trace-active-step-resource",
            seq=1,
            service_type="TUTORING",
            params=params,
            snapshot=_build_snapshot(),
            system_prompt="tutor prompt",
        )
    ]

    assert len(runner.calls) == 1
    assert runner.calls[0]["params"]["topic"] == "联合索引"
    assert runner.calls[0]["params"]["resourceTypes"] == ["SLIDES"]
    assert [event.event for event in events] == ["progress", "result_chunk", "progress", "resource_file"]


@pytest.mark.asyncio
async def test_tutor_agent_ignores_generic_explicit_topic_when_active_step_exists() -> None:
    runner = _RecordingResourceBundleRunner()
    tutor = TutorAgent(
        compactor=ConversationCompactor(token_budget=1000, keep_recent_turns=4),
        summary_store=InMemoryConversationSummaryStore(),
        llm_client=RuleBasedTutorLLM(),
        resource_intent_extractor=_resource_intent(["SLIDES"]),
        resource_bundle_runner=runner,
    )
    params = {
        "conversationId": "conv-generic-topic-active-step",
        "messages": [{"role": "user", "content": "生成一份PPT"}],
        "query": "生成一份PPT",
        "learningContext": {
            "explicitUserTopic": "一份",
            "activeLearningStepTitle": "Java线程创建基础概念学习",
        },
    }

    events = [
        event
        async for event in tutor.run(
            task_id="task-generic-topic-active-step",
            trace_id="trace-generic-topic-active-step",
            seq=1,
            service_type="TUTORING",
            params=params,
            snapshot=_build_snapshot(),
            system_prompt="tutor prompt",
        )
    ]

    assert len(runner.calls) == 1
    assert runner.calls[0]["params"]["topic"] == "Java线程创建基础概念学习"
    assert runner.calls[0]["params"]["resourceTypes"] == ["SLIDES"]
    assert "Java线程创建基础概念学习" in events[1].payload.text
    assert "一份" not in events[1].payload.text


@pytest.mark.asyncio
async def test_tutor_agent_uses_current_stage_for_resource_bundle_prompt() -> None:
    runner = _RecordingResourceBundleRunner()
    tutor = TutorAgent(
        compactor=ConversationCompactor(token_budget=1000, keep_recent_turns=4),
        summary_store=InMemoryConversationSummaryStore(),
        llm_client=RuleBasedTutorLLM(),
        resource_intent_extractor=_resource_intent(
            ["DOCUMENT", "SLIDES", "MINDMAP", "QUIZ", "VIDEO", "CODE"],
        ),
        resource_bundle_runner=runner,
    )
    query = "请根据我当前学习阶段生成一套学习资源，包括文档、PPT、思维导图、练习题、短视频和代码案例"
    params = {
        "conversationId": "conv-current-stage-resource",
        "messages": [{"role": "user", "content": query}],
        "query": query,
        "learningContext": {
            "activeLearningStepTitle": "Java并发编程基础：线程创建与休眠",
            "activeLearningStepId": "step-1",
        },
    }

    events = [
        event
        async for event in tutor.run(
            task_id="task-current-stage-resource",
            trace_id="trace-current-stage-resource",
            seq=1,
            service_type="TUTORING",
            params=params,
            snapshot=_build_snapshot(),
            system_prompt="tutor prompt",
        )
    ]

    assert len(runner.calls) == 1
    assert runner.calls[0]["params"]["topic"] == "Java并发编程基础：线程创建与休眠"
    assert "Java并发编程基础：线程创建与休眠" in events[1].payload.text
    assert "根据我当前学习阶段" not in events[1].payload.text


@pytest.mark.asyncio
async def test_tutor_agent_triggers_resource_bundle_from_conversation() -> None:
    runner = _RecordingResourceBundleRunner()
    tutor = TutorAgent(
        compactor=ConversationCompactor(token_budget=1000, keep_recent_turns=4),
        summary_store=InMemoryConversationSummaryStore(),
        llm_client=RuleBasedTutorLLM(),
        resource_intent_extractor=_resource_intent(
            ["DOCUMENT", "SLIDES", "MINDMAP", "QUIZ", "VIDEO", "CODE"],
            "联合索引",
        ),
        resource_bundle_runner=runner,
    )
    params = {
        "conversationId": "conv-resource",
        "messages": [{"role": "user", "content": "请围绕联合索引生成一套学习资源"}],
        "query": "请围绕联合索引生成一套学习资源",
    }

    events = [
        event
        async for event in tutor.run(
            task_id="task-resource",
            trace_id="trace-resource",
            seq=1,
            service_type="TUTORING",
            params=params,
            snapshot=_build_snapshot(),
            system_prompt="tutor prompt",
        )
    ]

    assert len(runner.calls) == 1
    assert runner.calls[0]["params"]["topic"] == "联合索引"
    assert runner.calls[0]["params"]["resourceTypes"] == [
        "DOCUMENT",
        "SLIDES",
        "MINDMAP",
        "QUIZ",
        "VIDEO",
        "CODE",
    ]
    assert [event.event for event in events] == ["progress", "result_chunk", "progress", "resource_file"]
    assert "资源生成需求" in events[1].payload.text
    assert events[-1].payload.asset_type == "DOCUMENT"


@pytest.mark.asyncio
async def test_tutor_agent_records_legacy_slide_outline_resource_as_generated_asset() -> None:
    runner = _RecordingResourceBundleRunner(display_mode="SLIDE_OUTLINE_CONFIRMATION")
    tutor = TutorAgent(
        compactor=ConversationCompactor(token_budget=1000, keep_recent_turns=4),
        summary_store=InMemoryConversationSummaryStore(),
        llm_client=RuleBasedTutorLLM(),
        resource_intent_extractor=_resource_intent(["SLIDES"], "联合索引"),
        resource_bundle_runner=runner,
    )
    params = {
        "conversationId": "conv-slides",
        "messages": [{"role": "user", "content": "请围绕联合索引生成 PPT"}],
        "query": "请围绕联合索引生成 PPT",
    }

    events = [
        event
        async for event in tutor.run(
            task_id="task-slides",
            trace_id="trace-slides",
            seq=1,
            service_type="TUTORING",
            params=params,
            snapshot=_build_snapshot(),
            system_prompt="tutor prompt",
        )
    ]

    assert events[-1].event == "resource_file"
    assert params["generatedAssets"][0]["title"] == "联合索引 resource"
    assert params["generatedAssets"][0]["inlineContent"] == "# generated"
    assert "pendingSlideOutlines" not in params


@pytest.mark.asyncio
async def test_tutor_agent_ignores_legacy_confirmed_slide_outline_from_learning_context() -> None:
    runner = _RecordingResourceBundleRunner(display_mode="DOWNLOAD_CARD")
    tutor = TutorAgent(
        compactor=ConversationCompactor(token_budget=1000, keep_recent_turns=4),
        summary_store=InMemoryConversationSummaryStore(),
        llm_client=RuleBasedTutorLLM(),
        resource_intent_extractor=_resource_intent(["SLIDES"], "B+ tree indexes"),
        resource_bundle_runner=runner,
    )
    params = {
        "conversationId": "conv-confirmed-slides",
        "messages": [{"role": "user", "content": "请围绕 B+ tree indexes 生成 PPT"}],
        "query": "请围绕 B+ tree indexes 生成 PPT",
        "learningContext": {
            "selectedService": "RESOURCE_GENERATION",
            "commandIntent": "generate_slides",
            "explicitUserTopic": "B+ tree indexes",
            "confirmedSlideOutline": "true",
            "confirmedSlideOutlineText": "# B+ tree indexes\n## Search\n## Range scan",
        },
    }

    events = [
        event
        async for event in tutor.run(
            task_id="task-confirmed-slides",
            trace_id="trace-confirmed-slides",
            seq=1,
            service_type="TUTORING",
            params=params,
            snapshot=_build_snapshot(),
            system_prompt="tutor prompt",
        )
    ]

    assert len(runner.calls) == 1
    runner_params = runner.calls[0]["params"]
    assert runner_params["resourceTypes"] == ["SLIDES"]
    assert "confirmedSlideOutline" not in runner_params
    assert "confirmedSlideOutlineText" not in runner_params
    assert events[-1].payload.display_mode == "DOWNLOAD_CARD"
    assert "pendingSlideOutlines" not in params


@pytest.mark.asyncio
async def test_tutor_agent_applies_llm_quiz_parameter_intent() -> None:
    runner = _RecordingResourceBundleRunner()
    tutor = TutorAgent(
        compactor=ConversationCompactor(token_budget=1000, keep_recent_turns=4),
        summary_store=InMemoryConversationSummaryStore(),
        llm_client=RuleBasedTutorLLM(),
        resource_intent_extractor=_FakeResourceIntentExtractor(
            should_generate=True,
            resource_types=["QUIZ"],
            topic="Java Stream",
            question_count=12,
            question_type_preference="SINGLE_CHOICE",
            difficulty_preference="BASIC",
        ),
        resource_bundle_runner=runner,
    )
    params = {
        "conversationId": "conv-quiz-intent",
        "messages": [{"role": "user", "content": "围绕 Java Stream 出 12 道简单选择题"}],
        "query": "围绕 Java Stream 出 12 道简单选择题",
    }

    events = [
        event
        async for event in tutor.run(
            task_id="task-quiz-intent",
            trace_id="trace-quiz-intent",
            seq=1,
            service_type="TUTORING",
            params=params,
            snapshot=_build_snapshot(),
            system_prompt="tutor prompt",
        )
    ]

    assert len(runner.calls) == 1
    runner_params = runner.calls[0]["params"]
    assert runner_params["resourceTypes"] == ["QUIZ"]
    assert runner_params["count"] == 12
    assert runner_params["questionTypePreference"] == "SINGLE_CHOICE"
    assert runner_params["difficulty"] == "BASIC"
    assert [event.event for event in events] == ["progress", "result_chunk", "progress", "resource_file"]


@pytest.mark.asyncio
async def test_tutor_agent_plain_question_does_not_trigger_resource_bundle() -> None:
    runner = _RecordingResourceBundleRunner()
    llm = _StreamingTutorLLM()
    tutor = TutorAgent(
        compactor=ConversationCompactor(token_budget=1000, keep_recent_turns=4),
        summary_store=InMemoryConversationSummaryStore(),
        llm_client=llm,
        resource_intent_extractor=_FakeResourceIntentExtractor(should_generate=False),
        resource_bundle_runner=runner,
    )
    params = {
        "conversationId": "conv-plain",
        "messages": [{"role": "user", "content": "What is Java?"}],
        "query": "What is Java?",
    }

    events = [
        event
        async for event in tutor.run(
            task_id="task-plain",
            trace_id="trace-plain",
            seq=1,
            service_type="TUTORING",
            params=params,
            snapshot=_build_snapshot(),
            system_prompt="tutor prompt",
        )
    ]

    assert runner.calls == []
    assert [event.event for event in events] == ["progress", "result_chunk"]
    assert "".join(event.payload.text for event in events if event.event == "result_chunk") == "LLM generated answer"


@pytest.mark.asyncio
async def test_tutor_agent_streams_raw_reasoning_separately_in_deep_mode() -> None:
    llm = _ReasoningStreamingTutorLLM()
    tutor = TutorAgent(
        compactor=ConversationCompactor(token_budget=1000, keep_recent_turns=4),
        summary_store=InMemoryConversationSummaryStore(),
        llm_client=llm,
        resource_intent_extractor=_FakeResourceIntentExtractor(should_generate=False),
    )
    params = {
        "query": "联合索引为什么遵循最左前缀",
        "messages": [{"role": "user", "content": "联合索引为什么遵循最左前缀"}],
        "reasoningMode": "DEEP",
    }

    events = [
        event
        async for event in tutor.run(
            task_id="task-plain-reasoning",
            trace_id="trace-plain-reasoning",
            seq=1,
            service_type="TUTORING",
            params=params,
            snapshot=_build_snapshot(),
            system_prompt=tutor.system_prompt(_build_snapshot()),
        )
    ]

    assert [event.event for event in events] == [
        "progress",
        "reasoning_chunk",
        "reasoning_chunk",
        "reasoning_chunk",
        "result_chunk",
        "reasoning_chunk",
        "result_chunk",
    ]
    reasoning_text = "".join(event.payload.text for event in events if event.event == "reasoning_chunk")
    assert "回答组织" in reasoning_text
    assert "质量自检" in reasoning_text
    assert "先分析问题再检查" in reasoning_text
    assert "".join(event.payload.text for event in events if event.event == "result_chunk") == "最终答案"
    assert llm.client.include_reasoning_calls == [True]


@pytest.mark.asyncio
async def test_tutor_agent_emits_public_reasoning_when_provider_has_no_reasoning_stream() -> None:
    llm = _StreamingTutorLLM()
    tutor = TutorAgent(
        compactor=ConversationCompactor(token_budget=1000, keep_recent_turns=4),
        summary_store=InMemoryConversationSummaryStore(),
        llm_client=llm,
        resource_intent_extractor=_FakeResourceIntentExtractor(should_generate=False),
    )
    params = {
        "query": "什么是AVL树",
        "messages": [{"role": "user", "content": "什么是AVL树"}],
        "reasoningMode": "DEEP",
        "retrievalResult": {
            "documents": [
                {
                    "title": "AVL树",
                    "channel": "phrase",
                    "evidence": "AVL树是一种自平衡二叉搜索树。",
                }
            ]
        },
    }

    events = [
        event
        async for event in tutor.run(
            task_id="task-public-deep-reasoning",
            trace_id="trace-public-deep-reasoning",
            seq=1,
            service_type="TUTORING",
            params=params,
            snapshot=_build_snapshot(),
            system_prompt=tutor.system_prompt(_build_snapshot()),
        )
    ]

    assert [event.event for event in events] == ["progress", "reasoning_chunk", "reasoning_chunk", "result_chunk"]
    reasoning_text = "".join(event.payload.text for event in events if event.event == "reasoning_chunk")
    assert "回答组织" in reasoning_text
    assert "质量自检" in reasoning_text
    assert "什么是AVL树" in reasoning_text
    assert "AVL树" in reasoning_text
    assert "LLM generated answer" == "".join(
        event.payload.text for event in events if event.event == "result_chunk"
    )


def test_tutor_agent_system_prompt_loads_skill_and_context() -> None:
    tutor = TutorAgent(
        summary_store=InMemoryConversationSummaryStore(),
        llm_client=RuleBasedTutorLLM(),
    )

    prompt = tutor.system_prompt(_build_snapshot())

    assert "# 辅导智能体" in prompt
    assert "回复原则" in prompt
    assert "read_retrieval_evidence" in prompt
    assert "外部 URL" in prompt
    assert "## 当前上下文" in prompt
    assert f"课程: {_build_snapshot().current_course}" in prompt


def test_tutor_skill_prompt_falls_back_when_skill_is_missing(tmp_path) -> None:
    loader = SkillPromptLoader(skills_root=tmp_path)

    prompt = loader.build_system_prompt(
        skill_name="tutor",
        snapshot=_build_snapshot(),
        fallback_prompt="辅导提示词兜底内容",
    )

    assert prompt == "辅导提示词兜底内容"


def test_tutor_retrieval_evidence_builds_graph_pack() -> None:
    tutor = TutorAgent(
        summary_store=InMemoryConversationSummaryStore(),
        llm_client=RuleBasedTutorLLM(),
    )
    params = {
        "query": "学习 B+树 前需要理解哪些概念？",
        "rewrittenQuery": "数据库 B+树 学习路径 前置依赖",
        "graphIntent": "PREREQUISITE_PATH",
        "retrievalResult": {
            "documents": [{"title": "B+树", "channel": "graph"}],
            "sourcesSummary": "B+树(graph:0.8)",
        },
        "graphRetrievalResult": {
            "graphIntent": "PREREQUISITE_PATH",
            "results": [
                ("database-index", "数据库索引", 11.0, "graph_1hop"),
                ("btree", "B树", 9.0, "graph_2hop"),
            ],
        },
        "retrievalRawResult": {
            "graphIntent": "PREREQUISITE_PATH",
            "graphDiagnostics": {
                "prerequisiteEvidence": {
                    "directEvidenceCandidatesTopN": [
                        {
                            "slug": "bplus-tree",
                            "title": "B+树",
                            "score": 33.0,
                            "source": "direct_evidence",
                        }
                    ],
                    "protectedSeeds": [
                        {
                            "slug": "index-structure",
                            "title": "索引结构",
                            "score": 29.0,
                            "source": "seed_protected",
                        }
                    ],
                }
            },
            "channels": {
                "graph": [
                    ("database-index", "数据库索引", 11.0, "graph_1hop"),
                    ("btree", "B树", 9.0, "graph_2hop"),
                ]
            },
        },
    }

    evidence = tutor._tool_read_retrieval_evidence(tool_input={}, params=params)
    graph_pack = evidence["graphEvidencePack"]

    assert graph_pack["intent"] == "PREREQUISITE_PATH"
    assert "前置基础" in graph_pack["guidance"]
    assert [node["title"] for node in graph_pack["nodes"][:4]] == [
        "数据库索引",
        "B树",
        "B+树",
        "索引结构",
    ]
    assert graph_pack["nodes"][0]["source"] == "graph_1hop"
    assert graph_pack["nodes"][1]["hop"] == 2
    assert any("非严格顺序" in hint for hint in graph_pack["relationHints"])


def test_tutor_runtime_context_includes_graph_evidence_pack() -> None:
    tutor = TutorAgent(
        summary_store=InMemoryConversationSummaryStore(),
        llm_client=RuleBasedTutorLLM(),
    )
    evidence = {
        "documents": [{"title": "B+树", "channel": "graph", "evidence": "B+树适合范围查询。"}],
        "graphEvidencePack": {
            "intent": "PREREQUISITE_PATH",
            "guidance": "按“前置基础 -> 当前概念 -> 后续延伸”组织学习路径。",
            "nodes": [
                {
                    "rank": 1,
                    "slug": "database-index",
                    "title": "数据库索引",
                    "source": "graph_1hop",
                    "hop": 1,
                    "score": 11.0,
                },
                {
                    "rank": 2,
                    "slug": "btree",
                    "title": "B树",
                    "source": "graph_2hop",
                    "hop": 2,
                    "score": 9.0,
                },
            ],
            "relationHints": ["学习路径相关候选集合（非严格顺序）：数据库索引、B树、B+树"],
        },
    }

    context = tutor._build_enriched_message(
        user_query="学习 B+树 前需要理解哪些概念？",
        memory={},
        context={},
        evidence=evidence,
        profile={},
        image_analysis={},
        recent_dialogue={},
        input_mode="clear_question",
        params={},
    )

    assert "图谱证据包" in context
    assert "图谱题型：学习路径/前置依赖" in context
    assert "数据库索引（一跳图谱相关概念）" in context
    assert "B树（二跳图谱补充概念）" in context
    assert "source=" not in context
    assert "graphIntent:" not in context
    assert "学习路径相关候选集合（非严格顺序）：数据库索引、B树、B+树" in context


def test_tutor_retrieval_evidence_collects_web_external_resources() -> None:
    tutor = TutorAgent(
        summary_store=InMemoryConversationSummaryStore(),
        llm_client=RuleBasedTutorLLM(),
    )
    params = {
        "query": "给我有关数据结构的视频链接",
        "rewrittenQuery": "数据结构 视频 教程 链接",
        "webSearchEnabled": True,
        "retrievalResult": {
            "documents": [
                {
                    "title": "数据结构课程视频",
                    "channel": "web",
                    "evidence": "面向入门学习者的数据结构视频课程。",
                    "url": "https://example.edu/ds-video",
                    "sourceTitle": "Example EDU",
                    "score": 0.91,
                },
                {
                    "title": "本地数据结构讲义",
                    "channel": "hybrid",
                    "evidence": "数组、链表、树的基础定义。",
                },
            ],
        },
        "webRetrievalResult": {
            "enabled": True,
            "results": [
                (
                    "https://example.edu/ds-video",
                    "数据结构课程视频重复项",
                    0.8,
                    {"url": "https://example.edu/ds-video", "snippet": "重复链接"},
                ),
                (
                    "https://example.org/algorithms",
                    "算法与数据结构公开视频",
                    0.77,
                    {"snippet": "包含栈、队列、树、图等章节。"},
                ),
            ],
        },
    }

    evidence = tutor._tool_read_retrieval_evidence(tool_input={}, params=params)

    assert evidence["webSearchEnabled"] is True
    assert [item["url"] for item in evidence["externalResources"]] == [
        "https://example.edu/ds-video",
        "https://example.org/algorithms",
    ]
    assert evidence["externalResources"][0]["title"] == "数据结构课程视频"


def test_tutor_runtime_context_allows_verified_external_links_when_web_search_enabled() -> None:
    tutor = TutorAgent(
        summary_store=InMemoryConversationSummaryStore(),
        llm_client=RuleBasedTutorLLM(),
    )
    evidence = {
        "webSearchEnabled": True,
        "externalResources": [
            {
                "title": "数据结构课程视频",
                "url": "https://example.edu/ds-video",
                "snippet": "面向入门学习者的数据结构视频课程。",
            }
        ],
        "documents": [
            {
                "title": "数据结构课程视频",
                "channel": "web",
                "evidence": "面向入门学习者的数据结构视频课程。",
                "url": "https://example.edu/ds-video",
            }
        ],
    }

    context = tutor._build_enriched_message(
        user_query="给我有关数据结构的视频链接",
        memory={},
        context={},
        evidence=evidence,
        profile={},
        image_analysis={},
        recent_dialogue={},
        input_mode="clear_question",
        params={"webSearchEnabled": True},
    )

    assert "联网搜索状态：已开启" in context
    assert "只能引用以下 adoptedExternalSources 中的来源" in context
    assert "依据对应" in context
    assert "[S1] 数据结构课程视频 (https://example.edu/ds-video)" in context
    assert "https://example.edu/ds-video" in context
    assert "不得编造未提供的 URL" in context


@pytest.mark.asyncio
async def test_tutor_web_search_video_link_request_returns_external_links_without_generation() -> None:
    runner = _RecordingResourceBundleRunner()
    tutor = TutorAgent(
        compactor=ConversationCompactor(token_budget=1000, keep_recent_turns=4),
        summary_store=InMemoryConversationSummaryStore(),
        llm_client=RuleBasedTutorLLM(),
        resource_intent_extractor=_FakeResourceIntentExtractor(should_generate=False),
        resource_bundle_runner=runner,
    )
    params = {
        "conversationId": "conv-video-link",
        "messages": [{"role": "user", "content": "给我有关数据结构的视频链接"}],
        "query": "给我有关数据结构的视频链接",
        "rewrittenQuery": "数据结构 视频 教程 链接",
        "webSearchEnabled": True,
        "retrievalResult": {
            "documents": [
                {
                    "title": "数据结构课程视频",
                    "channel": "web",
                    "evidence": "面向入门学习者的数据结构视频课程。",
                    "url": "https://example.edu/ds-video",
                    "sourceTitle": "Example EDU",
                    "score": 0.91,
                }
            ]
        },
    }

    events = [
        event
        async for event in tutor.run(
            task_id="task-video-link",
            trace_id="trace-video-link",
            seq=1,
            service_type="TUTORING",
            params=params,
            snapshot=_build_snapshot(),
            system_prompt="tutor prompt",
        )
    ]

    result_text = "".join(
        event.payload.text for event in events if event.event == "result_chunk"
    )
    assert runner.calls == []
    assert "https://example.edu/ds-video" in result_text
    assert "[数据结构课程视频](https://example.edu/ds-video)" in result_text
    assert "外部资源链接" in result_text
    assert not any(event.event in {"resource_file", "video_gen:start"} for event in events)


def test_tutor_runtime_context_omits_graph_pack_for_plain_retrieval() -> None:
    tutor = TutorAgent(
        summary_store=InMemoryConversationSummaryStore(),
        llm_client=RuleBasedTutorLLM(),
    )
    evidence = tutor._tool_read_retrieval_evidence(
        tool_input={},
        params={
            "query": "什么是 Java？",
            "retrievalResult": {
                "documents": [
                    {"title": "Java", "channel": "hybrid", "evidence": "Java 是编程语言。"}
                ]
            },
        },
    )

    context = tutor._build_enriched_message(
        user_query="什么是 Java？",
        memory={},
        context={},
        evidence=evidence,
        profile={},
        image_analysis={},
        recent_dialogue={},
        input_mode="clear_question",
        params={},
    )

    assert evidence["graphEvidencePack"] == {}
    assert "图谱证据包" not in context


@pytest.mark.asyncio
async def test_tutor_agent_tries_fallback_llm_when_primary_stream_and_core_loop_fail() -> None:
    secondary_llm = _StreamingTutorLLM()
    tutor = TutorAgent(
        compactor=ConversationCompactor(token_budget=1000, keep_recent_turns=4),
        summary_store=InMemoryConversationSummaryStore(),
        llm_client=_FailingTutorLLM(),
        llm_fallback_clients=[secondary_llm],
    )
    params = {
        "conversationId": "conv-llm-fallback",
        "messages": [{"role": "user", "content": "What is Java?"}],
        "query": "What is Java?",
        "rewrittenQuery": "Java programming language",
        "retrievalResult": {
            "documents": [
                {
                    "title": "Java",
                    "channel": "hybrid",
                    "evidence": "Java is a class-based programming language.",
                }
            ]
        },
    }

    events = [
        event
        async for event in tutor.run(
            task_id="task-llm-fallback",
            trace_id="trace-llm-fallback",
            seq=1,
            service_type="TUTORING",
            params=params,
            snapshot=_build_snapshot(),
            system_prompt="test",
        )
    ]

    result_text = "".join(
        event.payload.text
        for event in events
        if event.event == "result_chunk"
    )
    assert [event.event for event in events] == ["progress", "result_chunk"]
    assert result_text == "LLM generated answer"
    assert secondary_llm.client.stream_calls == 1


@pytest.mark.asyncio
async def test_tutor_core_loop_returns_evidence_fallback_when_iterations_exceeded() -> None:
    tutor = TutorAgent(
        compactor=ConversationCompactor(token_budget=1000, keep_recent_turns=4),
        summary_store=InMemoryConversationSummaryStore(),
        llm_client=RuleBasedTutorLLM(),
    )
    params = {
        "query": "How does a B+ tree support range queries?",
        "retrievalResult": {
            "documents": [
                {
                    "title": "B+ tree range query",
                    "channel": "hybrid",
                    "evidence": "Leaf nodes are linked so scans can continue after the first key match.",
                }
            ]
        },
    }

    answer = await tutor._run_with_agent_core_loop(
        llm_client=_LoopingToolTutorLLM(),
        system_prompt="test",
        user_query=params["query"],
        params=params,
        persisted_summary=None,
    )

    assert "基于已检索到的证据先给出有限回答" in answer
    assert "B+ tree range query" in answer
    assert "工具探索已达到上限" in answer


@pytest.mark.asyncio
async def test_tutor_agent_uses_real_llm_compression_for_explicit_length_limit() -> None:
    llm = _LengthAwareTutorLLM()
    tutor = TutorAgent(
        summary_store=InMemoryConversationSummaryStore(),
        llm_client=llm,
    )
    params = {
        "conversationId": "conv-length",
        "messages": [{"role": "user", "content": "80字内总结红黑树"}],
        "query": "80字内总结红黑树",
        "retrievalResult": {"documents": [{"title": "红黑树", "evidence": "红黑树保持近似平衡。"}]},
    }

    events = [
        event
        async for event in tutor.run(
            task_id="task-length",
            trace_id="trace-length",
            seq=1,
            service_type="TUTORING",
            params=params,
            snapshot=_build_snapshot(),
            system_prompt="test",
        )
    ]

    result_text = "".join(event.payload.text for event in events if event.event == "result_chunk")
    assert len(result_text) <= 80
    assert llm.client.stream_calls == 0
    assert llm.client.chat_calls == 2
    assert params["responseConstraints"]["maxChars"] == 80


def test_tutor_deep_mode_adds_quality_instruction_without_changing_route() -> None:
    tutor = TutorAgent(
        summary_store=InMemoryConversationSummaryStore(),
        llm_client=RuleBasedTutorLLM(),
    )

    context = tutor._build_enriched_message(
        user_query="Analyze synchronized lock design deeply",
        memory={},
        context={},
        evidence={"documents": [{"title": "SSE lock design", "evidence": "lock evidence"}]},
        profile={},
        image_analysis={},
        recent_dialogue={},
        input_mode="clear_question",
        params={"reasoningMode": "DEEP"},
    )

    assert "深度思考模式已开启" in context
    assert "正常辅导/资源生成路线" in context
    assert "自检" in context


@pytest.mark.asyncio
async def test_tutor_agent_golden_eval_preserves_guidance_contract() -> None:
    tutor = TutorAgent(
        compactor=ConversationCompactor(token_budget=1000, keep_recent_turns=4),
        summary_store=InMemoryConversationSummaryStore(),
        llm_client=RuleBasedTutorLLM(),
    )
    params = {
        "conversationId": "conv-golden-tutor",
        "messages": [
            {"role": "user", "content": "老师，我总是分不清联合索引什么时候会失效"},
            {"role": "assistant", "content": "我们先看查询条件是否符合最左匹配。"},
        ],
        "query": "联合索引为什么会失效?",
        "rewrittenQuery": "数据库原理 联合索引 失效 最左匹配",
        "retrievalResult": {
            "documents": [
                {
                    "title": "联合索引失效场景",
                    "channel": "hybrid",
                    "evidence": "联合索引需要按索引字段顺序匹配查询条件，跳过最左字段会削弱索引效果。",
                }
            ],
            "sourcesSummary": "命中联合索引失效场景。",
        },
    }

    events = [
        event
        async for event in tutor.run(
            task_id="task-golden-tutor",
            trace_id="trace-golden-tutor",
            seq=1,
            service_type="TUTORING",
            params=params,
            snapshot=_build_snapshot(),
            system_prompt=tutor.system_prompt(_build_snapshot()),
        )
    ]

    assert [event.event for event in events] == ["progress", "result_chunk"]
    assert params["inputMode"] == "clear_question"
    assert events[0].dialog_state is not None
    assert events[0].dialog_state.pedagogy_strategy == "retrieval_grounded_scaffold"
    assert events[0].dialog_state.next_action == "ask_follow_up"
    assert "联合索引" in events[1].payload.text
    assert "最左字段" in events[1].payload.text


@pytest.mark.asyncio
async def test_tutor_rule_based_smoke_consumes_graph_evidence_pack() -> None:
    tutor = TutorAgent(
        compactor=ConversationCompactor(token_budget=1000, keep_recent_turns=4),
        summary_store=InMemoryConversationSummaryStore(),
        llm_client=RuleBasedTutorLLM(),
    )
    params = {
        "conversationId": "conv-graph-smoke",
        "messages": [{"role": "user", "content": "学习 B+树 前需要理解哪些概念？"}],
        "query": "学习 B+树 前需要理解哪些概念？",
        "rewrittenQuery": "数据库 B+树 学习路径 前置依赖",
        "graphIntent": "PREREQUISITE_PATH",
        "retrievalResult": {
            "documents": [
                {
                    "title": "B+树",
                    "channel": "graph",
                    "evidence": "B+树常用于数据库索引，适合范围查询。",
                }
            ]
        },
        "graphRetrievalResult": {
            "graphIntent": "PREREQUISITE_PATH",
            "results": [
                ("database-index", "数据库索引", 11.0, "graph_1hop"),
                ("btree", "B树", 9.0, "graph_2hop"),
            ],
        },
    }

    events = [
        event
        async for event in tutor.run(
            task_id="task-graph-smoke",
            trace_id="trace-graph-smoke",
            seq=1,
            service_type="TUTORING",
            params=params,
            snapshot=_build_snapshot(),
            system_prompt="test",
        )
    ]

    text = "".join(event.payload.text for event in events if event.event == "result_chunk")
    assert "结合图谱证据" in text
    assert "数据库索引、B树、B+树" in text
    assert "不要硬背顺序" in text
    assert "graphIntent" not in text
    assert "source=" not in text


@pytest.mark.asyncio
async def test_tutor_agent_compacts_long_conversation_and_emits_dialog_state() -> None:
    tutor = TutorAgent(
        compactor=ConversationCompactor(token_budget=30, keep_recent_turns=2),
        summary_store=InMemoryConversationSummaryStore(),
        llm_client=RuleBasedTutorLLM(),
    )
    snapshot = _build_snapshot()
    params = {
        "conversationId": "conv-001",
        "messages": [
            {"role": "user", "content": "老师我不太懂什么是索引"},
            {"role": "assistant", "content": "索引可以帮助快速定位数据"},
            {"role": "user", "content": "联合索引和普通索引有什么区别"},
            {"role": "assistant", "content": "它们的适用场景不同"},
            {"role": "user", "content": "那我做题时总是分不清"},
        ],
        "query": "联合索引",
        "rewrittenQuery": "数据库原理 联合索引",
        "retrievalResult": {
            "documents": [
                {"title": "联合索引", "channel": "phrase"},
                {"title": "数据库索引导学", "channel": "hybrid"},
            ]
        },
    }

    events = [
        event
        async for event in tutor.run(
            task_id="task-001",
            trace_id="trace-001",
            seq=1,
            service_type="TUTORING",
            params=params,
            snapshot=snapshot,
            system_prompt="test",
        )
    ]

    assert [event.event for event in events] == ["progress", "result_chunk"]
    assert params["conversationSummary"]
    assert params["structuredConversationSummary"]["summaryText"]
    assert params["structuredConversationSummary"]["lastUserMessage"] == "联合索引和普通索引有什么区别"
    assert events[0].dialog_state is not None
    assert events[0].dialog_state.pedagogy_strategy == "retrieval_grounded_scaffold"
    assert "关于" in events[1].payload.text
    assert "联合索引" in events[1].payload.text


def test_conversation_compactor_keeps_recent_messages() -> None:
    compactor = ConversationCompactor(token_budget=20, keep_recent_turns=2)
    messages = [
        {"role": "user", "content": "a" * 20},
        {"role": "assistant", "content": "b" * 20},
        {"role": "user", "content": "c" * 20},
        {"role": "assistant", "content": "d" * 20},
    ]

    result = compactor.compact(messages)

    assert result.was_compacted is True
    assert len(result.compacted_messages) == 3
    assert result.structured_summary.summary_text
    assert result.compacted_messages[-2]["content"] == "c" * 20
    assert result.compacted_messages[-1]["content"] == "d" * 20


@pytest.mark.asyncio
async def test_tutor_agent_loads_and_persists_structured_summary() -> None:
    store = InMemoryConversationSummaryStore()
    await store.save_summary(
        ConversationSummaryDocument(
            conversationId="conv-001",
            userId=None,
            taskId="task-old",
            topicFocus=["索引"],
            learnerGoal="掌握联合索引",
            knownGaps=["总是分不清使用条件"],
            unresolvedQuestions=["联合索引和普通索引有什么区别？"],
            preferredHelpStyle="step_by_step",
            lastUserMessage="老师我还是不懂",
            recentProgress=["前面已经讲过概念定义"],
            summaryText="主题: 索引 ; 目标: 掌握联合索引 ; 薄弱点: 总是分不清使用条件 ; 未解决问题: 联合索引和普通索引有什么区别？",
        )
    )
    tutor = TutorAgent(
        compactor=ConversationCompactor(token_budget=30, keep_recent_turns=2),
        summary_store=store,
        llm_client=RuleBasedTutorLLM(),
    )

    params = {
        "conversationId": "conv-001",
        "messages": [
            {"role": "user", "content": "老师我不太懂什么是索引"},
            {"role": "assistant", "content": "索引可以帮助快速定位数据"},
            {"role": "user", "content": "联合索引和普通索引有什么区别"},
            {"role": "assistant", "content": "它们的适用场景不同"},
            {"role": "user", "content": "那我做题时总是分不清"},
        ],
        "query": "联合索引",
        "rewrittenQuery": "数据库原理 联合索引",
        "retrievalResult": {"documents": [{"title": "联合索引", "channel": "phrase"}]},
    }
    events = [
        event
        async for event in tutor.run(
            task_id="task-001",
            trace_id="trace-001",
            seq=1,
            service_type="TUTORING",
            params=params,
            snapshot=_build_snapshot(),
            system_prompt="test",
        )
    ]

    assert "联合索引" in events[1].payload.text
    assert len(store.documents) == 1
    saved = store.documents[0]
    assert "索引" in saved.topic_focus
    assert saved.summary_text


def test_conversation_compactor_merges_previous_summary_across_compactions() -> None:
    compactor = ConversationCompactor(token_budget=20, keep_recent_turns=2)
    previous_summary = StructuredConversationSummary(
        topicFocus=["synchronized", "线程安全"],
        learnerGoal="掌握 Java 并发基础",
        knownGaps=["分不清 volatile 和 synchronized"],
        unresolvedQuestions=["什么时候更适合用 volatile？"],
        preferredHelpStyle="step_by_step",
        lastUserMessage="volatile 和 synchronized 有什么不同",
        recentProgress=["已讲线程和进程区别"],
        summaryText="旧摘要",
    )
    messages = [
        {"role": "user", "content": "死锁是怎么产生的，怎么避免"},
        {"role": "assistant", "content": "可以从互斥、占有并等待几个条件来理解"},
        {"role": "user", "content": "线程池的核心参数有哪些"},
        {"role": "assistant", "content": "核心线程数、最大线程数、阻塞队列等"},
    ]

    result = compactor.compact(messages, previous_summary=previous_summary)

    assert result.was_compacted is True
    assert "synchronized" in result.structured_summary.topic_focus
    assert "死锁" in result.structured_summary.topic_focus
    assert result.structured_summary.learner_goal == "掌握 Java 并发基础"
    assert "什么时候更适合用 volatile？" in result.structured_summary.unresolved_questions
    assert len(result.structured_summary.summary_text) <= 500


@pytest.mark.asyncio
async def test_conversation_compactor_llm_refiner_recovers_nonstandard_deadlock_topic() -> None:
    class FakeSummaryRefiner:
        async def refine(self, *, messages, rule_summary):
            assert any("两把锁互相等" in str(message.get("content", "")) for message in messages)
            assert rule_summary["summaryText"]
            return {
                "topicFocus": ["死锁"],
                "canonicalTopicKeys": ["deadlock"],
                "aliases": {"deadlock": ["两把锁互相等的东西"]},
                "knownGaps": ["死锁"],
                "unresolvedQuestions": ["我搞不太明白那个两把锁互相等的东西"],
                "confidence": 0.88,
                "summaryText": "主题: 死锁 ; 薄弱点: 死锁 ; 未解决问题: 两把锁互相等的东西",
            }

    compactor = ConversationCompactor(
        token_budget=12,
        keep_recent_turns=1,
        summary_refiner=FakeSummaryRefiner(),
    )

    result = await compactor.compact_async(
        [
            {"role": "user", "content": "我搞不太明白那个两把锁互相等的东西"},
            {"role": "assistant", "content": "我们可以从等待关系讲起。"},
            {"role": "user", "content": "它为什么会卡住"},
        ]
    )

    assert result.was_compacted is True
    assert "死锁" in result.structured_summary.topic_focus
    assert "deadlock" in result.structured_summary.canonical_topic_keys
    assert result.structured_summary.topic_aliases["deadlock"] == ["两把锁互相等的东西"]
    assert result.structured_summary.confidence >= 0.88


def test_conversation_compactor_extracts_chinese_topic_focus_terms() -> None:
    compactor = ConversationCompactor(token_budget=1000, keep_recent_turns=4)
    result = compactor.compact(
        [
            {"role": "user", "content": "什么是线程安全"},
            {"role": "user", "content": "死锁是怎么产生的，怎么避免"},
            {"role": "user", "content": "线程池的核心参数有哪些"},
        ]
    )

    assert "线程安全" in result.structured_summary.topic_focus
    assert "死锁" in result.structured_summary.topic_focus
    assert "线程池核心参数" in result.structured_summary.topic_focus


def test_conversation_compactor_extracts_mixed_language_topic_focus_terms() -> None:
    compactor = ConversationCompactor(token_budget=1000, keep_recent_turns=4)
    result = compactor.compact(
        [
            {"role": "user", "content": "synchronized 关键字怎么用"},
            {"role": "user", "content": "volatile 和 synchronized 有什么不同"},
            {"role": "user", "content": "CountDownLatch 和 CyclicBarrier 区别"},
        ]
    )

    topic_focus = result.structured_summary.topic_focus
    assert "synchronized" in topic_focus
    assert "volatile" in topic_focus
    assert "CountDownLatch" in topic_focus
    assert "CyclicBarrier" in topic_focus


def test_conversation_compactor_extracts_follow_up_topic_focus_terms() -> None:
    compactor = ConversationCompactor(token_budget=1000, keep_recent_turns=4)
    result = compactor.compact(
        [
            {"role": "user", "content": "那我前面问的 synchronized 具体怎么用，能再举个例子吗"},
            {"role": "user", "content": "回到死锁问题，除了避免还有别的解决办法吗"},
        ]
    )

    topic_focus = result.structured_summary.topic_focus
    assert "synchronized" in topic_focus
    assert "死锁" in topic_focus


@pytest.mark.asyncio
async def test_mongo_summary_store_maps_save_and_load() -> None:
    class FakeCollection:
        def __init__(self) -> None:
            self.saved: list[dict] = []

        def update_one(self, criteria: dict, update: dict, upsert: bool):
            assert upsert is True
            payload = update["$set"]
            for index, item in enumerate(self.saved):
                if (
                    item.get("conversationId") == criteria.get("conversationId")
                    and item.get("userId") == criteria.get("userId")
                ):
                    self.saved[index] = payload
                    return
            self.saved.append(payload)

        def find_one(self, criteria: dict, sort: list[tuple[str, int]]):
            del sort
            for item in reversed(self.saved):
                if item.get("conversationId") == criteria.get("conversationId"):
                    return {**item, "_id": "fake"}
            return None

    collection = FakeCollection()
    store = MongoConversationSummaryStore(collection=collection)
    document = ConversationSummaryDocument(
        conversationId="conv-002",
        userId="user-001",
        taskId="task-002",
        topicFocus=["联合索引"],
        learnerGoal="理解使用条件",
        knownGaps=["不会判断最左匹配"],
        unresolvedQuestions=["为什么会失效？"],
        preferredHelpStyle="example_first",
        lastUserMessage="为什么联合索引会失效？",
        recentProgress=["已区分普通索引与联合索引"],
        summaryText="主题: 联合索引 ; 目标: 理解使用条件 ; 薄弱点: 不会判断最左匹配 ; 未解决问题: 为什么会失效？",
    )

    await store.save_summary(document)
    loaded = await store.get_latest_summary(conversation_id="conv-002", user_id="user-001")

    assert collection.saved
    assert loaded is not None
    assert loaded.topic_focus == ["联合索引"]
