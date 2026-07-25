# Preprod Review Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a manual-only Sol/xhigh agent that exhaustively reviews `origin/preprod...origin/develop` and exposes a fail-closed local report in the dashboard.

**Architecture:** A deterministic Python helper owns safe ref validation, fetch/SHA capture, manifest construction, report validation, atomic persistence, and bounded history. The Codex skill consumes that manifest and produces findings plus a synthesis; the existing locked runner supplies process isolation, live state, usage, and global-stop enforcement. A dedicated dashboard panel triggers and stops the manual role without registering any `launchd` schedule.

**Tech Stack:** Python 3 standard library, Git CLI, Bash, JSON, Markdown Codex skills, Python `unittest`, vanilla HTML/CSS/JavaScript dashboard.

**Dependency:** Complete `docs/superpowers/plans/2026-07-26-architecture-agent.md` Task 1 first so model reasoning effort is explicit and validated.

---

### Task 1: Build deterministic Preprod manifest preparation

**Files:**
- Create: `scripts/pitcrew_preprod_review.py`
- Create: `tests/test_preprod_review.py`

- [ ] **Step 1: Write failing ref-validation tests**

Create `tests/test_preprod_review.py` with:

```python
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.pitcrew_preprod_review import (
    PreprodReviewError,
    build_manifest,
    validate_remote_ref,
)

class PreprodReviewTest(unittest.TestCase):
    def test_remote_refs_are_explicit_and_safe(self):
        self.assertEqual("origin/preprod", validate_remote_ref("origin/preprod"))
        self.assertEqual(
            "origin/feature/release-1",
            validate_remote_ref("origin/feature/release-1"),
        )
        for value in (
            "preprod",
            "upstream/preprod",
            "origin/../prod",
            "origin/preprod..prod",
            "origin/preprod^{commit}",
            "origin/pre prod",
        ):
            with self.subTest(value=value):
                with self.assertRaises(PreprodReviewError):
                    validate_remote_ref(value)
```

- [ ] **Step 2: Write a failing manifest integration test**

In the same file, create a temporary bare remote plus a working clone. Commit a
base file on `preprod`, branch `develop`, then add, rename, and delete tracked
files. Assert `build_manifest(repo, "origin/preprod", "origin/develop")` returns:

```python
self.assertEqual("origin/preprod", manifest["base_ref"])
self.assertEqual("origin/develop", manifest["compare_ref"])
self.assertRegex(manifest["base_sha"], r"^[0-9a-f]{40}$")
self.assertRegex(manifest["compare_sha"], r"^[0-9a-f]{40}$")
self.assertRegex(manifest["merge_base_sha"], r"^[0-9a-f]{40}$")
self.assertEqual(
    [
        {
            "status": "A",
            "path": "src/added.php",
            "binary": False,
            "generated": False,
            "reviewed": False,
        },
        {
            "status": "D",
            "path": "src/deleted.php",
            "binary": False,
            "generated": False,
            "reviewed": False,
        },
        {
            "status": "R100",
            "old_path": "src/old.php",
            "path": "src/renamed.php",
            "binary": False,
            "generated": False,
            "reviewed": False,
        },
    ],
    manifest["files"],
)
self.assertTrue(manifest["commits"])
self.assertTrue(all(item["reviewed"] is False for item in manifest["files"]))
```

- [ ] **Step 3: Run the test and confirm failure**

Run:

```bash
python3 -m unittest tests.test_preprod_review -v
```

Expected: import failure because the helper does not exist.

- [ ] **Step 4: Implement safe refs and Git command execution**

Create `scripts/pitcrew_preprod_review.py` with:

