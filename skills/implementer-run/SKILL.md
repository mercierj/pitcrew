---
name: implementer-run
description: Use when implementing one eligible configured issue through a reviewed change.
---

## Load the project

Read `references/CODEX-RUNTIME.md`, then resolve and validate exactly one project configuration.
Read `references/PROVIDERS.md` and the configured provider reference before any external lookup.
Read the target repository's applicable `AGENTS.md` files before acting.
If the active profile is GetBill, also read `references/profiles/getbill.md` and the project
references it requires for the task area.

Perform exactly one bounded pass. If configuration, identity, provider, scope, or permission
validation fails, return the structured no-op from `references/CODEX-RUNTIME.md` and stop.


You are the implementer agent. This is one pass.

## Role-specific configuration

After the canonical project load, extract only the role-specific values used below from the validated `CONFIG_FILE`. `PROJECT`, `CONFIG_DIR`, `CONFIG_FILE`, and `STATE_DIR` come from `references/CODEX-RUNTIME.md`; do not resolve or reopen them independently.

**Required fields:** configured tracker identity/state/label mappings, configured forge
identity and owner/group, and `repos[]` (at least one).

```text
WORKTREE_ROOT="/tmp/agent-loop-quickwins/$PROJECT"


TRACKER_TEAM="<resolved from configured tracker reference>"
TRACKER_WORKSPACE="<resolved from configured tracker reference>"
TICKET_PREFIX="<resolved from configured tracker reference>"
ASSIGNEE_EMAIL="<resolved from configured tracker reference>"
# Master eligibility label — only label that matters for picking up tickets.
AGENT_LABEL="<resolved from configured tracker reference>"
AGENT_LABEL_ID="<resolved from configured tracker reference>"
# Categorization / sort hints (no longer eligibility flags — agent label is the gate)
QUICK_WIN_LABEL="<resolved from configured tracker reference>"
QUICK_WIN_LABEL_ID="<resolved from configured tracker reference>"
BUG_LABEL="<resolved from configured tracker reference>"
BUG_LABEL_ID="<resolved from configured tracker reference>"
IMPROVEMENT_LABEL="<resolved from configured tracker reference>"
IMPROVEMENT_LABEL_ID="<resolved from configured tracker reference>"
# Routing-skip labels — implementer ignores tickets carrying these (different agents handle them).
INVESTIGATE_LABEL="<resolved from configured tracker reference>"
INVESTIGATE_LABEL_ID="<resolved from configured tracker reference>"
# Workflow state names (the agent-* state machine — override in configured tracker states.* if you renamed them)
STATE_TODO="<resolved from configured tracker reference>"
STATE_PROCESSING="<resolved from configured tracker reference>"
STATE_REVIEW="<resolved from configured tracker reference>"
STATE_BLOCKED="<resolved from configured tracker reference>"
STATE_DONE="<resolved from configured tracker reference>"
# State IDs — PREFER these over names when calling UPDATE_TRACKER_ITEM (see HARD RULE 10).
STATE_TODO_ID="<resolved from configured tracker reference>"
STATE_PROCESSING_ID="<resolved from configured tracker reference>"
STATE_REVIEW_ID="<resolved from configured tracker reference>"
STATE_BLOCKED_ID="<resolved from configured tracker reference>"
STATE_DONE_ID="<resolved from configured tracker reference>"
FORGE_USER="<validated configured forge identity>"
FORGE_OWNER="<configured forge owner-or-group>"
SLACK_WEBHOOK_URL=$(jq -r '.slack.implementer_webhook_url // .slack.quickwins_webhook_url // empty' "$CONFIG_FILE")
SLACK_USER_MENTION=$(jq -r '.slack.user_mention // empty' "$CONFIG_FILE")
repo_path()           { jq -r --arg n "$1" '.repos[] | select(.name==$n) | .path' "$CONFIG_FILE" | sed "s|^~|$HOME|"; }
repo_default_branch() { jq -r --arg n "$1" '.repos[] | select(.name==$n) | .default_branch' "$CONFIG_FILE"; }
repo_tags()           { jq -r --arg n "$1" '.repos[] | select(.name==$n) | .tags // [] | join(",")' "$CONFIG_FILE"; }
repo_contributor_skill() { jq -r --arg n "$1" '.repos[] | select(.name==$n) | .contributor_skill // empty' "$CONFIG_FILE"; }
all_repo_names()      { jq -r '.repos[].name' "$CONFIG_FILE"; }
```

═══ PRIME DIRECTIVE (read every fire, do not skim) ═══

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


### GetBill preflight

When the active profile is GetBill:

1. Re-read the repository `AGENTS.md`.
2. Preserve all unrelated working-tree changes.
3. Never create a worktree only because the checkout is dirty.
4. Read the required domain reference before changing that area.
5. After code changes, rebuild Graphify before completion.
6. Stage only files changed by this crew item.

**DIRECTED TARGET (optional):** Read `references/DIRECTED-TARGET.md`. Parse only its configured-provider URL and short-reference forms. Validate provider, host, workspace, owner or group, and configured repository before lookup. Operate on exactly one validated target, preserve every safety gate, then stop.

**COORDINATED TARGET BINDING:** When `PITCREW_RUN_ID` is set, before the first
tracker mutation or checkout write, bind the canonical ticket:

```text
python3 <pitcrew-root>/scripts/pitcrew_run_dispatcher.py bind-target \
  --project <project> --run-id "$PITCREW_RUN_ID" --target <canonical-url>
```

If `bind-target` reports a conflict, select another eligible ticket or return a
structured no-op without a tracker mutation or checkout write.


**This file is the complete instruction set for this run.** Self-contained, deterministic, no external context needed.

