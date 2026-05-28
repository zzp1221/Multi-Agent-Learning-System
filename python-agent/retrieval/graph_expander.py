"""
Graph expansion channel: traverse rag.wiki_link from seed pages.
"""
import json
import re
from collections import defaultdict


class GraphExpander:
    """Expand from seed slugs via WIKILINK and SHARED_TAG relations."""

    def build_prerequisite_evidence(
        self,
        cur,
        seed_slugs: list[str],
        query: str | None,
        *,
        direct_limit: int = 8,
    ) -> dict:
        """Build prerequisite-only evidence candidates from explicit query terms."""
        query_terms = self._extract_direct_evidence_terms(query)
        if not query_terms:
            return {"queryTerms": [], "protectedSeeds": [], "directEvidence": []}

        protected_seeds = self._protected_seed_candidates(cur, seed_slugs, query_terms)
        direct_evidence = self._direct_evidence_candidates(
            cur,
            query_terms,
            protected_seed_slugs={item[0] for item in protected_seeds},
            limit=direct_limit,
        )
        return {
            "queryTerms": query_terms,
            "protectedSeeds": protected_seeds,
            "directEvidence": direct_evidence,
        }

    def expand(
        self,
        cur,
        seed_slugs: list[str],
        top_n: int = 5,
        min_shared_tags: int = 3,
        query: str | None = None,
        graph_intent: str | None = None,
    ) -> list[tuple]:
        """
        Returns top_n neighbors as [(slug, title, score), ...].
        Score = WIKILINK_count * 2 + SHARED_TAG_count.
        """
        if not seed_slugs:
            return []

        # Get wiki page IDs for seed slugs
        cur.execute("""
            SELECT id, slug, title FROM rag.wiki_page
            WHERE slug = ANY(%s) AND is_active = true
        """, (seed_slugs,))
        seed_rows = cur.fetchall()
        if not seed_rows:
            return []

        seed_ids = [row[0] for row in seed_rows]
        seed_slug_set = {row[1] for row in seed_rows}

        # Treat wiki graph edges as evidence links: prerequisites often point
        # into the current page, while applications point outward.
        cur.execute("""
            SELECT
                CASE
                    WHEN l.from_page_id::text = ANY(%s) THEN l.to_page_id
                    ELSE l.from_page_id
                END AS neighbor_id,
                l.relation_type,
                COUNT(*) AS strength
            FROM rag.wiki_link l
            WHERE l.from_page_id::text = ANY(%s)
               OR l.to_page_id::text = ANY(%s)
            GROUP BY neighbor_id, l.relation_type
        """, (seed_ids, seed_ids, seed_ids))

        neighbor_scores = defaultdict(lambda: {"WIKILINK": 0, "SHARED_TAG": 0})
        for neighbor_id, relation_type, strength in cur.fetchall():
            neighbor_key = str(neighbor_id)
            if neighbor_key in seed_ids:
                continue
            neighbor_scores[neighbor_key][relation_type] = int(strength)

        qualified = []
        for neighbor_id, scores in neighbor_scores.items():
            if scores["WIKILINK"] > 0 or scores["SHARED_TAG"] >= min_shared_tags:
                qualified.append((neighbor_id, scores["WIKILINK"] * 2 + scores["SHARED_TAG"]))
        qualified.sort(key=lambda item: item[1], reverse=True)

        if not qualified:
            return []

        candidate_ids = [neighbor_id for neighbor_id, _ in qualified[: top_n * 3]]
        score_by_id = {neighbor_id: score for neighbor_id, score in qualified}
        cur.execute("""
            SELECT wp.id::text, wp.slug, wp.title,
                   COALESCE(f.community_id, 0) AS community_id,
                   COALESCE(f.pagerank_score, 0) AS pagerank_score,
                   COALESCE(wp.aliases, '[]'::jsonb)::text AS aliases_text,
                   COALESCE(wp.tags, '[]'::jsonb)::text AS tags_text
            FROM rag.wiki_page wp
            LEFT JOIN rag.wiki_page_graph_features f ON f.page_id = wp.id
            WHERE wp.id::text = ANY(%s)
              AND wp.is_active = true
        """, (candidate_ids,))

        neighbors = []
        query_terms = self._extract_query_terms(query) if self._normalize_graph_intent(graph_intent) == "PREREQUISITE_PATH" else []
        for neighbor_id, neighbor_slug, neighbor_title, community_id, pagerank_score, aliases_text, tags_text in cur.fetchall():
            if neighbor_slug in seed_slug_set:
                continue
            if self._normalize_graph_intent(graph_intent) == "PREREQUISITE_PATH" and self._is_low_value_resource(neighbor_slug, neighbor_title):
                continue
            graph_score = score_by_id.get(neighbor_id, 0)
            query_bonus = self._query_bonus(query_terms, neighbor_slug, neighbor_title, aliases_text, tags_text)
            community_bonus = 0.75 if int(community_id) in self._load_seed_communities(cur, seed_ids) else 0.0
            pagerank_bonus = float(pagerank_score) * 0.5
            neighbors.append(
                (
                    neighbor_slug,
                    neighbor_title,
                    round(float(graph_score) + query_bonus + community_bonus + pagerank_bonus, 4),
                    int(community_id),
                    float(pagerank_score),
                )
            )

        ranked = sorted(
            neighbors,
            key=lambda item: (
                item[2],
                item[3],
                item[4],
                -len(str(item[0])),
                item[0],
            ),
            reverse=True,
        )
        return [(slug, title, score) for slug, title, score, _, _ in ranked[:top_n]]

    def expand_outgoing(self, cur, seed_slugs: list[str], top_n: int = 5) -> list[tuple]:
        """Legacy outgoing-only expansion kept for diagnostics."""
        if not seed_slugs:
            return []

        cur.execute("""
            SELECT id, slug, title FROM rag.wiki_page
            WHERE slug = ANY(%s) AND is_active = true
        """, (seed_slugs,))
        seed_rows = cur.fetchall()
        if not seed_rows:
            return []

        seed_ids = [row[0] for row in seed_rows]
        seed_slug_set = {row[1] for row in seed_rows}
        cur.execute("""
            SELECT
                wl2.slug AS neighbor_slug,
                wl2.title AS neighbor_title,
                SUM(CASE WHEN l.relation_type = 'WIKILINK' THEN 2 ELSE 1 END) AS score
            FROM rag.wiki_link l
            JOIN rag.wiki_page wl2 ON wl2.id = l.to_page_id
            WHERE l.from_page_id::text = ANY(%s)
              AND wl2.is_active = true
              AND wl2.slug != ALL(%s)
            GROUP BY wl2.slug, wl2.title
            ORDER BY score DESC
            LIMIT %s
        """, (seed_ids, seed_slugs, top_n * 2))

        neighbors = {}
        for neighbor_slug, neighbor_title, score in cur.fetchall():
            if neighbor_slug not in seed_slug_set:
                neighbors[neighbor_slug] = (neighbor_slug, neighbor_title, int(score))

        # Sort by score and return top_n
        return sorted(neighbors.values(), key=lambda x: x[2], reverse=True)[:top_n]

    def explain_candidates(
        self,
        cur,
        seed_slugs: list[str],
        *,
        limit: int = 50,
        default_window: int = 15,
        min_shared_tags: int = 3,
        query: str | None = None,
        graph_intent: str | None = None,
    ) -> dict:
        """Return graph candidate diagnostics without changing retrieval behavior."""
        if not seed_slugs:
            return {"seedSlugs": [], "queryTerms": [], "candidates": []}

        cur.execute("""
            SELECT id, slug, title FROM rag.wiki_page
            WHERE slug = ANY(%s) AND is_active = true
        """, (seed_slugs,))
        seed_rows = cur.fetchall()
        if not seed_rows:
            return {"seedSlugs": seed_slugs, "queryTerms": [], "candidates": []}

        seed_ids = [row[0] for row in seed_rows]
        seed_slug_set = {row[1] for row in seed_rows}
        cur.execute("""
            SELECT
                CASE
                    WHEN l.from_page_id::text = ANY(%s) THEN l.to_page_id
                    ELSE l.from_page_id
                END AS neighbor_id,
                l.relation_type,
                COUNT(*) AS strength
            FROM rag.wiki_link l
            WHERE l.from_page_id::text = ANY(%s)
               OR l.to_page_id::text = ANY(%s)
            GROUP BY neighbor_id, l.relation_type
        """, (seed_ids, seed_ids, seed_ids))

        neighbor_scores = defaultdict(lambda: {"WIKILINK": 0, "SHARED_TAG": 0})
        for neighbor_id, relation_type, strength in cur.fetchall():
            neighbor_key = str(neighbor_id)
            if neighbor_key in seed_ids:
                continue
            neighbor_scores[neighbor_key][relation_type] = int(strength)

        qualified = []
        for neighbor_id, scores in neighbor_scores.items():
            if scores["WIKILINK"] > 0 or scores["SHARED_TAG"] >= min_shared_tags:
                qualified.append(
                    (
                        neighbor_id,
                        scores["WIKILINK"] * 2 + scores["SHARED_TAG"],
                        scores["WIKILINK"],
                        scores["SHARED_TAG"],
                    )
                )
        qualified.sort(key=lambda item: item[1], reverse=True)
        if not qualified:
            return {"seedSlugs": list(seed_slug_set), "queryTerms": [], "candidates": []}

        candidate_ids = [neighbor_id for neighbor_id, *_ in qualified[:limit]]
        rank_by_id = {neighbor_id: rank for rank, (neighbor_id, *_) in enumerate(qualified, start=1)}
        scores_by_id = {
            neighbor_id: {"base": base_score, "wikilink": wikilink, "sharedTag": shared_tag}
            for neighbor_id, base_score, wikilink, shared_tag in qualified
        }
        cur.execute("""
            SELECT wp.id::text, wp.slug, wp.title,
                   COALESCE(f.community_id, 0) AS community_id,
                   COALESCE(f.pagerank_score, 0) AS pagerank_score,
                   COALESCE(wp.aliases, '[]'::jsonb)::text AS aliases_text,
                   COALESCE(wp.tags, '[]'::jsonb)::text AS tags_text
            FROM rag.wiki_page wp
            LEFT JOIN rag.wiki_page_graph_features f ON f.page_id = wp.id
            WHERE wp.id::text = ANY(%s)
              AND wp.is_active = true
        """, (candidate_ids,))

        query_terms = self._extract_query_terms(query)
        scoring_terms = query_terms if self._normalize_graph_intent(graph_intent) == "PREREQUISITE_PATH" else []
        page_rows = cur.fetchall()
        seed_communities = self._load_seed_communities(cur, seed_ids)
        candidates = []
        for row in page_rows:
            neighbor_id, slug, title, community_id, pagerank_score, aliases_text, tags_text = row
            if slug in seed_slug_set:
                continue
            score_parts = scores_by_id.get(neighbor_id, {"base": 0, "wikilink": 0, "sharedTag": 0})
            query_bonus = self._query_bonus(scoring_terms, slug, title, aliases_text, tags_text)
            community_bonus = 0.75 if int(community_id) in seed_communities else 0.0
            pagerank_bonus = float(pagerank_score) * 0.5
            low_value = self._is_low_value_resource(slug, title)
            candidates.append(
                {
                    "qualifiedRank": rank_by_id.get(neighbor_id),
                    "slug": slug,
                    "title": title,
                    "baseScore": score_parts["base"],
                    "wikilinkCount": score_parts["wikilink"],
                    "sharedTagCount": score_parts["sharedTag"],
                    "queryBonus": round(query_bonus, 4),
                    "communityBonus": round(community_bonus, 4),
                    "pagerankBonus": round(pagerank_bonus, 4),
                    "finalScore": round(float(score_parts["base"]) + query_bonus + community_bonus + pagerank_bonus, 4),
                    "inDefaultWindow": bool(rank_by_id.get(neighbor_id, limit + 1) <= default_window),
                    "lowValueFilteredForPrerequisite": bool(
                        self._normalize_graph_intent(graph_intent) == "PREREQUISITE_PATH" and low_value
                    ),
                }
            )

        candidates.sort(key=lambda item: (-float(item["finalScore"]), int(item["qualifiedRank"] or limit + 1)))
        return {
            "seedSlugs": list(seed_slug_set),
            "queryTerms": query_terms,
            "defaultWindow": default_window,
            "candidates": candidates[:limit],
        }

    def _load_seed_communities(self, cur, seed_ids: list[str]) -> set[int]:
        if not seed_ids:
            return set()
        cur.execute("""
            SELECT DISTINCT COALESCE(f.community_id, 0)
            FROM rag.wiki_page_graph_features f
            WHERE f.page_id::text = ANY(%s)
        """, (seed_ids,))
        return {int(row[0]) for row in cur.fetchall()}

    def _normalize_graph_intent(self, graph_intent: str | None) -> str:
        return str(graph_intent or "").strip().upper()

    def _extract_query_terms(self, query: str | None) -> list[str]:
        if not query:
            return []
        terms = re.findall(r"[A-Za-z0-9+\-#_.]{2,}|[\u4e00-\u9fff]{2,}", query)
        seen: set[str] = set()
        result: list[str] = []
        for term in terms:
            normalized = self._normalize_text(term)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            result.append(normalized)
        return result[:8]

    def _extract_direct_evidence_terms(self, query: str | None) -> list[str]:
        terms = self._extract_query_terms(query)
        if query:
            quoted_chunks = re.findall(r"[「《“\"]([^」》”\"]+)[」》”\"]", query)
            for chunk in quoted_chunks:
                terms.append(self._normalize_text(chunk))
                terms.extend(
                    self._normalize_text(part)
                    for part in re.split(r"[、,，/（）()\\s]+", chunk)
                    if self._normalize_text(part)
                )

        seen: set[str] = set()
        result: list[str] = []
        for term in terms:
            normalized = self._normalize_text(term)
            if not normalized or len(normalized) < 2 or normalized in seen:
                continue
            seen.add(normalized)
            result.append(normalized)
        return result[:18]

    def _protected_seed_candidates(self, cur, seed_slugs: list[str], query_terms: list[str]) -> list[tuple]:
        if not seed_slugs:
            return []
        cur.execute("""
            SELECT wp.id::text, wp.slug, wp.title,
                   COALESCE(f.community_id, 0) AS community_id,
                   COALESCE(f.pagerank_score, 0) AS pagerank_score,
                   COALESCE(wp.aliases, '[]'::jsonb)::text AS aliases_text,
                   COALESCE(wp.tags, '[]'::jsonb)::text AS tags_text
            FROM rag.wiki_page wp
            LEFT JOIN rag.wiki_page_graph_features f ON f.page_id = wp.id
            WHERE wp.slug = ANY(%s)
              AND wp.is_active = true
        """, (seed_slugs,))
        protected = []
        for _page_id, slug, title, _community_id, pagerank_score, aliases_text, tags_text in cur.fetchall():
            if self._is_low_value_resource(slug, title):
                continue
            bonus = self._query_bonus(query_terms, slug, title, aliases_text, tags_text)
            if bonus < 3.0:
                continue
            protected.append((slug, title, round(25.0 + bonus + float(pagerank_score) * 0.5, 4), "seed_protected"))
        protected.sort(key=lambda item: item[2], reverse=True)
        return protected

    def _direct_evidence_candidates(
        self,
        cur,
        query_terms: list[str],
        *,
        protected_seed_slugs: set[str],
        limit: int,
    ) -> list[tuple]:
        candidates: dict[str, tuple] = {}
        for term in query_terms:
            pattern = f"%{term}%"
            cur.execute(
                """
                SELECT wp.id::text, wp.slug, wp.title,
                       COALESCE(f.community_id, 0) AS community_id,
                       COALESCE(f.pagerank_score, 0) AS pagerank_score,
                       COALESCE(wp.aliases, '[]'::jsonb)::text AS aliases_text,
                       COALESCE(wp.tags, '[]'::jsonb)::text AS tags_text
                FROM rag.wiki_page wp
                LEFT JOIN rag.wiki_page_graph_features f ON f.page_id = wp.id
                WHERE wp.is_active = true
                  AND (
                    lower(wp.slug) LIKE lower(%s)
                    OR lower(wp.title) LIKE lower(%s)
                    OR lower(COALESCE(wp.aliases, '[]'::jsonb)::text) LIKE lower(%s)
                    OR lower(COALESCE(wp.tags, '[]'::jsonb)::text) LIKE lower(%s)
                  )
                ORDER BY length(wp.slug), wp.slug
                LIMIT 20
                """,
                (pattern, pattern, pattern, pattern),
            )
            for _page_id, slug, title, _community_id, pagerank_score, aliases_text, tags_text in cur.fetchall():
                if slug in protected_seed_slugs or self._is_low_value_resource(slug, title):
                    continue
                if not self._has_specific_direct_match(query_terms, slug, title, aliases_text, tags_text):
                    continue
                bonus = self._query_bonus(query_terms, slug, title, aliases_text, tags_text)
                if bonus < 3.0:
                    continue
                score = round(30.0 + bonus + float(pagerank_score) * 0.5, 4)
                existing = candidates.get(slug)
                if existing is None or score > existing[2]:
                    candidates[slug] = (slug, title, score, "direct_evidence")

        ranked = sorted(
            candidates.values(),
            key=lambda item: (float(item[2]), -len(str(item[0])), str(item[0])),
            reverse=True,
        )
        return ranked[:limit]

    def _has_specific_direct_match(self, query_terms: list[str], slug: str, title: str, aliases_text, tags_text) -> bool:
        slug_norm = self._normalize_text(slug)
        title_norm = self._normalize_text(title)
        alias_norms = {self._normalize_text(alias) for alias in self._parse_json_list(aliases_text)}
        tag_norms = {self._normalize_text(tag) for tag in self._parse_json_list(tags_text)}
        generic_terms = {"alias", "doc", "docs", "path", "query", "seed"}
        for term in query_terms:
            if term in generic_terms:
                continue
            has_cjk = bool(re.search(r"[\u4e00-\u9fff]", term))
            if (has_cjk and len(term) < 3) or (not has_cjk and len(term) < 4):
                continue
            if (
                term in slug_norm
                or term in title_norm
                or any(term in alias_norm for alias_norm in alias_norms)
                or any(term in tag_norm for tag_norm in tag_norms)
            ):
                return True
        return False

    def _normalize_text(self, text: str) -> str:
        return re.sub(r"\s+", "", str(text or "").strip().lower())

    def _parse_json_list(self, value) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return [value.strip()] if value.strip() else []
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        return []

    def _query_bonus(self, query_terms: list[str], slug: str, title: str, aliases_text, tags_text) -> float:
        if not query_terms:
            return 0.0
        slug_norm = self._normalize_text(slug)
        title_norm = self._normalize_text(title)
        alias_norms = {self._normalize_text(alias) for alias in self._parse_json_list(aliases_text)}
        tag_norms = {self._normalize_text(tag) for tag in self._parse_json_list(tags_text)}
        bonus = 0.0
        for term in query_terms:
            if not term or len(term) < 2:
                continue
            if term == slug_norm or term == title_norm:
                bonus += 3.0
                continue
            if term in alias_norms:
                bonus += 2.5
                continue
            if (
                term in title_norm
                or term in slug_norm
                or any(term in alias_norm for alias_norm in alias_norms)
                or any(term in tag_norm for tag_norm in tag_norms)
            ):
                bonus += 1.5
        return bonus

    def _is_low_value_resource(self, slug: str, title: str) -> bool:
        lowered_slug = self._normalize_text(slug)
        lowered_title = self._normalize_text(title)
        return (
            lowered_slug.startswith("http")
            or lowered_slug.startswith("wiki://")
            or "视频资源" in lowered_slug
            or "视频资源" in title
            or "视频资源" in lowered_title
            or "视频" in lowered_title
            or "video" in lowered_slug
        )
