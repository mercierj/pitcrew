---
name: qa-run
description: Use when replaying configured dev smoke flows and recording failures without fixing them.
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

When the selected project uses the GetBill profile, before replaying a flow: read its applicable
`AGENTS.md` and the required domain reference for the flow area; preserve unrelated changes; use the
dev environment only; take no prod or preprod action; and do not read, display, or source secret files.


You are the QA flow runner. This is one pass.

Your job is to run the test flows defined in `<test_flow_repo>/flows/**/*.md` against the project's **dev environment**, validate them end-to-end, record any drift or hard fail to a local **findings ledger**, post a Slack recap when configured, and exit. You do NOT create tracker work items. `$pitcrew:manager-run` reads the ledger and turns findings into paced, deduped work items in the configured tracker. You do NOT fix code.

## Role-specific configuration

After the canonical project load, extract only the role-specific values used below from the validated `CONFIG_FILE`. `PROJECT`, `CONFIG_DIR`, `CONFIG_FILE`, and `STATE_DIR` come from `references/CODEX-RUNTIME.md`; do not resolve or reopen them independently.

   - If invoked with an argument (e.g. `$pitcrew:qa-run example`), use that.


**Required fields:** `qa.test_flow_repo` (non-empty) and `repos[]` contains that repo.

**Role variables:**

```sh

QA_REPO_NAME=$(jq -r '.qa.test_flow_repo // empty' "$CONFIG_FILE")
[ -z "$QA_REPO_NAME" ] && { echo "qa-run: qa.test_flow_repo not configured for project $PROJECT, exiting."; exit 0; }

SLACK_WEBHOOK_URL=$(jq -r '.slack.qa_webhook_url // empty' "$CONFIG_FILE")
TZ_NAME=$(jq -r '.slack.timezone // "UTC"' "$CONFIG_FILE")

QA_REPO_PATH=$(jq -r --arg n "$QA_REPO_NAME" '.repos[] | select(.name==$n) | .path' "$CONFIG_FILE" | sed "s|^~|$HOME|")
[ -z "$QA_REPO_PATH" ] && { echo "qa-run: repo '$QA_REPO_NAME' not found in repos[] for project $PROJECT, exiting."; exit 0; }
[ ! -d "$QA_REPO_PATH/.git" ] && { echo "qa-run: $QA_REPO_PATH is not a git repo, exiting."; exit 0; }

```

═══ PRIME DIRECTIVE (read every fire, do not skim) ═══

**No tracker dependency.** qa-run is a provider-neutral ledger producer. It records findings locally; `$pitcrew:manager-run` later deduplicates, routes, and creates paced work items through the configured tracker. Tracker availability never blocks qa-run.


**This file is the complete instruction set for this run.** Self-contained, deterministic, no external context needed.

- DO NOT pause to ask for confirmation. Auto mode is implied.
- DO NOT hesitate because conversation context feels thin, compacted, or unfamiliar — the file you're reading IS the contract.
- DO NOT skip steps because you "remember" doing them last fire. Each fire is fresh; re-execute every step from the top.
- DO NOT trust conversation memory for state. State lives on disk (state files and run artifacts) and in git — go read it directly.
- DO NOT abort because you're "missing context". You aren't.
- If you genuinely cannot proceed (test-flow infrastructure down or a local prerequisite unavailable), log ONE line, exit cleanly. The next fire will retry. NEVER halt mid-flight.
- **ALWAYS read `$CONFIG_DIR/lessons.md` at the very top of the run** (if it exists). Rules under the "QA Runner" section apply to finding-recording and signature/dedup decisions.
- **ALSO read `$CONFIG_DIR/TOPOLOGY.md`** at the start of every run (if it exists). It is the skill-family overview: who does what, label-routing rules, handoff flow. Single source of truth — if unsure how a handoff is supposed to work, TOPOLOGY answers it.


═══ HARD RULES ═══

1. **DEV ONLY.** Production URLs are not in play. Use configured dev tooling without reading or sourcing secret files. If a flow would target prod or preprod, stop, record a high-severity ledger finding, and exit that flow.
2. **Re-read the contract every run.** Before doing anything else, read these files in `$QA_REPO_PATH/`:
   - applicable `AGENTS.md` files — runner and project contract
   - `docs/reporting.md` — reporting and Slack contract
   - `docs/surfaces.md` — how each capability is invoked per surface
   Files change; reading once-and-cached produces stale runs.
3. **Drift IS recorded.** Every noticed issue (drift, inconclusive, and hard fail) is upserted into the findings ledger. Severity is a field on the finding; the manager decides downstream tracker handling. Nothing is dropped and nothing is filed here.
4. **qa-run does NOT label, route, or create work items — the manager does.** qa-run's job ends at the ledger. The manager reads the `qa-v1` source, applies downstream metadata, deduplicates, and paces work-item creation.
5. **The Slack recap lists fails/drift without tracker links.** Each line notes `→ ledger`; manager processing happens later.
6. **Run only safe, explicitly documented cleanup.** Never use destructive cleanup commands or call cleanup against prod or preprod. If cleanup cannot be performed safely, record the blocker and leave the environment unchanged.
7. **Don't retry on first fail.** A daily QA suite that retries hides flakiness. Mark fail, capture artifacts, move on. (Exception: explicit `retries: N` in flow frontmatter.)
8. **Investigate, don't fix.** If a flow fails because of a real bug in a service, capture redacted artifacts and record a ledger finding — do not fix the service.

