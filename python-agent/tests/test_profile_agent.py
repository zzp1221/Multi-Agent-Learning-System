import pytest

from src.ai_modules.agents.profile_agent import ProfileAgent
from src.ai_modules.llms import RuleBasedProfileLLM
from src.ai_modules.memory import (
    ConversationSummaryDocument,
    InMemoryConversationSummaryStore,
    InMemoryProfileStore,
    PostgresProfileStore,
)
from src.ai_modules.memory.profile_feature_registry import (
    canonical_match_dimensions,
    exclusive_dimensions,
    get_feature_dimension_spec,
    resolved_by_skill_mastery_dimensions,
)
from src.ai_modules.models import LearnerProfileDimensions
from src.ai_modules.runtime import SystemSnapshot
from src.ai_modules.runtime.skill_loader import SkillPromptLoader


def _build_snapshot() -> SystemSnapshot:
    return SystemSnapshot(
        current_course="数据库原理",
        current_chapter="索引",
        course_progress=0.4,
        student_name="张三",
        student_level="BASIC",
        knowledge_gaps=["联合索引"],
        preferred_style="step_by_step",
        recent_mistakes=["最左匹配判断错误"],
        session_id="conv-profile",
        conversation_length=6,
        total_tokens_used=512,
        wiki_pages_count=12,
        last_index_update="2026-05-02",
        recent_activities=["完成索引练习"],
    )


def test_profile_agent_system_prompt_loads_skill_and_context() -> None:
    agent = ProfileAgent(profile_store=InMemoryProfileStore())

    prompt = agent.system_prompt(_build_snapshot())

    assert "# 画像智能体" in prompt
    assert "画像构建流程" in prompt
    assert "read_profile" in prompt
    assert "update_profile" in prompt
    assert "## 当前上下文" in prompt
    assert f"课程: {_build_snapshot().current_course}" in prompt


def test_profile_skill_prompt_falls_back_when_skill_is_missing(tmp_path) -> None:
    loader = SkillPromptLoader(skills_root=tmp_path)

    prompt = loader.build_system_prompt(
        skill_name="profile",
        snapshot=_build_snapshot(),
        fallback_prompt="画像提示词兜底内容",
    )

    assert prompt == "画像提示词兜底内容"


def test_profile_agent_infers_basic_foundation_from_intro_questions() -> None:
    agent = ProfileAgent(profile_store=InMemoryProfileStore())

    assert agent._infer_knowledge_foundation("什么是 MATLAB？", {"studentLevel": "UNKNOWN"}) == "BASIC"
    assert agent._infer_knowledge_foundation("解释一下基础概念", {"knowledgeBase": "待分析"}) == "BASIC"
    assert agent._infer_knowledge_foundation("我想继续学习", {"knowledgeFoundation": "INTERMEDIATE"}) == "INTERMEDIATE"


