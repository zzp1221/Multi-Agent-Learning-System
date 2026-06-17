"""Conversation context helpers used by TutorAgent."""

from __future__ import annotations

from typing import Any


def extract_conversation(params: dict[str, Any]) -> list[dict[str, Any]]:
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


def resolve_user_query(params: dict[str, Any]) -> str:
    return str(
        params.get("query")
        or params.get("message")
        or params.get("rewrittenQuery")
        or params.get("structuredConversationSummary", {}).get("lastUserMessage")
        or "褰撳墠涓婚"
    )


def select_recent_turns(
    *,
    conversation: list[dict[str, Any]],
    user_query: str,
    max_turns: int = 4,
    max_text_length: int = 220,
) -> list[dict[str, str]]:
    normalized_query = "".join(str(user_query).split())
    trimmed = list(conversation)
    if trimmed:
        last_item = trimmed[-1]
        last_content = "".join(str(last_item.get("content") or "").split())
        if last_item.get("role") == "user" and last_content == normalized_query:
            trimmed = trimmed[:-1]
    recent_turns = trimmed[-max_turns:]
    selected: list[dict[str, str]] = []
    for item in recent_turns:
        role = str(item.get("role") or "user")
        content = truncate_dialogue_text(str(item.get("content") or ""), max_text_length)
        if role not in {"user", "assistant"} or not content:
            continue
        selected.append({"role": role, "content": content})
    return selected


def infer_teaching_state(
    *,
    recent_messages: list[dict[str, str]],
    user_query: str,
    looks_like_question,
) -> dict[str, Any]:
    last_assistant_question = ""
    for item in reversed(recent_messages):
        if item.get("role") == "assistant":
            content = str(item.get("content") or "").strip()
            if looks_like_question(content):
                last_assistant_question = content
                break
    normalized_query = str(user_query).strip()
    likely_answer = bool(normalized_query) and not looks_like_question(normalized_query)
    awaiting_user_answer = bool(last_assistant_question) and likely_answer
    return {
        "lastAssistantQuestion": last_assistant_question,
        "awaitingUserAnswer": awaiting_user_answer,
        "currentUserIntent": "answer_previous_question" if awaiting_user_answer else "ask_or_shift_topic",
    }


def truncate_dialogue_text(text: str, max_length: int = 220) -> str:
    normalized = " ".join(str(text).split())
    if len(normalized) <= max_length:
        return normalized
    return normalized[: max_length - 1].rstrip() + "…"
