"""
Vectorize wiki pages using the configured embedding model.
Generates 1024-dim embeddings and writes to rag.knowledge_document + rag.knowledge_chunk.
Usage: python vectorize_wiki.py [--dry-run] [--limit N]
"""
import sys
import os
import uuid
import json
import time
import hashlib
import re
from dataclasses import dataclass

import psycopg2
from dashscope import MultiModalEmbedding
try:
    from settings_helper import configure_dashscope_api_key
except ModuleNotFoundError:
    from knowledge.settings_helper import configure_dashscope_api_key

RUNTIME_CONFIG = None


# 鈹€鈹€ Config 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
DIMENSION = 1024
BATCH_SIZE = 5  # texts per API call
API_DELAY = 0.3  # seconds between batches
MAX_CHUNK_TOKENS = 420


@dataclass(slots=True)
class MarkdownBlock:
    kind: str
    text: str
    level: int = 0


# 鈹€鈹€ DB 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
def connect():
    return psycopg2.connect(**get_runtime_config().postgres.model_dump())


def get_runtime_config():
    """Load runtime config lazily so pure chunking tests do not need API keys."""
    global RUNTIME_CONFIG
    if RUNTIME_CONFIG is None:
        RUNTIME_CONFIG = configure_dashscope_api_key()
    return RUNTIME_CONFIG


