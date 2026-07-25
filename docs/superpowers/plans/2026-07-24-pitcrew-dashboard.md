# Pitcrew Local Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a manually started, localhost-only dashboard for monitoring and controlling GetBill Pitcrew agents, with seven-day run history and GitLab work visibility.

**Architecture:** Add a locked JSONL history store to the bounded runner, extend the existing launchd scheduler with single-role control primitives, and serve a dependency-free HTML interface from a small Python HTTP server. Keep history, scheduler, GitLab, HTTP, and browser rendering behind separate interfaces so they can be tested without launchd, GitLab, or a browser process.

**Tech Stack:** Python 3 standard library, Bash, POSIX `fcntl.flock`, launchd, `glab`, HTML/CSS/vanilla JavaScript, Python `unittest`.

---

## File Structure

- Create `scripts/pitcrew_history.py`: append, parse, retain, and classify bounded run records.
- Modify `scripts/pitcrew_locked_exec.py`: measure every scheduled pass and write one history record.
- Modify `bin/pitcrew-codex.sh`: pass summary and history paths to the locked executor.
- Modify `bin/pitcrew-schedule.py`: expose structured running state and safe single-role install/stop operations.
- Create `scripts/pitcrew_dashboard.py`: aggregate local state, GitLab work, cache results, and execute allowlisted controls.
- Create `bin/pitcrew-dashboard`: localhost HTTP server and static-asset entry point.
- Create `dashboard/index.html`: semantic dashboard shell.
- Create `dashboard/app.js`: polling, filtering, rendering, and control interactions.
- Create `dashboard/styles.css`: responsive local dashboard presentation.
- Create `tests/test_history.py`: history and health-classification tests.
- Modify `tests/test_cli.py`: scheduled-run history integration tests.
- Modify `tests/test_schedule.py`: running state and single-role control tests.
- Create `tests/test_dashboard.py`: GitLab, aggregation, controls, and HTTP security tests.
- Modify `tests/test_docs.py`: documented dashboard command contract.
- Modify `README.md`: operator instructions.
- Modify `references/SCHEDULED-TASKS.md`: history and dashboard runtime contract.

### Task 1: Seven-day run history store

**Files:**
- Create: `scripts/pitcrew_history.py`
- Create: `tests/test_history.py`

- [ ] **Step 1: Write failing retention, corruption, concurrency, and classification tests**

Create `tests/test_history.py` with temporary-file tests that call these exact
interfaces:

```python
import tempfile
import unittest
from pathlib import Path

from scripts.pitcrew_history import HistoryStore, classify_record


class HistoryStoreTest(unittest.TestCase):
    def test_history_retains_valid_records_from_last_seven_days(self):
        with tempfile.TemporaryDirectory() as temp:
            store = HistoryStore(Path(temp) / "history.jsonl", retention_days=7)
            store.append({
                "project": "getbill",
                "skill": "research-run",
                "started_at": "2026-07-24T08:00:00Z",
                "finished_at": "2026-07-24T08:00:03Z",
                "duration_ms": 3000,
                "outcome": "success",
                "exit_code": 0,
                "summary": "Recorded one finding.",
            }, now="2026-07-24T12:00:00Z")
            self.assertEqual(
                1,
                len(store.read(now="2026-07-24T12:00:00Z")),
            )

    def test_actionable_noop_is_warning(self):
        self.assertEqual("warning", classify_record({
            "outcome": "noop",
            "summary": '{"reason":"required lessons.md file unavailable"}',
        }))
```

Continue with `unittest.TestCase` and `tempfile.TemporaryDirectory` to match the
existing suite. Add tests proving:

- records older than seven days are removed;
- malformed JSONL lines are skipped while valid lines remain;
- two processes appending through the same lock produce two valid lines;
- `success` and `"no eligible item"` classify as `healthy`;
- missing configuration/provider/authentication classifies as `warning`;
- non-zero exit and `failed` classify as `failed`;
- a `None` record classifies as `unknown`.

- [ ] **Step 2: Run the new tests and confirm the module is missing**

Run:

```bash
python3 -m unittest tests.test_history -v
```

Expected: `ERROR` with `ModuleNotFoundError: scripts.pitcrew_history`.

- [ ] **Step 3: Implement the locked JSONL store**

Create `scripts/pitcrew_history.py` with a `HistoryStore` constructor accepting
`path: Path` and `retention_days: int = 7`; an `append(record: dict,
now: str | None = None) -> None` method; a keyword-only `read(now, skill,
outcome) -> list[dict]` method; and
`classify_record(record: dict | None) -> str`.

Implementation requirements:

