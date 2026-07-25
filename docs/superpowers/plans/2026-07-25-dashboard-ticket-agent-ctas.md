# Dashboard Ticket Agent CTAs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add targeted agent-launch CTAs to GitLab ticket cards for `todo`, `blocked`, and `done` tickets.

**Architecture:** The Python dashboard service owns the lifecycle-to-agent mapping, validates the configured GitLab target, and launches one bounded runner with a directed ticket reference. The browser renders the server-provided action and posts only the selected skill plus ticket target; it never owns the mapping. Existing global-stop, enabled-agent, per-agent lock, and localhost session protections remain authoritative.

**Tech Stack:** Python 3 standard library dashboard service and HTTP server, Bash runner wrapper, vanilla JavaScript DOM rendering, unittest test suite.

---

## File map

- Modify `scripts/pitcrew_dashboard.py`: define the lifecycle action contract, validate configured GitLab issue targets, and launch a directed runner.
- Modify `bin/pitcrew-codex.sh`: accept an optional directed target and include it in the Codex prompt.
- Modify `bin/pitcrew-dashboard`: validate and route the new `launch-ticket-agent` HTTP action.
- Modify `dashboard/app.js`: render ticket CTAs, track pending ticket launches, and post the selected ticket target.
- Modify `dashboard/styles.css`: style the ticket action row and disabled/unavailable explanation.
- Modify `tests/test_dashboard.py`: cover backend mapping, directed launch, HTTP contract, and browser source behavior.
- Modify `tests/test_cli.py`: cover the runner’s optional directed target argument.

## Task 1: Add lifecycle action metadata and backend validation

**Files:**
- Modify: `scripts/pitcrew_dashboard.py`
- Test: `tests/test_dashboard.py`

- [ ] **Step 1: Write failing unit tests for the action mapping.**

Add tests beside the existing `gitlab_work` tests asserting that `todo`,
`blocked`, and `done` issues receive respectively `implementer-run`, `unblock`,
and `stale-sweep`, while `processing` and `review` receive no ticket CTA.
Assert that the action includes a stable label, the issue `web_url`, and an
availability field derived from the schedule.

- [ ] **Step 2: Run the focused tests and verify they fail.**

Run:

```bash
python3 -m unittest tests.test_dashboard.DashboardTests.test_gitlab_work_ticket_agent_actions
```

Expected: the test fails because the GitLab payload has no `agent_action`.

- [ ] **Step 3: Implement the server-side mapping.**

Add a module-level immutable mapping in `scripts/pitcrew_dashboard.py`:

```python
TICKET_AGENT_ACTIONS = {
    "todo": ("implementer-run", "Lancer l’implémentation"),
    "blocked": ("unblock", "Débloquer ce ticket"),
    "done": ("stale-sweep", "Vérifier la clôture"),
}
```

In `gitlab_work`, add `agent_action` only when the lifecycle is mapped. Set
`skill`, `label`, `target` from the normalized issue URL, and `available` from
the matching schedule entry’s `enabled` flag and absence of `running`.

- [ ] **Step 4: Run the focused tests and verify they pass.**

Run the same unittest command. Expected: PASS.

## Task 2: Add directed target support to the runner

**Files:**
- Modify: `bin/pitcrew-codex.sh`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing CLI tests for the optional target.**

Extend the existing dry-run tests to invoke:

```bash
bin/pitcrew-codex.sh implementer-run getbill --target https://gitlab.com/getbill1/getbill/-/issues/1 --dry-run
```

Assert the command succeeds and the generated prompt contains the exact target
reference. Assert an unknown option still exits with status 2.

- [ ] **Step 2: Run the focused CLI tests and verify they fail.**

Run:

```bash
python3 -m unittest tests.test_cli
```

Expected: the new target assertion fails because `--target` is rejected.

- [ ] **Step 3: Implement target parsing and prompt forwarding.**

Add `TARGET` to the wrapper’s argument parser, accept only `--target <value>`,
and append a sentence to the Codex prompt telling the selected skill to operate
on that directed target under `references/DIRECTED-TARGET.md`. Do not bypass
preflight, project resolution, or the scheduled lock.

- [ ] **Step 4: Run the focused CLI tests and verify they pass.**

Run:

```bash
python3 -m unittest tests.test_cli
```

Expected: PASS.

## Task 3: Add the authenticated HTTP launch action

**Files:**
- Modify: `scripts/pitcrew_dashboard.py`
- Modify: `bin/pitcrew-dashboard`
- Test: `tests/test_dashboard.py`

- [ ] **Step 1: Write failing service tests for directed launch.**

