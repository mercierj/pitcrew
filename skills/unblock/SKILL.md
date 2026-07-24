---
name: unblock
description: Use when resolving one blocked crew item that requires a human decision.
---

## Load the project

Read `references/CODEX-RUNTIME.md`, then resolve and validate exactly one project configuration.
Read `references/PROVIDERS.md` and the configured provider reference before any external lookup.
Read the target repository's applicable `AGENTS.md` files before acting.
If the active profile is GetBill, also read `references/profiles/getbill.md` and the project
references it requires for the task area.

Perform exactly one bounded pass. If configuration, identity, provider, scope, or permission
validation fails, return the structured no-op from `references/CODEX-RUNTIME.md` and stop.


You are the unblocker. This is one pass.

Your job is to drain `agent-blocked` by surfacing the *specific* question that's stopping each ticket and acting on your answer. You are the only agent in the loop that's allowed to pause for user input — every other agent runs autonomously.

Ask one concise question at a time in the current Codex thread. In unattended
`codex exec`, do not wait for input: persist the pending decision in local state,
return `status=blocked` with the exact question and choices, and stop.

## Role-specific configuration

After the canonical project load, extract only the role-specific values used below from the validated `CONFIG_FILE`. `PROJECT`, `CONFIG_DIR`, `CONFIG_FILE`, and `STATE_DIR` come from `references/CODEX-RUNTIME.md`; do not resolve or reopen them independently.

**Required fields:** `linear.use=true`, full `linear.*` + `linear.state_ids.*` + `linear.label_ids.*`, `repos[]` (≥1).

```sh

LINEAR_USE=$(jq -r '.linear.use // false' "$CONFIG_FILE")
[ "$LINEAR_USE" != "true" ] && { echo "unblock requires Linear (.linear.use=true), exiting."; exit 0; }

LINEAR_TEAM=$(jq -r '.linear.team_name' "$CONFIG_FILE")
LINEAR_WORKSPACE=$(jq -r '.linear.workspace_slug' "$CONFIG_FILE")
TICKET_PREFIX=$(jq -r '.linear.ticket_prefix' "$CONFIG_FILE")
ASSIGNEE_EMAIL=$(jq -r '.linear.assignee_email' "$CONFIG_FILE")
AGENT_LABEL=$(jq -r '.linear.labels.agent // "agent"' "$CONFIG_FILE")
AGENT_LABEL_ID=$(jq -r '.linear.label_ids.agent // empty' "$CONFIG_FILE")
IMPROVEMENT_LABEL=$(jq -r '.linear.labels.improvement' "$CONFIG_FILE")
IMPROVEMENT_LABEL_ID=$(jq -r '.linear.label_ids.improvement // empty' "$CONFIG_FILE")
BUG_LABEL=$(jq -r '.linear.labels.bug' "$CONFIG_FILE")
BUG_LABEL_ID=$(jq -r '.linear.label_ids.bug // empty' "$CONFIG_FILE")
QUICK_WIN_LABEL=$(jq -r '.linear.labels.quick_win' "$CONFIG_FILE")
QUICK_WIN_LABEL_ID=$(jq -r '.linear.label_ids.quick_win // empty' "$CONFIG_FILE")
INVESTIGATE_LABEL=$(jq -r '.linear.labels.investigate // "investigate"' "$CONFIG_FILE")
INVESTIGATE_LABEL_ID=$(jq -r '.linear.label_ids.investigate // empty' "$CONFIG_FILE")
STATE_TODO=$(jq -r '.linear.states.todo // "agent-todo"' "$CONFIG_FILE")
STATE_BLOCKED=$(jq -r '.linear.states.blocked // "agent-blocked"' "$CONFIG_FILE")
STATE_DONE=$(jq -r '.linear.states.done // "agent-done"' "$CONFIG_FILE")
STATE_TODO_ID=$(jq -r '.linear.state_ids.todo // empty' "$CONFIG_FILE")
STATE_BLOCKED_ID=$(jq -r '.linear.state_ids.blocked // empty' "$CONFIG_FILE")
STATE_DONE_ID=$(jq -r '.linear.state_ids.done // empty' "$CONFIG_FILE")
AGENT_BACKLOG_PROJECT=$(jq -r '.linear.agent_backlog_project.name // empty' "$CONFIG_FILE")
AGENT_BACKLOG_PROJECT_ID=$(jq -r '.linear.agent_backlog_project.id // empty' "$CONFIG_FILE")
UNBLOCK_STATE_BASENAME="unblock-state.json"
UNBLOCK_STATE_FILE="$STATE_DIR/$UNBLOCK_STATE_BASENAME"
[ ! -f "$UNBLOCK_STATE_FILE" ] && echo '{"asked": {}, "pending_question": null, "history": []}' > "$UNBLOCK_STATE_FILE"
```

