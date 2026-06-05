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
