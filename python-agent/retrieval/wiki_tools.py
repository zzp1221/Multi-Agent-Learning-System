"""Read-only wiki graph tools for agent-controlled multi-hop evidence lookup."""

from __future__ import annotations

import time
from typing import Any

import psycopg2


GRAPH_TOOL_INTENTS = {"PREREQUISITE_PATH", "MULTI_HOP_RELATION", "COMPARISON"}


class WikiToolset:
    """Small read-only facade over rag.wiki_page, rag.wiki_link, and chunks."""

    def __init__(self, db_config: dict[str, Any] | None = None) -> None:
        self.db_config = db_config or {}

    def wiki_search(self, query: str, *, limit: int = 5) -> dict[str, Any]:
        return self._with_connection(lambda cur: self.search(cur, query, limit=limit))

    def wiki_read(self, slug: str, *, chunk_limit: int = 3, neighbor_limit: int = 8) -> dict[str, Any]:
        return self._with_connection(
            lambda cur: self.read(cur, slug, chunk_limit=chunk_limit, neighbor_limit=neighbor_limit)
        )

    def wiki_neighbors(
        self,
        slug: str,
        *,
        relation_type: str | None = None,
        limit: int = 12,
    ) -> dict[str, Any]:
        return self._with_connection(
            lambda cur: self.neighbors(cur, slug, relation_type=relation_type, limit=limit)
        )

    def search(self, cur, query: str, *, limit: int = 5) -> dict[str, Any]:
        """Search title/aliases/tags/summary first; no embedding API call."""
        started = time.perf_counter()
        cleaned = str(query or "").strip()
        if not cleaned:
            return {"query": cleaned, "results": [], "diagnostics": {"wiki_search_ms": 0.0}}
        pattern = f"%{cleaned}%"
        cur.execute(
            """
            SELECT slug, title, summary_text,
                   COALESCE(aliases, '[]'::jsonb)::text AS aliases_text,
                   COALESCE(tags, '[]'::jsonb)::text AS tags_text,
                   CASE
                     WHEN lower(title) = lower(%s) THEN 100
                     WHEN lower(slug) = lower(%s) THEN 95
                     WHEN lower(title) LIKE lower(%s) THEN 80
                     WHEN lower(COALESCE(aliases, '[]'::jsonb)::text) LIKE lower(%s) THEN 70
                     WHEN lower(COALESCE(tags, '[]'::jsonb)::text) LIKE lower(%s) THEN 60
                     WHEN lower(COALESCE(summary_text, '')) LIKE lower(%s) THEN 40
                     ELSE 0
                   END AS score
            FROM rag.wiki_page
            WHERE is_active = true
              AND (
                lower(title) LIKE lower(%s)
                OR lower(slug) LIKE lower(%s)
                OR lower(COALESCE(aliases, '[]'::jsonb)::text) LIKE lower(%s)
                OR lower(COALESCE(tags, '[]'::jsonb)::text) LIKE lower(%s)
                OR lower(COALESCE(summary_text, '')) LIKE lower(%s)
              )
            ORDER BY score DESC, length(slug), slug
            LIMIT %s
            """,
            (cleaned, cleaned, pattern, pattern, pattern, pattern, pattern, pattern, pattern, pattern, pattern, limit),
        )
        results = [
            {
                "slug": row[0],
                "title": row[1],
                "summary": row[2] or "",
                "aliasesText": row[3],
                "tagsText": row[4],
                "score": float(row[5]),
            }
            for row in cur.fetchall()
        ]
        return {
            "query": cleaned,
            "results": results,
            "diagnostics": {"wiki_search_ms": round((time.perf_counter() - started) * 1000, 2)},
        }

    def read(self, cur, slug: str, *, chunk_limit: int = 3, neighbor_limit: int = 8) -> dict[str, Any]:
        """Read one wiki page, its top chunks, and shallow in/out edges."""
        started = time.perf_counter()
        page = self._load_page(cur, slug)
        if page is None:
            return {"slug": slug, "found": False, "diagnostics": {"wiki_read_ms": round((time.perf_counter() - started) * 1000, 2)}}

        cur.execute(
            """
            SELECT kc.chunk_no, kc.content, kc.metadata_json
            FROM rag.knowledge_chunk kc
            JOIN rag.knowledge_document kd ON kd.id = kc.document_id
            WHERE kd.external_doc_id = %s
               OR kd.source_ref = %s
            ORDER BY kc.chunk_no
            LIMIT %s
            """,
            (page["id"], page["slug"], chunk_limit),
        )
        chunks = [
            {
                "chunkNo": row[0],
                "content": row[1],
                "metadata": row[2] or {},
            }
            for row in cur.fetchall()
        ]
        edge_pack = self.neighbors(cur, page["slug"], limit=neighbor_limit)
        return {
            "slug": page["slug"],
            "found": True,
            "page": page,
            "chunks": chunks,
            "outgoing": edge_pack.get("outgoing", []),
            "incoming": edge_pack.get("incoming", []),
            "diagnostics": {
                "wiki_read_ms": round((time.perf_counter() - started) * 1000, 2),
                **edge_pack.get("diagnostics", {}),
            },
        }

    def neighbors(
        self,
        cur,
        slug: str,
        *,
        relation_type: str | None = None,
        limit: int = 12,
    ) -> dict[str, Any]:
        """Return incoming and outgoing wiki edges for one slug."""
        started = time.perf_counter()
        page = self._load_page(cur, slug)
        if page is None:
            return {
                "slug": slug,
                "found": False,
                "outgoing": [],
                "incoming": [],
                "diagnostics": {"wiki_neighbors_ms": round((time.perf_counter() - started) * 1000, 2)},
            }
        relation_filter = str(relation_type or "").strip().upper()
        outgoing = self._edge_rows(
            cur,
            page["id"],
            direction="outgoing",
            relation_type=relation_filter,
            limit=limit,
        )
        incoming = self._edge_rows(
            cur,
            page["id"],
            direction="incoming",
            relation_type=relation_filter,
            limit=limit,
        )
        return {
            "slug": page["slug"],
            "found": True,
            "outgoing": outgoing,
            "incoming": incoming,
            "diagnostics": {"wiki_neighbors_ms": round((time.perf_counter() - started) * 1000, 2)},
        }

    def _with_connection(self, fn):
        if not self.db_config:
            raise RuntimeError("WikiToolset db_config is required outside injected cursor tests")
        with psycopg2.connect(**self.db_config) as conn:
            with conn.cursor() as cur:
                return fn(cur)

    def _load_page(self, cur, slug: str) -> dict[str, Any] | None:
        cur.execute(
            """
            SELECT id::text, slug, title, summary_text,
                   difficulty_level::text,
                   COALESCE(aliases, '[]'::jsonb)::text,
                   COALESCE(tags, '[]'::jsonb)::text,
                   frontmatter_json,
                   markdown_content
            FROM rag.wiki_page
            WHERE slug = %s AND is_active = true
            LIMIT 1
            """,
            (slug,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "slug": row[1],
            "title": row[2],
            "summary": row[3] or "",
            "difficulty": row[4],
            "aliasesText": row[5],
            "tagsText": row[6],
            "metadata": row[7] or {},
            "markdown": row[8] or "",
        }

    def _edge_rows(
        self,
        cur,
        page_id: str,
        *,
        direction: str,
        relation_type: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        if direction == "outgoing":
            source_col = "l.from_page_id"
            join_col = "l.to_page_id"
        else:
            source_col = "l.to_page_id"
            join_col = "l.from_page_id"

        relation_sql = "AND l.relation_type = %s" if relation_type else ""
        params: tuple[Any, ...] = (page_id, relation_type, limit) if relation_type else (page_id, limit)
        cur.execute(
            f"""
            SELECT wp.slug, wp.title, l.relation_type, l.weight
            FROM rag.wiki_link l
            JOIN rag.wiki_page wp ON wp.id = {join_col}
            WHERE {source_col}::text = %s
              AND wp.is_active = true
              {relation_sql}
            ORDER BY l.weight DESC, wp.title
            LIMIT %s
            """,
            params,
        )
        return [
            {
                "slug": row[0],
                "title": row[1],
                "relationType": row[2],
                "weight": float(row[3]),
                "direction": direction,
            }
            for row in cur.fetchall()
        ]


def graph_intent_allows_wiki_tools(graph_intent: str | None) -> bool:
    return str(graph_intent or "").strip().upper() in GRAPH_TOOL_INTENTS