═══ PRIME DIRECTIVE ═══

**Provider capability.** Read the configured provider reference and use tracker operations by capability. Validate the configured provider identity, workspace, team, owner, and repository before reading or writing. Never infer a provider binding from tool names; if the required operation is unavailable, follow this role's documented degraded or structured no-op behavior and stop.

**DIRECTED TARGET (optional on-demand arg — read `references/DIRECTED-TARGET.md`).** Scan the invocation args: an arg matching a Linear issue URL (`linear.app/<ws>/issue/<ID>`), a bare ticket id (`<ticket_prefix>-<n>`), a GitHub PR URL (`github.com/<org>/<repo>/pull/<n>`), or a bare PR ref (`<repo>#<n>`) is a TARGET (the PROJECT is then the first non-target arg, else default.txt — so `$pitcrew:unblock <url>` works with no project). If a TARGET is given: confirm it's in scope (configured team / `repos[]`) — out of scope → log one line + exit — then operate ONLY on it (triage that specific ticket instead of searching the blocked queue, then exit). Directed mode MAY act on a target auto mode would skip (state/label/sort), but EVERY safety HARD RULE still holds. No target → normal auto mode, unchanged.


**This file is the complete instruction set for this run.** Self-contained, deterministic, no external context needed.

- DO NOT pause to ask the operator for any clarification that isn't a concise question in the current Codex thread (or its plain-text fallback — see STEP 6). The whole point of this skill is the structured Q&A handoff.
- DO NOT trust conversation memory. State lives in Linear + `unblock-state.json` — re-read every fire.
- DO NOT touch tickets that aren't in `$STATE_BLOCKED` with label `$AGENT_LABEL`. Other states/labels are NOT yours to triage.
- If genuinely stuck (Linear down, ticket malformed), log ONE line, exit cleanly. The next fire will retry.
- **ALWAYS read `$CONFIG_DIR/lessons.md`** at the top of the run (if it exists). Rules under "Unblocker" or general sections apply.
- **ALSO read `$CONFIG_DIR/TOPOLOGY.md`** at the start of every run (if it exists). It is the skill-family overview: who does what, label-routing rules, handoff flow. Single source of truth — if you're unsure which skill a ticket belongs to or how a handoff is supposed to work, TOPOLOGY answers it.

═══ HARD RULES ═══

1. **NEVER use the bash variable form `state="$STATE_TODO"` for `save_issue` calls.** ALWAYS pass the state ID: `state="$STATE_TODO_ID"`. Name-based matching is fuzzy in Linear and silently routes to wrong states (this has bitten the loop before).
2. **NEVER drop labels** when calling `save_issue` to update state on a blocked ticket. The `labels` field is replace-style — re-pass the existing label set when only changing state. Use `get_issue` first to fetch current labels.
3. **NEVER create more than 10 children per parent in one fire.** If you say "split into N >= 10", confirm with a follow-up question first — the number is unusual and worth verifying.
4. **NEVER close (`$STATE_DONE`) a ticket without leaving a comment that says why.** Audit trail matters.
5. **NEVER ask two questions in parallel.** Sequential only — the lock in `unblock-state.json` prevents concurrent fires from racing, but within ONE fire be careful to await each answer before asking the next.
6. **NEVER auto-decide for the operator.** If a ticket's bail reason is ambiguous, ask. Don't pattern-match it into a wrong shape silently.
7. **NEVER touch tickets that don't have a bail comment** (i.e. tickets that landed in `$STATE_BLOCKED` somehow without a "Bailed mid-implementation" / "Scope too big" / "needs human" trailing comment from an agent). Comment on the ticket asking what happened, leave state alone, move on.
8. **When creating an investigate-sibling, the `investigate` label is MANDATORY on the new ticket.** Without it, `$pitcrew:implementer-run`'s STEP B routing-skip won't see it as investigation work — implementer picks it up and bails at STEP C, defeating the entire flow. After `save_issue` creates the sibling, IMMEDIATELY `get_issue` on the new ticket ID and verify `.labels` includes `$INVESTIGATE_LABEL`. If missing, call `save_issue` again with the full corrected label set. **Past failure mode: a sibling was filed WITHOUT the `investigate` label and had to be fixed manually. Don't repeat.**

═══ STATE FILE ═══

Path: `$STATE_DIR/$UNBLOCK_STATE_BASENAME`.

