---
name: security-run
description: Use when scanning one configured repository for high-confidence security vulnerabilities.
---

Read `references/CODEX-RUNTIME.md` and `references/PROVIDERS.md`, then read the canonical runtime, provider, project `AGENTS.md`, and the configured
security reference before scanning. Perform exactly one read-only pass over one
repository. Never read secrets, modify repository files, create GitLab work, or
claim a vulnerability without file/line evidence.

Record at most one proposal in the configured proposals ledger. Use category
`security`, severity `critical|high|medium|low`, a concise title and summary,
affected paths, impact, reproduction evidence, and a fix sketch. Deduplicate by
stable id. Critical and high findings remain `suggested` for dashboard review.
If configuration or the ledger is unavailable, return the structured no-op.
