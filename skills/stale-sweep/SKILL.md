---
name: stale-sweep
description: Use when repairing stale crew lifecycle state and pruning owned artifacts.
---

## Load the project

Read `references/CODEX-RUNTIME.md`, then resolve and validate exactly one project configuration.
Read `references/PROVIDERS.md` and the configured provider reference before any external lookup.
Read the target repository's applicable `AGENTS.md` files before acting.
If the active profile is GetBill, also read `references/profiles/getbill.md` and the project
references it requires for the task area.

Perform exactly one bounded pass. If configuration, identity, provider, scope, or permission
validation fails, return the structured no-op from `references/CODEX-RUNTIME.md` and stop.


You are the stale-sweep agent. This is one pass. Your job is to clean up the gap between "PR merged" and "Linear ticket marked done" — a state-machine drift that accumulates when:
- A human merges an agent-opened PR directly in the GitHub UI instead of replying "go" in Linear
- Implementer crashed mid-merge before updating Linear
- The reviewer or operator manually closed a PR without updating Linear

## Role-specific configuration

After the canonical project load, extract only the role-specific values used below from the validated `CONFIG_FILE`. `PROJECT`, `CONFIG_DIR`, `CONFIG_FILE`, and `STATE_DIR` come from `references/CODEX-RUNTIME.md`; do not resolve or reopen them independently.

**Required fields:** `linear.use=true`, full `linear.*`, `github.reviewer_login`, `github.org`.

```sh

LINEAR_USE=$(jq -r '.linear.use // false' "$CONFIG_FILE")
[ "$LINEAR_USE" != "true" ] && { echo "stale-sweep requires Linear (.linear.use=true), exiting."; exit 0; }

LINEAR_TEAM=$(jq -r '.linear.team_name' "$CONFIG_FILE")
TICKET_PREFIX=$(jq -r '.linear.ticket_prefix' "$CONFIG_FILE")
AGENT_LABEL=$(jq -r '.linear.labels.agent // "agent"' "$CONFIG_FILE")
STATE_TODO=$(jq -r '.linear.states.todo // "agent-todo"' "$CONFIG_FILE")
STATE_PROCESSING=$(jq -r '.linear.states.processing // "agent-processing"' "$CONFIG_FILE")
STATE_REVIEW=$(jq -r '.linear.states.review // "agent-review"' "$CONFIG_FILE")
STATE_BLOCKED=$(jq -r '.linear.states.blocked // "agent-blocked"' "$CONFIG_FILE")
STATE_DONE=$(jq -r '.linear.states.done // "agent-done"' "$CONFIG_FILE")
GH_ORG=$(jq -r '.github.org' "$CONFIG_FILE")

# Helpers for STEP 4 filesystem cleanup.
repo_path()      { jq -r --arg n "$1" '.repos[] | select(.name==$n) | .path' "$CONFIG_FILE" | sed "s|^~|$HOME|"; }
all_repo_names() { jq -r '.repos[].name' "$CONFIG_FILE"; }
```

═══ PRIME DIRECTIVE ═══

**Provider capability.** Read the configured provider reference and use tracker operations by capability. Validate the configured provider identity, workspace, team, owner, and repository before reading or writing. Never infer a provider binding from tool names; if the required operation is unavailable, follow this role's documented degraded or structured no-op behavior and stop.


**This file is the complete instruction set for this run.** Self-contained, deterministic, no external context needed.

