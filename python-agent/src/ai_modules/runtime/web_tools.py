"""Opt-in web search and fetch tools for the Python agent runtime."""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from src.ai_modules.config import get_settings
from src.ai_modules.runtime.ttl_cache import InMemoryTTLCache, stable_cache_key

LOGGER = logging.getLogger(__name__)

_WEB_CACHE = InMemoryTTLCache(max_entries=256)
_WEB_SEARCH_CACHE_NAMESPACE = "web_search"
_WEB_FETCH_CACHE_NAMESPACE = "web_fetch"
_BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
_TAVILY_SEARCH_URL = "https://api.tavily.com/search"


@dataclass(slots=True)
class WebToolResult:
    ok: bool
    reason: str = ""
    results: list[dict[str, Any]] | None = None
    title: str = ""
    url: str = ""
    text_excerpt: str = ""
    provider: str = ""

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": self.ok,
            "reason": self.reason,
        }
        if self.provider:
            payload["provider"] = self.provider
        if self.results is not None:
            payload["results"] = self.results
        if self.title:
            payload["title"] = self.title
        if self.url:
            payload["url"] = self.url
        if self.text_excerpt:
            payload["textExcerpt"] = self.text_excerpt
        return payload


class WebSearchClient:
    """Opt-in public-web search client. Network access is never implicit."""

    def __init__(
        self,
        *,
        provider: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
        cache_ttl_seconds: int | None = None,
    ) -> None:
        settings = get_settings()
        self.provider = (provider if provider is not None else settings.web_search_provider).strip().lower()
        self.api_key = api_key if api_key is not None else settings.web_search_api_key
        self.base_url = (base_url if base_url is not None else settings.web_search_base_url).strip()
        self.timeout_seconds = timeout_seconds if timeout_seconds is not None else settings.web_search_timeout_seconds
        self.cache_ttl_seconds = (
            cache_ttl_seconds if cache_ttl_seconds is not None else settings.web_search_cache_ttl_seconds
        )

    def search(self, query: str, top_k: int = 5) -> WebToolResult:
        query = " ".join(str(query or "").split()).strip()
        top_k = max(1, min(int(top_k or 5), 10))
        if not query:
            return WebToolResult(ok=False, reason="web_search query is empty", results=[], provider=self.provider)

        cache_key = stable_cache_key(
            "web-search",
            {"provider": self.provider, "baseUrl": self.base_url, "query": query, "topK": top_k},
        )
        cached = _WEB_CACHE.get(cache_key, namespace=_WEB_SEARCH_CACHE_NAMESPACE)
        if isinstance(cached, dict):
            return WebToolResult(
                ok=bool(cached.get("ok")),
                reason=str(cached.get("reason") or ""),
                results=list(cached.get("results") or []),
                provider=str(cached.get("provider") or self.provider),
            )

        if self.provider in {"", "builtin", "internal", "direct"}:
            result = self._search_builtin(query=query, top_k=top_k)
        elif self.provider == "tavily":
            if str(self.api_key or "").strip():
                result = self._search_tavily(query=query, top_k=top_k)
            else:
                result = WebToolResult(
                    ok=False,
                    reason="web_search is not configured: WEB_SEARCH_API_KEY or TAVILY_API_KEY is missing",
                    results=[],
                    provider=self.provider,
                )
        elif self.provider == "brave":
            if str(self.api_key or "").strip():
                result = self._search_brave(query=query, top_k=top_k)
            else:
                result = WebToolResult(
                    ok=False,
                    reason="web_search is not configured: WEB_SEARCH_API_KEY is missing",
                    results=[],
                    provider=self.provider,
                )
        else:
            result = WebToolResult(
                ok=False,
                reason=f"unsupported web_search provider: {self.provider or 'unset'}",
                results=[],
                provider=self.provider,
            )

        if self.cache_ttl_seconds > 0:
            _WEB_CACHE.set(
                cache_key,
                result.as_dict(),
                ttl_seconds=self.cache_ttl_seconds,
                namespace=_WEB_SEARCH_CACHE_NAMESPACE,
            )
        return result

    async def asearch(self, query: str, top_k: int = 5) -> WebToolResult:
        return self.search(query=query, top_k=top_k)

    def _search_builtin(self, *, query: str, top_k: int) -> WebToolResult:
        direct_urls = _urls_from_query(query)
        if direct_urls:
            return WebToolResult(
                ok=True,
                reason="",
                results=[
                    {"title": url, "url": url, "snippet": "URL provided by the user query.", "publishedDate": ""}
                    for url in direct_urls[:top_k]
                ],
                provider="builtin",
            )
        if _is_bilibili_query(query):
            return self._search_bilibili(query=query, top_k=top_k)
        return WebToolResult(
            ok=False,
            reason=(
                "builtin web_search has no local full-web index for this query; "
                "provide a public URL, ask for a supported source such as Bilibili, "
                "or configure an explicit WEB_SEARCH_PROVIDER."
            ),
            results=[],
            provider="builtin",
        )

    def _search_bilibili(self, *, query: str, top_k: int) -> WebToolResult:
        endpoint = "https://api.bilibili.com/x/web-interface/search/type"
        search_query = _clean_bilibili_query(query) or query
        try:
            with httpx.Client(timeout=self.timeout_seconds, follow_redirects=True, max_redirects=3) as client:
                response = client.get(
                    endpoint,
                    headers={
                        "Accept": "application/json",
                        "Referer": "https://search.bilibili.com/",
                        "User-Agent": "zhixue-agent-web-search/1.0",
                    },
                    params={
                        "search_type": "video",
                        "keyword": search_query,
                        "page": 1,
                        "page_size": top_k,
                    },
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.TimeoutException:
            LOGGER.warning("Builtin Bilibili search timed out query=%r", query)
            return WebToolResult(ok=False, reason="builtin Bilibili search timed out", results=[], provider="builtin")
        except (httpx.HTTPError, ValueError) as exc:
            LOGGER.warning("Builtin Bilibili search failed query=%r error=%s", query, exc)
            return WebToolResult(ok=False, reason=f"builtin Bilibili search failed: {exc}", results=[], provider="builtin")

        raw_results = ((payload.get("data") or {}).get("result") if isinstance(payload, dict) else None)
        if not isinstance(raw_results, list):
            return WebToolResult(ok=True, reason="", results=[], provider="builtin")

        results: list[dict[str, Any]] = []
        for item in raw_results[:top_k]:
            if not isinstance(item, dict):
                continue
            url = _normalize_bilibili_url(item)
            title = _clean_text(_strip_tags(str(item.get("title") or ""))) or url
            if not _looks_like_public_http_url(url) or not title:
                continue
            author = _clean_text(item.get("author"))
            duration = _clean_text(item.get("duration"))
            description = _clean_text(_strip_tags(str(item.get("description") or "")))
            snippet_parts = [part for part in [author, duration, description] if part]
            results.append(
                {
                    "title": title,
                    "url": url,
                    "snippet": " | ".join(snippet_parts),
                    "publishedDate": _format_timestamp(item.get("pubdate")),
                }
            )
        return WebToolResult(ok=True, reason="", results=results, provider="builtin")

    def _search_tavily(self, *, query: str, top_k: int) -> WebToolResult:
        endpoint = self.base_url or _TAVILY_SEARCH_URL
        payload = {
            "query": query,
            "search_depth": "basic",
            "max_results": top_k,
            "include_answer": False,
            "include_raw_content": False,
        }
        try:
            with httpx.Client(timeout=self.timeout_seconds, follow_redirects=True, max_redirects=3) as client:
                response = client.post(
                    endpoint,
                    headers={
                        "Accept": "application/json",
                        "Authorization": f"Bearer {str(self.api_key).strip()}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.TimeoutException:
            LOGGER.warning("Web search timed out provider=%s query=%r", self.provider, query)
            return WebToolResult(ok=False, reason="web_search timed out", results=[], provider=self.provider)
        except (httpx.HTTPError, ValueError) as exc:
            LOGGER.warning("Web search failed provider=%s query=%r error=%s", self.provider, query, exc)
            return WebToolResult(ok=False, reason=f"web_search failed: {exc}", results=[], provider=self.provider)

        raw_results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(raw_results, list):
            return WebToolResult(ok=True, reason="", results=[], provider=self.provider)

        results: list[dict[str, Any]] = []
        for rank, item in enumerate(raw_results[:top_k], start=1):
            if not isinstance(item, dict):
                continue
            url = _clean_text(item.get("url"))
            title = _clean_text(item.get("title")) or url
            if not _looks_like_public_http_url(url) or not title:
                continue
            snippet = _clean_text(item.get("content") or item.get("snippet") or item.get("raw_content"))
            results.append(
                {
                    "title": title,
                    "url": url,
                    "snippet": snippet,
                    "publishedDate": _clean_text(item.get("published_date") or item.get("publishedDate")),
                }
            )
        return WebToolResult(ok=True, reason="", results=results, provider=self.provider)

    def _search_brave(self, *, query: str, top_k: int) -> WebToolResult:
        endpoint = self.base_url or _BRAVE_SEARCH_URL
        try:
            with httpx.Client(timeout=self.timeout_seconds, follow_redirects=True, max_redirects=3) as client:
                response = client.get(
                    endpoint,
                    headers={
                        "Accept": "application/json",
                        "X-Subscription-Token": str(self.api_key).strip(),
                    },
                    params={
                        "q": query,
                        "count": top_k,
                        "text_decorations": "false",
                    },
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.TimeoutException:
            LOGGER.warning("Web search timed out provider=%s query=%r", self.provider, query)
            return WebToolResult(ok=False, reason="web_search timed out", results=[], provider=self.provider)
        except (httpx.HTTPError, ValueError) as exc:
            LOGGER.warning("Web search failed provider=%s query=%r error=%s", self.provider, query, exc)
            return WebToolResult(ok=False, reason=f"web_search failed: {exc}", results=[], provider=self.provider)

        raw_results = ((payload.get("web") or {}).get("results") if isinstance(payload, dict) else None)
        if not isinstance(raw_results, list):
            return WebToolResult(ok=True, reason="", results=[], provider=self.provider)

        results: list[dict[str, Any]] = []
        for item in raw_results[:top_k]:
            if not isinstance(item, dict):
                continue
            url = _clean_text(item.get("url"))
            title = _clean_text(item.get("title")) or url
            if not _looks_like_public_http_url(url) or not title:
                continue
            results.append(
                {
                    "title": title,
                    "url": url,
                    "snippet": _clean_text(item.get("description") or item.get("snippet")),
                    "publishedDate": _clean_text(item.get("age") or item.get("page_age")),
                }
            )
        return WebToolResult(ok=True, reason="", results=results, provider=self.provider)


class WebFetchClient:
    """Fetch a public HTTP(S) page with SSRF and size boundaries."""

    def __init__(
        self,
        *,
        timeout_seconds: float | None = None,
        max_bytes: int | None = None,
        cache_ttl_seconds: int | None = None,
    ) -> None:
        settings = get_settings()
        self.timeout_seconds = timeout_seconds if timeout_seconds is not None else settings.web_fetch_timeout_seconds
        self.max_bytes = max_bytes if max_bytes is not None else settings.web_fetch_max_bytes
        self.cache_ttl_seconds = (
            cache_ttl_seconds if cache_ttl_seconds is not None else settings.web_search_cache_ttl_seconds
        )

    def fetch(self, url: str, redirects_remaining: int = 3) -> WebToolResult:
        url = str(url or "").strip()
        reason = validate_public_http_url(url)
        if reason:
            return WebToolResult(ok=False, reason=reason, url=url)

        cache_key = stable_cache_key("web-fetch", {"url": url, "maxBytes": self.max_bytes})
        cached = _WEB_CACHE.get(cache_key, namespace=_WEB_FETCH_CACHE_NAMESPACE)
        if isinstance(cached, dict):
            return WebToolResult(
                ok=bool(cached.get("ok")),
                reason=str(cached.get("reason") or ""),
                title=str(cached.get("title") or ""),
                url=str(cached.get("url") or url),
                text_excerpt=str(cached.get("textExcerpt") or ""),
            )

        try:
            with httpx.Client(timeout=self.timeout_seconds, follow_redirects=False) as client:
                with client.stream("GET", url, headers={"User-Agent": "zhixue-agent-web-fetch/1.0"}) as response:
                    final_url = str(response.url)
                    redirect_reason = validate_public_http_url(final_url)
                    if redirect_reason:
                        return WebToolResult(ok=False, reason=redirect_reason, url=final_url)
                    if 300 <= response.status_code < 400:
                        location = response.headers.get("location")
                        if not location:
                            return WebToolResult(ok=False, reason="web_fetch redirect missing location", url=final_url)
                        if redirects_remaining <= 0:
                            return WebToolResult(ok=False, reason="web_fetch redirect limit exceeded", url=final_url)
                        next_url = urljoin(final_url, location)
                        redirect_reason = validate_public_http_url(next_url)
                        if redirect_reason:
                            return WebToolResult(ok=False, reason=redirect_reason, url=next_url)
                        return self.fetch(next_url, redirects_remaining=redirects_remaining - 1)
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "")
                    if "text/html" not in content_type and "text/plain" not in content_type:
                        return WebToolResult(ok=False, reason="web_fetch supports only text/html or text/plain", url=final_url)
                    chunks: list[bytes] = []
                    total = 0
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > self.max_bytes:
                            return WebToolResult(ok=False, reason="web_fetch response body too large", url=final_url)
                        chunks.append(chunk)
                    raw = b"".join(chunks)
                    encoding = response.encoding or "utf-8"
        except httpx.TimeoutException:
            return WebToolResult(ok=False, reason="web_fetch timed out", url=url)
        except httpx.HTTPError as exc:
            return WebToolResult(ok=False, reason=f"web_fetch failed: {exc}", url=url)

        decoded = raw.decode(encoding, errors="replace")
        title, text = _html_to_text(decoded)
        result = WebToolResult(ok=True, reason="", title=title, url=final_url, text_excerpt=text[:4000])
        if self.cache_ttl_seconds > 0:
            _WEB_CACHE.set(
                cache_key,
                result.as_dict(),
                ttl_seconds=self.cache_ttl_seconds,
                namespace=_WEB_FETCH_CACHE_NAMESPACE,
            )
        return result

    async def afetch(self, url: str) -> WebToolResult:
        return self.fetch(url)


class WebSearchChannel:
    """RRF-compatible wrapper for opt-in web search results."""

    def __init__(self, client: WebSearchClient | None = None) -> None:
        self.client = client or WebSearchClient()
        self.last_status: dict[str, Any] = {}

    def search(self, query: str, top_k: int = 5) -> list[tuple]:
        result = self.client.search(query=query, top_k=top_k)
        self.last_status = result.as_dict()
        if not result.ok:
            return []
        rows: list[tuple] = []
        for rank, item in enumerate(result.results or [], start=1):
            url = str(item.get("url") or "").strip()
            title = str(item.get("title") or url).strip()
            if not url or not title:
                continue
            rows.append(
                (
                    url,
                    title,
                    round(1.0 / rank, 4),
                    {
                        "url": url,
                        "snippet": str(item.get("snippet") or "").strip(),
                        "sourceTitle": title,
                        "publishedDate": str(item.get("publishedDate") or "").strip(),
                    },
                )
            )
        return rows


def web_search(query: str, top_k: int = 5) -> dict[str, Any]:
    return WebSearchClient().search(query=query, top_k=top_k).as_dict()


def web_fetch(url: str) -> dict[str, Any]:
    return WebFetchClient().fetch(url=url).as_dict()


def validate_public_http_url(url: str) -> str:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in {"http", "https"}:
        return "web_fetch only allows http/https URLs"
    if not parsed.hostname:
        return "web_fetch URL is missing host"
    hostname = parsed.hostname.strip().lower()
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".localhost"):
        return "web_fetch blocks localhost"
    try:
        ip = ipaddress.ip_address(hostname)
        if _is_blocked_ip(ip):
            return "web_fetch blocks private or metadata addresses"
        return ""
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except socket.gaierror:
        return "web_fetch cannot resolve URL host"
    for info in infos:
        address = info[4][0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return "web_fetch host resolved to an invalid address"
        if _is_blocked_ip(ip):
            return "web_fetch blocks private or metadata addresses"
    return ""


def _looks_like_public_http_url(url: str) -> bool:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    host = parsed.hostname.strip().lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True
    return not _is_blocked_ip(ip)


def _is_blocked_ip(ip: ipaddress._BaseAddress) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or ip == ipaddress.ip_address("169.254.169.254")
    )


def _is_bilibili_query(query: str) -> bool:
    lowered = query.lower()
    return any(term in lowered for term in ("b站", "b 站", "bilibili", "哔哩哔哩", "site:bilibili.com"))


def _clean_bilibili_query(query: str) -> str:
    cleaned = re.sub(r"\bsite\s*:\s*(?:www\.)?bilibili\.com\b", " ", query, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:bilibili|www\.bilibili\.com)\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.replace("B站", " ").replace("b站", " ").replace("B 站", " ").replace("b 站", " ")
    cleaned = cleaned.replace("哔哩哔哩", " ")
    return " ".join(cleaned.split()).strip()


def _normalize_bilibili_url(item: dict[str, Any]) -> str:
    value = str(item.get("arcurl") or item.get("url") or "").strip()
    if value.startswith("//"):
        value = f"https:{value}"
    if value.startswith("http://"):
        value = f"https://{value.removeprefix('http://')}"
    if value:
        return value
    bvid = str(item.get("bvid") or "").strip()
    if bvid:
        return f"https://www.bilibili.com/video/{bvid}"
    aid = str(item.get("aid") or "").strip()
    if aid:
        return f"https://www.bilibili.com/video/av{aid}"
    return ""


def _format_timestamp(value: Any) -> str:
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return ""
    if timestamp <= 0:
        return ""
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).date().isoformat()


def _urls_from_query(query: str) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r"https?://[^\s<>()\"']+", query):
        url = match.group(0).rstrip(".,;!?，。；！？）)]")
        if not _looks_like_public_http_url(url):
            continue
        dedupe_key = url.lower()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        urls.append(url)
    return urls


def _clean_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return unescape(re.sub(r"\s+", " ", value)).strip()


def _html_to_text(raw: str) -> tuple[str, str]:
    title_match = re.search(r"<title[^>]*>(.*?)</title>", raw, flags=re.IGNORECASE | re.DOTALL)
    title = _clean_text(_strip_tags(title_match.group(1))) if title_match else ""
    body = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", raw)
    body = re.sub(r"(?is)<br\s*/?>", "\n", body)
    body = re.sub(r"(?is)</(p|div|li|h[1-6]|section|article)>", "\n", body)
    text = _clean_text(_strip_tags(body))
    return title, text


def _strip_tags(value: str) -> str:
    return re.sub(r"(?s)<[^>]+>", " ", value)
