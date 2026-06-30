"""
Grep (keyword) search channel with phrase-first matching + token-level fallback.
"""
import json
import re

from retrieval.fmm_tokenizer import FMMTokenizer


class GrepSearcher:
    """Phrase-first: complete query as contiguous substring → priority.
    Token-level ILIKE matching only as fallback → normal."""

    _SYNONYM_SCORE_FACTOR = 0.92
    _SYNONYM_IDF_FALLBACK = 0.8

    def __init__(self, tokenizer: FMMTokenizer):
        self.tokenizer = tokenizer

    @staticmethod
    def _normalize_phrase(text: str) -> str:
        return re.sub(r"\s+", "", text.strip().lower())

    def _compute_phrase_priority(
        self,
        *,
        title: str,
        query: str,
        body_match: bool,
    ) -> tuple[int, float]:
        """Score phrase matches so exact/near-exact title hits rank before body hits."""

        normalized_title = self._normalize_phrase(title)
        normalized_query = self._normalize_phrase(query)

        if normalized_title == normalized_query:
            return (400, 1.0)
        if normalized_title.startswith(normalized_query):
            return (350, 0.98)
        if normalized_query in normalized_title:
            return (300, 0.95)
        if body_match:
            return (200, 0.9)
        return (100, 0.85)

    _STOPWORDS = frozenset(
        "什么 是 的 和 与 如何 怎样 怎么 了 吗 呢 啊 请问 请".split()
    )
    _PUNCT_RE = re.compile(r"[，。？！、；：“”‘’（）\[\]【】\s]+")
    _FOCUS_QUOTE_RE = re.compile(r"[「『《“\"']([^」』》”\"']{1,120})[」』》”\"']")
    _MAX_FULL_PHRASE_QUERY_CHARS = 96

    def search(self, cur, query: str, domain: str = "COMPUTER_SCIENCE",
               coverage_min: float = 0.0) -> dict:
        """
        Returns:
          - priority: [(slug, title, coverage, tokens_matched), ...] — phrase matches only
          - normal:   [(slug, title, coverage, tokens_matched), ...] — token-level fallback
        """
        query_lower = query.lower()

        # Phase 1: plain concept explanations can focus on the quoted topic;
        # graph/relation questions need full-query term recall for all evidence nodes.
        priority = self._focused_phrase_search(cur, query, domain) if self._should_focus_topic_phrase(query) else []
        if not priority and self._should_search_full_phrase(query):
            priority = self._phrase_search(cur, query, query_lower, domain)

        # Phase 1.5: FMM-term sub-phrase search — each recognized term as a phrase
        if not priority:
            priority = self._term_phrase_search(cur, query_lower, domain)

        # Phase 2: Token-level fallback — only when phrase search finds nothing
        normal = []
        if not priority:
            normal = self._token_search(cur, query_lower, domain, coverage_min)

        return {"priority": priority, "normal": normal}

    def _extract_focus_phrases(self, query: str) -> list[str]:
        phrases: list[str] = []
        seen: set[str] = set()
        for match in self._FOCUS_QUOTE_RE.finditer(query):
            phrase = match.group(1).strip().strip("\"'“”「」『』《》")
            normalized = self._normalize_phrase(phrase)
            if not self._is_focus_phrase_candidate(normalized) or normalized in seen:
                continue
            seen.add(normalized)
            phrases.append(phrase)
        return phrases

    def _is_focus_phrase_candidate(self, normalized: str) -> bool:
        if len(normalized) >= 2:
            return True
        return bool(re.search(r"[\u4e00-\u9fff]", normalized))

    def _should_focus_topic_phrase(self, query: str) -> bool:
        compact = self._normalize_phrase(query)
        if "核心概念" not in compact:
            return False
        if not any(signal in compact for signal in ("典型场景", "参考方向", "常见误区")):
            return False
        relation_signals = (
            "知识图谱",
            "关系角度",
            "关系焦点",
            "多跳",
            "学习路径",
            "前置",
            "依赖",
            "通向",
            "连接",
            "联系",
        )
        return not any(signal in compact for signal in relation_signals)

    def _should_search_full_phrase(self, query: str) -> bool:
        compact = self._normalize_phrase(query)
        if len(compact) <= self._MAX_FULL_PHRASE_QUERY_CHARS:
            return True
        instruction_signals = (
            "请",
            "回答时",
            "说明",
            "解释",
            "比较",
            "构建",
            "覆盖",
            "关系",
            "路径",
            "联系",
            "please",
            "explain",
            "compare",
            "relationship",
            "cover",
        )
        return not any(signal in compact for signal in instruction_signals)

    def _focused_phrase_search(self, cur, query: str, domain: str) -> list:
        focused: list = []
        seen_slugs: set[str] = set()
        for phrase in self._extract_focus_phrases(query):
            for item in self._phrase_search(cur, phrase, phrase.lower(), domain):
                slug = str(item[0])
                if slug in seen_slugs:
                    continue
                seen_slugs.add(slug)
                focused.append(item)
        return focused

    def _decode_json_list(self, value) -> list[str]:
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

    def _lookup_synonym_terms(self, cur, query_lower: str, domain: str) -> list[tuple[str, float]]:
        cleaned = self._clean_query(query_lower)
        candidates = {
            self._normalize_phrase(query_lower),
            self._normalize_phrase(cleaned),
        }
        if cleaned:
            tokens = self.tokenizer.tokenize(cleaned)
            candidates.update(
                self._normalize_phrase(token.text)
                for token in tokens
                if token.term_type != "CHAR" and len(token.text) >= 2
            )
        candidates = {term for term in candidates if len(term) >= 2}
        if not candidates:
            return []

        cur.execute("""
            SELECT canonical_term, variants
            FROM rag.synonym_group
            WHERE is_active = true AND domain = %s
        """, (domain,))
        expanded: dict[str, float] = {}
        for canonical_term, variants in cur.fetchall():
            group_terms = [canonical_term, *self._decode_json_list(variants)]
            normalized_group = {
                self._normalize_phrase(term): term
                for term in group_terms
                if self._normalize_phrase(term)
            }
            if not (set(normalized_group.keys()) & candidates):
                continue
            for normalized, term in normalized_group.items():
                if normalized in candidates or len(normalized) < 2:
                    continue
                expanded[term] = self._SYNONYM_SCORE_FACTOR
        return sorted(
            expanded.items(),
            key=lambda item: len(self._normalize_phrase(item[0])),
            reverse=True,
        )

    def _term_phrase_search(
        self,
        cur,
        query_lower: str,
        domain: str,
        extra_terms: list[tuple[str, float]] | None = None,
    ) -> list:
        """Search for FMM-recognized terms as individual phrases in titles/content."""
        cleaned = self._clean_query(query_lower)
        if not cleaned:
            terms = []
        else:
            tokens = self.tokenizer.tokenize(cleaned)
            terms = self._dedupe_terms_by_normalized_phrase([
                {
                    "text": token.text,
                    "idf": token.idf,
                    "score_factor": 1.0,
                }
                for token in tokens
                if token.term_type != "CHAR" and len(token.text) >= 2
            ])

        seen_terms = {self._normalize_phrase(term["text"]) for term in terms}
        for text, score_factor in extra_terms or []:
            normalized = self._normalize_phrase(text)
            if normalized in seen_terms or len(normalized) < 2:
                continue
            terms.append(
                {
                    "text": text,
                    "idf": self.tokenizer.get_idf(normalized) or self._SYNONYM_IDF_FALLBACK,
                    "score_factor": score_factor,
                }
            )
            seen_terms.add(normalized)

        if not terms:
            return []

        # Batch the title/content scans to avoid two DB round trips per term.
        phrase_results: dict[str, dict] = {}
        title_rows = self._batch_term_phrase_rows(cur, terms, domain, content=False)
        content_rows = self._batch_term_phrase_rows(cur, terms, domain, content=True)
        title_rows_by_term = self._rows_by_term_index(title_rows)
        content_rows_by_term = self._rows_by_term_index(content_rows)

        for term_index, term in enumerate(terms):
            term_text = term["text"]
            score_factor = term["score_factor"]
            for slug, title in title_rows_by_term.get(term_index, []):
                priority_score, coverage = self._compute_phrase_priority(
                    title=title, query=term_text, body_match=False,
                )
                # Boost by IDF: more specific terms get higher priority
                boosted_score = (priority_score + term["idf"] * 10) * score_factor
                current = phrase_results.get(slug)
                if current is None or boosted_score > current["priority_score"]:
                    phrase_results[slug] = {
                        "title": title,
                        "coverage": round(coverage * score_factor, 4),
                        "priority_score": boosted_score,
                        "tokens": [term_text],
                    }

            for slug, title in content_rows_by_term.get(term_index, []):
                if slug in phrase_results:
                    continue  # title match already exists, skip lower-priority content match
                priority_score, coverage = self._compute_phrase_priority(
                    title=title, query=term_text, body_match=True,
                )
                boosted_score = (priority_score + term["idf"] * 5) * score_factor
                phrase_results[slug] = {
                    "title": title,
                    "coverage": round(coverage * score_factor, 4),
                    "priority_score": boosted_score,
                    "tokens": [term_text],
                }

        ranked = sorted(
            phrase_results.items(),
            key=lambda item: (item[1]["priority_score"], item[1]["coverage"]),
            reverse=True,
        )
        return [
            (slug, info["title"], info["coverage"], info["tokens"])
            for slug, info in ranked
        ]

    def _batch_term_phrase_rows(
        self,
        cur,
        terms: list[dict],
        domain: str,
        *,
        content: bool,
    ) -> list[tuple[int, str, str]]:
        if not terms:
            return []

        if content:
            branch_sql = """
                SELECT DISTINCT %s::integer AS term_index, kd.source_ref AS slug, kd.title
                FROM rag.knowledge_chunk kc
                JOIN rag.knowledge_document kd ON kd.id = kc.document_id
                WHERE kc.content ILIKE %s AND kd.domain = %s
            """
        else:
            branch_sql = """
                SELECT %s::integer AS term_index, kd.source_ref AS slug, kd.title
                FROM rag.knowledge_document kd
                WHERE kd.title ILIKE %s AND kd.domain = %s
            """

        sql = "\nUNION ALL\n".join(branch_sql for _ in terms)
        params: list[object] = []
        for index, term in enumerate(terms):
            pattern = f"%{term['text']}%"
            params.extend([index, pattern, domain])
        cur.execute(sql, tuple(params))
        return cur.fetchall()

    def _rows_by_term_index(self, rows: list[tuple]) -> dict[int, list[tuple[str, str]]]:
        grouped: dict[int, list[tuple[str, str]]] = {}
        for row in rows:
            if len(row) < 3:
                continue
            term_index, slug, title = row[:3]
            try:
                index = int(term_index)
            except (TypeError, ValueError):
                continue
            grouped.setdefault(index, []).append((slug, title))
        return grouped

    def _clean_query(self, text: str) -> str:
        """Remove stopwords and punctuation, keep meaningful terms."""
        segments = self._PUNCT_RE.split(text)
        kept: list[str] = []
        for seg in segments:
            # Remove stopwords from within each segment
            cleaned_seg = seg
            for sw in self._STOPWORDS:
                cleaned_seg = cleaned_seg.replace(sw, "")
            if len(cleaned_seg) >= 2:
                kept.append(cleaned_seg)
        return "".join(kept)

    def _phrase_search(self, cur, query: str, query_lower: str, domain: str) -> list:
        """Search for the complete query as a contiguous phrase in content and titles."""
        phrase_results: dict[str, dict] = {}

        # Search in chunk content (coverage=1.0 for full phrase match in body)
        cur.execute("""
            SELECT DISTINCT kd.source_ref AS slug, kd.title
            FROM rag.knowledge_chunk kc
            JOIN rag.knowledge_document kd ON kd.id = kc.document_id
            WHERE kc.content ILIKE %s AND kd.domain = %s
        """, (f"%{query}%", domain))
        for slug, title in cur.fetchall():
            priority_score, coverage = self._compute_phrase_priority(
                title=title,
                query=query,
                body_match=True,
            )
            phrase_results[slug] = {
                "title": title,
                "coverage": coverage,
                "priority_score": priority_score,
                "tokens": [query],
            }

        # Search in document titles (coverage=0.9 for title-only match)
        cur.execute("""
            SELECT source_ref AS slug, title
            FROM rag.knowledge_document
            WHERE title ILIKE %s AND domain = %s
        """, (f"%{query}%", domain))
        for slug, title in cur.fetchall():
            priority_score, coverage = self._compute_phrase_priority(
                title=title,
                query=query,
                body_match=False,
            )
            current = phrase_results.get(slug)
            if current is None or priority_score > current["priority_score"]:
                phrase_results[slug] = {
                    "title": title,
                    "coverage": coverage,
                    "priority_score": priority_score,
                    "tokens": [query],
                }

        ranked = sorted(
            phrase_results.items(),
            key=lambda item: (
                item[1]["priority_score"],
                item[1]["coverage"],
                -len(item[1]["title"]),
            ),
            reverse=True,
        )
        return [
            (slug, info["title"], info["coverage"], info["tokens"])
            for slug, info in ranked
        ]

    def _token_search(
        self,
        cur,
        query_lower: str,
        domain: str,
        coverage_min: float,
        extra_terms: list[tuple[str, float]] | None = None,
    ) -> list:
        """Fallback: token-level ILIKE with IDF-weighted coverage scoring."""
        tokens = self.tokenizer.tokenize(query_lower)
        if not tokens:
            return []

        known = [
            {
                "text": token.text,
                "idf": token.idf,
                "score_factor": 1.0,
            }
            for token in tokens
            if token.term_type != "CHAR"
        ]
        if not known:
            known = [
                {
                    "text": token.text,
                    "idf": token.idf,
                    "score_factor": 1.0,
                }
                for token in tokens
            ]
        known = self._dedupe_terms_by_normalized_phrase(known)

        seen_terms = {self._normalize_phrase(token["text"]) for token in known}
        for text, score_factor in extra_terms or []:
            normalized = self._normalize_phrase(text)
            if normalized in seen_terms or len(normalized) < 2:
                continue
            known.append(
                {
                    "text": text,
                    "idf": (self.tokenizer.get_idf(normalized) or self._SYNONYM_IDF_FALLBACK) * score_factor,
                    "score_factor": score_factor,
                }
            )
            seen_terms.add(normalized)

        total_idf = sum(token["idf"] for token in known)
        if total_idf == 0:
            return []

        matched_docs: dict[str, dict] = {}
        for token in known:
            cur.execute("""
                SELECT DISTINCT kd.source_ref AS slug, kd.title
                FROM rag.knowledge_chunk kc
                JOIN rag.knowledge_document kd ON kd.id = kc.document_id
                WHERE kc.content ILIKE %s AND kd.domain = %s
            """, (f"%{token['text']}%", domain))
            for slug, title in cur.fetchall():
                if slug not in matched_docs:
                    matched_docs[slug] = {"title": title, "tokens_matched": set(), "idf_sum": 0.0}
                matched_docs[slug]["tokens_matched"].add(token["text"])
                matched_docs[slug]["idf_sum"] += token["idf"]

        results = []
        for slug, doc in matched_docs.items():
            coverage = doc["idf_sum"] / total_idf if total_idf > 0 else 0
            if coverage < coverage_min:
                continue
            tokens_matched = sorted(doc["tokens_matched"])
            results.append((slug, doc["title"], round(coverage, 4), tokens_matched))

        results.sort(key=lambda x: x[2], reverse=True)
        return results

    def _dedupe_terms_by_normalized_phrase(self, terms: list[dict]) -> list[dict]:
        deduped: dict[str, dict] = {}
        for term in terms:
            normalized = self._normalize_phrase(term.get("text", ""))
            if not normalized:
                continue
            current = deduped.get(normalized)
            if current is None or (
                float(term.get("idf") or 0.0),
                float(term.get("score_factor") or 0.0),
                len(str(term.get("text") or "")),
            ) > (
                float(current.get("idf") or 0.0),
                float(current.get("score_factor") or 0.0),
                len(str(current.get("text") or "")),
            ):
                deduped[normalized] = term
        return list(deduped.values())