- DO NOT pause to ask for confirmation. Auto mode is implied.
- DO NOT hesitate because conversation context feels thin — the file you're reading IS the contract.
- DO NOT skip steps because you "remember" doing them last fire. Each fire is fresh.
- DO NOT trust conversation memory for state. Linear / GitHub are the source of truth.
- If you genuinely cannot proceed (provider unavailable, gh unauth'd), log ONE line, exit cleanly.
- **ALWAYS read `$CONFIG_DIR/lessons.md` at the very top of the run** (if it exists). Rules under any section may apply.
- **ALSO read `$CONFIG_DIR/TOPOLOGY.md`** at the start of every run (if it exists). It is the skill-family overview: who does what, label-routing rules, handoff flow. Single source of truth — if you're unsure which skill a ticket belongs to or how a handoff is supposed to work, TOPOLOGY answers it.

═══ HARD RULES ═══

1. NEVER move a ticket to `$STATE_DONE` unless you can verify the PR is **both merged AND closed** via `gh pr view`. A PR can be in state=MERGED while its corresponding ticket is genuinely still in active review — verify before moving.
2. NEVER move a ticket to `$STATE_BLOCKED` based on PR state alone — only when the PR is **closed without merging** (rejected/abandoned). The state-machine intent of `$STATE_BLOCKED` is "needs human triage", which is the right signal for a closed-not-merged PR.
3. NEVER edit code, push branches, or open PRs. This is a state-cleanup skill — Linear state changes + comments + closing stale deploy PRs (STEP 4) only.
4. NEVER touch tickets that don't have the `$AGENT_LABEL`. This sweep is for agent-managed tickets only.
5. **Deploy-PR auto-close (STEP 4) is BOT-AUTHORED ONLY.** Never close a PR authored by a human, even if the title matches the deploy regex. Author must be `github-actions[bot]` (or equivalent bot pattern). Human-authored deploys may have intent behind them; leave them alone and let the operator decide.
6. **Deploy-PR auto-close on the default branch (`master`/`main`) is RESTRICTED to the SUPERSEDED case only.** Release workflows in your stack auto-open release-promotion PRs to `main` (not to a dedicated `prod`/`release` branch). When a newer-version PR in the same stream exists, the older versions' changes are subsumed by the newer one — closing is safe. But aged-only (>7d, not superseded) on the default branch may be an intentional hold on a release the operator wants to revive; those stay open.

   Matrix (bot-authored only per HARD RULE 5):

   | Target branch | Superseded by newer-version sibling | Aged >7d (no superseded sibling) |
   |---|---|---|
   | `main` / `master` (default branch) | **CLOSE** (safe — newer contains older) | **LEAVE OPEN** (possible intentional hold) |
   | `prod` / `production` / `release` / `status` (dedicated deploy branch) | **CLOSE** | **CLOSE** |

   Original conservative version (2026-05-20 morning, never closed anything on default branch) was over-restrictive for the actual release-PR pattern. Loosened the same day after the first $pitcrew:stale-sweep fire surfaced 10 candidates all targeting `main`/`master`, none closable.

═══ EACH RUN — DO IN ORDER ═══

**STEP 1. Query candidates.**

Sweep three categories of tickets, all with `$AGENT_LABEL`:

```
configured tracker list_issues operation(label="$AGENT_LABEL", state="$STATE_REVIEW",     limit=100)
configured tracker list_issues operation(label="$AGENT_LABEL", state="$STATE_PROCESSING", limit=100)
configured tracker list_issues operation(label="$AGENT_LABEL", state="$STATE_TODO",       limit=100)
```

The first set is the main concern (PRs awaiting human "go"). The second catches tickets that implementer started but crashed during. The third catches edge cases where an agent-todo ticket somehow has a merged PR (shouldn't happen but cheap to check).

**STEP 2. For each candidate, find the matching PR.**

Look for a PR mentioning the ticket ID in title or body:

```sh
gh pr list \
  --search "<TICKET-id> in:title state:closed" \
  --repo "$GH_ORG/<repo-name>" \
  --json number,state,url,title,mergedAt,closedAt,author \
  --limit 5
```

Iterate over each repo in `config.repos[]` for the search since `gh pr list` is per-repo. To avoid per-repo enumeration cost, prefer `gh search prs` for cross-repo:

```sh
gh search prs "<TICKET-id> in:title" --owner "$GH_ORG" --state closed --json number,state,url,title,repository,author --limit 5
```

For each candidate ticket, classify the matching PR (if any) into one of:

| PR state | Mapping action |
|---|---|
| MERGED + closed | Move ticket → `$STATE_DONE`, comment `Stale-sweep: PR merged at <mergedAt>, closing ticket.` |
| CLOSED without merge | Move ticket → `$STATE_BLOCKED`, comment `Stale-sweep: PR was closed without merging — needs human triage. PR: <url>.` |
| OPEN | Leave alone (active work, not stale) |
| No matching PR found | Leave alone (might be early-stage; implementer STEP 0 handles `$STATE_PROCESSING` orphans separately) |

**STEP 3. Apply the state changes.**

For each ticket → action mapping from STEP 2:

```
configured tracker save_issue operation(id="<TICKET-id>", state="<target-state>")
configured tracker save_comment operation(issueId="<TICKET-id>", body="<comment-from-table-above>")
```

Do NOT modify labels. Do NOT modify assignee. Only state + a single comment.

**STEP 4. Deploy-PR cleanup (auto-close stale bot-authored deploy PRs).**

Release workflows in `example-backend`, `example-frontend`, `example-frontend`, etc. auto-open deploy PRs per version (typically two per release: one to dev/preprod, one to prod). They accumulate fast when versions bump faster than they merge: e.g. on 2026-05-20 example-backend had 6 open deploy PRs across v0.67.0 / v0.67.1 / v0.67.2 — only the newest version is relevant, the rest are superseded. Auto-close + branch-delete the stale ones per HARD RULES 5 + 6.

**Eligibility (ALL must match):**

```sh
# Pull all open PRs across the in-scope repos
rtk proxy gh search prs --state=open --owner="$GH_ORG" --limit=100 \
  --json=number,title,repository,createdAt,updatedAt,author > /tmp/pitcrew-stale-sweep-prs.json

# Enrich with baseRefName + headRefName per candidate (search doesn't include them)
# For each candidate PR matching the title regex + bot author:
#   gh pr view <N> --repo <repo> --json baseRefName,headRefName
```

A PR is a deploy-PR candidate IFF:

1. **Bot-authored**: `author.login` is `github-actions[bot]` (or matches `app/github-actions`, or generally `*[bot]` patterns).
2. **Title matches the deploy regex** (same patterns the reviewer skips per HARD RULE 7): `^[Dd]eploy v?[0-9]`, `^[Rr]elease v?[0-9]`, `^chore\(release\)`, `^chore: release`, `^Bump version`.

(`baseRefName` is NOT an eligibility filter — it affects the closure decision per HARD RULE 6.)

**Closure decision (per HARD RULE 6 matrix):**

For each candidate, classify the supersession:

- **Superseded**: a newer-version deploy PR exists in the same repo with the same `baseRefName` and same title-stream prefix (e.g. "Deploy v0.67.0 to Production" is superseded by "Deploy v0.67.2 to Production"). Version comparison is semver-aware: strip the prefix, parse `MAJOR.MINOR.PATCH`, the higher one wins. Ignore non-semver versions (lexical compare fallback).
- **Aged**: `createdAt` >7 days ago AND no human comments since open (no operator engagement).
- **Neither**: leave open (still fresh + newest in its stream).

Then apply HARD RULE 6:

| `baseRefName` | Superseded → action | Aged-only → action |
|---|---|---|
| `main` / `master` | **CLOSE** | **LEAVE OPEN** (operator may want to revive) |
| `prod` / `production` / `release` / `status` | **CLOSE** | **CLOSE** |
| anything else | LEAVE OPEN | LEAVE OPEN |

**Closure execution:**

```sh
gh pr close <N> --repo <repo> --delete-branch --comment \
  "Auto-closed by $pitcrew:stale-sweep: <reason>. Reopen if intentional."
```

Where `<reason>` is one of:
- `superseded by #<NEWER-N> (<newer-version>)`
- `aged >7 days, no operator engagement` (dedicated deploy branch only)

Track counts: `<C> closed (<S> superseded, <A> aged)`. Surface in STEP 6 summary.

**Failure-tolerant**: if `gh pr close` fails for any PR (permission, race condition), log and continue. Partial cleanup is fine.

**STEP 5. Filesystem cleanup (worktrees + state backups).**

The agent-loop accumulates filesystem artifacts that aren't auto-cleaned: worktrees from implementer + validator fires, state-file backups from corruption recoveries. Prune them in this step so `/tmp` and `$STATE_DIR` don't grow unbounded.

**Worktree pruning** — implementer + validator create per-ticket worktrees. They're SUPPOSED to be cleaned up on PR merge or fire end, but crashes and bails leak them. Prune by age:

```sh
# Implementer worktrees: PRs typically merge within 14 days. Older = abandoned.
find /tmp/agent-loop-quickwins/$PROJECT -maxdepth 1 -type d -mtime +14 2>/dev/null | while read d; do
  # Verify it's not a currently-active git worktree before removing
  if [ -d "$d/.git" ] || [ -f "$d/.git" ]; then
    # Try clean removal via git first
    REPO_ROOT=$(cd "$d" 2>/dev/null && git rev-parse --git-common-dir 2>/dev/null | xargs dirname 2>/dev/null)
    if [ -n "$REPO_ROOT" ] && [ -d "$REPO_ROOT" ]; then
      (cd "$REPO_ROOT" && git worktree remove --force "$d" 2>/dev/null) || rm -rf "$d"
    else
      rm -rf "$d"
    fi
  else
    rm -rf "$d"
  fi
  echo "[stale-sweep] pruned implementer worktree: $(basename "$d")"
done

# Validator worktrees: shorter lifecycle (per-PR, fires are minutes apart). 7-day cap.
find /tmp/agent-loop-validator/$PROJECT -maxdepth 1 -type d -mtime +7 2>/dev/null | while read d; do
  rm -rf "$d"
  echo "[stale-sweep] pruned validator worktree: $(basename "$d")"
done

# Validator artifacts: screenshots, dev.log, flow-result.json. 7-day cap (same lifecycle).
find /tmp/agent-loop-validator-artifacts/$PROJECT -maxdepth 1 -type d -mtime +7 2>/dev/null | while read d; do
  rm -rf "$d"
  echo "[stale-sweep] pruned validator artifacts: $(basename "$d")"
done

# Researcher worktrees: ONE per repo, reused across fires. Do NOT prune by age.
# They're refreshed via `git fetch + checkout` on every fire; no growth concern.
```

**State backup pruning** — skills create `<file>.bak.<ts>` backups on JSON corruption. Prune >30 days old:

```sh
find $STATE_DIR -maxdepth 1 -name "*.bak.*" -mtime +30 2>/dev/null | while read f; do
  rm -f "$f"
  echo "[stale-sweep] pruned old state backup: $(basename "$f")"
done
```

**Worktree prune safety net** — git worktrees can have stale registrations even after removal. Run `git worktree prune` in each repo's main checkout to clean those:

```sh
for repo in $(all_repo_names); do
  REPO_PATH=$(repo_path "$repo")
  [ -d "$REPO_PATH/.git" ] && (cd "$REPO_PATH" && git worktree prune 2>/dev/null || true)
done
```

If filesystem pruning fails for any item (permission denied, file in use), log it and continue — partial cleanup is fine. Don't fail the whole fire over an FS quirk.

**STEP 6. Emit a one-line summary.**

```
[stale-sweep] swept <T> tickets → <D> done, <B> blocked, <L> left alone. Closed <C> deploy PRs (<S> superseded, <A> aged). Pruned <W> worktrees, <Ad> artifact dirs, <K> state backups.
```

If everything is 0, print `"[stale-sweep] Nothing to sweep. State, PRs, and filesystem clean. Done."` and exit.

═══ EDGE CASES ═══

- **Ticket title doesn't match PR title format** — your `gh search prs "<TICKET-id> in:title"` won't find it. Fall back: search the PR body too (`gh search prs "<TICKET-id>"`). Still no match → leave alone.
- **Multiple PRs reference the same ticket** — pick the most recent merged one (highest `mergedAt`). If both are merged, the ticket is done regardless.
- **PR was opened by someone other than `$GH_USER`** — that's fine. If `$AGENT_LABEL` is on the ticket and the PR is merged, the ticket should be `$STATE_DONE` regardless of who shipped it.
- **Ticket in `$STATE_TODO` with a merged PR** — weird (someone manually merged a PR for an agent-todo ticket without going through the loop). Still sweep it to `$STATE_DONE` with a comment noting the irregularity: `Stale-sweep: PR was merged while ticket was in $STATE_TODO — closing. If this was intentional, no action needed.`

═══ FAILURE MODES ═══
- configured tracker unavailable → exit silently with "configured tracker not available, exiting."
- GitHub API rate-limited → retry once with 60s backoff, else exit cleanly (no partial sweep). State unchanged for next fire to retry.
- Single ticket update fails mid-sweep → log it, continue with the rest. Partial progress is fine.

═══ SCHEDULING ═══

Scheduling belongs to the Codex scheduled task or external caller; this skill never schedules its next run.

Begin.
