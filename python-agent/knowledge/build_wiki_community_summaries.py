"""Build offline wiki community summaries from Markdown files.

This is a deterministic, no-LLM asset builder. It prepares global/course-level
summary data for later GraphRAG-style global retrieval experiments without
changing the online retrieval path.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

try:
    from knowledge.wiki_file_filter import iter_content_wiki_markdown
except ModuleNotFoundError:
    from wiki_file_filter import iter_content_wiki_markdown


DEFAULT_WIKI_ROOT = Path(__file__).resolve().parents[2] / "wiki"
DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "reports" / "wiki_community_summaries.json"


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text
    parts = text[4:].split("\n---\n", 1)
    if len(parts) != 2:
        return {}, text
    raw, body = parts
    meta: dict[str, Any] = {}
    list_key = None
    list_values: list[str] = []
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("- ") and list_key:
            list_values.append(line[2:].strip().strip('"'))
            continue
        if list_key is not None:
            meta[list_key] = list_values
            list_key = None
            list_values = []
        match = re.match(r"^(\w+):\s*(.*)$", line)
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        if value:
            meta[key] = _parse_frontmatter_value(value)
        else:
            list_key = key
            list_values = []
    if list_key is not None:
        meta[list_key] = list_values
    return meta, body.strip()


def load_wiki_pages(wiki_root: Path | str = DEFAULT_WIKI_ROOT) -> list[dict[str, Any]]:
    root = Path(wiki_root)
    pages = []
    for path in iter_content_wiki_markdown(root):
        raw = path.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(raw)
        title = str(meta.get("title") or path.stem).strip().strip('"')
        slug = path.relative_to(root).with_suffix("").as_posix()
        pages.append(
            {
                "slug": slug,
                "title": title,
                "course": str(meta.get("course") or path.parent.name).strip().strip('"'),
                "chapter": str(meta.get("chapter") or "").strip().strip('"'),
                "difficulty": str(meta.get("difficulty") or "MIXED").strip().strip('"'),
                "tags": _string_list(meta.get("tags")),
                "aliases": _string_list(meta.get("aliases")),
                "summary": extract_summary(body),
                "wikilinks": extract_wikilinks(body),
            }
        )
    return pages


def build_community_summaries(pages: list[dict[str, Any]], *, representative_limit: int = 8) -> dict[str, Any]:
    by_course: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for page in pages:
        by_course[str(page.get("course") or "UNKNOWN")].append(page)

    communities = []
    for course, course_pages in sorted(by_course.items()):
        tag_counts = Counter(tag for page in course_pages for tag in page.get("tags", []))
        chapter_counts = Counter(str(page.get("chapter") or "") for page in course_pages if page.get("chapter"))
        representative_pages = sorted(
            course_pages,
            key=lambda page: (
                len(page.get("wikilinks", [])) + len(page.get("tags", [])),
                len(str(page.get("summary") or "")),
                str(page.get("slug") or ""),
            ),
            reverse=True,
        )[:representative_limit]
        key_tags = [tag for tag, _count in tag_counts.most_common(12)]
        chapters = [chapter for chapter, _count in chapter_counts.most_common(12)]
        communities.append(
            {
                "communityId": _community_id(course),
                "course": course,
                "pageCount": len(course_pages),
                "chapterCount": len(chapter_counts),
                "keyTags": key_tags,
                "chapters": chapters,
                "summaryText": build_summary_text(course, len(course_pages), chapters, key_tags),
                "representativePages": [
                    {
                        "slug": page["slug"],
                        "title": page["title"],
                        "chapter": page.get("chapter") or "",
                        "tags": page.get("tags", [])[:8],
                        "wikilinkCount": len(page.get("wikilinks", [])),
                    }
                    for page in representative_pages
                ],
            }
        )

    return {
        "version": 1,
        "source": "wiki_markdown",
        "pageCount": len(pages),
        "communityCount": len(communities),
        "communities": communities,
    }


def build_summary_text(course: str, page_count: int, chapters: list[str], key_tags: list[str]) -> str:
    chapter_text = "、".join(chapters[:5]) if chapters else "未分章节"
    tag_text = "、".join(key_tags[:8]) if key_tags else "暂无标签"
    return f"{course} contains {page_count} wiki pages. Main chapters: {chapter_text}. Key tags: {tag_text}."


def write_summary_file(output: Path | str, payload: dict[str, Any]) -> None:
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def extract_wikilinks(body: str) -> list[str]:
    return list(dict.fromkeys(link.strip() for link in re.findall(r"\[\[([^\]]+)\]\]", body) if link.strip()))


def extract_summary(body: str, max_chars: int = 300) -> str:
    for paragraph in body.split("\n\n"):
        stripped = paragraph.strip()
        if not stripped or stripped.startswith("#"):
            continue
        return stripped[:max_chars]
    return body.strip()[:max_chars]


def _parse_frontmatter_value(value: str) -> Any:
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        return [item.strip().strip('"') for item in value[1:-1].split(",") if item.strip()]
    return value.strip('"')


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip().strip('"') for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip().strip('"') for item in value.split(",") if item.strip()]
    return []


def _community_id(course: str) -> str:
    return re.sub(r"[\s/\\]+", "-", course.strip().lower()) or "unknown"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build offline wiki community summaries.")
    parser.add_argument("--wiki-root", default=str(DEFAULT_WIKI_ROOT))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()

    pages = load_wiki_pages(args.wiki_root)
    payload = build_community_summaries(pages)
    write_summary_file(args.output, payload)
    print(f"Wrote {payload['communityCount']} communitiy summary record(s) from {payload['pageCount']} page(s)")


if __name__ == "__main__":
    main()
