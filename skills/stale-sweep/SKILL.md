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


You are the stale-sweep agent. This is one pass. Your job is to clean up the gap between "change merged" and "configured tracker ticket marked done" — a state-machine drift that accumulates when:
- A human merges an agent-opened change directly in the configured forge UI instead of replying "go" in configured tracker
- Implementer crashed mid-merge before updating configured tracker
- The reviewer or operator manually closed a change without updating configured tracker

## Role-specific configuration

After the canonical project load, extract only the role-specific values used below from the validated `CONFIG_FILE`. `PROJECT`, `CONFIG_DIR`, `CONFIG_FILE`, and `STATE_DIR` come from `references/CODEX-RUNTIME.md`; do not resolve or reopen them independently.

**Required fields:** configured tracker identity/state/label mappings and configured forge
identity and owner/group.

```text


TRACKER_TEAM="<resolved from configured tracker reference>"
TICKET_PREFIX="<resolved from configured tracker reference>"
AGENT_LABEL="<resolved from configured tracker reference>"
STATE_TODO="<resolved from configured tracker reference>"
STATE_PROCESSING="<resolved from configured tracker reference>"
STATE_REVIEW="<resolved from configured tracker reference>"
STATE_BLOCKED="<resolved from configured tracker reference>"
STATE_DONE="<resolved from configured tracker reference>"
FORGE_OWNER="<configured forge owner-or-group>"

# Helpers for STEP 4 filesystem cleanup.
repo_path()      { jq -r --arg n "$1" '.repos[] | select(.name==$n) | .path' "$CONFIG_FILE" | sed "s|^~|$HOME|"; }
all_repo_names() { jq -r '.repos[].name' "$CONFIG_FILE"; }
```

═══ PRIME DIRECTIVE ═══

**Provider capability.** Read the configured provider reference and use tracker operations by capability. Validate the configured provider identity, workspace, team, owner, and repository before reading or writing. Never infer a provider binding from tool names; if the required operation is unavailable, follow this role's documented degraded or structured no-op behavior and stop.


### Provider dispatch — fail closed

Read `providers.forge` and `providers.tracker` from the validated configuration before
performing provider work. The role logic uses only these generic operations:

- **list eligible work**
- **claim work**
- **create change**
- **review change**
- **merge change**
- **close lifecycle**

Provider-specific command syntax belongs only in the selected provider reference. Uppercase operation names in later examples are abstract capabilities, not shell commands; resolve each through that reference.

- When `providers.forge` is `github`, read
  `references/providers/github-linear.md` and use configured forge **pull-request** terminology.
- When `providers.forge` is `gitlab`, read
  `references/providers/gitlab.md` and use GitLab **merge-request** terminology.
- When `providers.tracker` is `linear`, validate the configured Linear team before tracker
  operations.
- When `providers.tracker` is `github` or `gitlab`, use the matching provider reference and issue terminology.
- When `providers.tracker` is `none`, skip tracker work; if this role requires tracker work,
  return the structured no-op and stop.

**Never fall back to another provider, workspace, owner, project, repository, or environment.**
Validate the configured provider/host/owner-or-group/repository binding before every provider
operation. If it cannot be validated or lacks the required generic operation, return the
structured no-op and stop.



**This file is the complete instruction set for this run.** Self-contained, deterministic, no external context needed.

