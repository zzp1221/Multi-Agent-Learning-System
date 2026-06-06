"""Import verified external resources and optional RAG chunks.

This script is intentionally conservative:
- it inserts only URLs that pass a live HTTP accessibility check;
- it never writes fake embeddings;
- when no embedding key is configured, metadata import can continue while RAG is skipped;
- use --require-embeddings to fail instead of skipping RAG.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urldefrag, urljoin

import httpx

PYTHON_AGENT_ROOT = Path(__file__).resolve().parents[1]
if str(PYTHON_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_AGENT_ROOT))

from src.ai_modules.config import get_settings


ALLOWED_RESOURCE_TYPES = {
    "DOCUMENT",
    "PPT",
    "QUIZ",
    "VIDEO",
    "AUDIO",
    "IMAGE",
    "CODE",
    "MINDMAP",
    "READING",
    "PRACTICE",
}
ALLOWED_DIFFICULTIES = {"BASIC", "INTERMEDIATE", "ADVANCED", "MIXED"}
DEFAULT_SOURCE_FILE = Path(__file__).resolve().parent / "resource_sources" / "external_resource_sources.json"
IMPORT_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "zhixue-ai-resource-library")


@dataclass(frozen=True)
class ResourceCandidate:
    source_id: str
    source_name: str
    url: str
    title: str | None
    domain: str
    resource_type: str
    display_type: str
    difficulty: str
    license: str
    copyright_status: str
    tags: tuple[str, ...]
    quality_score: float
    popularity_score: float
    cs_category: str = "GENERAL_CS"
    cs_subcategory: str = "General"


@dataclass(frozen=True)
class AccessResult:
    original_url: str
    final_url: str
    status_code: int
    content_type: str
    checked_at: str
    body: bytes = b""
    error: str = ""

    @property
    def accessible(self) -> bool:
        return not self.error and 200 <= self.status_code < 400


@dataclass(frozen=True)
class ParsedDocument:
    title: str
    text: str
    summary: str
    content_hash: str


@dataclass
class ImportStats:
    discovered: int = 0
    accessible: int = 0
    inserted_metadata: int = 0
    rag_ingested: int = 0
    skipped_inaccessible: int = 0
    skipped_rag: int = 0
    failed: int = 0
    source_counts: dict[str, int] = field(default_factory=dict)
    category_counts: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CandidateContent:
    candidate: ResourceCandidate
    access: AccessResult
    parsed: ParsedDocument | None = None
    error: str = ""


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._in_title = False
        self._in_h1 = False
        self.title_parts: list[str] = []
        self.h1_parts: list[str] = []
        self.text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        normalized = tag.lower()
        if normalized in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1
        elif normalized == "title":
            self._in_title = True
        elif normalized == "h1":
            self._in_h1 = True
        elif normalized in {"p", "li", "br", "div", "section", "article", "h2", "h3", "pre", "code"}:
            self.text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        if normalized in {"script", "style", "noscript", "svg"} and self._skip_depth:
            self._skip_depth -= 1
        elif normalized == "title":
            self._in_title = False
        elif normalized == "h1":
            self._in_h1 = False

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if not text or self._skip_depth:
            return
        if self._in_title:
            self.title_parts.append(text)
        if self._in_h1:
            self.h1_parts.append(text)
        self.text_parts.append(text)


class _HTMLLinkExtractor(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.links: list[tuple[str, str]] = []
        self._current_href: str | None = None
        self._current_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        href = next((value for name, value in attrs if name.lower() == "href" and value), None)
        if not href:
            return
        self._current_href = urldefrag(urljoin(self.base_url, href.strip()))[0]
        self._current_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or not self._current_href:
            return
        title = _normalize_text(" ".join(self._current_text))[:240]
        self.links.append((self._current_href, title))
        self._current_href = None
        self._current_text = []

    def handle_data(self, data: str) -> None:
        if self._current_href:
            self._current_text.append(data)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_text(text: str) -> str:
    compact_lines = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        normalized = re.sub(r"\s+", " ", line).strip()
        if normalized:
            compact_lines.append(normalized)
    return "\n".join(compact_lines)


def _unsupported_content_error(content_type: str, body: bytes) -> str:
    if b"\x00" in body:
        return "BINARY_CONTENT"
    if not content_type:
        return ""
    if content_type.startswith("text/"):
        return ""
    if content_type in {"application/xhtml+xml", "application/xml", "application/json", "application/ld+json"}:
        return ""
    return f"UNSUPPORTED_CONTENT_TYPE {content_type}"


def parse_html_document(html: bytes | str, fallback_title: str | None = None) -> ParsedDocument:
    raw = html.decode("utf-8", errors="ignore") if isinstance(html, bytes) else html
    parser = _HTMLTextExtractor()
    parser.feed(raw)
    title = _normalize_text(" ".join(parser.h1_parts) or " ".join(parser.title_parts) or fallback_title or "Untitled resource")
    text = _normalize_text("\n".join(parser.text_parts))
    summary = text[:420]
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return ParsedDocument(title=title[:240], text=text, summary=summary, content_hash=content_hash)


def estimate_tokens(text: str) -> int:
    cjk = sum(1 for char in text if ord(char) >= 0x2E80)
    other = len(text) - cjk
    return max(1, int(cjk / 1.5 + other / 4))


def chunk_text(text: str, *, target_chars: int = 2600, overlap_chars: int = 320, min_chars: int = 260) -> list[str]:
    paragraphs = [part.strip() for part in text.split("\n") if part.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(current) + len(paragraph) + 1 <= target_chars:
            current = f"{current}\n{paragraph}".strip()
            continue
        if len(current) >= min_chars:
            chunks.append(current)
        carry = current[-overlap_chars:] if overlap_chars > 0 else ""
        current = f"{carry}\n{paragraph}".strip()
    if len(current) >= min_chars:
        chunks.append(current)
    if not chunks and len(text.strip()) >= min_chars:
        chunks.append(text.strip()[:target_chars])
    return chunks


def _strip_xml_namespace(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _decode_sitemap_body(url: str, body: bytes) -> str:
    if url.endswith(".gz"):
        try:
            body = gzip.decompress(body)
        except OSError:
            pass
    return body.decode("utf-8", errors="ignore")


def parse_sitemap_locations(url: str, body: bytes) -> tuple[str, list[str]]:
    xml_text = _decode_sitemap_body(url, body)
    root = ET.fromstring(xml_text)
    root_type = _strip_xml_namespace(root.tag)
    locations: list[str] = []
    for element in root.iter():
        if _strip_xml_namespace(element.tag) == "loc" and element.text:
            locations.append(element.text.strip())
    return root_type, locations


def fetch_sitemap_urls(
    client: httpx.Client,
    sitemap_url: str,
    *,
    max_sitemaps: int = 8,
    _seen: set[str] | None = None,
) -> list[str]:
    seen = _seen or set()
    if sitemap_url in seen or len(seen) >= max_sitemaps:
        return []
    seen.add(sitemap_url)
    response = client.get(sitemap_url)
    response.raise_for_status()
    sitemap_type, locations = parse_sitemap_locations(str(response.url), response.content)
    if sitemap_type == "sitemapindex":
        urls: list[str] = []
        for child_url in locations:
            urls.extend(fetch_sitemap_urls(client, child_url, max_sitemaps=max_sitemaps, _seen=seen))
        return urls
    return locations


def _compile_patterns(patterns: Iterable[str]) -> list[re.Pattern[str]]:
    return [re.compile(pattern, re.IGNORECASE) for pattern in patterns]


def _matches_any(patterns: list[re.Pattern[str]], value: str) -> bool:
    return any(pattern.search(value) for pattern in patterns)


def _as_tags(*tag_groups: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    tags: list[str] = []
    for group in tag_groups:
        for raw_tag in group:
            tag = str(raw_tag).strip().lower()
            if tag and tag not in seen:
                seen.add(tag)
                tags.append(tag)
    return tuple(tags)


def _source_value(source: dict[str, Any], defaults: dict[str, Any], key: str, fallback: Any = None) -> Any:
    return source.get(key, defaults.get(key, fallback))


def _normalize_enum(value: str, allowed: set[str], fallback: str) -> str:
    normalized = str(value or fallback).strip().upper()
    return normalized if normalized in allowed else fallback


def _candidate_from_source(
    source: dict[str, Any],
    defaults: dict[str, Any],
    url: str,
    resource: dict[str, Any] | None = None,
) -> ResourceCandidate:
    resource = resource or {}
    source_tags = _source_value(source, defaults, "tags", [])
    default_tags = defaults.get("tags", [])
    return ResourceCandidate(
        source_id=str(source["id"]),
        source_name=str(source.get("sourceName") or source["id"]),
        url=url,
        title=resource.get("title"),
        domain=str(resource.get("domain") or _source_value(source, defaults, "domain", "COMPUTER_SCIENCE")),
        resource_type=_normalize_enum(
            str(resource.get("resourceType") or _source_value(source, defaults, "resourceType", "READING")),
            ALLOWED_RESOURCE_TYPES,
            "READING",
        ),
        display_type=str(resource.get("displayType") or _source_value(source, defaults, "displayType", "DOCUMENT")).strip().upper(),
        difficulty=_normalize_enum(
            str(resource.get("difficulty") or _source_value(source, defaults, "difficulty", "MIXED")),
            ALLOWED_DIFFICULTIES,
            "MIXED",
        ),
        license=str(resource.get("license") or _source_value(source, defaults, "license", "")),
        copyright_status=str(
            resource.get("copyrightStatus")
            or _source_value(source, defaults, "copyrightStatus", "OFFICIAL_PUBLIC_DOCUMENTATION")
        ),
        tags=_as_tags(default_tags, source_tags, resource.get("tags", [])),
        quality_score=float(resource.get("qualityScore") or _source_value(source, defaults, "qualityScore", 0.82)),
        popularity_score=float(resource.get("popularityScore") or _source_value(source, defaults, "popularityScore", 0.5)),
        cs_category=str(resource.get("csCategory") or _source_value(source, defaults, "csCategory", "GENERAL_CS")).strip().upper(),
        cs_subcategory=str(resource.get("csSubcategory") or _source_value(source, defaults, "csSubcategory", "General")).strip(),
    )


def fetch_index_links(client: httpx.Client, source: dict[str, Any]) -> list[dict[str, str]]:
    response = client.get(str(source["indexUrl"]))
    response.raise_for_status()
    parser = _HTMLLinkExtractor(str(response.url))
    parser.feed(response.text)
    include = _compile_patterns(source.get("includeRegex", []))
    exclude = _compile_patterns(source.get("excludeRegex", []))
    links: list[dict[str, str]] = []
    seen: set[str] = set()
    for url, title in parser.links:
        if not url.startswith(("http://", "https://")):
            continue
        if include and not _matches_any(include, url):
            continue
        if exclude and _matches_any(exclude, url):
            continue
        if url in seen:
            continue
        seen.add(url)
        links.append({"url": url, "title": title})
    return links


def expand_url_template_resources(source: dict[str, Any]) -> list[dict[str, str]]:
    resources: list[dict[str, str]] = []
    for template in source.get("urlTemplates", []):
        item = dict(template)
        start = int(item.get("start", 1))
        end = int(item.get("end", start))
        step = int(item.get("step", 1))
        if step <= 0 or end < start:
            continue
        url_template = str(item["urlTemplate"])
        title_template = str(item.get("titleTemplate") or url_template)
        for number in range(start, end + 1, step):
            resources.append({
                "url": url_template.format(n=number),
                "title": title_template.format(n=number),
            })
    return resources


def _round_robin(source_candidates: list[list[ResourceCandidate]]) -> list[ResourceCandidate]:
    candidates: list[ResourceCandidate] = []
    seen_urls: set[str] = set()
    max_source_len = max((len(items) for items in source_candidates), default=0)
    for index in range(max_source_len):
        for items in source_candidates:
            if index >= len(items):
                continue
            candidate = items[index]
            if candidate.url in seen_urls:
                continue
            seen_urls.add(candidate.url)
            candidates.append(candidate)
    return candidates


def _source_selection_quotas(config: dict[str, Any]) -> dict[str, int]:
    quotas: dict[str, int] = {}
    for source in config.get("sources", []):
        quota = source.get("quota")
        if quota is not None:
            quotas[str(source["id"])] = max(0, int(quota))
    return quotas


def _append_candidate(
    selected: list[ResourceCandidate],
    candidate: ResourceCandidate,
    *,
    limit: int | None,
    seen_urls: set[str],
    source_counts: dict[str, int],
    category_counts: dict[str, int],
    source_quotas: dict[str, int],
) -> bool:
    if limit is not None and len(selected) >= limit:
        return False
    if candidate.url in seen_urls:
        return False
    source_quota = source_quotas.get(candidate.source_id)
    if source_quota is not None and source_counts.get(candidate.source_id, 0) >= source_quota:
        return False
    seen_urls.add(candidate.url)
    selected.append(candidate)
    source_counts[candidate.source_id] = source_counts.get(candidate.source_id, 0) + 1
    category_counts[candidate.cs_category] = category_counts.get(candidate.cs_category, 0) + 1
    return True


def _select_with_category_quotas(
    config: dict[str, Any],
    ordered_candidates: list[ResourceCandidate],
    *,
    limit: int | None,
) -> list[ResourceCandidate]:
    category_quotas = {
        str(category).strip().upper(): max(0, int(quota))
        for category, quota in config.get("categoryQuotas", {}).items()
    }
    if not category_quotas:
        return ordered_candidates[:limit] if limit is not None else ordered_candidates
    if str(config.get("selectionMode", "")).strip().lower() in {"categoryroundrobin", "category_round_robin", "balanced"}:
        return _select_category_round_robin(config, ordered_candidates, category_quotas, limit=limit)

    selected: list[ResourceCandidate] = []
    seen_urls: set[str] = set()
    source_counts: dict[str, int] = {}
    category_counts: dict[str, int] = {}
    source_quotas = _source_selection_quotas(config)
    candidates_by_category: dict[str, list[ResourceCandidate]] = {}
    for candidate in ordered_candidates:
        candidates_by_category.setdefault(candidate.cs_category, []).append(candidate)

    for category, quota in category_quotas.items():
        for candidate in candidates_by_category.get(category, []):
            if category_counts.get(category, 0) >= quota:
                break
            _append_candidate(
                selected,
                candidate,
                limit=limit,
                seen_urls=seen_urls,
                source_counts=source_counts,
                category_counts=category_counts,
                source_quotas=source_quotas,
            )
            if limit is not None and len(selected) >= limit:
                return selected

    while limit is None or len(selected) < limit:
        added = False
        for category in sorted(candidates_by_category):
            for candidate in candidates_by_category[category]:
                if _append_candidate(
                    selected,
                    candidate,
                    limit=limit,
                    seen_urls=seen_urls,
                    source_counts=source_counts,
                    category_counts=category_counts,
                    source_quotas=source_quotas,
                ):
                    added = True
                    break
            if limit is not None and len(selected) >= limit:
                return selected
        if not added:
            return selected
    return selected


def _select_category_round_robin(
    config: dict[str, Any],
    ordered_candidates: list[ResourceCandidate],
    category_quotas: dict[str, int],
    *,
    limit: int | None,
) -> list[ResourceCandidate]:
    selected: list[ResourceCandidate] = []
    seen_urls: set[str] = set()
    source_counts: dict[str, int] = {}
    category_counts: dict[str, int] = {}
    source_quotas = _source_selection_quotas(config)
    candidates_by_category: dict[str, list[ResourceCandidate]] = {}
    category_order: list[str] = []
    for category in category_quotas:
        category_order.append(category)
    for candidate in ordered_candidates:
        candidates_by_category.setdefault(candidate.cs_category, []).append(candidate)
        if candidate.cs_category not in category_order:
            category_order.append(candidate.cs_category)

    indices = {category: 0 for category in category_order}
    while limit is None or len(selected) < limit:
        added = False
        for category in category_order:
            quota = category_quotas.get(category)
            if quota is not None and category_counts.get(category, 0) >= quota:
                continue
            items = candidates_by_category.get(category, [])
            while indices.get(category, 0) < len(items):
                index = indices[category]
                indices[category] = index + 1
                if _append_candidate(
                    selected,
                    items[index],
                    limit=limit,
                    seen_urls=seen_urls,
                    source_counts=source_counts,
                    category_counts=category_counts,
                    source_quotas=source_quotas,
                ):
                    added = True
                    break
            if limit is not None and len(selected) >= limit:
                return selected
        if not added:
            return selected
    return selected


def iter_candidates(config: dict[str, Any], client: httpx.Client, *, limit: int | None = None) -> list[ResourceCandidate]:
    defaults = config.get("defaults", {})
    candidates_by_source: list[list[ResourceCandidate]] = []
    for source in config.get("sources", []):
        source_type = str(source.get("sourceType", "urls")).lower()
        source_limit = int(source.get("maxItems") or limit or 100000)
        source_candidates: list[ResourceCandidate] = []
        try:
            if source_type == "sitemap":
                include = _compile_patterns(source.get("includeRegex", []))
                exclude = _compile_patterns(source.get("excludeRegex", []))
                urls = fetch_sitemap_urls(
                    client,
                    str(source["sitemapUrl"]),
                    max_sitemaps=int(source.get("maxSitemaps", 8)),
                )
                for url in urls:
                    if include and not _matches_any(include, url):
                        continue
                    if exclude and _matches_any(exclude, url):
                        continue
                    source_candidates.append(_candidate_from_source(source, defaults, url))
                    if len(source_candidates) >= source_limit:
                        break
            elif source_type == "index":
                for item in fetch_index_links(client, source):
                    source_candidates.append(_candidate_from_source(source, defaults, item["url"], item))
                    if len(source_candidates) >= source_limit:
                        break
            else:
                for item in [*source.get("resources", []), *expand_url_template_resources(source)]:
                    resource = {"url": item} if isinstance(item, str) else dict(item)
                    source_candidates.append(_candidate_from_source(source, defaults, str(resource["url"]), resource))
                    if len(source_candidates) >= source_limit:
                        break
        except (httpx.HTTPError, ET.ParseError):
            source_candidates = []
        if source_candidates:
            candidates_by_source.append(source_candidates)

    return _select_with_category_quotas(config, _round_robin(candidates_by_source), limit=limit)


def check_accessibility(client: httpx.Client, url: str, *, max_bytes: int = 5_000_000) -> AccessResult:
    checked_at = _now_iso()
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.8,*/*;q=0.5",
        "User-Agent": "zhixue-resource-importer/1.0",
    }
    try:
        response = client.get(url, headers=headers)
        content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
        body = response.content[:max_bytes]
        content_error = _unsupported_content_error(content_type, body) if 200 <= response.status_code < 400 else ""
        return AccessResult(
            original_url=url,
            final_url=str(response.url),
            status_code=response.status_code,
            content_type=content_type,
            checked_at=checked_at,
            body=body,
            error=content_error or ("" if 200 <= response.status_code < 400 else f"HTTP {response.status_code}"),
        )
    except httpx.HTTPError as exc:
        return AccessResult(
            original_url=url,
            final_url=url,
            status_code=0,
            content_type="",
            checked_at=checked_at,
            error=f"{type(exc).__name__}: {exc}",
        )


def resource_uuid(url: str) -> str:
    return str(uuid.uuid5(IMPORT_NAMESPACE, f"resource:{url}"))


def document_uuid(url: str) -> str:
    return str(uuid.uuid5(IMPORT_NAMESPACE, f"resource-document:{url}"))


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def vector_literal(values: list[float]) -> str:
    return "[" + ",".join(str(float(value)) for value in values) + "]"


def build_metadata(
    candidate: ResourceCandidate,
    access: AccessResult,
    parsed: ParsedDocument,
    *,
    rag_ready: bool,
    rag_status: str,
) -> dict[str, Any]:
    source_url = access.final_url or candidate.url
    metadata = {
        "sourceUrl": source_url,
        "originalUrl": candidate.url,
        "sourceName": candidate.source_name,
        "license": candidate.license,
        "copyrightStatus": candidate.copyright_status,
        "accessibilityStatus": "ACCESSIBLE",
        "httpStatus": access.status_code,
        "contentType": access.content_type,
        "lastCheckedAt": access.checked_at,
        "qualityScore": round(max(0.0, min(candidate.quality_score, 1.0)), 4),
        "popularityScore": round(max(0.0, min(candidate.popularity_score, 1.0)), 4),
        "displayType": candidate.display_type,
        "csCategory": candidate.cs_category,
        "csSubcategory": candidate.cs_subcategory,
        "discoveredBy": candidate.source_id,
        "ingestedBy": "external_resource_importer",
        "contentHash": parsed.content_hash,
        "ragReady": rag_ready,
        "ragStatus": rag_status,
    }
    if source_url != candidate.url:
        metadata["redirected"] = True
    return metadata


def upsert_learning_resource(
    cur: Any,
    candidate: ResourceCandidate,
    access: AccessResult,
    parsed: ParsedDocument,
    metadata: dict[str, Any],
) -> str:
    source_url = metadata["sourceUrl"]
    resource_id = resource_uuid(source_url)
    cur.execute(
        """
        INSERT INTO app.learning_resource (
          id, title, domain, resource_type, difficulty_level, source_kind,
          access_scope, summary_text, tags, metadata_json, status
        )
        VALUES (
          %s, %s, %s, %s::app.resource_type, %s::app.difficulty_level, 'WEB'::app.source_kind,
          'GLOBAL'::app.access_scope, %s, %s::jsonb, %s::jsonb, 'ACTIVE'
        )
        ON CONFLICT (id) DO UPDATE SET
          title = EXCLUDED.title,
          domain = EXCLUDED.domain,
          resource_type = EXCLUDED.resource_type,
          difficulty_level = EXCLUDED.difficulty_level,
          source_kind = EXCLUDED.source_kind,
          access_scope = EXCLUDED.access_scope,
          summary_text = EXCLUDED.summary_text,
          tags = EXCLUDED.tags,
          metadata_json = app.learning_resource.metadata_json || EXCLUDED.metadata_json,
          status = 'ACTIVE',
          updated_at = now()
        """,
        (
            resource_id,
            candidate.title or parsed.title,
            candidate.domain,
            candidate.resource_type,
            candidate.difficulty,
            parsed.summary,
            _json(list(candidate.tags)),
            _json(metadata),
        ),
    )
    return resource_id


def upsert_resource_rag(
    cur: Any,
    candidate: ResourceCandidate,
    access: AccessResult,
    parsed: ParsedDocument,
    metadata: dict[str, Any],
    chunks: list[str],
    embeddings: list[list[float]],
) -> str:
    if len(chunks) != len(embeddings):
        raise ValueError("chunks and embeddings length mismatch")
    source_url = metadata["sourceUrl"]
    resource_id = resource_uuid(source_url)
    doc_id = document_uuid(source_url)
    cur.execute(
        """
        INSERT INTO rag.resource_document (
          id, resource_id, title, domain, resource_type, difficulty_level, source_kind,
          source_ref, summary_text, transcript_text, access_scope, metadata_json
        )
        VALUES (
          %s, %s, %s, %s, %s::app.resource_type, %s::app.difficulty_level, 'WEB'::app.source_kind,
          %s, %s, %s, 'GLOBAL'::app.access_scope, %s::jsonb
        )
        ON CONFLICT (resource_id) DO UPDATE SET
          title = EXCLUDED.title,
          domain = EXCLUDED.domain,
          resource_type = EXCLUDED.resource_type,
          difficulty_level = EXCLUDED.difficulty_level,
          source_kind = EXCLUDED.source_kind,
          source_ref = EXCLUDED.source_ref,
          summary_text = EXCLUDED.summary_text,
          transcript_text = EXCLUDED.transcript_text,
          access_scope = EXCLUDED.access_scope,
          metadata_json = rag.resource_document.metadata_json || EXCLUDED.metadata_json,
          updated_at = now()
        RETURNING id
        """,
        (
            doc_id,
            resource_id,
            candidate.title or parsed.title,
            candidate.domain,
            candidate.resource_type,
            candidate.difficulty,
            source_url,
            parsed.summary,
            parsed.text[:120000],
            _json(metadata),
        ),
    )
    returned = cur.fetchone()
    actual_doc_id = str(returned[0]) if returned else doc_id
    cur.execute("DELETE FROM rag.resource_chunk WHERE document_id = %s", (actual_doc_id,))
    for chunk_no, (chunk, embedding) in enumerate(zip(chunks, embeddings), start=1):
        cur.execute(
            """
            INSERT INTO rag.resource_chunk (
              document_id, resource_id, chunk_no, content, embedding, token_count,
              domain, resource_type, difficulty_level, access_scope, quality_score, metadata_json
            )
            VALUES (
              %s, %s, %s, %s, %s::vector, %s,
              %s, %s::app.resource_type, %s::app.difficulty_level,
              'GLOBAL'::app.access_scope, %s, %s::jsonb
            )
            """,
            (
                actual_doc_id,
                resource_id,
                chunk_no,
                chunk,
                vector_literal(embedding),
                estimate_tokens(chunk),
                candidate.domain,
                candidate.resource_type,
                candidate.difficulty,
                metadata["qualityScore"],
                _json({
                    "sourceUrl": source_url,
                    "csCategory": candidate.cs_category,
                    "csSubcategory": candidate.cs_subcategory,
                    "chunkHash": hashlib.sha256(chunk.encode("utf-8")).hexdigest(),
                }),
            ),
        )
    return actual_doc_id


def generate_embeddings(
    texts: list[str],
    *,
    api_key: str,
    model: str,
    dimension: int,
    batch_size: int = 2,
    delay_seconds: float = 0.25,
    max_retries: int = 4,
) -> list[list[float]]:
    if not api_key:
        raise RuntimeError("embedding API key is not configured")
    os.environ["DASHSCOPE_API_KEY"] = api_key
    from dashscope import MultiModalEmbedding

    embeddings: list[list[float]] = []
    for offset in range(0, len(texts), batch_size):
        batch = texts[offset : offset + batch_size]
        response = None
        for attempt in range(max_retries + 1):
            try:
                response = MultiModalEmbedding.call(
                    model=model,
                    input=[{"text": text} for text in batch],
                    dimension=dimension,
                    output_type="dense",
                )
                break
            except Exception:
                if attempt >= max_retries:
                    raise
                time.sleep(min(8.0, delay_seconds * (2 ** attempt)))
        if response is None:
            raise RuntimeError("Embedding API returned no response")
        if response.status_code != 200:
            raise RuntimeError(f"Embedding API error: {response.code} {response.message}")
        items = response.output.get("embeddings", [])
        items.sort(key=lambda item: item.get("index", 0))
        batch_embeddings = [[float(value) for value in item["embedding"]] for item in items]
        for embedding in batch_embeddings:
            if len(embedding) != dimension:
                raise RuntimeError(f"Embedding dimension mismatch: expected {dimension}, got {len(embedding)}")
        embeddings.extend(batch_embeddings)
        if delay_seconds > 0 and offset + batch_size < len(texts):
            time.sleep(delay_seconds)
    if len(embeddings) != len(texts):
        raise RuntimeError(f"Embedding count mismatch: expected {len(texts)}, got {len(embeddings)}")
    return embeddings


def _connect_postgres() -> Any:
    import psycopg2

    return psycopg2.connect(**get_settings().postgres_connect_kwargs())


def load_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def fetch_candidate_content(candidate: ResourceCandidate, *, timeout_seconds: float, max_bytes: int) -> CandidateContent:
    try:
        with httpx.Client(follow_redirects=True, timeout=timeout_seconds) as client:
            access = check_accessibility(client, candidate.url, max_bytes=max_bytes)
        if not access.accessible:
            return CandidateContent(candidate=candidate, access=access)
        parsed = parse_html_document(access.body, fallback_title=candidate.title)
        return CandidateContent(candidate=candidate, access=access, parsed=parsed)
    except Exception as exc:
        return CandidateContent(
            candidate=candidate,
            access=AccessResult(
                original_url=candidate.url,
                final_url=candidate.url,
                status_code=0,
                content_type="",
                checked_at=_now_iso(),
                error=f"{type(exc).__name__}: {exc}",
            ),
            error=f"{type(exc).__name__}: {exc}",
        )


def iter_candidate_contents(
    candidates: list[ResourceCandidate],
    *,
    client: httpx.Client,
    timeout_seconds: float,
    max_bytes: int,
    access_workers: int,
) -> Iterable[CandidateContent]:
    if access_workers <= 1:
        for candidate in candidates:
            access = check_accessibility(client, candidate.url, max_bytes=max_bytes)
            parsed = parse_html_document(access.body, fallback_title=candidate.title) if access.accessible else None
            yield CandidateContent(candidate=candidate, access=access, parsed=parsed)
        return

    with ThreadPoolExecutor(max_workers=access_workers) as executor:
        futures = [
            executor.submit(fetch_candidate_content, candidate, timeout_seconds=timeout_seconds, max_bytes=max_bytes)
            for candidate in candidates
        ]
        for future in as_completed(futures):
            yield future.result()


def import_resources(
    *,
    config_path: Path,
    limit: int,
    rag_limit: int,
    metadata_only: bool,
    require_embeddings: bool,
    dry_run: bool,
    timeout_seconds: float,
    max_bytes: int,
    skip_existing: bool = False,
    rag_missing_only: bool = False,
    embedding_batch_size: int = 2,
    access_workers: int = 1,
    embedder: Callable[..., list[list[float]]] = generate_embeddings,
    db_factory: Callable[[], Any] = _connect_postgres,
) -> ImportStats:
    settings = get_settings()
    api_key = settings.effective_embedding_api_key
    can_embed = bool(api_key) and not dry_run and not metadata_only and rag_limit > 0
    if require_embeddings and not dry_run and not can_embed:
        raise RuntimeError("RAG import requires a configured embedding API key")

    config = load_config(config_path)
    stats = ImportStats()
    with httpx.Client(follow_redirects=True, timeout=timeout_seconds) as client:
        candidates = iter_candidates(config, client, limit=limit)
        stats.discovered = len(candidates)
        for candidate in candidates:
            stats.source_counts[candidate.source_id] = stats.source_counts.get(candidate.source_id, 0) + 1
            stats.category_counts[candidate.cs_category] = stats.category_counts.get(candidate.cs_category, 0) + 1
        conn = None if dry_run else db_factory()
        try:
            if skip_existing and conn is not None:
                candidates = filter_existing_candidates(conn, candidates)
            if rag_missing_only and conn is not None:
                candidates = filter_rag_missing_candidates(conn, candidates)
            safe_access_workers = max(1, access_workers if metadata_only or not can_embed else 1)
            for content in iter_candidate_contents(
                candidates,
                client=client,
                timeout_seconds=timeout_seconds,
                max_bytes=max_bytes,
                access_workers=safe_access_workers,
            ):
                candidate = content.candidate
                try:
                    if content.error:
                        raise RuntimeError(content.error)
                    access = content.access
                    if not access.accessible:
                        stats.skipped_inaccessible += 1
                        continue
                    stats.accessible += 1
                    parsed = content.parsed or parse_html_document(access.body, fallback_title=candidate.title)
                    chunks: list[str] = []
                    embeddings: list[list[float]] = []
                    rag_ready = False
                    rag_status = "METADATA_ONLY" if metadata_only else "SKIPPED"
                    if can_embed and stats.rag_ingested < rag_limit:
                        chunks = chunk_text(parsed.text)
                        if chunks:
                            try:
                                embeddings = embedder(
                                    chunks,
                                    api_key=api_key,
                                    model=settings.knowledge_embedding_model_name,
                                    dimension=settings.knowledge_embedding_dimension,
                                    batch_size=embedding_batch_size,
                                )
                                rag_ready = True
                                rag_status = "READY"
                            except Exception as exc:
                                if require_embeddings:
                                    raise
                                rag_status = f"EMBEDDING_FAILED: {type(exc).__name__}"
                                stats.skipped_rag += 1
                        else:
                            rag_status = "NO_TEXT_CHUNKS"
                            stats.skipped_rag += 1
                    elif dry_run and not metadata_only:
                        stats.skipped_rag += 1
                        rag_status = "DRY_RUN_NO_EMBEDDING_CALL"
                    elif not metadata_only:
                        stats.skipped_rag += 1
                        rag_status = "NO_EMBEDDING_KEY" if not api_key else "RAG_LIMIT_REACHED"

                    metadata = build_metadata(candidate, access, parsed, rag_ready=rag_ready, rag_status=rag_status)
                    if dry_run:
                        stats.inserted_metadata += 1
                        if rag_ready:
                            stats.rag_ingested += 1
                        continue

                    assert conn is not None
                    with conn:
                        with conn.cursor() as cur:
                            upsert_learning_resource(cur, candidate, access, parsed, metadata)
                            stats.inserted_metadata += 1
                            if rag_ready:
                                upsert_resource_rag(cur, candidate, access, parsed, metadata, chunks, embeddings)
                                stats.rag_ingested += 1
                except Exception as exc:
                    stats.failed += 1
                    stats.errors.append(f"{candidate.url}: {type(exc).__name__}: {exc}")
                    if require_embeddings:
                        raise
        finally:
            if conn is not None:
                conn.close()
    return stats


def filter_existing_candidates(conn: Any, candidates: list[ResourceCandidate]) -> list[ResourceCandidate]:
    if not candidates:
        return candidates
    existing_ids: set[str] = set()
    with conn.cursor() as cur:
        for offset in range(0, len(candidates), 500):
            batch = candidates[offset : offset + 500]
            cur.execute(
                "SELECT id::text FROM app.learning_resource WHERE id = ANY(%s::uuid[])",
                ([resource_uuid(candidate.url) for candidate in batch],),
            )
            existing_ids.update(str(row[0]) for row in cur.fetchall())
    return [candidate for candidate in candidates if resource_uuid(candidate.url) not in existing_ids]


def filter_rag_missing_candidates(conn: Any, candidates: list[ResourceCandidate]) -> list[ResourceCandidate]:
    if not candidates:
        return candidates
    existing_ids: set[str] = set()
    with conn.cursor() as cur:
        for offset in range(0, len(candidates), 500):
            batch = candidates[offset : offset + 500]
            cur.execute(
                "SELECT resource_id::text FROM rag.resource_document WHERE resource_id = ANY(%s::uuid[])",
                ([resource_uuid(candidate.url) for candidate in batch],),
            )
            existing_ids.update(str(row[0]) for row in cur.fetchall())
    return [candidate for candidate in candidates if resource_uuid(candidate.url) not in existing_ids]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import accessible external learning resources into Zhixue.")
    parser.add_argument("--source-file", type=Path, default=DEFAULT_SOURCE_FILE)
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--rag-limit", type=int, default=300)
    parser.add_argument("--metadata-only", action="store_true")
    parser.add_argument("--require-embeddings", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--max-bytes", type=int, default=5_000_000)
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--rag-missing-only", action="store_true")
    parser.add_argument("--embedding-batch-size", type=int, default=2)
    parser.add_argument("--access-workers", type=int, default=1)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    stats = import_resources(
        config_path=args.source_file,
        limit=args.limit,
        rag_limit=args.rag_limit,
        metadata_only=args.metadata_only,
        require_embeddings=args.require_embeddings,
        dry_run=args.dry_run,
        timeout_seconds=args.timeout,
        max_bytes=args.max_bytes,
        skip_existing=args.skip_existing,
        rag_missing_only=args.rag_missing_only,
        embedding_batch_size=args.embedding_batch_size,
        access_workers=args.access_workers,
    )
    print(json.dumps(stats.__dict__, ensure_ascii=False, indent=2))
    return 1 if stats.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
