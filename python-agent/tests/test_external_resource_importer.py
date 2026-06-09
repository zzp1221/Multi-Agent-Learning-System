import gzip
import importlib.util
import json
import sys
from pathlib import Path

import httpx
import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "import_external_resources.py"
SPEC = importlib.util.spec_from_file_location("import_external_resources", MODULE_PATH)
import_external_resources = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = import_external_resources
SPEC.loader.exec_module(import_external_resources)


def test_sitemap_candidates_are_filtered_and_limited() -> None:
    sitemap_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://developer.mozilla.org/zh-CN/docs/Web/JavaScript/Guide</loc></url>
      <url><loc>https://developer.mozilla.org/zh-CN/docs/Games</loc></url>
      <url><loc>https://developer.mozilla.org/zh-CN/docs/Web/API/Fetch_API</loc></url>
    </urlset>
    """.encode()

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://example.test/sitemap.xml.gz"
        return httpx.Response(200, content=gzip.compress(sitemap_xml), request=request)

    config = {
        "defaults": {"domain": "COMPUTER_SCIENCE", "tags": ["official-docs"]},
        "sources": [
            {
                "id": "mdn",
                "sourceName": "MDN",
                "sourceType": "sitemap",
                "sitemapUrl": "https://example.test/sitemap.xml.gz",
                "includeRegex": ["/Web/JavaScript/", "/Web/API/"],
                "maxItems": 1,
                "tags": ["web"],
            }
        ],
    }
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)

    candidates = import_external_resources.iter_candidates(config, client, limit=10)

    assert len(candidates) == 1
    assert candidates[0].url.endswith("/Web/JavaScript/Guide")
    assert candidates[0].tags == ("official-docs", "web")


def test_candidates_are_sampled_across_sources_before_global_limit() -> None:
    sitemap_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://developer.mozilla.org/zh-CN/docs/Web/JavaScript/Guide</loc></url>
      <url><loc>https://developer.mozilla.org/zh-CN/docs/Web/API/Fetch_API</loc></url>
      <url><loc>https://developer.mozilla.org/zh-CN/docs/Learn_web_development/Core</loc></url>
    </urlset>
    """.encode()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=sitemap_xml, request=request)

    config = {
        "defaults": {"domain": "COMPUTER_SCIENCE", "resourceType": "READING", "difficulty": "MIXED"},
        "sources": [
            {
                "id": "mdn",
                "sourceName": "MDN",
                "sourceType": "sitemap",
                "sitemapUrl": "https://example.test/sitemap.xml",
                "includeRegex": ["/docs/"],
                "maxItems": 3,
            },
            {
                "id": "python",
                "sourceName": "Python Documentation",
                "sourceType": "urls",
                "resources": [
                    {"title": "Python Tutorial", "url": "https://docs.python.org/3/tutorial/index.html"},
                    {"title": "Python Library", "url": "https://docs.python.org/3/library/index.html"},
                ],
            },
        ],
    }
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)

    candidates = import_external_resources.iter_candidates(config, client, limit=3)

    assert [candidate.source_id for candidate in candidates] == ["mdn", "python", "mdn"]
    assert "https://docs.python.org/3/tutorial/index.html" in [candidate.url for candidate in candidates]


def test_bad_sitemap_source_does_not_block_other_sources() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not xml</broken>", request=request)

    config = {
        "defaults": {"domain": "COMPUTER_SCIENCE", "resourceType": "READING", "difficulty": "MIXED"},
        "sources": [
            {
                "id": "bad-sitemap",
                "sourceName": "Bad Sitemap",
                "sourceType": "sitemap",
                "sitemapUrl": "https://example.test/sitemap.xml",
                "includeRegex": ["/docs/"],
            },
            {
                "id": "manual",
                "sourceName": "Manual",
                "sourceType": "urls",
                "resources": [{"title": "Manual Resource", "url": "https://example.test/manual"}],
            },
        ],
    }
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)

    candidates = import_external_resources.iter_candidates(config, client, limit=10)

    assert [candidate.source_id for candidate in candidates] == ["manual"]