@pytest.mark.asyncio
async def test_profile_agent_updates_profile_and_caches_dimensions() -> None:
    class FakeProfileAnalyzer:
        async def analyze(self, *, context_payload):
            del context_payload
            return LearnerProfileDimensions(
                knowledgeFoundation="BASIC",
                learningGoal="掌握联合索引的使用条件",
                professionalBackground="计算机本科生",
                learningPreference="step_by_step",
                cognitiveStyle="procedural_oriented",
                weakPoints=["不会判断最左匹配"],
                learningPace="steady",
                confidenceLevel="LOW",
                source="CONVERSATION",
                summaryText="画像更新完成：学生需要一步步理解联合索引使用条件。",
            )

    store = InMemoryProfileStore()
    agent = ProfileAgent(
        profile_store=store,
        llm_client=RuleBasedProfileLLM(),
        profile_analyzer=FakeProfileAnalyzer(),
    )
    params = {
        "userId": "00000000-0000-0000-0000-000000000111",
        "conversationId": "00000000-0000-0000-0000-000000000222",
        "messages": [
            {"role": "user", "content": "老师，我刚学数据库，想一步步理解联合索引。"},
            {"role": "assistant", "content": "我们先从定义开始。"},
            {"role": "user", "content": "我做题时总是分不清什么时候会失效。"},
        ],
        "structuredConversationSummary": {
            "learnerGoal": "掌握联合索引的使用条件",
            "knownGaps": ["不会判断最左匹配"],
            "preferredHelpStyle": "step_by_step",
        },
        "profile": {
            "studentLevel": "BASIC",
            "professionalBackground": "计算机本科生",
        },
    }

    events = [
        event
        async for event in agent.run(
            task_id="task-profile",
            trace_id="trace-profile",
            seq=1,
            service_type="PROFILE_BUILD",
            params=params,
            snapshot=_build_snapshot(),
            system_prompt="test",
        )
    ]

    snapshot = await store.read_profile("00000000-0000-0000-0000-000000000111")

    assert [event.event for event in events] == ["progress", "result_chunk"]
    assert params["analyzedProfileDimensions"]["knowledgeFoundation"] == "BASIC"
    assert params["analyzedProfileDimensions"]["learningPreference"] == "step_by_step"
    assert "一步步理解联合索引使用条件" in params["profileUpdate"]["summaryText"]
    assert snapshot is not None
    assert snapshot.version == 1
    assert snapshot.profile.professional_background == "计算机本科生"
    assert snapshot.profile.weak_points == ["不会判断最左匹配"]
    assert "一步步理解联合索引使用条件" in events[1].payload.text


@pytest.mark.asyncio
async def test_profile_agent_reads_persisted_summary_as_analysis_input() -> None:
    captured_context: dict[str, dict] = {}

    class CapturingProfileAnalyzer:
        async def analyze(self, *, context_payload):
            captured_context["payload"] = context_payload
            return LearnerProfileDimensions(
                knowledgeFoundation="BASIC",
                learningGoal="understand lock waiting",
                professionalBackground="computer science student",
                learningPreference="step_by_step",
                cognitiveStyle="procedural_oriented",
                weakPoints=["deadlock"],
                learningPace="steady",
                confidenceLevel="MEDIUM",
                confidenceScore=0.72,
                source="CONVERSATION",
                summaryText="profile updated",
            )

    summary_store = InMemoryConversationSummaryStore()
    await summary_store.save_summary(
        ConversationSummaryDocument(
            conversationId="conv-profile-summary",
            userId="user-profile-summary",
            topicFocus=["deadlock"],
            canonicalTopicKeys=["deadlock"],
            aliases={"deadlock": ["two locks wait for each other"]},
            knownGaps=["circular wait"],
            unresolvedQuestions=["why does the program stop"],
            summaryText="student is confused about deadlock",
        )
    )
    agent = ProfileAgent(
        profile_store=InMemoryProfileStore(),
        summary_store=summary_store,
        llm_client=RuleBasedProfileLLM(),
        profile_analyzer=CapturingProfileAnalyzer(),
    )
    params = {
        "userId": "user-profile-summary",
        "conversationId": "conv-profile-summary",
        "messages": [{"role": "user", "content": "I still do not get locks."}],
        "structuredConversationSummary": {
            "topicFocus": ["mutex"],
            "aliases": {"deadlock": ["lock waiting"]},
            "knownGaps": ["mutual exclusion"],
        },
    }

    await agent._tool_analyze_dialogue(tool_input={}, params=params)

    payload = captured_context["payload"]
    assert payload["persistedConversationSummary"]["canonicalTopicKeys"] == ["deadlock"]
    assert payload["structuredConversationSummary"]["topicFocus"] == ["deadlock", "mutex"]
    assert payload["structuredConversationSummary"]["knownGaps"] == ["circular wait", "mutual exclusion"]
    assert payload["structuredConversationSummary"]["aliases"]["deadlock"] == [
        "two locks wait for each other",
        "lock waiting",
    ]


