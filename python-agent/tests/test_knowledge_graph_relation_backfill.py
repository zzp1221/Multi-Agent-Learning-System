from scripts.backfill_knowledge_graph_relations import NodeRow, infer_edges


def test_infer_edges_builds_part_of_and_related_without_duplicates() -> None:
    nodes = [
        NodeRow("java", "Java"),
        NodeRow("java_thread", "Java:线程"),
        NodeRow("java_lock", "Java:锁机制"),
        NodeRow("python_async", "Python:异步"),
    ]

    edges = infer_edges(nodes, limit_per_node=2)
    edge_keys = {(edge.from_key, edge.to_key, edge.relation_type) for edge in edges}

    assert ("java", "java_thread", "PART_OF") in edge_keys
    assert ("java", "java_lock", "PART_OF") in edge_keys
    assert any(edge.relation_type == "RELATED" for edge in edges)
    assert len(edge_keys) == len(edges)