Add tests asserting that a valid configured GitLab issue URL launches the
expected skill with `--target <url>`, rejects a foreign host/project, rejects a
disabled skill, rejects an unmapped skill/state combination, and rejects an
already-running target skill. Keep the command runner mocked and assert no
runner process is created on rejection.

- [ ] **Step 2: Run the focused tests and verify they fail.**

Run:

```bash
python3 -m unittest tests.test_dashboard.DashboardTests.test_launch_ticket_agent
```

Expected: failure because the service has no directed launch method.

- [ ] **Step 3: Implement service validation and launch.**

Add `DashboardService.launch_ticket_agent(skill, target)` that:

1. acquires `_control_lock`;
2. accepts only skills from `TICKET_AGENT_ACTIONS.values()`;
3. calls `_enabled_entry(skill)` and rejects an entry marked running;
4. parses `target` with `urlsplit`, requiring HTTPS, the configured GitLab
   host, the configured project path, and a `/issues/<positive-iid>` suffix;
5. starts `RUNNER` with `[skill, project, "--target", target, "--scheduled"]`;
6. returns `{"accepted": True, "pid": pid, "skill": skill, "target": target}`.

Reuse `_trigger`’s detached process behavior and preserve redacted errors.

- [ ] **Step 4: Write failing HTTP contract tests.**

Add a request test for JSON exactly containing
`action=launch-ticket-agent`, `skill`, and `target`, returning HTTP 202. Add
tests that extra fields, missing fields, malformed URLs, and invalid session
tokens are rejected without launching.

- [ ] **Step 5: Implement the HTTP dispatch.**

In `bin/pitcrew-dashboard`, validate the exact request key set and string
fields, then call `service.launch_ticket_agent`. Return 400 for malformed
requests, 403 for `DashboardError`, and 202 with the service result on success,
matching the existing action contracts.

- [ ] **Step 6: Run the focused backend and HTTP tests.**

Run:

```bash
python3 -m unittest tests.test_dashboard
```

Expected: PASS.

## Task 4: Render and operate the CTAs in the dashboard

**Files:**
- Modify: `dashboard/app.js`
- Modify: `dashboard/styles.css`
- Test: `tests/test_dashboard.py`

- [ ] **Step 1: Write failing browser-source assertions.**

Add source-level assertions that `renderGitLab` reads `issue.agent_action`,
renders its server-provided label, posts `launch-ticket-agent` with `skill` and
`target`, disables unavailable actions, and reports accepted/error states.

- [ ] **Step 2: Implement pending ticket launch state.**

Add a `pendingTicketActions` set keyed by the issue target. Render an action row
on mapped cards with a primary button. Disable it when `available` is false or
the target is pending. Use a short explanation such as `Agent indisponible`
when the server says the agent is disabled or running.

- [ ] **Step 3: Implement the action request.**

Add `launchTicketAgent(issue)` that posts:

```json
{
  "action": "launch-ticket-agent",
  "skill": "<server-provided skill>",
  "target": "<server-provided target>"
}
```

Use the existing session header, announce progress through
`operationalStatus`, refresh GitLab data on success, and clear the pending key
in `finally`.

- [ ] **Step 4: Add focused CSS and run source tests.**

Style `.ticket-agent-actions` and `.ticket-agent-unavailable` using existing
button and panel tokens; keep mobile layout stacked. Run:

```bash
python3 -m unittest tests.test_dashboard
```

Expected: PASS.

## Task 5: Full verification and handoff

**Files:**
- Modify: none beyond the files above.

- [ ] **Step 1: Run syntax and deterministic project checks.**

Run:

```bash
python3 -m py_compile scripts/pitcrew_dashboard.py bin/pitcrew-dashboard
bash tests/run.sh
```

Expected: both commands exit 0.

- [ ] **Step 2: Inspect the final diff.**

Run:

```bash
git diff --check
git status --short
git diff -- dashboard/app.js dashboard/styles.css scripts/pitcrew_dashboard.py bin/pitcrew-dashboard bin/pitcrew-codex.sh tests/test_dashboard.py tests/test_cli.py
```

Confirm only the requested implementation and tests are changed; preserve the
pre-existing modifications in `dashboard/index.html`,
`references/CODEX-RUNTIME.md`, `skills/implementer-run/SKILL.md`, and
`tests/test_skill_contracts.py`.

- [ ] **Step 3: Commit only owned implementation files if repository permissions allow.**

```bash
git add dashboard/app.js dashboard/styles.css scripts/pitcrew_dashboard.py bin/pitcrew-dashboard bin/pitcrew-codex.sh tests/test_dashboard.py tests/test_cli.py
git commit -m "feat: add ticket agent launch CTAs"
```

If `.git/index.lock` remains unwritable, leave the working-tree changes intact
and report that commit creation is blocked by checkout permissions.
