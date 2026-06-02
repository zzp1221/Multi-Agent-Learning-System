"""辅导对话流的会话压缩工具。"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.ai_modules.runtime.topic_canonicalizer import canonicalize_topics


class StructuredConversationSummary(BaseModel):
    """从历史对话中提取的结构化信息。"""

    topic_focus: list[str] = Field(default_factory=list, alias="topicFocus")
    canonical_topic_keys: list[str] = Field(default_factory=list, alias="canonicalTopicKeys")
    topic_aliases: dict[str, list[str]] = Field(default_factory=dict, alias="aliases")
    learner_goal: str | None = Field(default=None, alias="learnerGoal")
    known_gaps: list[str] = Field(default_factory=list, alias="knownGaps")
    unresolved_questions: list[str] = Field(default_factory=list, alias="unresolvedQuestions")
    preferred_help_style: str | None = Field(default=None, alias="preferredHelpStyle")
    last_user_message: str | None = Field(default=None, alias="lastUserMessage")
    recent_progress: list[str] = Field(default_factory=list, alias="recentProgress")
    confidence: float = 0.55
    summary_text: str = Field(default="", alias="summaryText")

    model_config = ConfigDict(populate_by_name=True, extra="ignore")


@dataclass(slots=True)
class CompactionResult:
    """对会话消息执行压缩操作的结果。"""

    compacted_messages: list[dict[str, Any]]
    summary: str
    structured_summary: StructuredConversationSummary
    was_compacted: bool
    estimated_tokens_before: int
    estimated_tokens_after: int


@dataclass(slots=True)
class TopicCandidate:
    """从用户消息中提取的带评分主题候选。"""

    text: str
    source: str
    recency_rank: int
    order: int


class ConversationCompactor:
    """压缩长对话，同时保留最近的辅导上下文。"""

    def __init__(
        self,
        token_budget: int = 1200,
        keep_recent_turns: int = 4,
        summary_max_chars: int = 500,
        summary_refiner: Any | None = None,
    ) -> None:
        self.token_budget = token_budget
        self.keep_recent_turns = keep_recent_turns
        self.summary_max_chars = summary_max_chars
        self.summary_refiner = summary_refiner

    def estimate_tokens(self, messages: list[dict[str, Any]]) -> int:
        """基于字符数的粗略 token 估算。"""

        total_chars = 0
        for message in messages:
            total_chars += len(str(message.get("role", "")))
            total_chars += len(str(message.get("content", "")))
        return max(1, total_chars // 2)

    def compact(
        self,
        messages: list[dict[str, Any]],
        previous_summary: StructuredConversationSummary | None = None,
    ) -> CompactionResult:
        """当超出 token 预算时，将早期对话压缩为简短的辅导摘要。"""

        estimated_before = self.estimate_tokens(messages)
        structured_summary = self._extract_structured_summary(messages)
        if previous_summary is not None:
            structured_summary = self._merge_summaries(
                new_summary=structured_summary,
                previous_summary=previous_summary,
            )
        if estimated_before <= self.token_budget or len(messages) <= self.keep_recent_turns:
            return CompactionResult(
                compacted_messages=[dict(message) for message in messages],
                summary=structured_summary.summary_text,
                structured_summary=structured_summary,
                was_compacted=False,
                estimated_tokens_before=estimated_before,
                estimated_tokens_after=estimated_before,
            )

        recent_messages = [dict(message) for message in messages[-self.keep_recent_turns :]]
        historical_messages = messages[: -self.keep_recent_turns]
        structured_summary = self._extract_structured_summary(historical_messages)
        if previous_summary is not None:
            structured_summary = self._merge_summaries(
                new_summary=structured_summary,
                previous_summary=previous_summary,
            )
        summary = structured_summary.summary_text
        compacted_messages = [
            {
                "role": "system",
                "content": f"历史对话摘要: {summary}",
            },
            *recent_messages,
        ]
        estimated_after = self.estimate_tokens(compacted_messages)
        return CompactionResult(
            compacted_messages=compacted_messages,
            summary=summary,
            structured_summary=structured_summary,
            was_compacted=True,
            estimated_tokens_before=estimated_before,
            estimated_tokens_after=estimated_after,
        )

    async def compact_async(
        self,
        messages: list[dict[str, Any]],
        previous_summary: StructuredConversationSummary | None = None,
    ) -> CompactionResult:
        """Compact and optionally refine the structured summary with an LLM."""

        result = self.compact(messages, previous_summary=previous_summary)
        if not self._should_refine_with_llm(messages=messages, result=result):
            return result

        try:
            payload = await self.summary_refiner.refine(
                messages=messages,
                rule_summary=result.structured_summary.model_dump(by_alias=True),
            )
            refined_summary = self._merge_refined_summary(result.structured_summary, payload)
        except Exception:
            return result

        compacted_messages = [dict(message) for message in result.compacted_messages]
        if result.was_compacted and compacted_messages:
            compacted_messages[0] = {
                **compacted_messages[0],
                "content": f"历史对话摘要: {refined_summary.summary_text}",
            }
        estimated_after = self.estimate_tokens(compacted_messages)
        return CompactionResult(
            compacted_messages=compacted_messages,
            summary=refined_summary.summary_text,
            structured_summary=refined_summary,
            was_compacted=result.was_compacted,
            estimated_tokens_before=result.estimated_tokens_before,
            estimated_tokens_after=estimated_after,
        )

    def _extract_structured_summary(
        self,
        messages: list[dict[str, Any]],
    ) -> StructuredConversationSummary:
        """从历史对话中提取结构化的辅导信号。"""

        user_messages = [
            str(message.get("content", "")).replace("\n", " ").strip()
            for message in messages
            if str(message.get("role", "")).lower() == "user"
        ]
        assistant_messages = [
            str(message.get("content", "")).replace("\n", " ").strip()
            for message in messages
            if str(message.get("role", "")).lower() == "assistant"
        ]

        topic_focus = self._extract_topic_focus(user_messages)
        canonical_topics = canonicalize_topics(topic_focus)
        if canonical_topics:
            topic_focus = [topic.display_name for topic in canonical_topics]
        canonical_topic_keys = [topic.canonical_key for topic in canonical_topics]
        topic_aliases = {
            topic.canonical_key: topic.aliases
            for topic in canonical_topics
            if topic.canonical_key and topic.aliases
        }
        learner_goal = next(
            (
                message
                for message in user_messages
                if any(keyword in message for keyword in ("想", "希望", "需要", "目标", "复习", "掌握"))
            ),
            None,
        )
        unresolved_questions = [
            message
            for message in user_messages[-3:]
            if "?" in message or "？" in message or any(keyword in message for keyword in ("不懂", "不会", "为什么", "怎么"))
        ]
        known_gaps = [
            message[:18]
            for message in user_messages[-3:]
            if any(keyword in message for keyword in ("不会", "不懂", "总错", "易错", "分不清"))
        ]
        preferred_help_style = self._detect_help_style(user_messages)
        last_user_message = user_messages[-1] if user_messages else None
        recent_progress = [
            message[:24]
            for message in assistant_messages[-2:]
            if message
        ]

        summary_text = self._build_summary_text(
            topic_focus=topic_focus,
            learner_goal=learner_goal,
            unresolved_questions=unresolved_questions,
            known_gaps=known_gaps,
        )
        return StructuredConversationSummary(
            topicFocus=topic_focus,
            canonicalTopicKeys=canonical_topic_keys,
            aliases=topic_aliases,
            learnerGoal=learner_goal,
            knownGaps=known_gaps,
            unresolvedQuestions=unresolved_questions,
            preferredHelpStyle=preferred_help_style,
            lastUserMessage=last_user_message,
            recentProgress=recent_progress,
            confidence=self._summary_confidence(
                topic_focus=topic_focus,
                known_gaps=known_gaps,
                unresolved_questions=unresolved_questions,
            ),
            summaryText=summary_text,
        )

    def _extract_topic_focus(self, user_messages: list[str]) -> list[str]:
        candidates: list[TopicCandidate] = []
        order = 0
        for recency_rank, message in enumerate(reversed(user_messages)):
            for candidate_text, source in self._extract_topic_candidates(message):
                candidates.append(
                    TopicCandidate(
                        text=candidate_text,
                        source=source,
                        recency_rank=recency_rank,
                        order=order,
                    )
                )
                order += 1
        if not candidates:
            return []

        scored_candidates = sorted(
            candidates,
            key=lambda candidate: (
                -self._score_topic_candidate(candidate),
                candidate.recency_rank,
                candidate.order,
            ),
        )
        focus_terms: list[str] = []
        for candidate in scored_candidates:
            if candidate.text in focus_terms:
                continue
            focus_terms.append(candidate.text)
            if len(focus_terms) >= 6:
                break
        return focus_terms

    def _detect_help_style(self, user_messages: list[str]) -> str | None:
        joined = " ".join(user_messages)
        if any(keyword in joined for keyword in ("例子", "举例", "案例")):
            return "example_first"
        if any(keyword in joined for keyword in ("一步步", "详细", "慢一点")):
            return "step_by_step"
        if user_messages:
            return "concept_then_question"
        return None

    def _build_summary_text(
        self,
        *,
        topic_focus: list[str],
        learner_goal: str | None,
        unresolved_questions: list[str],
        known_gaps: list[str],
    ) -> str:
        summary_parts = [
            f"主题: {', '.join(topic_focus) or '暂无'}",
            f"目标: {learner_goal or '暂无明确目标'}",
            f"薄弱点: {', '.join(known_gaps) or '暂无'}",
            f"未解决问题: {' | '.join(unresolved_questions) or '暂无'}",
        ]
        summary = " ; ".join(summary_parts)
        if len(summary) > self.summary_max_chars:
            return f"{summary[: self.summary_max_chars - 3]}..."
        return summary

    def _merge_summaries(
        self,
        *,
        new_summary: StructuredConversationSummary,
        previous_summary: StructuredConversationSummary,
    ) -> StructuredConversationSummary:
        topic_focus = self._merge_unique(previous_summary.topic_focus, new_summary.topic_focus, limit=6)
        known_gaps = self._merge_unique(previous_summary.known_gaps, new_summary.known_gaps, limit=5)
        unresolved_questions = self._merge_unique(
            previous_summary.unresolved_questions,
            new_summary.unresolved_questions,
            limit=5,
        )
        recent_progress = self._merge_unique(
            previous_summary.recent_progress,
            new_summary.recent_progress,
            limit=4,
        )
        canonical_topic_keys = self._merge_unique(
            previous_summary.canonical_topic_keys,
            new_summary.canonical_topic_keys,
            limit=6,
        )
        topic_aliases = self._merge_alias_maps(
            previous_summary.topic_aliases,
            new_summary.topic_aliases,
        )
        learner_goal = new_summary.learner_goal or previous_summary.learner_goal
        preferred_help_style = (
            new_summary.preferred_help_style or previous_summary.preferred_help_style
        )
        last_user_message = new_summary.last_user_message or previous_summary.last_user_message
        return StructuredConversationSummary(
            topicFocus=topic_focus,
            canonicalTopicKeys=canonical_topic_keys,
            aliases=topic_aliases,
            learnerGoal=learner_goal,
            knownGaps=known_gaps,
            unresolvedQuestions=unresolved_questions,
            preferredHelpStyle=preferred_help_style,
            lastUserMessage=last_user_message,
            recentProgress=recent_progress,
            confidence=max(previous_summary.confidence, new_summary.confidence),
            summaryText=self._build_summary_text(
                topic_focus=topic_focus,
                learner_goal=learner_goal,
                unresolved_questions=unresolved_questions,
                known_gaps=known_gaps,
            ),
        )

    def _merge_unique(self, older: list[str], newer: list[str], *, limit: int) -> list[str]:
        merged: list[str] = []
        for item in [*older, *newer]:
            normalized = str(item).strip()
            if not normalized or normalized in merged:
                continue
            merged.append(normalized)
            if len(merged) >= limit:
                break
        return merged

    def _merge_alias_maps(
        self,
        older: dict[str, list[str]],
        newer: dict[str, list[str]],
    ) -> dict[str, list[str]]:
        merged: dict[str, list[str]] = {}
        for source in (older or {}, newer or {}):
            if not isinstance(source, dict):
                continue
            for key, aliases in source.items():
                normalized_key = str(key).strip()
                if not normalized_key:
                    continue
                merged.setdefault(normalized_key, [])
                if isinstance(aliases, list):
                    merged[normalized_key] = self._merge_unique(
                        merged[normalized_key],
                        [str(alias) for alias in aliases],
                        limit=8,
                    )
        return merged

    def _summary_confidence(
        self,
        *,
        topic_focus: list[str],
        known_gaps: list[str],
        unresolved_questions: list[str],
    ) -> float:
        score = 0.45
        if topic_focus:
            score += 0.18
        if known_gaps:
            score += 0.12
        if unresolved_questions:
            score += 0.08
        return max(0.2, min(0.9, round(score, 2)))

    def _should_refine_with_llm(
        self,
        *,
        messages: list[dict[str, Any]],
        result: CompactionResult,
    ) -> bool:
        if self.summary_refiner is None or not hasattr(self.summary_refiner, "refine"):
            return False
        user_messages = [
            str(message.get("content", "")).strip()
            for message in messages
            if str(message.get("role", "")).lower() == "user"
        ]
        if result.was_compacted:
            return True
        if len(user_messages) >= 3 and result.structured_summary.confidence < 0.55:
            return True
        ambiguous_markers = ("那个", "这个", "东西", "玩意", "互相等", "互相等待")
        return any(marker in message for marker in ambiguous_markers for message in user_messages[-4:])

    def _merge_refined_summary(
        self,
        rule_summary: StructuredConversationSummary,
        payload: Any,
    ) -> StructuredConversationSummary:
        if not isinstance(payload, dict):
            return rule_summary
        normalized_payload = dict(payload.get("summary") if isinstance(payload.get("summary"), dict) else payload)
        aliases = normalized_payload.get("aliases", {})
        if not isinstance(aliases, dict):
            normalized_payload["aliases"] = {}
        try:
            refined = StructuredConversationSummary.model_validate(normalized_payload)
        except Exception:
            return rule_summary

        if refined.topic_focus and not refined.canonical_topic_keys:
            canonical_topics = canonicalize_topics(refined.topic_focus)
            refined = refined.model_copy(
                update={
                    "topic_focus": [topic.display_name for topic in canonical_topics],
                    "canonical_topic_keys": [topic.canonical_key for topic in canonical_topics],
                    "topic_aliases": {
                        topic.canonical_key: topic.aliases
                        for topic in canonical_topics
                        if topic.canonical_key and topic.aliases
                    },
                }
            )

        topic_focus = self._merge_unique(refined.topic_focus, rule_summary.topic_focus, limit=6)
        known_gaps = self._merge_unique(refined.known_gaps, rule_summary.known_gaps, limit=5)
        unresolved_questions = self._merge_unique(
            refined.unresolved_questions,
            rule_summary.unresolved_questions,
            limit=5,
        )
        recent_progress = self._merge_unique(
            rule_summary.recent_progress,
            refined.recent_progress,
            limit=4,
        )
        learner_goal = refined.learner_goal or rule_summary.learner_goal
        preferred_help_style = refined.preferred_help_style or rule_summary.preferred_help_style
        last_user_message = rule_summary.last_user_message or refined.last_user_message
        summary_text = refined.summary_text or self._build_summary_text(
            topic_focus=topic_focus,
            learner_goal=learner_goal,
            unresolved_questions=unresolved_questions,
            known_gaps=known_gaps,
        )
        return StructuredConversationSummary(
            topicFocus=topic_focus,
            canonicalTopicKeys=self._merge_unique(
                refined.canonical_topic_keys,
                rule_summary.canonical_topic_keys,
                limit=6,
            ),
            aliases=self._merge_alias_maps(refined.topic_aliases, rule_summary.topic_aliases),
            learnerGoal=learner_goal,
            knownGaps=known_gaps,
            unresolvedQuestions=unresolved_questions,
            preferredHelpStyle=preferred_help_style,
            lastUserMessage=last_user_message,
            recentProgress=recent_progress,
            confidence=max(rule_summary.confidence, refined.confidence),
            summaryText=summary_text[: self.summary_max_chars],
        )

    def _extract_topic_candidates(self, message: str) -> list[tuple[str, str]]:
        candidates: list[tuple[str, str]] = []
        for topic in self._extract_by_patterns(message):
            if topic not in [item[0] for item in candidates]:
                candidates.append((topic, "pattern"))
        for topic in self._extract_identifiers(message):
            if topic not in [item[0] for item in candidates]:
                candidates.append((topic, "identifier"))
        if candidates:
            return candidates
        for topic in self._extract_fallback_topics(message):
            if topic not in [item[0] for item in candidates]:
                candidates.append((topic, "fallback"))
        return candidates

    def _extract_by_patterns(self, message: str) -> list[str]:
        patterns: list[tuple[re.Pattern[str], tuple[str, ...]]] = [
            (re.compile(r"什么是(?P<a>[^，。！？；:：]+)"), ("a",)),
            (re.compile(r"(?P<a>[^，。！？；:：]+?)是什么"), ("a",)),
            (re.compile(r"(?:请解释|解释一下)(?P<a>[^，。！？；:：]+)"), ("a",)),
            (re.compile(r"(?P<a>[^，。！？；:：]+?)具体怎么用"), ("a",)),
            (re.compile(r"(?P<a>[^，。！？；:：]+?)怎么用"), ("a",)),
            (re.compile(r"(?P<a>[^，。！？；:：]+?)如何用"), ("a",)),
            (re.compile(r"(?P<a>[^，。！？；:：]+?)怎么写"), ("a",)),
            (re.compile(r"(?P<a>[^，。！？；:：]+?)是怎么产生的"), ("a",)),
            (re.compile(r"(?P<a>[^，。！？；:：]+?)怎么产生的"), ("a",)),
            (re.compile(r"(?P<a>[^，。！？；:：]+?)怎么避免"), ("a",)),
            (re.compile(r"(?P<a>[^，。！？；:：]+?)怎么解决"), ("a",)),
            (re.compile(r"(?P<a>[^，。！？；:：]+?)的核心参数有哪些"), ("a", "__core_params__")),
            (re.compile(r"(?P<a>[^，。！？；:：]+?)有哪些"), ("a",)),
            (re.compile(r"(?:回到|继续说|继续讲|总结一下今天学到的|总结一下)(?P<a>[^，。！？；:：]+)"), ("a",)),
            (
                re.compile(
                    r"(?P<a>[^，。！？；:：]+?)\s*(?:和|与)\s*(?P<b>[^，。！？；:：]+?)\s*(?:有什么区别|有什么不同|区别|不同)"
                ),
                ("a", "b"),
            ),
        ]
        extracted: list[str] = []
        for pattern, fields in patterns:
            match = pattern.search(message)
            if match is None:
                continue
            for field in fields:
                if field == "__core_params__":
                    base = self._normalize_topic_chunk(match.group("a"))
                    if base:
                        extracted.extend(self._dedupe_topics([f"{base}核心参数", base]))
                    continue
                normalized = self._normalize_topic_chunk(match.group(field))
                if normalized:
                    extracted.append(normalized)
        return self._dedupe_topics(extracted)

    def _extract_identifiers(self, message: str) -> list[str]:
        identifiers: list[str] = []
        for token in re.findall(r"\b[A-Za-z][A-Za-z0-9_]{1,31}\b", message):
            normalized = self._normalize_topic_chunk(token)
            if normalized:
                identifiers.append(normalized)
        return self._dedupe_topics(identifiers)

    def _extract_fallback_topics(self, message: str) -> list[str]:
        normalized_message = self._normalize_topic_chunk(message)
        if not normalized_message:
            return []
        topics: list[str] = []
        if any(connector in normalized_message for connector in ("和", "与", "及", "以及")):
            for piece in re.split(r"(?:和|与|及|以及)", normalized_message):
                normalized_piece = self._normalize_topic_chunk(piece)
                if normalized_piece:
                    topics.append(normalized_piece)
        topics.append(normalized_message)
        return self._dedupe_topics(topics)

    def _score_topic_candidate(self, candidate: TopicCandidate) -> int:
        score_map = {"pattern": 4, "identifier": 3, "fallback": 1}
        score = score_map.get(candidate.source, 0)
        if candidate.recency_rank < 2:
            score += 1
        if self._looks_like_technical_topic(candidate.text):
            score += 2
        if self._is_generic_topic(candidate.text):
            score -= 2
        if self._looks_like_full_question(candidate.text):
            score -= 2
        if len(candidate.text) > 16 and not self._is_identifier(candidate.text):
            score -= 1
        return score

    def _looks_like_technical_topic(self, text: str) -> bool:
        technical_markers = (
            "线程",
            "锁",
            "并发",
            "同步",
            "参数",
            "关键字",
            "线程池",
            "可见性",
            "原子",
            "死锁",
            "volatile",
            "synchronized",
        )
        return any(marker.lower() in text.lower() for marker in technical_markers)

    def _is_generic_topic(self, text: str) -> bool:
        generic_terms = {"这个", "那个", "问题", "东西", "内容", "情况", "知识点"}
        return text in generic_terms

    def _looks_like_full_question(self, text: str) -> bool:
        question_markers = ("什么", "怎么", "如何", "为什么", "哪些", "哪个", "能否", "可否", "吗")
        return any(marker in text for marker in question_markers) or "?" in text or "？" in text

    def _is_identifier(self, text: str) -> bool:
        return re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{1,31}", text) is not None

    def _dedupe_topics(self, topics: list[str]) -> list[str]:
        deduped: list[str] = []
        for topic in topics:
            if topic and topic not in deduped:
                deduped.append(topic)
        return deduped

    def _normalize_topic_chunk(self, chunk: str) -> str:
        normalized = str(chunk).strip()
        if not normalized:
            return ""
        normalized = re.sub(
            r"^(那我前面问的|回到|总结一下今天学到的|总结一下|继续上次的话题|继续说|继续讲|请问|请解释|解释一下|什么是)",
            "",
            normalized,
        )
        normalized = re.sub(
            r"(的核心参数有哪些|核心参数有哪些|具体怎么用|如何用|怎么写|是怎么产生的|怎么产生的|怎么避免|怎么解决|怎么用|有什么区别|有什么不同|区别|不同|有哪些|是什么|能再举个例子吗|能再举个例子|举个例子吗|举个例子|还有别的解决办法吗|还有别的解决办法)$",
            "",
            normalized,
        )
        normalized = re.sub(r"^(的|了|呢|吗|呀|啊)+|(?:的|了|呢|吗|呀|啊|问题)+$", "", normalized)
        normalized = normalized.strip(" ,，。？?:：;；|")
        if not normalized:
            return ""
        if self._is_identifier(normalized):
            return normalized
        normalized = re.sub(r"\s+", "", normalized)
        if len(normalized) < 2 or len(normalized) > 16:
            return ""
        if self._is_generic_topic(normalized):
            return ""
        return normalized
