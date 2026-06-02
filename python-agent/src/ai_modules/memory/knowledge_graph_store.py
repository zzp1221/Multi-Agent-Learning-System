"""用户学习路径图存储：节点（知识点）+ 边（依赖关系）。"""

from __future__ import annotations

import logging
import re
import unicodedata
import uuid
from typing import Any

LOGGER = logging.getLogger(__name__)

_MASTERY_THRESHOLDS = {
    "MASTERED": 0.85,
    "IN_PROGRESS": 0.4,
    "WEAK": 0.0,
}

_TOPIC_STAGE_PATTERNS = (
    r"(?:基础)?入门(?:指南|概览|导学)?$",
    r"基础(?:巩固|回顾|复习|训练|练习)$",
    r"(?:流程|知识|内容)?概览$",
    r"(?:综合)?(?:练习|训练|实践|实战)$",
    r"(?:进阶|高级)(?:应用|训练|实践)?$",
    r"(?:核心)?(?:概念|原理)(?:梳理|回顾)$",
    r"(?:学习)?(?:路径|计划)$",
)

def _canonicalize(text: str) -> str:
    """规范化知识点 key：去空白、小写、去标点。"""
    normalized = _normalize_topic_label(text)
    normalized = re.sub(r"[\s\-_/\\]+", "_", normalized)
    normalized = re.sub(r"[^\w一-鿿]", "", normalized)
    return normalized.lower()[:64]


def _normalize_topic_label(text: str) -> str:
    label = unicodedata.normalize("NFKC", str(text or "").strip())
    label = re.sub(r"\s+", "", label)
    for pattern in _TOPIC_STAGE_PATTERNS:
        label = re.sub(pattern, "", label, flags=re.IGNORECASE)
    return label or unicodedata.normalize("NFKC", str(text or "").strip())


def _status_from_mastery(mastery: float) -> str:
    if mastery >= _MASTERY_THRESHOLDS["MASTERED"]:
        return "MASTERED"
    if mastery >= _MASTERY_THRESHOLDS["IN_PROGRESS"]:
        return "IN_PROGRESS"
    if mastery > 0:
        return "WEAK"
    return "NOT_STARTED"