@pytest.mark.asyncio
async def test_profile_agent_falls_back_when_llm_returns_invalid_json() -> None:
    class BrokenProfileAnalyzer:
        async def analyze(self, *, context_payload):
            del context_payload
            raise ValueError("no json object found")

    store = InMemoryProfileStore()
    agent = ProfileAgent(
        profile_store=store,
        llm_client=RuleBasedProfileLLM(),
        profile_analyzer=BrokenProfileAnalyzer(),
    )
    params = {
        "userId": "00000000-0000-0000-0000-000000000888",
        "conversationId": "00000000-0000-0000-0000-000000000999",
        "messages": [
            {"role": "user", "content": "老师，我刚学数据库，想一步步理解联合索引。"},
            {"role": "assistant", "content": "我们先从索引顺序开始。"},
            {"role": "user", "content": "我还是总错，分不清最左匹配什么时候失效。"},
        ],
        "structuredConversationSummary": {
            "learnerGoal": "掌握联合索引的使用条件",
            "knownGaps": ["不会判断最左匹配"],
            "preferredHelpStyle": "step_by_step",
        },
        "profile": {
            "studentLevel": "BASIC",
            "professionalBackground": "计算机本科生",
        },
    }

    events = [
        event
        async for event in agent.run(
            task_id="task-profile-invalid-json",
            trace_id="trace-profile-invalid-json",
            seq=1,
            service_type="PROFILE_BUILD",
            params=params,
            snapshot=_build_snapshot(),
            system_prompt="test",
        )
    ]

    snapshot = await store.read_profile("00000000-0000-0000-0000-000000000888")

    assert [event.event for event in events] == ["progress", "result_chunk"]
    assert params["analyzedProfileDimensions"]["source"] == "CONVERSATION"
    assert params["analyzedProfileDimensions"]["knowledgeFoundation"] == "BASIC"
    assert params["analyzedProfileDimensions"]["weakPoints"] == ["不会判断最左匹配"]
    assert params["profileUpdate"]["summaryText"]
    assert snapshot is not None
    assert snapshot.profile.source == "CONVERSATION"
    assert snapshot.profile.summary_text


@pytest.mark.asyncio
async def test_profile_agent_golden_eval_preserves_profile_contract() -> None:
    class GoldenProfileAnalyzer:
        async def analyze(self, *, context_payload):
            del context_payload
            return LearnerProfileDimensions(
                knowledgeFoundation="INTERMEDIATE",
                learningGoal="复盘联合索引判题错误",
                professionalBackground="计算机本科生",
                learningPreference="step_by_step",
                cognitiveStyle="procedural_oriented",
                weakPoints=["旧弱点"],
                learningPace="steady",
                confidenceLevel="MEDIUM",
                confidenceScore=0.7,
                source="CONVERSATION",
                summaryText="",
            )

    store = InMemoryProfileStore()
    agent = ProfileAgent(
        profile_store=store,
        llm_client=RuleBasedProfileLLM(),
        profile_analyzer=GoldenProfileAnalyzer(),
    )
    params = {
        "userId": "00000000-0000-0000-0000-000000000123",
        "conversationId": "00000000-0000-0000-0000-000000000124",
        "profileSource": "PRACTICE",
        "messages": [{"role": "user", "content": "我想复盘联合索引，最左匹配还是容易错。"}],
        "practiceQuestionBatch": {
            "topic": "联合索引",
            "questions": [
                {
                    "questionId": "golden-q1",
                    "questionType": "SINGLE_CHOICE",
                    "knowledgeTags": ["联合索引", "最左匹配"],
                }
            ],
        },
        "judgeResult": {
            "summary": "学生对最左匹配和索引条件判断仍不稳定。",
            "accuracy": 0.5,
            "weakKnowledgeTags": ["最左匹配", "索引条件"],
            "items": [
                {
                    "questionId": "golden-q1",
                    "isCorrect": False,
                    "knowledgeTags": ["最左匹配"],
                }
            ],
        },
    }

    events = [
        event
        async for event in agent.run(
            task_id="task-profile-golden",
            trace_id="trace-profile-golden",
            seq=1,
            service_type="PRACTICE_JUDGE",
            params=params,
            snapshot=_build_snapshot(),
            system_prompt=agent.system_prompt(_build_snapshot()),
        )
    ]

    dimensions = params["analyzedProfileDimensions"]
    required_keys = {
        "knowledgeFoundation",
        "learningGoal",
        "professionalBackground",
        "learningPreference",
        "cognitiveStyle",
        "weakPoints",
        "learningPace",
        "confidenceLevel",
        "confidenceScore",
        "skillMastery",
        "weakPointDetails",
        "currentGoal",
        "preferredResourceTypes",
        "explanationPreference",
        "inferredRecommendations",
        "evidence",
        "source",
        "summaryText",
    }

    assert [event.event for event in events] == ["progress", "result_chunk"]
    assert required_keys.issubset(dimensions.keys())
    assert dimensions["source"] == "PRACTICE"
    assert dimensions["confidenceLevel"] == "MEDIUM"
    assert dimensions["confidenceScore"] <= 0.61
    assert dimensions["weakPoints"][:2] == ["最左匹配", "索引条件"]
    assert dimensions["skillMastery"]["联合索引"] == 0.5
    assert dimensions["skillMastery"]["最左匹配"] <= 0.39
    assert params["profileUpdate"]["summaryText"]


