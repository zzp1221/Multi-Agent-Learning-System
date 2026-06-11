"""基于 AgentCoreLoop、结构化压缩和持久化记忆的辅导 Agent。"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.ai_modules.agents.base import PlaceholderAgent
from src.ai_modules.generation.resource_builder import ResourceGenerationService
from src.ai_modules.llms import ConversationSummaryRefinerFactory, ResourceIntentExtractorFactory, TutorLLMClientFactory
from src.ai_modules.memory import MongoConversationSummaryStore
from src.ai_modules.models import (
    DialogState,
    ProgressPayload,
    ProgressSSEEvent,
    ReasoningChunkPayload,
    ReasoningChunkSSEEvent,
    ResultChunkPayload,
    ResultChunkSSEEvent,
    SSEEvent,
)
from src.ai_modules.prompts import build_tutor_system_prompt
from src.ai_modules.runtime import (
    AgentCoreLoop,
    ConversationCompactor,
    PermissionLevel,
    RecoveryEngine,
    StructuredConversationSummary,
    SystemSnapshot,
    ToolRegistry,
)
from src.ai_modules.runtime.skill_loader import SkillPromptLoader

LOGGER = logging.getLogger(__name__)

GRAPH_AWARE_INTENTS = {
    "COMMON_MISTAKE",
    "COMMUNITY_SUMMARY",
    "COMPARISON",
    "CROSS_LAYER_RELATION",
    "MECHANISM_APPLICATION",
    "MULTI_HOP_RELATION",
    "PREREQUISITE_PATH",
}

GRAPH_INTENT_GUIDANCE = {
    "PREREQUISITE_PATH": "围绕可能的前置基础、当前概念和后续延伸组织答案；不要把候选排序说成已验证的严格先修顺序。",
    "CROSS_LAYER_RELATION": "围绕跨层相关证据组织答案；只在证据明确时才表达确定的层间因果或调用链。",
    "MECHANISM_APPLICATION": "围绕机制、触发条件、实现位置和应用效果组织答案；不要把候选排序说成真实执行顺序。",
    "COMPARISON": "先给共同点，再给关键差异、适用边界和容易混淆的判断标准。",
    "COMMON_MISTAKE": "先指出常见误解，再用反例或边界条件瓦解，最后给正确心智模型。",
    "COMMUNITY_SUMMARY": "按概念群组总结主题、共同作用和组内差异。",
    "MULTI_HOP_RELATION": "围绕多跳相关证据组织答案，避免只解释孤立概念；不把候选列表当作真实路径。",
}

GRAPH_INTENT_LABELS = {
    "PREREQUISITE_PATH": "学习路径/前置依赖",
    "CROSS_LAYER_RELATION": "跨层关系",
    "MECHANISM_APPLICATION": "机制应用",
    "COMPARISON": "对比辨析",
    "COMMON_MISTAKE": "常见误区",
    "COMMUNITY_SUMMARY": "概念群总结",
    "MULTI_HOP_RELATION": "多跳关系",
}

GRAPH_SOURCE_LABELS = {
    "direct_evidence": "直接证据",
    "seed_protected": "种子概念",
    "graph_1hop": "一跳图谱相关概念",
    "graph_2hop": "二跳图谱补充概念",
    "graph": "图谱相关概念",
}

CONVERSATIONAL_RESOURCE_TYPES: tuple[str, ...] = (
    "DOCUMENT",
    "SLIDES",
    "MINDMAP",
    "QUIZ",
    "VIDEO",
    "CODE",
)

_DEFAULT_RESOURCE_INTENT_EXTRACTOR = object()


@dataclass(slots=True)
class ResourceGenerationIntent:
    """Tutor 识别出的对话资源生成请求。"""

    resource_types: list[str]
    topic: str
    question_count: int | None = None
    question_type_preference: str = ""
    difficulty_preference: str = ""
    confidence: float = 0.0
    rationale: str = ""


class TutorAgent(PlaceholderAgent):
    """使用近期对话和检索证据指导学习者。"""

    def __init__(
        self,
        compactor: ConversationCompactor | None = None,
        summary_store: Any | None = None,
        llm_client: Any | None = None,
        llm_fallback_clients: list[Any] | None = None,
        summary_refiner: Any | None = None,
        resource_intent_extractor: Any = _DEFAULT_RESOURCE_INTENT_EXTRACTOR,
        resource_bundle_runner: Any | None = None,
    ) -> None:
        super().__init__("Tutor Agent", "tutoring")
        self.summary_refiner = summary_refiner or ConversationSummaryRefinerFactory.create()
        self.compactor = compactor or ConversationCompactor(summary_refiner=self.summary_refiner)
        if compactor is not None and summary_refiner is not None:
            self.compactor.summary_refiner = summary_refiner
        self.summary_store = summary_store or MongoConversationSummaryStore()
        self.llm_clients = (
            [llm_client]
            if llm_client is not None
            else TutorLLMClientFactory.create_llm_candidates()
        )
        if llm_fallback_clients:
            self.llm_clients.extend(llm_fallback_clients)
        self.llm_client = self.llm_clients[0] if self.llm_clients else None
        self.skill_loader = SkillPromptLoader()
        if resource_intent_extractor is _DEFAULT_RESOURCE_INTENT_EXTRACTOR:
            self.resource_intent_extractor = ResourceIntentExtractorFactory.create() if llm_client is None else None
        else:
            self.resource_intent_extractor = resource_intent_extractor
        self.resource_bundle_runner = resource_bundle_runner

    def system_prompt(self, snapshot: SystemSnapshot) -> str:
        return self.skill_loader.build_system_prompt(
            skill_name="tutor",
            snapshot=snapshot,
            fallback_prompt=build_tutor_system_prompt(snapshot),
        )

    async def run(
        self,
        *,
        task_id: str,
        trace_id: str,
        seq: int,
        service_type: str,
        params: dict,
        snapshot: SystemSnapshot,
        system_prompt: str,
    ) -> AsyncIterator[SSEEvent]:
        del service_type
        conversation = self._extract_conversation(params)
        user_query = self._resolve_user_query(params)
        response_constraints = self._extract_response_constraints(user_query)
        if response_constraints:
            params["responseConstraints"] = response_constraints
        persisted_summary = await self._load_persisted_summary(
            conversation_id=self._conversation_id(params, task_id),
            user_id=params.get("userId"),
        )
        compaction_result = await self._compact_conversation(
            conversation,
            previous_summary=self._build_previous_summary(persisted_summary),
        )
        params["compactedConversation"] = compaction_result.compacted_messages
        params["structuredConversationSummary"] = compaction_result.structured_summary.model_dump(
            by_alias=True,
            mode="json",
        )
        params["conversationSummary"] = compaction_result.summary
        recent_dialogue = self._build_recent_dialogue_context(
            conversation=conversation,
            user_query=user_query,
        )
        input_mode = self._classify_input_mode(
            user_query=user_query,
            recent_dialogue=recent_dialogue,
            params=params,
        )
        params["deepQualityMode"] = self._is_deep_quality_mode(params)
        recent_dialogue["inputMode"] = input_mode
        params["recentDialogueContext"] = recent_dialogue
        params["inputMode"] = input_mode

        resource_intent = await self._detect_resource_generation_intent(
            user_query=user_query,
            conversation=conversation,
            params=params,
        )

        if compaction_result.was_compacted:
            await self._upsert_summary(
                params=params,
                task_id=task_id,
                structured_summary=compaction_result.structured_summary.model_dump(by_alias=True, mode="json"),
            )

        strategy = self._select_strategy(snapshot=snapshot, params=params)
        dialog_state = DialogState(
            conversationId=self._conversation_id(params, task_id),
            turnId=f"{task_id}-turn",
            pedagogyStrategy=strategy,
            nextAction=self._resolve_next_action(input_mode),
        )

        yield ProgressSSEEvent(
            taskId=task_id,
            traceId=trace_id,
            seq=seq,
            payload=ProgressPayload(
                stage=self.stage_name,
                percent=30 if compaction_result.was_compacted else 20,
                message=(
                    "已压缩历史对话，开始流式生成辅导回答"
                    if compaction_result.was_compacted
                    else "开始流式生成辅导回答"
                ),
            ),
            dialogState=dialog_state,
        )

        current_seq = seq + 1
        if resource_intent and self.resource_bundle_runner is not None:
            self._apply_resource_generation_intent(params=params, intent=resource_intent)
            yield ResultChunkSSEEvent(
                taskId=task_id,
                traceId=trace_id,
                seq=current_seq,
                payload=ResultChunkPayload(
                    text=self._resource_intent_acknowledgement(resource_intent),
                    stage="tutoring",
                ),
                dialogState=dialog_state,
            )
            current_seq += 1
            async for event in self.resource_bundle_runner(
                task_id=task_id,
                trace_id=trace_id,
                seq=current_seq,
                params=params,
                snapshot=snapshot,
            ):
                if event.event == "result_chunk":
                    continue
                self._remember_pending_slide_outline(params=params, event=event)
                yield event.model_copy(update={"dialog_state": event.dialog_state or dialog_state})
            return

        if params.pop("resourceIntentMissingTopic", False):
            yield ResultChunkSSEEvent(
                taskId=task_id,
                traceId=trace_id,
                seq=current_seq,
                payload=ResultChunkPayload(
                    text=self._missing_resource_topic_message(),
                    stage="tutoring",
                ),
                dialogState=dialog_state,
            )
            return

        if not self.llm_clients:
            raise RuntimeError("tutor_llm provider is not ready")

        last_error: Exception | None = None
        for llm_client in self.llm_clients:
            streamed = False
            emitted_stream_event = False
            if not response_constraints:
                try:
                    async for stream_event in self._try_direct_chat_stream(
                        llm_client=llm_client,
                        system_prompt=system_prompt,
                        params=params,
                        persisted_summary=persisted_summary,
                    ):
                        emitted_stream_event = True
                        if stream_event["kind"] == "reasoning":
                            yield ReasoningChunkSSEEvent(
                                taskId=task_id,
                                traceId=trace_id,
                                seq=current_seq,
                                payload=ReasoningChunkPayload(
                                    text=stream_event["text"],
                                    stage="reasoning",
                                    provider=stream_event.get("provider"),
                                    model=stream_event.get("model"),
                                ),
                                dialogState=dialog_state,
                            )
                        else:
                            streamed = True
                            yield ResultChunkSSEEvent(
                                taskId=task_id,
                                traceId=trace_id,
                                seq=current_seq,
                                payload=ResultChunkPayload(text=stream_event["text"], stage="tutoring"),
                                dialogState=dialog_state,
                            )
                        current_seq += 1
                    if streamed:
                        self._log_llm_success("direct_stream", llm_client)
                        return
                    details = self._describe_llm_client(llm_client)
                    LOGGER.warning(
                        "Direct tutor stream produced no tokens; trying non-stream LLM "
                        "provider=%s model=%s baseUrl=%s",
                        details["provider"],
                        details["model"],
                        details["baseUrl"],
                    )
                except Exception as exc:
                    last_error = exc
                    self._log_llm_failure("direct_stream", exc, llm_client)
                    if emitted_stream_event:
                        raise

            try:
                response_text = await self._run_agent_core_loop(
                    llm_client=llm_client,
                    system_prompt=system_prompt,
                    params=params,
                    snapshot=snapshot,
                    persisted_summary=persisted_summary,
                )
                response_text = await self._enforce_response_constraints(
                    llm_client=llm_client,
                    response_text=response_text,
                    constraints=response_constraints,
                    user_query=user_query,
                )
                yield ResultChunkSSEEvent(
                    taskId=task_id,
                    traceId=trace_id,
                    seq=current_seq,
                    payload=ResultChunkPayload(text=response_text, stage="tutoring"),
                    dialogState=dialog_state,
                )
                self._log_llm_success("agent_core_loop", llm_client)
                return
            except Exception as exc:
                last_error = exc
                self._log_llm_failure("agent_core_loop", exc, llm_client)

        if last_error is not None:
            raise last_error
        raise RuntimeError("tutor_llm provider is not ready")

    def _extract_response_constraints(self, user_query: str) -> dict[str, Any]:
        text = str(user_query or "")
        match = re.search(r"(\d{2,4})\s*(?:字|个字|字符)\s*(?:以内|内|之内|以下|左右)?", text)
        if not match:
            return {}
        max_chars = int(match.group(1))
        if max_chars <= 0:
            return {}
        return {
            "maxChars": max_chars,
            "instruction": f"用户明确要求回答控制在 {max_chars} 字以内；必须优先满足该长度要求。",
        }

    async def _enforce_response_constraints(
        self,
        *,
        llm_client: Any,
        response_text: str,
        constraints: dict[str, Any],
        user_query: str,
    ) -> str:
        max_chars = int(constraints.get("maxChars") or 0) if constraints else 0
        if max_chars <= 0 or len(response_text) <= max_chars:
            return self._dedupe_repeated_paragraphs(response_text)
        client = getattr(llm_client, "client", None)
        if client is None or not hasattr(client, "chat_completion"):
            raise RuntimeError("Tutor response exceeded explicit length constraint and no real LLM compressor is available")
        messages = [
            {
                "role": "system",
                "content": (
                    "你是学习辅导回答压缩器。只压缩已有答案，不新增事实；"
                    f"输出必须不超过 {max_chars} 个中文字符，保留核心观点。"
                ),
            },
            {
                "role": "user",
                "content": f"用户问题：{user_query}\n\n待压缩答案：\n{response_text}",
            },
        ]
        response = await client.chat_completion(messages=messages, max_tokens=max(64, max_chars * 2))
        message = client.extract_message(response)
        compressed = self._dedupe_repeated_paragraphs(client.extract_content(message).strip())
        if len(compressed) > max_chars:
            raise RuntimeError("Tutor LLM failed to satisfy explicit length constraint")
        return compressed

    def _dedupe_repeated_paragraphs(self, text: str) -> str:
        paragraphs = [paragraph.strip() for paragraph in str(text or "").replace("\r\n", "\n").split("\n") if paragraph.strip()]
        deduped: list[str] = []
        seen: set[str] = set()
        for paragraph in paragraphs:
            normalized = "".join(paragraph.split())
            if normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(paragraph)
        return "\n".join(deduped)

    def _describe_llm_client(self, llm_client: Any) -> dict[str, str]:
        client = getattr(llm_client, "client", llm_client)
        return {
            "provider": str(getattr(client, "provider_name", type(llm_client).__name__)),
            "model": str(getattr(client, "model_name", "")),
            "baseUrl": str(getattr(client, "base_url", "")),
        }

    def _log_llm_failure(self, stage: str, exc: Exception, llm_client: Any) -> None:
        details = self._describe_llm_client(llm_client)
        LOGGER.warning(
            "Tutor LLM attempt failed stage=%s provider=%s model=%s baseUrl=%s errorType=%s",
            stage,
            details["provider"],
            details["model"],
            details["baseUrl"],
            type(exc).__name__,
            exc_info=True,
        )

    def _log_llm_success(self, stage: str, llm_client: Any) -> None:
        if llm_client is self.llm_client:
            return
        details = self._describe_llm_client(llm_client)
        LOGGER.info(
            "Tutor LLM fallback succeeded stage=%s provider=%s model=%s baseUrl=%s",
            stage,
            details["provider"],
            details["model"],
            details["baseUrl"],
        )

    def _extract_conversation(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        candidates = params.get("messages") or params.get("conversation") or []
        if not isinstance(candidates, list):
            return []
        normalized: list[dict[str, Any]] = []
        for item in candidates:
            if not isinstance(item, dict):
                continue
            normalized.append(
                {
                    "role": item.get("role", "user"),
                    "content": item.get("content", ""),
                }
            )
        return normalized

    async def _detect_resource_generation_intent(
        self,
        *,
        user_query: str,
        conversation: list[dict[str, Any]] | None = None,
        params: dict[str, Any],
    ) -> ResourceGenerationIntent | None:
        text = str(user_query or "").strip()
        if not text or self.resource_intent_extractor is None:
            return None
        if self._looks_like_inline_tutoring_answer_request(text):
            return None
        try:
            payload = await self.resource_intent_extractor.extract(
                user_query=text,
                recent_messages=conversation or [],
                learning_context=params.get("learningContext") if isinstance(params.get("learningContext"), dict) else {},
                structured_summary=params.get("structuredConversationSummary") if isinstance(params.get("structuredConversationSummary"), dict) else {},
            )
        except Exception as exc:
            LOGGER.warning("Resource intent LLM extraction failed; continuing as normal tutoring", exc_info=True)
            return None

        if not bool(getattr(payload, "should_generate", False)):
            return None

        confidence = self._coerce_confidence(getattr(payload, "confidence", 0.0))
        if confidence < 0.65:
            return None
        topic = self._resolve_llm_resource_topic(str(getattr(payload, "topic", "") or ""), params=params)
        if not topic:
            params["resourceIntentMissingTopic"] = True
            params["resourceIntentMissingSlots"] = list(getattr(payload, "missing_slots", []) or ["topic"])
            return None
        requested_types = self._unique_resource_types([
            str(item).strip().upper()
            for item in getattr(payload, "resource_types", [])
        ])
        if not requested_types:
            params["resourceIntentMissingTypes"] = True
            return None
        return ResourceGenerationIntent(
            resource_types=requested_types,
            topic=topic,
            question_count=self._coerce_resource_question_count(getattr(payload, "question_count", None)),
            question_type_preference=self._coerce_question_type_preference(
                getattr(payload, "question_type_preference", "")
            ),
            difficulty_preference=self._coerce_difficulty_preference(
                getattr(payload, "difficulty_preference", "")
            ),
            confidence=confidence,
            rationale=str(getattr(payload, "rationale", "") or ""),
        )

    @staticmethod
    def _looks_like_inline_tutoring_answer_request(text: str) -> bool:
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

    def _resolve_llm_resource_topic(self, raw_topic: str, *, params: dict[str, Any]) -> str:
        topic = str(raw_topic or "").strip()
        if ResourceGenerationService._is_real_topic(topic):
            return topic[:80]
        return self._topic_from_context(params)

    @staticmethod
    def _coerce_confidence(value: Any) -> float:
        try:
            confidence = float(value)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, confidence))

    @staticmethod
    def _coerce_resource_question_count(value: Any) -> int | None:
        try:
            count = int(value)
        except (TypeError, ValueError):
            return None
        if count <= 0:
            return None
        return min(count, 20)

    @staticmethod
    def _coerce_question_type_preference(value: Any) -> str:
        normalized = str(value or "").strip().upper()
        return normalized if normalized in {"SINGLE_CHOICE", "SHORT_ANSWER", "MIXED"} else ""

    @staticmethod
    def _coerce_difficulty_preference(value: Any) -> str:
        normalized = str(value or "").strip().upper()
        return normalized if normalized in {"BASIC", "INTERMEDIATE", "ADVANCED"} else ""

    def _missing_resource_topic_message(self) -> str:
        return "请补充要生成资源的具体主题，例如“围绕联合索引生成 PPT”，或先进入一个学习阶段后再说“根据当前阶段生成 PPT”。"

    def _topic_from_context(self, params: dict[str, Any]) -> str:
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

    def _apply_resource_generation_intent(
        self,
        *,
        params: dict[str, Any],
        intent: ResourceGenerationIntent,
    ) -> None:
        params["originalTutorQuery"] = params.get("query") or params.get("message")
        params["query"] = intent.topic
        params["topic"] = intent.topic
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
            if learning_context.get("requiresSlideOutlineConfirmation"):
                params["requiresSlideOutlineConfirmation"] = learning_context.get("requiresSlideOutlineConfirmation")
            if learning_context.get("confirmedSlideOutline"):
                params["confirmedSlideOutline"] = learning_context.get("confirmedSlideOutline")
            if learning_context.get("confirmedSlideOutlineText"):
                params["confirmedSlideOutlineText"] = learning_context.get("confirmedSlideOutlineText")
        if "QUIZ" in intent.resource_types and not params.get("count"):
            params["count"] = self._extract_question_count(str(params.get("originalTutorQuery") or ""))
        params["conversationTriggeredResourceGeneration"] = True
        if self._is_deep_quality_mode(params):
            params["generationQualityMode"] = "deep"

    def _extract_question_count(self, text: str) -> int:
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

    def _remember_pending_slide_outline(self, *, params: dict[str, Any], event: SSEEvent) -> None:
        payload = getattr(event, "payload", None)
        asset_type = str(getattr(payload, "asset_type", "") or getattr(payload, "assetType", "") or "").upper()
        display_mode = str(getattr(payload, "display_mode", "") or getattr(payload, "displayMode", "") or "").upper()
        if asset_type != "SLIDES" or display_mode != "SLIDE_OUTLINE_CONFIRMATION":
            return
        outlines = params.get("pendingSlideOutlines")
        if not isinstance(outlines, list):
            outlines = []
            params["pendingSlideOutlines"] = outlines
        outlines.append(
            {
                "title": str(getattr(payload, "title", "") or "PPT 大纲"),
                "inlineContent": str(getattr(payload, "inline_content", "") or getattr(payload, "inlineContent", "") or ""),
            }
        )

    def _resource_intent_acknowledgement(self, intent: ResourceGenerationIntent) -> str:
        labels = [self._resource_type_label(item) for item in intent.resource_types]
        return f"我已识别到资源生成需求，正在围绕「{intent.topic}」生成{ '、'.join(labels) }。"

    def _resource_type_label(self, resource_type: str) -> str:
        return {
            "DOCUMENT": "文档",
            "SLIDES": "PPT",
            "MINDMAP": "思维导图",
            "QUIZ": "练习题",
            "VIDEO": "短视频",
            "CODE": "代码案例",
        }.get(resource_type, resource_type)

    def _unique_resource_types(self, resource_types: list[str]) -> list[str]:
        resolved: list[str] = []
        for resource_type in resource_types:
            if resource_type in CONVERSATIONAL_RESOURCE_TYPES and resource_type not in resolved:
                resolved.append(resource_type)
        return resolved

    def _select_strategy(self, *, snapshot: SystemSnapshot, params: dict[str, Any]) -> str:
        """选择教学策略，默认采用 Sigma 风格的苏格拉底式提问法。

        - mastery_socratic: 苏格拉底式提问 + 掌握程度评分标准 + 误解追踪
        - retrieval_grounded_scaffold: 基于检索证据的苏格拉底式辅导
        - diagnostic_scaffold: 聚焦薄弱点的诊断式分解
        """
        retrieval_result = params.get("retrievalResult", {})
        documents = retrieval_result.get("documents", [])
        profile = params.get("profile", {})
        has_misconceptions = bool(profile.get("misconceptions") or [])
        if snapshot.knowledge_gaps and documents:
            return "retrieval_grounded_scaffold"
        if snapshot.knowledge_gaps or has_misconceptions:
            return "diagnostic_scaffold"
        return "mastery_socratic"

    async def _run_agent_core_loop(
        self,
        *,
        llm_client: Any,
        system_prompt: str,
        params: dict[str, Any],
        snapshot: SystemSnapshot,
        persisted_summary: dict[str, Any] | None,
    ) -> str:
        del snapshot
        user_query = self._resolve_user_query(params)
        try:
            return await self._try_direct_chat(
                llm_client=llm_client,
                system_prompt=system_prompt,
                user_query=user_query,
                params=params,
                persisted_summary=persisted_summary,
            )
        except Exception as exc:
            self._log_llm_failure("direct_chat", exc, llm_client)
        return await self._run_with_agent_core_loop(
            llm_client=llm_client,
            system_prompt=system_prompt,
            user_query=user_query,
            params=params,
            persisted_summary=persisted_summary,
        )

    async def _try_direct_chat(
        self,
        *,
        llm_client: Any,
        system_prompt: str,
        user_query: str,
        params: dict[str, Any],
        persisted_summary: dict[str, Any] | None,
    ) -> str:
        client = llm_client.client
        memory_data = self._tool_load_conversation_memory(
            tool_input={}, persisted_summary=persisted_summary,
        )
        context_data = self._tool_read_compacted_context(tool_input={}, params=params)
        evidence_data = self._tool_read_retrieval_evidence(tool_input={}, params=params)
        profile_data = self._tool_read_profile_context(tool_input={}, params=params)
        image_analysis_data = self._tool_read_image_analysis_context(tool_input={}, params=params)
        recent_dialogue_data = self._tool_read_recent_dialogue_context(
            tool_input={}, params=params,
        )
        input_mode = self._resolve_input_mode(params=params, recent_dialogue=recent_dialogue_data)
        enriched_message = self._build_enriched_message(
            user_query=user_query,
            memory=memory_data,
            context=context_data,
            evidence=evidence_data,
            profile=profile_data,
            image_analysis=image_analysis_data,
            recent_dialogue=recent_dialogue_data,
            input_mode=input_mode,
            params=params,
        )
        llm_messages = self._build_llm_messages(
            system_prompt=system_prompt,
            runtime_context=enriched_message,
            recent_dialogue=recent_dialogue_data,
            user_query=user_query,
        )
        response = await client.chat_completion(
            messages=llm_messages,
        )
        message = client.extract_message(response)
        return client.extract_content(message)

    async def _try_direct_chat_stream(
        self,
        *,
        llm_client: Any,
        system_prompt: str,
        params: dict[str, Any],
        persisted_summary: dict[str, Any] | None,
    ):
        """从 LLM 流式输出 token 以实现实时辅导展示。"""
        client = llm_client.client
        user_query = self._resolve_user_query(params)
        memory_data = self._tool_load_conversation_memory(
            tool_input={}, persisted_summary=persisted_summary,
        )
        context_data = self._tool_read_compacted_context(tool_input={}, params=params)
        evidence_data = self._tool_read_retrieval_evidence(tool_input={}, params=params)
        profile_data = self._tool_read_profile_context(tool_input={}, params=params)
        image_analysis_data = self._tool_read_image_analysis_context(tool_input={}, params=params)
        recent_dialogue_data = self._tool_read_recent_dialogue_context(
            tool_input={}, params=params,
        )
        input_mode = self._resolve_input_mode(params=params, recent_dialogue=recent_dialogue_data)
        enriched_message = self._build_enriched_message(
            user_query=user_query,
            memory=memory_data,
            context=context_data,
            evidence=evidence_data,
            profile=profile_data,
            image_analysis=image_analysis_data,
            recent_dialogue=recent_dialogue_data,
            input_mode=input_mode,
            params=params,
        )
        llm_messages = self._build_llm_messages(
            system_prompt=system_prompt,
            runtime_context=enriched_message,
            recent_dialogue=recent_dialogue_data,
            user_query=user_query,
        )

        # Answer tokens are batched for smoother UI updates; reasoning stays raw.
        batch: list[str] = []
        stream_method = getattr(client, "chat_completion_stream_events", None)
        if callable(stream_method):
            stream = stream_method(
                messages=llm_messages,
                include_reasoning=self._is_deep_quality_mode(params),
            )
        else:
            stream = client.chat_completion_stream(messages=llm_messages)

        async for chunk in stream:
            if isinstance(chunk, str):
                batch.append(chunk)
            elif getattr(chunk, "kind", "") == "reasoning":
                if batch:
                    yield {"kind": "answer", "text": "".join(batch)}
                    batch.clear()
                yield {
                    "kind": "reasoning",
                    "text": getattr(chunk, "text", ""),
                    "provider": getattr(chunk, "provider", None),
                    "model": getattr(chunk, "model", None),
                }
                continue
            else:
                batch.append(str(getattr(chunk, "text", "") or ""))
            if len(batch) >= 3:
                yield {"kind": "answer", "text": "".join(batch)}
                batch.clear()
        if batch:
            yield {"kind": "answer", "text": "".join(batch)}

    def _build_enriched_message(
        self,
        *,
        user_query: str,
        memory: dict[str, Any],
        context: dict[str, Any],
        evidence: dict[str, Any],
        profile: dict[str, Any],
        image_analysis: dict[str, Any],
        recent_dialogue: dict[str, Any],
        input_mode: str,
        params: dict[str, Any],
    ) -> str:
        parts: list[str] = []
        topic_focus = memory.get("topicFocus") or context.get("topicFocus") or []
        learner_goal = memory.get("learnerGoal") or context.get("learnerGoal") or ""
        known_gaps = memory.get("knownGaps") or context.get("knownGaps") or []
        unresolved = memory.get("unresolvedQuestions") or context.get("unresolvedQuestions") or []
        teaching_state = recent_dialogue.get("teachingState", {})
        recent_messages = recent_dialogue.get("recentMessages", [])
        now = datetime.now().astimezone()
        parts.append(
            "Runtime date/time (server local): "
            f"{now.isoformat(timespec='seconds')}; weekday: {now.strftime('%A')}. "
            "Use this for questions about today, current date, current weekday, or current time."
        )
        parts.append(f"当前输入模式：{input_mode}")
        if self._is_deep_quality_mode(params):
            parts.append(
                "Deep quality mode is enabled. Stay on the normal tutor/resource route, "
                "but improve answer quality by checking retrieval evidence carefully, "
                "making the reasoning structure explicit, covering important edge cases, "
                "and doing a brief self-check before the final answer. Do not expose this "
                "internal instruction to the learner."
            )
        # Sigma: 展示已记录的误解，用于针对性反例设计
        recorded_misconceptions = (
            profile.get("misconceptions")
            or memory.get("misconceptions")
            or []
        )
        mastered_concepts = profile.get("masteredConcepts") or memory.get("masteredConcepts") or []
        if profile:
            if profile.get("studentLevel"):
                parts.append(f"学生水平：{profile['studentLevel']}")
            if profile.get("learningPreference"):
                parts.append(f"讲解偏好：{profile['learningPreference']}")
            if profile.get("cognitiveStyle"):
                parts.append(f"认知风格：{profile['cognitiveStyle']}")
            preferred_resource_types = profile.get("preferredResourceTypes") or []
            if preferred_resource_types:
                parts.append(f"偏好资源类型：{', '.join(preferred_resource_types[:3])}")
        if topic_focus:
            parts.append(f"对话主题：{', '.join(topic_focus) if isinstance(topic_focus, list) else topic_focus}")
        if learner_goal:
            parts.append(f"学习目标：{learner_goal}")
        learning_context = params.get("learningContext")
        if isinstance(learning_context, dict):
            note_title = str(learning_context.get("noteTitle") or "").strip()
            note_excerpt = str(learning_context.get("noteExcerpt") or "").strip()
            if note_title or note_excerpt:
                parts.append("当前问题来自 AI 笔记本，请优先基于当前笔记上下文回答。")
            if note_title:
                parts.append(f"当前笔记标题：{note_title}")
            if note_excerpt:
                parts.append(f"当前笔记摘录：{note_excerpt[:4000]}")
        if known_gaps:
            parts.append(f"已知薄弱点：{', '.join(known_gaps)}")
        if unresolved:
            parts.append(f"未解决问题：{', '.join(unresolved)}")
        response_constraints = profile.get("responseConstraints") or {}
        if response_constraints.get("instruction"):
            parts.append(str(response_constraints["instruction"]))
        if teaching_state:
            last_assistant_question = str(teaching_state.get("lastAssistantQuestion") or "").strip()
            current_user_intent = str(teaching_state.get("currentUserIntent") or "").strip()
            if last_assistant_question:
                parts.append(f"上一轮导师追问：{last_assistant_question}")
            if teaching_state.get("awaitingUserAnswer"):
                parts.append("当前教学状态：导师上一轮刚提出问题，当前更可能在等待学生作答。")
            if current_user_intent == "answer_previous_question":
                parts.append("当前轮意图：用户更像是在回答上一轮问题，不要把它当成新的话题开场。")
        if recent_messages:
            parts.append("最近对话片段（优先用于承接上下文）：")
            for item in recent_messages:
                role = "导师" if item.get("role") == "assistant" else "学生"
                content = str(item.get("content") or "").strip()
                if content:
                    parts.append(f"  - {role}：{content}")
        if recorded_misconceptions:
            parts.append("‼️ 已记录的错误概念（必须用反例瓦解，勿直接纠正）：")
            for mc in recorded_misconceptions[:5]:
                concept = mc.get("concept", "") if isinstance(mc, dict) else ""
                belief = mc.get("wrongBelief", "") if isinstance(mc, dict) else ""
                status = mc.get("status", "") if isinstance(mc, dict) else ""
                if concept and belief:
                    parts.append(f"  - [{concept}] {belief} (状态: {status or 'active'})")
        if mastered_concepts:
            concepts_str = ", ".join(
                mc.get("concept", "") if isinstance(mc, dict) else str(mc)
                for mc in mastered_concepts[:8]
            )
            if concepts_str:
                parts.append(f"已掌握概念（可用于交叉练习混入）：{concepts_str}")
        documents = evidence.get("documents", []) if isinstance(evidence.get("documents"), list) else []
        external_resources = (
            evidence.get("externalResources", [])
            if isinstance(evidence.get("externalResources"), list)
            else []
        )
        if evidence.get("webSearchEnabled") is True:
            if external_resources:
                parts.append(
                    "联网搜索状态：已开启。用户请求外部资料、媒体或链接时，"
                    "可以直接引用以下外部检索结果中的 URL；不得编造未提供的 URL。"
                )
                for i, resource in enumerate(external_resources[:5], 1):
                    if not isinstance(resource, dict):
                        continue
                    title = str(resource.get("title") or resource.get("sourceTitle") or "外部来源").strip()
                    url = str(resource.get("url") or "").strip()
                    snippet = str(resource.get("snippet") or resource.get("evidence") or "").strip()[:160]
                    if title and url:
                        suffix = f": {snippet}" if snippet else ""
                        parts.append(f"  {i}. {title} [{url}]{suffix}")
            else:
                parts.append(
                    "联网搜索状态：已开启，但当前检索证据没有可验证外部 URL。"
                    "如果用户要链接，只能说明暂未检索到可靠链接，不要编造链接。"
                )
        if documents:
            parts.append("检索到的知识来源：")
            for i, doc in enumerate(documents[:5], 1):
                title = str(doc.get("title") or "")
                snippet = str(doc.get("evidence") or doc.get("snippet") or "")[:200]
                url = str(doc.get("url") or "").strip()
                source_hint = f" [{url}]" if url else ""
                parts.append(f"  {i}. {title}{source_hint}: {snippet}")
        graph_pack = evidence.get("graphEvidencePack")
        if isinstance(graph_pack, dict) and graph_pack.get("intent"):
            intent = str(graph_pack.get("intent") or "")
            guidance = str(graph_pack.get("guidance") or "")
            parts.append("图谱证据包（用于辅助组织回答，不要复述内部诊断标签）：")
            parts.append(f"  - 图谱题型：{GRAPH_INTENT_LABELS.get(intent, intent)}")
            if guidance:
                parts.append(f"  - 回答组织建议：{guidance}")
            for i, node in enumerate(graph_pack.get("nodes", [])[:6], 1):
                if not isinstance(node, dict):
                    continue
                title = str(node.get("title") or "").strip()
                source_label = self._graph_source_label(str(node.get("source") or ""))
                if title:
                    parts.append(f"  {i}. {title}（{source_label}）")
            relation_hints = graph_pack.get("relationHints", [])
            if relation_hints:
                parts.append("  - 关系/路径线索：")
                for hint in relation_hints[:4]:
                    parts.append(f"    * {hint}")
        image_summary = str(image_analysis.get("summary") or "").strip()
        if image_summary:
            parts.append("图片识别结果：")
            parts.append(image_summary)
        if input_mode == "small_talk":
            parts.append("处理要求：这是寒暄、感谢或结束信号。自然简短回复，不进入教学诊断。")
        elif input_mode == "answer_previous_question":
            parts.append("处理要求：用户正在回答上一轮问题。先承接这句回答，指出其中合理部分，再继续推进，不要重新开题。")
        elif input_mode == "clear_question":
            parts.append("处理要求：用户提出了明确问题。先给一个简洁直接的回答，再按需要补充一个追问或例子。")
        else:
            parts.append("处理要求：只有在当前信息不足以作答时，才做一次简短澄清；禁止机械地追问“这是什么意思/什么场景”。")
        return "\n\n".join(parts)

    async def _run_with_agent_core_loop(
        self,
        *,
        llm_client: Any,
        system_prompt: str,
        user_query: str,
        params: dict[str, Any],
        persisted_summary: dict[str, Any] | None,
    ) -> str:
        tool_registry = ToolRegistry()
        tool_registry.register(
            name="load_conversation_memory",
            fn=lambda tool_input: self._tool_load_conversation_memory(
                tool_input=tool_input,
                persisted_summary=persisted_summary,
            ),
            permission_level=PermissionLevel.READ_ONLY,
            description="从持久化记忆中加载最新的结构化对话摘要。",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
        )
        tool_registry.register(
            name="read_compacted_context",
            fn=lambda tool_input: self._tool_read_compacted_context(
                tool_input=tool_input,
                params=params,
            ),
            permission_level=PermissionLevel.READ_ONLY,
            description="读取最新的结构化压缩对话上下文。",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
        )
        tool_registry.register(
            name="read_retrieval_evidence",
            fn=lambda tool_input: self._tool_read_retrieval_evidence(
                tool_input=tool_input,
                params=params,
            ),
            permission_level=PermissionLevel.READ_ONLY,
            description="读取支持辅导回答的检索证据。",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
        )
        tool_registry.register(
            name="read_recent_dialogue_context",
            fn=lambda tool_input: self._tool_read_recent_dialogue_context(
                tool_input=tool_input,
                params=params,
            ),
            permission_level=PermissionLevel.READ_ONLY,
            description="读取近期对话轮次和教学状态，用于多轮连续性。",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
        )
        tool_registry.register(
            name="read_image_analysis_context",
            fn=lambda tool_input: self._tool_read_image_analysis_context(
                tool_input=tool_input,
                params=params,
            ),
            permission_level=PermissionLevel.READ_ONLY,
            description="读取从上传题目图片中提取的多模态图片分析结果。",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
        )
        core_loop = AgentCoreLoop(
            llm_client=llm_client,
            tool_registry=tool_registry,
            recovery_engine=RecoveryEngine(),
            max_iterations=4,
            agent_level=PermissionLevel.READ_ONLY,
        )
        result = await core_loop.run(
            system_prompt=system_prompt,
            messages=[
                {
                    "role": "user",
                    "content": self._build_agent_core_request(
                        user_query=user_query,
                        params=params,
                        persisted_summary=persisted_summary,
                    ),
                }
            ],
        )
        return result.final_text

    async def _load_persisted_summary(
        self,
        *,
        conversation_id: str,
        user_id: str | None,
    ) -> dict[str, Any] | None:
        try:
            document = await self.summary_store.get_latest_summary(
                conversation_id=conversation_id,
                user_id=user_id,
            )
        except Exception:
            return None
        if document is None:
            return None
        return document.model_dump(by_alias=True, mode="json")

    async def _compact_conversation(
        self,
        conversation: list[dict[str, Any]],
        *,
        previous_summary: StructuredConversationSummary | None,
    ):
        compact_async = getattr(self.compactor, "compact_async", None)
        if callable(compact_async):
            return await compact_async(conversation, previous_summary=previous_summary)
        return self.compactor.compact(conversation, previous_summary=previous_summary)

    def _build_previous_summary(
        self,
        persisted_summary: dict[str, Any] | None,
    ) -> StructuredConversationSummary | None:
        if not persisted_summary:
            return None
        try:
            return StructuredConversationSummary.model_validate(persisted_summary)
        except Exception:
            return None

    async def _upsert_summary(
        self,
        *,
        params: dict[str, Any],
        task_id: str,
        structured_summary: dict[str, Any],
    ) -> None:
        from src.ai_modules.memory import ConversationSummaryDocument

        document = ConversationSummaryDocument(
            conversationId=self._conversation_id(params, task_id),
            userId=params.get("userId"),
            taskId=task_id,
            topicFocus=structured_summary.get("topicFocus", []),
            canonicalTopicKeys=structured_summary.get("canonicalTopicKeys", []),
            aliases=structured_summary.get("aliases", {}),
            learnerGoal=structured_summary.get("learnerGoal"),
            knownGaps=structured_summary.get("knownGaps", []),
            unresolvedQuestions=structured_summary.get("unresolvedQuestions", []),
            preferredHelpStyle=structured_summary.get("preferredHelpStyle"),
            lastUserMessage=structured_summary.get("lastUserMessage"),
            recentProgress=structured_summary.get("recentProgress", []),
            confidence=float(structured_summary.get("confidence", 0.55) or 0.55),
            summaryText=structured_summary.get("summaryText", ""),
        )
        try:
            upsert = getattr(self.summary_store, "upsert_summary", None)
            if callable(upsert):
                await upsert(document)
                return
            await self.summary_store.save_summary(document)
        except Exception:
            return

    def _conversation_id(self, params: dict[str, Any], task_id: str) -> str:
        return str(params.get("conversationId") or task_id)

    def _tool_load_conversation_memory(
        self,
        *,
        tool_input: dict[str, Any],
        persisted_summary: dict[str, Any] | None,
    ) -> dict[str, Any]:
        del tool_input
        return persisted_summary or {}

    def _tool_read_compacted_context(
        self,
        *,
        tool_input: dict[str, Any],
        params: dict[str, Any],
    ) -> dict[str, Any]:
        del tool_input
        summary = params.get("structuredConversationSummary", {})
        return {
            **summary,
            "recentMessages": params.get("compactedConversation", [])[-2:],
        }

    def _tool_read_retrieval_evidence(
        self,
        *,
        tool_input: dict[str, Any],
        params: dict[str, Any],
    ) -> dict[str, Any]:
        del tool_input
        retrieval_result = params.get("retrievalResult", {})
        if not isinstance(retrieval_result, dict):
            retrieval_result = {}
        documents = retrieval_result.get("documents", [])
        if not isinstance(documents, list):
            documents = []
        web_search_enabled = self._web_search_enabled(params)
        return {
            "query": params.get("query"),
            "rewrittenQuery": params.get("rewrittenQuery"),
            "documents": documents,
            "sourcesSummary": retrieval_result.get("sourcesSummary", ""),
            "webSearchEnabled": web_search_enabled,
            "externalResources": self._collect_external_resources(
                params=params,
                documents=documents,
            ) if web_search_enabled else [],
            "graphEvidencePack": self._build_graph_evidence_pack(
                params=params,
                documents=documents,
            ),
        }

    def _web_search_enabled(self, params: dict[str, Any]) -> bool:
        web_result = params.get("webRetrievalResult")
        return bool(
            params.get("webSearchEnabled") is True
            or params.get("enableWebSearch") is True
            or params.get("tavilySearchEnabled") is True
            or (
                isinstance(web_result, dict)
                and web_result.get("enabled") is True
            )
        )

    def _collect_external_resources(
        self,
        *,
        params: dict[str, Any],
        documents: list[Any],
    ) -> list[dict[str, Any]]:
        resources: list[dict[str, Any]] = []
        seen_urls: set[str] = set()

        def add_resource(
            *,
            title: Any,
            url: Any,
            snippet: Any = "",
            source_title: Any = "",
            published_date: Any = "",
            score: Any = None,
        ) -> None:
            normalized_url = str(url or "").strip()
            if not normalized_url.startswith(("http://", "https://")):
                return
            dedupe_key = normalized_url.lower()
            if dedupe_key in seen_urls:
                return
            seen_urls.add(dedupe_key)
            resources.append(
                {
                    "title": str(title or source_title or normalized_url).strip(),
                    "url": normalized_url,
                    "snippet": str(snippet or "").strip(),
                    "sourceTitle": str(source_title or title or "").strip(),
                    "publishedDate": str(published_date or "").strip(),
                    "score": self._safe_float(score),
                }
            )

        for document in documents:
            if not isinstance(document, dict):
                continue
            add_resource(
                title=document.get("title"),
                url=document.get("url"),
                snippet=document.get("snippet") or document.get("evidence"),
                source_title=document.get("sourceTitle"),
                published_date=document.get("publishedDate"),
                score=document.get("score"),
            )

        for item in self._iter_web_retrieval_items(params):
            if isinstance(item, dict):
                add_resource(
                    title=item.get("title") or item.get("sourceTitle"),
                    url=item.get("url") or item.get("slug"),
                    snippet=item.get("snippet") or item.get("evidence") or item.get("content"),
                    source_title=item.get("sourceTitle"),
                    published_date=item.get("publishedDate"),
                    score=item.get("score"),
                )
                continue
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            metadata = next((extra for extra in item[3:] if isinstance(extra, dict)), {})
            add_resource(
                title=item[1],
                url=metadata.get("url") or item[0],
                snippet=metadata.get("snippet") or metadata.get("content"),
                source_title=metadata.get("sourceTitle") or item[1],
                published_date=metadata.get("publishedDate"),
                score=item[2] if len(item) > 2 else None,
            )
        return resources[:8]

    def _iter_web_retrieval_items(self, params: dict[str, Any]) -> list[Any]:
        web_result = params.get("webRetrievalResult")
        if isinstance(web_result, dict) and isinstance(web_result.get("results"), list):
            return list(web_result["results"])
        raw_result = params.get("retrievalRawResult")
        if isinstance(raw_result, dict):
            channels = raw_result.get("channels")
            if isinstance(channels, dict) and isinstance(channels.get("web"), list):
                return list(channels["web"])
        return []

    def _build_graph_evidence_pack(
        self,
        *,
        params: dict[str, Any],
        documents: Any = None,
    ) -> dict[str, Any]:
        intent = self._resolve_graph_intent_from_params(params)
        if intent not in GRAPH_AWARE_INTENTS:
            return {}

        raw_result = params.get("retrievalRawResult", {})
        raw_result = raw_result if isinstance(raw_result, dict) else {}
        graph_result = params.get("graphRetrievalResult", {})
        graph_result = graph_result if isinstance(graph_result, dict) else {}

        nodes = self._collect_graph_evidence_nodes(
            raw_result=raw_result,
            graph_result=graph_result,
            documents=documents if isinstance(documents, list) else [],
        )
        if not nodes:
            return {}
        return {
            "intent": intent,
            "guidance": GRAPH_INTENT_GUIDANCE.get(intent, "优先按图谱关系组织回答。"),
            "nodes": nodes[:8],
            "relationHints": self._build_graph_relation_hints(
                intent=intent,
                nodes=nodes,
                raw_result=raw_result,
            ),
        }

    def _resolve_graph_intent_from_params(self, params: dict[str, Any]) -> str:
        for value in (
            params.get("graphIntent"),
            params.get("retrievalRawResult", {}).get("graphIntent")
            if isinstance(params.get("retrievalRawResult"), dict)
            else None,
            params.get("graphRetrievalResult", {}).get("graphIntent")
            if isinstance(params.get("graphRetrievalResult"), dict)
            else None,
        ):
            if isinstance(value, str) and value.strip():
                return value.strip().upper()
        classification = params.get("queryClassification")
        if isinstance(classification, dict):
            value = classification.get("graphIntent")
            if isinstance(value, str) and value.strip():
                return value.strip().upper()
        return ""

    def _collect_graph_evidence_nodes(
        self,
        *,
        raw_result: dict[str, Any],
        graph_result: dict[str, Any],
        documents: list[Any],
    ) -> list[dict[str, Any]]:
        candidates: list[tuple[Any, str]] = []
        candidates.extend((item, "graph") for item in self._graph_result_items(graph_result))
        candidates.extend(
            (item, "graph")
            for item in documents
            if isinstance(item, dict) and str(item.get("channel") or "").strip().lower() == "graph"
        )

        diagnostics = raw_result.get("graphDiagnostics", {})
        if isinstance(diagnostics, dict):
            prerequisite = diagnostics.get("prerequisiteEvidence", {})
            if isinstance(prerequisite, dict):
                candidates.extend(
                    (item, "direct_evidence")
                    for item in prerequisite.get("directEvidenceCandidatesTopN", [])
                )
                candidates.extend(
                    (item, "seed_protected")
                    for item in prerequisite.get("protectedSeeds", [])
                )

        channels = raw_result.get("channels", {})
        if isinstance(channels, dict):
            candidates.extend((item, "graph") for item in channels.get("graph", []))

        nodes: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, (item, fallback_source) in enumerate(candidates, start=1):
            node = self._graph_node_from_item(item, fallback_source=fallback_source, fallback_rank=index)
            if not node:
                continue
            dedupe_keys = [
                str(value)
                for value in (node.get("slug"), node.get("title"))
                if str(value or "").strip()
            ]
            if not dedupe_keys or any(key in seen for key in dedupe_keys):
                continue
            seen.update(dedupe_keys)
            nodes.append(node)
        return nodes

    def _graph_result_items(self, graph_result: dict[str, Any]) -> list[Any]:
        results = graph_result.get("results", [])
        return results if isinstance(results, list) else []

    def _graph_node_from_item(
        self,
        item: Any,
        *,
        fallback_source: str,
        fallback_rank: int,
    ) -> dict[str, Any]:
        if isinstance(item, dict):
            title = str(item.get("title") or "").strip()
            slug = str(item.get("slug") or "").strip()
            source = str(item.get("source") or fallback_source).strip()
            score = item.get("score")
            rank = item.get("rank") or fallback_rank
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            slug = str(item[0] or "").strip()
            title = str(item[1] or "").strip()
            source = str(item[3] if len(item) > 3 else fallback_source).strip()
            score = item[2] if len(item) > 2 else None
            rank = fallback_rank
        else:
            return {}

        if not title and not slug:
            return {}
        return {
            "rank": rank,
            "slug": slug,
            "title": title or slug,
            "source": source or fallback_source,
            "hop": self._graph_hop_from_source(source or fallback_source),
            "score": self._safe_float(score),
        }

    def _graph_source_label(self, source: str) -> str:
        normalized = str(source or "").strip().lower()
        return GRAPH_SOURCE_LABELS.get(normalized, "图谱相关概念")

    def _graph_hop_from_source(self, source: str) -> int | None:
        normalized = str(source or "").strip().lower()
        if "2hop" in normalized or "2_hop" in normalized:
            return 2
        if "1hop" in normalized or "1_hop" in normalized:
            return 1
        return None

    def _safe_float(self, value: Any) -> float | None:
        try:
            return round(float(value), 4)
        except (TypeError, ValueError):
            return None

    def _build_graph_relation_hints(
        self,
        *,
        intent: str,
        nodes: list[dict[str, Any]],
        raw_result: dict[str, Any],
    ) -> list[str]:
        titles = [str(node.get("title") or "").strip() for node in nodes if node.get("title")]
        hints: list[str] = []
        if len(titles) >= 2:
            joined_titles = "、".join(titles[:5])
            if intent == "PREREQUISITE_PATH":
                hints.append(f"学习路径相关候选集合（非严格顺序）：{joined_titles}")
            elif intent in {"CROSS_LAYER_RELATION", "MULTI_HOP_RELATION"}:
                hints.append(f"关系链相关候选集合（非严格顺序）：{joined_titles}")
            elif intent == "MECHANISM_APPLICATION":
                hints.append(f"机制应用相关候选集合（非严格顺序）：{'、'.join(titles[:4])}")
            elif intent == "COMPARISON":
                hints.append("对比对象候选：" + " / ".join(titles[:4]))
            elif intent == "COMMON_MISTAKE":
                hints.append("易错点证据候选：" + " / ".join(titles[:4]))
            elif intent == "COMMUNITY_SUMMARY":
                hints.append("同一概念群候选：" + " / ".join(titles[:5]))

        diagnostics = raw_result.get("graphDiagnostics", {})
        if isinstance(diagnostics, dict):
            top5 = diagnostics.get("top5Stabilization", {})
            if isinstance(top5, dict):
                protected = top5.get("seedProtectedTop5", [])
                if protected:
                    hints.append("Top5 中保护的种子节点：" + ", ".join(map(str, protected[:4])))
        return hints

    def _tool_read_profile_context(
        self,
        *,
        tool_input: dict[str, Any],
        params: dict[str, Any],
    ) -> dict[str, Any]:
        del tool_input
        profile = params.get("profile", {})
        if not isinstance(profile, dict):
            return {}
        return {
            "studentLevel": profile.get("studentLevel") or profile.get("knowledgeFoundation"),
            "learningPreference": profile.get("learningPreference") or profile.get("preferredStyle"),
            "cognitiveStyle": profile.get("cognitiveStyle"),
            "preferredResourceTypes": profile.get("preferredResourceTypes", []),
            "responseConstraints": params.get("responseConstraints") or {},
        }

    def _tool_read_recent_dialogue_context(
        self,
        *,
        tool_input: dict[str, Any],
        params: dict[str, Any],
    ) -> dict[str, Any]:
        del tool_input
        recent_dialogue = params.get("recentDialogueContext", {})
        return recent_dialogue if isinstance(recent_dialogue, dict) else {}

    def _tool_read_image_analysis_context(
        self,
        *,
        tool_input: dict[str, Any],
        params: dict[str, Any],
    ) -> dict[str, Any]:
        del tool_input
        image_analysis = params.get("imageAnalysisResult", {})
        return image_analysis if isinstance(image_analysis, dict) else {}

    def _resolve_user_query(self, params: dict[str, Any]) -> str:
        return str(
            params.get("query")
            or params.get("message")
            or params.get("rewrittenQuery")
            or params.get("structuredConversationSummary", {}).get("lastUserMessage")
            or "当前主题"
        )

    def _resolve_input_mode(
        self,
        *,
        params: dict[str, Any],
        recent_dialogue: dict[str, Any],
    ) -> str:
        mode = params.get("inputMode")
        if isinstance(mode, str) and mode.strip():
            return mode
        return str(recent_dialogue.get("inputMode") or "clear_question")

    def _build_recent_dialogue_context(
        self,
        *,
        conversation: list[dict[str, Any]],
        user_query: str,
    ) -> dict[str, Any]:
        recent_messages = self._select_recent_turns(conversation=conversation, user_query=user_query)
        teaching_state = self._infer_teaching_state(
            recent_messages=recent_messages,
            user_query=user_query,
        )
        return {
            "recentMessages": recent_messages,
            "teachingState": teaching_state,
        }

    def _classify_input_mode(
        self,
        *,
        user_query: str,
        recent_dialogue: dict[str, Any],
        params: dict[str, Any],
    ) -> str:
        query_type = str(params.get("queryType") or "").strip().upper()
        if query_type == "SMALL_TALK":
            return "small_talk"
        if query_type == "ANSWER_PREVIOUS":
            return "answer_previous_question"
        if query_type == "FOLLOW_UP":
            return "ambiguous_topic"
        normalized = "".join(str(user_query).lower().split())
        if not normalized:
            return "small_talk"
        teaching_state = recent_dialogue.get("teachingState", {})
        if teaching_state.get("awaitingUserAnswer"):
            return "answer_previous_question"
        if self._looks_like_question(user_query):
            return "clear_question"
        if len(normalized) <= 12:
            return "ambiguous_topic"
        return "clear_question"

    def _select_recent_turns(
        self,
        *,
        conversation: list[dict[str, Any]],
        user_query: str,
    ) -> list[dict[str, str]]:
        normalized_query = "".join(str(user_query).split())
        trimmed = list(conversation)
        if trimmed:
            last_item = trimmed[-1]
            last_content = "".join(str(last_item.get("content") or "").split())
            if last_item.get("role") == "user" and last_content == normalized_query:
                trimmed = trimmed[:-1]
        recent_turns = trimmed[-4:]
        selected: list[dict[str, str]] = []
        for item in recent_turns:
            role = str(item.get("role") or "user")
            content = self._truncate_dialogue_text(str(item.get("content") or ""))
            if role not in {"user", "assistant"} or not content:
                continue
            selected.append({"role": role, "content": content})
        return selected

    def _infer_teaching_state(
        self,
        *,
        recent_messages: list[dict[str, str]],
        user_query: str,
    ) -> dict[str, Any]:
        last_assistant_question = ""
        for item in reversed(recent_messages):
            if item.get("role") == "assistant":
                content = str(item.get("content") or "").strip()
                if self._looks_like_question(content):
                    last_assistant_question = content
                    break
        normalized_query = str(user_query).strip()
        likely_answer = bool(normalized_query) and not self._looks_like_question(normalized_query)
        awaiting_user_answer = bool(last_assistant_question) and likely_answer
        return {
            "lastAssistantQuestion": last_assistant_question,
            "awaitingUserAnswer": awaiting_user_answer,
            "currentUserIntent": "answer_previous_question" if awaiting_user_answer else "ask_or_shift_topic",
        }

    def _build_llm_messages(
        self,
        *,
        system_prompt: str,
        runtime_context: str,
        recent_dialogue: dict[str, Any],
        user_query: str,
    ) -> list[dict[str, str]]:
        system_content = system_prompt
        if runtime_context.strip():
            system_content = f"{system_prompt}\n\n# 运行时上下文\n{runtime_context}"
        messages: list[dict[str, str]] = [{"role": "system", "content": system_content}]
        for item in recent_dialogue.get("recentMessages", []):
            role = str(item.get("role") or "")
            content = str(item.get("content") or "").strip()
            if role in {"user", "assistant"} and content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": user_query})
        return messages

    def _build_agent_core_request(
        self,
        *,
        user_query: str,
        params: dict[str, Any],
        persisted_summary: dict[str, Any] | None,
    ) -> str:
        enriched_message = self._build_enriched_message(
            user_query=user_query,
            memory=self._tool_load_conversation_memory(
                tool_input={},
                persisted_summary=persisted_summary,
            ),
            context=self._tool_read_compacted_context(tool_input={}, params=params),
            evidence=self._tool_read_retrieval_evidence(tool_input={}, params=params),
            profile=self._tool_read_profile_context(tool_input={}, params=params),
            image_analysis=self._tool_read_image_analysis_context(tool_input={}, params=params),
            recent_dialogue=self._tool_read_recent_dialogue_context(tool_input={}, params=params),
            input_mode=self._resolve_input_mode(
                params=params,
                recent_dialogue=self._tool_read_recent_dialogue_context(tool_input={}, params=params),
            ),
            params=params,
        )
        return (
            "请基于以下结构化上下文给出自然、贴合输入类型的回答。"
            "如果用户是在回答上一轮问题，要先承接；如果是问候或感谢，就自然回复；"
            "只有真的不清楚时才澄清。除非适合继续教学，否则不要强行追问。\n\n"
            f"{enriched_message}"
        )

    def _build_short_circuit_reply(
        self,
        *,
        user_query: str,
        input_mode: str,
        recent_dialogue: dict[str, Any],
    ) -> str:
        if input_mode != "small_talk":
            return ""
        if recent_dialogue.get("recentMessages"):
            return "我在，继续说就行。也可以直接接着上一轮的问题回答。"
        return "我在。你可以直接发问题、概念或题目，我来一起拆解。"

    def _resolve_next_action(self, input_mode: str) -> str:
        if input_mode == "small_talk":
            return "wait_user"
        if input_mode == "answer_previous_question":
            return "continue_guidance"
        return "ask_follow_up"

    def _is_deep_quality_mode(self, params: dict[str, Any]) -> bool:
        reasoning_mode = params.get("reasoningMode")
        if isinstance(reasoning_mode, str) and reasoning_mode.strip().upper() == "DEEP":
            return True
        return params.get("deepReasoning") is True or params.get("deepQualityMode") is True

    def _looks_like_question(self, text: str) -> bool:
        normalized = str(text).strip()
        if not normalized:
            return False
        if "？" in normalized or "?" in normalized:
            return True
        question_markers = ("什么", "怎么", "如何", "为什么", "哪些", "哪个", "能否", "可否", "吗")
        return any(marker in normalized for marker in question_markers)

    def _truncate_dialogue_text(self, text: str, max_length: int = 220) -> str:
        normalized = " ".join(str(text).split())
        if len(normalized) <= max_length:
            return normalized
        return normalized[: max_length - 1].rstrip() + "…"