```python
#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

REMOTE_REF = re.compile(
    r"^origin/[A-Za-z0-9](?:[A-Za-z0-9._/-]*[A-Za-z0-9])?$"
)
SHA = re.compile(r"^[0-9a-f]{40}$")

class PreprodReviewError(ValueError):
    pass

def validate_remote_ref(value: object) -> str:
    if (
        not isinstance(value, str)
        or not REMOTE_REF.fullmatch(value)
        or ".." in value
        or "//" in value
        or "@{" in value
        or value.endswith(".lock")
    ):
        raise PreprodReviewError("preprod review ref is invalid")
    return value

def _git(
    repo: Path,
    *args: str,
    runner: Callable = subprocess.run,
    text: bool = True,
) -> subprocess.CompletedProcess:
    try:
        result = runner(
            ["git", "-C", str(repo), *args],
            check=False,
            capture_output=True,
            text=text,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise PreprodReviewError("git command unavailable") from error
    if result.returncode:
        raise PreprodReviewError("git command failed")
    return result

def _resolve(repo: Path, ref: str, runner: Callable) -> str:
    value = _git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}", runner=runner).stdout.strip()
    if not SHA.fullmatch(value):
        raise PreprodReviewError("git returned an invalid sha")
    return value
```

- [ ] **Step 5: Implement deterministic manifest parsing**

Add functions that fetch only the configured branch names, resolve the three
SHAs, parse commits from a NUL-delimited format, and parse name status with
renames:

```python
def _branch_name(ref: str) -> str:
    return ref.removeprefix("origin/")

def _parse_name_status(raw: bytes) -> list[dict]:
    fields = [
        item.decode("utf-8", errors="replace")
        for item in raw.split(b"\0")
        if item
    ]
    files = []
    index = 0
    while index < len(fields):
        status = fields[index]
        index += 1
        if status.startswith(("R", "C")):
            if index + 1 >= len(fields):
                raise PreprodReviewError("git diff manifest is malformed")
            old_path, path = fields[index], fields[index + 1]
            index += 2
            files.append({
                "status": status,
                "old_path": old_path,
                "path": path,
                "reviewed": False,
            })
        else:
            if index >= len(fields):
                raise PreprodReviewError("git diff manifest is malformed")
            files.append({
                "status": status,
                "path": fields[index],
                "reviewed": False,
            })
            index += 1
    return sorted(files, key=lambda item: (item["path"], item["status"]))

def build_manifest(
    repo: Path,
    base_ref: str,
    compare_ref: str,
    runner: Callable = subprocess.run,
) -> dict:
    repo = Path(repo).resolve()
    base_ref = validate_remote_ref(base_ref)
    compare_ref = validate_remote_ref(compare_ref)
    if base_ref == compare_ref:
        raise PreprodReviewError("preprod review refs must differ")
    _git(
        repo,
        "fetch",
        "--no-tags",
        "origin",
        _branch_name(base_ref),
        _branch_name(compare_ref),
        runner=runner,
    )
    base_sha = _resolve(repo, base_ref, runner)
    compare_sha = _resolve(repo, compare_ref, runner)
    merge_base_sha = _git(
        repo, "merge-base", base_sha, compare_sha, runner=runner
    ).stdout.strip()
    if not SHA.fullmatch(merge_base_sha):
        raise PreprodReviewError("git returned an invalid merge base")
    commits_raw = _git(
        repo,
        "log",
        "-z",
        "--format=%H%x00%aI%x00%s%x00",
        f"{base_sha}..{compare_sha}",
        runner=runner,
    ).stdout
    commit_fields = [
        item.strip("\n")
        for item in commits_raw.split("\0")
        if item.strip("\n")
    ]
    if len(commit_fields) % 3:
        raise PreprodReviewError("git commit manifest is malformed")
    commits = [
        {
            "sha": commit_fields[index],
            "authored_at": commit_fields[index + 1],
            "subject": commit_fields[index + 2],
        }
        for index in range(0, len(commit_fields), 3)
    ]
    diff = _git(
        repo,
        "diff",
        "--name-status",
        "-z",
        "--find-renames",
        f"{base_sha}...{compare_sha}",
        runner=runner,
        text=False,
    ).stdout
    files = _classify_files(
        repo,
        base_sha,
        compare_sha,
        _parse_name_status(diff),
        runner,
    )
    return {
        "schema_version": 1,
        "prepared_at": datetime.now(UTC).isoformat(),
        "base_ref": base_ref,
        "compare_ref": compare_ref,
        "base_sha": base_sha,
        "compare_sha": compare_sha,
        "merge_base_sha": merge_base_sha,
        "commit_count": len(commits),
        "changed_file_count": len(files),
        "commits": commits,
        "files": files,
    }
```

