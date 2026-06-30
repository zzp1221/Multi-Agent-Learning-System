"""
Hybrid Retriever: orchestrates grep + vector + graph channels with RRF fusion.
"""
import logging
import os
import re
import time
from datetime import datetime
import psycopg2
from retrieval.fmm_tokenizer import FMMTokenizer
from retrieval.grep_searcher import GrepSearcher
from retrieval.vector_searcher import VectorSearcher
from retrieval.graph_expander import GraphExpander
from retrieval.rrf_fusion import RRFFusion
from retrieval.slug_canonicalizer import safe_slug_key
from retrieval.tavily_searcher import TavilySearcher
from retrieval.wiki_tools import graph_intent_allows_wiki_tools
from retrieval.source_quality import low_value_penalty
from src.ai_modules.config import get_settings

LOGGER = logging.getLogger(__name__)

GRAPH_AWARE_INTENTS = {
    "COMMON_MISTAKE",
    "COMMUNITY_SUMMARY",
    "COMPARISON",
    "CROSS_LAYER_RELATION",
    "MECHANISM_APPLICATION",
    "MULTI_HOP_RELATION",
    "PREREQUISITE_PATH",
}
PREREQUISITE_PATH_INTENT = "PREREQUISITE_PATH"
GRAPH_EVIDENCE_FILL_INTENTS = {PREREQUISITE_PATH_INTENT}
DEFAULT_GRAPH_WEIGHT = 0.5
GRAPH_WEIGHT_BY_INTENT = {
    "COMPARISON": 1.2,
    "CROSS_LAYER_RELATION": 1.4,
    "MECHANISM_APPLICATION": 1.4,
    "COMMON_MISTAKE": 1.3,
    "COMMUNITY_SUMMARY": 1.5,
    "MULTI_HOP_RELATION": 1.6,
    PREREQUISITE_PATH_INTENT: 1.8,
}


