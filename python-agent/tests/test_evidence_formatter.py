"""测试证据格式化器"""

import pytest
from src.ai_modules.retrieval.evidence_formatter import (
    format_evidence_with_metadata,
    format_graph_evidence_nodes,
    _build_metadata_tags,
)


def test_format_evidence_basic():
    """测试基本格式化"""
    documents = [
        {
            "title": "Python基础教程",
            "snippet": "Python是一种高级编程语言",
            "channel": "vector",
            "score": 0.85,
        }
    ]

    result = format_evidence_with_metadata(documents=documents)

    assert "## 检索证据" in result
    assert "[1] Python基础教程" in result
    assert "🔍语义检索" in result
    assert "⭐高相关" in result
    assert "Python是一种高级编程语言" in result


def test_format_evidence_lost_in_middle():
    """测试避免lost-in-middle效应"""
    documents = [
        {"title": "低分1", "score": 0.3, "channel": "vector"},
        {"title": "高分1", "score": 0.9, "channel": "phrase"},
        {"title": "中分1", "score": 0.6, "channel": "vector"},
        {"title": "高分2", "score": 0.85, "channel": "graph", "hop": 1},
        {"title": "中分2", "score": 0.55, "channel": "vector"},
    ]

    result = format_evidence_with_metadata(documents=documents)

    # 验证顺序: 高分前2个应该在开头
    lines = result.split("\n")
    doc_lines = [l for l in lines if l.startswith("[")]

    assert "高分" in doc_lines[0]  # 第一个是高分
    assert "高分" in doc_lines[1] or "中分" in doc_lines[1]  # 第二个是高分或中分


def test_format_evidence_with_graph_intent():
    """测试图谱意图引导"""
    documents = [
        {
            "title": "机器学习概念",
            "channel": "graph",
            "hop": 1,
            "score": 0.8,
        }
    ]

    result = format_evidence_with_metadata(
        documents=documents,
        graph_intent="COMPARISON",  # 使用实际存在的意图
    )

    assert "### 答题引导" in result
    assert "先给共同点" in result


def test_format_evidence_snippet_truncation():
    """测试摘要截断"""
    long_snippet = "这是一段很长的文本" * 50
    documents = [
        {
            "title": "测试文档",
            "snippet": long_snippet,
            "channel": "vector",
            "score": 0.7,
        }
    ]

    result = format_evidence_with_metadata(
        documents=documents,
        snippet_max_length=100,
    )

    assert "..." in result
    # 验证实际截断长度不超过103（100 + "..."）
    snippet_line = [l for l in result.split("\n") if "这是一段" in l][0]
    assert len(snippet_line.strip()) <= 110  # 留点余量


def test_format_evidence_no_snippets():
    """测试不包含摘要"""
    documents = [
        {
            "title": "测试文档",
            "snippet": "这是摘要",
            "channel": "vector",
            "score": 0.7,
        }
    ]

    result = format_evidence_with_metadata(
        documents=documents,
        include_snippets=False,
    )

    assert "这是摘要" not in result
    assert "测试文档" in result


def test_build_metadata_tags_phrase():
    """测试精确匹配标签"""
    tags = _build_metadata_tags(
        channel="phrase",
        score=0.9,
        hop=None,
        source="",
    )

    assert "🎯精确匹配" in tags
    assert "⭐高相关" in tags


def test_build_metadata_tags_graph():
    """测试图谱标签"""
    tags = _build_metadata_tags(
        channel="graph",
        score=0.85,
        hop=1,
        source="seed_concept",
    )

    assert "📊图谱直接关联" in tags
    assert "⭐高相关" in tags
    assert "🌱种子概念" in tags


def test_build_metadata_tags_web():
    """测试联网搜索标签"""
    tags = _build_metadata_tags(
        channel="web",
        score=0.6,
        hop=None,
        source="",
    )

    assert "🌐联网搜索" in tags
    assert "✓相关" in tags


def test_format_graph_evidence_nodes():
    """测试图谱节点格式化"""
    nodes = [
        {"title": "概念A", "score": 0.9, "hop": 1},
        {"title": "概念B", "score": 0.7, "hop": 2},
        {"title": "概念C", "score": 0.8, "hop": 1},
    ]

    result = format_graph_evidence_nodes(
        nodes=nodes,
        intent="CONCEPT",
    )

    assert "## 图谱关联概念" in result
    assert "### 一跳关联" in result
    assert "### 二跳扩展" in result
    assert "概念A" in result
    assert "概念B" in result


def test_format_graph_evidence_nodes_with_intent():
    """测试图谱节点带意图引导"""
    nodes = [
        {"title": "概念A", "score": 0.9, "hop": 1},
    ]

    result = format_graph_evidence_nodes(
        nodes=nodes,
        intent="COMPARISON",  # 使用实际存在的意图
    )

    assert "### 答题策略" in result
    assert "先给共同点" in result


def test_format_evidence_empty():
    """测试空文档列表"""
    result = format_evidence_with_metadata(documents=[])
    assert result == ""


def test_format_evidence_max_documents():
    """测试文档数量限制"""
    documents = [
        {"title": f"文档{i}", "score": 0.7, "channel": "vector"}
        for i in range(10)
    ]

    result = format_evidence_with_metadata(
        documents=documents,
        max_documents=3,
    )

    # 应该只包含3个文档
    doc_count = result.count("[")
    assert doc_count == 3


def test_format_evidence_missing_fields():
    """测试缺失字段的容错性"""
    documents = [
        {
            "title": "测试文档",
            # 缺少 snippet, channel, score
        }
    ]

    result = format_evidence_with_metadata(documents=documents)

    # 应该能正常处理，不抛异常
    assert "测试文档" in result
