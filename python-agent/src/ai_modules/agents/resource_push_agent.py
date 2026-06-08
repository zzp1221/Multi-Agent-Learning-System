"""资源推送 Agent，选择现有资源并返回投递链接。"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from src.ai_modules.agents.base import PlaceholderAgent
from src.ai_modules.config import get_settings
from src.ai_modules.generation import ResourceGenerationService
from src.ai_modules.models import (
    ProgressPayload,
    ProgressSSEEvent,
    ResourceFilePayload,
    ResourceFileSSEEvent,
    ResultChunkPayload,
    ResultChunkSSEEvent,
    SSEEvent,
)
from src.ai_modules.runtime.provenance import build_llm_provenance, validate_llm_provenance

LOGGER = logging.getLogger(__name__)

MIN_TOPIC_RELEVANCE_SCORE = 4
PATH_RECOMMENDATION_TYPES = ("DOCUMENT", "VIDEO", "PRACTICAL_CASE")
MAX_STEP_EXTERNAL_RESOURCE_TYPES = 4
GENERIC_TOPIC_TERMS = {
    "学习",
    "课程",
    "教程",
    "入门",
    "基础",
    "开发",
    "后端",
    "后端开发",
    "项目",
    "项目实现",
    "实战",
    "实现",
    "软件工程",
    "计算机",
    "编程",
    "掌握",
    "理解",
    "应用",
    "backend",
    "course",
    "development",
    "engineering",
    "guide",
    "learning",
    "programming",
    "project",
    "software",
    "tutorial",
}
GENERIC_RESOURCE_TERMS = {
    "资源",
    "讲解文档",
    "代码案例",
    "实操案例",
    "拓展阅读",
    "视频",
    "教程",
    "文章",
    "文档",
    "课程",
    "官方文档",
    "高质量学习资源",
    "EXPLANATION",
    "CODE_CASE",
    "PRACTICAL_CASE",
    "READING",
    "VIDEO",
    "DOCUMENT",
    "QUIZ",
}


@dataclass(slots=True)
class PushResourceCandidate:
    title: str
    resource_type: str
    summary_text: str
    file_name: str
    mime_type: str | None
    score: int
    matched_terms: list[str]
    download_url: str | None = None
    rerank_reason: str = ""
    rerank_score: float | None = None
    thumbnail_url: str | None = None
    duration_seconds: int | None = None
    knowledge_point: str | None = None
    source_name: str | None = None
    source: str = "tavily"


class ResourcePushAgent(PlaceholderAgent):
    """推荐外部资源或生成可下载的推送资源。"""

    def __init__(self) -> None:
        super().__init__("Resource Push Agent", "resource_push")
        self.settings = get_settings()
        self.resource_generation_service = ResourceGenerationService()

    async def run(
        self,
        *,
        task_id: str,
        trace_id: str,
        seq: int,
        service_type: str,
        params: dict,
        snapshot,
        system_prompt: str,
    ) -> asyncio.AsyncIterator[SSEEvent]:
        del service_type, system_prompt

        profile_context = self._extract_profile_context(params, snapshot)
        learning_path = params.get("learningPath")
        if isinstance(learning_path, dict) and isinstance(learning_path.get("steps"), list):
            plan = await self._build_path_external_resource_plan(
                learning_path=learning_path,
                params=params,
                profile_context=profile_context,
            )
            params["resourcePushPlan"] = plan
            params["pushedResources"] = [
                resource
                for step_plan in plan.get("stepResources", [])
                if isinstance(step_plan, dict)
                for resource in step_plan.get("resources", [])
                if isinstance(resource, dict)
            ]
            yield ProgressSSEEvent(
                taskId=task_id,
                traceId=trace_id,
                seq=seq,
                payload=ProgressPayload(
                    stage=self.stage_name,
                    percent=70,
                    message=f"已按学习路径推送 {len(params['pushedResources'])} 个外部学习资源",
                ),
            )
            yield ResultChunkSSEEvent(
                taskId=task_id,
                traceId=trace_id,
                seq=seq + 1,
                payload=ResultChunkPayload(text=self._build_path_external_summary(plan)),
            )
            return

        query = self._build_query(params, profile_context)
        yield ProgressSSEEvent(
            taskId=task_id,
            traceId=trace_id,
            seq=seq,
            payload=ProgressPayload(
                stage=self.stage_name,
                percent=25,
                message=f"正在根据当前画像筛选 {query}",
            ),
        )

        preferred_type = self._normalize_text(params.get("resourceType")).upper()
        if preferred_type in {"PPT", "SLIDES"}:
            yield ProgressSSEEvent(
                taskId=task_id,
                traceId=trace_id,
                seq=seq + 1,
                payload=ProgressPayload(
                    stage=self.stage_name,
                    percent=40,
                    message="正在生成可下载 PPT 课件",
                ),
            )
            asset = await asyncio.to_thread(
                self._build_ppt_asset,
                params=params,
                profile_context=profile_context,
                snapshot=snapshot,
            )
            provenance = self._build_generated_asset_provenance(params=params)
            params["pushedResources"] = [
                {
                    "title": asset.title,
                    "resourceType": asset.asset_type,
                    "fileName": asset.file_name,
                    "downloadUrl": None,
                    "summaryText": asset.summary,
                    "matchedTerms": self._build_terms("SLIDES", profile_context)[:4],
                    "rerankReason": "基于当前学习画像生成 PPT 课件",
                    "rerankScore": 1.0,
                    "sourceName": "generated",
                    "thumbnailUrl": None,
                    **provenance,
                }
            ]
            yield ProgressSSEEvent(
                taskId=task_id,
                traceId=trace_id,
                seq=seq + 2,
                payload=ProgressPayload(
                    stage=self.stage_name,
                    percent=70,
                    message=f"已生成 {self._resource_type_label(asset.asset_type)}，正在准备下载链接",
                ),
            )
            yield ResultChunkSSEEvent(
                taskId=task_id,
                traceId=trace_id,
                seq=seq + 3,
                payload=ResultChunkPayload(
                    text=(
                        f"已基于当前学习画像生成 {self._resource_type_label(asset.asset_type)}：{asset.title}。"
                        "任务完成后可直接下载课件文件。"
                    ),
                ),
            )
            resource_payload = ResourceFilePayload(
                assetType=asset.asset_type,
                title=asset.title,
                summary=asset.summary,
                displayMode=asset.display_mode,
                fileName=asset.file_name,
                localPath=asset.local_path,
                mimeType=asset.mime_type,
                inlineContent=asset.inline_content,
                language=asset.language,
                explanation=asset.explanation,
                thumbnailPath=asset.thumbnail_path,
                thumbnailFileName=asset.thumbnail_file_name,
                thumbnailMimeType=asset.thumbnail_mime_type,
                durationSeconds=asset.duration_seconds,
                knowledgePoint=asset.knowledge_point,
                generatedBy=provenance["generatedBy"],
                contentOrigin=provenance["contentOrigin"],
                provider=provenance["provider"],
                model=provenance["model"],
                agentName=provenance["agentName"],
                evidenceIds=provenance["evidenceIds"],
                fallback=provenance["fallback"],
                fromCache=provenance["fromCache"],
            )
            validate_llm_provenance(resource_payload, artifact_label=f"{self.stage_name}:{asset.asset_type}")
            yield ResourceFileSSEEvent(
                taskId=task_id,
                traceId=trace_id,
                seq=seq + 4,
                payload=resource_payload,
            )
            return

        yield ProgressSSEEvent(
            taskId=task_id,
            traceId=trace_id,
            seq=seq + 1,
            payload=ProgressPayload(
                stage=self.stage_name,
                percent=40,
                message=f"正在通过 Tavily 搜索匹配的{self._resource_type_label(preferred_type)}",
            ),
        )
        candidates = await self._search_external_candidates(
            preferred_type=preferred_type,
            query=query,
            profile_context=profile_context,
        )
        previous_urls, previous_titles = self._extract_previous_resource_exclusions(params)
        if previous_urls or previous_titles:
            candidates = [
                candidate
                for candidate in candidates
                if self._first_unused_candidate([candidate], previous_urls, previous_titles) is not None
            ]

        if not candidates:
            params["pushedResources"] = []
            yield ResultChunkSSEEvent(
                taskId=task_id,
                traceId=trace_id,
                seq=seq + 1,
                payload=ResultChunkPayload(
                    text=(
                        f"未找到与当前画像匹配的{self._resource_type_label(preferred_type)}外部资源。"
                        "请调整画像或稍后重试。"
                    )
                ),
            )
            return

        params["pushedResources"] = [
            {
                "title": item.title,
                "resourceType": item.resource_type,
                "fileName": item.file_name,
                "downloadUrl": item.download_url,
                "summaryText": item.summary_text,
                "matchedTerms": item.matched_terms,
                "rerankReason": item.rerank_reason,
                "rerankScore": item.rerank_score,
                "sourceName": item.source_name,
                "thumbnailUrl": item.thumbnail_url,
            }
            for item in candidates
        ]

        yield ProgressSSEEvent(
            taskId=task_id,
            traceId=trace_id,
            seq=seq + 1,
            payload=ProgressPayload(
                stage=self.stage_name,
                percent=65,
                message=f"已筛选出 {len(candidates)} 个推荐资源",
            ),
        )
        yield ResultChunkSSEEvent(
            taskId=task_id,
            traceId=trace_id,
            seq=seq + 2,
            payload=ResultChunkPayload(
                text=self._build_summary_text(query, candidates),
            ),
        )

        next_seq = seq + 3
        for item in candidates:
            yield ResourceFileSSEEvent(
                taskId=task_id,
                traceId=trace_id,
                seq=next_seq,
                payload=ResourceFilePayload(
                    assetType=item.resource_type,
                    title=item.title,
                    summary=item.summary_text,
                    displayMode="external_link",
                    fileName=item.file_name,
                    localPath=None,
                    mimeType=item.mime_type,
                    downloadUrl=item.download_url,
                    thumbnailUrl=item.thumbnail_url,
                    durationSeconds=item.duration_seconds,
                    knowledgePoint=item.knowledge_point,
                    sourceName=item.source_name,
                ),
            )
            next_seq += 1

    def _build_ppt_asset(
        self,
        *,
        params: dict[str, Any],
        profile_context: dict[str, Any],
        snapshot: Any,
    ):
        generated_params = dict(params)
        current_course = self._normalize_text(profile_context.get("currentCourse"))
        current_chapter = self._normalize_text(profile_context.get("currentChapter"))
        primary_weak_point = self._normalize_text(profile_context.get("primaryWeakPoint"))
        topic = current_chapter or primary_weak_point or current_course or "学习主题"
        generated_params["resourceType"] = "SLIDES"
        generated_params["course"] = generated_params.get("course") or current_course or getattr(snapshot, "current_course", "")
        generated_params["topic"] = generated_params.get("topic") or topic
        generated_params["query"] = generated_params.get("query") or f"{generated_params['course']} {topic} PPT课件".strip()
        learning_context = generated_params.get("learningContext")
        if not isinstance(learning_context, dict):
            learning_context = {}
        generated_params["learningContext"] = {
            **learning_context,
            "course": learning_context.get("course") or generated_params["course"],
            "chapter": learning_context.get("chapter") or topic,
        }
        return self.resource_generation_service.build_asset(
            asset_type="SLIDES",
            params=generated_params,
            snapshot=snapshot,
        )

    async def _build_path_external_resource_plan(
        self,
        *,
        learning_path: dict[str, Any],
        params: dict[str, Any],
        profile_context: dict[str, Any],
    ) -> dict[str, Any]:
        step_resources: list[dict[str, Any]] = []
        coverage_gaps: list[dict[str, Any]] = []
        previous_urls, previous_titles = self._extract_previous_resource_exclusions(params)
        seen_urls: set[str] = set(previous_urls)
        seen_titles: set[str] = set(previous_titles)

        for index, step in enumerate(learning_path.get("steps", []), start=1):
            if not isinstance(step, dict):
                continue
            step_id = str(step.get("stepId") or f"step-{index}")
            step_title = self._normalize_text(step.get("title")) or f"步骤 {index}"
            step_context = self._build_step_profile_context(step=step, base_context=profile_context)
            preferred_types = self._path_recommendation_types(step.get("preferredResourceTypes"))
            query = self._build_step_external_query(step=step, params=params, profile_context=step_context)

            resources: list[dict[str, Any]] = []
            for resource_type in preferred_types:
                candidates = await self._search_external_candidates(
                    preferred_type=resource_type,
                    query=query,
                    profile_context=step_context,
                )
                candidate = self._first_unused_candidate(candidates, seen_urls, seen_titles)
                if candidate is None:
                    continue
                if candidate.download_url:
                    seen_urls.add(self._normalize_resource_url(candidate.download_url))
                seen_titles.add(self._normalize_resource_title(candidate.title))
                resources.append(self._candidate_to_path_resource(candidate))
                if len(resources) >= MAX_STEP_EXTERNAL_RESOURCE_TYPES:
                    break

            present_types = {
                self._normalize_resource_type(item.get("resourceType"))
                for item in resources
                if isinstance(item, dict)
            }
            missing_types = [item for item in preferred_types if item not in present_types]
            if missing_types:
                coverage_gaps.append(
                    {
                        "stepId": step_id,
                        "missingResourceTypes": missing_types,
                        "reason": "Tavily 暂未检索到足够匹配当前学习步骤的外部资源。",
                    }
                )
            step_resources.append(
                {
                    "stepId": step_id,
                    "stepTitle": step_title,
                    "targetKnowledgePoints": list(step_context.get("weakPoints") or []),
                    "resources": resources,
                }
            )

        return {
            "stepResources": step_resources,
            "coverageGaps": coverage_gaps,
            "profileSignals": {
                "primaryWeakPoint": profile_context.get("primaryWeakPoint"),
                "preferredResourceTypes": list(PATH_RECOMMENDATION_TYPES),
                "source": "tavily",
            },
        }

    def _build_step_profile_context(
        self,
        *,
        step: dict[str, Any],
        base_context: dict[str, Any],
    ) -> dict[str, Any]:
        target_points = [
            self._normalize_text(item)
            for item in step.get("targetKnowledgePoints", [])
            if self._normalize_text(item)
        ]
        step_title = self._normalize_text(step.get("title"))
        objective = self._normalize_text(step.get("objective"))
        checkpoint = self._normalize_text(step.get("checkpoint") or step.get("successCriteria"))
        primary_weak_point = target_points[0] if target_points else step_title
        return {
            **base_context,
            "primaryWeakPoint": primary_weak_point or base_context.get("primaryWeakPoint", ""),
            "weakPoints": target_points or [value for value in (step_title, objective) if value],
            "learningGoal": objective or checkpoint or base_context.get("learningGoal", ""),
            "currentChapter": step_title or base_context.get("currentChapter", ""),
            "preferredResourceTypes": list(PATH_RECOMMENDATION_TYPES),
        }

    def _path_recommendation_types(self, raw_value: Any) -> list[str]:
        normalized = self._normalize_resource_types(raw_value)
        selected = [
            item for item in normalized
            if item in {"DOCUMENT", "VIDEO", "PRACTICAL_CASE", "CODE", "READING"}
        ]
        if "CODE" in selected and "PRACTICAL_CASE" not in selected:
            selected.append("PRACTICAL_CASE")
        if "READING" in selected and "DOCUMENT" not in selected:
            selected.append("DOCUMENT")
        selected = [item for item in selected if item not in {"CODE", "READING"}]
        for required_type in PATH_RECOMMENDATION_TYPES:
            if required_type not in selected:
                selected.append(required_type)
        return selected[:4]

    def _build_step_external_query(
        self,
        *,
        step: dict[str, Any],
        params: dict[str, Any],
        profile_context: dict[str, Any],
    ) -> str:
        parts = [
            self._normalize_text(step.get("title")),
            self._normalize_text(step.get("objective")),
            self._normalize_text(step.get("checkpoint")),
            self._normalize_text(step.get("successCriteria")),
            " ".join(
                self._normalize_text(item)
                for item in step.get("targetKnowledgePoints", [])
                if self._normalize_text(item)
            ),
            self._normalize_text(profile_context.get("currentCourse")),
        ]
        return " ".join(part for part in parts if part).strip()

    def _first_unused_candidate(
        self,
        candidates: list[PushResourceCandidate],
        seen_urls: set[str],
        seen_titles: set[str] | None = None,
    ) -> PushResourceCandidate | None:
        seen_titles = seen_titles or set()
        for candidate in candidates:
            if candidate.download_url and self._normalize_resource_url(candidate.download_url) in seen_urls:
                continue
            if self._normalize_resource_title(candidate.title) in seen_titles:
                continue
            return candidate
        return None

    def _extract_previous_resource_exclusions(self, params: dict[str, Any]) -> tuple[set[str], set[str]]:
        urls: set[str] = set()
        titles: set[str] = set()

        previous_resource_urls = params.get("previousResourceUrls")
        if not isinstance(previous_resource_urls, list):
            previous_resource_urls = []
        for value in previous_resource_urls:
            url = self._normalize_resource_url(value)
            if url:
                urls.add(url)
        previous_resource_titles = params.get("previousResourceTitles")
        if not isinstance(previous_resource_titles, list):
            previous_resource_titles = []
        for value in previous_resource_titles:
            title = self._normalize_resource_title(value)
            if title:
                titles.add(title)

        existing_resources = params.get("existingResources")
        if not isinstance(existing_resources, list):
            existing_resources = []
        for resource in existing_resources:
            if not isinstance(resource, dict):
                continue
            url = self._normalize_resource_url(resource.get("downloadUrl"))
            title = self._normalize_resource_title(resource.get("title"))
            if url:
                urls.add(url)
            if title:
                titles.add(title)

        return urls, titles

    def _normalize_resource_url(self, value: Any) -> str:
        url = self._normalize_text(value).lower()
        return url.rstrip("/")

    def _normalize_resource_title(self, value: Any) -> str:
        return self._clean_display_text(self._normalize_text(value)).lower()

    def _candidate_to_path_resource(self, candidate: PushResourceCandidate) -> dict[str, Any]:
        return {
            "title": candidate.title,
            "resourceType": candidate.resource_type,
            "source": candidate.source,
            "sourceName": candidate.source_name,
            "downloadUrl": candidate.download_url,
            "summaryText": candidate.summary_text,
            "matchReason": candidate.rerank_reason,
            "rerankScore": candidate.rerank_score,
            "thumbnailUrl": candidate.thumbnail_url,
            "knowledgePoint": candidate.knowledge_point,
        }

    def _build_path_external_summary(self, plan: dict[str, Any]) -> str:
        step_count = len(plan.get("stepResources", []))
        resource_count = sum(
            len(step.get("resources", []))
            for step in plan.get("stepResources", [])
            if isinstance(step, dict)
        )
        gap_count = len(plan.get("coverageGaps", []))
        if gap_count:
            return f"已为 {step_count} 个学习步骤推送 {resource_count} 个外部学习资源，仍有 {gap_count} 个步骤需要继续检索补充。"
        return f"已为 {step_count} 个学习步骤推送 {resource_count} 个外部学习资源。"

    def _build_path_bound_resource_plan(
        self,
        *,
        learning_path: dict[str, Any],
        params: dict[str, Any],
        profile_context: dict[str, Any],
    ) -> dict[str, Any]:
        generated_assets = [
            item for item in params.get("generatedAssets", [])
            if isinstance(item, dict)
        ]
        retrieval_evidence = [
            item for item in params.get("retrievalEvidence", [])
            if isinstance(item, dict)
        ]
        step_resources: list[dict[str, Any]] = []
        coverage_gaps: list[dict[str, Any]] = []
        for index, step in enumerate(learning_path.get("steps", []), start=1):
            if not isinstance(step, dict):
                continue
            step_id = str(step.get("stepId") or f"step-{index}")
            preferred_types = self._normalize_resource_types(step.get("preferredResourceTypes"))
            resources = self._match_generated_assets_for_step(
                step=step,
                generated_assets=generated_assets,
                preferred_types=preferred_types,
            )
            resources.extend(
                self._match_retrieval_evidence_for_step(
                    step=step,
                    retrieval_evidence=retrieval_evidence,
                    existing_count=len(resources),
                )
            )
            present_types = {
                normalized_type
                for item in resources
                for normalized_type in [self._normalize_resource_type(item.get("resourceType"))]
                if normalized_type
            }
            missing_types = [resource_type for resource_type in preferred_types if resource_type not in present_types]
            if missing_types:
                coverage_gaps.append(
                    {
                        "stepId": step_id,
                        "missingResourceTypes": missing_types,
                        "reason": "当前上下文没有可验证的系统生成资源或检索证据，未伪造资源卡片。",
                    }
                )
            step_resources.append(
                {
                    "stepId": step_id,
                    "stepTitle": step.get("title") or f"学习步骤 {index}",
                    "targetKnowledgePoints": list(step.get("targetKnowledgePoints") or []),
                    "resources": resources,
                }
            )
        return {
            "stepResources": step_resources,
            "coverageGaps": coverage_gaps,
            "profileSignals": {
                "preferredResourceTypes": profile_context.get("preferredResourceTypes", []),
                "primaryWeakPoint": profile_context.get("primaryWeakPoint"),
            },
        }

    def _match_generated_assets_for_step(
        self,
        *,
        step: dict[str, Any],
        generated_assets: list[dict[str, Any]],
        preferred_types: list[str],
    ) -> list[dict[str, Any]]:
        resources: list[dict[str, Any]] = []
        step_terms = self._step_match_terms(step)
        for asset in generated_assets:
            resource_type = self._normalize_resource_type(asset.get("assetType") or asset.get("resourceType"))
            if preferred_types and resource_type not in preferred_types:
                continue
            haystack = " ".join(
                str(asset.get(key) or "")
                for key in ("title", "summary", "knowledgePoint", "assetType")
            ).lower()
            matched_by_term = any(term.lower() in haystack for term in step_terms) if step_terms else False
            resources.append(
                {
                    "title": asset.get("title") or resource_type or "系统生成资源",
                    "resourceType": resource_type or "DOCUMENT",
                    "source": "generated",
                    "matchReason": (
                        "匹配学习步骤的知识点和推荐资源类型"
                        if matched_by_term
                        else "匹配学习步骤要求的资源类型"
                    ),
                    "downloadUrl": asset.get("downloadUrl"),
                    "summaryText": asset.get("summary") or asset.get("summaryText") or "",
                    "generatedBy": asset.get("generatedBy"),
                    "contentOrigin": asset.get("contentOrigin"),
                    "provider": asset.get("provider"),
                    "model": asset.get("model"),
                    "agentName": asset.get("agentName"),
                    "evidenceIds": list(asset.get("evidenceIds") or []),
                    "fallback": asset.get("fallback"),
                    "fromCache": bool(asset.get("fromCache", False)),
                }
            )
        return resources[:3]

    def _match_retrieval_evidence_for_step(
        self,
        *,
        step: dict[str, Any],
        retrieval_evidence: list[dict[str, Any]],
        existing_count: int,
    ) -> list[dict[str, Any]]:
        if existing_count >= 3:
            return []
        resources: list[dict[str, Any]] = []
        step_terms = self._step_match_terms(step)
        for evidence in retrieval_evidence:
            title = str(evidence.get("title") or "检索证据").strip()
            evidence_text = str(evidence.get("evidence") or evidence.get("snippet") or "").strip()
            haystack = f"{title} {evidence_text}".lower()
            if step_terms and not any(term.lower() in haystack for term in step_terms):
                continue
            resources.append(
                {
                    "title": title,
                    "resourceType": "READING",
                    "source": "retrieval_evidence",
                    "matchReason": "来自知识检索智能体的可追溯证据",
                    "downloadUrl": evidence.get("url"),
                    "summaryText": evidence_text,
                    "evidenceSlug": evidence.get("slug"),
                    "sourceName": evidence.get("sourceTitle") or evidence.get("channel"),
                }
            )
            if len(resources) + existing_count >= 3:
                break
        return resources

    def _build_path_bound_summary(self, plan: dict[str, Any]) -> str:
        step_count = len(plan.get("stepResources", []))
        resource_count = sum(
            len(step.get("resources", []))
            for step in plan.get("stepResources", [])
            if isinstance(step, dict)
        )
        gap_count = len(plan.get("coverageGaps", []))
        if gap_count:
            return f"已为 {step_count} 个学习步骤绑定 {resource_count} 个真实资源或证据，仍有 {gap_count} 个资源类型缺口待生成。"
        return f"已为 {step_count} 个学习步骤绑定 {resource_count} 个真实资源或证据。"

    def _step_match_terms(self, step: dict[str, Any]) -> list[str]:
        terms = [
            str(step.get("title") or "").strip(),
            str(step.get("objective") or "").strip(),
        ]
        terms.extend(str(item).strip() for item in step.get("targetKnowledgePoints", []) if str(item).strip())
        return [term for term in terms if term]

    def _normalize_resource_types(self, raw_value: Any) -> list[str]:
        if isinstance(raw_value, list):
            normalized = [self._normalize_resource_type(item) for item in raw_value if str(item).strip()]
        elif isinstance(raw_value, str) and raw_value.strip():
            normalized = [self._normalize_resource_type(item) for item in raw_value.replace("，", ",").split(",") if item.strip()]
        else:
            normalized = []
        return [item for item in dict.fromkeys(normalized) if item]

    def _normalize_resource_type(self, raw_value: Any) -> str:
        resource_type = str(raw_value or "").strip().upper()
        aliases = {
            "EXPLANATION": "DOCUMENT",
            "CODE_CASE": "CODE",
            "PRACTICAL_CASE": "PRACTICAL_CASE",
            "PPT": "SLIDES",
            "QUESTION_BANK": "QUIZ",
            "QUESTION": "QUIZ",
            "PRACTICE": "QUIZ",
        }
        return aliases.get(resource_type, resource_type)

    def _build_generated_asset_provenance(self, *, params: dict[str, Any]) -> dict[str, Any]:
        generator = getattr(self.resource_generation_service.content_chain, "primary_generator", None)
        return build_llm_provenance(
            agent_name=self.stage_name,
            generator=generator,
            params=params,
        )

    def _build_query(self, params: dict[str, Any], profile_context: dict[str, Any]) -> str:
        parts = [
            self._normalize_text(profile_context.get("primaryWeakPoint")),
            self._normalize_text(profile_context.get("currentCourse")),
            self._normalize_text(profile_context.get("currentChapter")),
            self._normalize_text(profile_context.get("studentLevel")),
            self._resource_type_label(self._normalize_text(params.get("resourceType")).upper()),
        ]
        rendered = " / ".join(part for part in parts if part)
        return rendered or f"{self._normalize_text(params.get('resourceType')) or '资源'}"

    @staticmethod
    def _is_http_url(url: str | None) -> bool:
        if not url:
            return False
        normalized = url.strip().lower()
        return normalized.startswith("http://") or normalized.startswith("https://")

    def _build_terms(
        self,
        preferred_type: str,
        profile_context: dict[str, Any],
    ) -> list[str]:
        terms: list[str] = []
        for raw in (
            self._normalize_text(profile_context.get("primaryWeakPoint")),
            self._normalize_text(profile_context.get("currentCourse")),
            self._normalize_text(profile_context.get("currentChapter")),
            self._normalize_text(profile_context.get("learningGoal")),
            " ".join(profile_context.get("weakPoints", [])),
        ):
            if not raw:
                continue
            for token in raw.replace("/", " ").replace(">", " ").split():
                cleaned = token.strip()
                if cleaned and cleaned not in terms:
                    terms.append(cleaned)
        if preferred_type:
            terms.append(preferred_type)
        for resource_type in profile_context.get("preferredResourceTypes", []):
            normalized = self._normalize_text(resource_type)
            if normalized and normalized not in terms:
                terms.append(normalized)
        return terms

    def _extract_profile_context(self, params: dict[str, Any], snapshot: Any) -> dict[str, Any]:
        profile = params.get("profile") if isinstance(params.get("profile"), dict) else {}
        profile_analysis = params.get("profileAnalysis") if isinstance(params.get("profileAnalysis"), dict) else {}
        if profile_analysis:
            profile = self._merge_non_empty(profile, profile_analysis)
        learning_context = params.get("learningContext", {}) if isinstance(params.get("learningContext", {}), dict) else {}
        weak_points = [
            item for item in profile.get("weakPoints", [])
            if isinstance(item, str) and item.strip()
        ]
        if not weak_points:
            weak_points = [
                str(item.get("topic", "")).strip()
                for item in profile.get("weakPointDetails", [])
                if isinstance(item, dict)
            ]
        preferred_resource_types = [
            self._normalize_resource_type(item)
            for item in profile.get("preferredResourceTypes", [])
            if self._normalize_text(item)
        ]
        if not preferred_resource_types and getattr(snapshot, "preferred_style", ""):
            preferred_style = str(snapshot.preferred_style)
            if "visual" in preferred_style or "图" in preferred_style:
                preferred_resource_types = ["MINDMAP", "VIDEO"]
            elif "example" in preferred_style or "code" in preferred_style:
                preferred_resource_types = ["CODE", "DOCUMENT"]
            else:
                preferred_resource_types = ["DOCUMENT", "READING"]
        return {
            "studentLevel": self._normalize_text(profile.get("studentLevel") or profile.get("knowledgeFoundation") or getattr(snapshot, "student_level", "")),
            "learningGoal": self._normalize_text(profile.get("learningGoal") or (profile.get("currentGoal", {}) or {}).get("shortTerm")),
            "primaryWeakPoint": weak_points[0] if weak_points else (getattr(snapshot, "knowledge_gaps", []) or [""])[0],
            "weakPoints": weak_points or list(getattr(snapshot, "knowledge_gaps", [])),
            "preferredResourceTypes": preferred_resource_types,
            "currentCourse": self._normalize_text(
                getattr(snapshot, "current_course", "")
                or learning_context.get("course")
                or params.get("course")
            ),
            "currentChapter": self._normalize_text(
                getattr(snapshot, "current_chapter", "")
                or learning_context.get("chapter")
                or params.get("topic")
                or params.get("keyPoints")
            ),
        }

    def _build_summary_text(self, query: str, candidates: list[PushResourceCandidate]) -> str:
        titles = "，".join(item.title for item in candidates[:3])
        lead_reason = candidates[0].rerank_reason or "与当前学习画像和查询最匹配"
        resource_label = self._resource_type_label(candidates[0].resource_type)
        return (
            f"已基于当前画像完成“{query}”的 Tavily 外部推荐，并返回 {len(candidates)} 个{resource_label}。"
            f"优先资源：{titles}。首位推荐原因：{lead_reason}。"
        )

    async def _search_external_candidates(
        self,
        *,
        preferred_type: str,
        query: str,
        profile_context: dict[str, Any],
    ) -> list[PushResourceCandidate]:
        if not self.settings.tavily_api_key.strip():
            LOGGER.info("Skip Tavily search because TAVILY_API_KEY is not configured")
            return []

        search_query = self._build_tavily_query(preferred_type, query, profile_context)
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(
                    self.settings.tavily_base_url,
                    json={
                        "api_key": self.settings.tavily_api_key,
                        "query": search_query,
                        "topic": "general",
                        "search_depth": "advanced",
                        "max_results": 15,
                        "include_images": True,
                        "include_answer": False,
                        "include_raw_content": False,
                    },
                )
                response.raise_for_status()
        except Exception:
            LOGGER.warning("Tavily resource search failed for query=%s", search_query, exc_info=True)
            return []

        payload = response.json()
        results = payload.get("results")
        if not isinstance(results, list):
            return []

        matched_terms = self._build_terms(preferred_type, profile_context)[:4]
        knowledge_point = self._normalize_text(profile_context.get("primaryWeakPoint") or profile_context.get("currentChapter"))
        candidates: list[PushResourceCandidate] = []
        for index, item in enumerate(results, start=1):
            if not isinstance(item, dict):
                continue
            url = self._normalize_text(item.get("url"))
            raw_title = self._normalize_text(item.get("title"))
            raw_summary = self._normalize_text(item.get("content"))
            if not raw_title or not self._is_http_url(url):
                continue
            if not self._passes_content_safety(raw_title, raw_summary, url):
                continue
            if not self._is_valid_external_result(preferred_type, item, url, raw_title):
                continue
            relevance = self._score_topic_relevance(
                title=raw_title,
                summary=raw_summary,
                url=url,
                profile_context=profile_context,
            )
            if relevance < MIN_TOPIC_RELEVANCE_SCORE:
                LOGGER.info(
                    "Skip weakly related pushed resource title=%s url=%s relevance=%s",
                    raw_title,
                    url,
                    relevance,
                )
                continue
            title = self._truncate_display_text(self._clean_display_text(raw_title), 20)
            summary = self._truncate_display_text(
                self._clean_display_text(raw_summary) or f"已通过 Tavily 检索到与当前画像匹配的{self._resource_type_label(preferred_type)}。",
                20,
            )
            candidates.append(
                PushResourceCandidate(
                    title=title,
                    resource_type=preferred_type,
                    summary_text=summary,
                    file_name="",
                    mime_type="text/html",
                    score=max(1, 100 - index + self._source_preference_bonus(preferred_type, url) + relevance),
                    matched_terms=matched_terms,
                    download_url=url,
                    rerank_reason=f"主题相关性命中 {relevance} 分，匹配当前课程/薄弱点",
                    rerank_score=round(min(1.0, max(0.1, relevance / 12)), 4),
                    thumbnail_url=self._extract_tavily_thumbnail(item, payload),
                    knowledge_point=knowledge_point or None,
                    source_name=self._extract_source_name(url),
                )
            )
        candidates.sort(key=lambda candidate: (-candidate.score, candidate.title))
        return candidates[:6]

    def _build_tavily_query(self, preferred_type: str, query: str, profile_context: dict[str, Any]) -> str:
        type_hint_map = {
            "EXPLANATION": "概念讲解 教程 官方文档 文章",
            "DOCUMENT": "概念讲解 教程 官方文档 文档 guide",
            "CODE_CASE": "源码 示例项目 code example github tutorial",
            "CODE": "源码 示例项目 code example github tutorial",
            "PRACTICAL_CASE": "从零搭建 实战 项目 教程 源码 github hands-on build",
            "QUIZ": "练习题 题库 quiz exercises practice questions",
            "READING": "进阶阅读 深入解析 文章 文档",
            "VIDEO": "教学视频 讲解 course tutorial",
        }
        site_hint_map = {
            "CODE_CASE": "site:github.com OR site:gitee.com OR site:gitlab.com",
            "CODE": "site:github.com OR site:gitee.com OR site:gitlab.com",
            "PRACTICAL_CASE": "site:github.com OR site:gitee.com OR site:medium.com OR site:dev.to",
            "QUIZ": "练习题 OR quiz OR exercises",
            "VIDEO": "site:bilibili.com OR site:youtube.com",
        }
        topic_parts = [
            self._normalize_text(profile_context.get("currentCourse")),
            self._normalize_text(profile_context.get("currentChapter")),
            self._normalize_text(profile_context.get("primaryWeakPoint")),
            self._normalize_text(profile_context.get("learningGoal")),
            self._topic_query_text(query),
        ]
        parts = [
            self._dedupe_query_terms(topic_parts),
            type_hint_map.get(preferred_type, "高质量学习资源"),
            site_hint_map.get(preferred_type, ""),
        ]
        return " ".join(part for part in parts if part).strip()

    def _topic_query_text(self, query: str) -> str:
        normalized = query.replace("VIDEO", "视频").replace("数字人视频", "视频")
        generic_phrases = (
            "个性化学习路径规划和资源推送",
            "个性化学习路径资源推荐",
            "只刷新当前学习路径各阶段的推荐资源",
            "学习路径资源推荐",
            "资源推送",
        )
        for phrase in generic_phrases:
            normalized = normalized.replace(phrase, " ")
        return self._clean_display_text(normalized)

    def _dedupe_query_terms(self, parts: list[str]) -> str:
        terms: list[str] = []
        for part in parts:
            for token in re.split(r"[\s/、,，;；:：>《》()（）【】\[\]\"'“”]+", part):
                cleaned = token.strip()
                if cleaned and cleaned not in GENERIC_RESOURCE_TERMS and cleaned not in terms:
                    terms.append(cleaned)
        return " ".join(terms)

    def _merge_non_empty(self, base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
        merged = dict(base)
        for key, value in incoming.items():
            if value is None or value == "" or value == [] or value == {}:
                continue
            merged[key] = value
        return merged

    def _extract_tavily_thumbnail(self, item: dict[str, Any], payload: dict[str, Any]) -> str | None:
        for key in ("thumbnailUrl", "thumbnail_url", "image", "imageUrl"):
            value = item.get(key)
            if isinstance(value, str) and self._is_http_url(value):
                return value
        item_images = item.get("images")
        if isinstance(item_images, list):
            for value in item_images:
                if isinstance(value, str) and self._is_http_url(value):
                    return value
        payload_images = payload.get("images")
        if isinstance(payload_images, list):
            for value in payload_images:
                if isinstance(value, str) and self._is_http_url(value):
                    return value
        return None

    def _extract_source_name(self, url: str) -> str | None:
        if not self._is_http_url(url):
            return None
        host = urlparse(url).netloc.lower()
        if not host:
            return None
        if host.startswith("www."):
            host = host[4:]
        return host

    def _is_valid_external_result(self, preferred_type: str, item: dict[str, Any], url: str, title: str) -> bool:
        if preferred_type == "VIDEO":
            return self._is_valid_video_result(item, url, title)
        if preferred_type in {"CODE", "CODE_CASE"}:
            return self._is_valid_code_case_result(item, url, title)
        if preferred_type == "PRACTICAL_CASE":
            return self._is_valid_practical_case_result(item, url, title)
        if preferred_type == "QUIZ":
            return self._is_valid_quiz_result(item, url, title)
        if preferred_type == "READING":
            return self._is_valid_reading_result(url)
        return self._is_valid_explanation_result(url)

    def _is_valid_video_result(self, item: dict[str, Any], url: str, title: str) -> bool:
        lowered_url = url.lower()
        lowered_title = title.lower()
        if any(token in lowered_url for token in (".pdf", "/pdf", "arxiv.org", "doi.org")):
            return False
        if any(token in lowered_url for token in ("github.com", "gitlab.com", "docs.", "readthedocs")):
            return False
        source = self._extract_source_name(url) or ""
        if source in {"bilibili.com", "youtube.com", "youtu.be"}:
            return True
        if any(token in lowered_title for token in ("视频", "讲解", "课程", "lesson", "tutorial", "lecture")):
            return True
        content = self._normalize_text(item.get("content")).lower()
        return any(token in content for token in ("视频", "讲解", "教程", "lecture", "tutorial"))

    def _is_valid_code_case_result(self, item: dict[str, Any], url: str, title: str) -> bool:
        lowered_url = url.lower()
        lowered_title = title.lower()
        source = self._extract_source_name(url) or ""
        if any(token in lowered_url for token in (".pdf", "youtube.com", "bilibili.com")):
            return False
        if source in {"github.com", "gitee.com", "gitlab.com", "gist.github.com"}:
            return True
        content = self._normalize_text(item.get("content")).lower()
        return any(token in f"{lowered_title} {content}" for token in ("代码", "源码", "示例", "example", "sample", "github"))

    def _is_valid_practical_case_result(self, item: dict[str, Any], url: str, title: str) -> bool:
        lowered_url = url.lower()
        lowered_title = title.lower()
        if any(token in lowered_url for token in (".pdf", "youtube.com", "bilibili.com")):
            return False
        source = self._extract_source_name(url) or ""
        content = self._normalize_text(item.get("content")).lower()
        has_source_code_signal = source in {"github.com", "gitee.com", "gitlab.com"} or any(
            token in f"{lowered_title} {content}" for token in ("源码", "source code", "repo", "repository", "github")
        )
        has_hands_on_signal = any(
            token in f"{lowered_title} {content}"
            for token in ("从零", "实战", "搭建", "项目", "hands-on", "build", "tutorial", "step-by-step")
        )
        return has_source_code_signal and has_hands_on_signal

    def _is_valid_quiz_result(self, item: dict[str, Any], url: str, title: str) -> bool:
        lowered_url = url.lower()
        lowered_title = title.lower()
        if any(token in lowered_url for token in (".pdf", "youtube.com", "bilibili.com", "github.com")):
            return False
        content = self._normalize_text(item.get("content")).lower()
        return any(
            token in f"{lowered_title} {content}"
            for token in ("练习", "题库", "试题", "测验", "quiz", "exercise", "practice question")
        )

    def _is_valid_reading_result(self, url: str) -> bool:
        lowered_url = url.lower()
        return not any(token in lowered_url for token in ("youtube.com", "bilibili.com", "github.com", "gist.github.com"))

    def _is_valid_explanation_result(self, url: str) -> bool:
        lowered_url = url.lower()
        return not any(token in lowered_url for token in ("youtube.com", "bilibili.com", "github.com", "gist.github.com"))

    def _source_preference_bonus(self, preferred_type: str, url: str) -> int:
        source = self._extract_source_name(url) or ""
        if preferred_type == "VIDEO" and source in {"bilibili.com", "youtube.com", "youtu.be"}:
            return 20
        if preferred_type in {"CODE", "CODE_CASE"} and source in {"github.com", "gitee.com", "gitlab.com", "gist.github.com"}:
            return 25
        if preferred_type == "PRACTICAL_CASE" and source in {"github.com", "gitee.com", "gitlab.com"}:
            return 30
        return 0

    def _score_topic_relevance(
        self,
        *,
        title: str,
        summary: str,
        url: str,
        profile_context: dict[str, Any],
    ) -> int:
        weighted_text = f"{title} {summary} {self._extract_source_name(url) or ''}".lower()
        score = 0
        for term, weight in self._topic_relevance_terms(profile_context):
            if self._contains_topic_term(weighted_text, term):
                score += weight
        matched_specific_topic = self._matches_specific_topic(weighted_text, profile_context)
        if score > 0 and not matched_specific_topic:
            return min(score, MIN_TOPIC_RELEVANCE_SCORE - 1)
        return score

    def _topic_relevance_terms(self, profile_context: dict[str, Any]) -> list[tuple[str, int]]:
        terms: list[tuple[str, int]] = []
        for raw, weight in (
            (profile_context.get("primaryWeakPoint"), 4),
            (profile_context.get("currentChapter"), 4),
            (" ".join(profile_context.get("weakPoints", [])), 4),
            (profile_context.get("learningGoal"), 3),
            (profile_context.get("currentCourse"), 1),
        ):
            for term in self._split_topic_terms(self._normalize_text(raw)):
                if term and all(existing != term for existing, _ in terms):
                    terms.append((term, weight))
        return terms

    def _split_topic_terms(self, text: str) -> list[str]:
        if not text:
            return []
        parts = [
            part.strip()
            for part in re.split(r"[\s/、,，;；:：>《》()（）【】\[\]\"'“”]+", text)
            if part.strip()
        ]
        terms: list[str] = []
        for part in parts:
            if part in GENERIC_RESOURCE_TERMS:
                continue
            terms.append(part)
            if len(part) >= 4:
                for size in (4, 3, 2):
                    for index in range(0, len(part) - size + 1):
                        piece = part[index:index + size]
                        if piece not in GENERIC_RESOURCE_TERMS:
                            terms.append(piece)
        return terms

    def _contains_topic_term(self, text: str, term: str) -> bool:
        lowered = term.lower().strip()
        if not lowered:
            return False
        if re.fullmatch(r"[a-z0-9_+\-.#]+", lowered):
            return re.search(rf"(?<![a-z0-9_+\-.#]){re.escape(lowered)}(?![a-z0-9_+\-.#])", text) is not None
        return lowered in text

    def _matches_specific_topic(self, weighted_text: str, profile_context: dict[str, Any]) -> bool:
        raw_topics: list[str] = []
        for key in ("primaryWeakPoint", "currentChapter"):
            text = self._normalize_text(profile_context.get(key))
            if text:
                raw_topics.append(text)
        weak_points = profile_context.get("weakPoints")
        if isinstance(weak_points, list):
            raw_topics.extend(self._normalize_text(item) for item in weak_points if self._normalize_text(item))

        for raw_topic in raw_topics:
            if self._is_generic_topic_phrase(raw_topic):
                continue
            for term in self._split_topic_terms(raw_topic):
                if not self._is_generic_topic_term(term) and self._contains_topic_term(weighted_text, term):
                    return True
        return False

    def _is_generic_topic_term(self, term: str) -> bool:
        normalized = term.lower().strip()
        return not normalized or normalized in GENERIC_TOPIC_TERMS or any(
            normalized in generic_term or generic_term in normalized
            for generic_term in GENERIC_TOPIC_TERMS
        )

    def _is_generic_topic_phrase(self, text: str) -> bool:
        normalized = text.lower().strip()
        if not normalized:
            return True
        stripped = normalized
        for generic_term in sorted(GENERIC_TOPIC_TERMS, key=len, reverse=True):
            if generic_term:
                stripped = stripped.replace(generic_term, " ")
        stripped = re.sub(r"[\s/、，,；;：:。.!！?？()（）\[\]\"'“”]+", "", stripped)
        return not stripped

    def _resource_type_label(self, preferred_type: str) -> str:
        return {
            "EXPLANATION": "讲解文档",
            "DOCUMENT": "讲解文档",
            "CODE_CASE": "代码案例",
            "CODE": "代码案例",
            "PRACTICAL_CASE": "实操案例",
            "PPT": "PPT课件",
            "READING": "拓展阅读",
            "SLIDES": "PPT课件",
            "VIDEO": "视频",
            "QUIZ": "题库练习",
        }.get(preferred_type, preferred_type or "资源")

    def _passes_content_safety(self, title: str, summary: str, url: str) -> bool:
        combined = f"{title} {summary} {url}".lower()
        blocked_tokens = (
            "china-dictatorship",
            "anti chinese",
            "anti-china",
            "anti china",
            "anti ccp",
            "反共",
            "反华",
            "政治宣传",
            "宣传库",
            "propaganda",
            "dictatorship",
            "falun",
            "falun gong",
            "法轮功",
            "六四",
            "天安门",
            "疆独",
            "港独",
            "台独",
            "邪教",
            "习近平",
            "xijinping",
            "ccp",
            "共产党",
        )
        return not any(token in combined for token in blocked_tokens)

    def _clean_display_text(self, text: str) -> str:
        compact = " ".join(part for part in text.replace("\n", " ").replace("\r", " ").split() if part)
        return compact.strip()

    def _truncate_display_text(self, text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return text[:limit]

    @staticmethod
    def _normalize_text(value: Any) -> str:
        return str(value).strip() if isinstance(value, str) else ""
