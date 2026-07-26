# Autonomous Fixes Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a project-scoped dashboard switch that lets `improvement` tickets in `todo` or `blocked` merge without human review, human `go`, or green CI, while keeping bugs and feature proposals human-gated.

**Architecture:** A validated `delivery.fix_autonomy` value lives in the runtime project configuration and defaults to `off`. The dashboard exposes the value in its status snapshot and changes it through the same locked, atomic runtime-config mechanism used for model changes. `implementer-run` reads the value for each review decision and uses an autonomous merge path only when it is `on` and the ticket has the configured `improvement` label in `todo` or `blocked`.

**Tech Stack:** Python 3 standard library, JSON project profiles, Markdown skill contracts, browser JavaScript, HTML/CSS, Python `unittest`, Node test runner.

---

## File structure

- Modify `scripts/pitcrew_config.py`: validate and atomically update `delivery.fix_autonomy`.
- Modify `profiles/getbill.json`, `profiles/generic.json`, `references/config.example.json`: document the explicit disabled default.
- Modify `scripts/pitcrew_dashboard.py`: expose and toggle the persisted policy.
- Modify `bin/pitcrew-dashboard`: validate the authenticated dashboard action.
- Modify `dashboard/index.html`, `dashboard/app.js`, `dashboard/styles.css`: render and operate the switch.
- Modify `skills/implementer-run/SKILL.md`: select the autonomous merge decision and record its audit evidence.
- Modify `tests/test_config.py`, `tests/test_dashboard.py`, `tests/test_skill_contracts.py`: lock the policy, API and delivery contract.
- Modify `README.md`: describe the opt-in behavior and unchanged proposal gate.

### Task 1: Persist and validate the project policy

**Files:**
- Modify: `scripts/pitcrew_config.py`
- Modify: `profiles/getbill.json`
- Modify: `profiles/generic.json`
- Modify: `references/config.example.json`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write failing configuration tests**

```python
def test_delivery_fix_autonomy_defaults_off_and_accepts_known_values(self):
    profile = json.loads((ROOT / "profiles/generic.json").read_text(encoding="utf-8"))
    validate(profile)
    self.assertEqual("off", fix_autonomy(profile))
    for value in ("off", "on"):
        candidate = json.loads(json.dumps(profile))
        candidate["delivery"] = {"fix_autonomy": value}
        validate(candidate)
        self.assertEqual(value, fix_autonomy(candidate))

def test_delivery_fix_autonomy_rejects_invalid_shape(self):
    profile = json.loads((ROOT / "profiles/generic.json").read_text(encoding="utf-8"))
    for value in (True, "enabled", {"fix_autonomy": "on"}):
        candidate = json.loads(json.dumps(profile))
        candidate["delivery"] = value
        with self.assertRaisesRegex(ConfigError, "delivery"):
            validate(candidate)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_config.ConfigTest.test_delivery_fix_autonomy_defaults_off_and_accepts_known_values tests.test_config.ConfigTest.test_delivery_fix_autonomy_rejects_invalid_shape -v`

Expected: FAIL because `fix_autonomy` is not defined.

- [ ] **Step 3: Implement the minimal policy API**

Add a `FIX_AUTONOMY_VALUES = {"off", "on"}` constant, `fix_autonomy(config) -> str`, and this validation in `validate`:

```python
delivery = config.get("delivery", {})
if not isinstance(delivery, Mapping):
    raise ConfigError("delivery must be an object")
if set(delivery) - {"fix_autonomy"}:
    raise ConfigError("delivery contains unsupported fields")
if "fix_autonomy" in delivery and delivery["fix_autonomy"] not in FIX_AUTONOMY_VALUES:
    raise ConfigError("delivery.fix_autonomy must be off or on")
```

`fix_autonomy` returns `delivery.get("fix_autonomy", "off")`. Add `"delivery": {"fix_autonomy": "off"}` beside `release` in all three configuration examples.

- [ ] **Step 4: Run the focused tests**

Run: `python3 -m unittest tests.test_config.ConfigTest.test_delivery_fix_autonomy_defaults_off_and_accepts_known_values tests.test_config.ConfigTest.test_delivery_fix_autonomy_rejects_invalid_shape -v`

Expected: PASS.

- [ ] **Step 5: Commit the configuration contract**

```bash
git add scripts/pitcrew_config.py profiles/getbill.json profiles/generic.json references/config.example.json tests/test_config.py
git commit -m "feat: configure autonomous fixes policy"
```

### Task 2: Make policy changes atomic and visible to the dashboard