class LearnerKnowledgeGraphStore:
    """读写 app.learner_knowledge_node / app.learner_knowledge_edge。"""

    def __init__(self, db_config: dict | None = None) -> None:
        self._db_config = db_config

    def _get_conn(self):
        import psycopg2
        if self._db_config:
            return psycopg2.connect(**self._db_config)
        from src.ai_modules.config import get_settings
        s = get_settings()
        return psycopg2.connect(
            host=s.postgres_host,
            port=s.postgres_port,
            dbname=s.postgres_db,
            user=s.postgres_user,
            password=s.postgres_password,
        )

    async def upsert_node(
        self,
        user_id: str,
        canonical_key: str,
        topic: str,
        mastery_score: float,
        source: str = "PROFILE",
    ) -> None:
        import asyncio
        await asyncio.to_thread(
            self._upsert_node_sync, user_id, canonical_key, topic, mastery_score, source
        )

    def _upsert_node_sync(
        self,
        user_id: str,
        canonical_key: str,
        topic: str,
        mastery_score: float,
        source: str,
    ) -> None:
        key = _canonicalize(canonical_key) or _canonicalize(topic)
        if not key:
            return
        score = max(0.0, min(1.0, float(mastery_score)))
        status = _status_from_mastery(score)
        display_topic = _normalize_topic_label(topic) or key
        try:
            uid = str(uuid.UUID(str(user_id)))  # validate and normalize to string
            with self._get_conn() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app.learner_knowledge_node
                        (user_id, canonical_key, topic, mastery_score, node_status, source)
                    VALUES (%s::uuid, %s, %s, %s, %s, %s)
                    ON CONFLICT (user_id, canonical_key) DO UPDATE SET
                        topic = CASE
                            WHEN char_length(EXCLUDED.topic) < char_length(app.learner_knowledge_node.topic)
                                THEN EXCLUDED.topic
                            ELSE app.learner_knowledge_node.topic
                        END,
                        mastery_score = GREATEST(app.learner_knowledge_node.mastery_score, EXCLUDED.mastery_score),
                        node_status = CASE
                            WHEN GREATEST(app.learner_knowledge_node.mastery_score, EXCLUDED.mastery_score) >= 0.85 THEN 'MASTERED'
                            WHEN GREATEST(app.learner_knowledge_node.mastery_score, EXCLUDED.mastery_score) >= 0.4 THEN 'IN_PROGRESS'
                            WHEN GREATEST(app.learner_knowledge_node.mastery_score, EXCLUDED.mastery_score) > 0 THEN 'WEAK'
                            ELSE 'NOT_STARTED'
                        END,
                        source = CASE
                            WHEN CASE EXCLUDED.source
                                WHEN 'MANUAL' THEN 3
                                WHEN 'PRACTICE' THEN 2
                                WHEN 'EVALUATION' THEN 1
                                ELSE 0
                            END >= CASE app.learner_knowledge_node.source
                                WHEN 'MANUAL' THEN 3
                                WHEN 'PRACTICE' THEN 2
                                WHEN 'EVALUATION' THEN 1
                                ELSE 0
                            END THEN EXCLUDED.source
                            ELSE app.learner_knowledge_node.source
                        END,
                        updated_at   = now()
                    """,
                    (
                        uid,
                        key,
                        display_topic,
                        score,
                        status,
                        source,
                    ),
                )
                conn.commit()
        except Exception as exc:
            LOGGER.warning("upsert_node failed user=%s key=%s: %s", user_id, key, exc)

    async def upsert_edge(
        self,
        user_id: str,
        from_key: str,
        to_key: str,
        relation_type: str = "PREREQUISITE",
        weight: float = 1.0,
    ) -> None:
        import asyncio
        await asyncio.to_thread(
            self._upsert_edge_sync, user_id, from_key, to_key, relation_type, weight
        )

    def _upsert_edge_sync(
        self,
        user_id: str,
        from_key: str,
        to_key: str,
        relation_type: str,
        weight: float,
    ) -> None:
        fk = _canonicalize(from_key)
        tk = _canonicalize(to_key)
        if not fk or not tk or fk == tk:
            return
        try:
            uid = str(uuid.UUID(str(user_id)))
            with self._get_conn() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app.learner_knowledge_edge
                        (user_id, from_key, to_key, relation_type, weight)
                    VALUES (%s::uuid, %s, %s, %s, %s)
                    ON CONFLICT (user_id, from_key, to_key, relation_type) DO UPDATE SET
                        weight = EXCLUDED.weight
                    """,
                    (uid, fk, tk, relation_type, max(0.1, float(weight))),
                )
                conn.commit()
        except Exception as exc:
            LOGGER.warning("upsert_edge failed user=%s %s->%s: %s", user_id, fk, tk, exc)

    async def get_graph(self, user_id: str) -> dict[str, Any]:
        import asyncio
        return await asyncio.to_thread(self._get_graph_sync, user_id)

    async def deduplicate_user_graph(self, user_id: str) -> dict[str, int]:
        import asyncio
        return await asyncio.to_thread(self._deduplicate_user_graph_sync, user_id)

    def _deduplicate_user_graph_sync(self, user_id: str) -> dict[str, int]:
        uid = str(uuid.UUID(str(user_id)))
        with self._get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT canonical_key, topic, mastery_score, source
                FROM app.learner_knowledge_node
                WHERE user_id = %s::uuid
                ORDER BY updated_at ASC
                """,
                (uid,),
            )
            rows = cur.fetchall()
            key_map = {
                row[0]: _canonicalize(row[0]) or _canonicalize(row[1])
                for row in rows
            }
            changed_keys = {old: new for old, new in key_map.items() if old != new and new}
            has_duplicate_groups = len(set(key_map.values())) < len([key for key in key_map.values() if key])
            if not changed_keys and not has_duplicate_groups:
                return {"nodesMerged": 0, "edgesRewritten": 0, "edgesDeleted": 0}

            merge_groups: dict[str, list[Any]] = {}
            for row in rows:
                new_key = key_map.get(row[0])
                if new_key:
                    merge_groups.setdefault(new_key, []).append(row)

            nodes_merged = 0
            for new_key, group in merge_groups.items():
                if len(group) == 1 and group[0][0] == new_key:
                    continue
                best_topic = min(
                    (_normalize_topic_label(row[1]) or str(row[1] or row[0]) for row in group),
                    key=len,
                )
                best_score = max(float(row[2]) for row in group)
                best_source = self._best_source([str(row[3]) for row in group])
                cur.execute(
                    """
                    INSERT INTO app.learner_knowledge_node
                        (user_id, canonical_key, topic, mastery_score, node_status, source)
                    VALUES (%s::uuid, %s, %s, %s, %s, %s)
                    ON CONFLICT (user_id, canonical_key) DO UPDATE SET
                        topic = EXCLUDED.topic,
                        mastery_score = GREATEST(app.learner_knowledge_node.mastery_score, EXCLUDED.mastery_score),
                        node_status = EXCLUDED.node_status,
                        source = EXCLUDED.source,
                        updated_at = now()
                    """,
                    (uid, new_key, best_topic, best_score, _status_from_mastery(best_score), best_source),
                )
                old_keys = [row[0] for row in group if row[0] != new_key]
                if old_keys:
                    cur.execute(
                        """
                        DELETE FROM app.learner_knowledge_node
                        WHERE user_id = %s::uuid
                          AND canonical_key = ANY(%s)
                        """,
                        (uid, old_keys),
                    )
                    nodes_merged += cur.rowcount

            cur.execute(
                """
                SELECT from_key, to_key, relation_type, weight
                FROM app.learner_knowledge_edge
                WHERE user_id = %s::uuid
                """,
                (uid,),
            )
            edge_rows = cur.fetchall()
            edges_deleted = 0
            edges_rewritten = 0
            for from_key, to_key, relation_type, weight in edge_rows:
                new_from = key_map.get(from_key, _canonicalize(from_key))
                new_to = key_map.get(to_key, _canonicalize(to_key))
                if not new_from or not new_to or new_from == new_to:
                    cur.execute(
                        """
                        DELETE FROM app.learner_knowledge_edge
                        WHERE user_id = %s::uuid
                          AND from_key = %s
                          AND to_key = %s
                          AND relation_type = %s
                        """,
                        (uid, from_key, to_key, relation_type),
                    )
                    edges_deleted += cur.rowcount
                    continue
                if new_from == from_key and new_to == to_key:
                    continue
                cur.execute(
                    """
                    INSERT INTO app.learner_knowledge_edge
                        (user_id, from_key, to_key, relation_type, weight)
                    VALUES (%s::uuid, %s, %s, %s, %s)
                    ON CONFLICT (user_id, from_key, to_key, relation_type) DO UPDATE SET
                        weight = GREATEST(app.learner_knowledge_edge.weight, EXCLUDED.weight)
                    """,
                    (uid, new_from, new_to, relation_type, weight),
                )
                cur.execute(
                    """
                    DELETE FROM app.learner_knowledge_edge
                    WHERE user_id = %s::uuid
                      AND from_key = %s
                      AND to_key = %s
                      AND relation_type = %s
                    """,
                    (uid, from_key, to_key, relation_type),
                )
                edges_rewritten += 1

            conn.commit()
            return {
                "nodesMerged": nodes_merged,
                "edgesRewritten": edges_rewritten,
                "edgesDeleted": edges_deleted,
            }

    def _best_source(self, sources: list[str]) -> str:
        priority = {"PROFILE": 0, "EVALUATION": 1, "PRACTICE": 2, "MANUAL": 3}
        return max((source for source in sources if source), key=lambda source: priority.get(source, 0), default="PROFILE")

    def _get_graph_sync(self, user_id: str) -> dict[str, Any]:
        try:
            uid = str(uuid.UUID(str(user_id)))
            with self._get_conn() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT canonical_key, topic, mastery_score, node_status, source
                    FROM app.learner_knowledge_node
                    WHERE user_id = %s::uuid
                    ORDER BY updated_at DESC
                    LIMIT 60
                    """,
                    (uid,),
                )
                nodes = [
                    {
                        "key": row[0],
                        "topic": row[1],
                        "mastery": round(float(row[2]), 3),
                        "status": row[3],
                        "source": row[4],
                    }
                    for row in cur.fetchall()
                ]
                node_keys = {n["key"] for n in nodes}
                edges: list[dict] = []
                if node_keys:
                    cur.execute(
                        """
                        SELECT from_key, to_key, relation_type, weight
                        FROM app.learner_knowledge_edge
                        WHERE user_id = %s::uuid
                          AND from_key = ANY(%s)
                          AND to_key   = ANY(%s)
                        """,
                        (uid, list(node_keys), list(node_keys)),
                    )
                    edges = [
                        {
                            "from": row[0],
                            "to": row[1],
                            "type": row[2],
                            "weight": round(float(row[3]), 2),
                        }
                        for row in cur.fetchall()
                    ]
                next_recommended = self._compute_next_recommended(nodes, edges)
                return {"nodes": nodes, "edges": edges, "nextRecommended": next_recommended}
        except Exception as exc:
            LOGGER.warning("get_graph failed user=%s: %s", user_id, exc)
            return {"nodes": [], "edges": [], "nextRecommended": []}

    def _compute_next_recommended(
        self,
        nodes: list[dict],
        edges: list[dict],
    ) -> list[str]:
        """找出所有前置已掌握、自身未掌握的节点 key。"""
        mastered = {n["key"] for n in nodes if n["status"] == "MASTERED"}
        not_mastered = {n["key"] for n in nodes if n["status"] != "MASTERED"}
        # 有入边的节点 -> 其前置节点集合
        prerequisites: dict[str, set[str]] = {}
        for edge in edges:
            if edge["type"] == "PREREQUISITE":
                prerequisites.setdefault(edge["to"], set()).add(edge["from"])
        recommended: list[str] = []
        for key in not_mastered:
            prereqs = prerequisites.get(key, set())
            if prereqs and prereqs.issubset(mastered):
                recommended.append(key)
            elif not prereqs:
                # 无前置依赖且未掌握，也推荐
                recommended.append(key)
        # 优先推荐 WEAK > IN_PROGRESS > NOT_STARTED
        status_order = {"WEAK": 0, "IN_PROGRESS": 1, "NOT_STARTED": 2}
        node_status_map = {n["key"]: n["status"] for n in nodes}
        recommended.sort(key=lambda k: status_order.get(node_status_map.get(k, "NOT_STARTED"), 3))
        return recommended[:5]
