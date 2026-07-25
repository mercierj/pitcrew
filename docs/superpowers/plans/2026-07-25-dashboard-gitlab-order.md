# Dashboard GitLab Order Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Place the complete GitLab work section above the agent cards and activity history.

**Architecture:** Keep the existing GitLab section, IDs, dynamic rendering, and API behavior unchanged. Change only the DOM order in `dashboard/index.html`, placing `#gitlab-work` immediately after `#overview`.

**Tech Stack:** Static HTML dashboard, vanilla JavaScript, CSS, Python test suite.

---

### Task 1: Move the GitLab section in the dashboard DOM

**Files:**
- Modify: `dashboard/index.html`

- [ ] **Step 1: Move the existing `#gitlab-work` section**

  Cut the complete section beginning with `<section id="gitlab-work"` and ending at its closing `</section>`. Paste it immediately after the closing `</section>` for `#overview`, before `<section id="live-agents"`.

  Do not change any descendant IDs or markup inside the GitLab section, including `#merge-requests`, `#merge-request-list`, and `#gitlab-groups`.

- [ ] **Step 2: Verify the DOM order**

  Run:

  ```bash
  rg -n 'id="(overview|gitlab-work|live-agents|agents|activity)"' dashboard/index.html
  ```

  Expected order: `overview`, `gitlab-work`, `live-agents`, `agents`, `activity`.

- [ ] **Step 3: Run dashboard tests**

  Run:

  ```bash
  ./tests/run.sh
  ```

  Expected: the test suite exits with status 0.

- [ ] **Step 4: Commit the implementation**

  ```bash
  git add dashboard/index.html
  git commit -m "fix: move GitLab work above agent cards"
  ```