- parse timestamps as timezone-aware UTC;
- validate the required string fields `project`, `skill`, `started_at`,
  `finished_at`, `outcome`, and `summary`;
- accept only outcomes `success`, `noop`, `failed`, and `interrupted`;
- use a sibling `history.jsonl.lock` opened with mode `0600` and
  `fcntl.flock(LOCK_EX)` for both append/prune and reads;
- while locked, parse valid records, append the new record, remove entries whose
  `finished_at` is older than `now - timedelta(days=7)`, and atomically replace
  the JSONL file with mode `0600`;
- sort reads newest-first;
- never include the lock file or invalid lines in returned data.

`classify_record` must parse JSON summaries when possible and mark a no-op as a
warning when its reason contains one of:

```python
("required", "unavailable", "missing", "authentication", "permission", "failed")
```

- [ ] **Step 4: Run the history tests**

Run:

```bash
python3 -m unittest tests.test_history -v
```

Expected: all history tests pass.

- [ ] **Step 5: Commit the history store**

```bash
git add scripts/pitcrew_history.py tests/test_history.py
git commit -m "feat: record bounded Pitcrew run history"
```

### Task 2: Instrument scheduled runs

**Files:**
- Modify: `scripts/pitcrew_locked_exec.py`
- Modify: `bin/pitcrew-codex.sh`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Add failing scheduled-history integration tests**

Extend `tests/test_cli.py` so the fake Codex binary writes its final summary,
then assert:

```python
history = [
    json.loads(line)
    for line in (codex_home / "pitcrew/getbill/history.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
]
self.assertEqual("research-run", history[-1]["skill"])
self.assertEqual("success", history[-1]["outcome"])
self.assertEqual("bounded summary", history[-1]["summary"])
self.assertGreaterEqual(history[-1]["duration_ms"], 0)
```

Add a second test whose fake Codex exits `17`; assert the runner returns `17` and
the record has `outcome == "failed"` and `exit_code == 17`. Preserve the existing
overlap and killed-helper regression tests.

- [ ] **Step 2: Run the focused tests and confirm history is absent**

Run:

```bash
python3 -m unittest \
  tests.test_cli.CliTest.test_scheduled_runner_is_ephemeral_networked_and_never_overlaps \
  tests.test_cli.CliTest.test_scheduled_runner_records_failed_pass -v
```

Expected: failure because `history.jsonl` is not written.

- [ ] **Step 3: Extend the locked executor**

Add arguments to `scripts/pitcrew_locked_exec.py`:

```python
result.add_argument("--summary-file", required=True, type=Path)
result.add_argument("--history-file", required=True, type=Path)
```

Record `started_at` before lock acquisition. On overlap, append a `noop` record
with reason `<skill> already running`. For an acquired run:

- use `time.monotonic_ns()` for duration;
- keep the inherited lock FD behavior;
- read at most 64 KiB from the summary file after the child exits;
- use the structured no-summary message
  `"No bounded final summary was produced."`;
- map return code `0` to `success`, negative signal return codes to
  `interrupted`, and other non-zero codes to `failed`;
- call `HistoryStore.append` before returning the child's code.

Update `bin/pitcrew-codex.sh` to set:

```bash
HISTORY_FILE="$RUNTIME_ROOT/$PROJECT/history.jsonl"
```

and pass `--summary-file "$SUMMARY_FILE"` plus
`--history-file "$HISTORY_FILE"` to the helper.

- [ ] **Step 4: Run CLI and history tests**

Run:

```bash
python3 -m unittest tests.test_cli tests.test_history -v
```

Expected: all tests pass, including overlap and crash behavior.

- [ ] **Step 5: Commit runner instrumentation**

```bash
git add scripts/pitcrew_locked_exec.py bin/pitcrew-codex.sh tests/test_cli.py
git commit -m "feat: capture scheduled agent outcomes"
```

### Task 3: Safe single-role launchd controls

**Files:**
- Modify: `bin/pitcrew-schedule.py`
- Modify: `tests/test_schedule.py`

- [ ] **Step 1: Add failing scheduler-state and control tests**

Extend `tests/test_schedule.py` with a fake `launchctl` executable. Assert that:

- `status --project getbill --skill research-run` returns one object containing
  `loaded`, `running`, and `pid`;
- parsing `launchctl print` containing `state = running` and `pid = 1234`
  produces `running: true` and `pid: 1234`;
- `install --skill research-run` bootstraps only the research plist;
- `stop --skill research-run` calls only:

```text
launchctl bootout gui/<uid>/io.getbill.pitcrew.getbill.research-run
```