@pytest.mark.asyncio
async def test_profile_agent_raises_when_primary_store_update_fails() -> None:
    class BrokenProfileStore:
        async def read_profile(self, user_id: str):
            del user_id
            raise RuntimeError("db unavailable")

        async def update_profile(
            self,
            *,
            user_id: str,
            dimensions: LearnerProfileDimensions,
            source_session_id: str | None = None,
        ):
            del user_id, dimensions, source_session_id
            raise RuntimeError("db unavailable")

    class FakeProfileAnalyzer:
        async def analyze(self, *, context_payload):
            del context_payload
            return LearnerProfileDimensions(
                knowledgeFoundation="BASIC",
                learningGoal="掌握联合索引",
                professionalBackground="计算机本科生",
                learningPreference="step_by_step",
                cognitiveStyle="procedural_oriented",
                weakPoints=["最左匹配"],
                learningPace="steady",
                confidenceLevel="LOW",
                source="CONVERSATION",
                summaryText="画像更新完成：联合索引理解仍需强化。",
            )

    agent = ProfileAgent(
        profile_store=BrokenProfileStore(),
        llm_client=RuleBasedProfileLLM(),
        profile_analyzer=FakeProfileAnalyzer(),
    )
    params = {
        "userId": "00000000-0000-0000-0000-000000000333",
        "messages": [{"role": "user", "content": "我不太懂联合索引，能慢一点吗？"}],
    }

    with pytest.raises(RuntimeError, match="db unavailable"):
        _ = [
            event
            async for event in agent.run(
                task_id="task-profile-fallback",
                trace_id="trace-profile-fallback",
                seq=1,
                service_type="PROFILE_BUILD",
                params=params,
                snapshot=_build_snapshot(),
                system_prompt="test",
            )
        ]


