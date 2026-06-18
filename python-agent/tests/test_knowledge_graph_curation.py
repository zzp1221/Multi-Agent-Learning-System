import pytest

from src.ai_modules.agents.path_planning_agent import PathPlanningAgent
from src.ai_modules.agents.profile_agent import ProfileAgent
from src.ai_modules.memory.knowledge_graph_curation import (
    CandidateKnowledgeEdge,
    CandidateKnowledgeNode,
    KnowledgeGraphCurationService,
)
from src.ai_modules.models import LearningPlanPayload, LearnerProfileDimensions
from src.ai_modules.models.profile import WeakPointDetail


class RecordingGraphStore:
    def __init__(self) -> None:
        self.nodes: list[dict] = []
        self.edges: list[dict] = []

    async def upsert_node(self, **kwargs) -> None:
        self.nodes.append(kwargs)

    async def upsert_edge(self, **kwargs) -> None:
        self.edges.append(kwargs)


def test_curation_rejects_non_knowledge_nodes_and_keeps_real_concepts() -> None:
    service = KnowledgeGraphCurationService(store=RecordingGraphStore())

    result = service.curate(
        candidate_nodes=[
            CandidateKnowledgeNode(topic="当前主题", confidence=0.9),
            CandidateKnowledgeNode(topic="综合练习与复盘", confidence=0.9),
            CandidateKnowledgeNode(topic="学习主动性：并发编程", confidence=0.9),
            CandidateKnowledgeNode(topic="Java线程创建基础概念学习", confidence=0.9),
            CandidateKnowledgeNode(topic="ThreadLocal", confidence=0.82, evidence_text="错题标签 ThreadLocal"),
            CandidateKnowledgeNode(topic="数据库:两阶段锁协议", confidence=0.82, evidence_text="诊断知识点"),
        ],
        candidate_edges=[],
    )

    assert {node.topic for node in result.accepted_nodes} == {"ThreadLocal", "数据库:两阶段锁协议"}
    assert {node.topic for node in result.rejected_nodes} == {
        "当前主题",
        "综合练习与复盘",
        "学习主动性:并发编程",
        "Java线程创建基础概念学习",
    }
    assert all(node.reason for node in result.rejected_nodes)


def test_curation_handles_prerequisite_precision_before_recall() -> None:
    service = KnowledgeGraphCurationService(store=RecordingGraphStore())

    result = service.curate(
        candidate_nodes=[
            CandidateKnowledgeNode(topic="Java并发编程", confidence=0.9),
            CandidateKnowledgeNode(topic="线程池", confidence=0.9),
            CandidateKnowledgeNode(topic="Go语言基础语法", confidence=0.9),
        ],
        candidate_edges=[
            CandidateKnowledgeEdge(
                from_topic="Java并发编程",
                to_topic="线程池",
                relation_type="PREREQUISITE",
                confidence=0.68,
                evidence_text="同一并发主题但缺少明确前置证据",
            ),
            CandidateKnowledgeEdge(
                from_topic="Java并发编程",
                to_topic="Go语言基础语法",
                relation_type="PREREQUISITE",
                confidence=0.91,
                evidence_text="相邻学习步骤",
            ),
            CandidateKnowledgeEdge(
                from_topic="Java并发编程",
                to_topic="线程池",
                relation_type="PREREQUISITE",
                confidence=0.91,
            ),
        ],
    )

    assert len(result.accepted_edges) == 1
    assert result.accepted_edges[0].relation_type == "RELATED"
    assert result.accepted_edges[0].reason == "downgraded_prerequisite_to_related"
    assert {edge.reason for edge in result.rejected_edges} == {
        "step_adjacency_is_not_prerequisite_evidence",
        "prerequisite_requires_evidence",
    }


@pytest.mark.asyncio
async def test_curation_writes_only_accepted_facts() -> None:
    store = RecordingGraphStore()
    service = KnowledgeGraphCurationService(store=store)

    await service.curate_and_write(
        user_id="00000000-0000-0000-0000-000000000001",
        candidate_nodes=[
            CandidateKnowledgeNode(topic="当前主题", confidence=0.9),
            CandidateKnowledgeNode(topic="联合索引", confidence=0.82, mastery=0.35),
        ],
        candidate_edges=[
            CandidateKnowledgeEdge(
                from_topic="当前主题",
                to_topic="联合索引",
                relation_type="RELATED",
                confidence=0.82,
            )
        ],
    )

    assert [node["topic"] for node in store.nodes] == ["联合索引"]
    assert store.edges == []


def test_profile_agent_does_not_fallback_current_topic_into_skill_mastery() -> None:
    agent = ProfileAgent(profile_store=None, knowledge_graph_store=RecordingGraphStore())
    dimensions = LearnerProfileDimensions(
        knowledgeFoundation="BASIC",
        learningGoal="补强当前薄弱点",
        weakPoints=[],
    )

    skills = agent._derive_skill_mastery(
        dimensions=dimensions,
        judge_result={},
        practice_batch={},
        weak_point_details=[],
    )

    assert "当前主题" not in skills
    assert skills == {}


@pytest.mark.asyncio
async def test_path_planning_sync_uses_target_points_without_step_edges() -> None:
    store = RecordingGraphStore()
    curation = KnowledgeGraphCurationService(store=store)
    agent = PathPlanningAgent(
        learning_plan_store=None,
        knowledge_graph_store=store,
        knowledge_graph_curation=curation,
    )
    plan = LearningPlanPayload.model_validate(
        {
            "goal": "掌握并发编程",
            "duration": "7天",
            "milestones": ["阶段一", "阶段二"],
            "steps": [
                {
                    "title": "概念学习:线程创建基础",
                    "objective": "理解 Thread 与 Runnable",
                    "activities": ["阅读材料"],
                    "successCriteria": "能解释线程创建方式",
                    "targetKnowledgePoints": ["Thread", "Runnable"],
                },
                {
                    "title": "综合练习与复盘",
                    "objective": "完成题目",
                    "activities": ["练习"],
                    "successCriteria": "完成练习",
                    "targetKnowledgePoints": ["线程池"],
                },
            ],
            "summaryText": "计划",
        }
    )

    await agent._sync_plan_to_graph(user_id="00000000-0000-0000-0000-000000000001", plan=plan)

    assert [node["topic"] for node in store.nodes] == ["Thread", "Runnable", "线程池"]
    assert store.edges == []
