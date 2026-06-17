"""Graph evidence helpers used by TutorAgent."""

from __future__ import annotations

from typing import Any

GRAPH_AWARE_INTENTS = {
    "COMMON_MISTAKE",
    "COMMUNITY_SUMMARY",
    "COMPARISON",
    "CROSS_LAYER_RELATION",
    "MECHANISM_APPLICATION",
    "MULTI_HOP_RELATION",
    "PREREQUISITE_PATH",
}

GRAPH_INTENT_GUIDANCE = {
    "PREREQUISITE_PATH": "围绕可能的前置基础、当前概念和后续延伸组织答案；不要把候选排序说成已验证的严格先修顺序。",
    "CROSS_LAYER_RELATION": "围绕跨层相关证据组织答案；只在证据明确时才表达确定的层间因果或调用链。",
    "MECHANISM_APPLICATION": "围绕机制、触发条件、实现位置和应用效果组织答案；不要把候选排序说成真实执行顺序。",
    "COMPARISON": "先给共同点，再给关键差异、适用边界和容易混淆的判断标准。",
    "COMMON_MISTAKE": "先指出常见误解，再用反例或边界条件澄清，最后给正确心智模型。",
    "COMMUNITY_SUMMARY": "按概念群组总结主题、共同作用和组内差异。",
    "MULTI_HOP_RELATION": "围绕多跳相关证据组织答案，避免只解释孤立概念；不把候选列表当作真实路径。",
}

GRAPH_SOURCE_LABELS = {
    "direct_evidence": "直接证据",
    "seed_protected": "种子概念",
    "graph_1hop": "一跳图谱相关概念",
    "graph_2hop": "二跳图谱补充概念",
    "graph": "图谱相关概念",
}


def build_graph_evidence_pack(
    *,
    params: dict[str, Any],
    documents: Any = None,
) -> dict[str, Any]:
    intent = resolve_graph_intent_from_params(params)
    if intent not in GRAPH_AWARE_INTENTS:
        return {}

    raw_result = params.get("retrievalRawResult", {})
    raw_result = raw_result if isinstance(raw_result, dict) else {}
    graph_result = params.get("graphRetrievalResult", {})
    graph_result = graph_result if isinstance(graph_result, dict) else {}

    nodes = collect_graph_evidence_nodes(
        raw_result=raw_result,
        graph_result=graph_result,
        documents=documents if isinstance(documents, list) else [],
    )
    if not nodes:
        return {}
    return {
        "intent": intent,
        "guidance": GRAPH_INTENT_GUIDANCE.get(intent, "优先按图谱关系组织回答。"),
        "nodes": nodes[:8],
        "relationHints": build_graph_relation_hints(
            intent=intent,
            nodes=nodes,
            raw_result=raw_result,
        ),
    }


def resolve_graph_intent_from_params(params: dict[str, Any]) -> str:
    for value in (
        params.get("graphIntent"),
        params.get("retrievalRawResult", {}).get("graphIntent")
        if isinstance(params.get("retrievalRawResult"), dict)
        else None,
        params.get("graphRetrievalResult", {}).get("graphIntent")
        if isinstance(params.get("graphRetrievalResult"), dict)
        else None,
    ):
        if isinstance(value, str) and value.strip():
            return value.strip().upper()
    classification = params.get("queryClassification")
    if isinstance(classification, dict):
        value = classification.get("graphIntent")
        if isinstance(value, str) and value.strip():
            return value.strip().upper()
    return ""


