"""将确定性资源写入沙箱存储的生成 Agent。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import inspect
import logging
from pathlib import Path
from typing import Any

from src.ai_modules.agents.base import PlaceholderAgent
from src.ai_modules.agents.common_agents import CriticAgent, SafetyAgent
from src.ai_modules.async_utils import cancel_and_await
from src.ai_modules.config import get_settings
from src.ai_modules.generation import ResourceGenerationService
from src.ai_modules.llms import GenerationToolLLMClientFactory
from src.ai_modules.models import (
    DonePayload,
    DoneSSEEvent,
    ResourceFilePayload,
    ResourceFileSSEEvent,
    ResultChunkPayload,
    ResultChunkSSEEvent,
    SSEEvent,
    ProgressPayload,
    ProgressSSEEvent,
    VideoCompleteSSEEvent,
    VideoProgressSSEEvent,
)
from src.ai_modules.runtime import (
    SystemSnapshot,
)
from src.ai_modules.runtime.provenance import build_llm_provenance, validate_llm_provenance

LOGGER = logging.getLogger(__name__)


class _BaseGenerationAgent(PlaceholderAgent):
    def __init__(
        self,
        agent_name: str,
        stage_name: str,
        asset_type: str,
        generation_service: ResourceGenerationService | None = None,
        llm_client: Any | None = None,
        critic_agent: CriticAgent | None = None,
        safety_agent: SafetyAgent | None = None,
        heartbeat_interval_seconds: float = 15.0,
    ) -> None:
        super().__init__(agent_name, stage_name, emits_result_chunk=True)
        self.asset_type = asset_type
        self.generation_service = generation_service or ResourceGenerationService()
        self.llm_client = llm_client or GenerationToolLLMClientFactory.create()
        self.critic_agent = critic_agent or CriticAgent()
        self.safety_agent = safety_agent or SafetyAgent()
        self.heartbeat_interval_seconds = heartbeat_interval_seconds

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
        next_seq = seq
        final_output: dict[str, Any] | None = None
        final_output_task = asyncio.create_task(
            self._run_agent_core_loop(
                task_id=task_id,
                params=params,
                snapshot=snapshot,
                system_prompt=system_prompt,
            )
        )
        try:
            while not final_output_task.done():
                try:
                    final_output = await asyncio.wait_for(
                        asyncio.shield(final_output_task),
                        timeout=self.heartbeat_interval_seconds,
                    )
                    break
                except TimeoutError:
                    yield ProgressSSEEvent(
                        taskId=task_id,
                        traceId=trace_id,
                        seq=next_seq,
                        payload=ProgressPayload(
                            stage=self.stage_name,
                            percent=70,
                            message="资源生成仍在执行中，请稍候",
                        ),
                    )
                    next_seq += 1
            else:
                final_output = await final_output_task
        except asyncio.CancelledError:
            await cancel_and_await(final_output_task)
            raise

        if final_output is None:
            final_output = await final_output_task
        asset = final_output["asset"]
        critic_review = final_output["criticReview"]
        safety_review = final_output["safetyReview"]

        if not safety_review.allowed:
            yield ResultChunkSSEEvent(
                taskId=task_id,
                traceId=trace_id,
                seq=next_seq,
                payload=ResultChunkPayload(text=safety_review.summary_text),
            )
            yield DoneSSEEvent(
                taskId=task_id,
                traceId=trace_id,
                seq=next_seq + 1,
                payload=DonePayload(status="FAILED", summary=safety_review.summary_text),
            )
            return

        yield ResultChunkSSEEvent(
            taskId=task_id,
            traceId=trace_id,
            seq=next_seq,
            payload=ResultChunkPayload(
                text=(
                    f"{self.agent_name} 已生成 {asset.asset_type} 资源；"
                    f"预览: {asset.preview_text}；"
                    f"Critic: {critic_review.summary_text}；"
                    f"Safety: {safety_review.summary_text}"
                )
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
            videoStyle=asset.video_style,
            knowledgePoint=asset.knowledge_point,
            generatedBy=asset.generated_by,
            contentOrigin=asset.content_origin,
            provider=asset.provider,
            model=asset.model,
            agentName=asset.agent_name,
            evidenceIds=asset.evidence_ids,
            fallback=asset.fallback,
            fromCache=asset.from_cache,
        )
        validate_llm_provenance(resource_payload, artifact_label=f"{self.stage_name}:{asset.asset_type}")
        yield ResourceFileSSEEvent(
            taskId=task_id,
            traceId=trace_id,
            seq=next_seq + 1,
            payload=resource_payload,
        )

    async def _run_agent_core_loop(
        self,
        *,
        task_id: str,
        params: dict[str, Any],
        snapshot: SystemSnapshot,
        system_prompt: str,
    ) -> dict[str, Any]:
        outline = self._tool_generate_outline(tool_input={}, params=params, snapshot=snapshot)
        draft = await self._tool_expand_content(
            tool_input=outline, task_id=task_id, params=params, snapshot=snapshot,
        )

        import copy
        review_params = copy.deepcopy(params)
        safety_params = copy.deepcopy(params)

        await asyncio.gather(
            self._tool_review_content(tool_input=draft, params=review_params, snapshot=snapshot),
            self._tool_format_output(tool_input=draft, params=safety_params, snapshot=snapshot),
        )

        params["criticReview"] = review_params.get("criticReview", {})
        params["safetyReview"] = safety_params.get("safetyReview", {})

        return self._normalize_final_output({
            "asset": draft["asset"],
            "criticReview": params["criticReview"],
            "safetyReview": params["safetyReview"],
        })

    def _tool_generate_outline(
        self,
        *,
        tool_input: dict[str, Any],
        params: dict[str, Any],
        snapshot: SystemSnapshot,
    ) -> dict[str, Any]:
        del tool_input
        topic = self._resolve_topic(params)
        sources = (params.get("retrievalResult") or {}).get("documents", [])
        if self.asset_type == "DOCUMENT":
            outline = {
                "assetType": self.asset_type,
                "topic": topic,
                "sections": [
                    section.model_dump(by_alias=True)
                    for section in self.generation_service._plan_document_sections(
                        params=params,
                        snapshot=snapshot,
                        sources=sources,
                    )
                ],
            }
        else:
            outline = {
                "assetType": self.asset_type,
                "topic": topic,
                "sections": [
                    {"title": "学习目标", "objective": f"围绕 {topic} 建立概念框架"},
                    {"title": "核心内容", "objective": "扩展关键原理和例子"},
                    {"title": "复盘建议", "objective": "输出练习与复习建议"},
                ],
            }
        params["generationOutline"] = outline
        return outline

    async def _tool_expand_content(
        self,
        *,
        tool_input: dict[str, Any],
        task_id: str,
        params: dict[str, Any],
        snapshot: SystemSnapshot,
    ) -> dict[str, Any]:
        del tool_input
        build_params = dict(params)
        build_params.setdefault("taskId", task_id)
        if self._is_deep_quality_mode(build_params):
            build_params["generationQualityMode"] = "deep"
            build_params["generationQualityInstruction"] = (
                "Deep quality mode: create a more complete, evidence-grounded resource. "
                "Include clear structure, important edge cases, learner misconceptions, "
                "and a brief self-check before finalizing."
            )

        async def operation() -> dict[str, Any]:
            asset = await self._call_asset_builder(build_params=build_params, snapshot=snapshot)
            generated_content = (
                asset.preview_text
                if asset.asset_type == "VIDEO"
                else asset.inline_content
                if asset.inline_content
                else Path(asset.local_path).read_text(encoding="utf-8")
                if self._should_read_local_asset_text(asset)
                else asset.preview_text
            )
            return {
                "asset": asset.model_dump(by_alias=True),
                "generatedContent": generated_content,
            }
        draft = await operation()
        provenance = self._build_asset_provenance(params=params)
        draft["asset"].update(provenance)
        for key in ("videoGenerationTask", "videoSandboxArtifact", "tts_audio_bytes"):
            if key in build_params:
                params[key] = build_params[key]
        params["generatedAsset"] = draft["asset"]
        params["generatedContent"] = draft["generatedContent"]
        return draft

    async def _call_asset_builder(self, *, build_params: dict[str, Any], snapshot: SystemSnapshot) -> Any:
        builder = self.generation_service.build_asset
        kwargs = {"asset_type": self.asset_type, "params": build_params, "snapshot": snapshot}
        if inspect.iscoroutinefunction(builder):
            return await builder(**kwargs)
        return await asyncio.to_thread(builder, **kwargs)

    def _build_asset_provenance(self, *, params: dict[str, Any]) -> dict[str, Any]:
        generator = getattr(getattr(self.generation_service, "content_chain", None), "primary_generator", None)
        return build_llm_provenance(
            agent_name=self._wire_agent_name(),
            generator=generator,
            params=params,
        )

    def _wire_agent_name(self) -> str:
        return self.stage_name

    def _resolve_topic(self, params: dict[str, Any]) -> str:
        resolver = getattr(self.generation_service, "_display_topic", None)
        if callable(resolver):
            return str(resolver(params))
        learning_context = params.get("learningContext", {})
        candidates = [
            params.get("explicitUserTopic"),
            learning_context.get("explicitUserTopic") if isinstance(learning_context, dict) else None,
            params.get("topic"),
            params.get("keyPoints"),
            params.get("knowledgePoint"),
            learning_context.get("activeLearningStepTitle") if isinstance(learning_context, dict) else None,
            learning_context.get("chapter") if isinstance(learning_context, dict) else None,
            learning_context.get("course") if isinstance(learning_context, dict) else None,
            params.get("rewrittenQuery"),
            params.get("query"),
        ]
        for candidate in candidates:
            value = ResourceGenerationService._normalize_topic_candidate(candidate)
            if ResourceGenerationService._is_real_topic(value) and not ResourceGenerationService._looks_like_resource_command(value):
                return value
        raise RuntimeError("缺少资源生成真实主题：请提供当前学习阶段或明确的资源主题")

    @staticmethod
    def _is_deep_quality_mode(params: dict[str, Any]) -> bool:
        reasoning_mode = params.get("reasoningMode")
        if isinstance(reasoning_mode, str) and reasoning_mode.strip().upper() == "DEEP":
            return True
        return (
            params.get("deepReasoning") is True
            or params.get("deepQualityMode") is True
            or str(params.get("generationQualityMode") or "").strip().lower() == "deep"
        )

    @staticmethod
    def _should_read_local_asset_text(asset: Any) -> bool:
        if not getattr(asset, "local_path", None):
            return False
        mime_type = str(getattr(asset, "mime_type", "") or "").lower()
        if mime_type.startswith("text/"):
            return True
        if mime_type in {"application/json", "application/javascript"}:
            return True
        suffix = Path(str(asset.local_path)).suffix.lower()
        return suffix in {".md", ".txt", ".json", ".js", ".ts", ".tsx", ".py", ".java", ".xml", ".html", ".css"}

    async def _tool_review_content(
        self,
        *,
        tool_input: dict[str, Any],
        params: dict[str, Any],
        snapshot: SystemSnapshot,
    ) -> dict[str, Any]:
        if isinstance(tool_input, dict):
            params["generatedAsset"] = tool_input.get("asset", params.get("generatedAsset", {}))
            params["generatedContent"] = tool_input.get(
                "generatedContent",
                params.get("generatedContent", ""),
            )
        critic_review = await self.critic_agent.review_content(
            params=params,
            snapshot=snapshot,
            system_prompt=self.critic_agent.system_prompt(snapshot),
        )
        params["criticReview"] = critic_review.model_dump(by_alias=True)
        return {
            "asset": params["generatedAsset"],
            "generatedContent": params["generatedContent"],
            "criticReview": params["criticReview"],
        }

    async def _tool_format_output(
        self,
        *,
        tool_input: dict[str, Any],
        params: dict[str, Any],
        snapshot: SystemSnapshot,
    ) -> dict[str, Any]:
        if isinstance(tool_input, dict):
            params["generatedAsset"] = tool_input.get("asset", params.get("generatedAsset", {}))
            params["generatedContent"] = tool_input.get(
                "generatedContent",
                params.get("generatedContent", ""),
            )
            if tool_input.get("criticReview"):
                params["criticReview"] = tool_input["criticReview"]
        safety_review = await self.safety_agent.review_content(
            params=params,
            snapshot=snapshot,
            system_prompt=self.safety_agent.system_prompt(snapshot),
        )
        params["safetyReview"] = safety_review.model_dump(by_alias=True)
        return {
            "asset": params["generatedAsset"],
            "criticReview": params.get("criticReview", {}),
            "safetyReview": params["safetyReview"],
        }

    def _normalize_final_output(self, payload: dict[str, Any]) -> dict[str, Any]:
        from src.ai_modules.generation import GeneratedAsset
        from src.ai_modules.models import CriticReviewPayload, SafetyReviewPayload

        return {
            "asset": GeneratedAsset.model_validate(payload["asset"]),
            "criticReview": CriticReviewPayload.model_validate(payload["criticReview"]),
            "safetyReview": SafetyReviewPayload.model_validate(payload["safetyReview"]),
        }

class DocumentGeneratorAgent(_BaseGenerationAgent):
    def __init__(
        self,
        generation_service: ResourceGenerationService | None = None,
        llm_client: Any | None = None,
        critic_agent: CriticAgent | None = None,
        safety_agent: SafetyAgent | None = None,
    ) -> None:
        super().__init__(
            "Document Generator Agent",
            "document_generation",
            "DOCUMENT",
            generation_service=generation_service,
            llm_client=llm_client,
            critic_agent=critic_agent,
            safety_agent=safety_agent,
        )


class MindMapGeneratorAgent(_BaseGenerationAgent):
    def __init__(
        self,
        generation_service: ResourceGenerationService | None = None,
        llm_client: Any | None = None,
        critic_agent: CriticAgent | None = None,
        safety_agent: SafetyAgent | None = None,
    ) -> None:
        super().__init__(
            "MindMap Generator Agent",
            "mindmap_generation",
            "MINDMAP",
            generation_service=generation_service,
            llm_client=llm_client,
            critic_agent=critic_agent,
            safety_agent=safety_agent,
        )


class SlideGeneratorAgent(_BaseGenerationAgent):
    def __init__(
        self,
        generation_service: ResourceGenerationService | None = None,
        llm_client: Any | None = None,
        critic_agent: CriticAgent | None = None,
        safety_agent: SafetyAgent | None = None,
    ) -> None:
        super().__init__(
            "Slide Generator Agent",
            "slide_generation",
            "SLIDES",
            generation_service=generation_service,
            llm_client=llm_client,
            critic_agent=critic_agent,
            safety_agent=safety_agent,
        )


class ReadingGeneratorAgent(_BaseGenerationAgent):
    def __init__(
        self,
        generation_service: ResourceGenerationService | None = None,
        llm_client: Any | None = None,
        critic_agent: CriticAgent | None = None,
        safety_agent: SafetyAgent | None = None,
    ) -> None:
        super().__init__(
            "Reading Generator Agent",
            "reading_generation",
            "READING",
            generation_service=generation_service,
            llm_client=llm_client,
            critic_agent=critic_agent,
            safety_agent=safety_agent,
        )


class CodeGeneratorAgent(_BaseGenerationAgent):
    def __init__(
        self,
        generation_service: ResourceGenerationService | None = None,
        llm_client: Any | None = None,
        critic_agent: CriticAgent | None = None,
        safety_agent: SafetyAgent | None = None,
    ) -> None:
        super().__init__(
            "Code Generator Agent",
            "code_generation",
            "CODE",
            generation_service=generation_service,
            llm_client=llm_client,
            critic_agent=critic_agent,
            safety_agent=safety_agent,
        )


class VideoGenerationAgent(_BaseGenerationAgent):
    def __init__(
        self,
        generation_service: ResourceGenerationService | None = None,
        llm_client: Any | None = None,
        critic_agent: CriticAgent | None = None,
        safety_agent: SafetyAgent | None = None,
    ) -> None:
        super().__init__(
            "Video Generator Agent",
            "video_generation",
            "VIDEO",
            generation_service=generation_service,
            llm_client=llm_client,
            critic_agent=critic_agent,
            safety_agent=safety_agent,
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
        topic = self._resolve_topic(params)
        current_seq = seq
        yield VideoProgressSSEEvent(
            event="video_gen:start",
            taskId=task_id,
            traceId=trace_id,
            seq=current_seq,
            payload=ProgressPayload(
                stage="video_started",
                percent=10,
                message=f"{topic} 视频生成任务已启动",
            ),
        )
        current_seq += 1

        final_output_task = asyncio.create_task(
            self._run_agent_core_loop(
                task_id=task_id,
                params=params,
                snapshot=snapshot,
                system_prompt=system_prompt,
            )
        )
        final_output: dict[str, Any] | None = None
        try:
            while not final_output_task.done():
                try:
                    final_output = await asyncio.wait_for(
                        asyncio.shield(final_output_task),
                        timeout=self.heartbeat_interval_seconds,
                    )
                    break
                except TimeoutError:
                    yield ProgressSSEEvent(
                        taskId=task_id,
                        traceId=trace_id,
                        seq=current_seq,
                        payload=ProgressPayload(
                            stage=self.stage_name,
                            percent=85,
                            message="视频资源仍在生成中，请稍候",
                        ),
                    )
                    current_seq += 1
            else:
                final_output = await final_output_task
        except asyncio.CancelledError:
            await cancel_and_await(final_output_task)
            raise

        if final_output is None:
            final_output = await final_output_task
        asset = final_output["asset"]
        critic_review = final_output["criticReview"]
        safety_review = final_output["safetyReview"]

        if not safety_review.allowed:
            yield ResultChunkSSEEvent(
                taskId=task_id,
                traceId=trace_id,
                seq=current_seq,
                payload=ResultChunkPayload(text=safety_review.summary_text),
            )
            yield DoneSSEEvent(
                taskId=task_id,
                traceId=trace_id,
                seq=current_seq + 1,
                payload=DonePayload(status="FAILED", summary=safety_review.summary_text),
            )
            return

        yield ResultChunkSSEEvent(
            taskId=task_id,
            traceId=trace_id,
            seq=current_seq,
            payload=ResultChunkPayload(
                text=(
                    f"{self.agent_name} 已生成教学视频；"
                    f"预览: {asset.preview_text}；"
                    f"Critic: {critic_review.summary_text}；"
                    f"Safety: {safety_review.summary_text}"
                )
            ),
        )
        current_seq += 1
        resource_payload = ResourceFilePayload(
            assetType=asset.asset_type,
            title=asset.title,
            summary=asset.summary,
            displayMode=asset.display_mode,
            fileName=asset.file_name,
            localPath=asset.local_path,
            mimeType=asset.mime_type,
            thumbnailPath=asset.thumbnail_path,
            thumbnailFileName=asset.thumbnail_file_name,
            thumbnailMimeType=asset.thumbnail_mime_type,
            durationSeconds=asset.duration_seconds,
            videoStyle=asset.video_style,
            knowledgePoint=asset.knowledge_point,
            generatedBy=asset.generated_by,
            contentOrigin=asset.content_origin,
            provider=asset.provider,
            model=asset.model,
            agentName=asset.agent_name,
            evidenceIds=asset.evidence_ids,
            fallback=asset.fallback,
            fromCache=asset.from_cache,
        )
        validate_llm_provenance(resource_payload, artifact_label=f"{self.stage_name}:{asset.asset_type}")
        yield ResourceFileSSEEvent(
            taskId=task_id,
            traceId=trace_id,
            seq=current_seq,
            payload=resource_payload,
        )
        current_seq += 1
        video_provenance = {
            "generatedBy": asset.generated_by,
            "contentOrigin": asset.content_origin,
            "provider": asset.provider,
            "model": asset.model,
            "agentName": asset.agent_name,
            "evidenceIds": asset.evidence_ids,
            "fallback": asset.fallback,
            "fromCache": asset.from_cache,
        }
        validate_llm_provenance(video_provenance, artifact_label=f"{self.stage_name}:video_progress")
        settings = get_settings()
        video_task_payload = params.get("videoGenerationTask")
        video_artifact_payload = params.get("videoSandboxArtifact")
        script_payload = video_task_payload.get("script") if isinstance(video_task_payload, dict) else None
        browser_audio_base64 = None
        browser_audio_format = "mp3"
        browser_avatar_data_url = "/dh_live/assets/combined_data.json.gz"
        if isinstance(video_artifact_payload, dict):
            browser_audio_base64 = video_artifact_payload.get("audioBase64")
            browser_audio_format = str(video_artifact_payload.get("audioFormat") or browser_audio_format)
            browser_avatar_data_url = str(video_artifact_payload.get("avatarDataUrl") or browser_avatar_data_url)

        if isinstance(script_payload, dict):
            yield VideoProgressSSEEvent(
                event="video_gen:script",
                taskId=task_id,
                traceId=trace_id,
                seq=current_seq,
                payload=ProgressPayload(
                    stage="script_generated",
                    percent=25,
                    message="脚本生成完成",
                    scriptJson=script_payload,
                    scriptText=script_payload.get("fullText"),
                    durationSeconds=asset.duration_seconds,
                    title=asset.title,
                    topic=topic,
                    knowledgePoint=asset.knowledge_point,
                    videoStyle=asset.video_style,
                    **video_provenance,
                ),
            )
            current_seq += 1

        if browser_audio_base64:
            yield VideoProgressSSEEvent(
                event="video_gen:speech",
                taskId=task_id,
                traceId=trace_id,
                seq=current_seq,
                payload=ProgressPayload(
                    stage="speech_synthesized",
                    percent=50,
                    message="语音合成完成，前端即将开始浏览器本地渲染",
                    audioBase64=browser_audio_base64,
                    format=browser_audio_format,
                    avatarDataUrl=browser_avatar_data_url,
                    durationSeconds=asset.duration_seconds,
                    title=asset.title,
                    topic=topic,
                    knowledgePoint=asset.knowledge_point,
                    videoStyle=asset.video_style,
                    scriptJson=script_payload if isinstance(script_payload, dict) else None,
                    scriptText=script_payload.get("fullText") if isinstance(script_payload, dict) else None,
                    **video_provenance,
                ),
            )
            current_seq += 1

        yield VideoProgressSSEEvent(
            event="video_gen:avatar",
            taskId=task_id,
            traceId=trace_id,
            seq=current_seq,
            payload=ProgressPayload(
                stage="browser_rendering",
                percent=75,
                message="浏览器本地渲染准备完成",
                avatarDataUrl=browser_avatar_data_url,
                durationSeconds=asset.duration_seconds,
                title=asset.title,
                topic=topic,
                knowledgePoint=asset.knowledge_point,
                videoStyle=asset.video_style,
                **video_provenance,
            ),
        )
        current_seq += 1

        complete_payload: dict[str, Any] = {
            "topic": topic,
            "stage": "completed",
            "progress": 100,
            "message": "视频脚本与语音已生成，请在浏览器完成本地渲染",
            "title": asset.title,
            "duration": asset.duration_seconds,
            "durationSeconds": asset.duration_seconds,
            "style": asset.video_style,
            "videoStyle": asset.video_style,
            "knowledgePoint": asset.knowledge_point,
            "activeProvider": settings.runtime_provider_name(),
            "fallbackProvider": settings.selected_fallback_provider_name(),
            "thumbnailPath": asset.thumbnail_path,
            "criticScore": 1.0 if critic_review.verdict == "PASS" else 0.5,
            "safetyPassed": safety_review.allowed,
            "audioBase64": browser_audio_base64,
            "format": browser_audio_format,
            "avatarDataUrl": browser_avatar_data_url,
            **video_provenance,
        }
        if isinstance(video_task_payload, dict):
            complete_payload["videoGenerationTask"] = video_task_payload
            complete_payload["ttsProvider"] = video_task_payload.get("ttsProvider")
            complete_payload["avatarProvider"] = video_task_payload.get("avatarProvider")
            complete_payload["generationParams"] = video_task_payload.get("generationParams")
            if isinstance(script_payload, dict):
                complete_payload["scriptJson"] = script_payload
                complete_payload["scriptText"] = script_payload.get("fullText")
        if isinstance(video_artifact_payload, dict):
            complete_payload["videoSandboxArtifact"] = video_artifact_payload
            complete_payload["audioPath"] = video_artifact_payload.get("audioPath")
            complete_payload["thumbnailPath"] = video_artifact_payload.get("thumbnailPath", asset.thumbnail_path)
        yield VideoCompleteSSEEvent(
            taskId=task_id,
            traceId=trace_id,
            seq=current_seq,
            payload=complete_payload,
        )
        current_seq += 1
        yield ResultChunkSSEEvent(
            taskId=task_id,
            traceId=trace_id,
            seq=current_seq,
            payload=ResultChunkPayload(
                text=(
                    f"视频生成完成：主题={complete_payload['topic']}，"
                    f"时长={complete_payload.get('durationSeconds') or '未知'}秒，"
                    f"风格={complete_payload.get('videoStyle') or '默认'}，"
                    "可在当前浏览器直接预览和下载。"
                )
            ),
        )
