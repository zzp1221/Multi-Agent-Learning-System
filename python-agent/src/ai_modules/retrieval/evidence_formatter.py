"""检索证据格式化器 - 构建带元数据的上下文"""

from __future__ import annotations

from typing import Any

# 避免循环导入：直接定义GRAPH_INTENT_GUIDANCE
GRAPH_INTENT_GUIDANCE = {
    "PREREQUISITE_PATH": "围绕可能的前置基础、当前概念和后续延伸组织答案；不要把候选排序说成已验证的严格先修顺序。",
    "CROSS_LAYER_RELATION": "围绕跨层相关证据组织答案；只在证据明确时才表达确定的层间因果或调用链。",
    "MECHANISM_APPLICATION": "围绕机制、触发条件、实现位置和应用效果组织答案；不要把候选排序说成真实执行顺序。",
    "COMPARISON": "先给共同点，再给关键差异、适用边界和容易混淆的判断标准。",
    "COMMON_MISTAKE": "先指出常见误解，再用反例或边界条件澄清，最后给正确心智模型。",
    "COMMUNITY_SUMMARY": "按概念群组总结主题、共同作用和组内差异。",
    "MULTI_HOP_RELATION": "围绕多跳相关证据组织答案，避免只解释孤立概念；不把候选列表当作真实路径。",
}


def format_evidence_with_metadata(
    *,
    documents: list[dict[str, Any]],
    query: str = "",
    graph_intent: str | None = None,
    max_documents: int = 5,
    include_snippets: bool = True,
    snippet_max_length: int = 200,
) -> str:
    """
    构建带元数据的检索证据上下文

    Args:
        documents: 检索文档列表
        query: 用户查询
        graph_intent: 图谱意图类型
        max_documents: 最多包含的文档数
        include_snippets: 是否包含摘要片段
        snippet_max_length: 摘要片段最大长度

    Returns:
        格式化的证据上下文字符串
    """
    if not documents:
        return ""

    context_parts = ["## 检索证据\n"]

    # 避免 "lost in the middle" - 将高分文档放在开头和结尾
    high_score = [d for d in documents if d.get("score", 0) >= 0.8]
    mid_score = [d for d in documents if 0.5 <= d.get("score", 0) < 0.8]
    low_score = [d for d in documents if d.get("score", 0) < 0.5]

    # 重新排序: 高分前2个 + 中分 + 高分剩余 + 低分
    ordered = high_score[:2] + mid_score + high_score[2:] + low_score

    for idx, doc in enumerate(ordered[:max_documents], 1):
        title = str(doc.get("title", "")).strip()
        snippet = str(doc.get("snippet", "")).strip()
        channel = str(doc.get("channel", "")).strip().lower()
        score = float(doc.get("score", 0.0))
        hop = doc.get("hop")
        source = str(doc.get("source", "")).strip()

        if not title:
            continue

        # 构建元数据标签
        meta_tags = _build_metadata_tags(
            channel=channel,
            score=score,
            hop=hop,
            source=source,
        )

        # 格式化文档条目
        meta_str = " ".join(meta_tags) if meta_tags else ""
        context_parts.append(f"[{idx}] {title} {meta_str}")

        # 添加摘要片段
        if include_snippets and snippet:
            truncated_snippet = snippet[:snippet_max_length]
            if len(snippet) > snippet_max_length:
                truncated_snippet += "..."
            context_parts.append(f"    {truncated_snippet}")

        context_parts.append("")  # 空行分隔

    # 添加图谱意图引导
    if graph_intent and graph_intent.upper() in GRAPH_INTENT_GUIDANCE:
        guidance = GRAPH_INTENT_GUIDANCE[graph_intent.upper()]
        context_parts.append(f"### 答题引导\n{guidance}\n")

    return "\n".join(context_parts)


def _build_metadata_tags(
    *,
    channel: str,
    score: float,
    hop: int | None,
    source: str,
) -> list[str]:
    """构建元数据标签"""
    tags = []

    # 通道类型标签
    if channel == "phrase" or channel == "grep":
        tags.append("🎯精确匹配")
    elif channel == "graph":
        if hop == 1:
            tags.append("📊图谱直接关联")
        elif hop == 2:
            tags.append("📊图谱间接关联")
        else:
            tags.append("📊图谱相关")
    elif channel == "web":
        tags.append("🌐联网搜索")
    elif channel == "vector":
        tags.append("🔍语义检索")
    elif channel == "hybrid":
        tags.append("🔀混合检索")

    # 相关性评分标签
    if score >= 0.8:
        tags.append("⭐高相关")
    elif score >= 0.5:
        tags.append("✓相关")

    # 来源标签（图谱专用）
    if "seed" in source.lower():
        tags.append("🌱种子概念")
    elif "direct" in source.lower():
        tags.append("📍直接证据")

    return tags


def format_graph_evidence_nodes(
    *,
    nodes: list[dict[str, Any]],
    intent: str,
    max_nodes: int = 8,
) -> str:
    """
    格式化图谱证据节点

    Args:
        nodes: 图谱节点列表
        intent: 图谱意图类型
        max_nodes: 最多显示的节点数

    Returns:
        格式化的图谱证据字符串
    """
    if not nodes:
        return ""

    parts = ["## 图谱关联概念\n"]

    # 按跳数和评分分组
    by_hop: dict[str, list[dict]] = {}
    for node in nodes[:max_nodes]:
        hop = node.get("hop")
        key = f"{hop}hop" if hop else "other"
        by_hop.setdefault(key, []).append(node)

    # 输出各跳数的节点
    for hop_key in ["1hop", "2hop", "other"]:
        if hop_key not in by_hop:
            continue

        hop_nodes = by_hop[hop_key]
        hop_label = {
            "1hop": "一跳关联",
            "2hop": "二跳扩展",
            "other": "图谱相关",
        }[hop_key]

        parts.append(f"### {hop_label}\n")
        for node in hop_nodes:
            title = node.get("title", "")
            score = node.get("score")

            score_str = f"(分数: {score:.3f})" if score else ""
            parts.append(f"- {title} {score_str}")
        parts.append("")

    # 添加意图引导
    if intent in GRAPH_INTENT_GUIDANCE:
        guidance = GRAPH_INTENT_GUIDANCE[intent]
        parts.append(f"### 答题策略\n{guidance}\n")

    return "\n".join(parts)