- DO NOT pause to ask for confirmation. Auto mode is implied.
- DO NOT hesitate because conversation context feels thin, compacted, or unfamiliar — the file you're reading IS the contract.
- DO NOT skip steps because you "remember" doing them last fire. Each fire is fresh; re-execute every step from STEP 0.
- DO NOT trust conversation memory for state. State lives on disk, in configured tracker, in configured forge, in git — go read it directly.
- DO NOT abort because you're "missing context". You aren't.
- If you genuinely cannot proceed (corrupt state, provider unavailable, configured forge unauth'd), log ONE line, exit cleanly. The next fire will retry.
- **ALWAYS read `$CONFIG_DIR/lessons.md` at the very top of the run** (if it exists). Rules under the "Implementer" section (legacy "Quickwins" header still works for backwards compat) apply to STEP C scope-check and STEP D implementation. If a rule would have bailed this ticket, bail it.
- **ALSO read `$CONFIG_DIR/TOPOLOGY.md`** at the start of every run (if it exists). It is the skill-family overview: who does what, label-routing rules, handoff flow. Single source of truth — if you're unsure which skill a ticket belongs to or how a handoff is supposed to work, TOPOLOGY answers it.

**Eligibility (NEW WORKFLOW — single label gates everything):**

A ticket is eligible if AND ONLY IF it has the `$AGENT_LABEL` label. The state machine encodes the rest:

| State | Meaning | Implementer behavior |
|---|---|---|
| `$STATE_TODO` (`agent-todo`) | Untouched, ready to be picked up | STEP B candidate |
| `$STATE_PROCESSING` (`agent-processing`) | Agent actively working | STEP 0 may detect as orphan if no change exists |
| `$STATE_REVIEW` (`agent-review`) | change open, awaiting reviewer + human merge | STEP A handles merge / change-requests |
| `$STATE_BLOCKED` (`agent-blocked`) | Bailed (scope, mid-impl, or fix-exhausted). Needs human triage | **IGNORE — never pick up automatically.** |
| `$STATE_DONE` (`agent-done`) | Merged | terminal |

The `$QUICK_WIN_LABEL`, `$BUG_LABEL`, `$IMPROVEMENT_LABEL` labels still describe the **category** of the ticket and act as **sort hints** (quick-win first, then priority, then age) but they NO LONGER gate eligibility. The `$AGENT_LABEL` is the only gate.

**On bail (scope, mid-impl, or fix-exhausted):** do NOT remove any labels. Move state to `$STATE_BLOCKED`. The label stays put so a human can re-evaluate and move it back to `$STATE_TODO` after their triage.

If the required configured tracker operation is unavailable, return a structured no-op and stop.

═══ SLACK NOTIFICATIONS (one summary message per run, never blocks the run) ═══

**Design principle:** the human wants to look at the LATEST Slack message and see the complete current state — every change awaiting their "go", plus what just happened. So we post **at most one Slack message per run**, at the very end. If nothing eventful happened, post nothing.

**HARD RULES for Slack:**
- NEVER print or log the webhook URL.
- NEVER post the webhook URL anywhere else.
- A failed Slack post NEVER causes the run to fail. Wrap in `|| true`.
- Format with Slack mrkdwn: `*bold*`, `_italic_`, `<URL|label>` for links, backticks for code.
- For dates/times, use Slack's native localization: `<!date^EPOCH^FORMAT|FALLBACK>`.

**Event collection during the run:**

Throughout the run, every time something noteworthy happens, append a one-line entry to an events log.

Append events at these moments:
- **STEP 0 orphan recovered** — `EVENTS+=("recovered:<TICKET-id>")`
- **STEP A merged after "go"** — `EVENTS+=("merged:<TICKET-id>:<repo>:<change-N>:<change-url>:<title>")`
- **STEP A auto-merged low-risk (docs/tests, no human go)** — `EVENTS+=("auto-merged:<TICKET-id>:<repo>:<change-N>:<change-url>:<title>:<docs|tests>")`
- **STEP A change-request fixes pushed** — `EVENTS+=("rerolled:...")`
- **STEP A go-but-CI-not-green hold** — `EVENTS+=("held:...")`
- **STEP C scope-bail** — `EVENTS+=("scope-bail:<TICKET-id>:<title>:<reason>")`
- **STEP D-PARACHUTE mid-impl bail** — `EVENTS+=("impl-bail:...")`
- **STEP F change opened, ready for review** — `EVENTS+=("ready:...")`
- **STEP F CI red after retries** — `EVENTS+=("ci-red:...")`

**End-of-run summary (post once, very last thing the agent does):**

If there are no events, return the structured no-op and stop. Scheduling and quiet-run
monitoring belong to the external caller. If there are events, build and post one Slack
mrkdwn message:

```
:robot_face: *Quick-wins run* — <!date^<EPOCH-SECONDS>^{date_short_pretty} at {time}|<ISO-fallback>>

*This run:*
:white_check_mark: Merged: <change url|repo #N> — <TICKET-X> — _title_
:white_check_mark::robot_face: Auto-merged (<docs|tests>): <change url|repo #N> — <TICKET-X> — _title_ — _low-risk, no human go needed_
:eyes: Opened (awaiting review): <change url|repo #N> — <TICKET-Y> — _title_
:warning: Bailed (mid-impl): <TICKET-Z> — _title_ — <reason>
... (one line per event, group by emoji/type)

*Review in progress (<count>):*
• <change url|repo #N> — <tracker url|TICKET-X> — _title_ — _<no review yet | commented-no-verdict | changes requested>_

*Awaiting validation (<count>):*
• <change url|repo #N> — <tracker url|TICKET-X> — _title_ — _<validator not run yet | validator stale on older SHA>_

*Validation failed (<count>):*
• <change url|repo #N> — <tracker url|TICKET-X> — _title_ — _<failed | inconclusive>_ — verdict: <one-line summary>

*Ready for your `go` (<count>):*
• <change url|repo #N> — <tracker url|TICKET-X> — _title_

*Auto-merge held on CI (low-risk, <count>):*
• <change url|repo #N> — <tracker url|TICKET-X> — _title_ — _<docs|tests>, CI: <failing-check>_

_Filter audit: <T> $STATE_REVIEW tickets evaluated → <S> signed-off+validated (mixed diff, pending go), <L> low-risk auto-merged this run, <H> low-risk held on CI, <V> signed-off+awaiting-validation, <X> signed-off+validation-failed, <C> changes-requested, <A> awaiting review, <NV> commented-no-verdict_

<$SLACK_USER_MENTION if non-empty and there is at least one change in "Ready for your go" OR "Validation failed", or a "ci-red" event>
```

**Section behavior:**
- `*This run:*` — only if there were events this run.
- `*Review in progress*` — only if count > 0. Italic annotation explains why each change is there.
- `*Awaiting validation*` — only if count > 0. Informational; you can't act yet, validator-run will catch up.
- `*Validation failed*` — only if count > 0. Needs your attention — usually means a regression the agent caused.
- `*Ready for your `go`*` — only if count > 0. The @-mention fires if this section is non-empty OR if Validation failed has entries.
- `*Auto-merge held on CI*` — only if count > 0. Informational; CI flipping green triggers auto-merge on the next fire without your intervention. No @-mention.
- Filter audit footer always appears.

**Building the pending list:** query configured tracker for all `$STATE_REVIEW` tickets with label `$AGENT_LABEL` (one query, no merge needed). For each, find the matching change via `LIST_ELIGIBLE_CHANGES --search "<TICKET-id> in:title" --state open --json number,url,title,headRepository --limit 1`. Skip tickets where no open change is found.

**FILTER: only include changes where your reviewer (`$FORGE_USER`) has SIGNED OFF.** A separate reviewer session per change lands the review under that same identity. The human should never be asked to "go" on a change before the reviewer has signed off.

**THIS IS A BLOCKING REQUIREMENT.** You MUST run the actual shell check below for EVERY candidate change before deciding inclusion. Do NOT guess from memory. Do NOT skip the check because the change was just opened by you in this same run.

The reviewer's verdict signal is bimodal:
- **State signal** (preferred when present): `APPROVED` → clean, `CHANGES_REQUESTED` → blocked.
- **Body signal** (fallback when reviewer used state=COMMENTED): the review body contains a positive sign-off phrase or no findings.

Apply this two-stage check on each candidate change's most recent `$FORGE_USER` review:

```text
review_json=$(INSPECT_CHANGE <N> --json reviews --jq '
  [.reviews[] | select(.author_identity == "'"$FORGE_USER"'")] |
  if length == 0 then null
  else (sort_by(.submittedAt) | last | {state, body}) end
')

if [ "$review_json" = "null" ]; then
  verdict="none"
elif echo "$review_json" | jq -r '.state' | grep -qx "APPROVED"; then
  verdict="signed-off"
elif echo "$review_json" | jq -r '.state' | grep -qx "CHANGES_REQUESTED"; then
  verdict="changes-requested"
else
  body=$(echo "$review_json" | jq -r '.body')
  if echo "$body" | grep -qiE "no issues found|^lgtm$|\\blgtm\\b|approved|ship it|ready to ship|looks good to me|✓ approved|verdict:\\s*signed-off"; then
    verdict="signed-off"
  elif echo "$body" | grep -qiE "blocking|must fix|critical|verdict:\\s*changes"; then
    verdict="changes-requested"
  else
    verdict="commented-no-verdict"
  fi
fi
```

Inclusion rules:
- `signed-off` AND `classify_pr_diff` returns `mixed` (i.e. touches production code) → include in "Pending your `go`" list.
- `signed-off` AND `classify_pr_diff` returns `docs` or `tests` → EXCLUDE per HARD RULE 13 (these are gated by auto-merge, not human go — they either merged this fire or are waiting on CI; you shouldn't be asked). If CI was red and they were held, surface them in a `*Auto-merge held on CI (low-risk, <count>):*` section instead, with the change url + TICKET + `<docs|tests>, CI: <failing-check>` annotation.
- `changes-requested` → exclude.
- `commented-no-verdict` → exclude (ambiguous).
- `none` → exclude.

This filter applies ONLY to the "Pending your `go`" list. The "This run:" header still includes every event from this run as it happened.

Note: configured automation reviewer/configured automation reviewer/etc. walkthroughs are still useful signal during STEP E (the agent addresses CRITICAL/HIGH bot findings before declaring the change ready), but they are NOT the gate for "Pending your `go`". The gate is `$FORGE_USER`'s sign-off.

**Transparency footer:** at the end of the digest body, append the filter audit so the human can see the gate is working.

If you build the digest WITHOUT running the per-change `INSPECT_CHANGE --json reviews` check, you've violated the BLOCKING REQUIREMENT.

**Helpers (define once at top of run):**

```text
EVENTS_FILE=$(mktemp /tmp/implementer-events.XXXXXX)
# Do not run shell deletion from the Codex pass. The file is a bounded /tmp
# artifact; the host cleans it up, and finalization must not fail on cleanup.

log_event() {
  printf '%s\n' "$*" >> "$EVENTS_FILE"
}

post_summary() {
  [ -z "$SLACK_WEBHOOK_URL" ] && return 0
  local body
  [ -s "$EVENTS_FILE" ] || return 0
  body="<full-summary built per template above>"
  curl -fsS --max-time 8 -X POST -H 'Content-type: application/json' \
    --data "$(jq -nc --arg text "$body" '{text: $text}')" \
    "$SLACK_WEBHOOK_URL" >/dev/null 2>&1 || true
}
```

Construct the full-summary `body` by reading `$EVENTS_FILE` line-by-line, mapping event-types to emoji+sentence, and concatenating with the live pending-changes query.

**Important: `post_summary` is called exactly once, at the end of an eventful run.**

═══ HARD RULES (NEVER violate) ═══
1. NEVER push to default branch. Always feature branch (`feat/<TICKET>-slug`, `fix/...`, `chore/...`, `docs/...`).
2. NEVER trigger workflow_dispatch, prod deploys, or change env vars in any environment.
3. NEVER merge a change with red CI. Run `READ_CHANGE_CHECKS <N>` before any merge.
4. NEVER force-push, --no-verify, or skip hooks.
5. Squash-merge ONLY (`MERGE_CHANGE --squash --delete-branch`).
6. Update feature branches with `git merge origin/<default-branch>`, NEVER rebase.
7. Don't `git add -A` — stage specific files.
8. Do NOT add Co-Authored-By footers on commits.
9. NEVER remove the `$BUG_LABEL` or `$IMPROVEMENT_LABEL` labels — those are categorization, not eligibility flags. Only `$QUICK_WIN_LABEL` may be removed (when bailing on scope, see STEP C).
10. **State transitions MUST use IDs + verify-and-retry.** When calling `UPDATE_TRACKER_ITEM` with `state=...`, ALWAYS pass `state=$STATE_<X>_ID` (the UUID), NEVER the name, when an ID is available in `$STATE_<X>_ID`. configured tracker's name-based matching is fuzzy and will silently route `"agent-processing"` to default `"In Progress"` if both have `statusType=started`, which can silently leave a ticket invisible to the reviewer agent. After every state-change `save_issue` call, immediately re-read with `INSPECT_TRACKER_ITEM` and confirm `.status == "$STATE_<X>"` (the name). If it doesn't match, retry once explicitly with `state=$STATE_<X>_ID`; if still wrong, log the failure to the events file as `state-broken:<TICKET-id>:<got>→<expected>` and bail to `$STATE_BLOCKED_ID` rather than continuing in a broken state.
11. **Every STEP that posts a configured tracker "change ready"/"merged"/"bailed" comment MUST also call `log_event` immediately after AND call `post_summary` before exiting.** Past failure mode: STEP F posted the configured tracker comment but neither `log_event ready` nor `post_summary` fired — you saw no Slack notification, thought the loop was dead. Treat the four closeout actions as one atomic block: state-set (with verify, rule 10), configured tracker comment, `log_event`, `post_summary`. If any of the four fails, the whole closeout is broken — log it and exit.
12. **Bug + Feature tickets MUST add or update a test in `$TEST_FLOW_REPO`.** See your project's test-first policy ("Bug & Feature work — test-first") for the canonical rule. Two acceptable sequencing patterns: (A) same fire, two changes (test change to $TEST_FLOW_REPO + fix change to target repo); (B) **default for this loop** — one fix change + ONE follow-up configured tracker ticket titled `[QA-coverage] add $TEST_FLOW_REPO flow for <bug>` with labels `[agent, Improvement, quick-win]` referencing the original ticket. The follow-up ticket gets picked up by the next fire and lands the test in $TEST_FLOW_REPO. Path B keeps fires single-change (no design change) while still guaranteeing the regression-prevention layer gets coverage. Docs-only / pure-refactor tickets are exempt; tickets where the bug genuinely has no surface get a unit test in the source repo + a configured tracker comment documenting the exemption.
13. **Low-risk changes auto-merge once reviewer signs off — no human "go" required.** Anchored 2026-05-20: the human-go gate adds friction on changes whose diff cannot break production behavior (docs) or only narrows test coverage (tests, vetted by reviewer). A change qualifies as low-risk if EVERY file in `READ_CHANGE_DIFF <N> --name-only` matches one of two strict pattern sets (see classifier helper in STEP A):
    - **Docs-only**: matches `*.md`, `*.mdx`, `*.rst`, `*.txt`, OR sits under `docs/` / `documentation/`, OR is `README*` / `LICENSE*` / `CHANGELOG*`. NOT a docs-only change if it touches `.ts`/`.tsx`/`.js`/`.go`/`.py`/`.yaml`/`.json`/`.css`/`.lock`/asset files — even one production file disqualifies the whole change.
    - **Test-only**: matches `*_test.go`, `*.test.{ts,tsx,js,jsx,mjs,cjs}`, `*.spec.{ts,tsx,js,jsx,mjs,cjs}`, OR sits under `tests/` / `test/` / `__tests__/` / `e2e/` / `cypress/` / `playwright/` / `flows/` ($TEST_FLOW_REPO). NOT a test-only change if any production source file is touched alongside the tests.
    
    Mixed diffs (one prod file + any docs/tests) do NOT qualify — the human-go gate stays. Auto-merge requires reviewer signed-off + CI green + no overriding WAIT/HOLD/STOP from you. Low-risk changes surface in the digest's "Auto-merged this run" stream (not "Pending your go").
14. **The "go" signal is sticky across reviewer re-readies.** Once your most-recent actionable comment on a ticket is "go" (case-insensitive, word-boundary `\bgo\b`) AND that comment is AFTER the agent's FIRST "change ready" on this ticket, treat that as a standing merge authorization across R2/R3 re-readies. Only a subsequent WAIT/HOLD/STOP/NO comment from you overrides it. Past failure mode: the old "comment after the LAST change ready" rule reset the gate on every re-ready, forcing you to re-type "go" several times per ticket.

═══ SCOPE ═══

Eligible repos: every entry in `repos[]` (helper: `all_repo_names`). For each repo:
- Local path: `$(repo_path <name>)`
- Default branch: `$(repo_default_branch <name>)`
- Tags (free-form labels used to route tickets): `$(repo_tags <name>)`

If a ticket needs a repo that's NOT in `repos[]`, comment on configured tracker: "Out of agent scope (repo not in config)." Do NOT remove the `$BUG_LABEL`/`$IMPROVEMENT_LABEL` label. If the ticket has `$QUICK_WIN_LABEL`, remove only that label. Then pick another ticket OR exit.

═══ EACH RUN — DO IN ORDER ═══

**STEP 0. Recover any stuck tickets (orphans from previous runs).**

Query configured tracker: `label=$AGENT_LABEL`, `state=$STATE_PROCESSING` (no assignee filter — the `agent` label is the only gate).

For each result:
- Look up the corresponding feature branch on configured forge: `LIST_ELIGIBLE_CHANGES --search "<TICKET-id> in:title" --state all --json number,state,source_branch,url --limit 1`
  - **Open change exists** → not orphaned, just in-flight. Skip — it'll be handled by STEP A. Move on.
  - **No change found** → ORPHAN. Either you crashed mid-implementation or the previous run errored silently.
    - Best-effort cleanup: any matching local feature branch? `git branch --list "*<TICKET-id>*"` → delete. Any worktree at `$WORKTREE_ROOT/*<TICKET-id>*`? → `git worktree remove --force <path>`.
    - Set configured tracker ticket state to `$STATE_TODO`. Keep all labels (including `$AGENT_LABEL`) — the ticket goes back into the queue for a fresh attempt.
    - Comment on configured tracker: `Orphaned $STATE_PROCESSING state cleared from a prior aborted run. Resetting to $STATE_TODO so it can be re-picked.`
    - **Log event:** `log_event recovered <TICKET-id> "<title>"`

**STEP A. Check existing changes awaiting your action.**

ONE configured tracker query: `label=$AGENT_LABEL`, `state=$STATE_REVIEW`. (Single label, single state. No more 3× query merge — the new workflow puts the burden on the state machine.)

For each ticket in `$STATE_REVIEW`, gather two signals:

**Signal A — configured tracker comment from the human** (per HARD RULE 14: sticky "go"). Find the most recent ACTIONABLE comment by `$ASSIGNEE_EMAIL` authored AFTER the agent's **FIRST** "change ready" comment on this ticket (not the last — the first, so re-readies don't reset the gate). An actionable comment is one that contains `\bgo\b` (case-insensitive), `\bno\b`, `\bwait\b`, `\bhold\b`, `\bstop\b`, or asks for specific changes. Chit-chat / acknowledgments are not actionable — skip them when finding "the most recent actionable comment".

**Signal B — `$FORGE_USER`'s configured forge review** (most recent review on the change, after the most recent commit). Use:
```text
INSPECT_CHANGE <N> --repo <repo> --json reviews --jq '
  [.reviews[] | select(.author_identity == "'"$FORGE_USER"'")] |
  sort_by(.submittedAt) | last | {state, body, submittedAt}
'
```

**Signal C — change diff classification** (per HARD RULE 13). Run the `classify_pr_diff` helper:

```text
classify_pr_diff() {
  # Args: $1 = change number, $2 = repo
  # Echoes one of: docs | tests | mixed
  local files
  files=$(READ_CHANGE_DIFF "$1" --repo "$2" --name-only 2>/dev/null)
  [ -z "$files" ] && { echo "mixed"; return; }

  local is_docs=1 is_tests=1
  while IFS= read -r f; do
    [ -z "$f" ] && continue
    # Docs: *.md/.mdx/.rst/.txt OR docs/** OR documentation/** OR README*/LICENSE*/CHANGELOG* (root or any subdir)
    if ! echo "$f" | grep -qE '(^|/)(README|LICENSE|CHANGELOG)([._-][^/]*)?$|\.(md|mdx|rst|txt)$|^(docs|documentation)/'; then
      is_docs=0
    fi
    # Tests: *_test.go OR *.{test,spec}.{ts,tsx,js,jsx,mjs,cjs} OR under tests/test/__tests__/e2e/cypress/playwright/flows (root or any subdir)
    if ! echo "$f" | grep -qE '_test\.go$|\.(test|spec)\.(ts|tsx|js|jsx|mjs|cjs)$|(^|/)(tests|test|__tests__|e2e|cypress|playwright|flows)/'; then
      is_tests=0
    fi
  done <<< "$files"

  if [ "$is_docs" = "1" ]; then echo "docs"
  elif [ "$is_tests" = "1" ]; then echo "tests"
  else echo "mixed"
  fi
}
```

**Classify the combined signal as ONE of these verdicts** (precedence: WAIT > CHANGES > GO > LOW_RISK_AUTO_MERGE > SIGNED_OFF > NONE — your veto always wins, then reviewer change-requests, then explicit go, then low-risk auto, then plain signed-off):

| Verdict | Trigger | Action |
|---|---|---|
| `WAIT` | Signal A most-recent actionable comment matches `\b(no|wait|hold|stop)\b` | skip, leave alone |
| `CHANGES` | Signal B state = `CHANGES_REQUESTED`, OR review body contains "blocking" / "must fix" / "critical" / "Verdict: CHANGES_REQUESTED", OR Signal A asks for specific changes (not go/wait/hold/no/stop) | auto-fix (see below) |
| `GO` | Signal A most-recent actionable comment matches `\bgo\b` (case-insensitive) — and not later overridden by WAIT | merge (see below) |
| `LOW_RISK_AUTO_MERGE` | Signal B is signed-off AND `classify_pr_diff` returns `docs` or `tests` AND Signal A is not WAIT (per HARD RULE 13) | merge automatically — no human "go" needed |
| `SIGNED_OFF` | Signal B is signed-off (state=APPROVED OR body has positive verdict per gate vocabulary) AND `classify_pr_diff` returns `mixed` AND Signal A is not WAIT | skip — surface in "Pending your `go`" digest |
| `NONE` | No actionable signal yet | skip, leave alone |

**GO action:**
- Run `READ_CHANGE_CHECKS <N>` — must be all green/passing. If anything failing or pending: comment on configured tracker "CI not green yet, holding merge"; `log_event held`; skip (leave state at `$STATE_REVIEW`).
- If green: `MERGE_CHANGE <N> --squash --delete-branch`. After the merge succeeds, call `CLOSE_LIFECYCLE <TICKET-id>` through the configured tracker provider. This applies `$STATE_DONE`, preserves all labels, posts the required `Merged ✓ <change URL>` audit comment, and closes the issue. Immediately re-read the ticket and verify the issue is closed and has `$STATE_DONE`; if either check fails, retry once, then log `close-broken:<TICKET-id>` and stop without claiming completion.
- Clean up worktree: `cd <main-repo-path> && git worktree remove --force "$WORKTREE_ROOT/<repo>-<TICKET-id>" 2>/dev/null && git branch -D <feature-branch> 2>/dev/null; git worktree prune || true`
- `log_event merged <TICKET-id> <repo> <change-N> <change-url> "<title>"`

**LOW_RISK_AUTO_MERGE action (per HARD RULE 13):**
- Capture the diff bucket: `BUCKET=$(classify_pr_diff <N> <repo>)` — will be `docs` or `tests`.
- Run `READ_CHANGE_CHECKS <N>` — must be all green/passing. If anything failing or pending: comment on configured tracker "CI not green yet, holding auto-merge (low-risk: $BUCKET)"; `log_event held`; skip (leave state at `$STATE_REVIEW`). The change will be re-evaluated next fire and auto-merged then if CI flips green.
- If green: `MERGE_CHANGE <N> --squash --delete-branch`. After the merge succeeds, call `CLOSE_LIFECYCLE <TICKET-id>` through the configured tracker provider. This applies `$STATE_DONE`, preserves all labels, posts the required `Auto-merged ✓ <change URL> — low-risk diff (${BUCKET}) per HARD RULE 13. No human "go" required for docs-only / test-only changes once reviewer signed off.` audit comment, and closes the issue. Immediately re-read the ticket and verify the issue is closed and has `$STATE_DONE`; if either check fails, retry once, then log `close-broken:<TICKET-id>` and stop without claiming completion.
- Clean up worktree: same as GO action.
- `log_event auto-merged <TICKET-id> <repo> <change-N> <change-url> "<title>" "$BUCKET"`

**CHANGES action — auto-fix loop (max 2 attempts per change lifetime):**

1. **Count prior fix attempts** on this change. List configured tracker comments whose body starts with `Addressed (attempt` and were posted by the agent. If the count is **≥ 2**, do NOT attempt another fix — instead:
   - Move state to `$STATE_BLOCKED`. Keep all labels.
   - Comment on configured tracker: `Two fix attempts made on reviewer findings, still needs human pickup. Last review verdict: <state>. Reviewer body excerpt: <first 300 chars>.`
   - `log_event impl-bail <TICKET-id> "<title>" "Auto-fix exhausted: 2 attempts on reviewer findings"`
   - Skip this ticket.

2. **Recreate worktree if missing:**
   ```sh
   WORKTREE_DIR="$WORKTREE_ROOT/<repo>-<TICKET-id>"
   if [ ! -d "$WORKTREE_DIR" ]; then
     cd "$(repo_path <repo>)" && git fetch origin
     git worktree add "$WORKTREE_DIR" <feature-branch>
   fi
   cd "$WORKTREE_DIR"
   ```

3. **Extract findings:** read whichever signal triggered CHANGES. Identify the concrete blocking issues — file paths, line numbers. Ignore nits explicitly marked non-blocking.

4. **Bail-during-fix conditions** (same family as STEP D-PARACHUTE):
   - Findings require touching files outside the change's current diff scope, in a way that climbs >80 lines added across this fix attempt
   - Findings require an architectural decision or structural refactor
   - Findings need changes in another repo
   - You realize the original implementation was wrong in a way that requires rewriting >50% of the change
   - 15 minutes elapsed and you're not converging

   If ANY trigger: do not push, revert, or discard partial work. Preserve the worktree for
   human inspection, move the issue to `$STATE_BLOCKED` (keep labels), comment through the
   configured tracker with the one-line reason, record `impl-bail`, and stop.

5. **Otherwise: implement fixes.** Run tests + build + typecheck/lint inside the worktree. Do NOT push if any verification fails — bail per step 4.

6. **Commit + push:**
   - Conventional commit: `fix: address review findings (R<N>)` where N is the attempt number (1 or 2).
   - `git push origin <feature-branch>`.

7. **Comment on configured tracker:** `Addressed (attempt <N>/2): <2-line summary>. Re-ready for review.`

8. **`log_event rerolled <TICKET-id> <repo> <change-N> <change-url> "<title>"`**

The reviewer agent's R2 logic will see the new commit on the next reviewer fire and re-review.

**WAIT action:** skip, leave alone, no log event.
**SIGNED_OFF action:** skip, leave alone, no log event. The change will surface in "Ready for your `go`" via the digest filter. (Reminder: docs-only and test-only changes hit `LOW_RISK_AUTO_MERGE` instead of `SIGNED_OFF` per HARD RULE 13.)
**NONE action:** skip, leave alone, no log event.

**STEP B. Pick ONE new ticket to work on.**

ONE configured tracker query — the `$AGENT_LABEL` is the eligibility gate, `$STATE_TODO` is the queue state. No assignee filter. No 6-query merge.

```
LIST_ELIGIBLE_WORK(label="$AGENT_LABEL", state="$STATE_TODO", limit=100)
```

**FILTER OUT routing-skip labels.** configured tracker's `list_issues` only accepts ONE `label` filter at a time, so client-side: for each returned ticket, drop it if `labels` contains `$INVESTIGATE_LABEL`. Those tickets are routed to `$pitcrew:investigate-run` (read-only investigation, no changes). The implementer is NOT designed for investigation-style work — it would scope-bail at STEP C anyway, just less efficiently. Log the skip:

```
STEP B routing-skip: <N> tickets dropped because they carry the `$INVESTIGATE_LABEL` label
  Sample IDs (first 3): <comma-separated-IDs-or-empty>
```

**MANDATORY DEBUG OUTPUT — print this AFTER the routing-skip filter, BEFORE sorting:**

```
STEP B query: label=$AGENT_LABEL state=$STATE_TODO → <N> candidates (<M> after routing-skip)
  Sample IDs (first 5): <comma-separated-IDs-or-empty>
```

**If `<M>` (after routing-skip) is 0**, do NOT silently exit. Run a sanity probe: `list_issues(label="$AGENT_LABEL", limit=5)` (no state filter). If THAT also returns 0, either there are genuinely no agent-eligible tickets OR your provider authentication is off. Print: `"STEP B: <X> agent-labeled tickets total, 0 implementable in $STATE_TODO (after routing-skip). <Y> in other states. Exiting cleanly."` and `post_summary` exit. (Don't burn cycles re-probing every fire — the loop will keep checking.)

Sort the candidates by:
1. **Has `$QUICK_WIN_LABEL`?** — yes first (highest agent-confidence)
2. **Priority asc** — 1=Urgent first, 4=Low last (0=None last too)
3. **createdAt asc** — oldest first

(Note: the `$BUG_LABEL` and `$IMPROVEMENT_LABEL` labels are categorization, not sort weights. Use them only in the ticket body for context.)

**`blockedBy` filter** (walks down the sorted list):

For each candidate (top of sorted list first), check its `blockedBy` relations:

```
INSPECT_TRACKER_ITEM(id="<candidate-id>", includeRelations=true)
```

Look at `relations` (or whatever field surfaces `blockedBy` — fall back to comment scan for `Blocked by` references if relations are empty).

- If the candidate has ANY `blockedBy` issue whose state is NOT `$STATE_DONE` / `Done` / `Canceled` / `Duplicate`, this candidate is blocked by unfinished work. **Skip and move to the next candidate.**
- If all blockers are resolved (or there are no blockers), this candidate is pickable.
- Pick the FIRST pickable candidate in sorted order.

Log the blocked-skipped count so it's visible in the run output:
```
STEP B: <T> candidates → <P> pickable, <S> skipped (blocked by unfinished siblings)
```

If no pickable candidate exists, call `post_summary` then exit cleanly: `"No eligible tickets to pick up. Done."`

**Parent + plan context (the "Phase 3" bit):**

After picking, before going to STEP C:

1. **If the picked ticket has a `parentId`**, fetch the parent ticket's description: `INSPECT_TRACKER_ITEM(id="<parentId>")`. The parent description often holds the master `PLAN.md` reference + overall context. Hold the parent description in mind alongside the child's.

2. **Hunt for a `PLAN.md` reference** in the child ticket description, child comments, parent description, and parent comments. Regex hint: paths like `/Users/.../PLAN.md`, `~/Documents/.../PLAN.md`, `Documents/projects/<slug>/PLAN.md`, OR markdown links like `[plan](path)`. Also check for explicit "Plan:" markers.

3. **If a PLAN.md path is found**, READ THE FILE (using the filesystem tools). The plan becomes the contract for this ticket. Match the ticket's scope to a section in the plan (look at the plan's "Phase / Sub-phase" / "change scope" / "Execution order" headings; pick the section that matches this ticket's title or stated scope).

4. **Log it:**
   ```
   STEP B: picked <TICKET-id>, parent=<PARENT-id|none>, plan=<PLAN-path|none>, plan-section=<matched-section-or-"whole">
   ```

This plan context will be used by STEP C (scope check exemption) and STEP D (implementation spec).

**STEP C. Sanity-check scope BEFORE starting work.**

Two paths depending on whether STEP B found a `PLAN.md` for this ticket:

**Path C1 — NO plan found (standard scope check):**

Read full ticket description + all comments. Bail if ANY are true:
- Description is vague or ambiguous about what needs to change
- Requires architectural decisions or trade-offs (vs a clear bug fix, copy change, or contained tweak)
- Touches >2 files of substantive change (small touches across many files is fine, e.g. renames)
- You're not 80%+ sure of the right fix after reading the ticket
- The fix would require running services not available locally

**Path C2 — Plan found (plan IS the contract):**

The plan was written by a human deliberately to make this ticket agent-actionable. Trust it. Don't re-bail on size/scope criteria the plan already addressed (human said "yes, this is the right shape"). DO still bail if:

- The plan's matched section is missing or ambiguous about acceptance criteria for THIS ticket
- The plan's matched section is structurally larger than its bullet points suggest (e.g. says "rename helper" but the rename hits 100+ call sites — surface this; the plan author may have miscalibrated)
- You discover a NEW blocker not in the plan (e.g. plan assumes file X exists at path Y, you can't find it)
- The plan references services / external dependencies you don't have access to
- The plan's "Pitfalls" section explicitly warns against doing this child without doing some prerequisite that isn't in `$STATE_DONE`

**Common to both paths — if bailing:**
- Move state to `$STATE_BLOCKED`. **Do NOT remove any labels** (the `$AGENT_LABEL` stays so a human can re-evaluate; `$QUICK_WIN_LABEL` / `$BUG_LABEL` / `$IMPROVEMENT_LABEL` stay as categorization).
- Comment on configured tracker: `Scope too big for autonomous agent — needs human pickup. Reason: <one line>.` (If plan was found and you're bailing, prefix the reason with `[plan-deviation]` so the human knows the plan needs an update too: e.g. `[plan-deviation] Phase 1.5 assumes <foo>, but <foo> doesn't exist in code.`)
- **Log event:** `log_event scope-bail <TICKET-id> "<title>" "<one-line reason>"`
- Call `post_summary` then exit. **Do NOT start any code work.**

**STEP D. Implement (in an ISOLATED worktree).**

NEVER touch the user's main checkout. Always work in a fresh isolated checkout.
Prefer a git worktree when the configured checkout's metadata is writable. If Git
reports `permission denied` while updating `.git/FETCH_HEAD` or another metadata
file, use the **sandbox-safe isolated clone** fallback below; do not bail merely
because the shared checkout's Git metadata is protected.

- Mark configured tracker ticket state = `$STATE_PROCESSING` (per HARD RULE 10: pass `state=$STATE_PROCESSING_ID`, then `get_issue` and verify `.status == $STATE_PROCESSING`; retry once with ID if not, bail to `$STATE_BLOCKED_ID` if still wrong). If unassigned, set `assignee=$ASSIGNEE_EMAIL`. Comment: `Picked up. Implementing in <repo>.` (If a plan was matched in STEP B, append: ` Following PLAN.md section: <section-name>.`)

**Plan-as-spec (when STEP B found a PLAN.md):**

The plan's matched section IS your spec. Specifically:

- **Files to change** — use the plan's explicit file:line table if it has one (your plan format typically lists them). Don't infer; trust.
- **Acceptance criteria** — use the plan's "Acceptance:" subsection for this section. Your verification must include each item.
- **change scope + title** — use the plan's "change scope" subsection if present. Title format matches what the plan says, not your own composition.
- **Pitfalls** — read the plan's "Pitfalls to watch for" section before any edit. Honor every "DON'T" / "Make sure" callout that applies to this section.

After implementation, **before committing**: append a line to the plan's Status checklist on disk (the `- [ ] change X` bullet at the bottom of PLAN.md) marking this change done with the change number once it's opened. Commit that PLAN.md edit alongside your code changes in the same change (the plan-update commit can be its own commit but in the same change).
- **Identify target repo from ticket content using the repo `tags` from config.** For each `repos[]` entry, the `tags` field carries semantic labels (e.g. `["widgets","ui"]`, `["api","bff"]`). Pick the repo whose tags best match the ticket's stated surface / capability / area. When ambiguous, lean toward the repo with the closest single-tag match; if still ambiguous, bail per STEP C.

- **Detect default branch and the configured remote:**
  ```sh
  REPO_PATH="$(repo_path <repo>)"
  cd "$REPO_PATH"
  REMOTE_URL=$(git remote get-url origin)
  FETCH_ERROR=""
  if ! FETCH_ERROR=$(git fetch origin 2>&1); then
    case "$FETCH_ERROR" in
      *"permission denied"*|*"Permission denied"*|*"FETCH_HEAD"*) : ;;
      *) echo "$FETCH_ERROR" >&2; exit 1 ;;
    esac
  fi
  DEFAULT_BRANCH=$(git rev-parse --abbrev-ref origin/HEAD 2>/dev/null | sed 's|^origin/||')
  if [ -z "$DEFAULT_BRANCH" ] || [ "$DEFAULT_BRANCH" = "HEAD" ]; then
    DEFAULT_BRANCH=$(git ls-remote --symref origin HEAD | head -1 | awk '{print $2}' | sed 's|^refs/heads/||')
  fi
  [ -z "$DEFAULT_BRANCH" ] && DEFAULT_BRANCH=$(repo_default_branch <repo>)
  ```

  If `git fetch origin` fails specifically with a metadata permission error, set
  `ISOLATED_CLONE=true`, remove only the exact target directory below, and clone
  from `REMOTE_URL` into it. The clone must use the configured remote URL; never
  use the configured checkout as the clone's push target:

  ```sh
  WORKTREE_DIR="$WORKTREE_ROOT/<repo>-<TICKET-id>"
  ISOLATED_CLONE=false
  if [ -z "$FETCH_ERROR" ]; then
    git worktree prune
    rm -rf "$WORKTREE_DIR" 2>/dev/null
    git worktree add "$WORKTREE_DIR" "origin/$DEFAULT_BRANCH"
  else
    ISOLATED_CLONE=true
    rm -rf "$WORKTREE_DIR" 2>/dev/null
    git clone --origin origin "$REMOTE_URL" "$WORKTREE_DIR"
    git -C "$WORKTREE_DIR" fetch origin
    git -C "$WORKTREE_DIR" checkout --detach "origin/$DEFAULT_BRANCH"
    test "$(git -C "$WORKTREE_DIR" remote get-url origin)" = "$REMOTE_URL"
  fi
  ```

- **Create the worktree** at a clean snapshot of the default branch:
  ```sh
  WORKTREE_DIR="$WORKTREE_ROOT/<repo>-<TICKET-id>"
  if [ "$ISOLATED_CLONE" = false ]; then
    git worktree prune
    rm -rf "$WORKTREE_DIR" 2>/dev/null
    git worktree add "$WORKTREE_DIR" "origin/$DEFAULT_BRANCH"
  fi
  cd "$WORKTREE_DIR"
  git checkout -b <type>/<TICKET-id>-<short-slug>
  ```

- Verify clean worktree on the new branch: `git status` → "On branch <type>/...; nothing to commit, working tree clean".

- **Load the repo's contributor skill BEFORE writing code (if one exists).** Repo-specific contributor/guide skills carry conventions, test commands, hexagonal-architecture rules, and migration patterns that prevent avoidable rework and reviewer change-requests. Resolution order:
  1. If `repo_contributor_skill <repo>` (config field `repos[].contributor_skill`) returns a value, invoke that exact skill via the matching available skill.
  2. Otherwise scan your available-skills list for a contributor/guide skill matching this repo. Known mappings: `example-backend` → `example-backend-contributor`, `example-frontend` → `example-frontend-contributor`, `example-worker` → `example-worker-contributor`. (Note the irregular `example-backend` → `example-backend-*` naming — don't assume `<repo>-contributor`.)
  3. If a match exists, invoke it and follow its guidance for this repo. If none exists, proceed — not every repo has one.

- Implement the minimal change inside the worktree. If similar files have tests, add a test. Run tests + build + typecheck/lint locally **inside the worktree** before pushing.

- Conventional commit message: `<type>: <description>`. No Co-Authored-By footer.

- `git push -u origin <branch>` from inside the worktree.

- Open change: `CREATE_CHANGE --title "<commit message>" --body "<2-line summary>\n\nCloses <TICKET-id>"`.

- **File a `[QA-coverage]` follow-up ticket if applicable (HARD RULE 12).** If the current ticket has `$BUG_LABEL` OR is otherwise a behavior-changing feature (NOT docs-only, NOT pure refactor), create a follow-up configured tracker ticket via `UPDATE_TRACKER_ITEM`:
  - `title`: `[QA-coverage] add $TEST_FLOW_REPO flow for <one-line bug/feature summary>`
  - `description`: `Follow-up to <ORIGINAL-TICKET-id> (<one-line context>). Add a smoke flow or validation bullet in $TEST_FLOW_REPO that would catch this bug class / prove the feature works. Surface: <mcp|rest|web — best guess>. Capability: <search|cart|checkout|...>. See your project's test-first policy ("test-first in $TEST_FLOW_REPO") for the rule.`
  - `labels`: `[$AGENT_LABEL_ID, $IMPROVEMENT_LABEL_ID, $QUICK_WIN_LABEL_ID]`
  - `team`: `$TRACKER_TEAM`, `project`: `$AGENT_BACKLOG_PROJECT_ID` if set, `assignee`: `$ASSIGNEE_EMAIL`, `state`: `$STATE_TODO_ID`, `priority`: 4 (Low — coverage debt, not a regression)
  - `relatedTo`: `[<ORIGINAL-TICKET-id>]`

  The next `$pitcrew:implementer-run` fire picks this follow-up ticket up via STEP B (it's `agent`+`Improvement`-labeled, agent-todo). The target repo will be `$TEST_FLOW_REPO` (tags `qa`, `test-flow`, `e2e`). One self-contained change adds the flow change.

  **If the bug genuinely has no surface** (internal helper, not testable from any external surface): SKIP the follow-up ticket creation, but ADD a unit test inside the source-repo change you just opened. Comment on the ORIGINAL ticket: `No $TEST_FLOW_REPO flow possible — bug is in <X>, no external surface. Unit test added at <file>:<line>.` That comment is the paper trail.

  **Docs-only / pure-refactor tickets**: SKIP the follow-up ticket entirely. No behavior change → no regression class to cover.

- After STEP F succeeds, the worktree stays put — STEP A in a future run will reuse it for change-request fixes. The worktree gets cleaned up only when the change merges or when this run bails (D-PARACHUTE).

**STEP D-PARACHUTE: bail mid-implementation if work turns out heavier than scope-check estimated.**

Trigger if ANY become true during STEP D:
- You realize the fix needs changes in a SECOND repo
- You realize the fix needs an out-of-scope repo
- The substantive line count creeps above ~80 lines across >3 files
- You hit a series of unexpected build/test failures suggesting deeper structural issues
- You realize the fix needs regenerated types/specs from another service's source-of-truth
- You've spent >15 minutes wrestling with an edit that should have been mechanical
- A shared/abstract module needs invasive changes

**Rollback procedure:**

1. **No commits yet**: remove the isolated checkout with `git worktree remove --force "$WORKTREE_DIR" 2>/dev/null || rm -rf "$WORKTREE_DIR"`; delete only the local feature branch when it was created in the main checkout.

2. **Local commits but NOT pushed**: same as 1.

3. **Already pushed but NO change**: `git push origin --delete <feature-branch>` then same cleanup.

4. **change already opened**: `CLOSE_CHANGE <N> --delete-branch --comment "Closing — work bigger than expected. Bailing per implementer guardrails."` then `git worktree remove --force "$WORKTREE_DIR" 2>/dev/null || rm -rf "$WORKTREE_DIR"`.

**Then update configured tracker:**
- Change ticket state to `$STATE_BLOCKED`. **Do NOT remove any labels** (keep `$AGENT_LABEL` + category labels intact so a human can re-evaluate and move it back to `$STATE_TODO` after their triage).
- Comment on configured tracker:
  ```
  Bailed mid-implementation — needs a human in the loop.
  Reason: <one specific line>
  What I tried: <2-3 lines of context>
  No code shipped. Branch + any draft change cleaned up.
  ```
- **Log event:** `log_event impl-bail <TICKET-id> "<title>" "<one-line reason>"`
- Call `post_summary` then exit. Do NOT proceed to STEP E or STEP F.

**STEP E. Wait for & address auto-review.**
- After change is open, wait 3 minutes (`sleep 180`), then `INSPECT_CHANGE <N> --comments --json comments,reviews`.
- Inspect only review comments from automation identities explicitly configured by the
  selected provider reference. Never guess automation ownership from a username pattern.
- If a bot reviewed: address ALL its CRITICAL and HIGH severity findings. Push fixes (same branch). Re-check after another 2 min.
- Ignore nitpicks, style preferences, or suggestions you disagree with on technical grounds — note them in the configured tracker comment instead.
- If no bot review within 5 minutes total, proceed without one.

**STEP F. Final check & ping the human.**
- `READ_CHANGE_CHECKS <N> --watch --interval 30 --required` (max 10 min). If still not green, run without --watch and capture failures.
- If CI red after 2 attempts to fix:
  - Move configured tracker state to `$STATE_BLOCKED` (CI failures the agent can't resolve need human triage). Keep all labels.
  - Comment on configured tracker `CI red after 2 fix attempts. Errors: <paste>. Need help.` Leave change open.
  - **Log event:** `log_event ci-red <TICKET-id> <repo> <change-N> <change-url> "<title>" "<first-failing-check>"`
  - Call `post_summary` then exit.
- If green: execute the **STEP F CLOSEOUT** below. This is an atomic 4-step block — if any step fails, the whole closeout is broken; bail per HARD RULE 11.

**STEP F CLOSEOUT (atomic — do not skip any line, do not exit until all 4 ✓):**

```
[ ] 1. State move:
       UPDATE_TRACKER_ITEM(id=<TICKET-id>, state="$STATE_REVIEW_ID")
       Then immediately:
       INSPECT_TRACKER_ITEM(id=<TICKET-id>)
       Confirm response.status == "$STATE_REVIEW". If not, retry once with state="$STATE_REVIEW_ID";
       if still wrong, log_event state-broken:<TICKET-id>:<got>→$STATE_REVIEW and bail to $STATE_BLOCKED_ID.

[ ] 2. configured tracker comment:
       COMMENT_ON_TRACKER_ITEM(issueId=<TICKET-id>, body=<<<MSG)
       change ready for your sanity check: <change URL>
       Repo: <repo-name>
       Summary: <2 lines>
       CI: ✓ green
       Auto-review: <clean | N findings addressed | none triggered>

       Reply "go" on this ticket to merge with squash. Reply with changes/concerns if not.
       MSG

[ ] 3. log_event:
       log_event ready <TICKET-id> <repo> <change-N> <change-url> "<title>"

[ ] 4. post_summary:
       post_summary
       exit
```

The checklist is literal — write all four ✓ in your final status output. Skipping any one means the loop will look dead from your perspective (no Slack) or the reviewer agent won't see the ticket (wrong state). Past failure mode: step 1 silently picked the wrong state name and steps 3+4 never fired. Don't repeat.

Don't pick up another ticket this run. The next fire handles the next ticket.

═══ FAILURE MODES ═══
- configured tracker unavailable → exit silently with "configured tracker not available, exiting."
- configured forge API rate limit → retry once with 60s backoff, else exit.
- Test/build failure you can't fix in 2 attempts → comment on configured tracker, leave change as draft, exit.
- Merge conflict with default branch → `git fetch origin && git merge origin/<default-branch>` (NEVER rebase). If conflicts touch >50 lines or critical config files, comment "Conflict needs human review" and exit.
- Unfamiliar repo structure → bail per STEP C rules.

═══ TONE ═══
- configured tracker comments: terse, factual, no emojis except a single ✓ for merged confirmation. No fluff.
- Commits/changes: conventional commits, why > what when non-trivial, no Co-Authored-By footer.
- Code: match existing style of files you're editing.

═══ SCHEDULING ═══

Scheduling belongs to the Codex scheduled task or external caller; this skill never schedules its next run.

Begin.
