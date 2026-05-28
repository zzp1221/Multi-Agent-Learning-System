"""
Hybrid Retriever: orchestrates grep + vector + graph channels with RRF fusion.
"""
import logging
import os
from datetime import datetime
import psycopg2
from retrieval.fmm_tokenizer import FMMTokenizer
from retrieval.grep_searcher import GrepSearcher
from retrieval.vector_searcher import VectorSearcher
from retrieval.graph_expander import GraphExpander
from retrieval.rrf_fusion import RRFFusion
from retrieval.tavily_searcher import TavilySearcher
from src.ai_modules.config import get_settings

LOGGER = logging.getLogger(__name__)


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
        graph_intent: str | None = None,
    ) -> dict:
        """Run all 3 channels and fuse. Returns structured results."""
        try:
            self._init(cur)
        except Exception as exc:
            LOGGER.warning("Local hybrid retrieval init failed for query %r: %s", query, exc)
            web_results = TavilySearcher().search(self._web_query(query), top_k=5) if web_search_enabled else []
            fused = RRFFusion().fuse(
                grep_results={"priority": [], "normal": []},
                vector_results=[],
                graph_results=[],
                web_results=web_results,
            )
            return {
                "query": query,
                "graphIntent": graph_intent,
                "webSearchEnabled": web_search_enabled,
                "channels": {
                    "grep": {"priority": [], "normal_count": 0},
                    "vector": [],
                    "graph": [],
                    "web": web_results,
                },
                "fused": fused,
                "top": fused[:5],
            }

        # Channel A: Grep (keyword + coverage)
        try:
            grep_results = self._grep.search(cur, query, self.domain)
        except Exception as exc:
            LOGGER.warning("Grep retrieval failed for query %r: %s", query, exc)
            grep_results = {"priority": [], "normal": []}

        # Channel B: Vector (semantic) — search both knowledge + resource
        try:
            vector_all = self._vector.search_all(cur, query, top_k=self.top_k, domain=self.domain)
        except Exception as exc:
            LOGGER.warning("Vector retrieval failed for query %r: %s", query, exc)
            vector_all = []
        # Strip source tag for RRF: [(slug, title, similarity), ...]
        vector_results = [(r[0], r[1], r[2]) for r in vector_all]

        # Channel C: Graph expansion from top seeds
        grep_slugs = [r[0] for r in grep_results.get("priority", [])[:self.graph_seed_n]]
        vec_slugs = [r[0] for r in vector_results[:self.graph_seed_n]]
        seed_slugs = list(dict.fromkeys(grep_slugs + vec_slugs))[:self.graph_seed_n]
        try:
            graph_results = self._graph.expand(cur, seed_slugs, top_n=5)
        except Exception as exc:
            LOGGER.warning("Graph retrieval failed for query %r: %s", query, exc)
            graph_results = []

        # Channel D: Web search is strictly opt-in per user turn.
        web_results = self._web.search(self._web_query(query), top_k=5) if web_search_enabled else []

        # RRF Fusion
        fused = self._rrf.fuse(grep_results, vector_results, graph_results, web_results)

        return {
            "query": query,
            "graphIntent": graph_intent,
            "webSearchEnabled": web_search_enabled,
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

    def retrieve_grep_first(
        self,
        cur,
        query: str,
        web_search_enabled: bool = False,
        graph_intent: str | None = None,
    ) -> dict:
        """Run grep first and skip vector/graph when phrase confidence is strong."""
        try:
            self._init(cur)
        except Exception as exc:
            LOGGER.warning("Grep-first init failed for query %r: %s", query, exc)
            return self.retrieve(
                cur,
                query,
                web_search_enabled=web_search_enabled,
                graph_intent=graph_intent,
            )

        try:
            grep_results = self._grep.search(cur, query, self.domain)
        except Exception as exc:
            LOGGER.warning("Grep-first grep retrieval failed for query %r: %s", query, exc)
            grep_results = {"priority": [], "normal": []}

        if not self._has_strong_grep_hit(grep_results):
            raw_result = self.retrieve(
                cur,
                query,
                web_search_enabled=web_search_enabled,
                graph_intent=graph_intent,
            )
            raw_result["retrievalStrategy"] = "LOCAL_GREP_FIRST"
            raw_result["grepFirstPromoted"] = True
            return raw_result

        web_results = self._web.search(self._web_query(query), top_k=5) if web_search_enabled else []
        fused = self._rrf.fuse(grep_results, [], [], web_results)
        return {
            "query": query,
            "graphIntent": graph_intent,
            "retrievalStrategy": "LOCAL_GREP_FIRST",
            "grepFirstPromoted": False,
            "webSearchEnabled": web_search_enabled,
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

    def _web_query(self, query: str) -> str:
        lowered = query.lower()
        time_sensitive_terms = ("今天", "今日", "现在", "当前", "today", "current", "now")
        if not any(term in lowered for term in time_sensitive_terms):
            return query
        today = datetime.now()
        return f"{query} {today.year}年{today.month}月{today.day}日"