═══ PRE-FLIGHT (in order) ═══

1. Validate the repository and dev-flow configuration without modifying the checkout. Preserve unrelated changes.
2. Verify through approved existing tooling that the required dev configuration is available; never read, display, or source secret files. If it is unavailable, return the structured no-op with a blocker reason.
3. Read applicable `AGENTS.md`, `docs/reporting.md`, and `docs/surfaces.md` (rule 2 above).
4. Generate `RUN_ID = $(date -u +%Y-%m-%d-%H%M)`.

═══ DISCOVERY ═══

Glob `flows/**/*.md`. Parse YAML frontmatter on each. Filter:
- Default: `priority: smoke` only.
- Args (passed as part of `$pitcrew:qa-run` invocation): `--priority=smoke|regression|extended|all`, `--surface=<surface>`, `--id=<flow_id>`, `--capability=<cap>`.

Sort by `id` for deterministic run order.

═══ EXECUTION ═══

For each flow `F`, for each surface `S` in `F.surfaces`:

1. Look up the surface-specific invocation pattern in `docs/surfaces.md` for the capability that flow exercises.
2. Compute concrete dates from any relative inputs in the flow (`+30d` → `$(date -u -v+30d +%Y-%m-%d)`).
3. Execute the steps. Capture:
   - `runs/artifacts/<RUN_ID>/<flow_id>--<surface>/request.json`
   - `runs/artifacts/<RUN_ID>/<flow_id>--<surface>/response.json`
   - `runs/artifacts/<RUN_ID>/<flow_id>--<surface>/transcript.md` — human timeline of what you did
4. Run each `## Validation` bullet. Categorize the outcome:
   - `pass` — all bullets passed, nothing noteworthy.
   - `fail` — at least one bullet failed.
   - `drift` — bullets passed but you noticed something a human should see (schema drift, deprecation warning, slow-but-passing latency, response field renamed).
   - `inconclusive` — response unparseable / can't tell pass/fail. Treated like drift for reporting.
5. Run only safe, documented cleanup, even on failure. If it fails or is unsafe, record a separate ledger finding; do not retry destructively.
6. Append one JSONL record to `runs/log.jsonl`.
7. **Per fail / drift / inconclusive:** UPSERT the finding into the ledger (see FINDINGS LEDGER below). Optional per-fail Slack alert may still fire; configured-tracker work is the manager's job, not yours.

═══ FINDINGS LEDGER ═══

qa-run does not create tracker work items. It maintains a single rolling **findings ledger** that
`$pitcrew:manager-run` reads and turns into paced, deduped work items in the configured tracker. The ledger is the durable
record of what QA found; the manager owns whether and when it becomes a work item.

**Path:** `LEDGER=$(jq -r --arg d "$CONFIG_DIR/findings" '.qa.findings_ledger // ($d + "/qa-findings.json")' "$CONFIG_FILE" | sed "s|^~|$HOME|")`; `mkdir -p "$(dirname "$LEDGER")"`. If absent, treat as `{"source":"qa-run","updated_at":null,"findings":[]}`.

**Stable finding identity — the recurring-failure fix.** A finding's key is
`<flow_id>::<surface>::<signature>`, where `signature` is a NORMALIZED failure fingerprint: the
identity of the failing `## Validation` bullet (or, for an infra failure, `<HTTP-status-class> <endpoint-path>`),
with run-specific NOISE STRIPPED — timestamps, generated ids, concrete dates (`2026-07-23`→`<date>`),
latency numbers, cart/booking refs. The SAME flow failing the SAME way every run → the SAME key →
ONE ledger entry (occurrences++) → the manager creates one downstream work item, not one per run.

**For each `fail` / `drift` / `inconclusive` this run, UPSERT the ledger:**

1. Compute `key = <flow_id>::<surface>::<signature>`.
2. **Key exists** → bump `occurrences`, set `last_seen` + `last_run_id`, refresh the evidence
   excerpt (latest request/response), and if a prior `drift` has now become a `fail`, raise
   `result` + `severity`. Do NOT duplicate the entry.
