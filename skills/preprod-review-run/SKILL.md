---
name: preprod-review-run
description: Use when a human explicitly requests a read-only review of the unmerged origin/preprod to origin/develop delta.
---

## Manual-only gate

This role is manual-only: never schedule it, create a LaunchAgent, or start it from another role. Read
`references/CODEX-RUNTIME.md`, `references/PROVIDERS.md`, the runtime configuration, and every applicable
target-repository `AGENTS.md`. Read the configured domain references needed to understand the captured delta.
It is a local, read-only review; do not access the Preprod environment.

Require `gpt-5.6-sol` with `xhigh`. If either is unavailable or differs, call the helper `fail` with a bounded,
redacted reason, persist it in STORE/history, return `incomplete`, and stop.

Never create issues, comments, or tracker records. Never create branches, commits, merge requests, pull requests,
merge, deploy, or change code. Never use a database, read secrets, access Preprod, or inspect ignored, untracked,
or working-tree changes.

## Capture before review

The runtime paths are private: `MANIFEST=$CONFIG_DIR/preprod-review-manifest.json`,
`RESULT=$CONFIG_DIR/preprod-review-result.json`, and `STORE=$CONFIG_DIR/preprod-review-reports.json`.
Use only the configured `preprod_review.base_ref` (normally `origin/preprod`) and `compare_ref` (normally
`origin/develop`). Do not replace these refs or inspect mutable checkout state.

Run:

```bash
python3 <pitcrew-root>/scripts/pitcrew_preprod_review.py prepare \
  --repo "$REPO" --base-ref "$BASE_REF" --compare-ref "$COMPARE_REF" --manifest "$MANIFEST"
```

If preparation fails, run `fail` with the bounded/redacted failure reason and STORE/history path, then stop with
`incomplete`. The helper captures immutable SHAs; all analysis is strictly the exact merge-base three-dot delta
`base_sha...compare_sha`, never a mutable working tree.

## Review contract

Review every MANIFEST file path exactly once, including D/R/C/T entries and generated or binary entries. For a large
manifest, partition paths deterministically by their listed order and retain the exact one-time coverage ledger.
Assess regressions, business behavior, auth, security, migrations, config, jobs, contracts, compatibility, and tests.
Write one cross-change synthesis, not isolated file notes.

Atomically write private RESULT with exactly `reviewed_files`, `findings`, and `synthesis`. `reviewed_files` must
cover the manifest paths exactly once. Findings must be evidence-backed and bounded; do not include secrets.

## Verdict gate

Run the helper `finalize` with MANIFEST, RESULT, STORE, `gpt-5.6-sol`, and `xhigh`. The helper alone selects
`ready`, `changes_required`, or `incomplete` using its fixed policy. Never return `ready` directly. If finalization
fails, call helper `fail` with a bounded/redacted reason and stop as `incomplete`.