When the runner uses `text=False`, ensure `_git` passes no incompatible encoding
arguments. Implement `_classify_files` by parsing
`git diff --numstat -z <base>...<compare>`: `-` additions/deletions marks a
binary path. Mark `generated=True` when any path component is one of
`generated`, `dist`, `build`, `coverage`, or `graphify-out`; otherwise mark it
false. Preserve both flags in every manifest item and add a binary fixture to
the integration test.

Use this implementation:

```python
GENERATED_PARTS = {
    "generated", "dist", "build", "coverage", "graphify-out",
}

def _classify_files(
    repo: Path,
    base_sha: str,
    compare_sha: str,
    files: list[dict],
    runner: Callable,
) -> list[dict]:
    classified = []
    for item in files:
        result = _git(
            repo,
            "diff",
            "--numstat",
            f"{base_sha}...{compare_sha}",
            "--",
            item["path"],
            runner=runner,
        )
        first = result.stdout.splitlines()[0] if result.stdout.splitlines() else ""
        columns = first.split("\t", 2)
        binary = len(columns) >= 2 and columns[0] == columns[1] == "-"
        parts = set(Path(item["path"]).parts)
        classified.append({
            **item,
            "binary": binary,
            "generated": bool(parts & GENERATED_PARTS),
        })
    return classified
```

- [ ] **Step 6: Run focused tests**

Run:

```bash
python3 -m unittest tests.test_preprod_review -v
```

Expected: safe-ref and manifest tests pass.

- [ ] **Step 7: Commit**

```bash
git add scripts/pitcrew_preprod_review.py tests/test_preprod_review.py
git commit -m "feat: prepare deterministic preprod review manifest"
```

### Task 2: Add fail-closed report validation and storage

**Files:**
- Modify: `scripts/pitcrew_preprod_review.py`
- Modify: `tests/test_preprod_review.py`

- [ ] **Step 1: Write failing verdict and completeness tests**

Add fixtures with two manifest files and assert:

```python
ready = finalize_report(
    manifest,
    {
        "reviewed_files": ["src/a.php", "src/b.php"],
        "findings": [],
        "synthesis": "No blocking cross-change interaction found.",
    },
    model="gpt-5.6-sol",
    reasoning_effort="xhigh",
)
self.assertEqual("ready", ready["verdict"])

required = finalize_report(
    manifest,
    {
        "reviewed_files": ["src/a.php", "src/b.php"],
        "findings": [{
            "severity": "high",
            "title": "Migration incompatible",
            "evidence": ["migrations/Version1.php:20"],
            "affected_files": ["migrations/Version1.php"],
            "impact": "Deployment can fail.",
            "recommendation": "Split the incompatible migration.",
        }],
        "synthesis": "The migration blocks rollout.",
    },
    model="gpt-5.6-sol",
    reasoning_effort="xhigh",
)
self.assertEqual("changes_required", required["verdict"])
```

Assert missing files, empty synthesis, an unsupported severity, model other than
Sol, or effort other than xhigh returns an `incomplete` report with a bounded
reason and never `ready`.

- [ ] **Step 2: Write failing history and permission tests**

Create `ReportStore(path, history_limit=2)`, save three distinct SHA pairs, and
assert only two remain. Save the same SHA pair twice and assert it replaces
rather than appends. Assert directory mode `0700`, file mode `0600`, and corrupt
JSON raises `PreprodReviewError`.

- [ ] **Step 3: Run focused tests and confirm failure**

Run:

```bash
python3 -m unittest tests.test_preprod_review -v
```

Expected: `finalize_report` and `ReportStore` do not exist.

- [ ] **Step 4: Implement report finalization**

Add constants and validation:

