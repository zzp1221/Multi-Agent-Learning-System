from src.ai_modules.retrieval.query_classifier import QueryClassifier


def test_query_classifier_routes_small_talk_without_retrieval() -> None:
    result = QueryClassifier().classify({"query": "你好"})

    assert result.query_type == "SMALL_TALK"
    assert result.retrieval_strategy == "NONE"


def test_query_classifier_routes_answer_previous_from_dialogue_state() -> None:
    result = QueryClassifier().classify(
        {
            "query": "我选 A",
            "messages": [
                {"role": "assistant", "content": "这道题你觉得答案是什么？"},
                {"role": "user", "content": "我选 A"},
            ],
        }
    )

    assert result.query_type == "ANSWER_PREVIOUS"
    assert result.retrieval_strategy == "CONTEXT_ONLY"


def test_query_classifier_routes_new_concept_to_local_hybrid() -> None:
    result = QueryClassifier().classify({"query": "Java volatile 是什么"})

    assert result.query_type == "NEW_CONCEPT"
    assert result.retrieval_strategy == "LOCAL_HYBRID"


def test_query_classifier_routes_plain_concept_explanation_to_grep_first() -> None:
    result = QueryClassifier().classify(
        {
            "query": "请解释“安全策略与等保”的核心概念、典型场景和常见误区。（参考方向：安全策略、等级保护、等保2.0、合规）"
        }
    )

    assert result.query_type == "NEW_CONCEPT"
    assert result.retrieval_strategy == "LOCAL_GREP_FIRST"
    assert result.graph_intent is None
    assert result.reason == "plain_concept_explanation"


def test_query_classifier_keeps_multi_version_concept_local() -> None:
    result = QueryClassifier().classify(
        {
            "query": "请解释“MVCC多版本并发控制详解”的核心概念、典型场景和常见误区。（参考方向：数据库、MVCC、事务、并发控制）"
        }
    )

    assert result.query_type == "NEW_CONCEPT"
    assert result.retrieval_strategy == "LOCAL_GREP_FIRST"
    assert result.graph_intent is None


def test_query_classifier_routes_comparison_named_concept_explanation_to_grep_first() -> None:
    result = QueryClassifier().classify(
        {
            "query": "请解释“GCC与LLVM编译器架构对比”的核心概念、典型场景和常见误区。（参考方向：编译原理、GCC、LLVM、编译器架构）"
        }
    )

    assert result.query_type == "NEW_CONCEPT"
    assert result.retrieval_strategy == "LOCAL_GREP_FIRST"
    assert result.graph_intent is None


def test_query_classifier_keeps_explicit_comparison_question_as_graph_intent() -> None:
    result = QueryClassifier().classify({"query": "请比较GCC与LLVM编译器架构的区别和联系"})

    assert result.query_type == "COMPARISON"
    assert result.retrieval_strategy == "LOCAL_HYBRID"
    assert result.graph_intent == "COMPARISON"


def test_query_classifier_routes_error_debug_to_local_hybrid() -> None:
    result = QueryClassifier().classify({"query": "NullPointerException 报错怎么办"})

    assert result.query_type == "ERROR_DEBUG"
    assert result.retrieval_strategy == "LOCAL_HYBRID"


def test_query_classifier_routes_current_info_to_web_augmented() -> None:
    result = QueryClassifier().classify({"query": "今天最新版本有什么变化"})

    assert result.query_type == "CURRENT_INFO"
    assert result.retrieval_strategy == "WEB_AUGMENTED"


def test_query_classifier_routes_image_question() -> None:
    result = QueryClassifier().classify({"query": "看图讲一下这道题", "imageUrls": ["mock://image.png"]})

    assert result.query_type == "IMAGE_QUESTION"
    assert result.retrieval_strategy == "LOCAL_HYBRID"


def test_query_classifier_keeps_deep_mode_as_quality_signal() -> None:
    result = QueryClassifier().classify({"query": "分析所有边界", "reasoningMode": "DEEP"})

    assert result.query_type == "NEW_CONCEPT"
    assert result.retrieval_strategy == "DEEP_EVIDENCE"
    assert "deep_quality_mode" in result.reason


def test_query_classifier_prefers_mechanism_over_comparison_and_path_terms() -> None:
    result = QueryClassifier().classify(
        {
            "query": (
                "请说明「代码优化综合」在机制落地时如何连接「GCC与LLVM编译器架构对比、"
                "寄存器分配」。关系焦点：把代码优化、编译器架构和寄存器分配连成后端优化链路。"
            )
        }
    )

    assert result.graph_intent == "MECHANISM_APPLICATION"