def test_index_source_extracts_links_and_category_metadata() -> None:
    index_html = """
    <html><body>
      <a href="/docs/python.html#intro">Python</a>
      <a href="https://example.test/docs/rust.html">Rust</a>
      <a href="https://example.test/blog/news.html">News</a>
    </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=index_html.encode(), request=request)

    config = {
        "defaults": {"domain": "COMPUTER_SCIENCE", "tags": ["official-docs"]},
        "sources": [
            {
                "id": "language-index",
                "sourceName": "Language Index",
                "sourceType": "index",
                "indexUrl": "https://example.test/index.html",
                "includeRegex": ["/docs/"],
                "maxItems": 3,
                "csCategory": "PROGRAMMING_LANGUAGES",
                "csSubcategory": "Language References",
                "tags": ["programming"],
            }
        ],
    }
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)

    candidates = import_external_resources.iter_candidates(config, client, limit=10)

    assert [candidate.url for candidate in candidates] == [
        "https://example.test/docs/python.html",
        "https://example.test/docs/rust.html",
    ]
    assert candidates[0].title == "Python"
    assert candidates[0].cs_category == "PROGRAMMING_LANGUAGES"
    assert candidates[0].cs_subcategory == "Language References"


def test_url_template_source_expands_numbered_resources() -> None:
    config = {
        "defaults": {"domain": "COMPUTER_SCIENCE", "resourceType": "READING", "difficulty": "MIXED"},
        "sources": [
            {
                "id": "numbered-course",
                "sourceName": "Numbered Course",
                "sourceType": "urls",
                "csCategory": "MATH_FOUNDATIONS",
                "urlTemplates": [
                    {
                        "urlTemplate": "https://example.test/lesson/{n:02d}",
                        "titleTemplate": "Lesson {n:02d}",
                        "start": 1,
                        "end": 3,
                    }
                ],
            }
        ],
    }
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, request=request)), follow_redirects=True)

    candidates = import_external_resources.iter_candidates(config, client, limit=10)

    assert [candidate.url for candidate in candidates] == [
        "https://example.test/lesson/01",
        "https://example.test/lesson/02",
        "https://example.test/lesson/03",
    ]
    assert candidates[0].title == "Lesson 01"


def test_bilibili_search_source_builds_video_metadata_candidates() -> None:
    payload = {
        "code": 0,
        "data": {
            "result": [
                {
                    "type": "video",
                    "bvid": "BV1234567890",
                    "title": "操作系统 <em class=\"keyword\">进程</em> 调度",
                    "description": "讲解进程、线程和调度策略。",
                    "tag": "操作系统,进程,线程,调度",
                },
                {
                    "type": "ketang",
                    "bvid": "",
                    "title": "付费课堂",
                },
            ]
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["search_type"] == "video"
        return httpx.Response(200, json=payload, request=request)

    config = {
        "defaults": {"domain": "COMPUTER_SCIENCE", "tags": ["computer-science"]},
        "sources": [
            {
                "id": "bili-os",
                "sourceName": "Bilibili CS Videos",
                "sourceType": "bilibiliSearch",
                "keywords": ["操作系统"],
                "csCategory": "OPERATING_SYSTEMS",
                "csSubcategory": "进程与调度",
                "tags": ["video"],
            }
        ],
    }
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)

    candidates = import_external_resources.iter_candidates(config, client, limit=10)

    assert len(candidates) == 1
    assert candidates[0].url == "https://www.bilibili.com/video/BV1234567890/"
    assert candidates[0].title == "操作系统 进程 调度"
    assert candidates[0].resource_type == "VIDEO"
    assert candidates[0].display_type == "VIDEO"
    assert candidates[0].cs_category == "OPERATING_SYSTEMS"
    assert candidates[0].summary == "讲解进程、线程和调度策略。"
    assert "调度" in candidates[0].tags
    assert "CS方向：OPERATING_SYSTEMS" in (candidates[0].metadata_text or "")


def test_youtube_search_source_builds_video_metadata_candidates() -> None:
    html = """
    <html><body>
      "videoId":"abc123XYZ00","title":{"runs":[{"text":"Operating Systems Lecture 1"}]}
      "videoId":"def456XYZ00","title":{"runs":[{"text":"Operating Systems Lecture 2"}]}
    </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["search_query"] == "operating systems course"
        return httpx.Response(200, text=html, request=request)

    config = {
        "defaults": {"domain": "COMPUTER_SCIENCE", "tags": ["computer-science"]},
        "sources": [
            {
                "id": "youtube-os",
                "sourceName": "YouTube CS Videos",
                "sourceType": "youtubeSearch",
                "keywords": ["operating systems course"],
                "perKeyword": 1,
                "csCategory": "OPERATING_SYSTEMS",
                "csSubcategory": "Operating Systems",
                "summary": "Operating systems lecture videos.",
                "tags": ["video"],
            }
        ],
    }
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)

    candidates = import_external_resources.iter_candidates(config, client, limit=10)

    assert len(candidates) == 1
    assert candidates[0].url == "https://www.youtube.com/watch?v=abc123XYZ00"
    assert candidates[0].title == "Operating Systems Lecture 1"
    assert candidates[0].resource_type == "VIDEO"
    assert candidates[0].display_type == "VIDEO"
    assert candidates[0].cs_category == "OPERATING_SYSTEMS"
    assert candidates[0].summary == "Operating systems lecture videos."
    assert "operating" in candidates[0].tags
    assert "标题：Operating Systems Lecture 1" in (candidates[0].metadata_text or "")