**Files:**
- Modify: `scripts/pitcrew_config.py`
- Modify: `scripts/pitcrew_dashboard.py`
- Test: `tests/test_config.py`
- Test: `tests/test_dashboard.py`

- [ ] **Step 1: Write failing service tests**

```python
def test_set_fix_autonomy_replaces_only_delivery_policy(self):
    with tempfile.TemporaryDirectory() as temp:
        env = {"CODEX_HOME": str(Path(temp).resolve())}
        destination = write_project(ROOT / "profiles/getbill.json", "getbill", env)
        update_runtime_fix_autonomy("getbill", "on", env)
        updated = json.loads(destination.read_text(encoding="utf-8"))
        self.assertEqual("on", updated["delivery"]["fix_autonomy"])

def test_dashboard_fix_autonomy_snapshot_and_update(self):
    service = self.service(FakeRunner())
    self.assertEqual("off", service.snapshot()["fix_autonomy"])
    self.assertEqual({"accepted": True, "fix_autonomy": "on"}, service.set_fix_autonomy("on"))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_config.ConfigTest.test_set_fix_autonomy_replaces_only_delivery_policy tests.test_dashboard.DashboardServiceTest.test_dashboard_fix_autonomy_snapshot_and_update -v`

Expected: FAIL because `update_runtime_fix_autonomy` and `set_fix_autonomy` do not exist.

- [ ] **Step 3: Implement locked updates and snapshot state**

In `scripts/pitcrew_config.py`, add `update_runtime_fix_autonomy(project, mode, env=None)`. Mirror `update_runtime_model`: open the runtime project, acquire `_lock_runtime_config`, load, copy only `delivery`, assign `fix_autonomy`, validate, then call `_replace_runtime_config`; always unlock and close descriptors.

In `DashboardService.snapshot`, add:

```python
"fix_autonomy": fix_autonomy(self.config),
```

Add `DashboardService.set_fix_autonomy(mode)` under `_control_lock`; reject values outside `FIX_AUTONOMY_VALUES`, call `update_runtime_fix_autonomy`, reload `self.config`, and return `{"accepted": True, "fix_autonomy": mode}`. Convert `ConfigError` and `OSError` to the existing redacted `DashboardError` pattern.

- [ ] **Step 4: Run the focused tests**

Run: `python3 -m unittest tests.test_config tests.test_dashboard.DashboardServiceTest -v`

Expected: PASS.

- [ ] **Step 5: Commit the service boundary**

```bash
git add scripts/pitcrew_config.py scripts/pitcrew_dashboard.py tests/test_config.py tests/test_dashboard.py
git commit -m "feat: expose autonomous fixes policy"
```

### Task 3: Add the authenticated dashboard control

**Files:**
- Modify: `bin/pitcrew-dashboard`
- Modify: `dashboard/index.html`
- Modify: `dashboard/app.js`
- Modify: `dashboard/styles.css`
- Test: `tests/test_dashboard.py`
- Test: `tests/dashboard_pilotage.test.mjs`

- [ ] **Step 1: Write failing HTTP and UI contract tests**

```python
def test_post_fix_autonomy_requires_session_and_exact_payload(self):
    status, _, _ = self.request("POST", "/api/actions", json.dumps({
        "action": "set-fix-autonomy", "mode": "on"
    }).encode(), {"Content-Type": "application/json"})
    self.assertEqual(403, status)
    status, _, _ = self.request("POST", "/api/actions", json.dumps({
        "action": "set-fix-autonomy", "mode": "on"
    }).encode(), self.auth_headers())
    self.assertEqual(202, status)
    self.assertEqual([("set_fix_autonomy", "on")], self.service.calls[-1:])
```

```js
assert.match(html, /id="fix-autonomy-toggle"/);
assert.match(javascript, /api\.action\(\{action: "set-fix-autonomy", mode\}\)/);
assert.match(javascript, /snapshot\?\.fix_autonomy === "on"/);
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_dashboard.DashboardEntryPointTest.test_post_fix_autonomy_requires_session_and_exact_payload -v && node --test tests/dashboard_pilotage.test.mjs`

Expected: FAIL because the action and control do not exist.

- [ ] **Step 3: Implement the route and control**

Add an action branch before generic skill actions in `bin/pitcrew-dashboard`:

```python
if action == "set-fix-autonomy":
    if set(request) != {"action", "mode"} or request.get("mode") not in {"off", "on"}:
        self._send_json(400, {"error": "invalid request"})
        return
    try:
        result = service.set_fix_autonomy(request["mode"])
    except DashboardError:
        self._send_json(403, {"error": "action rejected"})
        return
    self._send_json(202, result)
    return
```

