---
name: product-discovery-run
description: Use when identifying one evidence-backed product opportunity for dashboard review.
---

Read `references/CODEX-RUNTIME.md` and `references/PROVIDERS.md`, then read the canonical runtime, provider, project `AGENTS.md`, and relevant product
references before scanning. Perform exactly one read-only pass. Do not create
tracker work, modify repository files, or infer customer data from generated
corpora.

Record at most one proposal in the configured proposals ledger. Use category
`feature`, severity `low|medium|high`, and include the user problem, evidence,
affected journey/UI, proposed behavior, acceptance criteria, and implementation
size. Deduplicate by stable id. New records must remain `suggested` until a
human approves them in the dashboard. Missing configuration or ledger produces
the structured no-op.
