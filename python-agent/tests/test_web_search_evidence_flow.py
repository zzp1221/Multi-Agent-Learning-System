from src.ai_modules.agents.retrieval_agent import RetrievalAgent
from src.ai_modules.agents.tutor_agent import TutorAgent
from src.ai_modules.llms import RuleBasedTutorLLM
from src.ai_modules.memory import InMemoryConversationSummaryStore
from src.ai_modules.models import RetrievalResponse


def test_tutor_deep_web_context_includes_full_retrieval_payloads() -> None:
    tutor = TutorAgent(
        summary_store=InMemoryConversationSummaryStore(),
        llm_client=RuleBasedTutorLLM(),
    )
    params = {
        "query": "Will TS replace JS?",
        "rewrittenQuery": "Will TS replace JS?",
        "webSearchEnabled": True,
        "reasoningMode": "DEEP",
        "retrievalResult": {
            "documents": [
                {
                    "title": "TypeScript and JavaScript",
                    "channel": "web",
                    "evidence": "TypeScript extends JavaScript with static typing.",
                    "url": "https://example.com/ts-js",
                    "sourceTitle": "Example",
                    "score": 0.91,
                }
            ],
            "sourcesSummary": "web evidence for TS and JS",
        },
        "webRetrievalResult": {
            "enabled": True,
            "query": "Will TS replace JS?",
            "results": [
                (
                    "https://example.com/ts-js",
                    "TypeScript and JavaScript",
                    0.91,
                    {"url": "https://example.com/ts-js", "snippet": "TS extends JS."},
                )
            ],
        },
    }

    evidence = tutor._tool_read_retrieval_evidence(tool_input={}, params=params)
    context = tutor._build_enriched_message(
        user_query="Will TS replace JS?",
        memory={},
        context={},
        evidence=evidence,
        profile={},
        image_analysis={},
        recent_dialogue={},
        input_mode="clear_question",
        params=params,
    )

    assert evidence["retrievalResult"] == params["retrievalResult"]
    assert evidence["webRetrievalResult"] == params["webRetrievalResult"]
    assert evidence["externalResources"][0]["url"] == "https://example.com/ts-js"
    assert "深度思考和联网搜索同时开启" in context
    assert "webRetrievalResult" in context
    assert "不能覆盖或替代用户的实际问题" in context


def test_tutor_uses_only_adopted_external_sources_for_answer_context() -> None:
    tutor = TutorAgent(
        summary_store=InMemoryConversationSummaryStore(),
        llm_client=RuleBasedTutorLLM(),
    )
    params = {
        "query": "Will TS replace JS?",
        "webSearchEnabled": True,
        "webRetrievalResult": {"enabled": True, "query": "Will TS replace JS?"},
        "adoptedExternalSources": [
            {
                "citationId": "S1",
                "id": "ext-1",
                "title": "TypeScript and JavaScript",
                "url": "https://example.com/ts-js",
                "snippet": "TypeScript extends JavaScript.",
            }
        ],
        "ignoredExternalSources": [
            {
                "title": "Unrelated runtime article",
                "url": "https://example.com/runtime",
                "reason": "相关性不足或未进入融合结果",
            }
        ],
        "evidenceIds": ["ext-1"],
        "externalUrls": ["https://example.com/ts-js"],
        "retrievalResult": {
            "documents": [
                {
                    "title": "Ignored duplicate should not be the authority",
                    "channel": "web",
                    "evidence": "Other content.",
                    "url": "https://example.com/runtime",
                    "score": 0.99,
                }
            ]
        },
    }

    evidence = tutor._tool_read_retrieval_evidence(tool_input={}, params=params)
    context = tutor._build_enriched_message(
        user_query="Will TS replace JS?",
        memory={},
        context={},
        evidence=evidence,
        profile={},
        image_analysis={},
        recent_dialogue={},
        input_mode="clear_question",
        params=params,
    )
    reasoning_text = "\n".join(tutor._build_answer_reasoning_chunks(user_query="Will TS replace JS?", params=params))

    assert [item["url"] for item in evidence["externalResources"]] == ["https://example.com/ts-js"]
    assert evidence["externalResources"][0]["citationId"] == "S1"
    assert "adoptedExternalSources 中的来源" in context
    assert "[S1] TypeScript and JavaScript (https://example.com/ts-js)" in context
    assert "依据对应" in context
    assert "Unrelated runtime article [https://example.com/runtime]：相关性不足或未进入融合结果" in context
    assert "我先识别问题意图" in reasoning_text
    assert "[S1]" in reasoning_text
    assert "https://example.com/ts-js" in reasoning_text
    assert "Unrelated runtime article：相关性不足或未进入融合结果" in reasoning_text
    assert "https://example.com/runtime" not in reasoning_text


