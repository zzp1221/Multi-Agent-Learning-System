"""Deterministic curation gate for learner knowledge graph writes."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
import json
import logging
import re
from typing import Literal

from src.ai_modules.memory.knowledge_graph_store import (
    LearnerKnowledgeGraphStore,
    _canonicalize,
    _normalize_topic_label,
    is_valid_knowledge_topic,
    reject_reason_for_topic,
)

LOGGER = logging.getLogger(__name__)

NodeKind = Literal["CONCEPT", "DOMAIN", "TASK_STEP", "BEHAVIOR_SIGNAL", "PLACEHOLDER"]
RelationType = Literal["PREREQUISITE", "RELATED", "PART_OF"]
CurationStatus = Literal["accepted", "rejected", "deferred"]


@dataclass(slots=True)
class CandidateKnowledgeNode:
    topic: str
    canonical_key_hint: str = ""
    node_kind: NodeKind = "CONCEPT"
    mastery: float = 0.0
    source_agent: str = ""
    source_event: str = ""
    source_task_id: str | None = None
    evidence_text: str = ""
    confidence: float = 0.75


@dataclass(slots=True)
class CandidateKnowledgeEdge:
    from_topic: str
    to_topic: str
    relation_type: RelationType = "RELATED"
    source_agent: str = ""
    evidence_text: str = ""
    confidence: float = 0.0
    rule_id: str = ""


@dataclass(slots=True)
class CuratedNodeFact:
    topic: str
    canonical_key: str
    mastery: float
    source: str
    confidence: float
    source_agent: str
    evidence_text: str
    status: CurationStatus = "accepted"
    reason: str = ""


@dataclass(slots=True)
class CuratedEdgeFact:
    from_topic: str
    to_topic: str
    from_key: str
    to_key: str
    relation_type: RelationType
    weight: float
    confidence: float
    source_agent: str
    evidence_text: str
    status: CurationStatus = "accepted"
    reason: str = ""


@dataclass(slots=True)
class KnowledgeGraphCurationResult:
    accepted_nodes: list[CuratedNodeFact] = field(default_factory=list)
    rejected_nodes: list[CuratedNodeFact] = field(default_factory=list)
    deferred_nodes: list[CuratedNodeFact] = field(default_factory=list)
    accepted_edges: list[CuratedEdgeFact] = field(default_factory=list)
    rejected_edges: list[CuratedEdgeFact] = field(default_factory=list)
    deferred_edges: list[CuratedEdgeFact] = field(default_factory=list)

    def to_report(self) -> dict[str, object]:
        return {
            "acceptedNodes": [asdict(item) for item in self.accepted_nodes],
            "rejectedNodes": [asdict(item) for item in self.rejected_nodes],
            "deferredNodes": [asdict(item) for item in self.deferred_nodes],
            "acceptedEdges": [asdict(item) for item in self.accepted_edges],
            "rejectedEdges": [asdict(item) for item in self.rejected_edges],
            "deferredEdges": [asdict(item) for item in self.deferred_edges],
            "summary": {
                "acceptedNodeCount": len(self.accepted_nodes),
                "rejectedNodeCount": len(self.rejected_nodes),
                "deferredNodeCount": len(self.deferred_nodes),
                "acceptedEdgeCount": len(self.accepted_edges),
                "rejectedEdgeCount": len(self.rejected_edges),
                "deferredEdgeCount": len(self.deferred_edges),
            },
        }


class KnowledgeGraphCurationService:
    """Curates candidate graph facts before writing formal learner graph tables."""

    def __init__(
        self,
        store: LearnerKnowledgeGraphStore | None = None,
        *,
        report_dir: str | Path | None = None,
    ) -> None:
        self.store = store or LearnerKnowledgeGraphStore()
        self.report_dir = Path(report_dir) if report_dir else None

    async def curate_and_write(
        self,
        *,
        user_id: str,
        candidate_nodes: list[CandidateKnowledgeNode],
        candidate_edges: list[CandidateKnowledgeEdge] | None = None,
        dry_run: bool = False,
        report_name: str | None = None,
    ) -> KnowledgeGraphCurationResult:
        result = self.curate(candidate_nodes=candidate_nodes, candidate_edges=candidate_edges or [])
        if not dry_run:
            await self._write_accepted_facts(user_id=user_id, result=result)
        if self.report_dir and (dry_run or report_name):
            self._write_report(user_id=user_id, result=result, report_name=report_name)
        return result

    def curate(
        self,
        *,
        candidate_nodes: list[CandidateKnowledgeNode],
        candidate_edges: list[CandidateKnowledgeEdge],
    ) -> KnowledgeGraphCurationResult:
        result = KnowledgeGraphCurationResult()
        accepted_by_key: dict[str, CuratedNodeFact] = {}

        for candidate in candidate_nodes:
            fact = self._curate_node(candidate)
            if fact.status == "accepted":
                existing = accepted_by_key.get(fact.canonical_key)
                if existing is None or self._should_replace_node(fact, existing):
                    accepted_by_key[fact.canonical_key] = fact
                continue
            if fact.status == "deferred":
                result.deferred_nodes.append(fact)
            else:
                result.rejected_nodes.append(fact)

        result.accepted_nodes = list(accepted_by_key.values())
        node_key_by_topic = {
            self._topic_lookup_key(fact.topic): fact.canonical_key
            for fact in result.accepted_nodes
        }

        for candidate in candidate_edges:
            fact = self._curate_edge(candidate, node_key_by_topic=node_key_by_topic)
            if fact.status == "accepted":
                result.accepted_edges.append(fact)
            elif fact.status == "deferred":
                result.deferred_edges.append(fact)
            else:
                result.rejected_edges.append(fact)

        return result

    def _curate_node(self, candidate: CandidateKnowledgeNode) -> CuratedNodeFact:
        normalized_topic = _normalize_topic_label(candidate.topic)
        canonical_key = _canonicalize(candidate.canonical_key_hint or normalized_topic)
        base = CuratedNodeFact(
            topic=normalized_topic or str(candidate.topic or "").strip(),
            canonical_key=canonical_key,
            mastery=max(0.0, min(1.0, float(candidate.mastery or 0.0))),
            source=self._source_from_agent(candidate.source_agent, candidate.source_event),
            confidence=max(0.0, min(1.0, float(candidate.confidence or 0.0))),
            source_agent=candidate.source_agent,
            evidence_text=str(candidate.evidence_text or "").strip()[:500],
        )
        if not normalized_topic or not canonical_key:
            base.status = "rejected"
            base.reason = "empty_topic"
            return base
        if candidate.node_kind in {"TASK_STEP", "BEHAVIOR_SIGNAL", "PLACEHOLDER"}:
            base.status = "rejected"
            base.reason = f"node_kind_{candidate.node_kind.lower()}"
            return base
        topic_reason = reject_reason_for_topic(normalized_topic)
        if topic_reason:
            base.status = "rejected"
            base.reason = topic_reason
            return base
        if base.confidence < 0.6:
            base.status = "deferred"
            base.reason = "confidence_below_accept_threshold"
            return base
        return base

    def _curate_edge(
        self,
        candidate: CandidateKnowledgeEdge,
        *,
        node_key_by_topic: dict[str, str],
    ) -> CuratedEdgeFact:
        from_topic = _normalize_topic_label(candidate.from_topic)
        to_topic = _normalize_topic_label(candidate.to_topic)
        from_key = node_key_by_topic.get(self._topic_lookup_key(from_topic), _canonicalize(from_topic))
        to_key = node_key_by_topic.get(self._topic_lookup_key(to_topic), _canonicalize(to_topic))
        confidence = max(0.0, min(1.0, float(candidate.confidence or 0.0)))
        relation_type = self._normalize_relation_type(candidate.relation_type)
        fact = CuratedEdgeFact(
            from_topic=from_topic,
            to_topic=to_topic,
            from_key=from_key,
            to_key=to_key,
            relation_type=relation_type,
            weight=max(0.1, confidence),
            confidence=confidence,
            source_agent=candidate.source_agent,
            evidence_text=str(candidate.evidence_text or "").strip()[:500],
        )
        if not from_topic or not to_topic or not from_key or not to_key:
            fact.status = "rejected"
            fact.reason = "empty_edge_endpoint"
            return fact
        if from_key == to_key:
            fact.status = "rejected"
            fact.reason = "self_edge"
            return fact
        for endpoint in (from_topic, to_topic):
            topic_reason = reject_reason_for_topic(endpoint)
            if topic_reason:
                fact.status = "rejected"
                fact.reason = f"invalid_endpoint_{topic_reason}"
                return fact
        if relation_type == "PREREQUISITE":
            if not fact.evidence_text:
                fact.status = "rejected"
                fact.reason = "prerequisite_requires_evidence"
                return fact
            if self._looks_like_step_adjacency_evidence(fact.evidence_text):
                fact.status = "rejected"
                fact.reason = "step_adjacency_is_not_prerequisite_evidence"
                return fact
            if confidence < 0.75:
                if self._same_domain(from_topic, to_topic) and confidence >= 0.6:
                    fact.relation_type = "RELATED"
                    fact.weight = max(0.1, min(0.7, confidence))
                    fact.status = "accepted"
                    fact.reason = "downgraded_prerequisite_to_related"
                    return fact
                fact.status = "rejected"
                fact.reason = "prerequisite_confidence_below_threshold"
                return fact
            if self._looks_like_cross_domain_step_edge(from_topic, to_topic):
                fact.status = "rejected"
                fact.reason = "cross_domain_step_order_is_not_prerequisite"
                return fact
        elif confidence < 0.6:
            fact.status = "deferred"
            fact.reason = "confidence_below_accept_threshold"
            return fact
        if relation_type == "PART_OF" and not self._has_parent_child_shape(from_topic, to_topic):
            fact.status = "rejected"
            fact.reason = "part_of_requires_hierarchy_evidence"
            return fact
        return fact

    async def _write_accepted_facts(self, *, user_id: str, result: KnowledgeGraphCurationResult) -> None:
        for node in result.accepted_nodes:
            await self.store.upsert_node(
                user_id=user_id,
                canonical_key=node.canonical_key,
                topic=node.topic,
                mastery_score=node.mastery,
                source=node.source,
            )
        for edge in result.accepted_edges:
            await self.store.upsert_edge(
                user_id=user_id,
                from_key=edge.from_key,
                to_key=edge.to_key,
                relation_type=edge.relation_type,
                weight=edge.weight,
            )

    def _write_report(
        self,
        *,
        user_id: str,
        result: KnowledgeGraphCurationResult,
        report_name: str | None,
    ) -> None:
        if self.report_dir is None:
            return
        self.report_dir.mkdir(parents=True, exist_ok=True)
        safe_user_id = re.sub(r"[^a-zA-Z0-9_-]+", "_", user_id)
        filename = report_name or f"knowledge_graph_curation_dry_run_{safe_user_id}.json"
        path = self.report_dir / filename
        path.write_text(json.dumps(result.to_report(), ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _normalize_relation_type(value: str) -> RelationType:
        normalized = str(value or "").strip().upper()
        if normalized in {"PREREQUISITE", "RELATED", "PART_OF"}:
            return normalized  # type: ignore[return-value]
        return "RELATED"

    @staticmethod
    def _source_from_agent(source_agent: str, source_event: str) -> str:
        joined = f"{source_agent} {source_event}".upper()
        if "PRACTICE" in joined or "JUDGE" in joined:
            return "PRACTICE"
        if "EVALUATION" in joined:
            return "EVALUATION"
        if "MANUAL" in joined:
            return "MANUAL"
        return "PROFILE"

    @staticmethod
    def _should_replace_node(candidate: CuratedNodeFact, existing: CuratedNodeFact) -> bool:
        if candidate.confidence != existing.confidence:
            return candidate.confidence > existing.confidence
        if candidate.mastery != existing.mastery:
            return candidate.mastery > existing.mastery
        return len(candidate.topic) < len(existing.topic)

    @staticmethod
    def _topic_lookup_key(topic: str) -> str:
        return _canonicalize(topic)

    @staticmethod
    def _same_domain(left: str, right: str) -> bool:
        left_domain = re.split(r"[:：/\\|\-—–]", left, maxsplit=1)[0]
        right_domain = re.split(r"[:：/\\|\-—–]", right, maxsplit=1)[0]
        if left_domain and right_domain and left_domain == right_domain:
            return True
        concurrency_terms = ("线程", "线程池", "锁", "Thread", "Runnable", "volatile", "synchronized")
        if "并发" in left and any(term in right for term in concurrency_terms):
            return True
        if "并发" in right and any(term in left for term in concurrency_terms):
            return True
        common_terms = ("Java", "Go", "Python", "数据库", "并发", "操作系统", "网络", "算法", "Rust", "Spring")
        return any(term in left and term in right for term in common_terms)

    @staticmethod
    def _has_parent_child_shape(from_topic: str, to_topic: str) -> bool:
        return (
            from_topic.startswith(f"{to_topic}:")
            or from_topic.startswith(f"{to_topic}：")
            or to_topic.startswith(f"{from_topic}:")
            or to_topic.startswith(f"{from_topic}：")
        )

    @staticmethod
    def _looks_like_cross_domain_step_edge(from_topic: str, to_topic: str) -> bool:
        if KnowledgeGraphCurationService._same_domain(from_topic, to_topic):
            return False
        return not any(token in f"{from_topic} {to_topic}" for token in ("->", "前置", "依赖", "基础"))

    @staticmethod
    def _looks_like_step_adjacency_evidence(evidence_text: str) -> bool:
        evidence = evidence_text.lower()
        if any(token in evidence for token in ("相邻", "adjacent", "step order", "步骤顺序")):
            return "前置" not in evidence and "依赖" not in evidence
        return False


def is_curatable_knowledge_topic(topic: str) -> bool:
    return is_valid_knowledge_topic(topic)