def test_static_video_resource_builds_metadata_text_from_title_summary_and_tags() -> None:
    config = {
        "defaults": {"domain": "COMPUTER_SCIENCE", "resourceType": "VIDEO", "displayType": "VIDEO"},
        "sources": [
            {
                "id": "video-list",
                "sourceName": "Public CS Videos",
                "sourceType": "urls",
                "csCategory": "AI_ML",
                "csSubcategory": "Deep Learning",
                "tags": ["video"],
                "resources": [
                    {
                        "title": "深度学习入门",
                        "url": "https://example.test/deep-learning-video",
                        "summary": "讲解神经网络、反向传播和 PyTorch。",
                        "tags": ["deep-learning", "pytorch"],
                    }
                ],
            }
        ],
    }
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, request=request)), follow_redirects=True)

    candidates = import_external_resources.iter_candidates(config, client, limit=10)

    assert candidates[0].metadata_text is not None
    assert "标题：深度学习入门" in candidates[0].metadata_text
    assert "简介：讲解神经网络、反向传播和 PyTorch。" in candidates[0].metadata_text
    assert "标签：video、deep-learning、pytorch" in candidates[0].metadata_text


def test_category_quotas_are_filled_before_global_round_robin() -> None:
    config = {
        "defaults": {"domain": "COMPUTER_SCIENCE", "resourceType": "READING", "difficulty": "MIXED"},
        "categoryQuotas": {"AI_ML": 2, "DATABASES": 2},
        "sources": [
            {
                "id": "ai",
                "sourceName": "AI Docs",
                "sourceType": "urls",
                "csCategory": "AI_ML",
                "resources": [
                    {"title": "AI 1", "url": "https://example.test/ai/1"},
                    {"title": "AI 2", "url": "https://example.test/ai/2"},
                    {"title": "AI 3", "url": "https://example.test/ai/3"},
                ],
            },
            {
                "id": "db",
                "sourceName": "DB Docs",
                "sourceType": "urls",
                "csCategory": "DATABASES",
                "resources": [
                    {"title": "DB 1", "url": "https://example.test/db/1"},
                    {"title": "DB 2", "url": "https://example.test/db/2"},
                ],
            },
        ],
    }
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, request=request)), follow_redirects=True)

    candidates = import_external_resources.iter_candidates(config, client, limit=4)

    assert [candidate.cs_category for candidate in candidates].count("AI_ML") == 2
    assert [candidate.cs_category for candidate in candidates].count("DATABASES") == 2


def test_category_round_robin_selection_balances_early_results() -> None:
    config = {
        "defaults": {"domain": "COMPUTER_SCIENCE", "resourceType": "READING", "difficulty": "MIXED"},
        "selectionMode": "categoryRoundRobin",
        "categoryQuotas": {"AI_ML": 3, "DATABASES": 3},
        "sources": [
            {
                "id": "ai",
                "sourceName": "AI Docs",
                "sourceType": "urls",
                "csCategory": "AI_ML",
                "resources": [
                    {"title": "AI 1", "url": "https://example.test/ai/1"},
                    {"title": "AI 2", "url": "https://example.test/ai/2"},
                    {"title": "AI 3", "url": "https://example.test/ai/3"},
                ],
            },
            {
                "id": "db",
                "sourceName": "DB Docs",
                "sourceType": "urls",
                "csCategory": "DATABASES",
                "resources": [
                    {"title": "DB 1", "url": "https://example.test/db/1"},
                    {"title": "DB 2", "url": "https://example.test/db/2"},
                    {"title": "DB 3", "url": "https://example.test/db/3"},
                ],
            },
        ],
    }
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, request=request)), follow_redirects=True)

    candidates = import_external_resources.iter_candidates(config, client, limit=4)

    assert [candidate.cs_category for candidate in candidates] == ["AI_ML", "DATABASES", "AI_ML", "DATABASES"]


def test_accessibility_requires_success_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("/ok"):
            return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<h1>OK</h1>", request=request)
        return httpx.Response(404, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)

    ok = import_external_resources.check_accessibility(client, "https://example.test/ok")
    missing = import_external_resources.check_accessibility(client, "https://example.test/missing")

    assert ok.accessible is True
    assert ok.content_type == "text/html"
    assert missing.accessible is False
    assert missing.error == "HTTP 404"