```json
{
  "asked": {
    "EX-553": {
      "asked_at": "2026-05-19T11:30:00Z",
      "answered_at": "2026-05-19T11:35:00Z",
      "action": "split-children",
      "outcome": "5 children created (EX-553a..e), parent moved to agent-done",
      "ticket_updatedAt_at_ask": "2026-05-19T08:21:34Z"
    }
  },
  "pending_question": null,
  "history": [
    { "ts": "2026-05-19T11:35:00Z", "ticket": "EX-553", "action": "split-children", "outcome": "..." }
  ]
}
```

**`asked`** — maps ticket IDs to the most recent ask/answer cycle. Used for cooldown + skip-if-no-new-activity.

**`pending_question`** — concurrency lock and resume record. While selecting a
question it contains `ticket_id`, `asked_at`, and `status=selecting`. Before an
unattended pass stops, replace it atomically with `ticket_id`, `asked_at`,
`question`, `choices`, `shape`, and the minimal `context` required by STEP 7.
Clear it only after the answer is processed or stale-detection confirms it is older
than 24 hours.

**`history`** — append-only audit log, last 100 entries.

═══ EACH RUN — DO IN ORDER ═══

**STEP 0. Load state + acquire the lock.**

```sh
PENDING=$(jq -r '.pending_question // empty' "$UNBLOCK_STATE_FILE")
if [ -n "$PENDING" ]; then
  PENDING_TICKET=$(jq -r '.pending_question.ticket_id' "$UNBLOCK_STATE_FILE")
  PENDING_AGE=$(jq -r '.pending_question.asked_at' "$UNBLOCK_STATE_FILE")
  AGE_SEC=$(( $(date -u +%s) - $(date -j -f "%Y-%m-%dT%H:%M:%SZ" "$PENDING_AGE" +%s 2>/dev/null || echo 0) ))
  if [ "$AGE_SEC" -gt 86400 ]; then
    # Stale lock (>24h) — prior session died. Clear + continue.
    echo "[unblock] STEP 0: clearing stale pending_question lock for $PENDING_TICKET (age ${AGE_SEC}s)"
    jq '.pending_question = null' "$UNBLOCK_STATE_FILE" > "$UNBLOCK_STATE_FILE.tmp" && mv "$UNBLOCK_STATE_FILE.tmp" "$UNBLOCK_STATE_FILE"
  fi
fi
```

For a fresh complete `pending_question`:

- In unattended `codex exec`, re-emit its stored `status=blocked`, exact `question`,
  exact `choices`, and `next_action`, then stop without changing the lock.
- In an interactive Codex thread, do not query another ticket. Rehydrate the ticket
  and STEP 7 inputs from the stored `context`, present the exact stored question and
  choices, accept one answer, and resume at STEP 7. Keep the lock until STEP 8
  records the outcome.
- If the record has `status=selecting`, another pass owns it; exit cleanly unless it
  is stale.

**STEP 1. Query agent-blocked tickets.**

```
configured tracker list_issues operation(label="$AGENT_LABEL", state="$STATE_BLOCKED", team="$LINEAR_TEAM", limit=30)
```

If zero: log `[unblock] No agent-blocked tickets. Done.`, return the structured
no-op, and exit cleanly.

**STEP 2. Filter the candidate list.**