@pytest.mark.asyncio
async def test_profile_agent_forces_practice_judge_signal_into_profile() -> None:
    class FakeProfileAnalyzer:
        async def analyze(self, *, context_payload):
            del context_payload
            return LearnerProfileDimensions(
                knowledgeFoundation="INTERMEDIATE",
                learningGoal="继续复习旧知识点",
                professionalBackground="计算机专业",
                learningPreference="step_by_step",
                cognitiveStyle="mixed",
                weakPoints=["银行家算法"],
                learningPace="normal",
                confidenceLevel="HIGH",
                confidenceScore=0.88,
                skillMastery={"银行家算法": 0.86},
                currentGoal={"shortTerm": "理解银行家算法的基本概念", "midTerm": "", "context": "旧画像", "urgency": "MEDIUM"},
                source="CONVERSATION",
                summaryText="旧画像摘要",
            )

    store = InMemoryProfileStore()
    agent = ProfileAgent(
        profile_store=store,
        llm_client=RuleBasedProfileLLM(),
        profile_analyzer=FakeProfileAnalyzer(),
    )
    params = {
        "userId": "00000000-0000-0000-0000-000000000666",
        "conversationId": "00000000-0000-0000-0000-000000000777",
        "profileSource": "PRACTICE",
        "messages": [{"role": "user", "content": "我想做并发编程练习题。"}],
        "practiceQuestionBatch": {
            "topic": "并发编程",
            "questions": [
                {"questionId": "q1", "questionType": "SINGLE_CHOICE", "stem": "volatile 题", "options": ["A", "B"], "answer": "A", "knowledgeTags": ["volatile可见性"], "difficultyLevel": "medium"},
                {"questionId": "q2", "questionType": "SHORT_ANSWER", "stem": "synchronized 锁对象", "options": [], "answer": "锁对象说明", "knowledgeTags": ["synchronized锁对象"], "difficultyLevel": "medium"},
            ],
        },
        "judgeResult": {
            "summary": "本次测试共 5 题，全部回答错误。学生在并发编程核心概念上存在明显不足。",
            "accuracy": 0.0,
            "weakKnowledgeTags": ["volatile可见性", "synchronized锁对象"],
            "items": [
                {"questionId": "q1", "isCorrect": False, "knowledgeTags": ["volatile可见性"]},
                {"questionId": "q2", "isCorrect": False, "knowledgeTags": ["synchronized锁对象"]},
            ],
        },
    }

    _ = [
        event
        async for event in agent.run(
            task_id="task-profile-fallback",
            trace_id="trace-profile-practice",
            seq=1,
            service_type="PRACTICE_JUDGE",
            params=params,
            snapshot=_build_snapshot(),
            system_prompt="test",
        )
    ]

    snapshot = await store.read_profile("00000000-0000-0000-0000-000000000666")

    assert snapshot is not None
    assert snapshot.profile.source == "PRACTICE"
    assert snapshot.profile.confidence_level == "LOW"
    assert snapshot.profile.confidence_score <= 0.36
    assert snapshot.profile.current_goal.short_term == "掌握并发编程"
    assert snapshot.profile.weak_points[:2] == ["synchronized锁对象", "volatile可见性"]
    assert snapshot.profile.skill_mastery["并发编程"] <= 0.05
    assert snapshot.profile.skill_mastery["volatile可见性"] <= 0.27


@pytest.mark.asyncio
async def test_postgres_profile_store_rolls_back_only_vector_write() -> None:
    executed_sql: list[str] = []

    class FakeCursor:
        def __init__(self) -> None:
            self._last_fetchone = None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            del exc_type, exc, tb

        def execute(self, sql: str, params=None) -> None:
            del params
            compact_sql = " ".join(sql.split())
            executed_sql.append(compact_sql)
            if "SELECT COALESCE(MAX(version), 0) + 1" in compact_sql:
                self._last_fetchone = (1,)
            elif "RETURNING id" in compact_sql:
                self._last_fetchone = ("snapshot-001",)
            elif "INSERT INTO rag.user_profile_vector" in compact_sql:
                raise RuntimeError("vector extension unavailable")
            else:
                self._last_fetchone = None

        def fetchone(self):
            return self._last_fetchone

    class FakeConnection:
        def __init__(self) -> None:
            self.commit_count = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            del exc_type, exc, tb

        def cursor(self) -> FakeCursor:
            return FakeCursor()

        def commit(self) -> None:
            self.commit_count += 1

    connection = FakeConnection()
    store = PostgresProfileStore(connect_fn=lambda **_: connection)

    snapshot = await store.update_profile(
        user_id="00000000-0000-0000-0000-000000000444",
        source_session_id="00000000-0000-0000-0000-000000000555",
        dimensions=LearnerProfileDimensions(
            knowledgeFoundation="BASIC",
            learningGoal="掌握联合索引",
            professionalBackground="计算机本科生",
            learningPreference="step_by_step",
            cognitiveStyle="procedural_oriented",
            weakPoints=["不会判断最左匹配"],
            learningPace="steady",
            confidenceLevel="LOW",
            source="CONVERSATION",
            summaryText="画像更新完成",
        ),
    )

    assert snapshot.version == 1
    assert connection.commit_count == 1
    assert any("SAVEPOINT profile_vector_insert" in sql for sql in executed_sql)
    assert any("ROLLBACK TO SAVEPOINT profile_vector_insert" in sql for sql in executed_sql)
    assert any("RELEASE SAVEPOINT profile_vector_insert" in sql for sql in executed_sql)


