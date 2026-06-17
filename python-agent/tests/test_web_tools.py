import httpx
import pytest

from src.ai_modules.runtime import web_tools
from src.ai_modules.runtime.web_tools import WebFetchClient, WebSearchClient, validate_public_http_url

ORIGINAL_HTTPX_CLIENT = httpx.Client


def test_web_search_builtin_direct_url_without_api_key() -> None:
    result = WebSearchClient(provider="builtin", api_key="", cache_ttl_seconds=0).search(
        "read https://www.typescriptlang.org/docs/ today",
        top_k=3,
    )

    assert result.ok is True
    assert result.provider == "builtin"
    assert result.results == [
        {
            "title": "https://www.typescriptlang.org/docs/",
            "url": "https://www.typescriptlang.org/docs/",
            "snippet": "URL provided by the user query.",
            "publishedDate": "",
        }
    ]


def test_web_search_builtin_bilibili_success(monkeypatch) -> None:
    captured = {}

    def fake_handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["params"] = dict(request.url.params)
        return httpx.Response(
            200,
            json={
                "data": {
                    "result": [
                        {
                            "title": "<em class=\"keyword\">TypeScript</em> 入门",
                            "arcurl": "http://www.bilibili.com/video/BV1ts",
                            "description": "TS basics",
                            "author": "up",
                            "duration": "12:34",
                            "pubdate": 1781568000,
                        }
                    ]
                }
            },
            request=request,
        )

    monkeypatch.setattr(
        web_tools.httpx,
        "Client",
        lambda **_: ORIGINAL_HTTPX_CLIENT(transport=httpx.MockTransport(fake_handler)),
    )

    result = WebSearchClient(provider="builtin", api_key="", cache_ttl_seconds=0).search(
        "推荐 B站 TypeScript 视频",
        top_k=3,
    )

    assert result.ok is True
    assert result.provider == "builtin"
    assert result.results == [
        {
            "title": "TypeScript 入门",
            "url": "https://www.bilibili.com/video/BV1ts",
            "snippet": "up | 12:34 | TS basics",
            "publishedDate": "2026-06-16",
        }
    ]
    assert "api.bilibili.com" in captured["url"]
    assert captured["params"]["search_type"] == "video"
    assert captured["params"]["keyword"] == "推荐 TypeScript 视频"


def test_web_search_builtin_generic_query_reports_no_local_index() -> None:
    result = WebSearchClient(provider="builtin", api_key="", cache_ttl_seconds=0).search("TypeScript docs")

    assert result.ok is False
    assert result.results == []
    assert result.provider == "builtin"
    assert "no local full-web index" in result.reason


def test_web_search_tavily_success(monkeypatch) -> None:
    captured = {}

    def fake_handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        captured["payload"] = request.read()
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "TypeScript Handbook",
                        "url": "https://www.typescriptlang.org/docs/",
                        "content": "Official TypeScript docs",
                        "published_date": "2026-06-01",
                    }
                ]
            },
            request=request,
        )

    monkeypatch.setattr(
        web_tools.httpx,
        "Client",
        lambda **_: ORIGINAL_HTTPX_CLIENT(transport=httpx.MockTransport(fake_handler)),
    )

    result = WebSearchClient(provider="tavily", api_key="tavily-key", cache_ttl_seconds=0).search(
        "TypeScript docs",
        top_k=3,
    )

    assert result.ok is True
    assert result.provider == "tavily"
    assert result.results == [
        {
            "title": "TypeScript Handbook",
            "url": "https://www.typescriptlang.org/docs/",
            "snippet": "Official TypeScript docs",
            "publishedDate": "2026-06-01",
        }
    ]
    assert captured["headers"]["authorization"] == "Bearer tavily-key"
    assert b'"query":"TypeScript docs"' in captured["payload"]
    assert b'"max_results":3' in captured["payload"]


def test_web_search_tavily_reports_missing_api_key() -> None:
    result = WebSearchClient(provider="tavily", api_key="", cache_ttl_seconds=0).search("TypeScript")

    assert result.ok is False
    assert result.results == []
    assert "TAVILY_API_KEY" in result.reason


def test_web_search_tavily_reports_http_error(monkeypatch) -> None:
    monkeypatch.setattr(
        web_tools.httpx,
        "Client",
        lambda **_: ORIGINAL_HTTPX_CLIENT(
            transport=httpx.MockTransport(lambda request: httpx.Response(429, request=request))
        ),
    )

    result = WebSearchClient(provider="tavily", api_key="key", cache_ttl_seconds=0).search("quota")

    assert result.ok is False
    assert result.results == []
    assert result.reason


