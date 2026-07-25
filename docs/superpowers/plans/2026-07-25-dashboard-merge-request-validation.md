# Dashboard Merge Request Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show open GitLab merge requests with source/target branches in the local dashboard and allow a confirmed merge followed by source-branch deletion.

**Architecture:** Extend the existing `DashboardService.gitlab_work()` payload with only open merge requests and normalized display fields. Add one authenticated dashboard action that re-fetches and validates the MR server-side, merges it, then deletes the returned source branch. Render the MRs in the existing GitLab area with safe DOM APIs, confirmation, busy state, and forced refresh.

**Tech Stack:** Python 3 standard library, `glab api`, `unittest`, vanilla JavaScript, HTML/CSS.

---

## File map

- Modify `scripts/pitcrew_dashboard.py`: GitLab open-MR query, normalization, merge/delete service operation, cache invalidation, and deployment-branch guard.
- Modify `bin/pitcrew-dashboard`: authenticated JSON routing and request-shape validation for the new action.
- Modify `dashboard/index.html`: add the MR validation container inside the GitLab section.
- Modify `dashboard/app.js`: render MR cards and submit the confirmed merge action.
- Modify `dashboard/styles.css`: compact responsive MR card/action styles.
- Modify `tests/test_dashboard.py`: service, HTTP, asset-contract, and JavaScript contract coverage.
- Modify `README.md`: document the local MR action and its safety boundary.

### Task 1: Lock down open-MR data and branch display with failing tests

**Files:**
- Modify: `tests/test_dashboard.py`
- Modify: `scripts/pitcrew_dashboard.py`

- [ ] **Step 1: Add a fixture MR with branch and pipeline fields.**

Extend `gitlab_merge_requests()` with `source_branch`, `target_branch`, `author.username`, and `head_pipeline.status`, and add a closed MR fixture. Keep the existing two fixtures so related-ticket behavior remains covered.

- [ ] **Step 2: Write the failing service test.**

Add `test_gitlab_work_lists_only_open_merge_requests_with_display_fields()` asserting that the merge-request API path contains `state=opened`, that the closed fixture is excluded, and that each returned MR exposes `iid`, `title`, `web_url`, `source_branch`, `target_branch`, `author_username`, and `pipeline_status`.

- [ ] **Step 3: Run the focused test and verify failure.**

Run:

```bash
python3 -m unittest tests.test_dashboard.DashboardServiceTest.test_gitlab_work_lists_only_open_merge_requests_with_display_fields
```

Expected: FAIL because the current query uses `scope=all` and returns raw merge-request fields.

- [ ] **Step 4: Implement the smallest normalization change.**

Change the merge-request collection path to `state=opened&per_page=100`. Add a private normalizer that reads `author.username` and `head_pipeline.status`, returning `None` when absent, and include normalized dictionaries in `work["merge_requests"]`. Use the normalized list for related-MR URL matching while preserving the raw URL/reference/description values needed by `_related_merge_requests()`.

- [ ] **Step 5: Run the focused and existing GitLab tests.**

Run:

```bash
python3 -m unittest tests.test_dashboard.DashboardServiceTest.test_gitlab_work_lists_only_open_merge_requests_with_display_fields tests.test_dashboard.DashboardServiceTest.test_gitlab_groups_work_and_extracts_related_merge_requests tests.test_dashboard.DashboardServiceTest.test_gitlab_collects_all_issue_and_merge_request_pages
```

Expected: PASS. Update existing path assertions from `scope=all` to `state=opened` where the behavior intentionally changed.

- [ ] **Step 6: Commit the data contract.**

```bash
git add scripts/pitcrew_dashboard.py tests/test_dashboard.py
git commit -m "feat: expose open merge request branches"
```

### Task 2: Add the guarded merge-and-delete service operation

**Files:**
- Modify: `tests/test_dashboard.py`
- Modify: `scripts/pitcrew_dashboard.py`

- [ ] **Step 1: Add a command-runner test for the successful sequence.**

