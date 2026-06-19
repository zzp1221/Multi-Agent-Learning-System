"""
Graph expansion channel: traverse rag.wiki_link from seed pages.
"""
import json
import re
from collections import defaultdict

PREREQUISITE_PATH_INTENT = "PREREQUISITE_PATH"
GRAPH_SOURCE_1HOP = "graph_1hop"
GRAPH_SOURCE_2HOP = "graph_2hop"
TWO_HOP_DECAY = 0.45
MIN_ONE_HOP_WINDOW = 12
MAX_ONE_HOP_WINDOW = 24
TWO_HOP_PER_NODE_LIMIT = 3
MAX_TWO_HOP_CANDIDATES = 24


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
        PREREQUISITE_PATH appends a source marker: (slug, title, score, source).
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

        seed_ids = [str(row[0]) for row in seed_rows]
        seed_slug_set = {row[1] for row in seed_rows}
        prerequisite_intent = self._is_prerequisite_path(graph_intent)

        qualified = self._one_hop_qualified(cur, seed_ids, min_shared_tags)

        if not qualified:
            return []

        one_hop_window = self._one_hop_window(top_n) if prerequisite_intent else top_n * 3
        one_hop_ids = [neighbor_id for neighbor_id, *_ in qualified[:one_hop_window]]
        one_hop_scores = self._score_parts_by_id(qualified)
        seed_communities = self._load_seed_communities(cur, seed_ids)
        query_terms = self._extract_query_terms(query) if prerequisite_intent else []
        candidates = self._score_page_rows(
            self._load_page_rows(cur, one_hop_ids),
            one_hop_scores,
            query_terms=query_terms,
            seed_communities=seed_communities,
            seed_slug_set=seed_slug_set,
            hop=1,
            source=GRAPH_SOURCE_1HOP,
            prerequisite_intent=prerequisite_intent,
            skip_low_value=prerequisite_intent,
        )

        if prerequisite_intent:
            two_hop_scores = self._two_hop_qualified(
                cur,
                one_hop_ids,
                excluded_ids=set(seed_ids) | set(one_hop_ids),
                min_shared_tags=min_shared_tags,
            )
            candidates.extend(
                self._score_page_rows(
                    self._load_page_rows(cur, list(two_hop_scores.keys())),
                    two_hop_scores,
                    query_terms=query_terms,
                    seed_communities=seed_communities,
                    seed_slug_set=seed_slug_set,
                    hop=2,
                    source=GRAPH_SOURCE_2HOP,
                    prerequisite_intent=True,
                    skip_low_value=True,
                )
            )

        ranked = self._rank_candidates(self._dedupe_candidates(candidates))
        return [self._candidate_tuple(candidate, include_source=prerequisite_intent) for candidate in ranked[:top_n]]

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

        seed_ids = [str(row[0]) for row in seed_rows]
        seed_slug_set = {row[1] for row in seed_rows}
        prerequisite_intent = self._is_prerequisite_path(graph_intent)
        qualified = self._one_hop_qualified(cur, seed_ids, min_shared_tags)
        if not qualified:
            return {"seedSlugs": list(seed_slug_set), "queryTerms": [], "candidates": []}

        rank_by_id = {neighbor_id: rank for rank, (neighbor_id, *_) in enumerate(qualified, start=1)}
        query_terms = self._extract_query_terms(query)
        scoring_terms = query_terms if prerequisite_intent else []
        seed_communities = self._load_seed_communities(cur, seed_ids)
        one_hop_ids = [neighbor_id for neighbor_id, *_ in qualified[:limit]]
        candidates = self._score_page_rows(
            self._load_page_rows(cur, one_hop_ids),
            self._score_parts_by_id(qualified),
            query_terms=scoring_terms,
            seed_communities=seed_communities,
            seed_slug_set=seed_slug_set,
            hop=1,
            source=GRAPH_SOURCE_1HOP,
            prerequisite_intent=prerequisite_intent,
            skip_low_value=prerequisite_intent,
        )

        two_hop_rank_by_id = {}
        if prerequisite_intent:
            two_hop_source_window = min(max(default_window, MIN_ONE_HOP_WINDOW), MAX_ONE_HOP_WINDOW)
            two_hop_source_ids = [neighbor_id for neighbor_id, *_ in qualified[:two_hop_source_window]]
            two_hop_scores = self._two_hop_qualified(
                cur,
                two_hop_source_ids,
                excluded_ids=set(seed_ids) | set(two_hop_source_ids),
                min_shared_tags=min_shared_tags,
            )
            two_hop_rank_by_id = {neighbor_id: rank for rank, neighbor_id in enumerate(two_hop_scores, start=1)}
            candidates.extend(
                self._score_page_rows(
                    self._load_page_rows(cur, list(two_hop_scores.keys())),
                    two_hop_scores,
                    query_terms=scoring_terms,
                    seed_communities=seed_communities,
                    seed_slug_set=seed_slug_set,
                    hop=2,
                    source=GRAPH_SOURCE_2HOP,
                    prerequisite_intent=True,
                    skip_low_value=True,
                )
            )

        formatted = []
        for candidate in self._rank_candidates(self._dedupe_candidates(candidates)):
            page_id = candidate["id"]
            qualified_rank = rank_by_id.get(page_id) if candidate["hop"] == 1 else two_hop_rank_by_id.get(page_id)
            formatted.append(
                {
                    "qualifiedRank": qualified_rank,
                    "slug": candidate["slug"],
                    "title": candidate["title"],
                    "source": candidate["source"],
                    "hop": candidate["hop"],
                    "sourceId": candidate["sourceId"],
                    "baseScore": candidate["base"],
                    "wikilinkCount": candidate["wikilink"],
                    "sharedTagCount": candidate["sharedTag"],
                    "queryBonus": candidate["queryBonus"],
                    "communityBonus": candidate["communityBonus"],
                    "pagerankBonus": candidate["pagerankBonus"],
                    "finalScore": candidate["score"],
                    "inDefaultWindow": bool(candidate["hop"] == 1 and rank_by_id.get(page_id, limit + 1) <= default_window),
                    "lowValueFilteredForPrerequisite": candidate["filteredForPrerequisite"],
                }
            )

        return {
            "seedSlugs": list(seed_slug_set),
            "queryTerms": query_terms,
            "defaultWindow": default_window,
            "candidates": formatted[:limit],
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

    def _one_hop_qualified(self, cur, seed_ids: list[str], min_shared_tags: int) -> list[tuple]:
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
        return self._qualified_from_edge_rows(
            cur.fetchall(),
            min_shared_tags=min_shared_tags,
            excluded_ids=set(seed_ids),
            decay=1.0,
        )

    def _two_hop_qualified(
        self,
        cur,
        one_hop_ids: list[str],
        *,
        excluded_ids: set[str],
        min_shared_tags: int,
    ) -> dict[str, dict]:
        if not one_hop_ids:
            return {}
        cur.execute(
            """
            WITH edge_counts AS (
                SELECT
                    CASE
                        WHEN l.from_page_id::text = ANY(%s) THEN l.from_page_id::text
                        ELSE l.to_page_id::text
                    END AS source_id,
                    CASE
                        WHEN l.from_page_id::text = ANY(%s) THEN l.to_page_id::text
                        ELSE l.from_page_id::text
                    END AS neighbor_id,
                    SUM(CASE WHEN l.relation_type = 'WIKILINK' THEN 1 ELSE 0 END) AS wikilink_count,
                    SUM(CASE WHEN l.relation_type = 'SHARED_TAG' THEN 1 ELSE 0 END) AS shared_tag_count
                FROM rag.wiki_link l
                WHERE l.from_page_id::text = ANY(%s)
                   OR l.to_page_id::text = ANY(%s)
                GROUP BY source_id, neighbor_id
            ),
            ranked AS (
                SELECT *,
                       ROW_NUMBER() OVER (
                           PARTITION BY source_id
                           ORDER BY (wikilink_count * 2 + shared_tag_count) DESC, neighbor_id DESC
                       ) AS source_rank
                FROM edge_counts
                WHERE neighbor_id != source_id
                  AND neighbor_id != ALL(%s)
                  AND (wikilink_count > 0 OR shared_tag_count >= %s)
            )
            SELECT source_id, neighbor_id, wikilink_count, shared_tag_count
            FROM ranked
            WHERE source_rank <= %s
            ORDER BY source_id, source_rank
            """,
            (
                one_hop_ids,
                one_hop_ids,
                one_hop_ids,
                one_hop_ids,
                sorted(excluded_ids),
                min_shared_tags,
                TWO_HOP_PER_NODE_LIMIT,
            ),
        )

        per_source_scores = defaultdict(lambda: defaultdict(lambda: {"WIKILINK": 0, "SHARED_TAG": 0}))
        for source_id, neighbor_id, wikilink_count, shared_tag_count in cur.fetchall():
            source_key = str(source_id)
            neighbor_key = str(neighbor_id)
            if neighbor_key in excluded_ids or neighbor_key == source_key:
                continue
            per_source_scores[source_key][neighbor_key]["WIKILINK"] = int(wikilink_count)
            per_source_scores[source_key][neighbor_key]["SHARED_TAG"] = int(shared_tag_count)

        selected: dict[str, dict] = {}
        for source_id in one_hop_ids:
            ranked = []
            for neighbor_id, scores in per_source_scores.get(str(source_id), {}).items():
                if scores["WIKILINK"] > 0 or scores["SHARED_TAG"] >= min_shared_tags:
                    base = scores["WIKILINK"] * 2 + scores["SHARED_TAG"]
                    ranked.append((neighbor_id, base, scores["WIKILINK"], scores["SHARED_TAG"]))
            ranked.sort(key=lambda item: item[1], reverse=True)
            for neighbor_id, base, wikilink, shared_tag in ranked[:TWO_HOP_PER_NODE_LIMIT]:
                decayed_base = round(float(base) * TWO_HOP_DECAY, 4)
                current = selected.get(neighbor_id)
                if current is None or decayed_base > float(current["base"]):
                    selected[neighbor_id] = {
                        "base": decayed_base,
                        "wikilink": wikilink,
                        "sharedTag": shared_tag,
                        "sourceId": str(source_id),
                    }
            if len(selected) >= MAX_TWO_HOP_CANDIDATES:
                break

        ranked_selected = sorted(
            selected.items(),
            key=lambda item: (float(item[1]["base"]), item[0]),
            reverse=True,
        )
        return dict(ranked_selected[:MAX_TWO_HOP_CANDIDATES])

    def _qualified_from_edge_rows(
        self,
        edge_rows: list[tuple],
        *,
        min_shared_tags: int,
        excluded_ids: set[str],
        decay: float,
    ) -> list[tuple]:
        neighbor_scores = defaultdict(lambda: {"WIKILINK": 0, "SHARED_TAG": 0})
        for neighbor_id, relation_type, strength in edge_rows:
            neighbor_key = str(neighbor_id)
            if neighbor_key in excluded_ids:
                continue
            neighbor_scores[neighbor_key][relation_type] = int(strength)

        qualified = []
        for neighbor_id, scores in neighbor_scores.items():
            if scores["WIKILINK"] > 0 or scores["SHARED_TAG"] >= min_shared_tags:
                base_score = (scores["WIKILINK"] * 2 + scores["SHARED_TAG"]) * decay
                qualified.append((neighbor_id, round(float(base_score), 4), scores["WIKILINK"], scores["SHARED_TAG"]))
        qualified.sort(key=lambda item: item[1], reverse=True)
        return qualified

    def _one_hop_window(self, top_n: int) -> int:
        return min(max(top_n * 3, MIN_ONE_HOP_WINDOW), MAX_ONE_HOP_WINDOW)

    def _score_parts_by_id(self, qualified: list[tuple]) -> dict[str, dict]:
        return {
            str(neighbor_id): {"base": base_score, "wikilink": wikilink, "sharedTag": shared_tag}
            for neighbor_id, base_score, wikilink, shared_tag in qualified
        }

    def _load_page_rows(self, cur, candidate_ids: list[str]) -> list[tuple]:
        if not candidate_ids:
            return []
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
        return cur.fetchall()

    def _score_page_rows(
        self,
        page_rows: list[tuple],
        score_parts_by_id: dict[str, dict],
        *,
        query_terms: list[str],
        seed_communities: set[int],
        seed_slug_set: set[str],
        hop: int,
        source: str,
        prerequisite_intent: bool,
        skip_low_value: bool,
    ) -> list[dict]:
        candidates = []
        for row in page_rows:
            page_id, slug, title, community_id, pagerank_score, aliases_text, tags_text = row
            if slug in seed_slug_set:
                continue
            low_value = self._is_low_value_resource(slug, title)
            if skip_low_value and low_value:
                continue
            score_parts = score_parts_by_id.get(
                str(page_id),
                {"base": 0.0, "wikilink": 0, "sharedTag": 0, "sourceId": None},
            )
            query_bonus = self._query_bonus(query_terms, slug, title, aliases_text, tags_text)
            community_bonus = 0.75 if int(community_id) in seed_communities else 0.0
            pagerank_bonus = float(pagerank_score) * 0.5
            candidates.append(
                {
                    "id": str(page_id),
                    "slug": slug,
                    "title": title,
                    "score": round(
                        float(score_parts["base"]) + query_bonus + community_bonus + pagerank_bonus,
                        4,
                    ),
                    "base": score_parts["base"],
                    "wikilink": score_parts["wikilink"],
                    "sharedTag": score_parts["sharedTag"],
                    "queryBonus": round(query_bonus, 4),
                    "communityBonus": round(community_bonus, 4),
                    "pagerankBonus": round(pagerank_bonus, 4),
                    "communityId": int(community_id),
                    "pagerank": float(pagerank_score),
                    "hop": hop,
                    "source": source,
                    "sourceId": score_parts.get("sourceId"),
                    "lowValue": low_value,
                    "filteredForPrerequisite": bool(prerequisite_intent and low_value),
                }
            )
        return candidates

    def _dedupe_candidates(self, candidates: list[dict]) -> list[dict]:
        by_slug: dict[str, dict] = {}
        for candidate in candidates:
            slug = str(candidate["slug"])
            current = by_slug.get(slug)
            if current is None or self._candidate_sort_key(candidate) > self._candidate_sort_key(current):
                by_slug[slug] = candidate
        return list(by_slug.values())

    def _rank_candidates(self, candidates: list[dict]) -> list[dict]:
        return sorted(candidates, key=self._candidate_sort_key, reverse=True)

    def _candidate_sort_key(self, candidate: dict) -> tuple:
        return (
            float(candidate["score"]),
            int(candidate["communityId"]),
            float(candidate["pagerank"]),
            -int(candidate.get("hop", 1)),
            -len(str(candidate["slug"])),
            str(candidate["slug"]),
        )

    def _candidate_tuple(self, candidate: dict, *, include_source: bool) -> tuple:
        item = (candidate["slug"], candidate["title"], candidate["score"])
        if include_source:
            return (*item, candidate["source"])
        return item

    def _normalize_graph_intent(self, graph_intent: str | None) -> str:
        return str(graph_intent or "").strip().upper()

    def _is_prerequisite_path(self, graph_intent: str | None) -> bool:
        return self._normalize_graph_intent(graph_intent) == PREREQUISITE_PATH_INTENT

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
            quoted_chunks.extend(
                re.findall(r"[\u300c\u300e\u201c\"]([^\u300d\u300f\u201d\"]+)[\u300d\u300f\u201d\"]", query)
            )
            for chunk in quoted_chunks:
                terms.append(self._normalize_text(chunk))
                terms.extend(
                    self._normalize_text(part)
                    for part in re.split(r"[\u3001,，;；/()（）\s]+", chunk)
                    if self._normalize_text(part)
                )
                terms.extend(
                    self._normalize_text(part)
                    for part in re.split(r"[、,，/（）()\\s]+", chunk)
                    if self._normalize_text(part)
                )

        seen: set[str] = set()
        result: list[str] = []
        for term in terms:
            normalized = self._normalize_text(term)
            if (
                not normalized
                or normalized in seen
                or (len(normalized) < 2 and not re.fullmatch(r"[\u4e00-\u9fff]", normalized))
            ):
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
        slug_tail_norm = self._normalize_text(str(slug or "").rsplit("/", 1)[-1])
        title_norm = self._normalize_text(title)
        alias_norms = {self._normalize_text(alias) for alias in self._parse_json_list(aliases_text)}
        tag_norms = {self._normalize_text(tag) for tag in self._parse_json_list(tags_text)}
        generic_terms = {"alias", "doc", "docs", "path", "query", "seed"}
        for term in query_terms:
            if term in generic_terms:
                continue
            if term == slug_tail_norm or term == title_norm or term in alias_norms:
                return True
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
        slug_tail_norm = self._normalize_text(str(slug or "").rsplit("/", 1)[-1])
        title_norm = self._normalize_text(title)
        alias_norms = {self._normalize_text(alias) for alias in self._parse_json_list(aliases_text)}
        tag_norms = {self._normalize_text(tag) for tag in self._parse_json_list(tags_text)}
        bonus = 0.0
        for term in query_terms:
            if not term:
                continue
            if term == slug_norm or term == slug_tail_norm or term == title_norm:
                bonus += 3.0
                continue
            if len(term) < 2:
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
        title = str(title or "")
        if not lowered_slug or lowered_slug == "none":
            return True
        return (
            lowered_slug.startswith("http")
            or lowered_slug.startswith("wiki://")
            or "视频资源" in lowered_slug
            or "视频资源" in title
            or "视频资源" in lowered_title
            or "视频" in lowered_title
            or "video" in lowered_slug
        )
