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
from src.ai_modules.runtime.skill_loader import append_user_skill_to_prompt


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
        return append_user_skill_to_prompt(
            build_critic_system_prompt(snapshot),
            component_name="review_llm",
            ability_key="ability:generation",
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
            payload = await self._reviewer().review(
                system_prompt=system_prompt,
                context_payload=self._build_critic_context(
                    params=params,
                    snapshot=snapshot,
                    review_signals=review_signals,
                ),
            )
            return self._merge_structured_scores(payload=payload, review_signals=review_signals)
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
            "learningPath": self._safe_dict(params.get("learningPath")) or {},
            "masteryDiagnosis": self._safe_dict(params.get("masteryDiagnosis")) or {},
            "resourcePushPlan": self._safe_dict(params.get("resourcePushPlan")) or {},
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
            "learningPathCoverage": self._tool_review_learning_path_coverage(params=params),
            "pathOrder": self._tool_review_path_order(params=params),
            "resourceMatch": self._tool_review_resource_match(params=params),
        }

    def _tool_review_learning_path_coverage(self, *, params: dict[str, Any]) -> dict[str, Any]:
        learning_path = self._safe_dict(params.get("learningPath")) or {}
        diagnosis = self._safe_dict(params.get("masteryDiagnosis")) or {}
        steps = self._learning_path_steps(learning_path)
        target_points = self._diagnosis_points(diagnosis)
        step_points = self._step_target_points(steps)
        if not target_points:
            score = 1.0 if steps else None
            missing_points: list[str] = []
        else:
            covered_points = [point for point in target_points if point in step_points]
            score = len(covered_points) / len(target_points)
            missing_points = [point for point in target_points if point not in step_points]
        status = "GOOD" if score is not None and score >= 0.8 else "LIMITED"
        return {
            "status": status if score is not None else "NOT_APPLICABLE",
            "score": round(score, 2) if score is not None else None,
            "issues": [f"学习路径未覆盖诊断知识点：{', '.join(missing_points[:3])}"] if missing_points else [],
            "evidence": {
                "diagnosisPointCount": len(target_points),
                "stepPointCount": len(step_points),
            },
        }

    def _tool_review_path_order(self, *, params: dict[str, Any]) -> dict[str, Any]:
        learning_path = self._safe_dict(params.get("learningPath")) or {}
        steps = self._learning_path_steps(learning_path)
        if not steps:
            return {
                "status": "NOT_APPLICABLE",
                "score": None,
                "issues": [],
                "evidence": {"stepCount": 0, "orders": []},
            }
        orders = [self._safe_int(step.get("order")) for step in steps]
        numeric_orders = [order for order in orders if order is not None]
        expected_orders = list(range(1, len(steps) + 1))
        has_duplicate = len(set(numeric_orders)) != len(numeric_orders)
        is_ordered = numeric_orders == sorted(numeric_orders) and not has_duplicate
        is_complete = len(numeric_orders) == len(steps) and sorted(numeric_orders) == expected_orders
        if is_complete:
            score = 1.0
        elif is_ordered:
            score = 0.75
        else:
            score = 0.45
        issues: list[str] = []
        if steps and not is_complete:
            issues.append("学习步骤 order 未形成连续顺序。")
        return {
            "status": "GOOD" if score >= 0.8 else "LIMITED",
            "score": score,
            "issues": issues,
            "evidence": {"stepCount": len(steps), "orders": numeric_orders},
        }

    def _tool_review_resource_match(self, *, params: dict[str, Any]) -> dict[str, Any]:
        resource_push_plan = self._safe_dict(params.get("resourcePushPlan")) or {}
        step_resources = resource_push_plan.get("stepResources")
        if not isinstance(step_resources, list):
            step_resources = []
        coverage_gaps = resource_push_plan.get("coverageGaps")
        if not isinstance(coverage_gaps, list):
            coverage_gaps = []
        step_count = len([item for item in step_resources if isinstance(item, dict)])
        if step_count == 0 and not coverage_gaps:
            return {
                "status": "NOT_APPLICABLE",
                "score": None,
                "issues": [],
                "evidence": {
                    "stepCount": 0,
                    "matchedStepCount": 0,
                    "resourceCount": 0,
                    "gapCount": 0,
                },
            }
        matched_step_count = 0
        matched_resource_count = 0
        for item in step_resources:
            if not isinstance(item, dict):
                continue
            resources = item.get("resources")
            if not isinstance(resources, list):
                resources = []
            valid_resources = [resource for resource in resources if isinstance(resource, dict)]
            if valid_resources:
                matched_step_count += 1
                matched_resource_count += len(valid_resources)
        gap_count = len([item for item in coverage_gaps if isinstance(item, dict)])
        if step_count == 0:
            score = 0.0
        else:
            score = matched_step_count / step_count
            if gap_count:
                score = max(0.0, score - min(0.3, gap_count / max(step_count, 1) * 0.2))
        issues = ["资源推送计划存在覆盖缺口。"] if gap_count else []
        return {
            "status": "GOOD" if score >= 0.8 else "LIMITED",
            "score": round(score, 2),
            "issues": issues,
            "evidence": {
                "stepCount": step_count,
                "matchedStepCount": matched_step_count,
                "resourceCount": matched_resource_count,
                "gapCount": gap_count,
            },
        }

    def _merge_structured_scores(
        self,
        *,
        payload: CriticReviewPayload,
        review_signals: dict[str, Any],
    ) -> CriticReviewPayload:
        serialized = payload.model_dump(by_alias=True)
        score_sources = {
            "coverageScore": review_signals.get("learningPathCoverage"),
            "pathOrderScore": review_signals.get("pathOrder"),
            "resourceMatchScore": review_signals.get("resourceMatch"),
        }
        for field, signal in score_sources.items():
            existing_score = self._normalize_score(serialized.get(field))
            signal_score = signal.get("score") if isinstance(signal, dict) else None
            serialized[field] = existing_score if existing_score is not None else self._normalize_score(signal_score)
        return CriticReviewPayload.model_validate(serialized)

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

    def _safe_dict(self, value: Any) -> dict[str, Any] | None:
        return value if isinstance(value, dict) else None

    def _learning_path_steps(self, learning_path: dict[str, Any]) -> list[dict[str, Any]]:
        steps = learning_path.get("steps")
        if not isinstance(steps, list):
            return []
        return [step for step in steps if isinstance(step, dict)]

    def _diagnosis_points(self, diagnosis: dict[str, Any]) -> list[str]:
        points: list[Any] = []
        target_scope = self._safe_dict(diagnosis.get("targetScope")) or {}
        scope_points = target_scope.get("knowledgePoints")
        if isinstance(scope_points, list):
            points.extend(scope_points)
        diagnoses = diagnosis.get("knowledgeDiagnoses")
        if isinstance(diagnoses, list):
            for item in diagnoses:
                if not isinstance(item, dict):
                    continue
                points.append(item.get("knowledgePoint"))
                next_focus = item.get("nextFocus")
                if next_focus:
                    points.append(next_focus)
        return self._unique_texts(points)

    def _step_target_points(self, steps: list[dict[str, Any]]) -> list[str]:
        points: list[Any] = []
        for step in steps:
            target_points = step.get("targetKnowledgePoints")
            if isinstance(target_points, list):
                points.extend(target_points)
            points.extend([step.get("title"), step.get("objective")])
        return self._unique_texts(points)

    def _unique_texts(self, items: list[Any]) -> list[str]:
        seen: set[str] = set()
        texts: list[str] = []
        for item in items:
            text = str(item or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            texts.append(text)
        return texts

    def _safe_int(self, value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _normalize_score(self, value: Any) -> float | None:
        if value is None:
            return None
        try:
            score = float(value)
        except (TypeError, ValueError):
            return None
        if score > 1:
            score = score / 100
        return max(0.0, min(score, 1.0))


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
        return append_user_skill_to_prompt(
            build_safety_system_prompt(snapshot),
            component_name="safety_llm",
            ability_key="ability:path",
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