def test_accessibility_rejects_binary_content() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/zip"},
            content=b"PK\x03\x04\x00binary",
            request=request,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)

    result = import_external_resources.check_accessibility(client, "https://example.test/archive.zip")

    assert result.accessible is False
    assert result.error == "BINARY_CONTENT"


def test_wiki_bound_fetch_validates_url_without_reading_body() -> None:
    candidate = import_external_resources.ResourceCandidate(
        source_id="wiki-graph",
        source_name="Wiki Resource Importer",
        url="https://example.test/graph",
        title="Graph Guide",
        domain="COMPUTER_SCIENCE",
        resource_type="READING",
        display_type="DOCUMENT",
        difficulty="MIXED",
        license="Public web page",
        copyright_status="PUBLICLY_ACCESSIBLE_WEB_RESOURCE",
        tags=("graph",),
        quality_score=0.88,
        popularity_score=0.6,
        summary="BFS and DFS guide",
        metadata_text="Wiki title: Graph Traversal\nWiki slug: algorithms/graph-traversal\nResource title: Graph Guide",
        wiki_slug="algorithms/graph-traversal",
        wiki_title="Graph Traversal",
        wiki_source_ref="wiki://algorithms/graph-traversal",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=b"<html><body>" + b"web body should not be read " * 1000 + b"</body></html>",
            request=request,
        )

    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        [content] = list(
            import_external_resources.iter_candidate_contents(
                [candidate],
                client=client,
                timeout_seconds=1.0,
                max_bytes=100000,
                access_workers=1,
            )
        )

    assert content.access.accessible is True
    assert content.access.body == b""
    assert content.parsed is not None
    assert content.parsed.text == candidate.metadata_text
    assert "web body should not be read" not in content.parsed.text


def test_html_parser_ignores_scripts_and_builds_summary() -> None:
    parsed = import_external_resources.parse_html_document(
        b"<html><head><title>Fallback</title><script>bad()</script></head>"
        b"<body><h1>Python Tutorial</h1><p>Learn syntax and control flow.</p></body></html>"
    )

    assert parsed.title == "Python Tutorial"
    assert "bad()" not in parsed.text
    assert "Learn syntax" in parsed.summary


def test_candidate_metadata_text_builds_rag_document_without_page_noise() -> None:
    candidate = import_external_resources.ResourceCandidate(
        source_id="bili-os",
        source_name="Bilibili CS Videos",
        url="https://www.bilibili.com/video/BV1234567890/",
        title="操作系统进程调度",
        domain="COMPUTER_SCIENCE",
        resource_type="VIDEO",
        display_type="VIDEO",
        difficulty="MIXED",
        license="Public video page",
        copyright_status="PUBLICLY_ACCESSIBLE_VIDEO_METADATA",
        tags=("操作系统", "调度"),
        quality_score=0.82,
        popularity_score=0.7,
        summary="讲解进程、线程和调度策略。",
        metadata_text="标题：操作系统进程调度\n简介：讲解进程、线程和调度策略。\n标签：操作系统、调度",
        cs_category="OPERATING_SYSTEMS",
        cs_subcategory="进程与调度",
    )

    parsed = import_external_resources.parse_candidate_document(
        candidate,
        b"<html><body><p>recommended videos and comments should not enter rag</p></body></html>",
    )

    assert parsed.title == "操作系统进程调度"
    assert "讲解进程" in parsed.text
    assert parsed.summary == "讲解进程、线程和调度策略。"
    assert "recommended videos" not in parsed.text


def test_metadata_rag_uses_short_chunk_threshold_for_video_resources() -> None:
    candidate = import_external_resources.ResourceCandidate(
        source_id="bili-ai",
        source_name="Bilibili CS Videos",
        url="https://www.bilibili.com/video/BV1234567890/",
        title="深度学习入门",
        domain="COMPUTER_SCIENCE",
        resource_type="VIDEO",
        display_type="VIDEO",
        difficulty="MIXED",
        license="Public video page",
        copyright_status="PUBLICLY_ACCESSIBLE_VIDEO_METADATA",
        tags=("深度学习",),
        quality_score=0.82,
        popularity_score=0.7,
        summary="讲解神经网络和 PyTorch。",
        metadata_text="标题：深度学习入门\n简介：讲解神经网络和 PyTorch。\n标签：深度学习、神经网络",
        cs_category="AI_ML",
        cs_subcategory="Deep Learning",
    )
    parsed = import_external_resources.parse_candidate_document(candidate, b"")

    chunks = import_external_resources.chunk_candidate_text(candidate, parsed)

    assert len(chunks) == 1
    assert "神经网络" in chunks[0]