def test_postgres_profile_store_resolves_mastered_weak_points_by_canonical_key() -> None:
    executed: list[tuple[str, tuple | None]] = []

    class FakeCursor:
        def __init__(self) -> None:
            self._rows = []

        def execute(self, sql: str, params=None) -> None:
            compact_sql = " ".join(sql.split())
            executed.append((compact_sql, params))
            if "SELECT feature_key, canonical_key, feature_value" in compact_sql:
                self._rows = [("deadlock", "deadlock", {"score": 0.91})]

        def fetchall(self):
            return self._rows

    store = PostgresProfileStore(connect_fn=lambda **_: None)
    store._resolve_mastered_weak_points(
        FakeCursor(),
        user_id="00000000-0000-0000-0000-000000000901",
    )

    update_calls = [item for item in executed if "SET status = 'RESOLVED'" in item[0]]
    assert update_calls
    assert update_calls[0][1][0] == "skill_mastery >= 0.85"
    assert update_calls[0][1][-1] == "deadlock"


def test_profile_feature_registry_drives_store_dimension_policies() -> None:
    store = PostgresProfileStore(connect_fn=lambda **_: None)

    assert store._singleton_dimensions() == exclusive_dimensions()
    assert store._canonical_match_dimensions() == canonical_match_dimensions()
    assert "weak_points" in resolved_by_skill_mastery_dimensions()


def test_postgres_profile_store_extracts_decay_settings_from_registry() -> None:
    store = PostgresProfileStore(connect_fn=lambda **_: None)
    dimensions = LearnerProfileDimensions(
        knowledgeFoundation="BASIC",
        learningGoal="掌握并发编程",
        learningHabits={"noteTaking": True, "selfTesting": True},
        skillMastery={"deadlock": 0.91},
        weakPoints=["deadlock"],
        inferredRecommendations=["隔天复测死锁四条件"],
    )

    features = store._extract_features(dimensions)
    by_dimension = {feature["dimension"]: feature for feature in features}

    for dimension in ("learning_habits", "skill_mastery", "weak_points", "inferred_recommendation"):
        spec = get_feature_dimension_spec(dimension)
        feature = by_dimension[dimension]
        assert feature["stability_period_days"] == spec.stability_period_days
        assert feature["decay_rate"] == spec.decay_rate


def test_postgres_profile_store_regresses_resolved_weak_point_on_reobserve() -> None:
    executed: list[tuple[str, tuple | None]] = []

    class FakeCursor:
        def __init__(self) -> None:
            self._last_fetchone = None

        def execute(self, sql: str, params=None) -> None:
            compact_sql = " ".join(sql.split())
            executed.append((compact_sql, params))
            if "SELECT id, feature_value, confidence" in compact_sql:
                self._last_fetchone = ("feature-1", {}, 0.82, 2, [], ["deadlock"], "RESOLVED")
            else:
                self._last_fetchone = None

        def fetchone(self):
            return self._last_fetchone

    store = PostgresProfileStore(connect_fn=lambda **_: None)
    feature = store._feature_record(
        dimension="weak_points",
        feature_key="deadlock",
        feature_value={"topic": "deadlock", "severity": 0.8},
        confidence=0.8,
        source_type="CONVERSATION",
    )

    store._upsert_features(
        FakeCursor(),
        user_id="00000000-0000-0000-0000-000000000902",
        features=[feature],
        source_session_id=None,
    )

    select_calls = [item for item in executed if "canonical_key = %s" in item[0]]
    update_calls = [item for item in executed if "status = %s" in item[0]]
    assert select_calls
    assert update_calls
    assert "REGRESSED" in update_calls[0][1]