class HybridRetriever:
    """3-channel hybrid retrieval with weighted RRF fusion."""

    def __init__(self, db_config: dict, domain: str = "COMPUTER_SCIENCE",
                 top_k: int = 10, graph_seed_n: int = 3):
        self.db_config = db_config
        self.domain = domain
        self.top_k = top_k
        self.graph_seed_n = graph_seed_n

        # Lazy init
        self._tokenizer: FMMTokenizer = None
        self._grep: GrepSearcher = None
        self._vector: VectorSearcher = None
        self._graph: GraphExpander = None
        self._web: TavilySearcher = None
        self._rrf: RRFFusion = None
        self._initialized = False

    def _init(self, cur):
        if self._initialized:
            return
        settings = get_settings()
        embedding_api_key = self._embedding_api_key(settings)
        if embedding_api_key:
            os.environ["DASHSCOPE_API_KEY"] = embedding_api_key
        self._web = TavilySearcher()
        self._rrf = RRFFusion()
        self._tokenizer = FMMTokenizer()
        n = self._tokenizer.load_from_db(cur, self.domain)
        self._grep = GrepSearcher(self._tokenizer)
        self._vector = VectorSearcher(
            dimension=settings.knowledge_embedding_dimension,
            model=settings.knowledge_embedding_model_name,
        )
        self._graph = GraphExpander()
        self._initialized = True
        print(f"  [HybridRetriever] Loaded {n} terms from term_lexicon")

    def initialize(self, cur):
        self._init(cur)

    def _add_timing(self, timings: dict | None, channel_name: str, started: float) -> float:
        elapsed_ms = (time.perf_counter() - started) * 1000
        if timings is not None:
            timings[channel_name] = timings.get(channel_name, 0.0) + elapsed_ms
        return elapsed_ms

    def _embedding_api_key(self, settings) -> str:
        explicit_key = str(getattr(settings, "effective_embedding_api_key", "") or "").strip()
        if explicit_key:
            return explicit_key
        provider_api_key = getattr(settings, "provider_api_key", None)
        if callable(provider_api_key):
            return str(provider_api_key() or "").strip()
        return str(getattr(settings, "openai_compatible_api_key", "") or "").strip()

    def retrieve(
        self,
        cur,
        query: str,
        web_search_enabled: bool = False,
        web_search_query: str | None = None,
        graph_intent: str | None = None,
        timings: dict | None = None,
        include_diagnostics: bool = False,
    ) -> dict:
        """Run all 3 channels and fuse. Returns structured results."""
        effective_web_query = self._web_query(web_search_query or query)
        channel_errors: dict[str, str] = {}
        try:
            started = time.perf_counter()
            self.initialize(cur)
        except Exception as exc:
            LOGGER.warning("Local hybrid retrieval init failed for query %r: %s", query, exc)
            self._add_timing(timings, "init_ms", started)
            channel_errors["init"] = f"{type(exc).__name__}: {exc}"
            started = time.perf_counter()
            web_results = TavilySearcher().search(effective_web_query, top_k=5) if web_search_enabled else []
            self._add_timing(timings, "web_ms", started)
            started = time.perf_counter()
            fused = RRFFusion().fuse(
                grep_results={"priority": [], "normal": []},
                vector_results=[],
                graph_results=[],
                web_results=web_results,
                graph_weight=self._graph_weight(graph_intent),
                slug_penalty=self._graph_slug_penalty if self._is_graph_aware_intent(graph_intent) else None,
                slug_key=safe_slug_key,
            )
            self._add_timing(timings, "fusion_ms", started)
            return {
                "query": query,
                "graphIntent": graph_intent,
                "webSearchEnabled": web_search_enabled,
                "webSearchQuery": effective_web_query,
                "channels": {
                    "grep": {"priority": [], "normal_count": 0},
                    "vector": [],
                    "graph": [],
                    "web": web_results,
                },
                "channelErrors": channel_errors,
                "fused": fused,
                "top": fused[:5],
            }
        else:
            self._add_timing(timings, "init_ms", started)

        # Channel A: Grep (keyword + coverage)
        try:
            started = time.perf_counter()
            grep_results = self._grep.search(cur, query, self.domain)
        except Exception as exc:
            LOGGER.warning("Grep retrieval failed for query %r: %s", query, exc)
            channel_errors["grep"] = f"{type(exc).__name__}: {exc}"
            grep_results = {"priority": [], "normal": []}
        finally:
            self._add_timing(timings, "grep_ms", started)

        # Channel B: Vector (semantic) — search both knowledge + resource
        try:
            started = time.perf_counter()
            vector_all = self._vector.search_all(cur, query, top_k=self.top_k, domain=self.domain)
        except Exception as exc:
            LOGGER.warning("Vector retrieval failed for query %r: %s", query, exc)
            channel_errors["vector"] = f"{type(exc).__name__}: {exc}"
            vector_all = []
        finally:
            self._add_timing(timings, "vector_ms", started)
        # Strip source tag for RRF: [(slug, title, similarity), ...]
        vector_results = [(r[0], r[1], r[2]) for r in vector_all]

        # Channel C: Graph expansion from top seeds
        seed_slugs = self._graph_seed_slugs(grep_results, vector_results, graph_intent)
        prerequisite_evidence = self._empty_prerequisite_evidence()
        try:
            graph_started = time.perf_counter()
            graph_results = self._graph.expand(
                cur,
                seed_slugs,
                top_n=self._graph_top_n(graph_intent),
                query=query,
                graph_intent=graph_intent,
            )
            if self._uses_prerequisite_evidence_fill(graph_intent, query) and hasattr(self._graph, "build_prerequisite_evidence"):
                prerequisite_evidence = self._graph.build_prerequisite_evidence(cur, seed_slugs, query)
                graph_results = self._merge_graph_evidence(
                    prerequisite_evidence["directEvidence"],
                    graph_results,
                    prerequisite_evidence["protectedSeeds"],
                )
            graph_read_ms = round(self._add_timing(timings, "graph_ms", graph_started), 2)
        except Exception as exc:
            LOGGER.warning("Graph retrieval failed for query %r: %s", query, exc)
            self._add_timing(timings, "graph_ms", graph_started)
            channel_errors["graph"] = f"{type(exc).__name__}: {exc}"
            graph_results = []
            graph_read_ms = 0.0

        # Channel D: Web search is strictly opt-in per user turn.
        started = time.perf_counter()
        web_results = self._web.search(effective_web_query, top_k=5) if web_search_enabled else []
        self._add_timing(timings, "web_ms", started)

        # RRF Fusion
        started = time.perf_counter()
        pre_fused = self._rrf.fuse(
            grep_results,
            vector_results,
            graph_results,
            web_results,
            graph_weight=self._graph_weight(graph_intent),
            slug_penalty=self._graph_slug_penalty if self._is_graph_aware_intent(graph_intent) else None,
            slug_key=safe_slug_key,
        )
        fused = pre_fused
        fused, graph_diagnostics = self._stabilize_graph_top5_with_diagnostics(
            fused,
            graph_results,
            graph_intent,
            protected_slugs={
                item[0]
                for item in prerequisite_evidence["protectedSeeds"] + prerequisite_evidence["directEvidence"]
            },
        )
        fused, grep_top3_diagnostics = self._protect_strong_grep_top3_with_diagnostics(
            fused,
            grep_results,
            graph_intent,
        )
        fused, grep_top_diagnostics = self._promote_strong_grep_top_with_diagnostics(
            fused,
            grep_results,
            graph_intent,
        )
        fused, grep_evidence_diagnostics = self._protect_strong_grep_evidence_with_diagnostics(
            fused,
            grep_results,
            graph_intent,
        )
        fused, query_object_diagnostics = self._balance_query_object_top3_with_diagnostics(
            fused,
            grep_results,
            vector_results,
            graph_results,
            query,
            graph_intent,
        )
        fused, primary_query_object_diagnostics = self._promote_primary_query_object_top1_with_diagnostics(
            fused,
            query,
            graph_intent,
        )
        fused, explicit_graph_evidence_diagnostics = self._protect_explicit_graph_evidence_with_diagnostics(
            fused,
            graph_results,
            query,
            graph_intent,
        )
        self._add_timing(timings, "fusion_ms", started)

        result = {
            "query": query,
            "graphIntent": graph_intent,
            "graphDiagnostics": {
                "prerequisiteEvidence": self._format_prerequisite_evidence(prerequisite_evidence),
                "top5Stabilization": graph_diagnostics,
                "strongGrepTop3": grep_top3_diagnostics,
                "strongGrepTop": grep_top_diagnostics,
                "strongGrepEvidence": grep_evidence_diagnostics,
                "queryObjectTop3": query_object_diagnostics,
                "primaryQueryObjectTop1": primary_query_object_diagnostics,
                "explicitGraphEvidence": explicit_graph_evidence_diagnostics,
                "wikiTraversal": {
                    "enabled": graph_intent_allows_wiki_tools(graph_intent),
                    "wiki_neighbors_ms": graph_read_ms,
                },
            },
            "webSearchEnabled": web_search_enabled,
            "webSearchQuery": effective_web_query,
            "channelErrors": channel_errors,
            "channels": {
                "grep": {
                    "priority": grep_results.get("priority", []),
                    "normal_count": len(grep_results.get("normal", [])),
                },
                "vector": [(r[0], r[1], r[2], r[3]) for r in vector_all[:5]],
                "graph": graph_results,
                "web": web_results,
            },
            "fused": fused,
            "top": fused[:5],
        }
        if include_diagnostics:
            graph_explain = {}
            try:
                graph_explain = self._graph.explain_candidates(
                    cur,
                    seed_slugs,
                    limit=50,
                    default_window=self._graph_top_n(graph_intent) * 3,
                    query=query,
                    graph_intent=graph_intent,
                )
            except Exception as exc:
                channel_errors["graphExplain"] = f"{type(exc).__name__}: {exc}"
            result["_retrievalDebug"] = {
                "retrievalGraphIntent": graph_intent,
                "channelErrors": channel_errors,
                "graphSeedSlugs": seed_slugs,
                "grepResults": grep_results,
                "vectorResults": vector_results,
                "graphResults": graph_results,
                "webResults": web_results,
                "preFused": pre_fused,
                "postFused": fused,
                "prerequisiteEvidence": prerequisite_evidence,
                "top5Stabilization": graph_diagnostics,
                "strongGrepTop3": grep_top3_diagnostics,
                "strongGrepTop": grep_top_diagnostics,
                "strongGrepEvidence": grep_evidence_diagnostics,
                "queryObjectTop3": query_object_diagnostics,
                "primaryQueryObjectTop1": primary_query_object_diagnostics,
                "explicitGraphEvidence": explicit_graph_evidence_diagnostics,
                "wikiTraversal": result["graphDiagnostics"]["wikiTraversal"],
                "graphCandidateExplainTop50": graph_explain,
            }
        return result

    def retrieve_grep_first(
        self,
        cur,
        query: str,
        web_search_enabled: bool = False,
        web_search_query: str | None = None,
        graph_intent: str | None = None,
        timings: dict | None = None,
        include_diagnostics: bool = False,
    ) -> dict:
        """Run grep first and skip vector/graph when phrase confidence is strong."""
        effective_web_query = self._web_query(web_search_query or query)
        if self._is_graph_aware_intent(graph_intent):
            raw_result = self.retrieve(
                cur,
                query,
                web_search_enabled=web_search_enabled,
                web_search_query=web_search_query,
                graph_intent=graph_intent,
                timings=timings,
                include_diagnostics=include_diagnostics,
            )
            raw_result["retrievalStrategy"] = "LOCAL_GREP_FIRST"
            raw_result["grepFirstPromoted"] = True
            return raw_result

        try:
            started = time.perf_counter()
            self._init(cur)
        except Exception as exc:
            LOGGER.warning("Grep-first init failed for query %r: %s", query, exc)
            self._add_timing(timings, "init_ms", started)
            return self.retrieve(
                cur,
                query,
                web_search_enabled=web_search_enabled,
                web_search_query=web_search_query,
                graph_intent=graph_intent,
                timings=timings,
                include_diagnostics=include_diagnostics,
            )
        else:
            self._add_timing(timings, "init_ms", started)

        try:
            started = time.perf_counter()
            grep_results = self._grep.search(cur, query, self.domain)
        except Exception as exc:
            LOGGER.warning("Grep-first grep retrieval failed for query %r: %s", query, exc)
            grep_results = {"priority": [], "normal": []}
        finally:
            self._add_timing(timings, "grep_ms", started)

        if not self._has_strong_grep_hit(grep_results):
            raw_result = self.retrieve(
                cur,
                query,
                web_search_enabled=web_search_enabled,
                web_search_query=web_search_query,
                graph_intent=graph_intent,
                timings=timings,
                include_diagnostics=include_diagnostics,
            )
            raw_result["retrievalStrategy"] = "LOCAL_GREP_FIRST"
            raw_result["grepFirstPromoted"] = True
            return raw_result

        started = time.perf_counter()
        web_results = self._web.search(effective_web_query, top_k=5) if web_search_enabled else []
        self._add_timing(timings, "web_ms", started)
        started = time.perf_counter()
        fused = self._rrf.fuse(grep_results, [], [], web_results, slug_key=safe_slug_key)
        self._add_timing(timings, "fusion_ms", started)
        return {
            "query": query,
            "graphIntent": graph_intent,
            "retrievalStrategy": "LOCAL_GREP_FIRST",
            "grepFirstPromoted": False,
            "webSearchEnabled": web_search_enabled,
            "webSearchQuery": effective_web_query,
            "channels": {
                "grep": {
                    "priority": grep_results.get("priority", []),
                    "normal_count": len(grep_results.get("normal", [])),
                },
                "vector": [],
                "graph": [],
                "web": web_results,
            },
            "fused": fused,
            "top": fused[:5],
        }

    def retrieve_flat(self, cur, query: str) -> list[tuple]:
        """Simple flat list of top fused results."""
        return self.retrieve(cur, query)["top"]

    def _has_strong_grep_hit(self, grep_results: dict) -> bool:
        priority = grep_results.get("priority", []) if isinstance(grep_results, dict) else []
        if not priority:
            return False
        top_hit = priority[0]
        if not isinstance(top_hit, (list, tuple)) or len(top_hit) < 3:
            return False
        try:
            return float(top_hit[2]) >= 0.9
        except (TypeError, ValueError):
            return False

    def _is_graph_aware_intent(self, graph_intent: str | None) -> bool:
        return self._normalize_graph_intent(graph_intent) in GRAPH_AWARE_INTENTS

    def _normalize_graph_intent(self, graph_intent: str | None) -> str:
        return str(graph_intent or "").strip().upper()

    def _uses_prerequisite_graph_fill(self, graph_intent: str | None) -> bool:
        return self._normalize_graph_intent(graph_intent) == PREREQUISITE_PATH_INTENT

    def _uses_graph_evidence_fill(self, graph_intent: str | None) -> bool:
        return self._normalize_graph_intent(graph_intent) in GRAPH_EVIDENCE_FILL_INTENTS

    def _uses_prerequisite_evidence_fill(self, graph_intent: str | None, query: str | None) -> bool:
        if not self._uses_graph_evidence_fill(graph_intent):
            return False
        compact_query = re.sub(r"\s+", "", str(query or "").strip().lower())
        if not compact_query:
            return False
        strong_path_signals = (
            "\u6784\u5efa\u4e00\u6761\u5b66\u4e60\u8def\u5f84",  # 构建一条学习路径
            "\u5b66\u4e60\u8def\u5f84",  # 学习路径
            "\u4f9d\u8d56\u6216\u901a\u5411",  # 依赖或通向
            "\u5982\u4f55\u4f9d\u8d56\u6216\u901a\u5411",  # 如何依赖或通向
            "\u524d\u7f6e\u8def\u5f84",  # 前置路径
            "\u5148\u4fee\u8def\u5f84",  # 先修路径
            "learningpath",
            "prerequisitepath",
        )
        return any(signal in compact_query for signal in strong_path_signals)

    def _graph_top_n(self, graph_intent: str | None) -> int:
        if self._uses_graph_evidence_fill(graph_intent):
            return 8
        return 5

    def _graph_weight(self, graph_intent: str | None) -> float:
        return GRAPH_WEIGHT_BY_INTENT.get(self._normalize_graph_intent(graph_intent), DEFAULT_GRAPH_WEIGHT)

    def _graph_seed_slugs(
        self,
        grep_results: dict,
        vector_results: list[tuple],
        graph_intent: str | None,
    ) -> list[str]:
        grep_slugs = [r[0] for r in grep_results.get("priority", [])[: self.graph_seed_n]]
        vec_slugs = [r[0] for r in vector_results[: self.graph_seed_n]]
        raw_slugs = grep_slugs + vec_slugs
        if self._is_graph_aware_intent(graph_intent):
            raw_slugs = [self._canonical_graph_seed_slug(slug) for slug in raw_slugs]
            raw_slugs = [slug for slug in raw_slugs if self._graph_slug_penalty(slug) >= 1.0]
        return list(dict.fromkeys(raw_slugs))[: self.graph_seed_n]

    def _canonical_graph_seed_slug(self, slug: str) -> str:
        value = str(slug or "").strip()
        if value.lower().startswith("wiki://"):
            return value[7:]
        return value

    def _empty_prerequisite_evidence(self) -> dict:
        return {"queryTerms": [], "protectedSeeds": [], "directEvidence": []}


    def _merge_graph_evidence(
        self,
        direct_evidence: list[tuple],
        graph_results: list[tuple],
        protected_seeds: list[tuple],
    ) -> list[tuple]:
        merged = []
        seen: set[str] = set()
        for source in (direct_evidence, protected_seeds, graph_results):
            for item in source:
                if not isinstance(item, (list, tuple)) or len(item) < 3:
                    continue
                slug = str(item[0])
                if slug in seen:
                    continue
                seen.add(slug)
                merged.append(item)
        return merged

    def _format_prerequisite_evidence(self, evidence: dict) -> dict:
        return {
            "queryTerms": list(evidence.get("queryTerms", [])),
            "protectedSeeds": self._format_graph_items(evidence.get("protectedSeeds", [])),
            "directEvidenceCandidatesTopN": self._format_graph_items(evidence.get("directEvidence", [])),
        }

    def _format_graph_items(self, items: list[tuple]) -> list[dict]:
        formatted = []
        for rank, item in enumerate(items, start=1):
            if not isinstance(item, (list, tuple)) or len(item) < 3:
                continue
            try:
                score = float(item[2])
            except (TypeError, ValueError):
                score = 0.0
            formatted.append(
                {
                    "rank": rank,
                    "slug": str(item[0]),
                    "title": str(item[1]),
                    "score": round(score, 4),
                    "source": str(item[3]) if len(item) > 3 else "graph",
                }
            )
        return formatted

    def _graph_slug_penalty(self, slug: str) -> float:
        return low_value_penalty(slug)

    def _promote_strong_grep_top_with_diagnostics(
        self,
        fused: list[tuple],
        grep_results: dict,
        graph_intent: str | None,
    ) -> tuple[list[tuple], dict]:
        diagnostics = {"promotedSlug": None, "fromRank": None, "reason": None}
        if not self._is_graph_aware_intent(graph_intent):
            return fused, diagnostics
        priority = grep_results.get("priority", []) if isinstance(grep_results, dict) else []
        if not priority:
            return fused, diagnostics
        top_hit = priority[0]
        if not isinstance(top_hit, (list, tuple)) or len(top_hit) < 3:
            return fused, diagnostics
        try:
            coverage = float(top_hit[2])
        except (TypeError, ValueError):
            return fused, diagnostics
        if coverage < 0.98:
            return fused, diagnostics

        target_slug = str(top_hit[0])
        ranked = list(fused)
        current_index = self._find_slug_index(ranked, target_slug, start=0)
        if current_index is None or current_index == 0 or current_index >= 3:
            return fused, diagnostics

        item = ranked.pop(current_index)
        lead_score = self._item_score(ranked[0]) if ranked else self._item_score(item)
        promoted = (item[0], item[1], max(self._item_score(item), lead_score))
        ranked.insert(0, promoted)
        diagnostics.update(
            {
                "promotedSlug": target_slug,
                "fromRank": current_index + 1,
                "reason": "strong_grep_top_in_top3",
            }
        )
        return ranked, diagnostics

    def _protect_strong_grep_top3_with_diagnostics(
        self,
        fused: list[tuple],
        grep_results: dict,
        graph_intent: str | None,
    ) -> tuple[list[tuple], dict]:
        diagnostics = {"promotedSlugs": [], "reason": None}
        if not self._is_graph_aware_intent(graph_intent):
            return fused, diagnostics
        priority = grep_results.get("priority", []) if isinstance(grep_results, dict) else []
        if not priority:
            return fused, diagnostics

        ranked = list(fused)
        for target_index, item in enumerate(priority[:3]):
            if not isinstance(item, (list, tuple)) or len(item) < 3:
                continue
            try:
                coverage = float(item[2])
            except (TypeError, ValueError):
                continue
            if coverage < 0.98:
                continue
            slug = str(item[0])
            current_index = self._find_slug_index(ranked, slug, start=0)
            if current_index is None or current_index < 3 or current_index >= 5:
                continue
            promoted = ranked.pop(current_index)
            insert_at = min(target_index, 2, len(ranked))
            ranked.insert(insert_at, promoted)
            diagnostics["promotedSlugs"].append(
                {"slug": slug, "fromRank": current_index + 1, "toRank": insert_at + 1}
            )

        if diagnostics["promotedSlugs"]:
            diagnostics["reason"] = "strong_grep_top3_in_top5"
            return ranked, diagnostics
        return fused, diagnostics

    def _protect_strong_grep_evidence_with_diagnostics(
        self,
        fused: list[tuple],
        grep_results: dict,
        graph_intent: str | None,
    ) -> tuple[list[tuple], dict]:
        diagnostics = {"insertedSlugs": [], "reason": None}
        if not self._is_graph_aware_intent(graph_intent):
            return fused, diagnostics
        if not self._normalize_graph_intent(graph_intent) in {
            "COMPARISON",
            "CROSS_LAYER_RELATION",
            "MECHANISM_APPLICATION",
        }:
            return fused, diagnostics

        priority = grep_results.get("priority", []) if isinstance(grep_results, dict) else []
        ranked = list(fused)
        top5_seen = {safe_slug_key(item[0]) for item in ranked[:5] if isinstance(item, (list, tuple)) and item}
        protected_insertions: set[str] = set()
        for item in priority[:6]:
            if not isinstance(item, (list, tuple)) or len(item) < 3:
                continue
            try:
                coverage = float(item[2])
            except (TypeError, ValueError):
                continue
            slug = str(item[0])
            slug_key = safe_slug_key(slug)
            if coverage < 1.0 or not slug_key or slug_key in top5_seen:
                continue
            if self._graph_slug_penalty(slug) < 1.0:
                continue
            if self._top5_represents_evidence(ranked, item):
                continue
            replace_at, replacement_kind = self._grep_evidence_replacement_target(
                ranked,
                protected_insertions,
                candidate_slug=slug,
            )
            if replace_at is None:
                break
            replaced = ranked[replace_at]
            ranked[replace_at] = (item[0], item[1], min(self._item_score(replaced), 0.02))
            existing_at = self._find_slug_key_index(ranked, slug_key, start=replace_at + 1)
            if existing_at is not None:
                ranked[existing_at] = replaced
            top5_seen.add(slug_key)
            protected_insertions.add(slug_key)
            diagnostics["insertedSlugs"].append(
                {
                    "slug": slug,
                    "rank": replace_at + 1,
                    "coverage": coverage,
                    "replacedSlug": str(replaced[0]) if isinstance(replaced, (list, tuple)) and replaced else None,
                    "reason": replacement_kind,
                }
            )
            if len(diagnostics["insertedSlugs"]) >= 2:
                break

        if diagnostics["insertedSlugs"]:
            diagnostics["reason"] = "strong_grep_evidence_top6"
            return ranked, diagnostics
        return fused, diagnostics

    def _protect_explicit_graph_evidence_with_diagnostics(
        self,
        fused: list[tuple],
        graph_results: list[tuple],
        query: str | None,
        graph_intent: str | None,
    ) -> tuple[list[tuple], dict]:
        diagnostics = {"insertedSlugs": [], "reason": None}
        if not self._is_graph_aware_intent(graph_intent) or not graph_results or not query:
            return fused, diagnostics

        ranked = list(fused)
        top5_seen = {safe_slug_key(item[0]) for item in ranked[:5] if isinstance(item, (list, tuple)) and item}
        inserted_signatures: set[str] = set()
        for item in graph_results[:10]:
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            slug = str(item[0])
            slug_key = safe_slug_key(slug)
            if not slug_key or slug_key in top5_seen or self._graph_slug_penalty(slug) < 1.0:
                continue
            if self._top5_represents_evidence(ranked, item, slug_only=True):
                continue
            signature = self._explicit_query_evidence_signature(item, query)
            if not signature or signature in inserted_signatures:
                continue
            replace_at, replacement_kind = self._graph_query_evidence_replacement_target(
                ranked,
                query,
                candidate=item,
            )
            if replace_at is None:
                continue

            existing_at = self._find_slug_key_index(ranked, slug_key, start=5)
            if existing_at is not None:
                candidate_item = ranked.pop(existing_at)
            else:
                replaced_score = self._item_score(ranked[replace_at]) if replace_at < len(ranked) else 0.02
                candidate_item = (item[0], item[1], min(replaced_score, 0.019))

            if replace_at >= len(ranked):
                ranked.append(candidate_item)
                top5_seen.add(slug_key)
                inserted_signatures.add(signature)
                diagnostics["insertedSlugs"].append(
                    {
                        "slug": slug,
                        "rank": len(ranked),
                        "replacedSlug": None,
                        "reason": replacement_kind,
                        "signature": signature,
                    }
                )
                continue

            replaced = ranked[replace_at]
            ranked[replace_at] = candidate_item
            if existing_at is not None:
                insert_at = min(existing_at, len(ranked))
                ranked.insert(insert_at, replaced)
            top5_seen.add(slug_key)
            inserted_signatures.add(signature)
            diagnostics["insertedSlugs"].append(
                {
                    "slug": slug,
                    "rank": replace_at + 1,
                    "replacedSlug": str(replaced[0]) if isinstance(replaced, (list, tuple)) and replaced else None,
                    "reason": replacement_kind,
                    "signature": signature,
                }
            )
            if len(diagnostics["insertedSlugs"]) >= 2:
                break

        if diagnostics["insertedSlugs"]:
            diagnostics["reason"] = "explicit_graph_evidence_top5"
            return ranked, diagnostics
        return fused, diagnostics

    def _balance_query_object_top3_with_diagnostics(
        self,
        fused: list[tuple],
        grep_results: dict,
        vector_results: list[tuple],
        graph_results: list[tuple],
        query: str | None,
        graph_intent: str | None,
    ) -> tuple[list[tuple], dict]:
        diagnostics = {"promotedSlugs": [], "queryObjects": [], "reason": None}
        if not self._is_graph_aware_intent(graph_intent):
            return fused, diagnostics

        query_objects = self._extract_query_object_terms(query)
        diagnostics["queryObjects"] = query_objects
        if not query_objects:
            return fused, diagnostics

        ranked = list(fused)
        candidate_pool = self._query_object_candidate_pool(ranked, grep_results, vector_results, graph_results)
        promoted_keys: set[str] = set()
        max_promotions = 3 if self._normalize_graph_intent(graph_intent) == "COMPARISON" else 2
        for object_index, object_term in enumerate(query_objects[:5]):
            if self._top3_has_object(ranked, object_term):
                continue
            candidate = self._best_query_object_candidate(candidate_pool, object_term)
            if candidate is None:
                continue
            candidate_key = safe_slug_key(candidate[0])
            if not candidate_key or candidate_key in promoted_keys:
                continue
            replace_at, replacement_reason = self._query_object_replacement_target(ranked, query_objects)
            if replace_at is None:
                continue

            existing_at = self._find_slug_key_index(ranked, candidate_key, start=0)
            if existing_at is not None:
                candidate_item = ranked.pop(existing_at)
                if existing_at < replace_at:
                    replace_at -= 1
            else:
                candidate_item = (candidate[0], candidate[1], min(self._item_score(ranked[replace_at]), 0.021))

            replaced = ranked[replace_at]
            ranked[replace_at] = candidate_item
            promoted_keys.add(candidate_key)
            diagnostics["promotedSlugs"].append(
                {
                    "slug": str(candidate_item[0]),
                    "queryObject": object_term,
                    "rank": replace_at + 1,
                    "replacedSlug": str(replaced[0]) if isinstance(replaced, (list, tuple)) and replaced else None,
                    "reason": replacement_reason,
                    "objectIndex": object_index,
                }
            )
            if len(diagnostics["promotedSlugs"]) >= max_promotions:
                break

        if diagnostics["promotedSlugs"]:
            diagnostics["reason"] = "query_object_top3_balance"
            return ranked, diagnostics
        return fused, diagnostics

    def _query_object_candidate_pool(
        self,
        ranked: list[tuple],
        grep_results: dict,
        vector_results: list[tuple],
        graph_results: list[tuple],
    ) -> list[tuple]:
        pool: list[tuple] = []
        seen: set[str] = set()
        sources = [
            ranked[:10],
            grep_results.get("priority", [])[:12] if isinstance(grep_results, dict) else [],
            vector_results[:10],
            graph_results[:10],
            grep_results.get("normal", [])[:8] if isinstance(grep_results, dict) else [],
        ]
        for items in sources:
            for item in items:
                if not isinstance(item, (list, tuple)) or len(item) < 2:
                    continue
                slug = str(item[0])
                key = safe_slug_key(slug)
                if not key or key in seen or self._graph_slug_penalty(slug) < 1.0:
                    continue
                seen.add(key)
                pool.append(item)
        return pool

    def _promote_primary_query_object_top1_with_diagnostics(
        self,
        fused: list[tuple],
        query: str | None,
        graph_intent: str | None,
    ) -> tuple[list[tuple], dict]:
        diagnostics = {"promotedSlug": None, "fromRank": None, "queryObject": None, "reason": None}
        if not self._is_graph_aware_intent(graph_intent):
            return fused, diagnostics

        query_objects = self._extract_query_object_terms(query)
        if not query_objects or len(fused) < 2:
            return fused, diagnostics

        primary_object = query_objects[0]
        ranked = list(fused)
        if self._item_matches_query_object(ranked[0], primary_object):
            return fused, diagnostics

        primary_index = None
        for index, item in enumerate(ranked[1:3], start=1):
            if not isinstance(item, (list, tuple)) or not item:
                continue
            if self._graph_slug_penalty(str(item[0])) < 1.0:
                continue
            if self._item_matches_query_object(item, primary_object):
                primary_index = index
                break
        if primary_index is None:
            return fused, diagnostics

        top1_secondary_hits = self._matched_query_objects(ranked[0], query_objects[1:])
        if not top1_secondary_hits:
            return fused, diagnostics

        item = ranked.pop(primary_index)
        lead_score = self._item_score(ranked[0]) if ranked else self._item_score(item)
        promoted = (item[0], item[1], max(self._item_score(item), lead_score))
        ranked.insert(0, promoted)
        diagnostics.update(
            {
                "promotedSlug": str(item[0]),
                "fromRank": primary_index + 1,
                "queryObject": primary_object,
                "reason": "primary_query_object_already_in_top3",
            }
        )
        return ranked, diagnostics

    def _best_query_object_candidate(self, candidates: list[tuple], object_term: str) -> tuple | None:
        for item in candidates:
            if self._item_matches_query_object(item, object_term):
                return item
        return None

    def _query_object_replacement_target(
        self,
        ranked: list[tuple],
        query_objects: list[str],
    ) -> tuple[int | None, str]:
        top_end = min(len(ranked), 3)
        if top_end == 0:
            return None, "empty_ranked"

        for index in range(top_end):
            item = ranked[index]
            if not isinstance(item, (list, tuple)) or not item:
                continue
            if self._graph_slug_penalty(str(item[0])) < 1.0:
                return index, "replace_low_value_top3"

        object_hits = [self._matched_query_objects(item, query_objects) for item in ranked[:top_end]]
        for index, hits in enumerate(object_hits):
            if not hits:
                return index, "replace_non_object_top3"

        for index in range(top_end - 1, -1, -1):
            hits = object_hits[index]
            if any(
                hit in other_hits
                for hit in hits
                for other_index, other_hits in enumerate(object_hits)
                if other_index != index
            ):
                return index, "replace_duplicate_object_top3"

        return top_end - 1, "replace_tail_top3"

    def _top3_has_object(self, ranked: list[tuple], object_term: str) -> bool:
        return any(self._item_matches_query_object(item, object_term) for item in ranked[:3])

    def _matched_query_objects(self, item: tuple, query_objects: list[str]) -> set[str]:
        return {term for term in query_objects if self._item_matches_query_object(item, term)}

    def _item_matches_query_object(self, item: tuple, object_term: str) -> bool:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            return False
        term = self._normalize_query_object_text(object_term)
        if not term:
            return False
        labels = self._query_object_labels(item[0], item[1])
        if term in labels:
            return True
        has_cjk = bool(re.search(r"[\u4e00-\u9fff]", term))
        min_len = 2 if has_cjk else 3
        if len(term) < min_len:
            return False
        return any(
            term in label or self._label_is_specific_query_object_alias(label, term)
            for label in labels
            if len(label) >= min_len
        )

    def _label_is_specific_query_object_alias(self, label: str, term: str) -> bool:
        if label not in term:
            return False
        if not term.startswith(label):
            return True
        return len(label) >= 5 or len(label) / max(len(term), 1) >= 0.72

    def _query_object_labels(self, slug: str, title: str) -> set[str]:
        raw_slug = str(slug or "")
        slug_without_scheme = raw_slug[7:] if raw_slug.lower().startswith("wiki://") else raw_slug
        tail = slug_without_scheme.rsplit("/", 1)[-1]
        return {
            label
            for label in {
                self._normalize_query_object_text(slug_without_scheme),
                self._normalize_query_object_text(tail),
                self._normalize_query_object_text(title),
                self._normalize_query_object_text(str(title or "").strip("\"'")),
            }
            if label
        }

    def _extract_query_object_terms(self, query: str | None) -> list[str]:
        if not query:
            return []
        quoted_chunks = re.findall(r"[\u300c\u300e\u201c\"]([^\u300d\u300f\u201d\"]+)[\u300d\u300f\u201d\"]", query)
        quoted_chunks.extend(re.findall(r"\u300a([^\u300b]+)\u300b", query))
        quoted_chunks.extend(re.findall(r"\u300c([^\u300d]+)\u300d", query))
        terms: list[str] = []
        for chunk in quoted_chunks:
            terms.extend(self._query_object_variants(chunk))
            for part in re.split(r"[\u3001,\uff0c\uff1b;]+", chunk):
                terms.extend(self._query_object_variants(part))

        seen: set[str] = set()
        result: list[str] = []
        for term in terms:
            normalized = self._normalize_query_object_text(term)
            if not normalized or normalized in seen:
                continue
            has_cjk = bool(re.search(r"[\u4e00-\u9fff]", normalized))
            if not has_cjk and len(normalized) < 3:
                continue
            seen.add(normalized)
            result.append(normalized)
        return result[:8]

    def _query_object_variants(self, value: str) -> list[str]:
        text = str(value or "").strip()
        if not text:
            return []
        variants = [text]
        if "-" in text:
            variants.append(text.rsplit("-", 1)[-1])
        if "/" in text:
            variants.append(text.rsplit("/", 1)[-1])
        return variants

    def _normalize_query_object_text(self, value: str | None) -> str:
        text = str(value or "").strip().strip("\"'`")
        if text.lower().startswith("wiki://"):
            text = text[7:]
        return re.sub(r"[\s\-_:/\u3001\uff0c,.;；:：()\uff08\uff09\"'`]+", "", text.lower())

    def _grep_evidence_replacement_target(
        self,
        ranked: list[tuple],
        protected_slug_keys: set[str],
        candidate_slug: str | None = None,
    ) -> tuple[int | None, str]:
        tail_end = min(len(ranked), 5)
        for index in range(3, tail_end):
            item = ranked[index]
            if not isinstance(item, (list, tuple)) or not item or safe_slug_key(item[0]) in protected_slug_keys:
                continue
            if self._graph_slug_penalty(str(item[0])) < 1.0:
                return index, "replace_low_value_tail"
        candidate_course = self._slug_course(candidate_slug)
        for index in range(tail_end - 1, 2, -1):
            item = ranked[index]
            if not isinstance(item, (list, tuple)) or not item or safe_slug_key(item[0]) in protected_slug_keys:
                continue
            if candidate_course and self._slug_course(item[0]) != candidate_course:
                return index, "replace_cross_course_tail"
        return None, "no_replaceable_tail"

    def _graph_query_evidence_replacement_target(
        self,
        ranked: list[tuple],
        query: str,
        *,
        candidate: tuple,
    ) -> tuple[int | None, str]:
        tail_end = min(len(ranked), 5)
        if tail_end < 5:
            return tail_end, "append_under_top5"

        key_counts: dict[str, int] = {}
        for item in ranked[:tail_end]:
            if not isinstance(item, (list, tuple)) or not item:
                continue
            key = safe_slug_key(item[0])
            if key:
                key_counts[key] = key_counts.get(key, 0) + 1
        for index in range(tail_end - 1, 2, -1):
            item = ranked[index]
            if not isinstance(item, (list, tuple)) or not item:
                continue
            if key_counts.get(safe_slug_key(item[0]), 0) > 1:
                return index, "replace_duplicate_tail"

        for index in range(tail_end - 1, 2, -1):
            item = ranked[index]
            if isinstance(item, (list, tuple)) and item and self._graph_slug_penalty(str(item[0])) < 1.0:
                return index, "replace_low_value_tail"

        candidate_course = self._slug_course(candidate[0])
        for index in range(tail_end - 1, 2, -1):
            item = ranked[index]
            if not isinstance(item, (list, tuple)) or not item:
                continue
            if candidate_course and self._slug_course(item[0]) != candidate_course:
                return index, "replace_cross_course_tail"

        for index in range(tail_end - 1, 2, -1):
            item = ranked[index]
            if not isinstance(item, (list, tuple)) or not item:
                continue
            if not self._explicit_query_evidence_signature(item, query):
                return index, "replace_non_explicit_tail"

        return None, "no_replaceable_tail"

    def _top5_represents_evidence(
        self,
        ranked: list[tuple],
        candidate: tuple,
        *,
        slug_only: bool = False,
    ) -> bool:
        return any(
            isinstance(item, (list, tuple))
            and item
            and self._items_share_specific_evidence_label(item, candidate, slug_only=slug_only)
            for item in ranked[:5]
        )

    def _items_share_specific_evidence_label(self, left: tuple, right: tuple, *, slug_only: bool = False) -> bool:
        left_labels = self._slug_evidence_labels(left[0]) if slug_only else self._query_object_labels(left[0], left[1])
        right_labels = self._slug_evidence_labels(right[0]) if slug_only else self._query_object_labels(right[0], right[1])
        for left_label in left_labels:
            for right_label in right_labels:
                if self._labels_share_specific_evidence(left_label, right_label):
                    return True
        return False

    def _slug_evidence_labels(self, slug: str) -> set[str]:
        raw_slug = str(slug or "")
        slug_without_scheme = raw_slug[7:] if raw_slug.lower().startswith("wiki://") else raw_slug
        tail = slug_without_scheme.rsplit("/", 1)[-1]
        return {
            label
            for label in {
                self._normalize_query_object_text(slug_without_scheme),
                self._normalize_query_object_text(tail),
            }
            if label
        }

    def _labels_share_specific_evidence(self, left_label: str, right_label: str) -> bool:
        if not left_label or not right_label:
            return False
        if left_label == right_label and len(left_label) >= 4:
            return True
        left_core = self._strip_generic_evidence_suffix(left_label)
        right_core = self._strip_generic_evidence_suffix(right_label)
        return len(left_core) >= 4 and left_core == right_core

    def _strip_generic_evidence_suffix(self, label: str) -> str:
        text = str(label or "")
        changed = True
        while changed:
            changed = False
            for suffix in ("算法", "原理", "机制", "详解", "基础", "概述"):
                if len(text) > len(suffix) + 3 and text.endswith(suffix):
                    text = text[: -len(suffix)]
                    changed = True
                    break
        return text

    def _explicit_query_evidence_signature(self, item: tuple, query: str | None) -> str:
        if not query or not isinstance(item, (list, tuple)) or len(item) < 2:
            return ""
        normalized_query = self._normalize_query_object_text(query)
        if not normalized_query:
            return ""

        labels = sorted(self._query_object_labels(item[0], item[1]), key=len, reverse=True)
        for label in labels:
            if len(label) >= 5 and label in normalized_query:
                return label

        query_tokens = set(re.findall(r"[a-z][a-z0-9+#.]{1,}", normalized_query))
        for label in labels:
            label_tokens = set(re.findall(r"[a-z][a-z0-9+#.]{1,}", label))
            matched_tokens = sorted(label_tokens & query_tokens)
            if not matched_tokens:
                continue
            cjk_anchor = self._longest_query_cjk_anchor(label, normalized_query)
            if cjk_anchor:
                return f"{'+'.join(matched_tokens)}:{cjk_anchor}"
        return ""

    def _longest_query_cjk_anchor(self, label: str, normalized_query: str) -> str:
        anchors = [
            chunk
            for chunk in re.findall(r"[\u4e00-\u9fff]{3,}", label)
            if chunk in normalized_query and chunk not in {"算法", "系统", "模型", "原理"}
        ]
        if not anchors:
            return ""
        return max(anchors, key=len)

    def _slug_course(self, slug: str | None) -> str:
        value = str(slug or "").strip()
        if value.lower().startswith("wiki://"):
            value = value[7:]
        return value.split("/", 1)[0].strip().lower() if "/" in value else ""

    def _item_score(self, item: tuple) -> float:
        try:
            return float(item[2])
        except (IndexError, TypeError, ValueError):
            return 0.0

    def _stabilize_graph_top5(
        self,
        fused: list[tuple],
        graph_results: list[tuple],
        graph_intent: str | None,
    ) -> list[tuple]:
        stabilized, _diagnostics = self._stabilize_graph_top5_with_diagnostics(fused, graph_results, graph_intent)
        return stabilized

    def _stabilize_graph_top5_with_diagnostics(
        self,
        fused: list[tuple],
        graph_results: list[tuple],
        graph_intent: str | None,
        *,
        protected_slugs: set[str] | None = None,
    ) -> tuple[list[tuple], dict]:
        diagnostics = {"seedProtectedTop5": [], "replacementReason": []}
        if not self._uses_graph_evidence_fill(graph_intent) or not graph_results:
            return fused, diagnostics

        ranked = list(fused)
        protected_slugs = protected_slugs or set()
        top5_seen = {str(item[0]) for item in ranked[:5] if isinstance(item, (list, tuple)) and item}
        seen = set(top5_seen)
        insertions = [
            (slug, title, round(0.01 + float(score) / 10000, 4))
            for slug, title, score, *_ in graph_results
            if str(slug) not in top5_seen and self._graph_slug_penalty(str(slug)) >= 1.0
        ]
        if not insertions:
            diagnostics["seedProtectedTop5"] = self._protected_slugs_in_top5(ranked, protected_slugs)
            return ranked, diagnostics

        protected_prefix = 3
        for candidate in insertions:
            candidate_slug = str(candidate[0])
            if len(ranked) < 5:
                ranked.append(candidate)
                seen.add(candidate_slug)
                diagnostics["replacementReason"].append(
                    {"rank": len(ranked), "insertedSlug": candidate_slug, "reason": "append_under_top5"}
                )
                continue
            replace_at, replacement_kind = self._graph_replacement_target(
                ranked,
                protected_prefix,
                protected_slugs,
            )
            if replace_at is None:
                break
            replaced = ranked[replace_at]
            ranked[replace_at] = candidate
            seen.add(candidate_slug)
            existing_at = self._find_slug_index(ranked, candidate_slug, start=replace_at + 1)
            if existing_at is not None:
                ranked[existing_at] = replaced
            diagnostics["replacementReason"].append(
                {
                    "rank": replace_at + 1,
                    "insertedSlug": candidate_slug,
                    "replacedSlug": str(replaced[0]) if isinstance(replaced, (list, tuple)) and replaced else None,
                    "reason": replacement_kind,
                }
            )
            if replacement_kind == "replace_unprotected_tail":
                break
        diagnostics["seedProtectedTop5"] = self._protected_slugs_in_top5(ranked, protected_slugs)
        return ranked, diagnostics

    def _protected_slugs_in_top5(self, ranked: list[tuple], protected_slugs: set[str]) -> list[str]:
        top5 = {str(item[0]) for item in ranked[:5] if isinstance(item, (list, tuple)) and item}
        return sorted(slug for slug in protected_slugs if slug in top5)

    def _graph_replacement_target(
        self,
        ranked: list[tuple],
        protected_prefix: int,
        protected_slugs: set[str] | None = None,
    ) -> tuple[int | None, str]:
        protected_slugs = protected_slugs or set()
        tail_end = min(len(ranked), 5)
        for index in range(protected_prefix, tail_end):
            item = ranked[index]
            if not isinstance(item, (list, tuple)) or not item:
                continue
            if str(item[0]) in protected_slugs:
                continue
            if self._graph_slug_penalty(str(item[0])) < 1.0:
                return index, "replace_low_value_tail"
        if len(ranked) >= 5:
            if str(ranked[4][0]) in protected_slugs:
                return None, "no_replaceable_tail"
            return 4, "replace_unprotected_tail"
        return None, "no_replaceable_tail"

    def _find_slug_index(self, ranked: list[tuple], slug: str, *, start: int) -> int | None:
        for index in range(start, len(ranked)):
            item = ranked[index]
            if isinstance(item, (list, tuple)) and item and str(item[0]) == slug:
                return index
        return None

    def _find_slug_key_index(self, ranked: list[tuple], slug_key: str, *, start: int) -> int | None:
        for index in range(start, len(ranked)):
            item = ranked[index]
            if isinstance(item, (list, tuple)) and item and safe_slug_key(item[0]) == slug_key:
                return index
        return None

    def _graph_replacement_index(
        self,
        ranked: list[tuple],
        protected_prefix: int,
        protected_slugs: set[str] | None = None,
    ) -> int | None:
        index, _reason = self._graph_replacement_target(ranked, protected_prefix, protected_slugs)
        return index

    def _web_query(self, query: str) -> str:
        lowered = query.lower()
        time_sensitive_terms = ("今天", "今日", "现在", "当前", "today", "current", "now")
        if not any(term in lowered for term in time_sensitive_terms):
            return query
        today = datetime.now()
        return f"{query} {today.year}年{today.month}月{today.day}日"
