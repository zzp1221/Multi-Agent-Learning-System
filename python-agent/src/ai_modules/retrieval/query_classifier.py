"""Local query classifier for choosing the cheapest safe tutoring route."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from pathlib import Path
import re
from typing import Any

LOGGER = logging.getLogger(__name__)

QUERY_TYPE_SMALL_TALK = "SMALL_TALK"
QUERY_TYPE_FOLLOW_UP = "FOLLOW_UP"
QUERY_TYPE_ANSWER_PREVIOUS = "ANSWER_PREVIOUS"
QUERY_TYPE_NEW_CONCEPT = "NEW_CONCEPT"
QUERY_TYPE_COMPARISON = "COMPARISON"
QUERY_TYPE_PROCEDURAL = "PROCEDURAL"
QUERY_TYPE_ERROR_DEBUG = "ERROR_DEBUG"
QUERY_TYPE_CURRENT_INFO = "CURRENT_INFO"
QUERY_TYPE_IMAGE_QUESTION = "IMAGE_QUESTION"
QUERY_TYPE_DEEP_REASONING = "DEEP_REASONING"

RETRIEVAL_NONE = "NONE"
RETRIEVAL_CONTEXT_ONLY = "CONTEXT_ONLY"
RETRIEVAL_LOCAL_GREP_FIRST = "LOCAL_GREP_FIRST"
RETRIEVAL_LOCAL_HYBRID = "LOCAL_HYBRID"
RETRIEVAL_WEB_AUGMENTED = "WEB_AUGMENTED"
RETRIEVAL_DEEP_EVIDENCE = "DEEP_EVIDENCE"

GRAPH_INTENT_NONE = None
GRAPH_INTENT_CROSS_LAYER_RELATION = "CROSS_LAYER_RELATION"
GRAPH_INTENT_MULTI_HOP_RELATION = "MULTI_HOP_RELATION"
GRAPH_INTENT_COMPARISON = "COMPARISON"
GRAPH_INTENT_PREREQUISITE_PATH = "PREREQUISITE_PATH"
GRAPH_INTENT_COMMUNITY_SUMMARY = "COMMUNITY_SUMMARY"
GRAPH_INTENT_MECHANISM_APPLICATION = "MECHANISM_APPLICATION"
GRAPH_INTENT_COMMON_MISTAKE = "COMMON_MISTAKE"


@dataclass(frozen=True)
class QueryClassification:
    """Decision payload shared by supervisor, retrieval, and tutor agents."""

    query_type: str
    retrieval_strategy: str
    confidence: float
    reason: str
    graph_intent: str | None = None


class QueryClassifier:
    """Deterministic, config-driven classifier for one tutoring turn."""

    def __init__(self, rules_path: Path | None = None) -> None:
        self.rules_path = rules_path or Path(__file__).with_name("query_classifier_rules.json")
        self.rules = self._load_rules()

    @property
    def low_confidence_threshold(self) -> float:
        return float(self.rules.get("lowConfidenceThreshold", 0.55))

    def classify(self, params: dict[str, Any]) -> QueryClassification:
        query = self._extract_query(params)
        normalized = self._normalize(query)
        lowered = query.lower()

        if self._is_deep_reasoning(params, lowered):
            return self._decision(
                QUERY_TYPE_DEEP_REASONING,
                RETRIEVAL_DEEP_EVIDENCE,
                0.99,
                "explicit_deep_reasoning",
            )
        if self._has_image(params):
            return self._decision(
                QUERY_TYPE_IMAGE_QUESTION,
                RETRIEVAL_LOCAL_HYBRID,
                0.92,
                "image_input",
            )
        if not normalized:
            return self._decision(QUERY_TYPE_SMALL_TALK, RETRIEVAL_NONE, 0.9, "empty_input")
        if self._is_small_talk(normalized):
            return self._decision(QUERY_TYPE_SMALL_TALK, RETRIEVAL_NONE, 0.95, "small_talk_rule")
        if self._is_answer_to_previous_question(params, query):
            return self._decision(
                QUERY_TYPE_ANSWER_PREVIOUS,
                RETRIEVAL_CONTEXT_ONLY,
                0.86,
                "answer_previous_question",
            )
        if self._contains_any(lowered, "currentInfoTerms") or self._web_search_enabled(params):
            return self._decision(
                QUERY_TYPE_CURRENT_INFO,
                RETRIEVAL_WEB_AUGMENTED,
                0.84,
                "current_info_or_web",
            )
        if self._looks_like_error_or_code(lowered):
            return self._decision(
                QUERY_TYPE_ERROR_DEBUG,
                RETRIEVAL_LOCAL_HYBRID,
                0.86,
                "error_debug_signal",
            )
        if self._contains_any(lowered, "comparisonTerms"):
            return self._decision(
                QUERY_TYPE_COMPARISON,
                RETRIEVAL_LOCAL_HYBRID,
                0.82,
                "comparison_signal",
                graph_intent=GRAPH_INTENT_COMPARISON,
            )
        if self._contains_any(lowered, "proceduralTerms"):
            return self._decision(
                QUERY_TYPE_PROCEDURAL,
                RETRIEVAL_LOCAL_GREP_FIRST,
                0.8,
                "procedural_signal",
                graph_intent=self._detect_graph_intent(lowered),
            )
        graph_intent = self._detect_graph_intent(lowered)
        if graph_intent:
            return self._decision(
                QUERY_TYPE_NEW_CONCEPT,
                RETRIEVAL_LOCAL_HYBRID,
                0.74,
                f"graph_{graph_intent.lower()}_signal",
                graph_intent=graph_intent,
            )
        if self._is_follow_up(lowered, normalized):
            return self._decision(
                QUERY_TYPE_FOLLOW_UP,
                RETRIEVAL_CONTEXT_ONLY,
                0.72,
                "follow_up_signal",
            )
        if self._looks_like_question(query):
            return self._decision(
                QUERY_TYPE_NEW_CONCEPT,
                RETRIEVAL_LOCAL_HYBRID,
                0.76,
                "question_signal",
            )
        if len(normalized) <= int(self.rules.get("ambiguousMaxLength", 12)):
            return self._decision(
                QUERY_TYPE_NEW_CONCEPT,
                RETRIEVAL_LOCAL_HYBRID,
                0.45,
                "ambiguous_short_input",
            )
        return self._decision(
            QUERY_TYPE_NEW_CONCEPT,
            RETRIEVAL_LOCAL_HYBRID,
            0.6,
            "default_new_concept",
        )

    def _decision(
        self,
        query_type: str,
        retrieval_strategy: str,
        confidence: float,
        reason: str,
        graph_intent: str | None = None,
    ) -> QueryClassification:
        return QueryClassification(
            query_type=query_type,
            retrieval_strategy=retrieval_strategy,
            confidence=round(float(confidence), 4),
            reason=reason,
            graph_intent=graph_intent,
        )

    def _load_rules(self) -> dict[str, Any]:
        try:
            with self.rules_path.open("r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                return loaded
        except Exception:
            LOGGER.warning("Failed to load query classifier rules: %s", self.rules_path, exc_info=True)
        return {
            "lowConfidenceThreshold": 0.55,
            "smallTalkMaxLength": 12,
            "ambiguousMaxLength": 12,
            "smallTalkTerms": ["hi", "hello"],
            "questionTerms": ["?", "what", "how", "why"],
            "followUpTerms": [],
            "comparisonTerms": ["vs"],
            "proceduralTerms": [],
            "errorTerms": ["error", "exception"],
            "currentInfoTerms": ["today", "current", "latest", "now"],
            "deepReasoningTerms": [],
            "graphStrongTerms": ["图谱", "多跳", "跨层", "串联", "知识链", "链路"],
            "graphRelationTerms": ["关系", "联系", "关联", "图谱", "多跳", "串联", "脉络"],
            "graphMultiHopTerms": ["多跳", "多步", "跨层", "跨领域", "跨课程", "multi-hop"],
            "graphComparisonTerms": ["对比", "比较", "区别", "差异", "vs", "versus"],
            "graphPrerequisiteTerms": ["前置", "前提", "基础", "先学", "依赖", "学习路径", "路径", "顺序"],
            "graphSummaryTerms": ["总结", "概括", "梳理", "综述", "归纳", "总览"],
            "graphMechanismTerms": ["机制", "落地", "应用到", "如何串联", "怎么串", "实现到"],
            "graphCommonMistakeTerms": ["常见错误", "误区", "容易混淆", "混淆", "坑点"],
        }

    def _extract_query(self, params: dict[str, Any]) -> str:
        for key in ("query", "message", "userInput", "question", "topic", "prompt"):
            value = params.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        messages = params.get("messages")
        if isinstance(messages, list):
            for item in reversed(messages):
                if not isinstance(item, dict) or item.get("role") != "user":
                    continue
                content = item.get("content")
                if isinstance(content, str) and content.strip():
                    return content.strip()
        return ""

    def _normalize(self, text: str) -> str:
        return "".join(str(text).lower().split())

    def _contains_any(self, lowered_text: str, rule_key: str) -> bool:
        return any(str(term).lower() in lowered_text for term in self.rules.get(rule_key, []))

    def _is_small_talk(self, normalized: str) -> bool:
        max_length = int(self.rules.get("smallTalkMaxLength", 12))
        for term in self.rules.get("smallTalkTerms", []):
            normalized_term = self._normalize(str(term))
            if normalized == normalized_term:
                return True
            if len(normalized) <= max_length and normalized_term and normalized_term in normalized:
                return True
        return False

    def _is_follow_up(self, lowered_text: str, normalized: str) -> bool:
        if len(normalized) > int(self.rules.get("ambiguousMaxLength", 12)) * 2:
            return False
        return self._contains_any(lowered_text, "followUpTerms")

    def _is_answer_to_previous_question(self, params: dict[str, Any], query: str) -> bool:
        recent_messages = params.get("messages") or params.get("conversation") or []
        if not isinstance(recent_messages, list):
            return False
        last_assistant = ""
        for item in reversed(recent_messages):
            if not isinstance(item, dict):
                continue
            if item.get("role") == "assistant":
                last_assistant = str(item.get("content") or "").strip()
                break
        if not last_assistant or not self._looks_like_question(last_assistant):
            return False
        normalized_query = self._normalize(query)
        return bool(normalized_query) and not self._looks_like_question(query) and len(normalized_query) <= 40

    def _looks_like_question(self, text: str) -> bool:
        if not text.strip():
            return False
        if "?" in text or "？" in text:
            return True
        lowered_text = text.lower()
        return self._contains_any(lowered_text, "questionTerms")

    def _looks_like_error_or_code(self, lowered_text: str) -> bool:
        if self._contains_any(lowered_text, "errorTerms"):
            return True
        if "```" in lowered_text:
            return True
        return bool(re.search(r"\b[a-z]+(?:exception|error)\b", lowered_text))

    def _is_deep_reasoning(self, params: dict[str, Any], lowered_text: str) -> bool:
        reasoning_mode = params.get("reasoningMode")
        if isinstance(reasoning_mode, str) and reasoning_mode.strip().upper() == "DEEP":
            return True
        if params.get("deepReasoning") is True:
            return True
        return self._contains_any(lowered_text, "deepReasoningTerms")

    def _has_image(self, params: dict[str, Any]) -> bool:
        for key in ("imageUrls", "images", "imageFiles", "attachments"):
            value = params.get(key)
            if isinstance(value, list) and value:
                return True
            if isinstance(value, dict) and value:
                return True
        return False

    def _web_search_enabled(self, params: dict[str, Any]) -> bool:
        return bool(
            params.get("webSearchEnabled") is True
            or params.get("enableWebSearch") is True
            or params.get("tavilySearchEnabled") is True
        )

    def _detect_graph_intent(self, lowered_text: str) -> str | None:
        if self._contains_any(lowered_text, "graphComparisonTerms"):
            return GRAPH_INTENT_COMPARISON
        if self._contains_any(lowered_text, "graphPrerequisiteTerms"):
            return GRAPH_INTENT_PREREQUISITE_PATH
        if self._contains_any(lowered_text, "graphSummaryTerms"):
            return GRAPH_INTENT_COMMUNITY_SUMMARY
        if self._contains_any(lowered_text, "graphMultiHopTerms"):
            return GRAPH_INTENT_MULTI_HOP_RELATION
        if self._looks_like_graph_relation_query(lowered_text):
            return GRAPH_INTENT_CROSS_LAYER_RELATION
        if self._contains_any(lowered_text, "graphMechanismTerms"):
            return GRAPH_INTENT_MECHANISM_APPLICATION
        if self._contains_any(lowered_text, "graphCommonMistakeTerms"):
            return GRAPH_INTENT_COMMON_MISTAKE
        return GRAPH_INTENT_NONE

    def _looks_like_graph_relation_query(self, lowered_text: str) -> bool:
        if not self._contains_any(lowered_text, "graphRelationTerms"):
            return False
        if self._contains_any(lowered_text, "graphStrongTerms"):
            return True
        return bool(re.search(r"(与|和|跟|同).{1,80}(之间)?(关系|联系|关联)", lowered_text))
