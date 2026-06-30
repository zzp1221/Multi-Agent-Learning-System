---
title: Raw Sources README
tags: [llm-wiki, raw-sources]
updated_at: 2026-06-29
---

# Raw Sources

Raw sources are the immutable source-of-truth layer for the LLM-Wiki. They are not compiled concept pages and must not be imported into RAG as wiki pages.

Recommended layout:

- `sources/`: source notes created from [[templates/source_note_template]].
- `assets/`: local images, PDFs, screenshots, or diagrams referenced by source notes.
- [[source_registry]]: source IDs and metadata.

Rules:

- Never silently rewrite an existing source note to change historical meaning.
- Add a new versioned source note if the source changes materially.
- Link compiled pages back to source IDs when possible.
