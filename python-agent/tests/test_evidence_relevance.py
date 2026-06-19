from src.ai_modules.retrieval.evidence_relevance import select_relevant_evidence


def test_select_relevant_evidence_keeps_query_matching_documents() -> None:
    documents = [
        {
            "title": "AVL树",
            "evidence": "AVL树会通过旋转保持二叉搜索树平衡。",
            "score": 0.82,
        },
        {
            "title": "线程调度笔记",
            "evidence": "这份资料讨论线程休眠和任务调度。",
            "score": 0.95,
        },
    ]

    selection = select_relevant_evidence(query="什么是AVL树", documents=documents)

    assert selection.adopted == [documents[0]]
    assert selection.discarded_count == 1


def test_select_relevant_evidence_handles_chinese_terms_without_domain_blacklists() -> None:
    documents = [
        {
            "title": "注意力机制",
            "evidence": "自注意力会计算序列内部 token 之间的相关性。",
            "score": 0.76,
        },
        {
            "title": "并发控制基础",
            "evidence": "这份资料讨论锁和线程调度。",
            "score": 0.91,
        },
    ]

    selection = select_relevant_evidence(query="注意力机制是什么", documents=documents)

    assert selection.adopted == [documents[0]]
    assert selection.discarded_count == 1