Add `test_merge_merge_request_merges_then_deletes_source_branch()` using a runner that returns one open MR for `projects/<encoded>/merge_requests/11`, then accepts `PUT` to `/merge` and `DELETE` to `/repository/branches/<encoded-source>`. Assert the exact order: re-fetch MR, `PUT merge`, then `DELETE branch`, and assert `{"accepted": True, "partial": False, ...}`.

- [ ] **Step 2: Add failure and deployment-branch tests.**

Add tests asserting that a failed merge raises `DashboardError` and emits no branch deletion, that a closed re-fetch raises before mutation, and that `target_branch` values `preprod` and `prod` are rejected before the merge call.

- [ ] **Step 3: Implement the service operation.**

Add `DashboardService.merge_merge_request(iid: int) -> dict` under the existing control lock. Validate a positive integer IID, fetch `projects/{encoded_project}/merge_requests/{iid}`, require `state == "opened"`, require a string source branch, and reject target branches in `{"preprod", "prod"}`. Call `glab api ... -X PUT .../merge` without a pipeline-success flag, then call `glab api ... -X DELETE projects/{encoded}/repository/branches/{quote(source_branch, safe="")}`. Invalidate `_gitlab_cache`, `_gitlab_cached_at`, and return `partial=True` only when merge succeeded but branch deletion failed. Reuse redacted `DashboardError` handling and never retry a merge automatically.

- [ ] **Step 4: Run service tests.**

Run:

```bash
python3 -m unittest tests.test_dashboard.DashboardServiceTest.test_merge_merge_request_merges_then_deletes_source_branch tests.test_dashboard.DashboardServiceTest.test_merge_merge_request_does_not_delete_after_failed_merge tests.test_dashboard.DashboardServiceTest.test_merge_merge_request_rejects_deployment_targets
```

Expected: PASS.

- [ ] **Step 5: Commit the mutation boundary.**

```bash
git add scripts/pitcrew_dashboard.py tests/test_dashboard.py
git commit -m "feat: merge dashboard merge requests safely"
```

### Task 3: Expose the action through the authenticated HTTP API

**Files:**
- Modify: `bin/pitcrew-dashboard`
- Modify: `tests/test_dashboard.py`

- [ ] **Step 1: Add HTTP tests before routing.**

Add `FakeDashboardService.merge_merge_request()` recording `("merge_merge_request", iid)` and returning the accepted payload. Add tests for: missing session returns `403` with no service call; exact valid `{ "action": "merge-merge-request", "iid": 11 }` returns `202`; extra fields, non-integer IID, and boolean IID return `400`; a service rejection returns `403`.

- [ ] **Step 2: Run the new HTTP tests and verify failure.**

Run:

```bash
python3 -m unittest tests.test_dashboard.DashboardHttpTest.test_post_merge_merge_request_requires_session_and_validates_request
```

Expected: FAIL because the handler does not recognize the action.

- [ ] **Step 3: Implement exact request routing.**

In `do_POST()`, add an `action == "merge-merge-request"` branch requiring exactly `{"action", "iid"}`, an integer IID that is not `bool` and is greater than zero, then call `service.merge_merge_request(iid)`. Preserve the existing `202`, `403`, and `500` error mapping used by other actions.

- [ ] **Step 4: Run the full HTTP test group.**

Run:

```bash
python3 -m unittest tests.test_dashboard.DashboardHttpTest tests.test_dashboard.DashboardRealAssetsHttpTest
```

Expected: PASS.

- [ ] **Step 5: Commit the API boundary.**

```bash
git add bin/pitcrew-dashboard tests/test_dashboard.py
git commit -m "feat: expose merge request dashboard action"
```

### Task 4: Render and confirm merge requests in the dashboard

**Files:**
- Modify: `dashboard/index.html`
- Modify: `dashboard/app.js`
- Modify: `dashboard/styles.css`
- Modify: `tests/test_dashboard.py`

- [ ] **Step 1: Add frontend contract assertions.**