- an unknown or disabled skill exits `2` without calling `launchctl`;
- launchctl failures propagate their return code and scrubbed stderr.

- [ ] **Step 2: Run scheduler tests and confirm unsupported arguments fail**

Run:

```bash
python3 -m unittest tests.test_schedule -v
```

Expected: new tests fail because `--skill` and `stop` do not exist.

- [ ] **Step 3: Implement filtered status, install, and stop**

Add `enabled_entry(skill: str) -> dict` that rejects unknown and disabled roles.
Extend the parser:

```python
install.add_argument("--skill")
status.add_argument("--skill")
stop = commands.add_parser("stop")
stop.add_argument("--project", type=validated_project, default="getbill")
stop.add_argument("--skill", required=True)
```

Change `install` and `status` to accept an optional selected skill. Add:

```python
def stop(project: str, skill: str) -> int:
    entry = enabled_entry(skill)
    label = launchd_label(project, str(entry["skill"]))
    result = launchctl("bootout", f"gui/{os.getuid()}/{label}", check=False)
    if result.returncode:
        print(result.stderr.strip() or f"failed to stop {label}", file=sys.stderr)
    return result.returncode
```

Parse `launchctl print` with anchored regular expressions for `state` and `pid`;
do not expose the raw output.

- [ ] **Step 4: Run scheduler tests**

Run:

```bash
python3 -m unittest tests.test_schedule -v
```

Expected: all scheduler tests pass.

- [ ] **Step 5: Commit scheduler controls**

```bash
git add bin/pitcrew-schedule.py tests/test_schedule.py
git commit -m "feat: control individual Pitcrew agents"
```

### Task 4: Dashboard data and GitLab adapters

**Files:**
- Create: `scripts/pitcrew_dashboard.py`
- Create: `tests/test_dashboard.py`

- [ ] **Step 1: Write failing aggregation and GitLab tests**

Create `tests/test_dashboard.py` using fake subprocess runners and a temporary
runtime. Test these interfaces:

```python
service = DashboardService(
    project="getbill",
    runtime_dir=runtime,
    command_runner=fake_runner,
    now=lambda: fixed_now,
)

snapshot = service.snapshot()
gitlab = service.gitlab_work(force_refresh=True)
result = service.control("trigger", "research-run")
```

Assertions must cover:

- seven enabled agents and six disabled roles;
- local `loaded`, `running`, interval, latest history, health, and estimated next
  pass fields;
- warning classification for the current missing `lessons.md` style result;
- GitLab issues grouped by lifecycle label and normalized route/source;
- related merge request URLs extracted from issue links and references;
- 60-second GitLab cache and forced refresh;
- unavailable or unauthenticated `glab` returns a degraded object while local
  snapshot still succeeds;
- unknown actions, disabled roles, and unknown roles are rejected before any
  subprocess call.

- [ ] **Step 2: Run dashboard tests and confirm the module is missing**

Run:

```bash
python3 -m unittest tests.test_dashboard -v
```

Expected: `ERROR` with `ModuleNotFoundError: scripts.pitcrew_dashboard`.

- [ ] **Step 3: Implement the dashboard service**

Create `DashboardError(RuntimeError)` and `DashboardService`. Its constructor
accepts `project`, `runtime_dir`, an injected subprocess runner, and an injected
UTC clock. Define `snapshot() -> dict`, `history(skill, outcome) -> list[dict]`,
`gitlab_work(force_refresh=False) -> dict`, and
`control(action, skill) -> dict`.

Use argument arrays only:

- schedule status:
  `python3 bin/pitcrew-schedule.py status --project getbill`;
- GitLab issues:
  `glab api projects/<urlencoded-project>/issues?scope=all&labels=pitcrew-agent&per_page=100`;
- GitLab merge requests:
  `glab api projects/<urlencoded-project>/merge_requests?scope=all&per_page=100`;
- trigger:
  `bin/pitcrew-codex.sh <skill> getbill --scheduled`, started detached from the
  request with stdout/stderr redirected to `/dev/null`;
- stop:
  `python3 bin/pitcrew-schedule.py stop --project getbill --skill <skill>`;
- restart:
  `python3 bin/pitcrew-schedule.py install --project getbill --skill <skill>`.

Resolve the GitLab project from validated runtime configuration, not browser
input. Limit subprocess error text to 2 KiB and redact strings matching
`token`, `authorization`, `password`, or `secret` key/value patterns.

- [ ] **Step 4: Run dashboard service tests**

Run:

```bash
python3 -m unittest tests.test_dashboard -v
```

Expected: aggregation, caching, degradation, and control tests pass.

