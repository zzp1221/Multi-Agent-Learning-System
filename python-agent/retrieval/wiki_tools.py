"""Read-only wiki graph tools for agent-controlled multi-hop evidence lookup."""

from __future__ import annotations

import re
import time
import unicodedata
from typing import Any

import psycopg2


GRAPH_TOOL_INTENTS = {"PREREQUISITE_PATH", "MULTI_HOP_RELATION", "COMPARISON", "CROSS_LAYER_RELATION"}
ALLOWED_WIKI_RELATION_TYPES = {"WIKILINK", "SHARED_TAG", "SHARED_SOURCE", "COMMUNITY"}
MAX_WIKI_QUERY_CHARS = 320
MAX_WIKI_SLUG_CHARS = 240
MAX_WIKI_MARKDOWN_CHARS = 3000
MAX_WIKI_SEARCH_TERMS = 6

_SLUG_EDGE_CHARS = "\"'` <>[](){}"
_WHITESPACE_RE = re.compile(r"\s+")
_QUOTED_TERM_RE = re.compile(r"[\"'“”‘’《》「」『』]([^\"'“”‘’《》「」『』]{2,80})[\"'“”‘’《》「」『』]")
_ASCII_PHRASE_RE = re.compile(r"\b[A-Z][A-Za-z0-9_+#.-]{1,48}(?:\s+[A-Z][A-Za-z0-9_+#.-]{1,48}){1,3}\b")
_ASCII_TERM_RE = re.compile(r"[A-Za-z][A-Za-z0-9_+#./-]{1,48}")
_CJK_TERM_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9_+#.-]{2,24}")
_WIKI_SEARCH_STOPWORDS = {
    "please",
    "compare",
    "explain",
    "difference",
    "relationship",
    "between",
    "with",
    "and",
    "请",
    "说明",
    "解释",
    "比较",
    "关系",
    "区别",
    "联系",
    "如何",
    "为什么",
    "是什么",
    "请比较",
    "请说明",
    "请解释",
    "如何帮助",
}

_WIKI_SEARCH_STOP_PREFIXES = ("请", "说明", "解释", "比较", "如何", "为什么")


