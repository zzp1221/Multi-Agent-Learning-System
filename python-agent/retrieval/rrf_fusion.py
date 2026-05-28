"""
Weighted Reciprocal Rank Fusion (RRF) for combining grep, vector, and graph results.
"""
from collections.abc import Callable
from typing import Optional


class RRFFusion:
    """Weighted RRF with phrase-priority boost."""

    def __init__(self, k: int = 60, grep_weight: float = 3.0,
                 vector_weight: float = 5.0, graph_weight: float = 0.5,
                 web_weight: float = 1.5):
        self.k = k
        self.grep_weight = grep_weight
        self.vector_weight = vector_weight
        self.graph_weight = graph_weight
        self.web_weight = web_weight

    def fuse(self,
             grep_results: Optional[dict] = None,
             vector_results: Optional[list[tuple]] = None,
             graph_results: Optional[list[tuple]] = None,
             web_results: Optional[list[tuple]] = None,
             top_n: int = 15,
             grep_weight: float | None = None,
             vector_weight: float | None = None,
             graph_weight: float | None = None,
             web_weight: float | None = None,
             slug_penalty: Callable[[str], float] | None = None) -> list[tuple]:
        """
        Fuse 3 ranked lists via weighted RRF.
        Each input:
          - grep_results: {"priority": [(slug, title, coverage, tokens), ...],
                           "normal":   [(slug, title, coverage, tokens), ...]}
          - vector_results: [(slug, title, similarity), ...]
          - graph_results:  [(slug, title, score), ...]
          - web_results:    [(url, title, score, metadata), ...]
        Returns top_n as [(slug, title, rrf_score), ...].
        """
        active_grep_weight = self.grep_weight if grep_weight is None else grep_weight
        active_vector_weight = self.vector_weight if vector_weight is None else vector_weight
        active_graph_weight = self.graph_weight if graph_weight is None else graph_weight
        active_web_weight = self.web_weight if web_weight is None else web_weight
        scores: dict[str, dict] = {}  # slug -> {title, score}

        def add_ranked(items, weight, priority_boost: float = 1.0):
            for rank, item in enumerate(items):
                slug, title = item[0], item[1]
                penalty = slug_penalty(str(slug)) if slug_penalty else 1.0
                if penalty <= 0:
                    continue
                rrf = weight * priority_boost * penalty / (self.k + rank + 1)
                if slug not in scores:
                    scores[slug] = {"title": title, "score": 0.0}
                scores[slug]["score"] += rrf

        # Grep: priority (phrase matches) get 1.5x boost, normal no boost
        if grep_results:
            priority = grep_results.get("priority", [])
            normal = grep_results.get("normal", [])
            add_ranked(priority, active_grep_weight, priority_boost=1.5)
            add_ranked(normal, active_grep_weight)

        # Vector results
        if vector_results:
            add_ranked(vector_results, active_vector_weight)

        # Graph results
        if graph_results:
            add_ranked(graph_results, active_graph_weight)

        # Web results: opt-in Tavily channel with lower trust than local KB.
        if web_results:
            add_ranked(web_results, active_web_weight)

        # Sort by RRF score
        ranked = sorted(scores.items(), key=lambda x: x[1]["score"], reverse=True)
        return [(slug, info["title"], round(info["score"], 4))
                for slug, info in ranked[:top_n]]