- [ ] **Step 5: Commit dashboard service**

```bash
git add scripts/pitcrew_dashboard.py tests/test_dashboard.py
git commit -m "feat: aggregate Pitcrew dashboard data"
```

### Task 5: Local HTTP API and security boundary

**Files:**
- Create: `bin/pitcrew-dashboard`
- Modify: `tests/test_dashboard.py`

- [ ] **Step 1: Add failing HTTP security and route tests**

Start the server in-process on port `0` with a fake `DashboardService`. Assert:

- `GET /api/status`, `/api/history`, and `/api/gitlab` return JSON;
- `GET /` and the three asset routes return local files;
- missing assets return `404`;
- POST `/api/actions` requires `X-Pitcrew-Session`;
- invalid `Origin` or `Host` returns `403`;
- valid `{action: "trigger", skill: "research-run"}` returns `202`;
- malformed JSON, unknown fields, disabled roles, and oversized bodies return
  `400` or `403` without calling controls;
- response headers include
  `Content-Security-Policy: default-src 'self'`,
  `X-Content-Type-Options: nosniff`, and `Cache-Control: no-store`.

- [ ] **Step 2: Run HTTP tests and confirm the entry point is missing**

Run:

```bash
python3 -m unittest tests.test_dashboard.DashboardHttpTest -v
```

Expected: failure because `bin/pitcrew-dashboard` does not exist.

- [ ] **Step 3: Implement the localhost server**

Create executable `bin/pitcrew-dashboard` with a
`create_server(host, port, service, session_token) -> ThreadingHTTPServer`
factory and a `main() -> int` entry point.

Requirements:

- accept `--project getbill`, `--host 127.0.0.1`, and `--port 8765`;
- reject every host other than `127.0.0.1`;
- generate `secrets.token_urlsafe(32)` once per process;
- serve assets from the repository's `dashboard/` directory using fixed route
  mappings, never arbitrary paths;
- inject the session token into `index.html` by replacing the literal
  `__PITCREW_SESSION_TOKEN__`;
- accept request bodies up to 8 KiB;
- validate `Host` against `127.0.0.1:<actual-port>` and allow an absent `Origin`
  or exact same-origin value only;
- install SIGINT/SIGTERM handlers for clean shutdown;
- print exactly one startup line:
  `Pitcrew dashboard: http://127.0.0.1:<port>`.

- [ ] **Step 4: Run dashboard HTTP tests**

Run:

```bash
python3 -m unittest tests.test_dashboard -v
```

Expected: all service and HTTP tests pass.

- [ ] **Step 5: Commit the HTTP server**

```bash
git add bin/pitcrew-dashboard tests/test_dashboard.py
git commit -m "feat: serve Pitcrew dashboard locally"
```

### Task 6: Browser interface

**Files:**
- Create: `dashboard/index.html`
- Create: `dashboard/app.js`
- Create: `dashboard/styles.css`
- Modify: `tests/test_dashboard.py`

- [ ] **Step 1: Add failing static-interface contract tests**

Add tests that load the three assets and assert:

- semantic `header`, `main`, `section`, and status live-region elements;
- overview, agents, activity, GitLab, and disabled-role containers;
- session-token replacement marker exists exactly once in HTML;
- buttons are created with `type="button"` and action names are allowlisted;
- `app.js` polls local status at `10_000` ms and never contains an absolute HTTP
  URL;
- controls send `X-Pitcrew-Session`;
- stop uses a confirmation dialog;
- CSS includes visible `:focus-visible`, reduced-motion handling, responsive
  breakpoints, and warning/error tokens;
- no external scripts, fonts, images, or stylesheets.

- [ ] **Step 2: Run static tests and confirm assets are missing**

Run:

```bash
python3 -m unittest tests.test_dashboard.DashboardAssetsTest -v
```

Expected: failures for missing `dashboard/` assets.

- [ ] **Step 3: Implement the semantic HTML shell**

Create `dashboard/index.html` with:

- a header containing project name, refresh state, and manual refresh;
- overview summary cards;
- agent-card grid and collapsed disabled-role list;
- history filters for skill and outcome;
- activity list;
- lifecycle-grouped GitLab work;
- an `aria-live="polite"` operational status region;
- a `<meta name="pitcrew-session" content="__PITCREW_SESSION_TOKEN__">`;
- only local `/assets/styles.css` and `/assets/app.js` references.

- [ ] **Step 4: Implement polling, rendering, and controls**

