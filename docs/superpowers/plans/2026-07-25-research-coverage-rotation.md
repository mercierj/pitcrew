# Research Coverage Rotation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make each repeated `research-run` pass select a different tracked repository area within its chosen cell before starting a new coverage epoch.

**Architecture:** Add a small dependency-free Python module that discovers stable top-level scan areas and reads/writes the per-cell coverage cursor atomically. Update the research skill contract to call the helper, constrain analysis to the selected area, and persist the cursor after each pass. Existing legacy state remains compatible because missing or invalid coverage is rebuilt lazily.

**Tech Stack:** Python 3 standard library, JSON state, Markdown skill contract, unittest.

---

### Task 1: Add coverage cursor behavior tests

**Files:**
- Create: `tests/test_research_coverage.py`
- Test: `scripts/research_coverage.py`

- [x] **Step 1: Write tests for deterministic area discovery and fallback**

Test a temporary repository containing tracked-style top-level directories and ignored/generated directories. Assert relevant areas are sorted, generated areas are excluded, and an empty repository falls back to `.`.

- [x] **Step 2: Write tests for oldest-area selection and epoch rollover**

Assert the first selection is the first sorted area, subsequent selections choose the least recently visited area, and once every area has been visited the next selection starts epoch `+1` at the first area.

- [x] **Step 3: Write tests for legacy and malformed state**

Assert a cell with no `coverage` field and a cell with invalid coverage both receive a valid initialized cursor without changing unrelated state fields.

- [x] **Step 4: Run the focused tests and verify they fail for the missing module**

Run: `python3 -m unittest tests.test_research_coverage -v`

Expected: import failure because `scripts/research_coverage.py` does not exist yet.

### Task 2: Implement the coverage helper

**Files:**
- Create: `scripts/research_coverage.py`
- Test: `tests/test_research_coverage.py`

- [x] **Step 1: Implement deterministic area discovery**

Expose `discover_areas(repo_path)` using Git-tracked paths when available. Keep top-level directories that contain source, test, documentation, or configuration files; exclude `.git`, generated/build/cache/data directories, ignored paths, and hidden directories. Return `['.']` when no area qualifies.

- [x] **Step 2: Implement cursor normalization and selection**

Expose `select_area(cell_state, areas, now)` returning the selected area and normalized coverage. Preserve valid visited timestamps, remove areas no longer present, add new areas as unvisited, and roll the epoch when all current areas have timestamps.

- [x] **Step 3: Implement atomic state persistence**

Expose `update_cell_state(state, cell_key, areas, selected_area, epoch, timestamp)` and a CLI with `discover`, `select`, and `record` subcommands. Write JSON through a same-directory temporary file and `os.replace`.

- [x] **Step 4: Run the focused tests and verify they pass**

Run: `python3 -m unittest tests.test_research_coverage -v`

Expected: all coverage discovery, rotation, migration, and atomic-state tests pass.

### Task 3: Integrate the helper into the research skill contract

**Files:**
- Modify: `skills/research-run/SKILL.md`
- Modify: `tests/test_skill_contracts.py`

- [x] **Step 1: Document the coverage fields and helper commands**

Add the optional coverage schema, legacy migration rules, and the exact helper invocation sequence after cell selection.

- [x] **Step 2: Constrain the scan to the selected area**

Require the agent to print the selected area, inspect it as the primary scope, and include `coverage_area` and `coverage_epoch` in the final summary/history entry.

- [x] **Step 3: Require cursor recording on success and safe skip**

Require recording the selected area after analysis, including when no finding is recorded, while keeping failure/no-op behavior from falsely advancing coverage.

- [x] **Step 4: Add contract assertions**

Assert the skill mentions deterministic areas, legacy-state migration, `discover_areas`, selection, atomic recording, and the selected-area scope.

- [x] **Step 5: Run skill contract tests**

Run: `python3 -m unittest tests.test_skill_contracts -v`

Expected: all skill contract tests pass.

### Task 4: Run the complete verification suite

**Files:**
- No additional files.

- [x] **Step 1: Run the complete test suite**

Run: `./tests/run.sh`

Expected: the complete suite passes without modifying unrelated working-tree changes.

- [x] **Step 2: Inspect the final diff**

Run: `git diff --check` and `git status --short`.

Expected: no whitespace errors; only the coverage helper, its tests, the skill contract, and the implementation plan are new/modified beyond the pre-existing user changes.
