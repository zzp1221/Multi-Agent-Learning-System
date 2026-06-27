"""测试语义重排序器"""

import pytest
from src.ai_modules.retrieval.semantic_reranker import SemanticReranker


@pytest.fixture
def sample_documents():
    """示例文档列表"""
    return [
        {
            "title": "Python 列表推导式",
            "snippet": "列表推导式是一种简洁的创建列表的方法",
            "score": 0.6,
        },
        {
            "title": "Python 生成器表达式",
            "snippet": "生成器表达式类似列表推导式，但返回生成器",
            "score": 0.7,
        },
        {
            "title": "Python 字典推导式",
            "snippet": "字典推导式用于创建字典",
            "score": 0.5,
        },
        {
            "title": "Java Lambda 表达式",
            "snippet": "Lambda 表达式是 Java 8 引入的特性",
            "score": 0.4,
        },
        {
            "title": "Python map 函数",
            "snippet": "map 函数用于映射操作",
            "score": 0.55,
        },
    ]


def test_reranker_fallback_without_model(sample_documents):
    """测试无模型时回退到启发式排序"""
    reranker = SemanticReranker(use_api=False)

    # 不初始化模型，直接调用rerank
    result = reranker.rerank(
        query="Python 列表推导",
        documents=sample_documents,
        top_k=3,
    )

    # 应该返回按启发式score排序的前3个
    assert len(result) == 3
    assert result[0]["score"] == 0.7  # 生成器表达式


def test_reranker_returns_all_when_fewer_than_topk(sample_documents):
    """测试文档数量少于top_k时返回全部"""
    reranker = SemanticReranker()

    result = reranker.rerank(
        query="Python",
        documents=sample_documents[:2],
        top_k=5,
    )

    assert len(result) == 2


def test_reranker_handles_empty_documents():
    """测试空文档列表"""
    reranker = SemanticReranker()

    result = reranker.rerank(
        query="test",
        documents=[],
        top_k=5,
    )

    assert result == []


def test_reranker_coarse_filtering(sample_documents):
    """测试粗排阶段正确保留top-N"""
    reranker = SemanticReranker()

    result = reranker.rerank(
        query="Python",
        documents=sample_documents,
        top_k=2,
        coarse_top_n=3,  # 粗排只保留前3个
    )

    # 由于没有模型，返回粗排结果
    assert len(result) == 2


def test_reranker_preserves_document_fields(sample_documents):
    """测试重排序保留文档原始字段"""
    reranker = SemanticReranker()

    result = reranker.rerank(
        query="Python",
        documents=sample_documents,
        top_k=3,
    )

    for doc in result:
        assert "title" in doc
        assert "snippet" in doc
        assert "score" in doc


def test_reranker_lazy_initialization():
    """测试延迟初始化"""
    reranker = SemanticReranker()

    # 创建时不应该初始化
    assert not reranker._initialized

    # 第一次调用rerank时，如果文档数<=top_k，不会触发初始化
    # 使用更多文档来触发初始化逻辑
    docs = [{"title": f"doc{i}", "score": 0.5} for i in range(10)]
    reranker.rerank(query="test", documents=docs, top_k=3)

    # 初始化应该被触发（即使模型加载失败）
    assert reranker._initialized


def test_reranker_blend_weight_parameter(sample_documents):
    """测试混合权重参数被接受"""
    reranker = SemanticReranker()

    # 即使没有模型，blend_weight参数也应该被接受
    result = reranker.rerank(
        query="Python",
        documents=sample_documents,
        top_k=3,
        blend_weight=0.5,
    )

    assert len(result) <= 3
