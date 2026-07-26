---
name: architecture-run
description: Use when scanning one configured repository for high-confidence architecture boundary findings.
---

## Load the project

Read `references/CODEX-RUNTIME.md`, then resolve and validate exactly one project configuration.
Read `references/PROVIDERS.md`; this role makes no provider call. Read every applicable target-repository
`AGENTS.md` before acting. If the active profile is GetBill, also read `references/profiles/getbill.md`
and the required architecture references before scanning. On any invalid configuration, scope, or permission,
return the structured no-op from `references/CODEX-RUNTIME.md` and stop.

### GetBill preflight

Read applicable `AGENTS.md` and the selected architecture reference. Preserve all WIP and do local,
read-only analysis only. Never read, display, or source a secret file; do not take prod or preprod actions.

This is one bounded, deterministic architecture pass. No code, ticket, or remote action.

## Scope and state

Use the configured repository path as a read-only source. Before reading any candidate, inspect its Git
tracking and ignore status. Exclude modified, untracked, ignored, generated, dependency, secret, and
customer-data files. Do not create a worktree merely because a checkout is dirty. Never quote discovered
personal data in state or output.

Load curated architecture references only after the same tracked-file check. Read the runtime state at
`$STATE_DIR/architecture-state.json`; use no other state path. The state has one cell named
`architecture:$REPO_NAME` for every configured repository.

Use `python3 <pitcrew-root>/scripts/research_coverage.py` to discover and select the area for that cell.
The helper supplies a deterministic area; inspect only that area and do not substitute a convenient one.
After a valid, completed scan, including a scan with zero findings, record the selected area and advance the cell state.
Invalid configuration, unreadable tracked inputs, interruption, or incomplete analysis are not valid scans: do not
advance the cell state in any of those cases.

## Findings

Consider only these strict categories:

1. responsabilités mélangées
2. couplage framework/persistence
3. direction/cycles dépendances
4. frontières dupliquées
5. interfaces fuyantes
6. abstraction manquante prouvée

Each proposal needs concrete, tracked evidence: boundary, files and lines, why the observed dependency
violates that boundary, and a minimal fix sketch. `confidence >= 80%` is required. Set `category` to the
stable routing value `architecture`; set `architecture_category` to exactly one of the six categories above.
Exclude every concern outside that enum.

Produce maximum 3 proposals, ranked by confidence. Each record is a suggested local proposal with this
shape:

```json
{
  "id": "stable id: repo+boundary+evidence+fingerprint",
  "source": "architecture-run",
  "category": "architecture",
  "architecture_category": "one of the six French categories above",
  "severity": "medium",
  "title": "short boundary violation title",
  "summary": "observed architectural problem and impact",
  "status": "suggested",
  "evidence": ["tracked/path:line"],
  "recommendation": "minimal safe fix sketch",
  "confidence": 80
}
```

Deduplicate by the stable id, where the fingerprint derives from the selected tracked area. Keep the state
and proposal data local only. Do not modify repository code, create a tracker record, or contact a remote.
