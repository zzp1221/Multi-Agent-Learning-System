import pytest

from src.ai_modules.agents.deep_reasoning_agent import DeepReasoningAgent
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
)
from src.ai_modules.runtime.skill_loader import SkillPromptLoader


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
        raise RuntimeError("primary stream failed")
        yield ""  # pragma: no cover


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

    async def chat_completion(self, **kwargs):
        del kwargs
        raise AssertionError("secondary stream should satisfy the tutor response")

    async def chat_completion_stream(self, **kwargs):
        del kwargs
        self.stream_calls += 1
        for token in ["LLM ", "generated ", "answer"]:
            yield token


class _StreamingTutorLLM:
    def __init__(self) -> None:
        self.client = _StreamingTutorClient()


class _DeepReasoningLLM:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, **kwargs):
        del kwargs
        self.calls += 1
        return AssistantTurn(content=f"LLM deep step {self.calls}")


def test_tutor_agent_system_prompt_loads_skill_and_context() -> None:
    tutor = TutorAgent(
        summary_store=InMemoryConversationSummaryStore(),
        llm_client=RuleBasedTutorLLM(),
    )

    prompt = tutor.system_prompt(_build_snapshot())

    assert "# 辅导智能体" in prompt
    assert "回复原则" in prompt
    assert "read_retrieval_evidence" in prompt
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
    )

    assert "图谱证据包" in context
    assert "图谱题型：学习路径/前置依赖" in context
    assert "数据库索引（一跳图谱相关概念）" in context
    assert "B树（二跳图谱补充概念）" in context
    assert "source=" not in context
    assert "graphIntent:" not in context
    assert "学习路径相关候选集合（非严格顺序）：数据库索引、B树、B+树" in context


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
async def test_deep_reasoning_agent_passes_params_to_input_mode_classifier() -> None:
    llm = _DeepReasoningLLM()
    agent = DeepReasoningAgent(
        compactor=ConversationCompactor(token_budget=1000, keep_recent_turns=4),
        summary_store=InMemoryConversationSummaryStore(),
        llm_client=llm,
    )
    params = {
        "conversationId": "conv-deep",
        "messages": [{"role": "user", "content": "Analyze synchronized lock design deeply"}],
        "query": "Analyze synchronized lock design deeply",
        "queryType": "DEEP_REASONING",
        "retrievalResult": {"documents": [{"title": "SSE lock design", "channel": "hybrid"}]},
    }

    events = [
        event
        async for event in agent.run(
            task_id="task-deep",
            trace_id="trace-deep",
            seq=1,
            service_type="TUTORING",
            params=params,
            snapshot=_build_snapshot(),
            system_prompt="test",
        )
    ]

    assert [event.event for event in events] == [
        "progress",
        "progress",
        "progress",
        "progress",
        "result_chunk",
    ]
    assert params["inputMode"] == "clear_question"
    assert events[-1].payload.text == "LLM deep step 4"


@pytest.mark.asyncio
async def test_deep_reasoning_agent_disables_deterministic_direct_fallback() -> None:
    agent = DeepReasoningAgent(
        compactor=ConversationCompactor(token_budget=1000, keep_recent_turns=4),
        summary_store=InMemoryConversationSummaryStore(),
        llm_client=object(),
    )

    with pytest.raises(RuntimeError, match="deterministic fallback is not allowed"):
        await agent._run_direct_reasoning_step(
            system_prompt="test",
            user_query="Explain indexes deeply",
            step_key="analysis",
            artifacts={},
        )


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