def test_query_classifier_prefers_common_mistake_over_path_terms() -> None:
    result = QueryClassifier().classify(
        {
            "query": (
                "请围绕常见误区，说明「恶意软件分类与防护」与「操作系统安全机制、"
                "安全策略与等保」为什么容易被混淆或遗漏。"
            )
        }
    )

    assert result.graph_intent == "COMMON_MISTAKE"


def test_query_classifier_prefers_summary_over_path_terms() -> None:
    result = QueryClassifier().classify(
        {
            "query": (
                "请总结「敏捷开发」所在知识簇，并说明它与「Scrum与Kanban、云原生架构」"
                "的协作关系。"
            )
        }
    )

    assert result.graph_intent == "COMMUNITY_SUMMARY"


def test_query_classifier_does_not_treat_generic_path_as_prerequisite() -> None:
    result = QueryClassifier().classify(
        {
            "query": (
                "请从知识图谱关系角度说明「信息论基础」与「消息认证码MAC与HMAC、"
                "区块链密码学基础」之间的多跳联系，并串成安全基础关系路径。"
            )
        }
    )

    assert result.graph_intent == "CROSS_LAYER_RELATION"


def test_query_classifier_keeps_web_search_as_evidence_toggle() -> None:
    result = QueryClassifier().classify(
        {"query": "Will TS replace JS?", "webSearchEnabled": True}
    )

    assert result.query_type == "NEW_CONCEPT"
    assert result.retrieval_strategy == "LOCAL_HYBRID"
    assert result.reason == "question_signal"


def test_query_classifier_routes_latest_version_to_web_augmented() -> None:
    result = QueryClassifier().classify({"query": "What changed in the latest TypeScript version?"})

    assert result.query_type == "CURRENT_INFO"
    assert result.retrieval_strategy == "WEB_AUGMENTED"


def test_query_classifier_keeps_graph_relation_template_over_comparison_terms() -> None:
    result = QueryClassifier().classify(
        {
            "query": (
                "请从知识图谱关系角度说明「IPv4与IPv6全面对比」与「TCP三次握手详解、"
                "HTTP协议详解」之间的多跳联系。"
            )
        }
    )

    assert result.graph_intent == "CROSS_LAYER_RELATION"


def test_query_classifier_keeps_graph_relation_template_for_graph_theory_links() -> None:
    result = QueryClassifier().classify(
        {
            "query": (
                "请从知识图谱关系角度说明「图着色与色多项式」与"
                "「欧拉图与哈密顿图、NP完全性与归约」之间的多跳联系。"
            )
        }
    )

    assert result.graph_intent == "CROSS_LAYER_RELATION"


def test_query_classifier_keeps_combinatorics_bridge_as_multi_hop() -> None:
    result = QueryClassifier().classify(
        {
            "query": (
                "请从知识图谱关系角度说明「鸽巢原理及其推广」与"
                "「图着色与色多项式、欧拉图与哈密顿图」之间的多跳联系。"
                "关系焦点：从组合计数思想连接图着色、欧拉图与哈密顿图。"
            )
        }
    )

    assert result.graph_intent == "MULTI_HOP_RELATION"


def test_query_classifier_keeps_prerequisite_template_over_mechanism_terms() -> None:
    result = QueryClassifier().classify(
        {
            "query": (
                "请构建一条学习路径，说明「Peterson算法」如何依赖或通向"
                "「经典同步问题-生产者消费者、管程」。"
            )
        }
    )

    assert result.graph_intent == "PREREQUISITE_PATH"


def test_query_classifier_marks_prerequisite_dependency_relation() -> None:
    result = QueryClassifier().classify({"query": "请说明NFA到DFA最小化的前置依赖关系"})

    assert result.retrieval_strategy == "LOCAL_HYBRID"
    assert result.graph_intent == "PREREQUISITE_PATH"


def test_query_classifier_keeps_web_strategy_for_current_graph_question() -> None:
    result = QueryClassifier().classify({"query": "今天最新的GraphRAG和当前RAG关系是什么"})

    assert result.query_type == "CURRENT_INFO"
    assert result.retrieval_strategy == "WEB_AUGMENTED"
    assert result.graph_intent == "CROSS_LAYER_RELATION"