Add a labelled checkbox (`id="fix-autonomy-toggle"`) beside the existing global actions. In `renderOverview`, set it from `snapshot.fix_autonomy === "on"`; on change, call `api.action({action: "set-fix-autonomy", mode})`, render the pending/success/error state with `runAction`, and refresh. Do not add a confirmation dialog: it is an explicit persistent control. Add compact styles that preserve the existing header layout and its narrow-screen icon behavior.

- [ ] **Step 4: Run the focused tests**

Run: `python3 -m unittest tests.test_dashboard.DashboardEntryPointTest -v && node --test tests/dashboard_pilotage.test.mjs`

Expected: PASS.

- [ ] **Step 5: Commit the dashboard control**

```bash
git add bin/pitcrew-dashboard dashboard/index.html dashboard/app.js dashboard/styles.css tests/test_dashboard.py tests/dashboard_pilotage.test.mjs
git commit -m "feat: add autonomous fixes dashboard toggle"
```

### Task 4: Route correction merges through the autonomous policy

**Files:**
- Modify: `skills/implementer-run/SKILL.md`
- Modify: `tests/test_skill_contracts.py`
- Modify: `README.md`

- [ ] **Step 1: Write the failing skill-contract test**

```python
def test_implementer_autonomous_fix_policy_bypasses_only_merge_gates(self):
    skill = (ROOT / "skills/implementer-run/SKILL.md").read_text(encoding="utf-8")
    for required in (
        'FIX_AUTONOMY=$(jq -r \' .delivery.fix_autonomy // "off" \')',
        "AUTONOMOUS_FIX_MERGE",
        "CI result observed",
        "improvement label",
        "bug tickets remain human-gated",
        "feature proposals remain human-approved",
        "prod", "preprod", "allow_secret_reads", "allow_database_writes",
    ):
        self.assertIn(required, skill)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest tests.test_skill_contracts.SkillContractTest.test_implementer_autonomous_fix_policy_bypasses_only_merge_gates -v`

Expected: FAIL because the autonomous merge policy is absent.

- [ ] **Step 3: Implement the explicit decision precedence**

In the canonical config-loading block of `skills/implementer-run/SKILL.md`, extract the defaulted policy from the already validated `CONFIG_FILE`:

```sh
FIX_AUTONOMY=$(jq -r '.delivery.fix_autonomy // "off"' "$CONFIG_FILE")
case "$FIX_AUTONOMY" in on|off) ;; *) bail "invalid fix autonomy policy";; esac
```

Extend the Step A verdict table with `AUTONOMOUS_FIX_MERGE`: it applies only when `FIX_AUTONOMY=on`, the ticket is labelled with configured `$IMPROVEMENT_LABEL`, its state is `$STATE_TODO` or `$STATE_BLOCKED`, and no explicit human `WAIT`/`HOLD`/`STOP` is newer than the ready marker. It precedes `CHANGES`, `GO`, reviewer sign-off and diff classification, so CI and reviewer state are observed and recorded but never block. Its action still calls `READ_CHANGE_CHECKS`, captures its status, calls `MERGE_CHANGE --squash --delete-branch`, closes and verifies the tracker lifecycle, then posts a comment containing `Autonomous improvement merge`, the change URL, and `CI result observed: <status>`. It must never apply to `$BUG_LABEL` tickets, feature/proposal tickets, relax existing repository safety policy, or proposal approval behavior.

Document the opt-in switch, its improvement-only eligibility, and the unchanged bug/feature-proposal gates in `README.md`.

- [ ] **Step 4: Run the focused tests**

Run: `python3 -m unittest tests.test_skill_contracts tests.test_docs -v`

Expected: PASS.

- [ ] **Step 5: Commit the delivery behavior**

```bash
git add skills/implementer-run/SKILL.md tests/test_skill_contracts.py README.md
git commit -m "feat: merge fixes autonomously when enabled"
```

### Task 5: Run complete verification

**Files:**
- Verify only: all files above

- [ ] **Step 1: Validate Python behavior**

Run: `python3 -m unittest tests.test_config tests.test_dashboard tests.test_skill_contracts tests.test_docs -v`

Expected: PASS with no failures or errors.

- [ ] **Step 2: Validate dashboard behavior**

Run: `node --test tests/dashboard_pilotage.test.mjs tests/dashboard_view_model.test.mjs`

Expected: PASS with no failures.

- [ ] **Step 3: Validate the plugin bundle**

Run: `python3 /Users/jo/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py .`

Expected: exit code 0.

- [ ] **Step 4: Inspect the final diff**

Run: `git diff HEAD~4..HEAD --check && git status --short`

Expected: no whitespace errors; only pre-existing unrelated worktree changes remain.
