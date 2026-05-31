"""Critic and safety review agents for generated learning content."""

from __future__ import annotations

from collections.abc import AsyncIterator
import logging
from typing import Any

from src.ai_modules.agents.base import PlaceholderAgent
from src.ai_modules.llms import CriticReviewer, SafetyReviewer
from src.ai_modules.models import (
    CriticReviewPayload,
    ProgressPayload,
    ProgressSSEEvent,
    ResultChunkPayload,
    ResultChunkSSEEvent,
    SafetyReviewPayload,
    SSEEvent,
)
from src.ai_modules.prompts import build_critic_system_prompt, build_safety_system_prompt
from src.ai_modules.runtime import SystemSnapshot


ACADEMIC_MISCONDUCT_KEYWORDS = ("作弊", "代写", "替考", "考试答案", "绕过检测")
BOUNDARY_RISK_KEYWORDS = ("攻击", "破解", "绕过", "注入", "提权", "爆破")

LOGGER = logging.getLogger(__name__)


class CriticAgent(PlaceholderAgent):
    """Review generated content quality, difficulty fit, and source support."""

    def __init__(
        self,
        llm_client: Any | None = None,
        reviewer: Any | None = None,
    ) -> None:
        super().__init__("Critic Agent", "critic")
        self.llm_client = llm_client
        self.reviewer = reviewer

    def system_prompt(self, snapshot: SystemSnapshot) -> str:
        return build_critic_system_prompt(snapshot)

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
        payload = await self.review_content(
            params=params,
            snapshot=snapshot,
            system_prompt=system_prompt,
        )
        params["criticReview"] = payload.model_dump(by_alias=True)

        yield ProgressSSEEvent(
            taskId=task_id,
            traceId=trace_id,
            seq=seq,
            payload=ProgressPayload(
                stage=self.stage_name,
                percent=96,
                message="已完成内容质量复核",
            ),
        )
        yield ResultChunkSSEEvent(
            taskId=task_id,
            traceId=trace_id,
            seq=seq + 1,
            payload=ResultChunkPayload(text=payload.summary_text),
        )

    async def review_content(
        self,
        *,
        params: dict[str, Any],
        snapshot: SystemSnapshot,
        system_prompt: str,
    ) -> CriticReviewPayload:
        review_signals = self._collect_critic_signals(params=params, snapshot=snapshot)
        try:
            return await self._reviewer().review(
                system_prompt=system_prompt,
                context_payload=self._build_critic_context(
                    params=params,
                    snapshot=snapshot,
                    review_signals=review_signals,
                ),
            )
        except Exception as exc:
            LOGGER.exception("Critic review LLM failed")
            raise RuntimeError("Critic review LLM failed; heuristic fallback is disabled") from exc

    def _tool_check_fact_consistency(
        self,
        *,
        tool_input: dict[str, Any],
        params: dict[str, Any],
    ) -> dict[str, Any]:
        del tool_input
        content = self._content_text(params)
        sources = self._source_titles(params)
        issues: list[str] = []
        status = "SUPPORTED"
        if not content:
            status = "UNCLEAR"
            issues.append("缺少可复核的正文内容。")
        if not sources:
            status = "UNCLEAR"
            issues.append("缺少检索来源，事实支撑不足。")
        return {
            "status": status,
            "issues": issues,
            "evidence": f"contentLength={len(content)}, sourceCount={len(sources)}",
        }

    def _tool_check_difficulty_match(
        self,
        *,
        tool_input: dict[str, Any],
        params: dict[str, Any],
        snapshot: SystemSnapshot,
    ) -> dict[str, Any]:
        del tool_input
        student_level = self._student_level(params=params, snapshot=snapshot)
        content = self._content_text(params)
        sentence_count = max(content.count("\n"), 1)
        status = "MATCHED"
        issues: list[str] = []
        if student_level == "BASIC" and len(content) > 5000:
            status = "TOO_COMPLEX"
            issues.append("对基础学生来说内容偏长，建议拆成更小步骤。")
        if student_level == "ADVANCED" and sentence_count < 5:
            status = "TOO_SIMPLE"
            issues.append("对高阶学生来说内容过于简略，建议增加原理或变式。")
        return {
            "status": status,
            "issues": issues,
            "evidence": f"studentLevel={student_level}, lineCount={sentence_count}",
        }

    def _tool_review_source_coverage(
        self,
        *,
        tool_input: dict[str, Any],
        params: dict[str, Any],
    ) -> dict[str, Any]:
        del tool_input
        sources = self._source_titles(params)
        content = self._content_text(params)
        cited_sources = [title for title in sources if title and title in content]
        status = "GOOD" if len(sources) >= 2 else "LIMITED"
        issues: list[str] = []
        if len(sources) < 2:
            issues.append("来源数量偏少，建议补充更多证据。")
        if sources and not cited_sources:
            issues.append("正文未显式体现来源标题，来源覆盖感知较弱。")
        return {
            "status": status,
            "issues": issues,
            "evidence": {
                "sourceCount": len(sources),
                "citedSourceCount": len(cited_sources),
            },
        }

    async def _tool_synthesize_review(
        self,
        *,
        tool_input: dict[str, Any],
        params: dict[str, Any],
        snapshot: SystemSnapshot,
        system_prompt: str,
    ) -> dict[str, Any]:
        del tool_input
        payload = await self.review_content(
            params=params,
            snapshot=snapshot,
            system_prompt=system_prompt,
        )
        return payload.model_dump(by_alias=True)

    def _build_critic_context(
        self,
        *,
        params: dict[str, Any],
        snapshot: SystemSnapshot,
        review_signals: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "asset": params.get("generatedAsset", {}),
            "query": params.get("query"),
            "rewrittenQuery": params.get("rewrittenQuery"),
            "studentLevel": self._student_level(params=params, snapshot=snapshot),
            "sources": self._source_titles(params),
            "contentPreview": self._content_text(params)[:1500],
            "reviewSignals": review_signals,
        }

    def _reviewer(self) -> Any:
        if self.reviewer is None:
            self.reviewer = CriticReviewer()
        return self.reviewer

    def _collect_critic_signals(
        self,
        *,
        params: dict[str, Any],
        snapshot: SystemSnapshot,
    ) -> dict[str, Any]:
        return {
            "factConsistency": self._tool_check_fact_consistency(tool_input={}, params=params),
            "difficultyMatch": self._tool_check_difficulty_match(
                tool_input={},
                params=params,
                snapshot=snapshot,
            ),
            "sourceCoverage": self._tool_review_source_coverage(tool_input={}, params=params),
        }

    def _content_text(self, params: dict[str, Any]) -> str:
        final_answer = params.get("finalAnswer")
        if final_answer:
            return str(final_answer)
        content = params.get("generatedContent")
        if content:
            return str(content)
        generated_asset = params.get("generatedAsset", {})
        if not isinstance(generated_asset, dict):
            return ""
        return "\n".join(
            [
                str(generated_asset.get("title") or ""),
                str(generated_asset.get("summary") or ""),
                str(generated_asset.get("previewText") or ""),
            ]
        ).strip()

    def _source_titles(self, params: dict[str, Any]) -> list[str]:
        retrieval_result = params.get("retrievalResult", {})
        documents = retrieval_result.get("documents", []) if isinstance(retrieval_result, dict) else []
        return [
            str(document.get("title", "")).strip()
            for document in documents
            if isinstance(document, dict) and str(document.get("title", "")).strip()
        ]

    def _student_level(self, *, params: dict[str, Any], snapshot: SystemSnapshot) -> str:
        profile = params.get("profile", {})
        return str(profile.get("studentLevel") or snapshot.student_level or "BASIC")


