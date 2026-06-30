from src.ai_modules.retrieval.evidence_relevance import (
    EVIDENCE_STATE_HIGH,
    EVIDENCE_STATE_LOW,
    EVIDENCE_STATE_PARTIAL,
    extract_query_terms,
    select_relevant_evidence,
)


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
    assert selection.evidence_state == EVIDENCE_STATE_HIGH


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


def test_select_relevant_evidence_matches_generic_titlecase_acronyms() -> None:
    documents = [
        {
            "title": "TypeScript and JavaScript",
            "evidence": "TypeScript adds static typing on top of JavaScript.",
            "score": 0.82,
        },
        {
            "title": "Java并发JUC",
            "evidence": "JUC 包含锁、线程池和 CAS。",
            "score": 0.91,
        },
    ]

    selection = select_relevant_evidence(query="帮我解释一下 TS 和 JS 的区别，至少500字", documents=documents)

    assert selection.adopted[0] == documents[0]
    assert selection.evidence_state in {EVIDENCE_STATE_HIGH, EVIDENCE_STATE_PARTIAL}
    assert "至少500字" not in extract_query_terms("TS 和 JS 的区别，至少500字")


def test_select_relevant_evidence_soft_falls_back_to_low_confidence_local_context() -> None:
    documents = [
        {"title": "线程调度笔记", "evidence": "线程休眠和任务调度。", "score": 0.95},
    ]

    selection = select_relevant_evidence(query="什么是SBOM", documents=documents)

    assert selection.adopted == documents
    assert selection.evidence_state == EVIDENCE_STATE_LOW
    assert selection.fallback_low_count == 1


def test_select_relevant_evidence_can_disable_low_confidence_fallback() -> None:
    documents = [
        {"title": "线程调度笔记", "evidence": "线程休眠和任务调度。", "score": 0.95},
    ]

    selection = select_relevant_evidence(query="什么是SBOM", documents=documents, allow_low_fallback=False)

    assert selection.adopted == []