Create `dashboard/app.js` as an ES module with
`POLL_INTERVAL_MS = 10_000`, an allowlist containing `trigger`, `stop`, and
`restart`, plus focused `fetchJson`, `renderOverview`, `renderAgents`,
`renderHistory`, `renderGitLab`, `control`, and `refresh` functions.

Use `textContent`, never `innerHTML`, for external summaries and GitLab content.
Disable a role's buttons while its action is pending. Confirm stop with:

```javascript
window.confirm(`Arrêter ${skill} et son passage courant ?`)
```

Poll local status every ten seconds and GitLab at most once per minute unless
manual refresh is requested.

- [ ] **Step 5: Implement responsive styles**

Create `dashboard/styles.css` using system fonts, CSS custom properties, a
responsive card grid, visible outcome badges, left-aligned operational text,
keyboard focus styles, and:

```css
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    scroll-behavior: auto !important;
    transition-duration: 0.01ms !important;
  }
}
```

Do not use animations, remote assets, or icon dependencies.

- [ ] **Step 6: Run all dashboard tests**

Run:

```bash
python3 -m unittest tests.test_dashboard -v
```

Expected: service, HTTP, security, and asset tests pass.

- [ ] **Step 7: Commit the browser interface**

```bash
git add dashboard/index.html dashboard/app.js dashboard/styles.css tests/test_dashboard.py
git commit -m "feat: add Pitcrew monitoring interface"
```

### Task 7: Documentation and end-to-end verification

**Files:**
- Modify: `README.md`
- Modify: `references/SCHEDULED-TASKS.md`
- Modify: `tests/test_docs.py`

- [ ] **Step 1: Add failing documentation contract tests**

Extend `tests/test_docs.py` to assert the primary docs include:

```text
./bin/pitcrew-dashboard
http://127.0.0.1:8765
seven-day history
trigger, stop, and restart
```

Also assert the docs state that release, prod, and preprod controls are absent.

- [ ] **Step 2: Run docs tests and confirm the dashboard is undocumented**

Run:

```bash
python3 -m unittest tests.test_docs -v
```

Expected: new dashboard documentation assertions fail.

- [ ] **Step 3: Document operation and limits**

Update `README.md` with:

```bash
cd /Users/jo/Prog/pitcrew
./bin/pitcrew-dashboard
```

Document the default URL, Ctrl-C shutdown, seven-day history, GitLab degradation,
the three controls, and the absence of release/prod/preprod actions.

Update `references/SCHEDULED-TASKS.md` with the history-record schema, retention
rule, and the fact that an expected no-op is healthy while an actionable no-op is
a dashboard warning.

- [ ] **Step 4: Run the complete deterministic suite**

Run:

```bash
bash tests/run.sh
bash -n bin/pitcrew-codex.sh
env PYTHONPYCACHEPREFIX=/private/tmp/pitcrew-pycache \
  python3 -m py_compile \
  bin/pitcrew-schedule.py \
  bin/pitcrew-dashboard \
  scripts/pitcrew_config.py \
  scripts/pitcrew_history.py \
  scripts/pitcrew_locked_exec.py \
  scripts/pitcrew_dashboard.py
git diff --check
```

Expected: all tests pass, syntax checks return zero, and `git diff --check`
prints nothing.

- [ ] **Step 5: Run a local smoke check**

Start on an ephemeral port without touching remote environments:

```bash
./bin/pitcrew-dashboard --project getbill --port 0
```

From another terminal, request the printed URL and verify:

```bash
curl -fsS http://127.0.0.1:<printed-port>/api/status
curl -fsS http://127.0.0.1:<printed-port>/
```

Expected: status JSON includes seven enabled roles; HTML includes
`Pitcrew dashboard`. Stop the server with Ctrl-C.

- [ ] **Step 6: Validate and refresh the Codex plugin**

Run:

```bash
/Users/jo/Prog/getbill/.venv/bin/python \
  /Users/jo/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py \
  /Users/jo/Prog/pitcrew

python3 \
  /Users/jo/.codex/skills/.system/plugin-creator/scripts/update_plugin_cachebuster.py \
  /Users/jo/Prog/pitcrew

codex plugin add pitcrew@personal
```

Expected: plugin validation passes and Codex reports the refreshed installed
plugin root.

- [ ] **Step 7: Commit documentation and final metadata**

```bash
git add \
  .codex-plugin/plugin.json \
  README.md \
  references/SCHEDULED-TASKS.md \
  tests/test_docs.py
git commit -m "docs: explain Pitcrew dashboard operations"
```

- [ ] **Step 8: Final branch verification**

Run:

```bash
git status --short
git log --oneline -8
```

Expected: clean working tree and the dashboard task commits listed in order.