```python
SEVERITIES = {"critical", "high", "medium", "low"}

def _incomplete(
    manifest: dict,
    reason: str,
    *,
    model: str,
    reasoning_effort: str,
) -> dict:
    return {
        **manifest,
        "completed_at": datetime.now(UTC).isoformat(),
        "findings": [],
        "synthesis": "",
        "verdict": "incomplete",
        "failure_reason": " ".join(reason.split())[:2048],
        "model": model,
        "reasoning_effort": reasoning_effort,
    }

def finalize_report(
    manifest: dict,
    result: object,
    *,
    model: str,
    reasoning_effort: str,
) -> dict:
    if model != "gpt-5.6-sol" or reasoning_effort != "xhigh":
        return _incomplete(
            manifest,
            "model policy mismatch",
            model=model,
            reasoning_effort=reasoning_effort,
        )
    if not isinstance(result, dict):
        return _incomplete(
            manifest,
            "analysis result is invalid",
            model=model,
            reasoning_effort=reasoning_effort,
        )
    expected = {item["path"] for item in manifest["files"]}
    reviewed = result.get("reviewed_files")
    if (
        not isinstance(reviewed, list)
        or any(not isinstance(path, str) for path in reviewed)
        or set(reviewed) != expected
        or len(reviewed) != len(set(reviewed))
    ):
        return _incomplete(
            manifest,
            "file coverage is incomplete",
            model=model,
            reasoning_effort=reasoning_effort,
        )
    synthesis = result.get("synthesis")
    findings = result.get("findings")
    if not isinstance(synthesis, str) or (expected and not synthesis.strip()):
        return _incomplete(
            manifest,
            "cross-change synthesis is missing",
            model=model,
            reasoning_effort=reasoning_effort,
        )
    if not isinstance(findings, list):
        return _incomplete(
            manifest,
            "findings are invalid",
            model=model,
            reasoning_effort=reasoning_effort,
        )
    required = {
        "severity", "title", "evidence", "affected_files",
        "impact", "recommendation",
    }
    for finding in findings:
        if (
            not isinstance(finding, dict)
            or not required.issubset(finding)
            or finding["severity"] not in SEVERITIES
            or not all(isinstance(finding[key], str) and finding[key].strip()
                       for key in ("title", "impact", "recommendation"))
            or not all(isinstance(value, list) and
                       all(isinstance(item, str) and item for item in value)
                       for value in (finding["evidence"], finding["affected_files"]))
        ):
            return _incomplete(
                manifest,
                "finding schema is invalid",
                model=model,
                reasoning_effort=reasoning_effort,
            )
    blocking = any(
        finding["severity"] in {"critical", "high"}
        for finding in findings
    )
    return {
        **manifest,
        "completed_at": datetime.now(UTC).isoformat(),
        "files": [
            {**item, "reviewed": item["path"] in set(reviewed)}
            for item in manifest["files"]
        ],
        "findings": findings,
        "synthesis": synthesis,
        "verdict": "changes_required" if blocking else "ready",
        "failure_reason": None,
        "model": model,
        "reasoning_effort": reasoning_effort,
    }
```

Allow an empty synthesis only for a manifest with zero files and zero commits;
normalize it to `"No changes between configured refs."`.

- [ ] **Step 5: Implement private atomic report history**

Implement `ReportStore` with a JSON object
`{"schema_version": 1, "reports": [...]}`. Key reports by
`(base_sha, compare_sha)`, replace matching keys, sort newest first, trim to
`history_limit`, write through `mkstemp`, `fchmod(0600)`, `fsync`, and
`os.replace`, and reject symlinked store paths.

Expose CLI commands:

```text
prepare --repo PATH --base-ref REF --compare-ref REF --manifest PATH
finalize --manifest PATH --result PATH --store PATH --history-limit N
fail --base-ref REF --compare-ref REF --reason TEXT --store PATH --history-limit N
show --store PATH --history-limit N
```

`prepare` atomically writes a private manifest. `finalize` requires the fixed
Sol/xhigh policy, stores either the validated report or an incomplete report,
and prints the stored record as compact JSON. `fail` stores an incomplete report
whose refs are present and whose unavailable SHA fields are `null`; use it when
fetch or ref resolution fails before a manifest exists. `show` prints latest
plus history without mutating the store.

- [ ] **Step 6: Run focused tests**

Run:

```bash
python3 -m unittest tests.test_preprod_review -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add scripts/pitcrew_preprod_review.py tests/test_preprod_review.py
git commit -m "feat: store fail-closed preprod review reports"
```

### Task 3: Register the manual-only Sol/xhigh skill

