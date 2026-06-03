"""SSE event contracts for the internal Python agent stream."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.ai_modules.models.practice import JudgeResultPayload, QuestionBatchPayload


class DialogState(BaseModel):
    """Dialogue metadata for tutoring-oriented event streams."""

    conversation_id: str | None = Field(default=None, alias="conversationId")
    turn_id: str | None = Field(default=None, alias="turnId")
    pedagogy_strategy: str | None = Field(default=None, alias="pedagogyStrategy")
    next_action: str | None = Field(default=None, alias="nextAction")

    model_config = ConfigDict(populate_by_name=True)


class EngineStreamRequest(BaseModel):
    """Internal request from Java BFF to the Python agent."""

    service_type: str = Field(alias="serviceType")
    params: dict[str, Any] = Field(default_factory=dict)
    user_id: str | None = Field(default=None, alias="userId")
    task_id: str = Field(alias="taskId")
    trace_id: str = Field(alias="traceId")
    conversation_id: str | None = Field(default=None, alias="conversationId")

    model_config = ConfigDict(populate_by_name=True)

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_java_payload(cls, raw_value: Any) -> Any:
        """Accept legacy Java envelopes so FastAPI does not reject them with 422."""

        if not isinstance(raw_value, dict):
            return raw_value

        normalized = dict(raw_value)
        wrapped_payload = normalized.get("requestPayload")
        if isinstance(wrapped_payload, dict):
            merged = dict(wrapped_payload)
            merged.update(normalized)
            normalized = merged

        service_type = normalized.get("serviceType", normalized.get("service_type"))
        if isinstance(service_type, str):
            normalized["serviceType"] = {
                "LEARNING_EVALUATION": "EVALUATION",
            }.get(service_type.strip().upper(), service_type.strip().upper())

        params = normalized.get("params")
        if params is None and isinstance(wrapped_payload, dict):
            params = wrapped_payload.get("params")
        if isinstance(params, dict) and isinstance(params.get("params"), dict):
            nested_params = params.get("params")
            other_keys = {key for key in params if key != "params"}
            if not other_keys:
                params = nested_params
        if params is None:
            params = {}
        normalized["params"] = params

        for key in ("userId", "taskId", "traceId", "conversationId"):
            value = normalized.get(key)
            if value is not None and not isinstance(value, str):
                normalized[key] = str(value)

        return normalized


class ProgressPayload(BaseModel):
    """Progress update payload."""

    stage: str
    percent: int
    message: str | None = None
    agent_name: str | None = Field(default=None, alias="agentName")
    phase: str | None = None
    artifact_type: str | None = Field(default=None, alias="artifactType")
    status: str | None = None
    parent_step: str | None = Field(default=None, alias="parentStep")
    script_json: dict[str, Any] | None = Field(default=None, alias="scriptJson")
    script_text: str | None = Field(default=None, alias="scriptText")
    audio_base64: str | None = Field(default=None, alias="audioBase64")
    audio_url: str | None = Field(default=None, alias="audioUrl")
    audio_format: str | None = Field(default=None, alias="format")
    avatar_data_url: str | None = Field(default=None, alias="avatarDataUrl")
    duration_seconds: int | None = Field(default=None, alias="durationSeconds")
    title: str | None = None
    topic: str | None = None
    knowledge_point: str | None = Field(default=None, alias="knowledgePoint")
    video_style: str | None = Field(default=None, alias="videoStyle")
    generated_by: str | None = Field(default=None, alias="generatedBy")
    content_origin: str | None = Field(default=None, alias="contentOrigin")
    provider: str | None = None
    model: str | None = None
    evidence_ids: list[str] = Field(default_factory=list, alias="evidenceIds")
    fallback: bool | None = None
    from_cache: bool = Field(default=False, alias="fromCache")

    model_config = ConfigDict(populate_by_name=True)


class ResultChunkPayload(BaseModel):
    """Streaming text chunk payload."""

    text: str
    stage: str | None = None


class ResourceFilePayload(BaseModel):
    """Generated asset metadata payload."""

    asset_type: str = Field(alias="assetType")
    title: str
    summary: str
    display_mode: str = Field(alias="displayMode")
    file_name: str = Field(default="", alias="fileName")
    local_path: str | None = Field(default=None, alias="localPath")
    mime_type: str | None = Field(default=None, alias="mimeType")
    inline_content: str | None = Field(default=None, alias="inlineContent")
    language: str | None = None
    explanation: str | None = None
    download_url: str | None = Field(default=None, alias="downloadUrl")
    expires_in_sec: int | None = Field(default=None, alias="expiresInSec")
    expires_at: str | None = Field(default=None, alias="expiresAt")
    thumbnail_url: str | None = Field(default=None, alias="thumbnailUrl")
    thumbnail_path: str | None = Field(default=None, alias="thumbnailPath")
    thumbnail_file_name: str | None = Field(default=None, alias="thumbnailFileName")
    thumbnail_mime_type: str | None = Field(default=None, alias="thumbnailMimeType")
    duration_seconds: int | None = Field(default=None, alias="durationSeconds")
    video_style: str | None = Field(default=None, alias="videoStyle")
    knowledge_point: str | None = Field(default=None, alias="knowledgePoint")
    source_name: str | None = Field(default=None, alias="sourceName")
    generated_by: str | None = Field(default=None, alias="generatedBy")
    content_origin: str | None = Field(default=None, alias="contentOrigin")
    provider: str | None = None
    model: str | None = None
    agent_name: str | None = Field(default=None, alias="agentName")
    evidence_ids: list[str] = Field(default_factory=list, alias="evidenceIds")
    fallback: bool | None = None
    from_cache: bool = Field(default=False, alias="fromCache")

    model_config = ConfigDict(populate_by_name=True)


class DonePayload(BaseModel):
    """Completion payload."""

    status: Literal["SUCCESS", "FAILED", "PARTIAL_FAILED"] = "SUCCESS"
    summary: str
    mastery_diagnosis: dict[str, Any] | None = Field(default=None, alias="masteryDiagnosis")
    learning_path: dict[str, Any] | None = Field(default=None, alias="learningPath")
    learning_plan: dict[str, Any] | None = Field(default=None, alias="learningPlan")
    resource_push_plan: dict[str, Any] | None = Field(default=None, alias="resourcePushPlan")
    pushed_resources: list[dict[str, Any]] = Field(default_factory=list, alias="pushedResources")
    agent_trace: list[dict[str, Any]] = Field(default_factory=list, alias="agentTrace")
    critic_review: dict[str, Any] | None = Field(default=None, alias="criticReview")
    resource_failures: list[dict[str, Any]] = Field(default_factory=list, alias="resourceFailures")

    model_config = ConfigDict(populate_by_name=True)


class ErrorPayload(BaseModel):
    """Error payload."""

    code: str
    message: str


class SSEEvent(BaseModel):
    """Generic server-sent event with a JSON payload."""

    event: str
    task_id: str = Field(alias="taskId")
    trace_id: str = Field(alias="traceId")
    seq: int
    payload: dict[str, Any]
    dialog_state: DialogState | None = Field(default=None, alias="dialogState")

    model_config = ConfigDict(populate_by_name=True)

    def to_sse(self) -> str:
        """Render the event in SSE wire format."""

        return (
            f"event: {self.event}\n"
            f"data: {json.dumps(self.model_dump(by_alias=True), ensure_ascii=False)}\n\n"
        )


class ProgressSSEEvent(SSEEvent):
    """Typed progress event."""

    event: Literal["progress"] = "progress"
    payload: ProgressPayload


class ResultChunkSSEEvent(SSEEvent):
    """Typed incremental text event."""

    event: Literal["result_chunk"] = "result_chunk"
    payload: ResultChunkPayload


class ResourceFileSSEEvent(SSEEvent):
    """Typed generated-file event."""

    event: Literal["resource_file"] = "resource_file"
    payload: ResourceFilePayload


class QuestionBatchSSEEvent(SSEEvent):
    """Typed generated-question event."""

    event: Literal["question_batch"] = "question_batch"
    payload: QuestionBatchPayload


class JudgeResultSSEEvent(SSEEvent):
    """Typed judge-result event."""

    event: Literal["judge_result"] = "judge_result"
    payload: JudgeResultPayload


class DoneSSEEvent(SSEEvent):
    """Typed completion event."""

    event: Literal["done"] = "done"
    payload: DonePayload


class ErrorSSEEvent(SSEEvent):
    """Typed error event."""

    event: Literal["error"] = "error"
    payload: ErrorPayload


class VideoProgressSSEEvent(SSEEvent):
    """Video generation progress event with stage-specific event type."""

    event: str  # "video_gen:start", "video_gen:script", "video_gen:speech", "video_gen:avatar"
    payload: ProgressPayload


class VideoCompleteSSEEvent(SSEEvent):
    """Video generation completion with result data."""

    event: Literal["video_gen:complete"] = "video_gen:complete"
    payload: dict[str, Any]