def test_tutor_answer_reasoning_declares_no_adopted_external_source() -> None:
    tutor = TutorAgent(
        summary_store=InMemoryConversationSummaryStore(),
        llm_client=RuleBasedTutorLLM(),
    )
    params = {
        "query": "Will TS replace JS?",
        "webSearchEnabled": True,
        "webRetrievalResult": {"enabled": True, "query": "Will TS replace JS?", "results": []},
        "adoptedExternalSources": [],
        "ignoredExternalSources": [],
        "retrievalResult": {"documents": []},
    }

    reasoning_text = "\n".join(tutor._build_answer_reasoning_chunks(user_query="Will TS replace JS?", params=params))

    assert "联网证据：未采用外部来源。" in reasoning_text
    assert "最后自检" in reasoning_text
    assert "http" not in reasoning_text


def test_retrieval_agent_writes_shared_external_evidence_contract() -> None:
    agent = RetrievalAgent()
    params = {
        "webRetrievalResult": {
            "enabled": True,
            "query": "Will TS replace JS?",
            "results": [
                (
                    "https://example.com/ts-js",
                    "TypeScript and JavaScript",
                    0.91,
                    {"url": "https://example.com/ts-js", "snippet": "TypeScript extends JavaScript."},
                ),
                (
                    "https://example.com/runtime",
                    "Unrelated runtime article",
                    0.4,
                    {"url": "https://example.com/runtime", "snippet": "Runtime internals."},
                ),
            ],
        }
    }
    retrieval_response = RetrievalResponse.model_validate(
        {
            "query": "Will TS replace JS?",
            "rewrittenQuery": "Will TS replace JS?",
            "keywords": ["TypeScript", "JavaScript"],
            "documents": [
                {
                    "slug": "https://example.com/ts-js",
                    "title": "TypeScript and JavaScript",
                    "score": 0.91,
                    "channel": "web",
                    "evidence": "TypeScript extends JavaScript.",
                    "url": "https://example.com/ts-js",
                }
            ],
            "sourcesSummary": "web evidence",
        }
    )

    contract = agent._build_external_evidence_contract(
        query="Will TS replace JS?",
        retrieval_response=retrieval_response,
        params=params,
    )

    assert contract["adoptedExternalSources"][0]["url"] == "https://example.com/ts-js"
    assert contract["adoptedExternalSources"][0]["citationId"] == "S1"
    assert contract["evidenceIds"] == ["ext-1"]
    assert contract["externalUrls"] == ["https://example.com/ts-js"]
    assert contract["ignoredExternalSources"][0]["url"] == "https://example.com/runtime"


def test_tutor_web_context_mentions_fallback_when_external_resources_empty() -> None:
    tutor = TutorAgent(
        summary_store=InMemoryConversationSummaryStore(),
        llm_client=RuleBasedTutorLLM(),
    )
    evidence = {
        "webSearchEnabled": True,
        "externalResources": [],
        "documents": [],
        "retrievalResult": {"documents": [], "sourcesSummary": ""},
        "webRetrievalResult": {"enabled": True, "query": "Will TS replace JS?", "results": []},
    }

    context = tutor._build_enriched_message(
        user_query="Will TS replace JS?",
        memory={},
        context={},
        evidence=evidence,
        profile={},
        image_analysis={},
        recent_dialogue={},
        input_mode="clear_question",
        params={"webSearchEnabled": True},
    )

    assert "未采用到足够相关的联网证据" in context
    assert "回答当前问题" in context




def test_tutor_appends_web_citation_table_when_answer_omits_it() -> None:
    tutor = TutorAgent(
        summary_store=InMemoryConversationSummaryStore(),
        llm_client=RuleBasedTutorLLM(),
    )
    params = {
        "webSearchEnabled": True,
        "adoptedExternalSources": [
            {
                "citationId": "S1",
                "id": "ext-1",
                "title": "TypeScript and JavaScript",
                "url": "https://example.com/ts-js",
                "snippet": "TypeScript extends JavaScript.",
            }
        ],
    }

    answer = tutor._finalize_web_cited_answer("TypeScript will not simply replace JavaScript.", params)

    assert "[S1] TypeScript and JavaScript" in answer
    assert "依据对应" in answer
    assert "| 结论 | 来源 |" in answer