def test_chunk_text_splits_single_oversized_paragraph() -> None:
    text = "x" * 7000

    chunks = import_external_resources.chunk_text(text, target_chars=1200, overlap_chars=100, min_chars=260)

    assert len(chunks) > 1
    assert max(len(chunk) for chunk in chunks) <= 1200


def test_generate_embeddings_refuses_missing_api_key() -> None:
    with pytest.raises(RuntimeError, match="embedding API key"):
        import_external_resources.generate_embeddings(["hello"], api_key="", model="qwen3-vl-embedding", dimension=1024)


def test_generate_embeddings_refuses_oversized_input_before_api_call() -> None:
    with pytest.raises(RuntimeError, match="embedding input too long"):
        import_external_resources.generate_embeddings(
            ["x" * 60000],
            api_key="key",
            model="qwen3-vl-embedding",
            dimension=1024,
        )


def test_embedding_preflight_validates_three_1024_vectors() -> None:
    captured = {}

    def fake_embedder(texts, **kwargs):
        captured["texts"] = texts
        captured["kwargs"] = kwargs
        return [[0.01] * 1024 for _ in texts]

    result = import_external_resources.run_embedding_preflight(
        api_key="key",
        model="qwen3-vl-embedding",
        dimension=1024,
        request_timeout=3.0,
        batch_size=2,
        embedder=fake_embedder,
    )

    assert result["passed"] is True
    assert result["model"] == "qwen3-vl-embedding"
    assert result["dimensions"] == [1024, 1024, 1024]
    assert len(captured["texts"]) == 3
    assert captured["kwargs"]["dimension"] == 1024


def test_embedding_preflight_rejects_bad_dimensions() -> None:
    def fake_embedder(texts, **kwargs):
        del kwargs
        return [[0.01] * 1024, [0.02] * 8, [0.03] * 1024]

    with pytest.raises(RuntimeError, match="dimension mismatch"):
        import_external_resources.run_embedding_preflight(
            api_key="key",
            model="qwen3-vl-embedding",
            dimension=1024,
            request_timeout=3.0,
            batch_size=3,
            embedder=fake_embedder,
        )


def test_upsert_resource_and_rag_use_real_vector_payloads() -> None:
    class RecordingCursor:
        def __init__(self) -> None:
            self.calls = []
            self.next_fetchone = None

        def execute(self, sql, params=None):
            self.calls.append((sql, params))

        def fetchone(self):
            return self.next_fetchone

    cursor = RecordingCursor()
    candidate = import_external_resources.ResourceCandidate(
        source_id="python-docs",
        source_name="Python Documentation",
        url="https://docs.python.org/3/tutorial/index.html",
        title="Python Tutorial",
        domain="COMPUTER_SCIENCE",
        resource_type="READING",
        display_type="COURSE",
        difficulty="BASIC",
        license="PSF documentation license",
        copyright_status="OFFICIAL_PUBLIC_DOCUMENTATION",
        tags=("python", "tutorial"),
        quality_score=0.9,
        popularity_score=0.8,
        cs_category="PROGRAMMING_LANGUAGES",
        cs_subcategory="Python",
    )
    access = import_external_resources.AccessResult(
        original_url=candidate.url,
        final_url=candidate.url,
        status_code=200,
        content_type="text/html",
        checked_at="2026-06-06T00:00:00+00:00",
        body=b"",
    )
    parsed = import_external_resources.ParsedDocument(
        title="Python Tutorial",
        text="Python tutorial content " * 30,
        summary="Python tutorial content",
        content_hash="abc",
    )
    metadata = import_external_resources.build_metadata(candidate, access, parsed, rag_ready=True, rag_status="READY")
    embedding = [0.001] * 1024
    expected_doc_id = import_external_resources.document_uuid(candidate.url)
    cursor.next_fetchone = (expected_doc_id,)

    resource_id = import_external_resources.upsert_learning_resource(cursor, candidate, access, parsed, metadata)
    doc_id = import_external_resources.upsert_resource_rag(cursor, candidate, access, parsed, metadata, ["chunk text"], [embedding])

    joined_sql = "\n".join(call[0] for call in cursor.calls)
    assert "INSERT INTO app.learning_resource" in joined_sql
    assert "INSERT INTO rag.resource_document" in joined_sql
    assert "INSERT INTO rag.resource_chunk" in joined_sql
    assert resource_id == import_external_resources.resource_uuid(candidate.url)
    assert doc_id == expected_doc_id
    assert metadata["displayType"] == "COURSE"
    assert metadata["csCategory"] == "PROGRAMMING_LANGUAGES"
    assert metadata["csSubcategory"] == "Python"
    assert "[0.001" in cursor.calls[-1][1][4]
    assert json.loads(cursor.calls[-1][1][-1])["csCategory"] == "PROGRAMMING_LANGUAGES"


