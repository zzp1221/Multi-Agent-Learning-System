"""
Parse wiki/*.md files and write data to PostgreSQL rag.wiki_page and rag.wiki_link tables.
Embedding generation (calling 讯飞 MaaS API) is handled separately.
Usage: python import_wiki_to_db.py [--dry-run]
"""
import sys
import os
import re
import uuid
import json
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict

import psycopg2
from settings_helper import configure_dashscope_api_key
from graph_low_evidence_repairs import build_repair_wikilinks, load_repair_link_records

RUNTIME_CONFIG = configure_dashscope_api_key()
from psycopg2.extras import execute_values

# ── Config ──────────────────────────────────────────────────
DB_CONFIG = RUNTIME_CONFIG.postgres.model_dump()
_WIKI_ROOT_CANDIDATES = [
    Path(__file__).parent.parent.parent / "wiki",
    Path(__file__).parent.parent / "wiki",
]
WIKI_ROOT = next((path for path in _WIKI_ROOT_CANDIDATES if path.exists()), _WIKI_ROOT_CANDIDATES[0])


# ── Frontmatter Parser ───────────────────────────────────────
def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML-like frontmatter. Returns (meta, body)."""
    if not text.startswith("---\n"):
        return {}, text
    parts = text[4:].split("\n---\n", 1)
    if len(parts) != 2:
        return {}, text
    raw, body = parts
    meta = {}
    list_key = None
    list_values = []
    for line in raw.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("- ") and list_key:
            list_values.append(line[2:].strip())
            continue
        if list_key and list_values:
            meta[list_key] = list_values
            list_key = None
            list_values = []
        m = re.match(r"^(\w+):\s*(.*)", line)
        if not m:
            continue
        key = m.group(1)
        val = m.group(2).strip()
        if val:
            meta[key] = val
        else:
            list_key = key
            list_values = []
    if list_key and list_values:
        meta[list_key] = list_values
    # normalize
    tags_str = meta.get("tags", "")
    if isinstance(tags_str, str) and tags_str.startswith("[") and tags_str.endswith("]"):
        meta["tags"] = [t.strip() for t in tags_str[1:-1].split(",") if t.strip()]
    elif isinstance(tags_str, str):
        meta["tags"] = [t.strip() for t in tags_str.split(",") if t.strip()]
    aliases_str = meta.get("aliases", "")
    if isinstance(aliases_str, str) and aliases_str.startswith("[") and aliases_str.endswith("]"):
        meta["aliases"] = [t.strip() for t in aliases_str[1:-1].split(",") if t.strip()]
    elif isinstance(aliases_str, str):
        meta["aliases"] = [t.strip() for t in aliases_str.split(",") if t.strip()]
    return meta, body.strip()


def extract_wikilinks(body: str) -> list[str]:
    """Extract [[page title]] links from body text."""
    return re.findall(r"\[\[([^\]]+)\]\]", body)


def extract_summary(body: str, max_chars: int = 300) -> str:
    """Extract first meaningful paragraph as summary."""
    for para in body.split("\n\n"):
        para = para.strip()
        # skip headings
        if para.startswith("#"):
            continue
        if len(para) > 20:
            if len(para) > max_chars:
                return para[:max_chars] + "..."
            return para
    return body[:max_chars] if len(body) > max_chars else body


def make_slug(fpath: Path, wiki_root: Path) -> str:
    """Create a URL-friendly slug from the relative file path."""
    rel = str(fpath.relative_to(wiki_root))
    # remove .md extension, replace backslashes with forward slashes
    slug = rel.replace("\\", "/").replace(".md", "")
    return slug


# ── DB Operations ────────────────────────────────────────────
def connect():
    return psycopg2.connect(**DB_CONFIG)


def clear_knowledge_data(cur):
    """Remove existing wiki data (preserve schema)."""
    cur.execute("DELETE FROM rag.wiki_link")
    cur.execute("DELETE FROM rag.wiki_page")
    cur.execute("DELETE FROM rag.knowledge_chunk")
    cur.execute("DELETE FROM rag.knowledge_document")
    print("  Cleared existing wiki data")


def insert_wiki_pages(cur, pages: list[dict]) -> dict[str, str]:
    """Insert wiki_page rows. Returns {title: page_id} mapping."""
    if not pages:
        return {}
    rows = []
    for p in pages:
        rows.append((
            p["id"],
            p["slug"],
            p["title"],
            p["domain"],
            p.get("difficulty", "BASIC").upper(),
            json.dumps(p.get("aliases", []), ensure_ascii=False),
            json.dumps(p.get("tags", []), ensure_ascii=False),
            json.dumps(p.get("source", []), ensure_ascii=False),
            json.dumps(p.get("frontmatter", {}), ensure_ascii=False),
            p.get("summary", ""),
            p.get("body", ""),
            1,  # version
        ))
    sql = """
        INSERT INTO rag.wiki_page (id, slug, title, domain, difficulty_level,
            aliases, tags, source_refs, frontmatter_json, summary_text, markdown_content, version)
        VALUES %s
        ON CONFLICT (slug, version) DO UPDATE SET
            title = EXCLUDED.title,
            domain = EXCLUDED.domain,
            difficulty_level = EXCLUDED.difficulty_level,
            aliases = EXCLUDED.aliases,
            tags = EXCLUDED.tags,
            source_refs = EXCLUDED.source_refs,
            frontmatter_json = EXCLUDED.frontmatter_json,
            summary_text = EXCLUDED.summary_text,
            markdown_content = EXCLUDED.markdown_content,
            updated_at = now()
    """
    execute_values(cur, sql, rows)
    # re-fetch the page IDs to get the mapping
    cur.execute("SELECT title, id FROM rag.wiki_page WHERE is_active = true")
    return {row[0]: str(row[1]) for row in cur.fetchall()}


def insert_wiki_links(cur, links: list[dict], title_to_id: dict[str, str]):
    """Insert wiki_link rows for WIKILINK and SHARED_TAG relations."""
    if not links:
        return
    rows = []
    seen = set()
    for link in links:
        from_id = title_to_id.get(link["from_title"])
        to_id = title_to_id.get(link["to_title"])
        if not from_id or not to_id:
            continue
        rel = link["relation"]
        key = (from_id, to_id, rel)
        if key in seen:
            continue
        seen.add(key)
        rows.append((from_id, to_id, rel, link.get("weight", 1)))
    if not rows:
        print("  Inserted 0 wiki links")
        return
    sql = """
        INSERT INTO rag.wiki_link (from_page_id, to_page_id, relation_type, weight)
        VALUES %s
        ON CONFLICT (from_page_id, to_page_id, relation_type) DO NOTHING
    """
    execute_values(cur, sql, rows)
    print(f"  Inserted {len(rows)} wiki links")


def build_shared_tag_links(pages: list[dict], title_to_id: dict[str, str]) -> list[dict]:
    """Generate SHARED_TAG links for pages sharing >= 2 tags."""
    tag_to_pages = defaultdict(set)
    for p in pages:
        for tag in (p.get("tags") or []):
            tag_to_pages[tag].add(p["title"])
    links = []
    pairs_seen = set()
    for tag, titles in tag_to_pages.items():
        titles = sorted(titles)
        for i in range(len(titles)):
            for j in range(i + 1, len(titles)):
                pair = (titles[i], titles[j])
                if pair in pairs_seen:
                    continue
                pairs_seen.add(pair)
                links.append({
                    "from_title": titles[i],
                    "to_title": titles[j],
                    "relation": "SHARED_TAG",
                    "weight": 1,
                })
    return links


# ── Main ─────────────────────────────────────────────────────
def main():
    dry_run = "--dry-run" in sys.argv
    incremental = "--incremental" in sys.argv
    print("=" * 60)
    print("Wiki → PostgreSQL Import" + (" (INCREMENTAL)" if incremental else ""))
    print("=" * 60)

    md_files = sorted(WIKI_ROOT.rglob("*.md"))
    print(f"\nFound {len(md_files)} .md files")

    pages = []
    for fpath in md_files:
        raw = fpath.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(raw)
        if not meta.get("title"):
            print(f"  SKIP (no title): {fpath.relative_to(WIKI_ROOT)}")
            continue
        title = meta["title"]
        wikilinks_raw = extract_wikilinks(body)
        wikilinks = list(dict.fromkeys([w.strip() for w in wikilinks_raw if w.strip()]))

        # Build frontmatter_json: keep all meta except the ones mapped to dedicated columns
        dedicated_keys = {"title", "course", "chapter", "difficulty", "tags", "aliases", "source"}
        extra_meta = {k: v for k, v in meta.items() if k not in dedicated_keys}

        pages.append({
            "id": str(uuid.uuid4()),
            "slug": make_slug(fpath, WIKI_ROOT),
            "title": title,
            "domain": "COMPUTER_SCIENCE",
            "difficulty": meta.get("difficulty", "BASIC"),
            "aliases": meta.get("aliases", []),
            "tags": meta.get("tags", []),
            "source": meta.get("source", []),
            "frontmatter": {
                "course": meta.get("course", ""),
                "chapter": meta.get("chapter", ""),
                **extra_meta,
            },
            "summary": extract_summary(body),
            "body": body,
            "wikilinks": wikilinks,
            "file": str(fpath.relative_to(WIKI_ROOT)),
        })
        rel_path = str(fpath.relative_to(WIKI_ROOT))
        print(f"  {rel_path:50s}  [{title}]  tags={len(meta.get('tags', []))}  wikilinks={len(wikilinks)}")

    print(f"\nParsed {len(pages)} valid pages")

    # Build wiki_link records
    wikilink_records = []
    for p in pages:
        for target_title in p["wikilinks"]:
            wikilink_records.append({
                "from_title": p["title"],
                "to_title": target_title,
                "relation": "WIKILINK",
                "weight": 1,
            })
    repair_records = load_repair_link_records()
    repair_wikilinks = build_repair_wikilinks(repair_records, pages=pages)
    wikilink_records.extend(repair_wikilinks)
    if repair_wikilinks:
        print(f"  Added {len(repair_wikilinks)} graph low-evidence repair WIKILINK(s)")

    if dry_run:
        print(f"\n[DRY RUN] Would write {len(pages)} page(s) and {len(wikilink_records)} WIKILINK(s) to PostgreSQL")
        print("Done (dry-run).")
        return

    conn = connect()
    try:
        with conn:
            with conn.cursor() as cur:
                if incremental:
                    # Incremental: only insert new pages, skip existing slugs
                    cur.execute("SELECT slug FROM rag.wiki_page WHERE is_active = true")
                    existing_slugs = {row[0] for row in cur.fetchall()}
                    new_pages = [p for p in pages if p["slug"] not in existing_slugs]
                    print(f"\nIncremental: {len(existing_slugs)} existing, {len(new_pages)} new pages to insert")
                    pages = new_pages
                else:
                    clear_knowledge_data(cur)

                print(f"\nWriting {len(pages)} wiki pages...")
                title_to_id = insert_wiki_pages(cur, pages)
                print(f"  OK: {len(title_to_id)} pages inserted")

                full_title_to_id = title_to_id
                if incremental:
                    cur.execute("SELECT title, id FROM rag.wiki_page WHERE is_active = true")
                    full_title_to_id = {row[0]: str(row[1]) for row in cur.fetchall()}

                # WIKILINK relations
                print(f"Writing {len(wikilink_records)} WIKILINK relations...")
                insert_wiki_links(cur, wikilink_records, title_to_id)
                if incremental and repair_records:
                    cur.execute("SELECT slug, title FROM rag.wiki_page WHERE is_active = true")
                    full_slug_to_title = {row[0]: row[1] for row in cur.fetchall()}
                    repair_wikilinks = build_repair_wikilinks(repair_records, slug_to_title=full_slug_to_title)
                    print(f"Writing {len(repair_wikilinks)} incremental graph repair WIKILINK relations...")
                    insert_wiki_links(cur, repair_wikilinks, full_title_to_id)

                # SHARED_TAG relations
                if incremental:
                    # Include existing pages for cross-linking
                    cur.execute("SELECT id, title, tags FROM rag.wiki_page WHERE is_active = true")
                    all_pages_for_tags = []
                    for row in cur.fetchall():
                        pid, ptitle, ptags = row
                        all_pages_for_tags.append({"title": ptitle, "tags": ptags if isinstance(ptags, list) else (ptags or [])})
                    st_links = build_shared_tag_links(all_pages_for_tags, full_title_to_id)
                else:
                    st_links = build_shared_tag_links(pages, title_to_id)
                print(f"Writing {len(st_links)} SHARED_TAG relations...")
                insert_wiki_links(cur, st_links, title_to_id if not incremental else full_title_to_id)

        print("\nAll wiki metadata written to PostgreSQL")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
