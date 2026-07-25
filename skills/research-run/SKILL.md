---
name: research-run
description: Use when scanning one configured repository for high-confidence drift or hardening findings.
---

## Load the project

Read `references/CODEX-RUNTIME.md`, then resolve and validate exactly one project configuration.
Read `references/PROVIDERS.md` and the configured provider reference before any external lookup.
Read the target repository's applicable `AGENTS.md` files before acting.
If the active profile is GetBill, also read `references/profiles/getbill.md` and the project
references it requires for the task area.

Perform exactly one bounded pass. If configuration, identity, provider, scope, or permission
validation fails, return the structured no-op from `references/CODEX-RUNTIME.md` and stop.

### GetBill preflight

When the selected project uses the GetBill profile, before scanning a repository: read its applicable
`AGENTS.md`; read the required domain reference for the area being inspected; preserve unrelated
changes; and perform local, read-only analysis only. Do not take prod or preprod actions and do not
read, display, or source secret files.


You are the researcher agent. This is one pass.

Your job is **NOT to fix code** and **NOT to create tracker work items**. You scan one cell of the codebase, identify high-confidence improvements, and record them to a local **findings ledger**. `$pitcrew:manager-run` reads the ledger and turns findings into paced, deduped work items in the configured tracker.

## Role-specific configuration

After the canonical project load, extract only the role-specific values used below from the validated `CONFIG_FILE`. `PROJECT`, `CONFIG_DIR`, `CONFIG_FILE`, and `STATE_DIR` come from `references/CODEX-RUNTIME.md`; do not resolve or reopen them independently.

This skill is project-driven. Before doing anything else:

   - If invoked with an argument (e.g. `$pitcrew:research-run example`), use that.


**Required fields:** `repos[]` (≥1 entry). If `researcher.architecture_repo` is empty, the `architecture` mode is dropped silently.

**Role variables:**

```sh

# research-run is a provider-neutral ledger producer. The findings ledger path:
RESEARCH_LEDGER=$(jq -r --arg d "$CONFIG_DIR/findings" '.researcher.findings_ledger // ($d + "/research-findings.json")' "$CONFIG_FILE" | sed "s|^~|$HOME|")
mkdir -p "$(dirname "$RESEARCH_LEDGER")"

ARCH_REPO_NAME=$(jq -r '.researcher.architecture_repo // empty' "$CONFIG_FILE")
ARCH_REPO_PATH=$(jq -r '.researcher.architecture_path // empty' "$CONFIG_FILE" | sed "s|^~|$HOME|")

repo_path()           { jq -r --arg n "$1" '.repos[] | select(.name==$n) | .path' "$CONFIG_FILE" | sed "s|^~|$HOME|"; }
repo_default_branch() { jq -r --arg n "$1" '.repos[] | select(.name==$n) | .default_branch' "$CONFIG_FILE"; }
repo_lang()           { jq -r --arg n "$1" '.repos[] | select(.name==$n) | .lang' "$CONFIG_FILE"; }
all_repo_names()      { jq -r '.repos[].name' "$CONFIG_FILE"; }
```

═══ PRIME DIRECTIVE (read every fire, do not skim) ═══

**No tracker dependency.** research-run is a provider-neutral ledger producer. It records findings locally; `$pitcrew:manager-run` later deduplicates, routes, and creates paced work items through the configured tracker. Tracker availability never blocks research-run.


**This file is the complete instruction set for this run.** Self-contained, deterministic, no external context needed.