def test_wiki_source_ref_is_written_to_resource_document() -> None:
    class RecordingCursor:
        def __init__(self) -> None:
            self.calls = []
            self.next_fetchone = None

        def execute(self, sql, params=None):
            self.calls.append((sql, params))

        def fetchone(self):
            return self.next_fetchone

    cursor = RecordingCursor()
    candidate = import_external_resources.ResourceCandidate(
        source_id="wiki-deadlock",
        source_name="Wiki Resource Importer",
        url="https://example.test/deadlock",
        title="Deadlock Guide",
        domain="COMPUTER_SCIENCE",
        resource_type="READING",
        display_type="DOCUMENT",
        difficulty="MIXED",
        license="Public web page",
        copyright_status="PUBLICLY_ACCESSIBLE_WEB_RESOURCE",
        tags=("concurrency",),
        quality_score=0.88,
        popularity_score=0.6,
        summary="deadlock prevention",
        metadata_text="Wiki title: Deadlock\nResource title: Deadlock Guide",
        cs_category="OPERATING_SYSTEMS",
        cs_subcategory="Concurrency",
        wiki_slug="操作系统/死锁",
        wiki_title="死锁",
        wiki_source_ref="wiki://操作系统/死锁",
        wiki_aliases=("deadlock",),
    )
    access = import_external_resources.AccessResult(
        original_url=candidate.url,
        final_url=candidate.url,
        status_code=200,
        content_type="text/html",
        checked_at="2026-06-08T00:00:00+00:00",
        body=b"<h1>Deadlock Guide</h1><p>deadlock prevention and recovery</p>",
    )
    parsed = import_external_resources.parse_candidate_document(candidate, access.body)
    metadata = import_external_resources.build_metadata(candidate, access, parsed, rag_ready=True, rag_status="READY")
    cursor.next_fetchone = (import_external_resources.document_uuid(candidate.url),)

    import_external_resources.upsert_learning_resource(cursor, candidate, access, parsed, metadata)
    import_external_resources.upsert_resource_rag(cursor, candidate, access, parsed, metadata, ["deadlock chunk"], [[0.1] * 1024])

    resource_doc_call = next(call for call in cursor.calls if "INSERT INTO rag.resource_document" in call[0])
    resource_chunk_call = next(call for call in cursor.calls if "INSERT INTO rag.resource_chunk" in call[0])
    assert resource_doc_call[1][6] == "wiki://操作系统/死锁"
    assert metadata["ingestedBy"] == "wiki_resource_importer"
    assert metadata["wikiSlug"] == "操作系统/死锁"
    assert metadata["wikiSourceRef"] == "wiki://操作系统/死锁"
    assert json.loads(resource_chunk_call[1][-1])["wikiSlug"] == "操作系统/死锁"


def test_wiki_bound_resource_rag_uses_metadata_text_only() -> None:
    candidate = import_external_resources.ResourceCandidate(
        source_id="wiki-graph",
        source_name="Wiki Resource Importer",
        url="https://example.test/graph",
        title="Graph Guide",
        domain="COMPUTER_SCIENCE",
        resource_type="READING",
        display_type="DOCUMENT",
        difficulty="BASIC",
        license="Public web page",
        copyright_status="PUBLICLY_ACCESSIBLE_WEB_RESOURCE",
        tags=("graph", "bfs"),
        quality_score=0.9,
        popularity_score=0.6,
        summary="BFS and DFS overview",
        metadata_text="Wiki title: 图遍历\nWiki slug: algorithms/graph-traversal\nResource title: Graph Guide\nTags: graph, bfs\nURL: https://example.test/graph",
        wiki_slug="algorithms/graph-traversal",
        wiki_title="图遍历",
        wiki_source_ref="wiki://algorithms/graph-traversal",
        wiki_aliases=("BFS", "DFS"),
    )

    parsed = import_external_resources.parse_candidate_document(
        candidate,
        b"<html><body><p>large crawler body should not enter wiki resource rag chunks</p></body></html>",
    )
    chunks = import_external_resources.chunk_candidate_text(candidate, parsed)

    assert "Wiki title: 图遍历" in parsed.text
    assert "Graph Guide" in parsed.text
    assert "large crawler body" not in parsed.text
    assert chunks == [parsed.text]


