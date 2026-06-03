"""查询改写 Agent 实现。"""

from __future__ import annotations

from collections.abc import AsyncIterator
import logging
from typing import Any

from src.ai_modules.agents.base import PlaceholderAgent
from src.ai_modules.llms import (
    QueryRewriteToolLLMClientFactory,
    QueryRewriteGenerator,
)
from src.ai_modules.models import (
    ProgressPayload,
    ProgressSSEEvent,
    QueryRewriteResult,
    ResultChunkPayload,
    ResultChunkSSEEvent,
    SSEEvent,
)
from src.ai_modules.prompts import build_query_rewrite_prompt
from src.ai_modules.retrieval import QueryRewriteService
from src.ai_modules.runtime import (
    SystemSnapshot,
)
from src.ai_modules.runtime.skill_loader import SkillPromptLoader

LOGGER = logging.getLogger(__name__)


class QueryRewriteAgent(PlaceholderAgent):
    """在混合检索器运行前改写检索查询。"""

    def __init__(
        self,
        service: QueryRewriteService | None = None,
        llm_client: Any | None = None,
        llm_generator: Any | None = None,
    ) -> None:
        super().__init__("Query Rewrite Agent", "query_rewrite")
        self.service = service or QueryRewriteService()
        self.llm_client = llm_client or QueryRewriteToolLLMClientFactory.create()
        self.llm_generator = llm_generator
        self.skill_loader = SkillPromptLoader()

    def system_prompt(self, snapshot: SystemSnapshot) -> str:
        return self.skill_loader.build_system_prompt(
            skill_name="query_rewrite",
            snapshot=snapshot,
            fallback_prompt=build_query_rewrite_prompt(snapshot),
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
        rewrite_result = await self._run_agent_core_loop(
            params=params,
            snapshot=snapshot,
            system_prompt=system_prompt,
        )
        params["query"] = rewrite_result.original_query
        params["rewrittenQuery"] = rewrite_result.rewritten_query
        params["keywords"] = rewrite_result.keywords

        yield ProgressSSEEvent(
            taskId=task_id,
            traceId=trace_id,
            seq=seq,
            payload=ProgressPayload(
                stage=self.stage_name,
                percent=20,
                message="查询改写完成",
            ),
        )
        yield ResultChunkSSEEvent(
            taskId=task_id,
            traceId=trace_id,
            seq=seq + 1,
            payload=ResultChunkPayload(
                text=(
                    f"原始查询: {rewrite_result.original_query}；"
                    f"改写后: {rewrite_result.rewritten_query}；"
                    f"关键词: {', '.join(rewrite_result.keywords)}"
                )
            ),
        )

    async def _run_agent_core_loop(
        self,
        *,
        params: dict[str, Any],
        snapshot: SystemSnapshot,
        system_prompt: str,
    ):
        # 步骤 1: 提取查询上下文（确定性操作）
        context = self._tool_extract_query_context(tool_input={}, params=params)
        original_query = context["originalQuery"]

        # 步骤 2: 通过 LLM 改写查询（1 次 LLM 调用）
        try:
            rewritten_payload = await self._tool_rewrite_query(
                tool_input={},
                params=params,
                snapshot=snapshot,
                system_prompt=system_prompt,
            )
        except Exception:
            LOGGER.warning("LLM query rewrite failed, falling back to direct rewrite.", exc_info=True)
            return self.service.rewrite(params)

        # 步骤 3: 验证结果（确定性操作）
        try:
            return self._tool_finalize_rewrite(rewritten_payload)
        except Exception:
            LOGGER.warning("Query rewrite validation failed, falling back.", exc_info=True)
            return self.service.rewrite(params)

    def _tool_extract_query_context(
        self,
        *,
        tool_input: dict[str, Any],
        params: dict[str, Any],
    ) -> dict[str, Any]:
        del tool_input
        learning_context = params.get("learningContext", {})
        if not isinstance(learning_context, dict):
            learning_context = {}
        diagnosis_context = self._extract_diagnosis_context(params.get("masteryDiagnosis"))
        profile_context = self._extract_profile_context(params.get("profileAnalysis") or params.get("profile"))
        original_query = self.service.extract_query(params)
        context = {
            "originalQuery": original_query,
            "learningContext": {
                **learning_context,
                **profile_context,
                **diagnosis_context,
            },
            "profileAnalysis": profile_context,
            "masteryDiagnosis": diagnosis_context,
            "course": learning_context.get("course"),
            "chapter": learning_context.get("chapter"),
        }
        params["queryRewriteContext"] = context
        return context

    async def _tool_rewrite_query(
        self,
        *,
        tool_input: dict[str, Any],
        params: dict[str, Any],
        snapshot: SystemSnapshot,
        system_prompt: str,
    ) -> dict[str, Any]:
        del snapshot
        context = params.get("queryRewriteContext") or tool_input
        original_query = str(context.get("originalQuery") or self.service.extract_query(params))
        try:
            generator = self.llm_generator or QueryRewriteGenerator()
            rewritten = await generator.rewrite(
                system_prompt=system_prompt,
                original_query=original_query,
                learning_context=context.get("learningContext", {}),
            )
            payload = rewritten.model_dump(by_alias=True)
        except Exception:
            payload = self.service.rewrite(params).model_dump(by_alias=True)
        params["rewrittenQueryPayload"] = payload
        return payload

    def _tool_finalize_rewrite(self, tool_input: dict[str, Any]) -> QueryRewriteResult:
        return QueryRewriteResult.model_validate(tool_input)

    def _extract_diagnosis_context(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        focus: list[Any] = []
        weaknesses: list[Any] = []
        resource_types: list[Any] = []
        diagnoses = value.get("knowledgeDiagnoses")
        if isinstance(diagnoses, list):
            sorted_diagnoses = [item for item in diagnoses if isinstance(item, dict)]
            sorted_diagnoses.sort(
                key=lambda item: (
                    self._safe_int(item.get("priority"), default=999),
                    self._safe_float(item.get("masteryScore"), default=1.0),
                )
            )
            for diagnosis in sorted_diagnoses:
                focus.extend([diagnosis.get("nextFocus"), diagnosis.get("knowledgePoint")])
                score = self._safe_float(diagnosis.get("masteryScore"))
                status = str(diagnosis.get("status") or "").strip().lower()
                if score is None or score < 0.75 or status in {"weak", "at_risk", "not_mastered", "low"}:
                    weaknesses.extend([diagnosis.get("knowledgePoint"), diagnosis.get("nextFocus")])
                recommended = diagnosis.get("recommendedResourceTypes")
                if isinstance(recommended, list):
                    resource_types.extend(recommended)
        target_scope = value.get("targetScope")
        if isinstance(target_scope, dict) and isinstance(target_scope.get("knowledgePoints"), list):
            focus.extend(target_scope["knowledgePoints"])
        hints = value.get("planAdjustmentHints")
        strategy = hints.get("strategy") if isinstance(hints, dict) else ""
        return {
            "diagnosisFocus": self._unique_items(focus)[:5],
            "diagnosisWeaknesses": self._unique_items(weaknesses)[:5],
            "recommendedResourceTypes": self._unique_items(resource_types)[:5],
            "diagnosisStrategy": str(strategy or "").strip(),
        }

    def _extract_profile_context(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        return {
            "profileWeakPoints": self._unique_items(
                list(value.get("weakPoints") or []) + list(value.get("knowledgeGaps") or [])
            )[:5],
            "learningPreference": str(value.get("learningPreference") or value.get("preferredStyle") or "").strip(),
        }

    def _unique_items(self, items: list[Any]) -> list[str]:
        seen: set[str] = set()
        normalized: list[str] = []
        for item in items:
            text = str(item or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            normalized.append(text)
        return normalized

    def _safe_float(self, value: Any, default: float | None = None) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _safe_int(self, value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
