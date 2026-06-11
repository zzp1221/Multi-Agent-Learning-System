import pytest

from src.ai_modules.llms.openai_compatible import (
    OpenAICompatibleClient,
    extract_json_object_from_text,
)
from src.ai_modules.config import Settings


def test_extract_json_object_from_text_prefers_fenced_final_object() -> None:
    mixed_output = """
推理草稿:
{"topic":"联合索引","attempt":1}

最终答案:
```json
{"title":"联合索引导学","summary":"真实结构化输出"}
```
"""

    payload = extract_json_object_from_text(mixed_output)

    assert payload["title"] == "联合索引导学"
    assert payload["summary"] == "真实结构化输出"


def test_extract_json_object_from_text_prefers_outermost_object_for_plain_json() -> None:
    mixed_output = """
{
  "title": "联合索引导学",
  "slides": [
    {
      "title": "第一页",
      "bullets": ["A", "B"]
    }
  ]
}
"""

    payload = extract_json_object_from_text(mixed_output)

    assert payload["title"] == "联合索引导学"
    assert isinstance(payload["slides"], list)


class _FakeStreamResponse:
    def __init__(self, lines: list[str]) -> None:
        self.lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    async def aiter_lines(self):
        for line in self.lines:
            yield line


class _FakeAsyncClient:
    def __init__(self, lines: list[str]) -> None:
        self.lines = lines
        self.request_json = None
        self.request_headers = None

    def stream(self, method, url, *, headers, json):
        self.request_json = json
        self.request_headers = headers
        return _FakeStreamResponse(self.lines)


@pytest.mark.asyncio
async def test_openai_compatible_client_streams_delta_content(monkeypatch) -> None:
    fake_client = _FakeAsyncClient(
        [
            'data: {"choices":[{"delta":{"content":"Hel"}}]}',
            'data: {"choices":[{"delta":{"content":"lo"}}]}',
            'data: [DONE]',
        ]
    )
    client = OpenAICompatibleClient(
        api_key="test-key",
        base_url="https://llm.example.test/v1",
        model_name="test-model",
    )

    async def fake_get_client():
        return fake_client

    monkeypatch.setattr(client, "_get_client", fake_get_client)

    chunks = [
        chunk
        async for chunk in client.chat_completion_stream(
            messages=[{"role": "user", "content": "hello"}],
        )
    ]

    assert chunks == ["Hel", "lo"]
    assert fake_client.request_json["stream"] is True
    assert fake_client.request_headers["Accept"] == "text/event-stream"


@pytest.mark.asyncio
async def test_openai_compatible_client_streams_reasoning_chunks(monkeypatch) -> None:
    fake_client = _FakeAsyncClient(
        [
            'data: {"choices":[{"delta":{"reasoning_content":"think-1"}}]}',
            'data: {"choices":[{"delta":{"content":"answer-1"}}]}',
            'data: {"choices":[{"delta":{"reasoning":"think-2","content":"answer-2"}}]}',
            'data: [DONE]',
        ]
    )
    settings = Settings(
        ACTIVE_PROVIDER="openai_compatible",
        OPENAI_COMPATIBLE_API_KEY="test-key",
        REASONING_MODEL_NAME="reasoning-model",
    )
    client = OpenAICompatibleClient(
        api_key="test-key",
        base_url="https://llm.example.test/v1",
        model_name="reasoning-model",
        provider_name="openai_compatible",
    )

    async def fake_get_client():
        return fake_client

    monkeypatch.setattr("src.ai_modules.llms.openai_compatible.get_settings", lambda: settings)
    monkeypatch.setattr(client, "_get_client", fake_get_client)

    chunks = [
        chunk
        async for chunk in client.chat_completion_stream_events(
            messages=[{"role": "user", "content": "hello"}],
            include_reasoning=True,
        )
    ]

    assert [(chunk.kind, chunk.text) for chunk in chunks] == [
        ("reasoning", "think-1"),
        ("answer", "answer-1"),
        ("reasoning", "think-2"),
        ("answer", "answer-2"),
    ]
    assert fake_client.request_json["thinking"] == {"type": "enabled"}


@pytest.mark.asyncio
async def test_openai_compatible_client_does_not_enable_unconfigured_reasoning(monkeypatch) -> None:
    fake_client = _FakeAsyncClient(
        [
            'data: {"choices":[{"delta":{"reasoning_content":"hidden","content":"answer"}}]}',
            'data: [DONE]',
        ]
    )
    settings = Settings(
        ACTIVE_PROVIDER="openai_compatible",
        OPENAI_COMPATIBLE_API_KEY="test-key",
        REASONING_MODEL_NAME="different-model",
    )
    client = OpenAICompatibleClient(
        api_key="test-key",
        base_url="https://llm.example.test/v1",
        model_name="plain-model",
        provider_name="openai_compatible",
    )

    async def fake_get_client():
        return fake_client

    monkeypatch.setattr("src.ai_modules.llms.openai_compatible.get_settings", lambda: settings)
    monkeypatch.setattr(client, "_get_client", fake_get_client)

    chunks = [
        chunk
        async for chunk in client.chat_completion_stream_events(
            messages=[{"role": "user", "content": "hello"}],
            include_reasoning=True,
        )
    ]

    assert [(chunk.kind, chunk.text) for chunk in chunks] == [("answer", "answer")]
    assert fake_client.request_json["thinking"] == {"type": "disabled"}
