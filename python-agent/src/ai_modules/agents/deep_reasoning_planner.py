"""Deep reasoning planner that turns DEEP mode into reusable tutor context."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

from src.ai_modules.agents.base import PlaceholderAgent
from src.ai_modules.config import get_settings
from src.ai_modules.llms import OpenAICompatibleJSONGenerator
from src.ai_modules.llms.json_utils import dumps_json
from src.ai_modules.models import ProgressPayload, ProgressSSEEvent, SSEEvent
from src.ai_modules.runtime import SnapshotBuilder, SystemSnapshot

LOGGER = logging.getLogger(__name__)

CONTEXT_FIELDS = (
    "problemFrame",
    "assumptions",
    "evidenceIds",
    "missingInfo",
    "reasoningPlan",
    "critiqueChecks",
    "answerConstraints",
)


class DeepReasoningPlanner(PlaceholderAgent):
    """Build a compact reasoning brief that later Tutor prompts can consume."""

    def __init__(self, generator: Any | None = None) -> None:
        super().__init__("Deep Reasoning Planner", "deep_reasoning")
        self.generator = generator

    def system_prompt(self, snapshot: SystemSnapshot) -> str:
        context = SnapshotBuilder.render_prompt_context(snapshot)
        return "\n".join(
            [
                "你是 Deep Reasoning Planner，负责把深度思考模式转成可执行的回答 brief。",
                "只输出 JSON，不要输出 markdown、代码块或逐步隐性推理。",
                "JSON 字段必须为 problemFrame、assumptions、evidenceIds、missingInfo、reasoningPlan、critiqueChecks、answerConstraints。",
                "reasoningPlan 写可公开的解题/讲解结构，不要写内部 chain-of-thought。",
                "critiqueChecks 写最终回答前必须自检的项目。",
                context,
            ]
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
        del service_type, snapshot
        try:
            raw_context = await self._generate_context(params=params, system_prompt=system_prompt)
            context = self._normalize_context(raw_context, params=params)
            params["deepReasoningContext"] = context
            message = "深度思考上下文已生成" if context else "深度思考上下文为空，继续普通回答"
        except Exception:
            LOGGER.warning("Deep reasoning planner failed; continuing without planner context.", exc_info=True)
            params["deepReasoningContext"] = {}
            message = "深度思考上下文生成失败，已降级继续回答"

        yield ProgressSSEEvent(
            taskId=task_id,
            traceId=trace_id,
            seq=seq,
            payload=ProgressPayload(
                stage=self.stage_name,
                percent=55,
                message=message,
                agentName="deep_reasoning_planner",
                phase="brief",
                status="DONE",
            ),
        )

    async def _generate_context(self, *, params: dict[str, Any], system_prompt: str) -> dict[str, Any]:
        generator = self.generator or self._create_generator()
        return await generator.generate(
            system_prompt=system_prompt,
            user_prompt="\n".join(
                [
                    "请基于以下上下文生成深度思考 brief JSON。",
                    "brief 必须用于后续 Tutor 最终回答，不要泄露内部逐步推理。",
                    dumps_json(self._build_prompt_payload(params), ensure_ascii=False),
                ]
            ),
            max_tokens=900,
        )

    def _create_generator(self) -> OpenAICompatibleJSONGenerator:
        settings = get_settings()
        provider_name = settings.resolve_component_provider("tutor_llm")
        if not settings.provider_ready(provider_name):
            raise RuntimeError("tutor_llm provider is not ready")
        model_name = settings.resolve_component_model(
            "tutor_llm",
            default_logical_model="main_chat_model",
            provider_name=provider_name,
        )
        return OpenAICompatibleJSONGenerator(
            model_name=model_name,
            provider_name=provider_name,
            temperature=0.1,
            cache_namespace="deep_reasoning_context",
        )

    def _build_prompt_payload(self, params: dict[str, Any]) -> dict[str, Any]:
        retrieval_result = params.get("retrievalResult")
        retrieval_result = retrieval_result if isinstance(retrieval_result, dict) else {}
        documents = retrieval_result.get("documents")
        documents = documents if isinstance(documents, list) else []
        return {
            "query": self._first_text(params, "query", "message", "userInput", "question"),
            "rewrittenQuery": self._first_text(params, "rewrittenQuery"),
            "queryClassification": self._safe_dict(params.get("queryClassification")),
            "retrievalStrategy": str(params.get("retrievalStrategy") or ""),
            "retrievalSummary": self._truncate_text(
                str(params.get("retrievalSummaryText") or retrieval_result.get("sourcesSummary") or ""),
                1200,
            ),
            "retrievalEvidence": self._compact_evidence(params.get("retrievalEvidence")),
            "documents": self._compact_documents(documents),
            "recentDialogue": self._compact_dialogue(params),
            "profile": self._safe_dict(params.get("profileAnalysis") or params.get("profile")),
            "masteryDiagnosis": self._safe_dict(params.get("masteryDiagnosis")),
            "imageAnalysis": self._safe_dict(params.get("imageAnalysisResult")),
        }

    def _normalize_context(self, payload: dict[str, Any], *, params: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        context = {
            "problemFrame": self._truncate_text(str(payload.get("problemFrame") or "").strip(), 800),
            "assumptions": self._normalize_text_list(payload.get("assumptions"), max_items=5, max_chars=180),
            "evidenceIds": self._normalize_text_list(payload.get("evidenceIds"), max_items=6, max_chars=120),
            "missingInfo": self._normalize_text_list(payload.get("missingInfo"), max_items=5, max_chars=180),
            "reasoningPlan": self._normalize_text_list(payload.get("reasoningPlan"), max_items=6, max_chars=220),
            "critiqueChecks": self._normalize_text_list(payload.get("critiqueChecks"), max_items=5, max_chars=180),
            "answerConstraints": self._normalize_text_list(payload.get("answerConstraints"), max_items=5, max_chars=180),
        }
        if not context["evidenceIds"]:
            context["evidenceIds"] = self._infer_evidence_ids(params)
        if not any(context[field] for field in CONTEXT_FIELDS):
            return {}
        return context

    def _compact_documents(self, documents: list[Any]) -> list[dict[str, Any]]:
        compacted: list[dict[str, Any]] = []
        for index, document in enumerate(documents[:5], start=1):
            if not isinstance(document, dict):
                continue
            evidence = str(document.get("evidence") or document.get("snippet") or "")
            compacted.append(
                {
                    "id": self._document_id(document, fallback=f"doc-{index}"),
                    "title": self._truncate_text(str(document.get("title") or ""), 120),
                    "slug": self._truncate_text(str(document.get("slug") or ""), 120),
                    "channel": self._truncate_text(str(document.get("channel") or ""), 60),
                    "evidence": self._truncate_text(evidence, 500),
                    "url": self._truncate_text(str(document.get("url") or ""), 180),
                    "sourceTitle": self._truncate_text(str(document.get("sourceTitle") or ""), 120),
                }
            )
        return compacted

    def _compact_evidence(self, evidence: Any) -> list[dict[str, Any]]:
        if not isinstance(evidence, list):
            return []
        compacted: list[dict[str, Any]] = []
        for index, item in enumerate(evidence[:6], start=1):
            if not isinstance(item, dict):
                continue
            compacted.append(
                {
                    "id": self._document_id(item, fallback=f"evidence-{index}"),
                    "title": self._truncate_text(str(item.get("title") or ""), 120),
                    "channel": self._truncate_text(str(item.get("channel") or ""), 60),
                    "evidence": self._truncate_text(str(item.get("evidence") or item.get("snippet") or ""), 500),
                }
            )
        return compacted

    def _compact_dialogue(self, params: dict[str, Any]) -> list[dict[str, str]]:
        messages = params.get("messages") or params.get("conversation") or []
        if not isinstance(messages, list):
            return []
        compacted: list[dict[str, str]] = []
        for item in messages[-6:]:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "")
            content = self._truncate_text(str(item.get("content") or ""), 240)
            if role in {"user", "assistant"} and content:
                compacted.append({"role": role, "content": content})
        return compacted

    def _infer_evidence_ids(self, params: dict[str, Any]) -> list[str]:
        retrieval_result = params.get("retrievalResult")
        documents = retrieval_result.get("documents") if isinstance(retrieval_result, dict) else []
        documents = documents if isinstance(documents, list) else []
        ids: list[str] = []
        for index, document in enumerate(documents[:5], start=1):
            if isinstance(document, dict):
                ids.append(self._document_id(document, fallback=f"doc-{index}"))
        return ids

    def _document_id(self, document: dict[str, Any], *, fallback: str) -> str:
        for key in ("id", "slug", "title", "url"):
            value = str(document.get(key) or "").strip()
            if value:
                return self._truncate_text(value, 120)
        return fallback

    def _normalize_text_list(self, value: Any, *, max_items: int, max_chars: int) -> list[str]:
        if isinstance(value, str):
            candidates = [value]
        elif isinstance(value, list):
            candidates = value
        else:
            return []
        normalized: list[str] = []
        seen: set[str] = set()
        for item in candidates:
            text = self._truncate_text(str(item or "").strip(), max_chars)
            if not text or text in seen:
                continue
            seen.add(text)
            normalized.append(text)
            if len(normalized) >= max_items:
                break
        return normalized

    def _first_text(self, params: dict[str, Any], *keys: str) -> str:
        for key in keys:
            value = params.get(key)
            if isinstance(value, str) and value.strip():
                return self._truncate_text(value.strip(), 600)
        return ""

    @staticmethod
    def _safe_dict(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _truncate_text(value: str, max_chars: int) -> str:
        normalized = " ".join(str(value or "").split())
        if len(normalized) <= max_chars:
            return normalized
        return normalized[: max_chars - 1].rstrip() + "…"