class SafetyAgent(PlaceholderAgent):
    """Review generated content for boundary, compliance, and academic risks."""

    def __init__(
        self,
        llm_client: Any | None = None,
        reviewer: Any | None = None,
    ) -> None:
        super().__init__("Safety Agent", "safety")
        self.llm_client = llm_client
        self.reviewer = reviewer

    def system_prompt(self, snapshot: SystemSnapshot) -> str:
        return build_safety_system_prompt(snapshot)

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
        payload = await self.review_content(
            params=params,
            snapshot=snapshot,
            system_prompt=system_prompt,
        )
        params["safetyReview"] = payload.model_dump(by_alias=True)

        yield ProgressSSEEvent(
            taskId=task_id,
            traceId=trace_id,
            seq=seq,
            payload=ProgressPayload(
                stage=self.stage_name,
                percent=97,
                message="已完成安全复核",
            ),
        )
        yield ResultChunkSSEEvent(
            taskId=task_id,
            traceId=trace_id,
            seq=seq + 1,
            payload=ResultChunkPayload(text=payload.summary_text),
        )

    async def review_content(
        self,
        *,
        params: dict[str, Any],
        snapshot: SystemSnapshot,
        system_prompt: str,
    ) -> SafetyReviewPayload:
        review_signals = self._collect_safety_signals(params=params)
        try:
            return await self._reviewer().review(
                system_prompt=system_prompt,
                context_payload=self._build_safety_context(
                    params=params,
                    snapshot=snapshot,
                    review_signals=review_signals,
                ),
            )
        except Exception as exc:
            LOGGER.exception("Safety review LLM failed")
            raise RuntimeError("Safety review LLM failed; heuristic fallback is disabled") from exc

    def _tool_classify_content(
        self,
        *,
        tool_input: dict[str, Any],
        params: dict[str, Any],
    ) -> dict[str, Any]:
        del tool_input
        generated_asset = params.get("generatedAsset", {})
        asset_type = str(generated_asset.get("assetType") or "DOCUMENT")
        return {
            "categories": ["educational_content", asset_type.lower()],
            "contentType": asset_type,
        }

    def _tool_detect_boundary_risk(
        self,
        *,
        tool_input: dict[str, Any],
        params: dict[str, Any],
    ) -> dict[str, Any]:
        del tool_input
        text = self._content_text(params)
        hits = [keyword for keyword in BOUNDARY_RISK_KEYWORDS if keyword in text]
        return {
            "riskLevel": "HIGH" if hits else "LOW",
            "riskTags": hits,
            "issues": ["内容包含潜在越界操作提示。"] if hits else [],
        }

    def _tool_filter_academic_misconduct(
        self,
        *,
        tool_input: dict[str, Any],
        params: dict[str, Any],
    ) -> dict[str, Any]:
        del tool_input
        text = " ".join(
            [
                self._content_text(params),
                str(params.get("query") or ""),
                str(params.get("rewrittenQuery") or ""),
            ]
        )
        hits = [keyword for keyword in ACADEMIC_MISCONDUCT_KEYWORDS if keyword in text]
        return {
            "blocked": bool(hits),
            "riskTags": hits,
            "issues": ["内容疑似提供学术违规或作弊协助。"] if hits else [],
        }

    async def _tool_synthesize_review(
        self,
        *,
        tool_input: dict[str, Any],
        params: dict[str, Any],
        snapshot: SystemSnapshot,
        system_prompt: str,
    ) -> dict[str, Any]:
        del tool_input
        payload = await self.review_content(
            params=params,
            snapshot=snapshot,
            system_prompt=system_prompt,
        )
        return payload.model_dump(by_alias=True)

    def _build_safety_context(
        self,
        *,
        params: dict[str, Any],
        snapshot: SystemSnapshot,
        review_signals: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "asset": params.get("generatedAsset", {}),
            "query": params.get("query"),
            "studentLevel": self._student_level(params=params, snapshot=snapshot),
            "contentPreview": self._content_text(params)[:1500],
            "reviewSignals": review_signals,
        }

    def _reviewer(self) -> Any:
        if self.reviewer is None:
            self.reviewer = SafetyReviewer()
        return self.reviewer

    def _collect_safety_signals(self, *, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "contentClassification": self._tool_classify_content(tool_input={}, params=params),
            "boundaryRisk": self._tool_detect_boundary_risk(tool_input={}, params=params),
            "academicMisconduct": self._tool_filter_academic_misconduct(
                tool_input={},
                params=params,
            ),
        }

    def _content_text(self, params: dict[str, Any]) -> str:
        content = params.get("generatedContent")
        if content:
            return str(content)
        generated_asset = params.get("generatedAsset", {})
        if not isinstance(generated_asset, dict):
            return ""
        return "\n".join(
            [
                str(generated_asset.get("title") or ""),
                str(generated_asset.get("summary") or ""),
                str(generated_asset.get("previewText") or ""),
            ]
        ).strip()

    def _student_level(self, *, params: dict[str, Any], snapshot: SystemSnapshot) -> str:
        profile = params.get("profile", {})
        return str(profile.get("studentLevel") or snapshot.student_level or "BASIC")