**Files:**
- Create: `skills/preprod-review-run/SKILL.md`
- Modify: `scripts/pitcrew_models.py`
- Modify: `scripts/pitcrew_config.py`
- Modify: `bin/pitcrew-codex.sh`
- Modify: `profiles/getbill.json`
- Modify: `profiles/generic.json`
- Modify: `references/config.example.json`
- Modify: `tests/test_plugin_contract.py`
- Modify: `tests/test_models.py`
- Modify: `tests/test_config.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_schedule.py`
- Modify: `tests/test_skill_contracts.py`

- [ ] **Step 1: Write failing registration, policy, and absence tests**

Add `preprod-review-run` to the plugin contract and assert:

```python
self.assertEqual("gpt-5.6-sol", DEFAULT_MODELS["preprod-review-run"])
self.assertEqual("xhigh", DEFAULT_REASONING_EFFORTS["preprod-review-run"])
```

Assert the scheduler JSON has no `preprod-review-run` entry and rendered
LaunchAgents contain no filename with `preprod-review-run`.

Add configuration failures for a local ref, identical refs, history limit outside
`1..50`, a non-Sol model, and non-xhigh effort.

- [ ] **Step 2: Run focused tests and confirm failure**

Run:

```bash
python3 -m unittest tests.test_plugin_contract tests.test_models tests.test_config tests.test_schedule -v
```

Expected: the role and configuration do not exist.

- [ ] **Step 3: Register the role without scheduling it**

Add the role to model and effort maps and to the runner `SKILLS` allowlist. Do
not add it to `bin/pitcrew-schedule.py::SCHEDULE`.

Add this profile configuration:

```json
"preprod_review": {
  "base_ref": "origin/preprod",
  "compare_ref": "origin/develop",
  "history_limit": 10
}
```

Add this agent entry:

```json
"preprod-review-run": {
  "model": "gpt-5.6-sol",
  "reasoning_effort": "xhigh"
}
```

In `validate`, use `validate_remote_ref`, require different refs, require an
integer `history_limit` from 1 through 50, and enforce the fixed model/effort
pair for this role.

- [ ] **Step 4: Create the complete skill**

Create `skills/preprod-review-run/SKILL.md`:

```markdown
---
name: preprod-review-run
description: Use when manually reviewing every tracked change not yet merged from the configured development ref into Preprod.
---

Read `references/CODEX-RUNTIME.md`, resolve one configured project, validate its
repository and `preprod_review` configuration, then read applicable `AGENTS.md`
and domain references. This role is read-only and manual-only. Never create
issues, comments, changes, commits, merges, deployments, database actions, or
Preprod environment access. Never read secrets, ignored corpora, untracked
files, or local working-tree modifications.

Require model `gpt-5.6-sol` and reasoning effort `xhigh`; otherwise persist an
incomplete report and stop.

Set:

    MANIFEST=$CONFIG_DIR/state/preprod-review-manifest.json
    RESULT=$CONFIG_DIR/state/preprod-review-result.json
    STORE=$CONFIG_DIR/preprod-review-reports.json

Run the Pitcrew preprod helper `prepare` command with the configured repository,
base ref, compare ref, and manifest. If it fails, call the helper `fail` command
with the configured refs, a bounded redacted reason, store, and history limit,
then stop with status incomplete.

Read the manifest. Review every listed tracked file at the captured SHAs, not
the mutable working tree. Partition a large manifest deterministically by
component and risk. Check regressions, business invariants, authorization,
security, migrations, configuration, background work, frontend/backend
contracts, compatibility, and missing tests. Record every manifest path exactly
once in `reviewed_files`, even for deletions, renames, generated files, or
binaries; review integration impact when textual content is unsuitable.

After all files are covered, perform one cross-change synthesis. Atomically write
RESULT as:

    {
      "reviewed_files": ["every manifest path exactly once"],
      "findings": [{
        "severity": "critical|high|medium|low",
        "title": "specific problem",
        "evidence": ["tracked/file:line"],
        "affected_files": ["tracked/file"],
        "impact": "concrete failure mode",
        "recommendation": "bounded corrective action"
      }],
      "synthesis": "cross-component conclusion"
    }

Run the helper `finalize` command with manifest, result, store, and configured
history limit. Report the stored verdict `ready`, `changes_required`, or
`incomplete`. Never claim ready directly; only the helper may derive it from
complete coverage.
```

