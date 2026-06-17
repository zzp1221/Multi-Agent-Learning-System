"""Backfill explainable learner graph relations for one user.

Default mode is dry-run. Use --apply with --user-id to write inferred RELATED and
PART_OF edges. The script does not create fake learning-path order edges.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ai_modules.memory.knowledge_graph_store import LearnerKnowledgeGraphStore  # noqa: E402


@dataclass(frozen=True)
class NodeRow:
    key: str
    topic: str


@dataclass(frozen=True)
class InferredEdge:
    from_key: str
    to_key: str
    relation_type: str
    weight: float


def infer_edges(nodes: list[NodeRow], limit_per_node: int = 2) -> list[InferredEdge]:
    edges: list[InferredEdge] = []
    edges.extend(_infer_part_of_edges(nodes))
    edges.extend(_infer_related_edges(nodes, limit_per_node=limit_per_node))
    seen: set[tuple[str, str, str]] = set()
    unique_edges: list[InferredEdge] = []
    for edge in edges:
        key = (edge.from_key, edge.to_key, edge.relation_type)
        if edge.from_key == edge.to_key or key in seen:
            continue
        seen.add(key)
        unique_edges.append(edge)
    return unique_edges


def _infer_part_of_edges(nodes: list[NodeRow]) -> list[InferredEdge]:
    key_by_topic = {_normalize_topic(node.topic): node.key for node in nodes}
    edges: list[InferredEdge] = []
    for node in nodes:
        parent_topic = _parent_topic(node.topic)
        if not parent_topic:
            continue
        parent_key = key_by_topic.get(_normalize_topic(parent_topic))
        if parent_key:
            edges.append(InferredEdge(parent_key, node.key, "PART_OF", 0.82))
    return edges


def _infer_related_edges(nodes: list[NodeRow], *, limit_per_node: int) -> list[InferredEdge]:
    edges: list[InferredEdge] = []
    for left_index, left in enumerate(nodes):
        candidates: list[tuple[float, NodeRow]] = []
        left_tokens = _topic_tokens(left.topic)
        if not left_tokens:
            continue
        for right in nodes[left_index + 1:]:
            right_tokens = _topic_tokens(right.topic)
            if not right_tokens:
                continue
            overlap = left_tokens & right_tokens
            if not overlap:
                continue
            score = len(overlap) / max(len(left_tokens | right_tokens), 1)
            if score >= 0.25:
                candidates.append((score, right))
        candidates.sort(key=lambda item: item[0], reverse=True)
        for score, right in candidates[:limit_per_node]:
            edges.append(InferredEdge(left.key, right.key, "RELATED", max(0.35, round(score, 2))))
    return edges


def _parent_topic(topic: str) -> str:
    normalized = topic.strip()
    for delimiter in ("：", ":", "/", "／", "-", "｜", "|"):
        if delimiter in normalized:
            parent = normalized.split(delimiter, 1)[0].strip()
            if parent and parent != normalized:
                return parent
    return ""


def _topic_tokens(topic: str) -> set[str]:
    normalized = re.sub(r"[^\w\u4e00-\u9fff]+", " ", topic.lower())
    tokens = {token for token in normalized.split() if len(token) >= 2}
    for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", topic):
        tokens.add(chunk)
        max_size = min(4, len(chunk))
        for size in range(2, max_size + 1):
            tokens.update(chunk[index:index + size] for index in range(0, len(chunk) - size + 1))
    return tokens


def _normalize_topic(topic: str) -> str:
    return re.sub(r"\s+", "", topic.strip().lower())


def load_nodes(store: LearnerKnowledgeGraphStore, user_id: str, limit: int) -> list[NodeRow]:
    with store._get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT canonical_key, topic
            FROM app.learner_knowledge_node
            WHERE user_id = %s::uuid
            ORDER BY updated_at DESC
            LIMIT %s
            """,
            (user_id, limit),
        )
        return [NodeRow(str(row[0]), str(row[1])) for row in cur.fetchall()]


def apply_edges(store: LearnerKnowledgeGraphStore, user_id: str, edges: list[InferredEdge]) -> int:
    written = 0
    with store._get_conn() as conn, conn.cursor() as cur:
        for edge in edges:
            cur.execute(
                """
                INSERT INTO app.learner_knowledge_edge
                    (user_id, from_key, to_key, relation_type, weight)
                VALUES (%s::uuid, %s, %s, %s, %s)
                ON CONFLICT (user_id, from_key, to_key, relation_type) DO UPDATE SET
                    weight = GREATEST(app.learner_knowledge_edge.weight, EXCLUDED.weight)
                """,
                (user_id, edge.from_key, edge.to_key, edge.relation_type, edge.weight),
            )
            written += 1
        conn.commit()
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill learner knowledge graph RELATED/PART_OF edges.")
    parser.add_argument("--user-id", required=True, help="Target user UUID.")
    parser.add_argument("--limit", type=int, default=120, help="Max nodes to inspect.")
    parser.add_argument("--related-limit-per-node", type=int, default=2, help="Max RELATED edges inferred per node.")
    parser.add_argument("--apply", action="store_true", help="Write inferred edges. Omit for dry-run.")
    args = parser.parse_args()

    store = LearnerKnowledgeGraphStore()
    nodes = load_nodes(store, args.user_id, args.limit)
    edges = infer_edges(nodes, limit_per_node=args.related_limit_per_node)
    print(f"nodes={len(nodes)} inferred_edges={len(edges)} apply={args.apply}")
    for edge in edges[:20]:
        print(f"{edge.relation_type} {edge.from_key} -> {edge.to_key} weight={edge.weight}")
    if not args.apply:
        print("dry-run only; pass --apply to write")
        return 0
    written = apply_edges(store, args.user_id, edges)
    print(f"written={written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