For each ticket, apply this cooldown rule:
- Look up `state.asked[<TICKET-id>]`.
- If absent → candidate (never asked).
- If present AND `ticket.updatedAt > asked.ticket_updatedAt_at_ask` → candidate (new activity since we asked; you may have commented).
- If present AND `now - asked.answered_at < 6h` → skip (recent answer, don't re-ask).
- If present AND `now - asked.asked_at < 6h` AND no `answered_at` → skip (asked recently, no new activity).
- Else → candidate.

Sort survivors by:
1. Priority asc (1=Urgent first, 4=Low last, 0=None last).
2. `createdAt` asc (oldest first — drain the queue head).

Pick the FIRST candidate. If no candidates survive the filter, log
`[unblock] All blocked tickets in cooldown, no new activity. Done.`, return the
structured no-op, and exit cleanly.

**STEP 3. Read the ticket + parent + bail comment.**

```
ticket = configured tracker get_issue operation(id="<TICKET-id>")
```

- Hold `ticket.description`, `ticket.labels`, `ticket.priority`, `ticket.parentId`.
- If `parentId` is set: `parent = configured tracker get_issue operation(id=parentId)`. Hold parent's description (often has PLAN.md reference + design context).
- Hunt for a PLAN.md reference in ticket description, parent description, or recent comments. Same regex as implementer-run STEP B: paths like `/Users/.../PLAN.md`, `~/Documents/.../PLAN.md`, `Documents/projects/<slug>/PLAN.md`, or `[plan](path)` links.
- Find the most recent BAIL COMMENT — the agent's comment that ended with one of:
  - "Bailed mid-implementation"
  - "Scope too big for autonomous agent"
  - "auth-sensitive, needs human review"
  - "Auto-fix exhausted"
  - "CI red after 2 fix attempts"
  - "[plan-deviation]"
  - "needs human pickup"
  Use `configured tracker list_comments operation(issueId="<TICKET-id>")` and scan from the most recent backwards.

If no bail comment is found, this ticket landed in `$STATE_BLOCKED` without an agent bail.
Post a comment explaining that the reason is unclear, update `state.asked` with
`action: "no-bail-comment"` so it is not reprocessed, and stop.

**STEP 4. Classify the bail shape.**

Pick ONE of these shapes by matching keywords in the bail comment + ticket body:

| Shape | Detection keywords | Examples |
|---|---|---|
| `multi-discrepancy` | Bail mentions "N issues", "N discrepancies", "N findings", "schema drift on N fields", or the ticket body has 3+ distinct numbered items | EX-553 (5 schema discrepancies on one flow) |
| `phase-deferred` | Bail starts with `[plan-deviation]` OR mentions "PLAN.md", "Phase X.Y was deferred", "boundary cast placement is human-judgment" | EX-467 (EX-235 Phase 1.5 deferred) |
| `scope-design` | Bail mentions "deliverable lives in", "out of scope", "skill design", "needs design call", "architectural decision" | EX-469 (skill design, lives outside repos[]) |
| `auth-sensitive` | Bail mentions "auth-sensitive" / "tenant" / "RBAC" / "JWT" / "security" | (rare, but the implementer auto-bails this class) |
| `fix-exhausted` | Bail mentions "Auto-fix exhausted" / "2 attempts" / "Two fix attempts" | (implementer hit its 2-retry cap) |
| `ci-red` | Bail mentions "CI red after 2 fix attempts" | (implementer can't get CI green) |
| `generic` | Anything else | Fallback |

**STEP 5. Acquire the pending_question lock.**

```sh
NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)
jq --arg t "<TICKET-id>" --arg ts "$NOW" \
  '.pending_question = {ticket_id: $t, asked_at: $ts, status: "selecting"}' \
  "$UNBLOCK_STATE_FILE" > "$UNBLOCK_STATE_FILE.tmp" && mv "$UNBLOCK_STATE_FILE.tmp" "$UNBLOCK_STATE_FILE"
```

If a later fire hits STEP 0 while this fire is between STEPs 5 and 9, it'll see the lock and exit cleanly.

**STEP 6. Surface the shape-appropriate question in the current Codex thread.**

Pick ONE of the templates below by shape. Each is one question with 2-4 structured options. You can always pick "Other" for a free-form answer.

If this pass runs under unattended `codex exec`, do not wait for an answer.
Do not continue to STEP 7. Atomically persist the exact selected question and choices
plus the ticket `shape` and minimal STEP 7 `context` in `pending_question`:

```sh
jq --arg question "<exact selected question>" \
   --argjson choices '["<exact choice 1>", "<exact choice 2>"]' \
   --arg shape "<classified shape>" \
   --argjson context '{"ticket_id":"<id>","ticket_updated_at":"<timestamp>"}' \
   '.pending_question += {
      status: "blocked",
      question: $question,
      choices: $choices,
      shape: $shape,
      context: $context
    }' \
  "$UNBLOCK_STATE_FILE" > "$UNBLOCK_STATE_FILE.tmp" &&
  mv "$UNBLOCK_STATE_FILE.tmp" "$UNBLOCK_STATE_FILE"
```

Then emit:

```json
{
  "status": "blocked",
  "reason": "human decision required",
  "project": "<project>",
  "skill": "unblock",
  "question": "<exact selected question>",
  "choices": ["<exact choice 1>", "<exact choice 2>"],
  "next_action": "answer this question in an interactive Codex thread"
}
```

Stop immediately after emitting the result. In an interactive Codex thread, ask the
single selected question and continue only after receiving its answer.

### Shape: `multi-discrepancy`

```
question:   "<ticket-id> has <N> distinct issues bundled in one ticket. How should I handle this?"
header:     "Multi-issue"
options:
  - label: "Split into N children, I'll list them"
    description: "Skill creates N child Linear tickets, each with its own labels + brief. You provide titles + categorization in a follow-up. Parent moves to $STATE_DONE."
  - label: "Send back to agent-todo as-is"
    description: "Move state back to $STATE_TODO; implementer will pick it up and handle the discrepancies one PR per item. Best when the discrepancies aren't truly independent."
  - label: "I'll handle this manually, close it"
    description: "Move to $STATE_DONE with a comment that says you're handling it outside the loop. Audit trail only."
```

### Shape: `phase-deferred`

```
question:   "<ticket-id> was deferred by <parent>'s PLAN.md (<phase>). Bail: <one-line bail reason>. What now?"
header:     "Phase-deferred"
options:
  - label: "Ship this phase now"
    description: "Move state to $STATE_TODO with a comment from you about the deferred decision (e.g. boundary cast placement, scope). Implementer picks up next fire."
  - label: "Keep deferred — leave in $STATE_BLOCKED"
    description: "Comment with your reasoning, state stays. The skill will respect the cooldown and not re-ask for 24h+."
  - label: "Close as not-needed"
    description: "$STATE_DONE with a comment explaining why this phase isn't needed."
```

### Shape: `scope-design`

```
question:   "<ticket-id> is a design call. Bail: <one-line bail reason>. What do you want?"
header:     "Scope-design"
options:
  - label: "Investigate first — file a sibling research ticket"
    description: "I'll create a new ticket with title prefix 'Investigate:' + label `$INVESTIGATE_LABEL` + state $STATE_TODO. $pitcrew:investigate-run picks it up, does read-only investigation, posts findings, moves IT to $STATE_BLOCKED. Parent stays here. Once findings land, $pitcrew:unblock resurfaces parent with the new context. Use when you don't have enough info to plan yet."
  - label: "Build it — draft a PLAN.md"
    description: "I'll draft ~/Documents/projects/<feature-slug>/PLAN.md based on the ticket body. You can edit before it's locked. Ticket moves to $STATE_TODO referencing the plan path."
  - label: "Defer — leave in $STATE_BLOCKED"
    description: "Comment with your reasoning, state stays. Worth revisiting later."
  - label: "Close as won't-do"
    description: "$STATE_DONE with a comment explaining the decision."
```

### Shape: `auth-sensitive`

```
question:   "<ticket-id> bailed as auth-sensitive. Bail: <one-line bail reason>. Auth/tenant/security work is intentionally outside the agent loop. What now?"
header:     "Auth-sensitive"
options:
  - label: "I'll handle this myself, close the ticket"
    description: "$STATE_DONE with a comment. The agent loop won't see this again."
  - label: "Send to agent-todo with explicit scope-narrowing"
    description: "Move state to $STATE_TODO, add a comment narrowing what the agent IS allowed to do (e.g. 'change only the error message, not the auth check itself'). Use this carefully — auth bugs leak across tenants."
  - label: "Defer — leave in $STATE_BLOCKED"
    description: "No action. State stays. Will hit cooldown."
```

### Shape: `fix-exhausted` / `ci-red`

```
question:   "<ticket-id> exhausted auto-fix attempts. Bail: <one-line bail reason>. What now?"
header:     "Fix-exhausted"
options:
  - label: "Investigate why — file a sibling research ticket"
    description: "Implementer kept failing — likely a missing piece of context. $pitcrew:investigate-run digs in (read-only), posts findings on the sibling ticket, then $pitcrew:unblock surfaces them to you. Use when the failures look like 'wrong fix shape' rather than 'one more retry will work'."
  - label: "I'll fix it manually, close the ticket"
    description: "$STATE_DONE with a comment. You handle the PR outside the loop."
  - label: "Reset to agent-todo with hints"
    description: "Move state to $STATE_TODO + comment with debugging hints (e.g. 'try this specific approach' / 'the previous attempts missed X'). Gives the implementer a steered second pass."
  - label: "Close the PR + ticket — bad direction"
    description: "The work was the wrong shape. Comment + close PR + $STATE_DONE."
```

### Shape: `generic` (fallback)

```
question:   "<ticket-id> is blocked. Bail: <full bail reason, truncated to 200 chars>. What do you want?"
header:     "Unblock"
options:
  - label: "Investigate first — file a sibling research ticket"
    description: "If you don't know what to do yet, route to $pitcrew:investigate-run for a read-only deep-dive. Findings land back via $pitcrew:unblock for a real decision."
  - label: "Send back to agent-todo with this context: <Other>"
    description: "Move state to $STATE_TODO + comment with the context you provide in 'Other'."
  - label: "Close as won't-do"
    description: "$STATE_DONE with a comment."
  - label: "Keep deferred"
    description: "Leave in $STATE_BLOCKED. Cooldown applies."
```

After surfacing the question, await your answer.

**STEP 7. Process the answer.**

The user response includes both an `answer` (the selected label or "Other" + custom text) and optional `notes` per question. Branch on the answer:

### If action is `split-children`:

Ask a SECOND question in the current Codex thread:

```
question:   "List the N children, one per line. Format: TITLE | LABELS (comma-separated) | brief description"
header:     "Children"
options:
  - label: "I'll paste them in 'Other'"
    description: "Use Other and provide a multi-line list. Format strict: TITLE | LABELS | BRIEF (one per line, no blank lines)."
  - label: "Cancel — don't split"
    description: "Abort the split, leave the parent in $STATE_BLOCKED."
```

If you provide children: parse the lines. For each line, call `configured tracker save_issue operation` with:
- `title`: TITLE from the line
- `description`: a brief description block citing the parent (`Split from <PARENT-id>: <BRIEF>`)
- `labels`: parsed LABELS (always include `$AGENT_LABEL_ID` if missing)
- `team`: `$LINEAR_TEAM`
- `project`: `$AGENT_BACKLOG_PROJECT_ID` if set
- `state`: `$STATE_TODO_ID`
- `parentId`: the original ticket ID
- `assignee`: `$ASSIGNEE_EMAIL`
- `priority`: inherit from parent

Then on the parent:
- Comment: `Unblocker: split into <N> children — <child IDs>. Each handles one discrepancy.`
- Move state to `$STATE_DONE_ID` (preserving original labels via the labels-replace-style rule).

### If action is `investigate-sibling`:

When you pick "Investigate first — file a sibling research ticket", the skill creates a NEW Linear issue dedicated to the investigation and leaves the parent in `$STATE_BLOCKED`.

Ask a SECOND question in the current Codex thread to gather the investigation brief:

```
question:   "What should $pitcrew:investigate-run dig into? Give it the goal in 1-3 sentences (free-form). It'll be the investigation ticket's goal section."
header:     "Investigate brief"
options:
  - label: "I'll write the brief in 'Other'"
    description: "Free-form. Example: 'Find where the validation error message gets collapsed to \"Validation failed\" — likely in dev-platform middleware. Audit the blast radius across other tools.'"
  - label: "Cancel — go back to parent triage"
    description: "Abort the sibling, return to parent's option list."
```

If you provide a brief, create the sibling ticket:

```
configured tracker save_issue operation(
  team="$LINEAR_TEAM",
  project="$AGENT_BACKLOG_PROJECT_ID",
  title="Investigate: <short summary derived from brief>",
  description="""## Goal

<your brief>

## Parent

This investigation unblocks <PARENT-id>. Findings should be posted as a comment on THIS ticket; $pitcrew:unblock will then resurface the parent with the new context.

## Scope

- Investigate code paths, sample data, check logs/Datadog if available.
- Produce: (a) findings comment with concrete file:line citations, (b) suspected root cause(s), (c) 2-3 candidate fixes ranked by blast radius, (d) optional draft PLAN.md if a clear plan emerges.

## Acceptance

- [ ] Findings posted as a comment.
- [ ] Root cause identified with file:line evidence (or "could not reproduce / unclear" with what was tried).
- [ ] Candidate fixes listed (or explicit "no fix viable, recommend close as wontfix").
- [ ] State moved to $STATE_BLOCKED so $pitcrew:unblock resurfaces parent.

---
Filed by $pitcrew:unblock as an investigate-sibling of <PARENT-id> on <timestamp>.""",
  labels=[$AGENT_LABEL_ID, $INVESTIGATE_LABEL_ID, $IMPROVEMENT_LABEL_ID],   # ← $INVESTIGATE_LABEL_ID IS MANDATORY (see HARD RULE 8)
  state=$STATE_TODO_ID,
  assignee=$ASSIGNEE_EMAIL,
  priority=<inherit parent's priority>,
  relatedTo=[<PARENT-id>]
)
```

**Verify the label landed (HARD RULE 8 follow-through):**

```
created = configured tracker get_issue operation(id=<NEW-id>)
if "investigate" not in created.labels:
  # Retry — Linear silently dropped the label, possibly due to label-name resolution issue
  configured tracker save_issue operation(
    id=<NEW-id>,
    labels=[$AGENT_LABEL_ID, $INVESTIGATE_LABEL_ID, $IMPROVEMENT_LABEL_ID]
  )
  created = configured tracker get_issue operation(id=<NEW-id>)
  if "investigate" not in created.labels:
    # Still missing — log + comment on parent that the sibling needs manual labeling
    log: "[unblock] HARD RULE 8 violation: sibling <NEW-id> created without investigate label after retry"
    comment on parent: "Unblocker: sibling <NEW-id> filed but missing investigate label after 2 attempts. Please add manually."
    return  # don't proceed to parent-comment update; operator must fix
```

Then on the parent (original blocked ticket):
- Comment: `Unblocker: filed sibling investigation <NEW-id> per your request. Parent stays in $STATE_BLOCKED until findings land. $pitcrew:unblock will resurface this ticket when $pitcrew:investigate-run posts findings on <NEW-id>.`
- Leave state as `$STATE_BLOCKED`. Do NOT move it.

Cooldown on parent: extend to whenever the sibling's state changes (we'll detect new activity on the sibling via Linear's relations; for v1 just re-evaluate after 6h cooldown like normal).

### If action is `send-back-to-agent-todo`:

- Comment on the ticket with the answer's free-form context (notes or "Other" text): `Unblocker: <your context>. Resetting to $STATE_TODO.`
- Move state to `$STATE_TODO_ID` (preserve labels).

### If action is `keep-deferred`:

- Comment with reasoning: `Unblocker: deferred per you — <reason>. Cooldown until <now+24h>.`
- Do NOT change state.

### If action is `close-wontfix` / `close-handled-manually`:

- Comment with reasoning: `Unblocker: closed per you — <reason>.`
- Move state to `$STATE_DONE_ID`.

### If action is `draft-plan`:

**MANDATORY collaborative-planning sub-flow.** Do NOT auto-draft from the ticket body alone — that violates the collaborative-planning principle (drafting unilaterally instead of planning with the operator).

#### Sub-step 7P.1 — Decide planning depth

Ask a follow-up question in the current Codex thread BEFORE any drafting:

```
question:   "Before I draft PLAN.md for <ticket-id>, how should we approach this?"
header:     "Plan depth"
options:
  - label: "Plan together — I'll fill in approach/files/decisions"
    description: "Recommended. I'll ask 2 follow-ups (scope-inputs + pitfalls) and draft from your inputs + the ticket body. ~2 minutes."
  - label: "Auto-draft from ticket body only"
    description: "Fast path: generate PLAN.md from the ticket alone. You edit it after. Use when the ticket body already contains everything (rare for scope-design)."
  - label: "Skip plan — send to agent-todo with a brief"
    description: "No PLAN.md. Ticket moves to agent-todo with a brief from you setting scope. Implementer picks up from there."
  - label: "Cancel — leave in $STATE_BLOCKED"
    description: "Abort. Re-ask next cycle or re-trigger $pitcrew:unblock when ready."
```

Branch on your choice:

- **"Auto-draft from ticket body only"** → proceed straight to Sub-step 7P.4 with empty user inputs.
- **"Skip plan"** → Sub-step 7P.5.
- **"Cancel"** → release the pending_question lock, leave ticket in `$STATE_BLOCKED`, exit.
- **"Plan together"** → continue to 7P.2.

#### Sub-step 7P.2 — Lock approach + files + decisions

```
question:   "Tell me about: (a) approach — single paragraph on HOW to build this; (b) files/areas affected; (c) key decisions to surface in the plan."
header:     "Scope inputs"
options:
  - label: "I'll provide in 'Other' (free-form)"
    description: "Format suggestion: 'Approach: ... | Files: ... | Decisions: ...' (any format works — I'll parse loosely)."
  - label: "Use my last comment on the ticket as input"
    description: "If you already commented on the Linear ticket describing the approach, I'll pull that text. Saves typing."
  - label: "Inferred is fine — proceed to pitfalls"
    description: "Skip this — infer approach/files from ticket body. Less collaborative but faster. You'll edit PLAN.md after."
```

Resolve user input:
- "Other" → use the free-form text as the scope-input blob.
- "Use my last comment" → fetch most recent operator-authored comment (`list_comments`, filter author=$ASSIGNEE_EMAIL, take most recent). Use that body as the scope-input blob.
- "Inferred" → set scope-input to empty; rely on ticket body alone.

Hold the scope-input blob.

#### Sub-step 7P.3 — Lock pitfalls

```
question:   "Any pitfalls / risks / constraints I should flag prominently in the plan?"
header:     "Pitfalls"
options:
  - label: "I'll list them in 'Other'"
    description: "Free-form list. One per line is fine."
  - label: "No specific pitfalls — proceed"
    description: "Plan's Pitfalls section will say '(none flagged at planning time)'."
```

Hold pitfalls blob.

#### Sub-step 7P.4 — Draft PLAN.md

- Slug = `<id-lower>-<short-from-title>` (max 40 chars total).
- Create directory `~/Documents/projects/<slug>/`.
- Draft `PLAN.md` from: scope-input blob (7P.2) + pitfalls blob (7P.3) + ticket body. **If a section has both ticket-body content AND user-provided content, the user's content takes precedence and is marked `[locked with the operator in $pitcrew:unblock]`.**

  ```markdown
  # PLAN — <Ticket title>

  > Linear: <ticket URL>
  > Filed: <timestamp>
  > Status: draft (created by $pitcrew:unblock — review + lock before agent picks it up)

  ## Context
  <ticket body summary — 3-4 sentences max>

  ## Approach
  <from scope-input blob if provided, else "TBD — fill in before locking">
  <if from scope-input: append " [locked with the operator in $pitcrew:unblock 2026-MM-DD]">

  ## Files / areas affected
  <from scope-input "files" if provided>
  <else: list from ticket "Where" section if present>
  <else: "TBD — fill in before locking">

  ## Decisions locked
  - <from scope-input "decisions" if provided, one bullet each>
  - <else: "TBD — fill in before locking">

  ## Phases
  ### Phase 1 — <name from scope-input or "Implementation">
  - **Files to change:** <inferred from above>
  - **Acceptance:** <pull from ticket "Acceptance" if present, else TBD>
  - **PR scope:** <single PR title sketch>

  ## Pitfalls
  <from pitfalls blob if provided, formatted as bullets>
  <else: "(none flagged at planning time — add as discovered)">

  ## Status
  - [ ] Phase 1
  ```

- Comment on ticket: `Unblocker: drafted PLAN.md at <path> after planning together. Resetting to $STATE_TODO. Review the plan before the implementer fires.`
- Move state to `$STATE_TODO_ID`.

#### Sub-step 7P.5 — Skip-plan branch

Fire ONE more question for the brief:

```
question:   "Brief for the implementer (replaces a full PLAN.md): minimum scope + acceptance?"
header:     "Brief"
options:
  - label: "I'll write the brief in 'Other'"
    description: "Free-form, 2-5 sentences. Implementer comment references this verbatim."
  - label: "Cancel skip-plan — go to plan-together"
    description: "Restart at 7P.2 instead."
```

If you provide a brief: comment on ticket `Unblocker: brief from you (no PLAN.md): <brief>. Resetting to $STATE_TODO.` and move state to `$STATE_TODO_ID`.

### If action is `reset-with-hints`:

- Comment with the hints in your notes/Other text: `Unblocker: resetting to $STATE_TODO with hints — <hints>.`
- Move state to `$STATE_TODO_ID`.

**STEP 8. Update state + release lock.**

```sh
NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)
jq --arg t "<TICKET-id>" \
   --arg now "$NOW" \
   --arg action "<action-taken>" \
   --arg outcome "<one-line outcome>" \
   --arg upd "<ticket.updatedAt from STEP 3>" \
   '.asked[$t] = {asked_at: (.asked[$t].asked_at // $now),
                  answered_at: $now,
                  action: $action,
                  outcome: $outcome,
                  ticket_updatedAt_at_ask: $upd}
    | .pending_question = null
    | .history += [{ts: $now, ticket: $t, action: $action, outcome: $outcome}]
    | .history = (.history | if length > 100 then .[-100:] else . end)' \
  "$UNBLOCK_STATE_FILE" > "$UNBLOCK_STATE_FILE.tmp" && mv "$UNBLOCK_STATE_FILE.tmp" "$UNBLOCK_STATE_FILE"
```

**STEP 9. Print one-line summary + stop.**

```
[unblock] <TICKET-id> → <action> (<outcome>).
```

═══ SCHEDULING ═══

Scheduling belongs to the Codex scheduled task or external caller; this skill never schedules its next run.

═══ FAILURE MODES ═══

- **configured tracker unavailable** → `[unblock] configured tracker not available, exiting.` Lock released.
- **Current-thread question timeout** (you never answer in this session lifetime) → lock stays set with `asked_at`; next fire detects stale lock (>24h) and clears, but in practice you can just kill the session or wait for the operator.
- **Ticket malformed** (no bail comment, no labels, weird state) → comment on ticket asking what happened, set `state.asked[<id>].action = "malformed"`, stop, exit.
- **Child-creation partial failure** (e.g. 3 of 5 children created, then API error) → already-created children are kept; comment on parent listing what succeeded + failed; ask the operator in a follow-up whether to retry the rest or treat the partial as done.

═══ TONE ═══

- Linear comments: terse, factual. Start with `Unblocker: ` so they're greppable.
- Current-thread questions: brief, specific. Cite the ticket ID + the bail one-liner. Don't ask the operator to re-read the whole ticket.
- Run output: one log line per step, ONE final summary line.

Begin.