def collect_graph_evidence_nodes(
    *,
    raw_result: dict[str, Any],
    graph_result: dict[str, Any],
    documents: list[Any],
) -> list[dict[str, Any]]:
    candidates: list[tuple[Any, str]] = []
    candidates.extend((item, "graph") for item in graph_result_items(graph_result))
    candidates.extend(
        (item, "graph")
        for item in documents
        if isinstance(item, dict) and str(item.get("channel") or "").strip().lower() == "graph"
    )

    diagnostics = raw_result.get("graphDiagnostics", {})
    if isinstance(diagnostics, dict):
        prerequisite = diagnostics.get("prerequisiteEvidence", {})
        if isinstance(prerequisite, dict):
            candidates.extend(
                (item, "direct_evidence")
                for item in prerequisite.get("directEvidenceCandidatesTopN", [])
            )
            candidates.extend(
                (item, "seed_protected")
                for item in prerequisite.get("protectedSeeds", [])
            )

    channels = raw_result.get("channels", {})
    if isinstance(channels, dict):
        candidates.extend((item, "graph") for item in channels.get("graph", []))

    nodes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, (item, fallback_source) in enumerate(candidates, start=1):
        node = graph_node_from_item(item, fallback_source=fallback_source, fallback_rank=index)
        if not node:
            continue
        dedupe_keys = [
            str(value)
            for value in (node.get("slug"), node.get("title"))
            if str(value or "").strip()
        ]
        if not dedupe_keys or any(key in seen for key in dedupe_keys):
            continue
        seen.update(dedupe_keys)
        nodes.append(node)
    return nodes


def graph_result_items(graph_result: dict[str, Any]) -> list[Any]:
    results = graph_result.get("results", [])
    return results if isinstance(results, list) else []


def graph_node_from_item(
    item: Any,
    *,
    fallback_source: str,
    fallback_rank: int,
) -> dict[str, Any]:
    if isinstance(item, dict):
        title = str(item.get("title") or "").strip()
        slug = str(item.get("slug") or "").strip()
        source = str(item.get("source") or fallback_source).strip()
        score = item.get("score")
        rank = item.get("rank") or fallback_rank
    elif isinstance(item, (list, tuple)) and len(item) >= 2:
        slug = str(item[0] or "").strip()
        title = str(item[1] or "").strip()
        source = str(item[3] if len(item) > 3 else fallback_source).strip()
        score = item[2] if len(item) > 2 else None
        rank = fallback_rank
    else:
        return {}

    if not title and not slug:
        return {}
    return {
        "rank": rank,
        "slug": slug,
        "title": title or slug,
        "source": source or fallback_source,
        "hop": graph_hop_from_source(source or fallback_source),
        "score": safe_float(score),
    }


def graph_source_label(source: str) -> str:
    normalized = str(source or "").strip().lower()
    return GRAPH_SOURCE_LABELS.get(normalized, "图谱相关概念")


def graph_hop_from_source(source: str) -> int | None:
    normalized = str(source or "").strip().lower()
    if "2hop" in normalized or "2_hop" in normalized:
        return 2
    if "1hop" in normalized or "1_hop" in normalized:
        return 1
    return None


def safe_float(value: Any) -> float | None:
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None


def build_graph_relation_hints(
    *,
    intent: str,
    nodes: list[dict[str, Any]],
    raw_result: dict[str, Any],
) -> list[str]:
    titles = [str(node.get("title") or "").strip() for node in nodes if node.get("title")]
    hints: list[str] = []
    if len(titles) >= 2:
        joined_titles = "、".join(titles[:5])
        if intent == "PREREQUISITE_PATH":
            hints.append(f"学习路径相关候选集合（非严格顺序）：{joined_titles}")
        elif intent in {"CROSS_LAYER_RELATION", "MULTI_HOP_RELATION"}:
            hints.append(f"关系链相关候选集合（非严格顺序）：{joined_titles}")
        elif intent == "MECHANISM_APPLICATION":
            hints.append(f"机制应用相关候选集合（非严格顺序）：{'、'.join(titles[:4])}")
        elif intent == "COMPARISON":
            hints.append("对比对象候选：" + " / ".join(titles[:4]))
        elif intent == "COMMON_MISTAKE":
            hints.append("易错点证据候选：" + " / ".join(titles[:4]))
        elif intent == "COMMUNITY_SUMMARY":
            hints.append("同一概念群候选：" + " / ".join(titles[:5]))

    diagnostics = raw_result.get("graphDiagnostics", {})
    if isinstance(diagnostics, dict):
        top5 = diagnostics.get("top5Stabilization", {})
        if isinstance(top5, dict):
            protected = top5.get("seedProtectedTop5", [])
            if protected:
                hints.append("Top5 中保护的种子节点：" + ", ".join(map(str, protected[:4])))
    return hints