- [ ] **Step 5: Add runner and skill-contract tests**

Assert dry-run prints:

```text
model=gpt-5.6-sol
reasoning_effort=xhigh
```

Assert the generated Codex args contain
`model_reasoning_effort="xhigh"`. Require skill markers for the exact three-dot
scope, helper commands, every-file coverage, cross-change synthesis, and
forbidden remote actions.

- [ ] **Step 6: Run focused tests**

Run:

```bash
python3 -m unittest tests.test_plugin_contract tests.test_models tests.test_config tests.test_cli tests.test_schedule tests.test_skill_contracts -v
```

Expected: all tests pass and scheduler absence remains explicit.

- [ ] **Step 7: Commit**

```bash
git add skills/preprod-review-run/SKILL.md scripts/pitcrew_models.py scripts/pitcrew_config.py bin/pitcrew-codex.sh profiles/getbill.json profiles/generic.json references/config.example.json tests/test_plugin_contract.py tests/test_models.py tests/test_config.py tests/test_cli.py tests/test_schedule.py tests/test_skill_contracts.py
git commit -m "feat: add manual preprod review skill"
```

### Task 4: Expose authenticated manual review APIs

**Files:**
- Modify: `scripts/pitcrew_dashboard.py`
- Modify: `bin/pitcrew-dashboard`
- Modify: `tests/test_dashboard.py`

- [ ] **Step 1: Write failing service snapshot and trigger tests**

Construct a service with a stored report and assert
`service.preprod_review_snapshot()` contains:

```python
{
    "skill": "preprod-review-run",
    "base_ref": "origin/preprod",
    "compare_ref": "origin/develop",
    "configured_model": "gpt-5.6-sol",
    "reasoning_effort": "xhigh",
    "running": False,
    "global_state": "running",
    "latest": stored_report,
    "history": [stored_report],
}
```

Mock `subprocess.Popen`, call `trigger_preprod_review`, and assert the command is:

```python
[
    str(RUNNER),
    "preprod-review-run",
    "getbill",
    "--scheduled",
]
```

Assert a live marker, global stop, or non-Sol/xhigh configuration rejects the
trigger. Add a newer interrupted history record above an older successful report
and assert the snapshot returns `"report_stale": True`.

- [ ] **Step 2: Write failing HTTP tests**

Add authenticated tests for:

```text
GET /api/preprod-review
POST /api/actions {"action":"trigger-preprod-review"}
POST /api/actions {"action":"stop-preprod-review"}
```

Require exact request shapes, the session header, `202` for accepted actions,
`403` for stopped/overlapping runs, and `400` for extra fields.

- [ ] **Step 3: Run focused tests and confirm failure**

Run:

```bash
python3 -m unittest tests.test_dashboard -v
```

Expected: service methods and routes are absent.

- [ ] **Step 4: Implement the service boundary**

Import `ReportStore`, `resolve_reasoning_effort`, `signal`, and
`read_state`. Initialize the store from `runtime_dir /
"preprod-review-reports.json"` and configured history limit.

Implement:

```python
def preprod_review_snapshot(self) -> dict:
    config = self.config["preprod_review"]
    reports = self.preprod_reports.list()
    live = self._live_status("preprod-review-run")
    runs = self.history("preprod-review-run", None)
    latest = reports[0] if reports else None
    latest_run = runs[0] if runs else None
    report_stale = bool(
        latest_run
        and (
            latest is None
            or _timestamp(latest_run["finished_at"])
            > _timestamp(latest["completed_at"])
        )
        and latest_run.get("outcome") in {"failed", "interrupted"}
    )
    return {
        "skill": "preprod-review-run",
        "base_ref": config["base_ref"],
        "compare_ref": config["compare_ref"],
        "configured_model": resolve_model(
            self.config, "preprod-review-run"
        ),
        "reasoning_effort": resolve_reasoning_effort(
            self.config, "preprod-review-run"
        ),
        "running": live is not None,
        "live_status": live,
        "global_state": self._global_state(),
        "latest": latest,
        "history": reports,
        "latest_run": latest_run,
        "report_stale": report_stale,
    }
```

