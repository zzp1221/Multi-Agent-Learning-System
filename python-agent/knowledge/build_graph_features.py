"""Build lightweight graph features for wiki expansion.

Features:
- community_id via weighted label propagation on the undirected wiki graph
- pagerank_score via weighted PageRank on the directed wiki graph

Usage:
  python knowledge/build_graph_features.py
"""
from __future__ import annotations

import sys
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import execute_values

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from settings_helper import configure_dashscope_api_key
from retrieval.graph_relation_policy import weighted_relation_score

RUNTIME_CONFIG = configure_dashscope_api_key()
DB_CONFIG = RUNTIME_CONFIG.postgres.model_dump()
DOMAIN = RUNTIME_CONFIG.retrieval_domain
DAMPING = 0.85
LABEL_PROPAGATION_ROUNDS = 12
PAGERANK_ROUNDS = 20


def connect():
    return psycopg2.connect(**DB_CONFIG)


def relation_weight(relation_type: str, weight) -> float:
    return weighted_relation_score(relation_type, weight or 1.0)


def build_communities(page_ids: list[str], undirected_adj: dict[str, list[tuple[str, float]]]) -> dict[str, int]:
    labels = {page_id: index for index, page_id in enumerate(sorted(page_ids))}
    for _ in range(LABEL_PROPAGATION_ROUNDS):
        changed = False
        for page_id in sorted(page_ids):
            neighbors = undirected_adj.get(page_id, [])
            if not neighbors:
                continue
            scores: dict[int, float] = defaultdict(float)
            for neighbor_id, edge_weight in neighbors:
                scores[labels[neighbor_id]] += edge_weight
            best_label, best_score = max(
                scores.items(),
                key=lambda item: (item[1], -item[0]),
            )
            if best_label != labels[page_id]:
                labels[page_id] = best_label
                changed = True
        if not changed:
            break

    compact_ids = {
        label: index
        for index, label in enumerate(sorted(set(labels.values())), start=1)
    }
    return {page_id: compact_ids[label] for page_id, label in labels.items()}


def build_pagerank(page_ids: list[str], outgoing_adj: dict[str, list[tuple[str, float]]]) -> dict[str, float]:
    total_nodes = len(page_ids)
    if total_nodes == 0:
        return {}

    page_rank = {page_id: 1.0 / total_nodes for page_id in page_ids}
    outbound_weight_sum = {
        page_id: sum(weight for _, weight in outgoing_adj.get(page_id, []))
        for page_id in page_ids
    }

    for _ in range(PAGERANK_ROUNDS):
        next_rank = {page_id: (1.0 - DAMPING) / total_nodes for page_id in page_ids}
        dangling_share = sum(
            page_rank[page_id]
            for page_id in page_ids
            if outbound_weight_sum.get(page_id, 0.0) == 0.0
        )
        dangling_contribution = DAMPING * dangling_share / total_nodes
        for page_id in page_ids:
            next_rank[page_id] += dangling_contribution

        for from_page_id, neighbors in outgoing_adj.items():
            total_outbound = outbound_weight_sum.get(from_page_id, 0.0)
            if total_outbound <= 0:
                continue
            for to_page_id, edge_weight in neighbors:
                next_rank[to_page_id] += (
                    DAMPING * page_rank[from_page_id] * (edge_weight / total_outbound)
                )
        page_rank = next_rank

    return page_rank


def main() -> None:
    print("=" * 60)
    print("Build rag.wiki_page_graph_features")
    print("=" * 60)

    conn = connect()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS rag.wiki_page_graph_features (
                        page_id UUID PRIMARY KEY REFERENCES rag.wiki_page(id) ON DELETE CASCADE,
                        community_id BIGINT NOT NULL,
                        pagerank_score NUMERIC(12,8) NOT NULL DEFAULT 0,
                        in_degree INT NOT NULL DEFAULT 0,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_wiki_page_graph_features_community
                    ON rag.wiki_page_graph_features(community_id, pagerank_score DESC)
                """)

                cur.execute("""
                    SELECT id::text
                    FROM rag.wiki_page
                    WHERE is_active = true AND domain = %s
                """, (DOMAIN,))
                page_ids = [row[0] for row in cur.fetchall()]
                print(f"Active wiki pages: {len(page_ids)}")

                cur.execute("""
                    SELECT from_page_id::text, to_page_id::text, relation_type, weight
                    FROM rag.wiki_link
                """)
                edges = cur.fetchall()
                print(f"Wiki links: {len(edges)}")

                outgoing_adj: dict[str, list[tuple[str, float]]] = defaultdict(list)
                undirected_adj: dict[str, list[tuple[str, float]]] = defaultdict(list)
                in_degree_counter: Counter[str] = Counter()

                active_page_id_set = set(page_ids)
                for from_page_id, to_page_id, relation_type, raw_weight in edges:
                    if from_page_id not in active_page_id_set or to_page_id not in active_page_id_set:
                        continue
                    edge_weight = relation_weight(relation_type, raw_weight)
                    outgoing_adj[from_page_id].append((to_page_id, edge_weight))
                    undirected_adj[from_page_id].append((to_page_id, edge_weight))
                    undirected_adj[to_page_id].append((from_page_id, edge_weight))
                    in_degree_counter[to_page_id] += 1

                communities = build_communities(page_ids, undirected_adj)
                pagerank = build_pagerank(page_ids, outgoing_adj)

                rows = [
                    (
                        page_id,
                        communities.get(page_id, 0),
                        round(float(pagerank.get(page_id, 0.0)), 8),
                        int(in_degree_counter.get(page_id, 0)),
                    )
                    for page_id in page_ids
                ]
                cur.execute("DELETE FROM rag.wiki_page_graph_features")
                execute_values(
                    cur,
                    """
                    INSERT INTO rag.wiki_page_graph_features (
                        page_id, community_id, pagerank_score, in_degree
                    ) VALUES %s
                    """,
                    rows,
                )

                print(f"Inserted graph features: {len(rows)}")
                print(f"Communities: {len(set(communities.values()))}")
                print(f"Top PageRank: {sorted(pagerank.values(), reverse=True)[:5]}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