def fetch_wiki_pages(cur, limit: int = None) -> list[dict]:
    """Fetch wiki pages that haven't been vectorized yet."""
    sql = """
        SELECT w.id, w.slug, w.title, w.markdown_content, w.difficulty_level::text,
               w.tags, w.source_refs, w.frontmatter_json, w.summary_text
        FROM rag.wiki_page w
        WHERE w.is_active = true
        AND NOT EXISTS (
            SELECT 1 FROM rag.knowledge_document kd
            WHERE kd.external_doc_id = w.id::text AND kd.source_type = 'md'
        )
        ORDER BY w.slug
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    cur.execute(sql)
    rows = cur.fetchall()
    return [
        {
            "wiki_id": str(r[0]),
            "slug": r[1],
            "title": r[2],
            "content": r[3],
            "difficulty": r[4] or "MIXED",
            "tags": r[5] or [],
            "source_refs": r[6] or [],
            "frontmatter": r[7] or {},
            "summary": r[8] or "",
        }
        for r in rows
    ]


def clear_vectorized_data(cur):
    """Remove vectorized data for re-run."""
    cur.execute("DELETE FROM rag.knowledge_chunk")
    cur.execute("DELETE FROM rag.knowledge_document")
    print("  Cleared existing vectorized data")


def insert_knowledge_document(cur, doc: dict) -> str:
    """Insert a single knowledge_document row. Returns the document id."""
    cur.execute("""
        INSERT INTO rag.knowledge_document (id, title, domain, source_type, source_ref,
            external_doc_id, content_hash, difficulty_level, access_scope, tags, metadata_json,
            created_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s::app.difficulty_level, %s::app.access_scope, %s, %s, %s)
        ON CONFLICT (course_id, domain, external_doc_id, version) DO UPDATE SET
            title = EXCLUDED.title,
            content_hash = EXCLUDED.content_hash,
            tags = EXCLUDED.tags,
            metadata_json = EXCLUDED.metadata_json,
            updated_at = now()
        RETURNING id
    """, (
        doc["id"],
        doc["title"],
        doc["domain"],
        doc["source_type"],
        doc["source_ref"],
        doc["external_doc_id"],
        doc["content_hash"],
        doc["difficulty"],
        doc["access_scope"],
        json.dumps(doc["tags"], ensure_ascii=False),
        json.dumps(doc["metadata"], ensure_ascii=False),
        doc["created_by"],
    ))
    return str(cur.fetchone()[0])


def insert_knowledge_chunks(cur, chunks: list[dict]):
    """Insert knowledge_chunk rows (batch)."""
    for c in chunks:
        cur.execute("""
            INSERT INTO rag.knowledge_chunk (document_id, chunk_no, content, embedding,
                token_count, domain, difficulty_level, access_scope, quality_score, metadata_json)
            VALUES (%s, %s, %s, %s, %s, %s, %s::app.difficulty_level, %s::app.access_scope, %s, %s)
            ON CONFLICT (document_id, chunk_no) DO UPDATE SET
                content = EXCLUDED.content,
                embedding = EXCLUDED.embedding,
                token_count = EXCLUDED.token_count,
                quality_score = EXCLUDED.quality_score,
                metadata_json = EXCLUDED.metadata_json
        """, (
            c["document_id"],
            c["chunk_no"],
            c["content"],
            c["embedding"],
            c["token_count"],
            c["domain"],
            c["difficulty"],
            c["access_scope"],
            c["quality_score"],
            json.dumps(c["metadata"], ensure_ascii=False),
        ))


# 鈹€鈹€ Embedding 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
def generate_embeddings(texts: list[str], dimension: int | None = None) -> list[list[float]]:
    """Generate embeddings for multiple texts via DashScope API."""
    runtime_config = get_runtime_config()
    dimension = dimension or runtime_config.embedding_dimension
    input_data = [{"text": t} for t in texts]
    resp = MultiModalEmbedding.call(
        model=runtime_config.embedding_model_name,
        input=input_data,
        dimension=dimension,
        output_type="dense",
    )
    if resp.status_code != 200:
        raise RuntimeError(f"API error: code={resp.code} message={resp.message}")

    emb_list = resp.output.get("embeddings", [])
    if not emb_list:
        raise RuntimeError(f"No embeddings in response: {resp.output}")

    # Sort by index to maintain order
    emb_list.sort(key=lambda x: x.get("index", 0))
    return [e["embedding"] for e in emb_list]


def estimate_tokens(text: str) -> int:
    """Rough token estimation for mixed Chinese/English text."""
    # Chinese chars ~1.5 per token, others ~4 per token
    chinese = sum(1 for c in text if ord(c) > 0x2000)
    other = len(text) - chinese
    return int(chinese / 1.5 + other / 4)


def build_embedding_str(vec: list[float]) -> str:
    """Format a float list as pgvector-compatible string."""
    return "[" + ",".join(str(v) for v in vec) + "]"


# 鈹€鈹€ Main 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
def split_markdown_blocks(markdown: str) -> list[MarkdownBlock]:
    """Split markdown into heading and paragraph/code blocks."""
    blocks: list[MarkdownBlock] = []
    paragraph: list[str] = []
    code_block: list[str] = []
    in_code = False

    def flush_paragraph() -> None:
        if paragraph:
            text = "\n".join(paragraph).strip()
            if text:
                blocks.append(MarkdownBlock(kind="paragraph", text=text))
            paragraph.clear()

    for raw_line in str(markdown or "").replace("\r\n", "\n").split("\n"):
        line = raw_line.rstrip()
        if line.strip().startswith("```"):
            if in_code:
                code_block.append(line)
                text = "\n".join(code_block).strip()
                if text:
                    blocks.append(MarkdownBlock(kind="code", text=text))
                code_block.clear()
                in_code = False
            else:
                flush_paragraph()
                code_block = [line]
                in_code = True
            continue
        if in_code:
            code_block.append(line)
            continue
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match:
            flush_paragraph()
            blocks.append(
                MarkdownBlock(
                    kind="heading",
                    text=match.group(2).strip(),
                    level=len(match.group(1)),
                )
            )
            continue
        if not line.strip():
            flush_paragraph()
            continue
        paragraph.append(line)

    if in_code and code_block:
        blocks.append(MarkdownBlock(kind="code", text="\n".join(code_block).strip()))
    flush_paragraph()
    return blocks


def split_long_text(text: str, max_tokens: int = MAX_CHUNK_TOKENS) -> list[str]:
    """Split oversized paragraphs by sentence-like boundaries."""
    text = str(text or "").strip()
    if not text:
        return []
    if estimate_tokens(text) <= max_tokens:
        return [text]

    pieces = [piece.strip() for piece in re.split(r"(?<=[。！？.!?])\s*", text) if piece.strip()]
    if len(pieces) <= 1:
        step = max(200, max_tokens * 2)
        return [text[i : i + step].strip() for i in range(0, len(text), step) if text[i : i + step].strip()]

    chunks: list[str] = []
    current: list[str] = []
    for piece in pieces:
        candidate = "".join(current + [piece])
        if current and estimate_tokens(candidate) > max_tokens:
            chunks.append("".join(current).strip())
            current = [piece]
        else:
            current.append(piece)
    if current:
        chunks.append("".join(current).strip())
    return chunks


def markdown_chunks_for_page(page: dict, max_tokens: int = MAX_CHUNK_TOKENS) -> list[dict]:
    """Build readable chunks and embedding texts for one wiki page."""
    title = str(page.get("title") or "").strip()
    slug = str(page.get("slug") or "").strip()
    wiki_page_id = str(page.get("wiki_id") or "").strip()
    blocks = split_markdown_blocks(str(page.get("content") or ""))
    if not blocks:
        fallback = str(page.get("summary") or title).strip()
        blocks = [MarkdownBlock(kind="paragraph", text=fallback)]

    section_stack: list[tuple[int, str]] = []
    chunks: list[dict] = []
    current_parts: list[str] = []
    current_section_path: list[str] = []

    def active_section_path() -> list[str]:
        return [heading for _level, heading in section_stack]

    def flush_current() -> None:
        if not current_parts:
            return
        content = "\n\n".join(part.strip() for part in current_parts if part.strip()).strip()
        current_parts.clear()
        if not content:
            return
        section_path = list(current_section_path)
        chunk_index = len(chunks) + 1
        path_text = " > ".join(section_path)
        embedding_parts = [title]
        if path_text:
            embedding_parts.append(path_text)
        embedding_parts.append(content)
        chunks.append(
            {
                "chunk_no": chunk_index,
                "content": content,
                "embedding_text": "\n".join(part for part in embedding_parts if part),
                "token_count": estimate_tokens(content),
                "metadata": {
                    "wiki_page_id": wiki_page_id,
                    "slug": slug,
                    "section_path": section_path,
                    "chunk_index": chunk_index,
                    "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                },
            }
        )

    for block in blocks:
        if block.kind == "heading":
            flush_current()
            section_stack = [(level, text) for level, text in section_stack if level < block.level]
            section_stack.append((block.level, block.text))
            continue

        for text_part in split_long_text(block.text, max_tokens=max_tokens):
            candidate_parts = current_parts + [text_part]
            if current_parts and estimate_tokens("\n\n".join(candidate_parts)) > max_tokens:
                flush_current()
            if not current_parts:
                current_section_path = active_section_path()
            current_parts.append(text_part)
            if estimate_tokens(text_part) > max_tokens:
                flush_current()

    flush_current()
    return chunks


def main():
    dry_run = "--dry-run" in sys.argv
    incremental = "--incremental" in sys.argv
    limit = None
    for i, arg in enumerate(sys.argv):
        if arg == "--limit" and i + 1 < len(sys.argv):
            limit = int(sys.argv[i + 1])
    runtime_config = get_runtime_config()
    dimension = runtime_config.embedding_dimension

    print("=" * 60)
    print("Wiki Vectorization 鈫?PostgreSQL" + (" (INCREMENTAL)" if incremental else ""))
    print(f"Model: {runtime_config.embedding_model_name} | Dimension: {dimension}")
    print("=" * 60)

    if dry_run:
        print("\n[DRY RUN MODE]")

    conn = connect()
    try:
        with conn:
            with conn.cursor() as cur:
                if not dry_run and not incremental:
                    clear_vectorized_data(cur)

                pages = fetch_wiki_pages(cur, limit=limit)
                total = len(pages)
                print(f"\nPages to vectorize: {total}")

                if total == 0:
                    print("Already up to date.")
                    return

                # Process in batches
                for batch_start in range(0, total, BATCH_SIZE):
                    batch = pages[batch_start : batch_start + BATCH_SIZE]
                    batch_num = batch_start // BATCH_SIZE + 1
                    total_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE

                    status = ", ".join(p["title"][:20] for p in batch)
                    print(f"\n[Batch {batch_num}/{total_batches}] {len(batch)} pages: {status}...")

                    if dry_run:
                        chunk_count = sum(len(markdown_chunks_for_page(page)) for page in batch)
                        print(f"  (dry-run, skipping API call; would create {chunk_count} chunks)")
                        continue

                    # Write to DB
                    for i, page in enumerate(batch):
                        page_chunks = markdown_chunks_for_page(page)
                        if not page_chunks:
                            print(f"  SKIP (no chunks): {page['title']}")
                            continue

                        content = page["content"] or ""
                        doc = {
                            "id": str(uuid.uuid4()),
                            "title": page["title"],
                            "domain": "COMPUTER_SCIENCE",
                            "source_type": "md",
                            "source_ref": page["slug"],
                            "external_doc_id": page["wiki_id"],
                            "content_hash": hashlib.sha256(content.encode()).hexdigest(),
                            "difficulty": page["difficulty"],
                            "access_scope": "GLOBAL",
                            "tags": [json.dumps(page["tags"])] if isinstance(page["tags"], str) else page["tags"],
                            "metadata": {"wiki_page_id": page["wiki_id"], "slug": page["slug"]},
                            "created_by": "wiki_vectorizer",
                        }

                        pending_chunks = []
                        for chunk_start in range(0, len(page_chunks), BATCH_SIZE):
                            chunk_batch = page_chunks[chunk_start : chunk_start + BATCH_SIZE]
                            texts = [chunk["embedding_text"] for chunk in chunk_batch]
                            try:
                                embeddings = generate_embeddings(texts, dimension)
                            except Exception as e:
                                print(f"  API ERROR: {e}")
                                print("  Falling back to single-text mode...")
                                embeddings = []
                                for text in texts:
                                    try:
                                        emb = generate_embeddings([text], dimension)
                                        embeddings.extend(emb)
                                        time.sleep(API_DELAY)
                                    except Exception as e2:
                                        print(f"  FAILED: {e2}")
                                        embeddings.append(None)

                            for chunk_offset, chunk_data in enumerate(chunk_batch):
                                emb_vec = (
                                    embeddings[chunk_offset]
                                    if chunk_offset < len(embeddings) and embeddings[chunk_offset] is not None
                                    else None
                                )
                                if emb_vec is None:
                                    continue
                                pending_chunks.append(
                                    {
                                        "chunk_no": chunk_data["chunk_no"],
                                        "content": chunk_data["content"],
                                        "embedding": build_embedding_str(emb_vec),
                                        "token_count": chunk_data["token_count"],
                                        "domain": "COMPUTER_SCIENCE",
                                        "difficulty": page["difficulty"],
                                        "access_scope": "GLOBAL",
                                        "quality_score": 0.88,
                                        "metadata": chunk_data["metadata"],
                                    }
                                )

                        if pending_chunks:
                            doc_id = insert_knowledge_document(cur, doc)
                            for pending_chunk in pending_chunks:
                                pending_chunk["document_id"] = doc_id
                            insert_knowledge_chunks(cur, pending_chunks)

                        print(
                            f"  OK [{batch_start + i + 1}/{total}]: {page['title']}  "
                            f"chunks={len(pending_chunks)}/{len(page_chunks)}"
                        )

                    time.sleep(API_DELAY)

        print(f"\nAll done. {total} pages vectorized.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