def test_dry_run_never_calls_embedder_or_database(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "sources.json"
    config_path.write_text(
        json.dumps(
            {
                "defaults": {"domain": "COMPUTER_SCIENCE", "resourceType": "READING", "difficulty": "BASIC"},
                "sources": [
                    {
                        "id": "docs",
                        "sourceName": "Docs",
                        "sourceType": "urls",
                        "resources": [{"title": "Docs", "url": "https://example.test/docs"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=b"<h1>Docs</h1><p>" + b"content " * 200 + b"</p>",
            request=request,
        )

    original_client = httpx.Client

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self._client = original_client(transport=httpx.MockTransport(handler), follow_redirects=True)

        def __enter__(self):
            return self._client

        def __exit__(self, exc_type, exc, tb):
            self._client.close()

    monkeypatch.setattr(import_external_resources.httpx, "Client", FakeClient)

    def fail_embedder(*args, **kwargs):
        raise AssertionError("dry-run must not call embeddings")

    def fail_db_factory():
        raise AssertionError("dry-run must not open database")

    stats = import_external_resources.import_resources(
        config_path=config_path,
        limit=1,
        rag_limit=1,
        metadata_only=False,
        require_embeddings=True,
        dry_run=True,
        timeout_seconds=1,
        max_bytes=100000,
        access_workers=2,
        preflight_embeddings=True,
        embedder=fail_embedder,
        db_factory=fail_db_factory,
    )

    assert stats.discovered == 1
    assert stats.accessible == 1
    assert stats.inserted_metadata == 1
    assert stats.rag_ingested == 0
    assert stats.skipped_rag == 1
    assert stats.source_counts == {"docs": 1}


def test_import_preflight_failure_happens_before_database_open(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "sources.json"
    config_path.write_text(
        json.dumps(
            {
                "defaults": {"domain": "COMPUTER_SCIENCE", "resourceType": "READING", "difficulty": "BASIC"},
                "sources": [
                    {
                        "id": "docs",
                        "sourceName": "Docs",
                        "sourceType": "urls",
                        "resources": [{"title": "Docs", "url": "https://example.test/docs"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    class FakeSettings:
        effective_embedding_api_key = "key"
        knowledge_embedding_model_name = "qwen3-vl-embedding"
        knowledge_embedding_dimension = 1024
        knowledge_embedding_timeout_seconds = 1.0

    monkeypatch.setattr(import_external_resources, "get_settings", lambda: FakeSettings())

    def fail_embedder(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("preflight failed")

    def fail_db_factory():
        raise AssertionError("preflight failure must not open database")

    with pytest.raises(RuntimeError, match="preflight failed"):
        import_external_resources.import_resources(
            config_path=config_path,
            limit=1,
            rag_limit=1,
            metadata_only=False,
            require_embeddings=True,
            dry_run=False,
            timeout_seconds=1,
            max_bytes=100000,
            preflight_embeddings=True,
            embedder=fail_embedder,
            db_factory=fail_db_factory,
        )


def test_filter_existing_candidates_uses_stable_resource_ids() -> None:
    class RecordingCursor:
        def __init__(self) -> None:
            self.params = None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, params):
            del sql
            self.params = params

        def fetchall(self):
            return [(import_external_resources.resource_uuid("https://example.test/exists"),)]

    class RecordingConnection:
        def __init__(self) -> None:
            self.cursor_obj = RecordingCursor()

        def cursor(self):
            return self.cursor_obj

    candidates = [
        import_external_resources.ResourceCandidate(
            source_id="docs",
            source_name="Docs",
            url="https://example.test/exists",
            title="Exists",
            domain="COMPUTER_SCIENCE",
            resource_type="READING",
            display_type="DOCUMENT",
            difficulty="MIXED",
            license="",
            copyright_status="OFFICIAL_PUBLIC_DOCUMENTATION",
            tags=("docs",),
            quality_score=0.8,
            popularity_score=0.5,
        ),
        import_external_resources.ResourceCandidate(
            source_id="docs",
            source_name="Docs",
            url="https://example.test/new",
            title="New",
            domain="COMPUTER_SCIENCE",
            resource_type="READING",
            display_type="DOCUMENT",
            difficulty="MIXED",
            license="",
            copyright_status="OFFICIAL_PUBLIC_DOCUMENTATION",
            tags=("docs",),
            quality_score=0.8,
            popularity_score=0.5,
        ),
    ]

    remaining = import_external_resources.filter_existing_candidates(RecordingConnection(), candidates)

    assert [candidate.url for candidate in remaining] == ["https://example.test/new"]


def test_filter_existing_candidates_matches_legacy_wiki_slug_and_url() -> None:
    class RecordingCursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, params):
            del params
            self.sql = sql

        def fetchall(self):
            if "metadata_json ->> 'wikiSlug'" in self.sql:
                return [("algorithms/graph", "wiki://algorithms/graph", "https://example.test/shared", "https://example.test/shared/")]
            return []

    class RecordingConnection:
        def cursor(self):
            return RecordingCursor()

    def candidate(slug: str) -> import_external_resources.ResourceCandidate:
        return import_external_resources.ResourceCandidate(
            source_id=f"wiki-{slug}",
            source_name="Wiki Resource Importer",
            url="https://example.test/shared",
            title="Shared",
            domain="COMPUTER_SCIENCE",
            resource_type="READING",
            display_type="DOCUMENT",
            difficulty="MIXED",
            license="Public web page",
            copyright_status="PUBLICLY_ACCESSIBLE_WEB_RESOURCE",
            tags=("shared",),
            quality_score=0.8,
            popularity_score=0.5,
            wiki_slug=slug,
            wiki_source_ref=f"wiki://{slug}",
            metadata_text=f"Wiki slug: {slug}",
        )

    remaining = import_external_resources.filter_existing_candidates(
        RecordingConnection(),
        [candidate("algorithms/graph"), candidate("data-structures/tree")],
    )

    assert [item.wiki_slug for item in remaining] == ["data-structures/tree"]


def test_filter_rag_missing_candidates_skips_documented_resources() -> None:
    class RecordingCursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, params):
            del sql, params

        def fetchall(self):
            return [(import_external_resources.resource_uuid("https://example.test/rag-ready"),)]

    class RecordingConnection:
        def cursor(self):
            return RecordingCursor()

    def candidate(url: str) -> import_external_resources.ResourceCandidate:
        return import_external_resources.ResourceCandidate(
            source_id="docs",
            source_name="Docs",
            url=url,
            title=url.rsplit("/", 1)[-1],
            domain="COMPUTER_SCIENCE",
            resource_type="READING",
            display_type="DOCUMENT",
            difficulty="MIXED",
            license="",
            copyright_status="OFFICIAL_PUBLIC_DOCUMENTATION",
            tags=("docs",),
            quality_score=0.8,
            popularity_score=0.5,
        )

    remaining = import_external_resources.filter_rag_missing_candidates(
        RecordingConnection(),
        [candidate("https://example.test/rag-ready"), candidate("https://example.test/rag-missing")],
    )

    assert [item.url for item in remaining] == ["https://example.test/rag-missing"]


def test_wiki_bound_resource_identity_includes_slug_for_shared_urls() -> None:
    base = {
        "source_id": "wiki-shared",
        "source_name": "Wiki Resource Importer",
        "url": "https://example.test/shared",
        "title": "Shared Guide",
        "domain": "COMPUTER_SCIENCE",
        "resource_type": "READING",
        "display_type": "DOCUMENT",
        "difficulty": "MIXED",
        "license": "Public web page",
        "copyright_status": "PUBLICLY_ACCESSIBLE_WEB_RESOURCE",
        "tags": ("shared",),
        "quality_score": 0.84,
        "popularity_score": 0.5,
        "metadata_text": "Wiki title: Shared",
    }
    graph = import_external_resources.ResourceCandidate(**base, wiki_slug="algorithms/graph")
    tree = import_external_resources.ResourceCandidate(**base, wiki_slug="data-structures/tree")
    plain = import_external_resources.ResourceCandidate(**base)

    assert import_external_resources.candidate_dedupe_key(graph) != import_external_resources.candidate_dedupe_key(tree)
    assert import_external_resources.candidate_resource_uuid(graph) != import_external_resources.candidate_resource_uuid(tree)
    assert import_external_resources.candidate_resource_uuid(plain) == import_external_resources.resource_uuid(plain.url)


def test_round_robin_keeps_shared_url_for_different_wiki_pages() -> None:
    config = {
        "defaults": {"domain": "COMPUTER_SCIENCE", "resourceType": "READING", "difficulty": "MIXED"},
        "sources": [
            {
                "id": "wiki-a",
                "sourceName": "Wiki Resource Importer",
                "sourceType": "urls",
                "resources": [
                    {
                        "title": "Shared Guide",
                        "url": "https://example.test/shared",
                        "wikiSlug": "algorithms/graph",
                        "wikiSourceRef": "wiki://algorithms/graph",
                        "metadataText": "Wiki title: Graph",
                    }
                ],
            },
            {
                "id": "wiki-b",
                "sourceName": "Wiki Resource Importer",
                "sourceType": "urls",
                "resources": [
                    {
                        "title": "Shared Guide",
                        "url": "https://example.test/shared",
                        "wikiSlug": "data-structures/tree",
                        "wikiSourceRef": "wiki://data-structures/tree",
                        "metadataText": "Wiki title: Tree",
                    }
                ],
            },
        ],
    }
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, request=request)))

    candidates = import_external_resources.iter_candidates(config, client, limit=10)

    assert [candidate.wiki_slug for candidate in candidates] == ["algorithms/graph", "data-structures/tree"]