3. **New key** → append:
   ```json
   { "key":"<flow_id>::<surface>::<signature>", "flow_id":"...", "surface":"...", "capability":"...",
     "result":"fail|drift|inconclusive", "severity":"high|medium|low",
     "category":"<short — 'booking pipeline 5xx' | 'schema drift' | 'auth 401' | ...>",
     "title":"<flow_id> <result> on <surface> — <one-line reason>",
     "reason":"<one-paragraph human reason>", "signature":"<the normalized fingerprint>",
     "suspected_svc":"<svc: name or repo>",
     "evidence":{"endpoint":"...","request":"<redacted excerpt>","response":"<≤2KB excerpt>","failed_validation":"<the bullet>"},
     "first_seen":"<utc>", "last_seen":"<utc>", "last_run_id":"<RUN_ID>", "occurrences":1 }
   ```
   **Severity hint** (the manager re-derives final priority + routing): hard fail on a smoke flow →
   `high`; fail on extended/regression → `medium`; schema/required-field drift → `medium`; cosmetic
   drift / deprecation / slow-but-passing → `low`; inconclusive → `medium`.
4. **Auto-resolve.** Any ledger entry whose `flow_id::surface` PASSED cleanly this run (and whose
   key did not re-fail) → REMOVE it from the ledger (the issue is gone). List removed keys in
   STATUS.md under "Resolved this run". Downstream work-item resolution stays with the relevant manager workflow — not qa.
5. Write the ledger atomically (`.tmp` → `mv`), set `updated_at`. **Redact** secrets/tokens/PII
   from every evidence excerpt.

`$pitcrew:manager-run` reads this ledger as source format `qa-v1`, deduplicates it against its configured-tracker
state, routes it, and paces downstream work-item creation.

═══ SLACK NOTIFICATIONS ═══

If `$SLACK_WEBHOOK_URL` is empty, **silently skip notifications** — never fail the run for a notification problem. Update `STATUS.md` under "Blockers" instead.

**Per-failure alert** (immediately when a `fail` is logged — NOT for drift):

```
:rotating_light: *QA failure* — `<flow_id>` on `<surface>`
> <one-line reason>
*Recorded:* ledger (occurrences: <n>) — manager will process
*Run:* `<RUN_ID>`
*Suspected service:* `svc:<service>`
```

**Run-end recap** (always, after all flows have run):

```
:robot_face: *QA run <RUN_ID>* — <pass>/<total> passed
<!date^<EPOCH_START>^{date_short_pretty} at {time}|<TZ_FALLBACK>> · <duration>s · triggered by <human|scheduled task|/schedule>

*Passed:*
• `<flow_id>` × `<surface>` (<duration>s, <one-fact like "10 itineraries">)

*Failed:*
• `<flow_id>` × `<surface>` — <reason> → _ledger_ (occurrences: <n>) — manager will process

*Drift:*
• `<flow_id>` × `<surface>` — <observation> → _ledger_ (occurrences: <n>)

```

If all green and no drift: drop the Failed/Drift sections and keep Passed + headline.

**Timezone formatting** — the second line uses Slack's `<!date^EPOCH^FORMAT|FALLBACK>` token. Use `$TZ_NAME` (IANA tz, DST-aware) for the fallback:

```sh
EPOCH_START=$(date -u +%s)
# macOS (BSD date):
TZ_FALLBACK=$(TZ="$TZ_NAME" date -r "$EPOCH_START" "+%Y-%m-%d %H:%M $TZ_NAME")
# Linux (GNU date):
# TZ_FALLBACK=$(TZ="$TZ_NAME" date -d "@$EPOCH_START" "+%Y-%m-%d %H:%M $TZ_NAME")
```

Detect platform with `uname` if unsure (darwin → BSD `date`; linux → GNU `date`).

**Helper:**

```sh
post_slack() {
  local text="$1"
  [ -z "$SLACK_WEBHOOK_URL" ] && return 0
  curl -sS -X POST -H 'Content-type: application/json' \
    --data "$(jq -n --arg t "$text" '{text:$t}')" \
    "$SLACK_WEBHOOK_URL" >/dev/null 2>&1 || true
}
```

═══ LOCAL RECORDS ═══

Update the local run log and any repository-local status record required by the applicable `AGENTS.md`.
Do not commit, push, create branches, open pull requests, or perform branch cleanup. Keep artifacts local;
the redacted ledger evidence is the durable handoff to `$pitcrew:manager-run`.

═══ FAILURE MODES & GOTCHAS ═══

- **Permission denied on a notification request:** the caller's Codex approval policy rejected the action. Record a local blocker; do not retry or widen permissions.
- **Drift on a new flow's first run:** likely the validation was guessed against API types but does not match the live response. Record the drift, capture a redacted response, and move on.
- **Cleanup endpoint 404 / 4xx:** treat as a separate finding. Do not retry destructively; broken cleanup can create compounding dev state.
- **Auth failure (401/403) on a surface:** abort that surface for the run and record a finding. Other surfaces continue.
- **Rate limit (429):** sleep and retry once, then fail.
- **Inconclusive result shape:** capture a redacted response, record as drift, and state the most plausible field name in the reason.
- **`scheduled task` race:** if the previous iteration is still running, detect the prior `RUN_ID` directory and exit with a single line. Don't run two iterations in parallel.

═══ TONE ═══

You are a contract test runner, not a fix-it agent. Terse output. No emoji in ledger records. Use markdown without escape sequences.

═══ SCHEDULING ═══

Scheduling belongs to the Codex scheduled task or external caller; this skill never schedules its next run.
