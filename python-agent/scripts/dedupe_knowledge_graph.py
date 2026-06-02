"""Deduplicate learner knowledge graph nodes for one user."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ai_modules.memory.knowledge_graph_store import LearnerKnowledgeGraphStore, _canonicalize


def main() -> int:
    parser = argparse.ArgumentParser(description="Deduplicate learner knowledge graph nodes.")
    parser.add_argument("--user-id", default="", help="Target app.users.id UUID.")
    parser.add_argument("--login-id", default="", help="Target app.users.login_id.")
    parser.add_argument("--apply", action="store_true", help="Apply changes. Default is dry-run.")
    args = parser.parse_args()

    store = LearnerKnowledgeGraphStore()
    user_id = args.user_id.strip() or _resolve_user_id(store, args.login_id.strip())
    if not user_id:
        parser.error("Provide --user-id or --login-id.")

    if not args.apply:
        preview = _preview(store, user_id)
        print(json.dumps({"mode": "dry-run", **preview}, ensure_ascii=False, indent=2))
        return 0

    result = store._deduplicate_user_graph_sync(user_id)
    print(json.dumps({"mode": "apply", "userId": user_id, **result}, ensure_ascii=False, indent=2))
    return 0


def _resolve_user_id(store: LearnerKnowledgeGraphStore, login_id: str) -> str:
    if not login_id:
        return ""
    with store._get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id::text
            FROM app.users
            WHERE login_id = %s OR full_name = %s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (login_id, login_id),
        )
        rows = cur.fetchall()
    return str(rows[0][0]) if rows else ""


def _preview(store: LearnerKnowledgeGraphStore, user_id: str) -> dict:
    nodes = _read_nodes(store, user_id)
    groups: dict[str, list[dict]] = {}
    for node in nodes:
        new_key = _canonicalize(node["key"]) or _canonicalize(node["topic"])
        groups.setdefault(new_key, []).append(node)
    duplicates = {
        key: [
            {
                "key": node["key"],
                "topic": node["topic"],
                "mastery": node["mastery"],
                "status": node["status"],
            }
            for node in nodes
        ]
        for key, nodes in groups.items()
        if key and len(nodes) > 1
    }
    return {
        "userId": user_id,
        "duplicateGroups": duplicates,
        "duplicateGroupCount": len(duplicates),
    }


def _read_nodes(store: LearnerKnowledgeGraphStore, user_id: str) -> list[dict]:
    with store._get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT canonical_key, topic, mastery_score, node_status
            FROM app.learner_knowledge_node
            WHERE user_id = %s::uuid
            ORDER BY updated_at DESC
            """,
            (user_id,),
        )
        return [
            {
                "key": row[0],
                "topic": row[1],
                "mastery": round(float(row[2]), 3),
                "status": row[3],
            }
            for row in cur.fetchall()
        ]


if __name__ == "__main__":
    raise SystemExit(main())