def test_web_search_brave_success(monkeypatch) -> None:
    captured = {}

    def fake_handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        captured["params"] = dict(request.url.params)
        return httpx.Response(
            200,
            json={
                "web": {
                    "results": [
                        {
                            "title": "TypeScript Handbook",
                            "url": "https://www.typescriptlang.org/docs/",
                            "description": "Official TypeScript docs",
                            "age": "2026-06-01",
                        }
                    ]
                }
            },
            request=request,
        )

    monkeypatch.setattr(
        web_tools.httpx,
        "Client",
        lambda **_: ORIGINAL_HTTPX_CLIENT(transport=httpx.MockTransport(fake_handler)),
    )

    result = WebSearchClient(provider="brave", api_key="brave-key", cache_ttl_seconds=0).search(
        "TypeScript docs",
        top_k=3,
    )

    assert result.ok is True
    assert result.results == [
        {
            "title": "TypeScript Handbook",
            "url": "https://www.typescriptlang.org/docs/",
            "snippet": "Official TypeScript docs",
            "publishedDate": "2026-06-01",
        }
    ]
    assert captured["headers"]["x-subscription-token"] == "brave-key"
    assert captured["params"]["q"] == "TypeScript docs"
    assert captured["params"]["count"] == "3"


def test_web_search_brave_reports_missing_api_key() -> None:
    result = WebSearchClient(provider="brave", api_key="", cache_ttl_seconds=0).search("TypeScript")

    assert result.ok is False
    assert result.results == []
    assert "WEB_SEARCH_API_KEY" in result.reason


def test_web_search_brave_reports_http_error(monkeypatch) -> None:
    monkeypatch.setattr(
        web_tools.httpx,
        "Client",
        lambda **_: ORIGINAL_HTTPX_CLIENT(
            transport=httpx.MockTransport(lambda request: httpx.Response(429, request=request))
        ),
    )

    result = WebSearchClient(provider="brave", api_key="key", cache_ttl_seconds=0).search("quota")

    assert result.ok is False
    assert result.results == []
    assert result.reason


def test_web_search_brave_reports_timeout(monkeypatch) -> None:
    def fake_handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timeout")

    monkeypatch.setattr(
        web_tools.httpx,
        "Client",
        lambda **_: ORIGINAL_HTTPX_CLIENT(transport=httpx.MockTransport(fake_handler)),
    )

    result = WebSearchClient(provider="brave", api_key="key", cache_ttl_seconds=0).search("slow")

    assert result.ok is False
    assert result.reason


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://10.0.0.5",
        "http://169.254.169.254/latest/meta-data",
    ],
)
def test_web_fetch_rejects_unsafe_urls(url: str) -> None:
    assert validate_public_http_url(url)


def test_web_fetch_returns_text(monkeypatch) -> None:
    monkeypatch.setattr(web_tools, "validate_public_http_url", lambda _url: "")

    def fake_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text="<html><head><title>Doc</title></head><body><h1>Hello</h1><p>World</p></body></html>",
            request=request,
        )

    monkeypatch.setattr(
        web_tools.httpx,
        "Client",
        lambda **_: ORIGINAL_HTTPX_CLIENT(transport=httpx.MockTransport(fake_handler)),
    )

    result = WebFetchClient(cache_ttl_seconds=0).fetch("https://example.com/doc")

    assert result.ok is True
    assert result.title == "Doc"
    assert "Hello World" in result.text_excerpt


def test_web_fetch_rejects_large_response(monkeypatch) -> None:
    monkeypatch.setattr(web_tools, "validate_public_http_url", lambda _url: "")
    body = "x" * 32

    def fake_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            text=body,
            request=request,
        )

    monkeypatch.setattr(
        web_tools.httpx,
        "Client",
        lambda **_: ORIGINAL_HTTPX_CLIENT(transport=httpx.MockTransport(fake_handler)),
    )

    result = WebFetchClient(max_bytes=8, cache_ttl_seconds=0).fetch("https://example.com/large")

    assert result.ok is False
    assert result.reason


def test_web_fetch_rejects_unsafe_redirect_target(monkeypatch) -> None:
    requested_urls = []

    def fake_handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(
            302,
            headers={"location": "http://127.0.0.1:8000/private"},
            request=request,
        )

    monkeypatch.setattr(
        web_tools.httpx,
        "Client",
        lambda **kwargs: ORIGINAL_HTTPX_CLIENT(
            follow_redirects=kwargs.get("follow_redirects", False),
            transport=httpx.MockTransport(fake_handler),
        ),
    )

    result = WebFetchClient(cache_ttl_seconds=0).fetch("https://example.com/redirect")

    assert result.ok is False
    assert "127.0.0.1" in result.url
    assert requested_urls == ["https://example.com/redirect"]


def test_web_fetch_follows_safe_redirect(monkeypatch) -> None:
    monkeypatch.setattr(web_tools, "validate_public_http_url", lambda _url: "")
    requested_urls = []

    def fake_handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        if str(request.url) == "https://example.com/redirect":
            return httpx.Response(
                302,
                headers={"location": "https://docs.example.com/final"},
                request=request,
            )
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            text="final page",
            request=request,
        )

    monkeypatch.setattr(
        web_tools.httpx,
        "Client",
        lambda **kwargs: ORIGINAL_HTTPX_CLIENT(
            follow_redirects=kwargs.get("follow_redirects", False),
            transport=httpx.MockTransport(fake_handler),
        ),
    )

    result = WebFetchClient(cache_ttl_seconds=0).fetch("https://example.com/redirect")

    assert result.ok is True
    assert result.url == "https://docs.example.com/final"
    assert result.text_excerpt == "final page"
    assert requested_urls == ["https://example.com/redirect", "https://docs.example.com/final"]
