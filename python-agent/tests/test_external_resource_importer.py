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


def test_html_parser_ignores_scripts_and_builds_summary() -> None:
    parsed = import_external_resources.parse_html_document(
        b"<html><head><title>Fallback</title><script>bad()</script></head>"
        b"<body><h1>Python Tutorial</h1><p>Learn syntax and control flow.</p></body></html>"
    )

    assert parsed.title == "Python Tutorial"
    assert "bad()" not in parsed.text
    assert "Learn syntax" in parsed.summary


def test_generate_embeddings_refuses_missing_api_key() -> None:
    with pytest.raises(RuntimeError, match="embedding API key"):
        import_external_resources.generate_embeddings(["hello"], api_key="", model="qwen3-vl-embedding", dimension=1024)


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
        embedder=fail_embedder,
        db_factory=fail_db_factory,
    )

    assert stats.discovered == 1
    assert stats.accessible == 1
    assert stats.inserted_metadata == 1
    assert stats.rag_ingested == 0
    assert stats.skipped_rag == 1
    assert stats.source_counts == {"docs": 1}


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
