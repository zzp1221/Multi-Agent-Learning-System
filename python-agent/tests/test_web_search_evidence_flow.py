from src.ai_modules.agents.deep_reasoning_planner import DeepReasoningPlanner
from src.ai_modules.agents.tutor_agent import TutorAgent
from src.ai_modules.llms import RuleBasedTutorLLM
from src.ai_modules.memory import InMemoryConversationSummaryStore


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


def test_deep_reasoning_planner_prompt_payload_includes_web_evidence() -> None:
    planner = DeepReasoningPlanner(generator=object())
    params = {
        "query": "Will TS replace JS?",
        "webSearchEnabled": True,
        "retrievalResult": {
            "documents": [
                {
                    "title": "TypeScript and JavaScript",
                    "slug": "https://example.com/ts-js",
                    "channel": "web",
                    "evidence": "TypeScript extends JavaScript.",
                    "url": "https://example.com/ts-js",
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

    payload = planner._build_prompt_payload(params)

    assert payload["webSearchEnabled"] is True
    assert payload["retrievalResult"]["documents"][0]["url"] == "https://example.com/ts-js"
    assert payload["webRetrievalResult"]["results"][0]["url"] == "https://example.com/ts-js"
    assert payload["externalResources"][0]["url"] == "https://example.com/ts-js"