def clean_wiki_query(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    text = _WHITESPACE_RE.sub(" ", text)
    if len(text) > MAX_WIKI_QUERY_CHARS:
        text = text[:MAX_WIKI_QUERY_CHARS].rstrip()
    return text


def normalize_wiki_slug(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().strip(_SLUG_EDGE_CHARS)
    if not text or text.lower() == "none":
        return ""
    if text.lower().startswith("wiki://"):
        text = text[7:]
    text = text.strip().strip(_SLUG_EDGE_CHARS).strip("/")
    if not text or len(text) > MAX_WIKI_SLUG_CHARS:
        return ""
    return text


def normalize_wiki_relation_type(value: Any) -> str:
    return str(value or "").strip().upper()


def bounded_wiki_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def wiki_search_terms(query: Any) -> list[str]:
    cleaned = clean_wiki_query(query)
    terms: list[str] = []

    def add_term(value: Any) -> None:
        term = clean_wiki_query(value).strip(_SLUG_EDGE_CHARS)
        if len(term) < 2 or term.lower() in _WIKI_SEARCH_STOPWORDS:
            return
        if term.isascii() and len(term) < 3 and "/" not in term:
            return
        if any(term.startswith(prefix) for prefix in _WIKI_SEARCH_STOP_PREFIXES):
            return
        term_key = term.lower()
        for existing in terms:
            existing_tokens = {token.lower() for token in existing.split()}
            if term_key in existing_tokens:
                return
        if term not in terms:
            terms.append(term)

    for match in _QUOTED_TERM_RE.finditer(cleaned):
        add_term(match.group(1))
        if len(terms) >= MAX_WIKI_SEARCH_TERMS:
            return terms

    for match in _ASCII_PHRASE_RE.finditer(cleaned):
        add_term(match.group(0))
        if len(terms) >= MAX_WIKI_SEARCH_TERMS:
            return terms

    for match in _ASCII_TERM_RE.finditer(cleaned):
        add_term(match.group(0))
        if len(terms) >= MAX_WIKI_SEARCH_TERMS:
            return terms

    for match in _CJK_TERM_RE.finditer(cleaned):
        add_term(match.group(0))
        if len(terms) >= MAX_WIKI_SEARCH_TERMS:
            return terms

    return terms


class WikiToolset:
    """Small read-only facade over rag.wiki_page, rag.wiki_link, and chunks."""

    def __init__(self, db_config: dict[str, Any] | None = None) -> None:
        self.db_config = db_config or {}

    def wiki_search(self, query: str, *, limit: int = 5) -> dict[str, Any]:
        cleaned = clean_wiki_query(query)
        limit = bounded_wiki_int(limit, default=5, minimum=1, maximum=8)
        if not cleaned:
            return {"query": cleaned, "results": [], "diagnostics": {"wiki_search_ms": 0.0, "reason": "empty query"}}
        return self._with_connection(lambda cur: self.search(cur, cleaned, limit=limit))

    def wiki_read(
        self,
        slug: str,
        *,
        chunk_limit: int = 3,
        neighbor_limit: int = 8,
        query: str | None = None,
    ) -> dict[str, Any]:
        normalized_slug = normalize_wiki_slug(slug)
        chunk_limit = bounded_wiki_int(chunk_limit, default=3, minimum=1, maximum=5)
        neighbor_limit = bounded_wiki_int(neighbor_limit, default=8, minimum=1, maximum=12)
        cleaned_query = clean_wiki_query(query)
        if not normalized_slug:
            return {
                "slug": normalized_slug,
                "found": False,
                "diagnostics": {"wiki_read_ms": 0.0, "reason": "empty or invalid slug"},
            }
        return self._with_connection(
            lambda cur: self.read(
                cur,
                normalized_slug,
                chunk_limit=chunk_limit,
                neighbor_limit=neighbor_limit,
                query=cleaned_query,
            )
        )

    def wiki_neighbors(
        self,
        slug: str,
        *,
        relation_type: str | None = None,
        limit: int = 12,
    ) -> dict[str, Any]:
        normalized_slug = normalize_wiki_slug(slug)
        relation_filter = normalize_wiki_relation_type(relation_type)
        limit = bounded_wiki_int(limit, default=12, minimum=1, maximum=12)
        if not normalized_slug:
            return {
                "slug": normalized_slug,
                "found": False,
                "outgoing": [],
                "incoming": [],
                "diagnostics": {"wiki_neighbors_ms": 0.0, "reason": "empty or invalid slug"},
            }
        if relation_filter and relation_filter not in ALLOWED_WIKI_RELATION_TYPES:
            return {
                "slug": normalized_slug,
                "found": False,
                "outgoing": [],
                "incoming": [],
                "diagnostics": {"wiki_neighbors_ms": 0.0, "reason": "invalid relationType"},
            }
        return self._with_connection(
            lambda cur: self.neighbors(cur, normalized_slug, relation_type=relation_filter, limit=limit)
        )

    def search(self, cur, query: str, *, limit: int = 5) -> dict[str, Any]:
        """Search title/aliases/tags/summary first; no embedding API call."""
        started = time.perf_counter()
        cleaned = clean_wiki_query(query)
        limit = bounded_wiki_int(limit, default=5, minimum=1, maximum=8)
        if not cleaned:
            return {"query": cleaned, "results": [], "diagnostics": {"wiki_search_ms": 0.0}}
        search_terms = wiki_search_terms(cleaned)
        patterns = [f"%{cleaned}%"] + [f"%{term}%" for term in search_terms]
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
                     WHEN EXISTS (
                       SELECT 1 FROM unnest(%s::text[]) AS p(pattern)
                       WHERE lower(title) LIKE lower(p.pattern)
                     ) THEN 55
                     WHEN EXISTS (
                       SELECT 1 FROM unnest(%s::text[]) AS p(pattern)
                       WHERE lower(COALESCE(aliases, '[]'::jsonb)::text) LIKE lower(p.pattern)
                     ) THEN 50
                     WHEN EXISTS (
                       SELECT 1 FROM unnest(%s::text[]) AS p(pattern)
                       WHERE lower(COALESCE(tags, '[]'::jsonb)::text) LIKE lower(p.pattern)
                     ) THEN 45
                     WHEN EXISTS (
                       SELECT 1 FROM unnest(%s::text[]) AS p(pattern)
                       WHERE lower(COALESCE(summary_text, '')) LIKE lower(p.pattern)
                     ) THEN 30
                     ELSE 0
                   END AS score
            FROM rag.wiki_page
            WHERE is_active = true
              AND (
                EXISTS (
                  SELECT 1 FROM unnest(%s::text[]) AS p(pattern)
                  WHERE lower(title) LIKE lower(p.pattern)
                     OR lower(slug) LIKE lower(p.pattern)
                     OR lower(COALESCE(aliases, '[]'::jsonb)::text) LIKE lower(p.pattern)
                     OR lower(COALESCE(tags, '[]'::jsonb)::text) LIKE lower(p.pattern)
                     OR lower(COALESCE(summary_text, '')) LIKE lower(p.pattern)
                )
              )
            ORDER BY score DESC, length(slug), slug
            LIMIT %s
            """,
            (
                cleaned,
                cleaned,
                patterns[0],
                patterns[0],
                patterns[0],
                patterns[0],
                patterns[1:],
                patterns[1:],
                patterns[1:],
                patterns[1:],
                patterns,
                limit,
            ),
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
            "diagnostics": {
                "wiki_search_ms": round((time.perf_counter() - started) * 1000, 2),
                "fallbackTerms": search_terms,
            },
        }

    def read(
        self,
        cur,
        slug: str,
        *,
        chunk_limit: int = 3,
        neighbor_limit: int = 8,
        query: str | None = None,
    ) -> dict[str, Any]:
        """Read one wiki page, its top chunks, and shallow in/out edges."""
        started = time.perf_counter()
        normalized_slug = normalize_wiki_slug(slug)
        chunk_limit = bounded_wiki_int(chunk_limit, default=3, minimum=1, maximum=5)
        neighbor_limit = bounded_wiki_int(neighbor_limit, default=8, minimum=1, maximum=12)
        cleaned_query = clean_wiki_query(query)
        if not normalized_slug:
            return {
                "slug": normalized_slug,
                "found": False,
                "diagnostics": {
                    "wiki_read_ms": 0.0,
                    "reason": "empty or invalid slug",
                },
            }
        page = self._load_page(cur, normalized_slug)
        if page is None:
            return {
                "slug": normalized_slug,
                "found": False,
                "diagnostics": {"wiki_read_ms": round((time.perf_counter() - started) * 1000, 2)},
            }

        if cleaned_query:
            full_pattern = f"%{cleaned_query}%"
            term_patterns = [f"%{term}%" for term in wiki_search_terms(cleaned_query)]
            cur.execute(
                """
                WITH page_chunks AS (
                    SELECT kc.chunk_no, kc.content, kc.metadata_json,
                           (
                             CASE WHEN kc.content ILIKE %s THEN 100 ELSE 0 END
                             + CASE WHEN COALESCE(kc.metadata_json::text, '') ILIKE %s THEN 30 ELSE 0 END
                             + (
                               SELECT COUNT(*) * 10
                               FROM unnest(%s::text[]) AS p(pattern)
                               WHERE kc.content ILIKE p.pattern
                                  OR COALESCE(kc.metadata_json::text, '') ILIKE p.pattern
                             )
                           ) AS query_score
                    FROM rag.knowledge_chunk kc
                    JOIN rag.knowledge_document kd ON kd.id = kc.document_id
                    WHERE kd.external_doc_id = %s
                       OR kd.source_ref = %s
                )
                SELECT chunk_no, content, metadata_json
                FROM page_chunks
                ORDER BY query_score DESC, chunk_no
                LIMIT %s
                """,
                (full_pattern, full_pattern, term_patterns, page["id"], page["slug"], chunk_limit),
            )
        else:
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
        edge_pack = self._neighbors_for_page(cur, page, relation_type=None, limit=neighbor_limit, started=started)
        return {
            "slug": page["slug"],
            "found": True,
            "page": self._bounded_page_payload(page),
            "chunks": chunks,
            "outgoing": edge_pack.get("outgoing", []),
            "incoming": edge_pack.get("incoming", []),
            "diagnostics": {
                "wiki_read_ms": round((time.perf_counter() - started) * 1000, 2),
                "chunkQuery": cleaned_query,
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
        normalized_slug = normalize_wiki_slug(slug)
        limit = bounded_wiki_int(limit, default=12, minimum=1, maximum=12)
        if not normalized_slug:
            return {
                "slug": normalized_slug,
                "found": False,
                "outgoing": [],
                "incoming": [],
                "diagnostics": {
                    "wiki_neighbors_ms": 0.0,
                    "reason": "empty or invalid slug",
                },
            }
        page = self._load_page(cur, normalized_slug)
        if page is None:
            return {
                "slug": normalized_slug,
                "found": False,
                "outgoing": [],
                "incoming": [],
                "diagnostics": {"wiki_neighbors_ms": round((time.perf_counter() - started) * 1000, 2)},
            }
        relation_filter = normalize_wiki_relation_type(relation_type)
        if relation_filter and relation_filter not in ALLOWED_WIKI_RELATION_TYPES:
            return {
                "slug": page["slug"],
                "found": True,
                "outgoing": [],
                "incoming": [],
                "diagnostics": {
                    "wiki_neighbors_ms": round((time.perf_counter() - started) * 1000, 2),
                    "reason": "invalid relationType",
                },
            }
        return self._neighbors_for_page(cur, page, relation_type=relation_filter, limit=limit, started=started)

    def _neighbors_for_page(
        self,
        cur,
        page: dict[str, Any],
        *,
        relation_type: str | None,
        limit: int,
        started: float,
    ) -> dict[str, Any]:
        relation_filter = normalize_wiki_relation_type(relation_type)
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
        normalized_slug = normalize_wiki_slug(slug)
        if not normalized_slug:
            return None
        cur.execute(
            """
            SELECT id::text, slug, title, summary_text,
                   difficulty_level::text,
                   COALESCE(aliases, '[]'::jsonb)::text,
                   COALESCE(tags, '[]'::jsonb)::text,
                   frontmatter_json,
                   markdown_content
            FROM rag.wiki_page
            WHERE is_active = true
              AND (slug = %s OR lower(slug) = lower(%s))
            ORDER BY CASE WHEN slug = %s THEN 0 ELSE 1 END
            LIMIT 1
            """,
            (normalized_slug, normalized_slug, normalized_slug),
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

    def _bounded_page_payload(self, page: dict[str, Any]) -> dict[str, Any]:
        payload = dict(page)
        markdown = str(payload.get("markdown") or "")
        payload["markdown"] = self._truncate_text(markdown, MAX_WIKI_MARKDOWN_CHARS)
        payload["markdownTruncated"] = len(markdown) > MAX_WIKI_MARKDOWN_CHARS
        return payload

    def _truncate_text(self, text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        return text[:max_chars].rstrip() + "\n...[truncated]"

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