Extend the asset tests to require a dedicated MR container, `source_branch`, `target_branch`, the visible `Fusionner et supprimer la branche` label, `window.confirm`, the `merge-merge-request` action, and a forced GitLab refresh after success. Assert that `innerHTML` is still absent.

- [ ] **Step 2: Add the semantic container and styles.**

Add `#merge-requests` with an accessible heading and live region in `dashboard/index.html`. Add responsive `.merge-request-card`, `.merge-request-branches`, `.merge-request-meta`, and `.merge-request-actions` rules in `dashboard/styles.css`; use existing button variables and keep the action visually destructive.

- [ ] **Step 3: Implement safe MR rendering.**

Add `elements.mergeRequests` and a `renderMergeRequests(work)` function. Render an empty state when there are no open MRs; otherwise render title/link, IID, author, pipeline status (`Absent` when null), and `source_branch → target_branch` using `textContent`. Add the merge button with `data-iid` and disable it while `mergeSubmitting` is true.

- [ ] **Step 4: Implement confirmed submission.**

Add `mergeSubmitting` and `mergeMergeRequest(iid, title)`. Confirm with a message naming the MR and source branch, POST the session-authenticated JSON action, announce success or partial deletion through `operationalStatus`, then call `refresh({ manual: true })`. Always clear the busy state in `finally`.

- [ ] **Step 5: Wire rendering into refresh.**

Call `renderMergeRequests(work)` after the GitLab response is received, including the degraded fallback. Keep the existing 60-second polling behavior and use the manual refresh path after a mutation.

- [ ] **Step 6: Run asset contract tests.**

Run:

```bash
python3 -m unittest tests.test_dashboard.DashboardAssetContractTest tests.test_dashboard.DashboardRealAssetsHttpTest
```

Expected: PASS.

- [ ] **Step 7: Commit the UI.**

```bash
git add dashboard/index.html dashboard/app.js dashboard/styles.css tests/test_dashboard.py
git commit -m "feat: add merge request validation controls"
```

### Task 5: Document and verify the complete flow

**Files:**
- Modify: `README.md`
- Modify: `tests/test_dashboard.py`

- [ ] **Step 1: Document the user-visible behavior.**

In the dashboard section of `README.md`, state that open GitLab MRs show source and target branches, that the local action merges even with a non-green pipeline, that it deletes the source branch after successful merge, and that `preprod`/`prod` targets remain blocked.

- [ ] **Step 2: Add an end-to-end service/API regression test.**

Compose one test that loads the real fixture MR, posts the authenticated action through the test server, verifies the service call receives IID `11`, and verifies the fake runner records merge before branch deletion. Assert that the response payload does not contain the session token.

- [ ] **Step 3: Run the complete dashboard suite and syntax checks.**

Run:

```bash
python3 -m unittest tests.test_dashboard
python3 -m py_compile scripts/pitcrew_dashboard.py bin/pitcrew-dashboard
```

Expected: all dashboard tests pass and `py_compile` exits with status 0.

- [ ] **Step 4: Review the final diff and working tree.**

Run:

```bash
git diff --check HEAD~4..HEAD
git status --short
```

Expected: no whitespace errors; only the feature files and their commits are changed beyond pre-existing user modifications.

- [ ] **Step 5: Commit documentation and final verification.**

```bash
git add README.md tests/test_dashboard.py
git commit -m "docs: explain dashboard merge request validation"
```

## Self-review

- Open-only listing, branch display, author/pipeline metadata, and related-ticket compatibility are covered by Task 1.
- Direct merge, non-green pipeline allowance, source-branch deletion, partial deletion failure, and deployment-target guard are covered by Task 2.
- Session authentication and strict request validation are covered by Task 3.
- Confirmation, accessible status feedback, safe rendering, and refresh/removal from the open list are covered by Task 4.
- Degraded GitLab behavior and user documentation are retained and checked by Task 5.
- No `TODO`, `TBD`, or unspecified implementation placeholders remain.
