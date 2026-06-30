"""Shared filesystem filters for the local LLM-Wiki vault."""

from __future__ import annotations

from pathlib import Path

WIKI_META_FILES = {
    "index.md",
    "log.md",
    "schema.md",
    "README.md",
}

WIKI_META_DIRS = {
    "raw",
    "maintenance",
    "templates",
    "assets",
}


def is_content_wiki_markdown(path: Path, wiki_root: Path | str) -> bool:
    """Return True for course/content wiki pages that should enter RAG."""
    root = Path(wiki_root)
    try:
        rel = path.relative_to(root)
    except ValueError:
        return False
    if path.suffix.lower() != ".md":
        return False
    if len(rel.parts) == 1 and rel.name in WIKI_META_FILES:
        return False
    if rel.parts and rel.parts[0] in WIKI_META_DIRS:
        return False
    return True


def iter_content_wiki_markdown(wiki_root: Path | str) -> list[Path]:
    """List markdown pages that represent compiled wiki knowledge."""
    root = Path(wiki_root)
    return [path for path in sorted(root.rglob("*.md")) if is_content_wiki_markdown(path, root)]
