import pytest

from src.ai_modules.llms.openai_compatible import (
    OpenAICompatibleClient,
    extract_json_object_from_text,
)
from src.ai_modules.llms.errors import LLMServiceError, classify_llm_http_error
from src.ai_modules.config import Settings
from src.ai_modules.models import ReasoningStreamConfig


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


def test_llm_http_error_classifier_detects_quota_before_status_category() -> None:
    error = classify_llm_http_error(
        status_code=400,
        response_text='{"error":{"message":"insufficient quota for this API key"}}',
        provider="openai_compatible",
        model="test-model",
    )

    assert error.code == "LLM_QUOTA_EXHAUSTED"
    assert error.message == "当前模型额度不足，请检查 API Key 额度或更换模型配置。"
    assert error.http_status == 400
    assert error.provider == "openai_compatible"
    assert error.model == "test-model"


def test_llm_http_error_classifier_detects_rate_limit() -> None:
    error = classify_llm_http_error(
        status_code=429,
        response_text='{"error":{"message":"too many requests"}}',
        provider="openai_compatible",
        model="test-model",
    )

    assert error.code == "LLM_RATE_LIMITED"
    assert error.retryable is True


def test_llm_service_error_can_be_recovered_from_exception_chain() -> None:
    llm_error = LLMServiceError(
        code="LLM_AUTH_INVALID",
        message="API Key 无效或已过期，请在设置页重新保存模型配置。",
        http_status=401,
        provider="openai_compatible",
        model="test-model",
    )
    wrapped = RuntimeError("outer")
    wrapped.__cause__ = llm_error

    assert LLMServiceError.from_exception(wrapped) is llm_error


class _FakeStreamResponse:
    def __init__(self, lines: list[str]) -> None:
        self.lines = lines
        self.status_code = 200

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


@pytest.mark.asyncio
async def test_openai_compatible_client_allows_reasoning_config_without_request_param(monkeypatch) -> None:
    fake_client = _FakeAsyncClient(
        [
            'data: {"choices":[{"delta":{"reasoning_content":"think","content":"answer"}}]}',
            'data: [DONE]',
        ]
    )
    settings = Settings(
        ACTIVE_PROVIDER="openai_compatible",
        OPENAI_COMPATIBLE_API_KEY="test-key",
    )
    routing = settings.build_default_model_routing_config()
    routing.providers["openai_compatible"].reasoning_models["deepseek-reasoner"] = ReasoningStreamConfig(
        request={},
        streamFields=["reasoning_content"],
        messageFields=["reasoning_content"],
    )
    client = OpenAICompatibleClient(
        api_key="test-key",
        base_url="https://llm.example.test/v1",
        model_name="deepseek-reasoner",
        provider_name="openai_compatible",
    )

    async def fake_get_client():
        return fake_client

    class FakeSettings:
        def reasoning_stream_config(self, *, provider_name, model_name):
            return routing.resolve_reasoning_config(model_name, provider_name)

    monkeypatch.setattr("src.ai_modules.llms.openai_compatible.get_settings", FakeSettings)
    monkeypatch.setattr(client, "_get_client", fake_get_client)

    chunks = [
        chunk
        async for chunk in client.chat_completion_stream_events(
            messages=[{"role": "user", "content": "hello"}],
            include_reasoning=True,
        )
    ]

    assert [(chunk.kind, chunk.text) for chunk in chunks] == [
        ("reasoning", "think"),
        ("answer", "answer"),
    ]
    assert "thinking" not in fake_client.request_json
