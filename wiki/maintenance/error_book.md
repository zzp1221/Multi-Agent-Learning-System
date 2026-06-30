---
title: LLM-Wiki Error Book
tags: [llm-wiki, error-book, repair]
updated_at: 2026-06-29
---

# LLM-Wiki Error Book

This file tracks structural and semantic issues that should compound into future repairs.

Use one section per issue:

```markdown
## [YYYY-MM-DD] status | issue-type | Short title
- Status: open | fixed | rejected
- Evidence:
- Affected pages:
- Proposed repair:
- Verification:
```

## [2026-06-29] open | provenance | Existing pages mostly use free-text source refs

- Status: open
- Evidence: Existing course pages commonly cite books or docs in `source`, but many claims do not yet map to stable `source_id` values.
- Affected pages: Broadly across course folders.
- Proposed repair: Gradually register high-value sources in [[raw/source_registry]] and backfill `source_ids` for pages used by RAG benchmarks.
- Verification: Sample pages expose stable source IDs in frontmatter or `## 来源`.
