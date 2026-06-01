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


def _canonicalize(text: str) -> str:
    """规范化知识点 key：去空白、小写、去标点。"""
    normalized = unicodedata.normalize("NFKC", str(text or "").strip())
    normalized = re.sub(r"[\s\-_/\\]+", "_", normalized)
    normalized = re.sub(r"[^\w一-鿿]", "", normalized)
    return normalized.lower()[:64]


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
        try:
            uid = str(uuid.UUID(str(user_id)))  # validate and normalize to string
            with self._get_conn() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app.learner_knowledge_node
                        (user_id, canonical_key, topic, mastery_score, node_status, source)
                    VALUES (%s::uuid, %s, %s, %s, %s, %s)
                    ON CONFLICT (user_id, canonical_key) DO UPDATE SET
                        topic        = EXCLUDED.topic,
                        mastery_score = EXCLUDED.mastery_score,
                        node_status  = EXCLUDED.node_status,
                        source       = EXCLUDED.source,
                        updated_at   = now()
                    """,
                    (uid, key, topic.strip() or key, score, status, source),
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