- DO NOT pause to ask for confirmation. Auto mode is implied.
- DO NOT hesitate because conversation context feels thin — the file you're reading IS the contract.
- DO NOT skip steps because you "remember" doing them last fire. Each fire is fresh.
- DO NOT trust conversation memory for state. configured tracker / configured forge are the source of truth.
- If you genuinely cannot proceed (provider unavailable, configured forge unauth'd), log ONE line, exit cleanly.
- **ALWAYS read `$CONFIG_DIR/lessons.md` at the very top of the run** (if it exists). Rules under any section may apply.
- **ALSO read `$CONFIG_DIR/TOPOLOGY.md`** at the start of every run (if it exists). It is the skill-family overview: who does what, label-routing rules, handoff flow. Single source of truth — if you're unsure which skill a ticket belongs to or how a handoff is supposed to work, TOPOLOGY answers it.

### GetBill preflight

When the active profile is GetBill:

1. Re-read the repository `AGENTS.md`.
2. Preserve all unrelated working-tree changes.
3. Never create a worktree only because the checkout is dirty.
4. Read the required domain reference before changing that area.
5. After code changes, rebuild Graphify before completion.
6. Stage only files changed by this crew item.

═══ HARD RULES ═══

1. NEVER move a ticket to `$STATE_DONE` unless you can verify the change is **both merged AND closed** via `INSPECT_CHANGE`. A change can be in state=MERGED while its corresponding ticket is genuinely still in active review — verify before moving.
2. NEVER move a ticket to `$STATE_BLOCKED` based on change state alone — only when the change is **closed without merging** (rejected/abandoned). The state-machine intent of `$STATE_BLOCKED` is "needs human triage", which is the right signal for a closed-not-merged change.
3. NEVER edit code, push branches, or open changes. This is a state-cleanup skill — configured tracker state changes + comments + closing stale deploy changes (STEP 4) only.
4. NEVER touch tickets that don't have the `$AGENT_LABEL`. This sweep is for agent-managed tickets only.
5. **Deploy-change auto-close (STEP 4) is BOT-AUTHORED ONLY.** Never close a change authored by a human, even if the title matches the deploy regex. Author must be `<configured-forge automation identity>` (or equivalent bot pattern). Human-authored deploys may have intent behind them; leave them alone and let the operator decide.
6. **Deploy-change auto-close on the default branch (`master`/`main`) is RESTRICTED to the SUPERSEDED case only.** Release workflows in your stack auto-open release-promotion changes to `main` (not to a dedicated `prod`/`release` branch). When a newer-version change in the same stream exists, the older versions' changes are subsumed by the newer one — closing is safe. But aged-only (>7d, not superseded) on the default branch may be an intentional hold on a release the operator wants to revive; those stay open.

   Matrix (bot-authored only per HARD RULE 5):

   | Target branch | Superseded by newer-version sibling | Aged >7d (no superseded sibling) |
   |---|---|---|
   | `main` / `master` (default branch) | **CLOSE** (safe — newer contains older) | **LEAVE OPEN** (possible intentional hold) |
   | `prod` / `production` / `release` / `status` (dedicated deploy branch) | **CLOSE** | **CLOSE** |

   Original conservative version (2026-05-20 morning, never closed anything on default branch) was over-restrictive for the actual release-change pattern. Loosened the same day after the first $pitcrew:stale-sweep fire surfaced 10 candidates all targeting `main`/`master`, none closable.

═══ EACH RUN — DO IN ORDER ═══

**STEP 1. Query candidates.**

Sweep three categories of tickets, all with `$AGENT_LABEL`:

```
LIST_ELIGIBLE_WORK(label="$AGENT_LABEL", state="$STATE_REVIEW",     limit=100)
LIST_ELIGIBLE_WORK(label="$AGENT_LABEL", state="$STATE_PROCESSING", limit=100)
LIST_ELIGIBLE_WORK(label="$AGENT_LABEL", state="$STATE_TODO",       limit=100)
```

The first set is the main concern (changes awaiting human "go"). The second catches tickets that implementer started but crashed during. The third catches edge cases where an agent-todo ticket somehow has a merged change (shouldn't happen but cheap to check).

**STEP 2. For each candidate, find the matching change.**

Look for a change mentioning the ticket ID in title or body:

```text
LIST_ELIGIBLE_CHANGES \
  --search "<TICKET-id> in:title state:closed" \
  --repo "$FORGE_OWNER/<repo-name>" \
  --json number,state,url,title,merged_at,closed_at,author \
  --limit 5
```

Iterate over each repo in `config.repos[]` for the search since `LIST_ELIGIBLE_CHANGES` is per-repo. To avoid per-repo enumeration cost, prefer `LIST_ELIGIBLE_CHANGES` for cross-repo:

```text
LIST_ELIGIBLE_CHANGES "<TICKET-id> in:title" --owner "$FORGE_OWNER" --state closed --json number,state,url,title,repository,author --limit 5
```

For each candidate ticket, classify the matching change (if any) into one of:

| change state | Mapping action |
|---|---|
| MERGED + closed | Move ticket → `$STATE_DONE`, comment `Stale-sweep: change merged at <merged_at>, closing ticket.` |
| CLOSED without merge | Move ticket → `$STATE_BLOCKED`, comment `Stale-sweep: change was closed without merging — needs human triage. change: <url>.` |
| OPEN | Leave alone (active work, not stale) |
| No matching change found | Leave alone (might be early-stage; implementer STEP 0 handles `$STATE_PROCESSING` orphans separately) |

**STEP 3. Apply the state changes.**

For each ticket → action mapping from STEP 2:

```
UPDATE_TRACKER_ITEM(id="<TICKET-id>", state="<target-state>")
COMMENT_ON_TRACKER_ITEM(issueId="<TICKET-id>", body="<comment-from-table-above>")
```

Do NOT modify labels. Do NOT modify assignee. Only state + a single comment.

**STEP 4. Deploy-change cleanup (auto-close stale bot-authored deploy changes).**

Release workflows in `example-backend`, `example-frontend`, `example-frontend`, etc. auto-open deploy changes per version (typically two per release: one to dev/preprod, one to prod). They accumulate fast when versions bump faster than they merge: e.g. on 2026-05-20 example-backend had 6 open deploy changes across v0.67.0 / v0.67.1 / v0.67.2 — only the newest version is relevant, the rest are superseded. Auto-close + branch-delete the stale ones per HARD RULES 5 + 6.

**Eligibility (ALL must match):**

```text
# Pull all open changes across the in-scope repos
LIST_ELIGIBLE_CHANGES --state=open --owner="$FORGE_OWNER" --limit=100 \
  --json=number,title,repository,createdAt,updatedAt,author > /tmp/pitcrew-stale-sweep-prs.json

# Enrich with target_branch + source_branch per candidate (search doesn't include them)
# For each candidate change matching the title regex + bot author:
#   INSPECT_CHANGE <N> --repo <repo> --json target_branch,source_branch
```

A change is a deploy-change candidate IFF:

1. **Automation-authored**: `author_identity` matches an automation identity explicitly
   configured by the selected provider reference. Never infer bot ownership from a
   provider-specific username pattern.
2. **Title matches the deploy regex** (same patterns the reviewer skips per HARD RULE 7): `^[Dd]eploy v?[0-9]`, `^[Rr]elease v?[0-9]`, `^chore\(release\)`, `^chore: release`, `^Bump version`.

(`target_branch` is NOT an eligibility filter — it affects the closure decision per HARD RULE 6.)

**Closure decision (per HARD RULE 6 matrix):**

For each candidate, classify the supersession:

- **Superseded**: a newer-version deploy change exists in the same repo with the same `target_branch` and same title-stream prefix (e.g. "Deploy v0.67.0 to Production" is superseded by "Deploy v0.67.2 to Production"). Version comparison is semver-aware: strip the prefix, parse `MAJOR.MINOR.PATCH`, the higher one wins. Ignore non-semver versions (lexical compare fallback).
- **Aged**: `createdAt` >7 days ago AND no human comments since open (no operator engagement).
- **Neither**: leave open (still fresh + newest in its stream).

Then apply HARD RULE 6:

| `target_branch` | Superseded → action | Aged-only → action |
|---|---|---|
| `main` / `master` | **CLOSE** | **LEAVE OPEN** (operator may want to revive) |
| `prod` / `production` / `release` / `status` | **CLOSE** | **CLOSE** |
| anything else | LEAVE OPEN | LEAVE OPEN |

**Closure execution:**

```text
CLOSE_CHANGE <N> --repo <repo> --delete-branch --comment \
  "Auto-closed by $pitcrew:stale-sweep: <reason>. Reopen if intentional."
```

Where `<reason>` is one of:
- `superseded by #<NEWER-N> (<newer-version>)`
- `aged >7 days, no operator engagement` (dedicated deploy branch only)

Track counts: `<C> closed (<S> superseded, <A> aged)`. Surface in STEP 6 summary.

**Failure-tolerant**: if `CLOSE_CHANGE` fails for any change (permission, race condition), log and continue. Partial cleanup is fine.

**STEP 5. Filesystem cleanup (worktrees + state backups).**

The agent-loop accumulates filesystem artifacts that aren't auto-cleaned: worktrees from implementer + validator fires, state-file backups from corruption recoveries. Prune them in this step so `/tmp` and `$STATE_DIR` don't grow unbounded.

**Worktree pruning** — implementer + validator create per-ticket worktrees. They're SUPPOSED to be cleaned up on change merge or fire end, but crashes and bails leak them. Prune by age:

```text
# Implementer worktrees: changes typically merge within 14 days. Older = abandoned.
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

# Validator worktrees: shorter lifecycle (per-change, fires are minutes apart). 7-day cap.
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

```text
find $STATE_DIR -maxdepth 1 -name "*.bak.*" -mtime +30 2>/dev/null | while read f; do
  rm -f "$f"
  echo "[stale-sweep] pruned old state backup: $(basename "$f")"
done
```

**Worktree prune safety net** — git worktrees can have stale registrations even after removal. Run `git worktree prune` in each repo's main checkout to clean those:

```text
for repo in $(all_repo_names); do
  REPO_PATH=$(repo_path "$repo")
  [ -d "$REPO_PATH/.git" ] && (cd "$REPO_PATH" && git worktree prune 2>/dev/null || true)
done
```

If filesystem pruning fails for any item (permission denied, file in use), log it and continue — partial cleanup is fine. Don't fail the whole fire over an FS quirk.

**STEP 6. Emit a one-line summary.**

```
[stale-sweep] swept <T> tickets → <D> done, <B> blocked, <L> left alone. Closed <C> deploy changes (<S> superseded, <A> aged). Pruned <W> worktrees, <Ad> artifact dirs, <K> state backups.
```

If everything is 0, print `"[stale-sweep] Nothing to sweep. State, changes, and filesystem clean. Done."` and exit.

═══ EDGE CASES ═══

- **Ticket title doesn't match change title format** — your `LIST_ELIGIBLE_CHANGES "<TICKET-id> in:title"` won't find it. Fall back: search the change body too (`LIST_ELIGIBLE_CHANGES "<TICKET-id>"`). Still no match → leave alone.
- **Multiple changes reference the same ticket** — pick the most recent merged one (highest `merged_at`). If both are merged, the ticket is done regardless.
- **change was opened by someone other than `$FORGE_USER`** — that's fine. If `$AGENT_LABEL` is on the ticket and the change is merged, the ticket should be `$STATE_DONE` regardless of who shipped it.
- **Ticket in `$STATE_TODO` with a merged change** — weird (someone manually merged a change for an agent-todo ticket without going through the loop). Still sweep it to `$STATE_DONE` with a comment noting the irregularity: `Stale-sweep: change was merged while ticket was in $STATE_TODO — closing. If this was intentional, no action needed.`

═══ FAILURE MODES ═══
- configured tracker unavailable → exit silently with "configured tracker not available, exiting."
- configured forge API rate-limited → retry once with 60s backoff, else exit cleanly (no partial sweep). State unchanged for next fire to retry.
- Single ticket update fails mid-sweep → log it, continue with the rest. Partial progress is fine.

═══ SCHEDULING ═══

Scheduling belongs to the Codex scheduled task or external caller; this skill never schedules its next run.

Begin.
