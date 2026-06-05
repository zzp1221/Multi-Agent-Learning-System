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
        deep_quality_mode = self._is_deep_quality_mode(params, lowered)

        if self._has_image(params):
            return self._apply_deep_quality_mode(self._decision(
                QUERY_TYPE_IMAGE_QUESTION,
                RETRIEVAL_LOCAL_HYBRID,
                0.92,
                "image_input",
            ), deep_quality_mode)
        if not normalized:
            return self._apply_deep_quality_mode(
                self._decision(QUERY_TYPE_SMALL_TALK, RETRIEVAL_NONE, 0.9, "empty_input"),
                deep_quality_mode,
            )
        if self._is_small_talk(normalized):
            return self._apply_deep_quality_mode(
                self._decision(QUERY_TYPE_SMALL_TALK, RETRIEVAL_NONE, 0.95, "small_talk_rule"),
                deep_quality_mode,
            )
        if self._is_answer_to_previous_question(params, query):
            return self._apply_deep_quality_mode(self._decision(
                QUERY_TYPE_ANSWER_PREVIOUS,
                RETRIEVAL_CONTEXT_ONLY,
                0.86,
                "answer_previous_question",
            ), deep_quality_mode)
        graph_intent = self._detect_graph_intent(lowered)
        if graph_intent:
            if self._contains_any(lowered, "currentInfoTerms") or self._web_search_enabled(params):
                return self._apply_deep_quality_mode(self._decision(
                    QUERY_TYPE_CURRENT_INFO,
                    RETRIEVAL_WEB_AUGMENTED,
                    0.84,
                    f"current_info_with_graph_{graph_intent.lower()}",
                    graph_intent=graph_intent,
                ), deep_quality_mode)
            return self._apply_deep_quality_mode(self._decision(
                QUERY_TYPE_NEW_CONCEPT,
                RETRIEVAL_LOCAL_HYBRID,
                0.74,
                f"graph_{graph_intent.lower()}_signal",
                graph_intent=graph_intent,
            ), deep_quality_mode)
        if self._contains_any(lowered, "currentInfoTerms") or self._web_search_enabled(params):
            return self._apply_deep_quality_mode(self._decision(
                QUERY_TYPE_CURRENT_INFO,
                RETRIEVAL_WEB_AUGMENTED,
                0.84,
                "current_info_or_web",
            ), deep_quality_mode)
        if self._looks_like_error_or_code(lowered):
            return self._apply_deep_quality_mode(self._decision(
                QUERY_TYPE_ERROR_DEBUG,
                RETRIEVAL_LOCAL_HYBRID,
                0.86,
                "error_debug_signal",
            ), deep_quality_mode)
        if self._contains_any(lowered, "comparisonTerms"):
            return self._apply_deep_quality_mode(self._decision(
                QUERY_TYPE_COMPARISON,
                RETRIEVAL_LOCAL_HYBRID,
                0.82,
                "comparison_signal",
                graph_intent=GRAPH_INTENT_COMPARISON,
            ), deep_quality_mode)
        if self._contains_any(lowered, "proceduralTerms"):
            return self._apply_deep_quality_mode(self._decision(
                QUERY_TYPE_PROCEDURAL,
                RETRIEVAL_LOCAL_GREP_FIRST,
                0.8,
                "procedural_signal",
                graph_intent=self._detect_graph_intent(lowered),
            ), deep_quality_mode)
        if self._is_follow_up(lowered, normalized):
            return self._apply_deep_quality_mode(self._decision(
                QUERY_TYPE_FOLLOW_UP,
                RETRIEVAL_CONTEXT_ONLY,
                0.72,
                "follow_up_signal",
            ), deep_quality_mode)
        if self._looks_like_question(query):
            return self._apply_deep_quality_mode(self._decision(
                QUERY_TYPE_NEW_CONCEPT,
                RETRIEVAL_LOCAL_HYBRID,
                0.76,
                "question_signal",
            ), deep_quality_mode)
        if len(normalized) <= int(self.rules.get("ambiguousMaxLength", 12)):
            return self._apply_deep_quality_mode(self._decision(
                QUERY_TYPE_NEW_CONCEPT,
                RETRIEVAL_LOCAL_HYBRID,
                0.45,
                "ambiguous_short_input",
            ), deep_quality_mode)
        return self._apply_deep_quality_mode(self._decision(
            QUERY_TYPE_NEW_CONCEPT,
            RETRIEVAL_LOCAL_HYBRID,
            0.6,
            "default_new_concept",
        ), deep_quality_mode)

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

    def _apply_deep_quality_mode(
        self,
        classification: QueryClassification,
        enabled: bool,
    ) -> QueryClassification:
        if not enabled:
            return classification
        if classification.retrieval_strategy not in {
            RETRIEVAL_LOCAL_GREP_FIRST,
            RETRIEVAL_LOCAL_HYBRID,
        }:
            return classification
        return QueryClassification(
            query_type=classification.query_type,
            retrieval_strategy=RETRIEVAL_DEEP_EVIDENCE,
            confidence=classification.confidence,
            reason=f"{classification.reason}+deep_quality_mode",
            graph_intent=classification.graph_intent,
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
            "graphPrerequisiteTemplateTerms": ["请构建一条学习路径", "构建一条学习路径"],
            "graphComparisonTemplateTerms": ["请比较"],
            "graphMechanismTemplateTerms": ["在机制落地时如何连接"],
            "graphCommonMistakeTemplateTerms": ["请围绕常见误区"],
            "graphSummaryTemplateTerms": ["请总结"],
            "graphRelationTemplateTerms": ["请从知识图谱关系角度说明"],
            "graphStrongTerms": ["图谱", "多跳", "跨层", "串联", "知识链", "链路"],
            "graphRelationTerms": ["关系", "联系", "关联", "图谱", "多跳", "串联", "脉络"],
            "graphMultiHopTerms": ["多跳", "多步", "跨层", "跨领域", "跨课程", "multi-hop"],
            "graphComparisonTerms": ["对比", "比较", "区别", "差异", "vs", "versus"],
            "graphPrerequisiteTerms": ["前置", "前提", "基础", "先学", "依赖", "通向"],
            "graphPrerequisiteStrongTerms": ["前置知识", "前置路径", "前置依赖关系", "先修路径", "学习路径", "学习顺序", "先学什么", "依赖或通向", "如何依赖或通向", "构建一条学习路径"],
            "graphSummaryTerms": ["总结", "概括", "梳理", "综述", "归纳", "总览"],
            "graphMechanismTerms": ["机制", "落地", "应用到", "如何串联", "怎么串", "实现到"],
            "graphCommonMistakeTerms": ["常见错误", "误区", "容易混淆", "混淆", "坑点"],
            "graphMultiHopStrongTerms": ["跨领域", "跨课程", "multi-hop"],
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

    def _is_deep_quality_mode(self, params: dict[str, Any], lowered_text: str) -> bool:
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
        if self._contains_any(lowered_text, "graphPrerequisiteTemplateTerms"):
            return GRAPH_INTENT_PREREQUISITE_PATH
        if self._contains_any(lowered_text, "graphComparisonTemplateTerms"):
            return GRAPH_INTENT_COMPARISON
        if self._contains_any(lowered_text, "graphMechanismTemplateTerms"):
            return GRAPH_INTENT_MECHANISM_APPLICATION
        if self._contains_any(lowered_text, "graphCommonMistakeTemplateTerms"):
            return GRAPH_INTENT_COMMON_MISTAKE
        if self._contains_any(lowered_text, "graphSummaryTemplateTerms"):
            return GRAPH_INTENT_COMMUNITY_SUMMARY
        if self._contains_any(lowered_text, "graphMultiHopStrongTerms"):
            return GRAPH_INTENT_MULTI_HOP_RELATION
        if self._is_algorithm_multi_hop_relation_query(lowered_text):
            return GRAPH_INTENT_MULTI_HOP_RELATION
        if self._contains_any(lowered_text, "graphRelationTemplateTerms"):
            return GRAPH_INTENT_CROSS_LAYER_RELATION
        if self._contains_any(lowered_text, "graphCommonMistakeTerms"):
            return GRAPH_INTENT_COMMON_MISTAKE
        if self._contains_any(lowered_text, "graphSummaryTerms"):
            return GRAPH_INTENT_COMMUNITY_SUMMARY
        if self._contains_any(lowered_text, "graphMechanismTerms"):
            return GRAPH_INTENT_MECHANISM_APPLICATION
        if self._is_strong_prerequisite_path_query(lowered_text):
            return GRAPH_INTENT_PREREQUISITE_PATH
        if self._contains_any(lowered_text, "graphComparisonTerms"):
            return GRAPH_INTENT_COMPARISON
        if self._looks_like_graph_relation_query(lowered_text):
            return GRAPH_INTENT_CROSS_LAYER_RELATION
        return GRAPH_INTENT_NONE

    def _looks_like_graph_relation_query(self, lowered_text: str) -> bool:
        if not self._contains_any(lowered_text, "graphRelationTerms"):
            return False
        if self._contains_any(lowered_text, "graphStrongTerms"):
            return True
        return bool(re.search(r"(与|和|跟|同).{1,80}(之间)?(关系|联系|关联)", lowered_text))

    def _is_algorithm_multi_hop_relation_query(self, lowered_text: str) -> bool:
        if "多跳" not in lowered_text:
            return False
        return any(term in lowered_text for term in ("鸽巢原理", "组合计数", "图着色", "欧拉图", "哈密顿图"))

    def _is_strong_prerequisite_path_query(self, lowered_text: str) -> bool:
        if self._contains_any(lowered_text, "graphPrerequisiteStrongTerms"):
            return True
        prerequisite_hits = sum(
            1
            for term in self.rules.get("graphPrerequisiteTerms", [])
            if str(term).lower() in lowered_text
        )
        return prerequisite_hits >= 2 and not self._contains_any(lowered_text, "graphRelationTerms")
