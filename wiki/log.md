---
title: LLM-Wiki Log
tags: [llm-wiki, log]
updated_at: 2026-06-29
---

# LLM-Wiki Log

Append entries using this format:

```markdown
## [YYYY-MM-DD] type | Short title
- Scope:
- Changes:
- Verification:
- Follow-up:
```

## [2026-06-29] structure | Bootstrap LLM-Wiki vault structure

- Scope: Added Karpathy-style LLM-Wiki control files, maintenance directories, source registry, templates, and RAG import boundary.
- Changes: Created root index/schema/log/README plus `raw/`, `maintenance/`, `templates/`, and `assets/` structure.
- Verification: Root maintenance files are excluded from wiki import and community summary builders by shared Python filter.
- Follow-up: Gradually assign stable `source_id` values to existing course pages and backfill claim-level provenance.