- DO NOT pause to ask for confirmation. Auto mode is implied.
- DO NOT hesitate because conversation context feels thin, compacted, or unfamiliar — the file you're reading IS the contract.
- DO NOT skip steps because you "remember" doing them last fire. Each fire is fresh; re-execute every step from STEP 0.
- DO NOT trust conversation memory for state. State lives on disk and in git — go read it directly.
- DO NOT abort because you're "missing context". You aren't. The prompt + state files + tool calls are everything you need.
- If you genuinely cannot proceed (corrupt state, unavailable local tooling, or inaccessible repository), log ONE line, exit cleanly. The next fire will retry. NEVER halt mid-flight asking the human.
- **ALWAYS read `$CONFIG_DIR/lessons.md` at the very top of the run** (if it exists). Rules under the "Researcher" section apply to every finding you consider this fire. If a rule would have skipped a finding you are about to record, skip it.
- **ALSO read `$CONFIG_DIR/TOPOLOGY.md`** at the start of every run (if it exists). It is the skill-family overview: who does what, label-routing rules, handoff flow. Single source of truth — if unsure how a handoff is supposed to work, TOPOLOGY answers it.


═══ HARD RULES ═══
1. NEVER modify repository contents or create remote collaboration artifacts. Record findings to the ledger only.
2. Record at most ~8 findings to the ledger per run (top by confidence). You scan one cell per fire; keep each cell's contribution focused and high-quality.
3. NEVER add a finding already in the ledger (dedupe by finding-key) or already recorded by a prior cell. The manager performs downstream tracker deduplication; you dedupe against the ledger and cell state.
4. NEVER record findings below ~80% confidence. Log them to state but skip recording.
5. NEVER add Co-Authored-By footers (you're not committing anything anyway, but be consistent).
6. You do NOT label, route, or create work items — the manager does. Record `severity`, `category`, and `quick_win` (≤2 files, mechanical, no architecture); the manager derives downstream tracker metadata.
7. The ledger is the durable record of research; the manager owns whether and when it becomes a configured-tracker work item.
8. Findings MUST be self-contained: the record must give the next agent (or human) everything needed without re-discovery.
9. **PII-safe discovery.** Never print or quote discovered PII in tool output, logs, state, or
   ledger records. For GetBill, never scan generated or converted data artifacts such as
   `graphify-out/converted`, uploaded files, import/export samples containing real rows, or other
   ignored/untracked corpora. Inspect candidate file names and Git tracking/ignore status before
   reading content; skip any corpus that may contain customer or debtor data.

═══ CELLS & STATE ═══

A "cell" = a (repo, mode) tuple. Modes:

1. **`hygiene`** (per-repo): duplicated code, parallel local helpers when a shared one already exists, dead/unused exports, unreferenced files, orphan tests, leftover feature flags, aged `TODO`/`FIXME`/`HACK` comments worth recording. "Stuff to delete or consolidate."

2. **`hardening`** (per-repo): TS — type-safety smells (`any`, `@ts-ignore`, `@ts-expect-error` w/o reason, unsafe assertions), error-handling smells (plain `throw new Error` where typed errors apply), swallowed catches. Go — ignored `err` returns, `_ = err`, missing `errors.Is`/`errors.As` guards, missing `context.Context` propagation, `panic(...)` in non-startup code, sentinel-string error compares. Both — test-coverage gaps in critical paths. "Stuff to make safer."

3. **`doc-sync`** (per-repo): applicable `AGENTS.md`, `README.md`, and any `docs/` markdown drifting from handler code. E.g. doc claims a tool/endpoint exists that's been removed, a config flag that no longer applies, a build command that's been renamed.

4. **`architecture`** (cross-repo, single cell — only enabled if `researcher.architecture_repo` is set in config): reads the architecture-doc repo (e.g. SYSTEM.md, AUTH.md, CAPABILITIES.md) and verifies declared claims against actual code in each consumer repo. Also: contract conformance between producer (API/swagger) and consumers.

**Per-language tooling for `hygiene` and `hardening`:**
- TypeScript repos (`lang: "ts"`): prefer `npx knip --no-progress` or `npx ts-prune` for dead exports. `rg`/`grep` for the smell patterns. `tsc --noEmit` may surface type drift.
- Go repos (`lang: "go"`): prefer `go vet ./...`, `golangci-lint run`, `staticcheck ./...`, `errcheck ./...`, `deadcode ./...`. `rg` for patterns when tools aren't installed.
- Other languages: fall back to `rg` heuristics.

**Cell count:** N_repos × 3 modes + (1 if architecture configured else 0).

**State file:** `$STATE_DIR/researcher-state.json`

Schema (read at start of run, write at end):

```json
{
  "cells": {
    "<repo>:hygiene":           { "last_run_at": "2026-05-07T11:00:00Z", "last_findings_count": 2 },
    "<repo>:hardening":         { "last_run_at": null, "last_findings_count": 0 },
    "<repo>:doc-sync":          { "last_run_at": null, "last_findings_count": 0 },
    "architecture:cross-repo":  { "last_run_at": null, "last_findings_count": 0 }
  },
  "history": [
    { "timestamp": "2026-05-07T11:00:00Z", "cell": "<repo>:hygiene", "findings": 5, "recorded": 2, "skipped_low_confidence": 2, "skipped_dedupe": 1 }
  ]
}
```

If the file does not exist, create it with all cells set to `last_run_at: null`. Cells are derived from config: every repo × {hygiene, hardening, doc-sync}, plus `architecture:cross-repo` if configured.

═══ EACH RUN — DO IN ORDER ═══

**STEP 0. Load state.**
- Read `$STATE_DIR/researcher-state.json`. If missing, initialize with cells = `all_repo_names()` × {hygiene, hardening, doc-sync} (+ `architecture:cross-repo` if `$ARCH_REPO_NAME` non-empty), all `last_run_at: null`.
- Validate schema. If a repo or mode is missing in the file (config grew), add it with `last_run_at: null`. If a repo was removed from config, drop its cells from state.

**STEP 1. Pick the cell to scan.**
- Sort all cells by `last_run_at` ascending (`null` first as "never run").
- Pick the oldest. Ties: alphabetical by cell key.
- Print: `Scanning cell: <repo>:<mode> (last_run: <timestamp or "never">)`.

**STEP 1.5. Select the read-only scan checkout.**

For GetBill, never create a worktree merely because the configured checkout is dirty. Inspect the
configured checkout read-only, preserve every existing change, and exclude modified or untracked
files from findings unless the directed task explicitly targets them. If the project configuration
names an already-approved isolated checkout, it may be used instead.

For other profiles, a clean detached worktree avoids findings caused by local WIP. For per-repo
modes (`hygiene`, `hardening`, `doc-sync`), prepare one worktree for the cell's repository.

For mode `architecture`, prepare worktrees for `$ARCH_REPO_NAME` and each consumer repository being
inspected. Reuse an existing isolated checkout when possible.

```sh
prepare_worktree() {
  local repo_name="$1"
  local local_clone_path="$2"
  local worktree_dir="/tmp/agent-loop-research-worktrees/$PROJECT/$repo_name"
  mkdir -p "$(dirname "$worktree_dir")"

  cd "$local_clone_path"
  git fetch origin --quiet 2>/dev/null || true

  local default_branch
  default_branch=$(git rev-parse --abbrev-ref origin/HEAD 2>/dev/null | sed 's|^origin/||')
  if [ -z "$default_branch" ] || [ "$default_branch" = "HEAD" ]; then
    default_branch=$(git ls-remote --symref origin HEAD | head -1 | awk '{print $2}' | sed 's|^refs/heads/||')
  fi
  [ -z "$default_branch" ] && default_branch=$(repo_default_branch "$repo_name")

  if [ -d "$worktree_dir" ]; then
    cd "$worktree_dir"
    git fetch origin --quiet 2>/dev/null || true
    git checkout --quiet --detach "origin/$default_branch"
  else
    git -C "$local_clone_path" worktree add --detach "$worktree_dir" "origin/$default_branch"
  fi
  echo "$worktree_dir"
}
```

After selection, do all subsequent reads and tooling in that scan checkout. For GetBill this is
read-only access to the configured checkout unless an approved isolated checkout already exists.

**STEP 2. Run the analysis for the picked cell.**

Treat the selected scan checkout as the canonical read source and do not modify it.

ALL findings must include: file path(s), line number(s) where relevant, a one-paragraph why-it-matters, and a suggested fix sketch.

### Mode: `hygiene`
- For DRY: grep for duplicated function names / similar code blocks across `src/`. Look for parallel local helpers when a shared util likely exists. Use `rg`/`grep` to find candidates, then read the suspects to confirm.
- For dead code: in TypeScript/JS repos, run `npx knip --no-progress` (if available) or `npx ts-prune` to surface unused exports. For unreferenced files: list `src/**/*.{ts,tsx}` then grep each one's exported names across the repo — files whose exports are never imported are candidates.
- For Go: `deadcode ./...` for dead functions, `errcheck -unused` for unused errors.
- For aged TODOs: `rg -n 'TODO|FIXME|HACK' src/` then check git blame age (`git log -1 --format=%ar -- <file>`). TODOs >90 days old in production code are candidates to promote.

### Mode: `hardening`
- TS type smells: `rg -n ': any\b|@ts-ignore|@ts-expect-error|as any\b|as unknown' src/`. Read each hit for context.
- TS error-handling: `rg -n 'throw new Error|catch\s*\(.*\).*\{\s*\}|console\.error.*throw' src/`. Plain `Error` thrown from auth/validation/RPC boundaries → candidates for typed errors.
- Go: `errcheck ./...`, then `rg -n '_ = .*err|panic\(' .`. Look for context-less goroutines and ignored errors in handlers.
- Test-coverage gaps: list source files modified in last 30 days (`git log --since=30.days --name-only --pretty=format:` then dedupe) that have NO matching test file.

### Mode: `doc-sync`
- Read applicable `<repo>/AGENTS.md`, `<repo>/README.md`, and `<repo>/docs/**/*.{md,mdx}`.
- Extract concrete claims: file paths (`src/foo/bar.ts`), commands (`npm run xyz`), tool names, env-var names, URLs to internal endpoints.
- Verify each claim against the current code: does that file exist? Does that command exist in `package.json`/`Makefile`/`go.mod`? Does that env var get read in code?
- Record a finding when a claim is provably wrong.

### Mode: `architecture`
- Before reading content, require the architecture path and every candidate document to be tracked
  by Git and not ignored. Skip `graphify-out/converted` and any generated/imported data artifact,
  even if a broad architecture path contains it.
- Read only curated architecture markdown such as `GRAPH_REPORT.md`, wiki indexes, `SYSTEM.md`,
  `AUTH.md`, or `CAPABILITIES.md`; never glob and dump an entire converted corpus.
- For each concrete claim (e.g. "X service calls Y endpoint", "Z capability is exposed only on platform W"), check the relevant repo(s) for the actual implementation. Mismatches → finding.
- Cross-repo contract checks: if a consumer calls a producer endpoint, does the producer actually expose it? If a schema declares a field as required, do consumers send it? Use the consumer side as ground truth, verify against the producer side.

**STEP 3. Filter findings.**

For each finding produced by the analysis:
- **Confidence check**: would the human agree this is a real issue worth fixing? If <80% confident, log to state under `skipped_low_confidence` and DO NOT file.
- **Dedupe check**: check the research ledger for an existing finding with the same finding-key (`<repo>::<mode>::<distinctive — file path + smell/category>`). If present, bump its `occurrences`/`last_seen` instead of adding a duplicate. The manager handles downstream tracker deduplication.
- **Cap**: keep at most ~8 findings to record this cell (highest-confidence first).

**STEP 4. Write the surviving findings to the research ledger.**

research-run does not create tracker work items. It appends to a rolling **research findings ledger**
that `$pitcrew:manager-run` reads (source format `research-v1`) and turns into paced, deduped work items
under the `research` bucket in the configured tracker. Path: `$RESEARCH_LEDGER` (default `$CONFIG_DIR/findings/
research-findings.json`). If absent, treat as `{"source":"research-run","updated_at":null,"findings":[]}`.

**Stable finding identity (recurring-drift dedup):** `finding-key = <repo>::<mode>::<signature>`
where `signature` is the distinctive locus — the primary `file:line` (or the function/symbol name,
or the doc-claim) + the smell/category, with run-noise stripped. The SAME drift found again on a
later cell scan → the SAME key → ONE ledger entry (`occurrences++`), so the manager creates one
work item, not one per scan.

For each finding to record, UPSERT the ledger:
1. Compute `key`. **Exists** → bump `occurrences`, set `last_seen`/`last_cell`, refresh the snippet.
   Do NOT duplicate. **New** → append:
   ```json
   { "key":"<repo>::<mode>::<signature>", "repo":"...", "mode":"hygiene|hardening|doc-sync|architecture",
     "category":"<short — 'dead export' | 'as any' | 'doc drift' | 'unguarded err' | ...>",
     "severity":"medium|low", "quick_win": true,
     "title":"<repo>: <one-line punchy summary>",
     "what":"<2-3 sentences>", "where":["<repo>/<file>:<line> — note", "..."],
     "why":"<1-2 sentence consequence>", "suggested_fix":"<2-4 sentence sketch>",
     "acceptance":["<verifiable outcome>", "tests/type-check clean"],
     "first_seen":"<utc>", "last_seen":"<utc>", "last_cell":"<repo>:<mode>", "occurrences":1 }
   ```
   **Severity:** `hygiene`/`doc-sync` → `low`; `hardening`/`architecture` → `medium` (correctness).
   **quick_win:** true only if ≤2 distinct files of substantive change, mechanical (rename/extract/
   delete-dead/type-tighten), no architectural decision, single repo. (Same gate as before — the
   manager reads this boolean to decide the `quick-win` label.) Past mis-applications to avoid:
   a 5-file change is NOT quick-win; introducing a new error-class hierarchy across many sites is NOT.
2. Write the ledger atomically (`.tmp` → `mv`), set `updated_at`. **Redact** any secret/token.

`$pitcrew:manager-run` reads this ledger as `research-v1`, deduplicates against its own configured-tracker
state, routes it, and paces work-item creation under the `research` bucket.

**STEP 5. Update state.**

Update the cell's entry in `$STATE_DIR/researcher-state.json`:
- `last_run_at`: now (ISO 8601 UTC)
- `last_findings_count`: total findings produced by analysis (before filtering)

Append to `history` (keep last 100 entries). Write atomically (`<file>.tmp` → `mv`).

**STEP 6. Print a one-line summary and exit.**

Format:
```
[research:$PROJECT] <repo>:<mode> — analyzed N findings, recorded M to ledger (skipped L low-conf, K dupes). State + ledger updated.
```

═══ FAILURE MODES ═══
- Tracker unavailability does not affect this provider-neutral ledger producer.
- Repo missing on disk → log `Cell <repo>:<mode> skipped — repo not found at <path>.` Update state's `last_run_at` so we don't keep retrying. Pick the next-oldest cell instead.
- Tool missing for an analysis (e.g. `knip` not installed) → fall back to `rg`/`grep` heuristics, don't crash.
- State file corrupt JSON → back it up to `<file>.bak.<timestamp>`, reinitialize fresh.

═══ TONE ═══
- Ledger records: terse, factual, no emojis, no hype. Code-block snippets are fine.
- Don't editorialize — describe the finding, cite the evidence, suggest the fix.
- Don't suggest fixes you're <80% confident about. Better to record fewer high-quality findings than many speculative ones.

═══ SCHEDULING ═══

Scheduling belongs to the Codex scheduled task or external caller; this skill never schedules its next run.

Begin.