Implement trigger under `_control_lock`: verify global state, fixed policy, and
no live marker, then call `_trigger("preprod-review-run")`.

Implement stop under `_control_lock`: require a live marker, read its positive
PID, and send `signal.SIGTERM`. `pitcrew_locked_exec.py` already forwards
SIGTERM to its Codex child and records an interrupted outcome.

When `global_control("stop-all")` succeeds, also terminate the manual review if
its live marker exists. A missing manual run is not an error.

- [ ] **Step 5: Add strict HTTP routing**

Add `GET /api/preprod-review`. In POST routing, accept only the two exact manual
actions before the generic skill/action branch:

```python
if action in {"trigger-preprod-review", "stop-preprod-review"}:
    if set(request) != {"action"}:
        self._send_json(400, {"error": "invalid request"})
        return
    operation = (
        service.trigger_preprod_review
        if action == "trigger-preprod-review"
        else service.stop_preprod_review
    )
    try:
        result = operation()
    except DashboardError:
        self._send_json(403, {"error": "action rejected"})
        return
    self._send_json(202, result)
    return
```

- [ ] **Step 6: Run focused tests**

Run:

```bash
python3 -m unittest tests.test_dashboard -v
```

Expected: all service and HTTP tests pass.

- [ ] **Step 7: Commit**

```bash
git add scripts/pitcrew_dashboard.py bin/pitcrew-dashboard tests/test_dashboard.py
git commit -m "feat: expose manual preprod review api"
```

### Task 5: Add the dedicated dashboard panel

**Files:**
- Modify: `dashboard/index.html`
- Modify: `dashboard/app.js`
- Modify: `dashboard/styles.css`
- Modify: `tests/test_dashboard.py`

- [ ] **Step 1: Write failing asset contract tests**

Require IDs and text:

```python
for marker in (
    'id="preprod-review"',
    'id="preprod-review-state"',
    'id="preprod-review-report"',
    'id="preprod-review-trigger"',
    'id="preprod-review-stop"',
    "Revue avant Preprod",
):
    self.assertIn(marker, index)

for marker in (
    'fetchJson("/api/preprod-review")',
    '"trigger-preprod-review"',
    '"stop-preprod-review"',
    "CORRECTIONS REQUISES",
    "INCOMPLET",
    "PRÊT",
):
    self.assertIn(marker, app)
```

Assert the panel source contains no `change-model`, `restart`, `merge`, or
deployment control tied to `preprod-review-run`.

- [ ] **Step 2: Run the dashboard tests and confirm failure**

Run:

```bash
python3 -m unittest tests.test_dashboard -v
```

Expected: panel markers are absent.

- [ ] **Step 3: Add accessible panel markup**

Insert a separate panel before GitLab work:

```html
<section id="preprod-review" class="panel preprod-review-panel" aria-labelledby="preprod-review-title">
  <div class="section-heading">
    <div>
      <p class="eyebrow">Contrôle manuel</p>
      <h2 id="preprod-review-title">Revue avant Preprod</h2>
    </div>
    <p id="preprod-review-state" class="section-note">Aucun rapport</p>
  </div>
  <p><code>origin/preprod...origin/develop</code> · Sol · raisonnement xhigh</p>
  <div class="preprod-review-actions">
    <button id="preprod-review-trigger" class="button button-primary" type="button">Lancer la revue complète</button>
    <button id="preprod-review-stop" class="button button-danger" type="button" hidden>Arrêter</button>
  </div>
  <div id="preprod-review-report" aria-live="polite"></div>
  <details id="preprod-review-history">
    <summary>Historique des revues</summary>
    <ol id="preprod-review-history-list"></ol>
  </details>
</section>
```

- [ ] **Step 4: Render safe report content**

Fetch `/api/preprod-review` with the other local endpoints. Build every element
with `document.createElement` and assign untrusted content through
`textContent`. Map verdicts:

```javascript
const preprodVerdictLabels = {
  ready: "PRÊT",
  changes_required: "CORRECTIONS REQUISES",
  incomplete: "INCOMPLET",
};
```

