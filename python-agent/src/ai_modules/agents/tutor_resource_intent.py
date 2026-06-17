"""Resource generation intent helpers for TutorAgent."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from src.ai_modules.generation.resource_builder import ResourceGenerationService
from src.ai_modules.runtime.planning_contract import PlanningParamKeys

LOGGER = logging.getLogger(__name__)

CONVERSATIONAL_RESOURCE_TYPES: tuple[str, ...] = (
    "DOCUMENT",
    "SLIDES",
    "MINDMAP",
    "QUIZ",
    "VIDEO",
    "CODE",
)

DEFAULT_RESOURCE_INTENT_EXTRACTOR = object()


@dataclass(slots=True)
class ResourceGenerationIntent:
    """Tutor identified a conversational resource generation request."""

    resource_types: list[str]
    topic: str
    question_count: int | None = None
    question_type_preference: str = ""
    difficulty_preference: str = ""
    confidence: float = 0.0
    rationale: str = ""

async def detect_resource_generation_intent(
    *,
    resource_intent_extractor: Any,
    user_query: str,
    conversation: list[dict[str, Any]] | None = None,
    params: dict[str, Any],
) -> ResourceGenerationIntent | None:
    text = str(user_query or "").strip()
    if not text or resource_intent_extractor is None:
        return None
    if looks_like_decline_or_cancel(text):
        return None
    if looks_like_inline_tutoring_answer_request(text):
        return None
    try:
        payload = await resource_intent_extractor.extract(
            user_query=text,
            recent_messages=conversation or [],
            learning_context=params.get("learningContext") if isinstance(params.get("learningContext"), dict) else {},
            structured_summary=params.get("structuredConversationSummary") if isinstance(params.get("structuredConversationSummary"), dict) else {},
        )
    except Exception:
        LOGGER.warning("Resource intent LLM extraction failed; continuing as normal tutoring", exc_info=True)
        return None

    if not bool(getattr(payload, "should_generate", False)):
        return None

    confidence = coerce_confidence(getattr(payload, "confidence", 0.0))
    if confidence < 0.65:
        return None
    topic = resolve_llm_resource_topic(str(getattr(payload, "topic", "") or ""), params=params)
    if not topic:
        params["resourceIntentMissingTopic"] = True
        params["resourceIntentMissingSlots"] = list(getattr(payload, "missing_slots", []) or ["topic"])
        return None
    requested_types = unique_resource_types([
        str(item).strip().upper()
        for item in getattr(payload, "resource_types", [])
    ])
    if not requested_types:
        params["resourceIntentMissingTypes"] = True
        return None
    return ResourceGenerationIntent(
        resource_types=requested_types,
        topic=topic,
        question_count=coerce_resource_question_count(getattr(payload, "question_count", None)),
        question_type_preference=coerce_question_type_preference(
            getattr(payload, "question_type_preference", "")
        ),
        difficulty_preference=coerce_difficulty_preference(
            getattr(payload, "difficulty_preference", "")
        ),
        confidence=confidence,
        rationale=str(getattr(payload, "rationale", "") or ""),
    )

def looks_like_decline_or_cancel(text: str) -> bool:
    """Short negative replies that reject a pending generation or cancel a request."""
    normalized = re.sub(r"\s+", "", str(text or "").lower())
    if not normalized:
        return False
    decline_phrases = (
        "暂不生成", "不生成", "不用生成", "取消生成", "算了", "不需要",
        "不用了", "暂时不", "先不", "不要生成", "停止生成", "取消",
        "不要了", "暂不", "先不生成", "不做了",
    )
    return any(phrase in normalized for phrase in decline_phrases)

def looks_like_inline_tutoring_answer_request(text: str) -> bool:
    normalized = re.sub(r"\s+", "", str(text or "").lower())
    if not normalized:
        return False
    artifact_terms = (
        "学习资源",
        "资源包",
        "资料包",
        "ppt",
        "slides",
        "幻灯片",
        "演示文稿",
        "课件",
        "思维导图",
        "脑图",
        "短视频",
        "微课",
        "视频",
        "代码案例",
        "代码示例",
        "示例程序",
        "下载",
        "文件",
    )
    answer_markers = ("较长回答", "长回答", "回答", "解释", "讲解", "比较", "分析", "说明")
    embedded_learning_markers = ("最后给", "最后提供", "附上", "包含", "包括")
    if any(marker in normalized for marker in answer_markers) and not any(term in normalized for term in artifact_terms):
        return True
    if (
        any(marker in normalized for marker in answer_markers)
        and any(marker in normalized for marker in embedded_learning_markers)
        and ("自测题" in normalized or "学习路径" in normalized or "练习" in normalized)
    ):
        return True
    if (
        re.search(r"不少于\d{3,5}(?:字|个字|字符)", normalized)
        and ("讲义" in normalized or "文章" in normalized or "报告" in normalized)
        and not any(term in normalized for term in ("资源包", "学习资源", "ppt", "下载", "文件"))
    ):
        return True
    return False

def resolve_llm_resource_topic(raw_topic: str, *, params: dict[str, Any]) -> str:
    topic = str(raw_topic or "").strip()
    if ResourceGenerationService._is_real_topic(topic):
        return topic[:80]
    return topic_from_context(params)

def coerce_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, confidence))

def coerce_resource_question_count(value: Any) -> int | None:
    try:
        count = int(value)
    except (TypeError, ValueError):
        return None
    if count <= 0:
        return None
    return min(count, 20)

def coerce_question_type_preference(value: Any) -> str:
    normalized = str(value or "").strip().upper()
    return normalized if normalized in {"SINGLE_CHOICE", "SHORT_ANSWER", "MIXED"} else ""

def coerce_difficulty_preference(value: Any) -> str:
    normalized = str(value or "").strip().upper()
    return normalized if normalized in {"BASIC", "INTERMEDIATE", "ADVANCED"} else ""

def missing_resource_topic_message() -> str:
    return "请补充要生成资源的具体主题，例如“围绕联合索引生成 PPT”，或先进入一个学习阶段后再说“根据当前阶段生成 PPT”。"

def topic_from_context(params: dict[str, Any]) -> str:
    learning_context = params.get("learningContext", {})
    if isinstance(learning_context, dict):
        for key in ("explicitUserTopic", "activeLearningStepTitle", "activeLearningStep", "knowledgePoint", "chapter", "course"):
            value = ResourceGenerationService._normalize_topic_candidate(learning_context.get(key))
            if ResourceGenerationService._is_real_topic(value):
                return value
    for key in ("topic", "keyPoints", "knowledgePoint"):
        value = ResourceGenerationService._normalize_topic_candidate(params.get(key))
        if ResourceGenerationService._is_real_topic(value):
            return value
    summary = params.get("structuredConversationSummary")
    if isinstance(summary, dict):
        topic_focus = summary.get("topicFocus")
        if isinstance(topic_focus, list):
            joined = "、".join(str(item).strip() for item in topic_focus[:3] if str(item).strip())
            if ResourceGenerationService._is_real_topic(joined):
                return joined
        last_user_message = str(summary.get("lastUserMessage") or "").strip()
        if ResourceGenerationService._is_real_topic(last_user_message) and not ResourceGenerationService._looks_like_resource_command(last_user_message):
            return last_user_message[:80]
    return ""

def apply_resource_generation_intent(
    *,
    params: dict[str, Any],
    intent: ResourceGenerationIntent,
    deep_quality_mode: bool = False,
) -> None:
    params["originalTutorQuery"] = params.get("query") or params.get("message")
    params["query"] = intent.topic
    params["topic"] = intent.topic
    params["explicitUserTopic"] = intent.topic
    params["keyPoints"] = params.get("keyPoints") or intent.topic
    params["resourceTypes"] = intent.resource_types
    if intent.question_count:
        params["count"] = intent.question_count
    if intent.question_type_preference:
        params["questionTypePreference"] = intent.question_type_preference
    if intent.difficulty_preference:
        params["difficulty"] = intent.difficulty_preference
    learning_context = params.get("learningContext", {})
    if isinstance(learning_context, dict):
        if learning_context.get("questionCount") and not params.get("count"):
            params["count"] = learning_context.get("questionCount")
        if learning_context.get("questionTypePreference") and not params.get("questionTypePreference"):
            params["questionTypePreference"] = learning_context.get("questionTypePreference")
        if learning_context.get("difficultyPreference") and not params.get("difficulty"):
            params["difficulty"] = learning_context.get("difficultyPreference")
        if learning_context.get("confirmedSlideOutline") is not None:
            params["confirmedSlideOutline"] = learning_context.get("confirmedSlideOutline")
        if learning_context.get("confirmedSlideOutlineText"):
            params["confirmedSlideOutlineText"] = learning_context.get("confirmedSlideOutlineText")
    if "QUIZ" in intent.resource_types and not params.get("count"):
        params["count"] = extract_question_count(str(params.get("originalTutorQuery") or ""))
    params[PlanningParamKeys.CONVERSATION_TRIGGERED_RESOURCE_GENERATION] = True
    if deep_quality_mode:
        params["generationQualityMode"] = "deep"

def extract_question_count(text: str) -> int:
    match = re.search(r"(\d{1,2}|[一二三四五六七八九十])\s*(?:道|个|题)", text)
    if not match:
        return 5
    raw_count = match.group(1)
    chinese_digits = {
        "一": 1,
        "二": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "十": 10,
    }
    try:
        count = chinese_digits[raw_count] if raw_count in chinese_digits else int(raw_count)
        return max(1, min(count, 20))
    except ValueError:
        return 5

def resource_intent_acknowledgement(intent: ResourceGenerationIntent) -> str:
    labels = [resource_type_label(item) for item in intent.resource_types]
    return f"我已识别到资源生成需求，正在围绕「{intent.topic}」生成{ '、'.join(labels) }。"

def resource_type_label(resource_type: str) -> str:
    return {
        "DOCUMENT": "文档",
        "SLIDES": "PPT",
        "MINDMAP": "思维导图",
        "QUIZ": "练习题",
        "VIDEO": "短视频",
        "CODE": "代码案例",
    }.get(resource_type, resource_type)

def unique_resource_types(resource_types: list[str]) -> list[str]:
    resolved: list[str] = []
    for resource_type in resource_types:
        if resource_type in CONVERSATIONAL_RESOURCE_TYPES and resource_type not in resolved:
            resolved.append(resource_type)
    return resolved
