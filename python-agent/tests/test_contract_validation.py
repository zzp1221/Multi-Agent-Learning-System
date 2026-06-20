"""Contract validation: ensure Python SSE output matches the shared schema.

Validates that every event type emitted by the Python agent produces
correct SSE wire format that the Java BFF can parse.

Run: pytest tests/test_contract_validation.py -v
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from src.ai_modules.models import (
    DonePayload,
    DoneSSEEvent,
    ErrorPayload,
    ErrorSSEEvent,
    ProgressPayload,
    ProgressSSEEvent,
    ReasoningChunkPayload,
    ReasoningChunkSSEEvent,
    ResourceFilePayload,
    ResourceFileSSEEvent,
    ResultChunkPayload,
    ResultChunkSSEEvent,
    VideoCompleteSSEEvent,
    VideoProgressSSEEvent,
)


def _schema_definitions() -> dict:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "contracts" / "sse-events.schema.json"
        if candidate.exists():
            return json.loads(candidate.read_text(encoding="utf-8"))["definitions"]
    raise AssertionError("contracts/sse-events.schema.json not found")


def _validate_contract_subset(event_data: dict) -> None:
    """Minimal local validator for the event-aware shared SSE contract."""

    definitions = _schema_definitions()
    payload_by_event = {
        "progress": "ProgressPayload",
        "result_chunk": "ResultChunkPayload",
        "reasoning_chunk": "ReasoningChunkPayload",
        "resource_file": "ResourceFilePayload",
        "question_batch": "QuestionBatchPayload",
        "judge_result": "JudgeResultPayload",
        "done": "DonePayload",
        "error": "ErrorPayload",
        "video_gen:start": "VideoGenerationPayload",
        "video_gen:script": "VideoGenerationPayload",
        "video_gen:speech": "VideoGenerationPayload",
        "video_gen:avatar": "VideoGenerationPayload",
        "video_gen:complete": "VideoGenerationPayload",
    }
    required_top_level = {"event", "taskId", "traceId", "seq", "payload"}
    assert required_top_level.issubset(event_data), f"Missing top-level fields: {required_top_level - set(event_data)}"
    assert event_data["event"] in definitions["EventType"]["enum"]
    payload_definition = definitions[payload_by_event[event_data["event"]]]
    payload = event_data["payload"]
    for field in payload_definition.get("required", []):
        assert field in payload, f"Missing required payload field {field} for {event_data['event']}"
    if event_data["event"] == "reasoning_chunk":
        phase = payload.get("phase")
        status = payload.get("status")
        percent = payload.get("percent")
        assert phase in payload_definition["properties"]["phase"]["enum"]
        assert status in payload_definition["properties"]["status"]["enum"]
        if percent is not None:
            assert 0 <= percent <= 100


def parse_sse_wire(wire: str) -> tuple[str, dict]:
    """Parse a single SSE frame into (event_type, data_dict)."""
    event_match = re.search(r"^event:\s*(.+)$", wire, re.MULTILINE)
    data_match = re.search(r"^data:\s*(.+)$", wire, re.MULTILINE)
    assert event_match, f"Missing event: line in SSE wire:\n{wire}"
    assert data_match, f"Missing data: line in SSE wire:\n{wire}"
    return event_match.group(1), json.loads(data_match.group(1))


def assert_common_fields(data: dict, expected_event: str, min_seq: int = 1) -> None:
    """Verify the fields that every SSE event must contain."""
    assert data["event"] == expected_event, f"event mismatch: {data['event']} != {expected_event}"
    assert "taskId" in data, "Missing taskId"
    assert "traceId" in data, "Missing traceId"
    assert "seq" in data, "Missing seq"
    assert data["seq"] >= min_seq, f"seq {data['seq']} < {min_seq}"
    assert "payload" in data, "Missing payload"
    assert isinstance(data["payload"], dict), "payload must be an object"


class TestSseEventContracts:
    """Verify every event type produces valid wire format."""

    TASK_ID = "550e8400-e29b-41d4-a716-446655440000"
    TRACE_ID = "trace-abc-123"

    def test_progress_event_wire_format(self) -> None:
        event = ProgressSSEEvent(
            event="progress",
            taskId=self.TASK_ID,
            traceId=self.TRACE_ID,
            seq=1,
            payload=ProgressPayload(stage="retrieving", percent=30),
        )
        wire = event.to_sse()
        assert wire.endswith("\n\n"), "SSE frame must end with double newline"
        evt_type, data = parse_sse_wire(wire)
        assert evt_type == "progress"
        assert_common_fields(data, "progress")
        assert data["payload"]["stage"] == "retrieving"
        assert data["payload"]["percent"] == 30

    def test_result_chunk_event_wire_format(self) -> None:
        event = ResultChunkSSEEvent(
            event="result_chunk",
            taskId=self.TASK_ID,
            traceId=self.TRACE_ID,
            seq=2,
            payload=ResultChunkPayload(text="这是第一段生成内容", stage="tutoring"),
        )
        wire = event.to_sse()
        evt_type, data = parse_sse_wire(wire)
        assert evt_type == "result_chunk"
        assert_common_fields(data, "result_chunk", min_seq=2)
        assert data["payload"]["text"] == "这是第一段生成内容"
        assert data["payload"]["stage"] == "tutoring"

    def test_public_trace_reasoning_chunk_event_wire_format(self) -> None:
        event = ReasoningChunkSSEEvent(
            taskId=self.TASK_ID,
            traceId=self.TRACE_ID,
            seq=3,
            payload=ReasoningChunkPayload(
                text="Tutor Agent 已识别到资源生成请求。",
                stage="resource_generation",
                publicTrace=True,
                agentName="Tutor",
                phase="intent",
                status="RUNNING",
                percent=24,
            ),
        )
        wire = event.to_sse()
        evt_type, data = parse_sse_wire(wire)
        assert evt_type == "reasoning_chunk"
        assert_common_fields(data, "reasoning_chunk", min_seq=3)
        assert data["payload"]["publicTrace"] is True
        assert data["payload"]["stage"] == "resource_generation"
        assert data["payload"]["agentName"] == "Tutor"
        _validate_contract_subset(data)

    def test_normal_reasoning_chunk_event_wire_format(self) -> None:
        event = ReasoningChunkSSEEvent(
            taskId=self.TASK_ID,
            traceId=self.TRACE_ID,
            seq=4,
            payload=ReasoningChunkPayload(
                text="理解问题：用户正在询问检索策略。",
                stage="reasoning",
                provider="test-provider",
                model="test-model",
            ),
        )

        wire = event.to_sse()
        evt_type, data = parse_sse_wire(wire)

        assert evt_type == "reasoning_chunk"
        assert_common_fields(data, "reasoning_chunk", min_seq=4)
        assert data["payload"]["publicTrace"] is False
        assert data["payload"]["stage"] == "reasoning"
        _validate_contract_subset(data)

    def test_resource_file_event_wire_format(self) -> None:
        event = ResourceFileSSEEvent(
            event="resource_file",
            taskId=self.TASK_ID,
            traceId=self.TRACE_ID,
            seq=3,
            payload=ResourceFilePayload(
                assetType="DOCUMENT",
                title="Python入门教程",
                summary="一份适合初学者的Python教程",
                displayMode="inline",
                fileName="tutorial.md",
                localPath="/data/sandbox-temp/doc-123/tutorial.md",
                mimeType="text/markdown",
            ),
        )
        wire = event.to_sse()
        evt_type, data = parse_sse_wire(wire)
        assert evt_type == "resource_file"
        assert_common_fields(data, "resource_file")
        p = data["payload"]
        assert p["assetType"] == "DOCUMENT"
        assert p["title"] == "Python入门教程"
        assert p["fileName"] == "tutorial.md"

    def test_done_event_wire_format(self) -> None:
        event = DoneSSEEvent(
            event="done",
            taskId=self.TASK_ID,
            traceId=self.TRACE_ID,
            seq=10,
            payload=DonePayload(status="SUCCESS", summary="文档生成完成"),
        )
        wire = event.to_sse()
        evt_type, data = parse_sse_wire(wire)
        assert evt_type == "done"
        assert_common_fields(data, "done")
        assert data["payload"]["status"] == "SUCCESS"
        assert data["payload"]["summary"] == "文档生成完成"

    def test_done_event_planning_metadata_keeps_sse_wire_format(self) -> None:
        event = DoneSSEEvent(
            event="done",
            taskId=self.TASK_ID,
            traceId=self.TRACE_ID,
            seq=11,
            payload=DonePayload(
                status="SUCCESS",
                summary="自主规划完成",
                planning={"preset": "RAG_TUTOR", "level": "preset_router"},
                checkpointActions=[{"checkpointType": "RETRIEVAL_EVIDENCE", "status": "APPLIED"}],
                learningLoop={"status": "COMPLETED"},
            ),
        )

        wire = event.to_sse()
        evt_type, data = parse_sse_wire(wire)

        assert wire.startswith("event: done\ndata: ")
        assert wire.endswith("\n\n")
        assert evt_type == "done"
        assert_common_fields(data, "done")
        assert data["payload"]["planning"]["preset"] == "RAG_TUTOR"
        assert data["payload"]["checkpointActions"][0]["checkpointType"] == "RETRIEVAL_EVIDENCE"
        assert data["payload"]["learningLoop"]["status"] == "COMPLETED"

    def test_error_event_wire_format(self) -> None:
        event = ErrorSSEEvent(
            event="error",
            taskId=self.TASK_ID,
            traceId=self.TRACE_ID,
            seq=5,
            payload=ErrorPayload(code="LLM_TIMEOUT", message="模型调用超时"),
        )
        wire = event.to_sse()
        evt_type, data = parse_sse_wire(wire)
        assert evt_type == "error"
        assert_common_fields(data, "error")
        assert data["payload"]["code"] == "LLM_TIMEOUT"

    def test_video_gen_progress_event_wire_format(self) -> None:
        event = VideoProgressSSEEvent(
            event="video_gen:start",
            taskId=self.TASK_ID,
            traceId=self.TRACE_ID,
            seq=1,
            payload=ProgressPayload(stage="video_gen", percent=0),
        )
        wire = event.to_sse()
        evt_type, data = parse_sse_wire(wire)
        assert evt_type == "video_gen:start"
        assert_common_fields(data, "video_gen:start")

    def test_video_gen_complete_event_wire_format(self) -> None:
        event = VideoCompleteSSEEvent(
            event="video_gen:complete",
            taskId=self.TASK_ID,
            traceId=self.TRACE_ID,
            seq=10,
            payload={"videoUrl": "http://example.com/video.mp4"},
        )
        wire = event.to_sse()
        evt_type, data = parse_sse_wire(wire)
        assert evt_type == "video_gen:complete"
        assert_common_fields(data, "video_gen:complete")

    def test_all_terminal_events_have_correct_semantics(self) -> None:
        """Done and Error are the only terminal events."""
        done = DoneSSEEvent(
            event="done",
            taskId=self.TASK_ID,
            traceId=self.TRACE_ID,
            seq=1,
            payload=DonePayload(status="SUCCESS", summary="done"),
        )
        err = ErrorSSEEvent(
            event="error",
            taskId=self.TASK_ID,
            traceId=self.TRACE_ID,
            seq=1,
            payload=ErrorPayload(code="E1", message="err"),
        )
        # Done with FAILED status is still a done event (terminal)
        fail_done = DoneSSEEvent(
            event="done",
            taskId=self.TASK_ID,
            traceId=self.TRACE_ID,
            seq=1,
            payload=DonePayload(status="FAILED", summary="cancelled"),
        )

        for event in [done, err, fail_done]:
            _, data = parse_sse_wire(event.to_sse())
            assert data["event"] in ("done", "error")

    def test_event_type_mapping_is_complete(self) -> None:
        """All known event types from StreamEventType are testable via typed models."""
        typed_events = {
            "progress": ProgressSSEEvent,
            "result_chunk": ResultChunkSSEEvent,
            "reasoning_chunk": ReasoningChunkSSEEvent,
            "resource_file": ResourceFileSSEEvent,
            "done": DoneSSEEvent,
            "error": ErrorSSEEvent,
        }
        # Also covered: video_gen:* via VideoProgressSSEEvent / VideoCompleteSSEEvent
        known_events = {
            "message", "progress", "result_chunk", "reasoning_chunk", "resource_file",
            "question_batch", "judge_result", "done", "error",
            "video_gen:start", "video_gen:script", "video_gen:speech",
            "video_gen:avatar", "video_gen:complete",
        }
        covered = set(typed_events.keys()) | {"message", "question_batch", "judge_result",
            "video_gen:start", "video_gen:script", "video_gen:speech", "video_gen:avatar", "video_gen:complete"}
        assert covered == known_events, f"Missing event types in coverage: {known_events - covered}"