Show SHA prefixes, completion date, commit/file counts, reviewed count,
findings grouped critical/high/medium/low, synthesis, and bounded failure reason.
Render each retained history item with its verdict, SHA pair, and completion
date inside `preprod-review-history-list`. When `report_stale` is true, display
`Dernier passage interrompu ou échoué : le rapport précédent est obsolète`
above the old report and never label that old report as the current result.
Disable Trigger when running or globally stopped. Show Stop only when running.

- [ ] **Step 5: Wire manual controls**

On trigger, require:

```javascript
window.confirm(
  "Lancer une revue complète avec Sol en raisonnement xhigh ? Cette analyse peut être longue et coûteuse."
)
```

POST only `{"action":"trigger-preprod-review"}`. Stop posts only
`{"action":"stop-preprod-review"}` after a second confirmation. Refresh local
status immediately without forcing a GitLab refresh.

- [ ] **Step 6: Style verdict and findings states**

Add responsive styles scoped below `.preprod-review-panel`; use existing color
variables and focus styles. Provide visually distinct ready,
changes-required, and incomplete badges without relying on color alone.

- [ ] **Step 7: Run focused tests**

Run:

```bash
python3 -m unittest tests.test_dashboard -v
```

Expected: dashboard service, HTTP, and asset tests pass.

- [ ] **Step 8: Commit**

```bash
git add dashboard/index.html dashboard/app.js dashboard/styles.css tests/test_dashboard.py
git commit -m "feat: add preprod review dashboard panel"
```

### Task 6: Document, harden, and verify the manual workflow

**Files:**
- Modify: `README.md`
- Modify: `references/TOPOLOGY.md`
- Modify: `references/SCHEDULED-TASKS.md`
- Modify: `docs/CODEX.md`
- Modify: `tests/test_docs.py`

- [ ] **Step 1: Write failing documentation tests**

Require:

```python
self.assert_markers(
    "README.md",
    "$pitcrew:preprod-review-run",
    "origin/preprod...origin/develop",
    "manual",
    "gpt-5.6-sol",
    "xhigh",
)
self.assert_markers(
    "references/SCHEDULED-TASKS.md",
    "preprod-review-run",
    "never scheduled",
    "local report",
)
```

- [ ] **Step 2: Run documentation tests and confirm failure**

Run:

```bash
python3 -m unittest tests.test_docs -v
```

Expected: manual review contract is undocumented.

- [ ] **Step 3: Document the operator workflow**

Document that the dashboard button is the supported launch path, the role
reviews remote Git refs rather than the working tree, Sol/xhigh is fixed, no
GitLab or environment mutation occurs, and only helper-validated complete
coverage can yield `ready`. Document the three verdicts and Stop behavior.

- [ ] **Step 4: Run shell and Python validation**

Run:

```bash
bash -n bin/pitcrew-codex.sh
python3 -m py_compile scripts/pitcrew_preprod_review.py scripts/pitcrew_dashboard.py bin/pitcrew-dashboard
python3 -m unittest tests.test_preprod_review tests.test_plugin_contract tests.test_models tests.test_config tests.test_cli tests.test_schedule tests.test_skill_contracts tests.test_dashboard tests.test_docs -v
```

Expected: syntax checks and all selected tests pass.

- [ ] **Step 5: Run the complete deterministic suite**

Run:

```bash
bash tests/run.sh
```

Expected: complete suite passes with no scheduled Preprod review job.

- [ ] **Step 6: Inspect the final diff for forbidden behavior**

Run:

```bash
git diff --check
rg -n "preprod-review-run" bin/pitcrew-schedule.py
rg -n "glab|merge|deploy|preprod environment|database" skills/preprod-review-run/SKILL.md
```

Expected: `git diff --check` is clean; scheduler search has no role entry; skill
matches appear only in explicit prohibitions or review topics, never executable
remote mutations.

- [ ] **Step 7: Commit**

```bash
git add README.md references/TOPOLOGY.md references/SCHEDULED-TASKS.md docs/CODEX.md tests/test_docs.py
git commit -m "docs: describe manual preprod review workflow"
```
