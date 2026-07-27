# Ticket Reconciliation and Security Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate false ticket/MR relationships, reconcile merged work automatically, and fix the three remaining GetBill security findings.

**Architecture:** Pitcrew uses one exact-number reference matcher wherever it derives ticket/MR relationships; merged evidence remains the only authority for lifecycle closure. GetBill controllers and services keep sensitive error/token details in safe server logs while returning constant public responses and rejecting missing webhook configuration.

**Tech Stack:** Python 3 + unittest, Symfony 6.4/PHP 8.3 + PHPUnit, GitLab lifecycle labels.

---

### Task 1: Make ticket-to-change matching exact in Pitcrew

**Files:**
- Modify: `scripts/pitcrew_forge_work.py:281-300`
- Modify: `scripts/pitcrew_dashboard.py:1441-1480`
- Test: `tests/test_forge_work.py`
- Test: `tests/test_dashboard.py`

- [ ] **Step 1: Write failing exact-reference tests**

Add a Forge-work test where issue `#1` and a merged change whose text contains only `Closes #15` produce no relationship. Add the complementary `Closes #1` case and assert that its URL is retained. Add the same regression coverage for `DashboardService._related_merge_requests`.

- [ ] **Step 2: Run the focused tests and confirm RED**

Run: `python3 -m unittest tests.test_forge_work tests.test_dashboard`

Expected: failure showing that `#1` incorrectly receives the MR mentioning `#15`.

- [ ] **Step 3: Introduce an exact numeric ticket-reference matcher**

Replace substring checks such as `f"#{issue_number}" in text` with a matcher equivalent to:

```python
def references_ticket(text: str, issue_number: int) -> bool:
    return re.search(rf"(?<![\\w#])#{issue_number}(?!\\d)", text) is not None
```

Use it for every reverse issue-to-MR relationship in the GitLab and GitHub adapters. Keep exact MR reference matching unchanged.

- [ ] **Step 4: Run focused tests and confirm GREEN**

Run: `python3 -m unittest tests.test_forge_work tests.test_dashboard`

Expected: all selected tests pass, including the new `#1`/`#15` regression.

### Task 2: Admit merged tickets to the reconciliation workflow

**Files:**
- Modify: `scripts/pitcrew_eligibility.py`
- Test: `tests/test_eligibility.py`
- Verify: `skills/stale-sweep/SKILL.md`

- [ ] **Step 1: Write failing eligibility tests**

Add cases proving a directed `stale-sweep` accepts an open configured GitLab issue with `pitcrew-agent` and each configured lifecycle label (`todo`, `processing`, `review`, `blocked`, `done`), while rejecting no-agent, no-lifecycle, closed, or wrong-project targets.

- [ ] **Step 2: Run the focused eligibility tests and confirm RED**

Run: `python3 -m unittest tests.test_eligibility`

Expected: blocked/review/todo candidates fail admission before the reconciliation change.

- [ ] **Step 3: Expand only stale-sweep target validation**

Allow the configured `pitcrew-agent` label plus any configured lifecycle label only when `skill == "stale-sweep"`; retain all existing role eligibility rules and require the sweep itself to re-fetch a GitLab MR with `state=merged` and `merged_at` before closing.

- [ ] **Step 4: Run the focused tests and contracts**

Run: `python3 -m unittest tests.test_eligibility tests.test_run_dispatcher tests.test_skill_contracts`

Expected: all tests pass; no other role gains broader eligibility.

### Task 3: Stop logging AI bearer material

**Files:**
- Modify: `/Users/jo/Prog/getbill/src/Service/AIProviderTokenService.php:84-170`
- Create or modify: `/Users/jo/Prog/getbill/tests/Service/AIProviderTokenServiceTest.php`

- [ ] **Step 1: Write failing service tests**

Use a logger spy to assert that validation failures, expiry, and unexpected validation errors never receive the supplied bearer or any prefix/identifier derived from it. Assert that non-sensitive metadata such as expiry timestamp remains available where useful.

- [ ] **Step 2: Run the focused test and confirm RED**

Run: `php bin/phpunit tests/Service/AIProviderTokenServiceTest.php`

Expected: the captured context contains the supplied token prefix.

- [ ] **Step 3: Remove credential-derived log context**

Remove `jwt_preview`, `token_identifier`, and `token` context values derived from the bearer. Replace validation-path context with safe operational fields only, such as `expired_at` and an error class; keep exception details out of public exceptions.

- [ ] **Step 4: Run the focused service test and confirm GREEN**

Run: `php bin/phpunit tests/Service/AIProviderTokenServiceTest.php`

Expected: all assertions pass and no captured log context contains the bearer or its prefix.

### Task 4: Make the Stripe App webhook fail closed

**Files:**
- Modify: `/Users/jo/Prog/getbill/src/Controller/StripeApp/StripeAppController.php:1972-2005`
- Create or modify: `/Users/jo/Prog/getbill/tests/Controller/StripeApp/StripeAppControllerTest.php`

- [ ] **Step 1: Write failing controller tests**

Construct the controller with `kernel.environment=dev` and an empty `stripe.app_webhook_secret`. Submit a signed-looking webhook request and assert it is rejected; assert that the configuration failure returns the existing failure response and does not invoke processing.

- [ ] **Step 2: Run the focused test and confirm RED**

Run: `php bin/phpunit tests/Controller/StripeApp/StripeAppControllerTest.php`

Expected: dev currently accepts the request because the signature helper returns `true`.

- [ ] **Step 3: Remove the dev bypass**

Make a missing secret return `false` in every environment. Keep the log entry free of secrets and retain Stripe’s normal signature verification for configured secrets.

- [ ] **Step 4: Run the focused controller test and confirm GREEN**

Run: `php bin/phpunit tests/Controller/StripeApp/StripeAppControllerTest.php`

Expected: empty-secret webhook requests are rejected in dev and configured-secret coverage remains green.

### Task 5: Sanitize Bridge API exceptions

**Files:**
- Modify: `/Users/jo/Prog/getbill/src/Controller/ExternalAPI/BridgeCampaignSyncController.php:70-90`
- Modify: `/Users/jo/Prog/getbill/src/Controller/ExternalAPI/BridgePreviewController.php:55-78`
- Modify: `/Users/jo/Prog/getbill/src/Controller/ExternalAPI/BridgeImportRollbackController.php:55-90`
- Test: `/Users/jo/Prog/getbill/tests/Controller/ExternalAPI/BridgeCampaignSyncControllerTest.php`
- Test: `/Users/jo/Prog/getbill/tests/Controller/ExternalAPI/BridgePreviewControllerTest.php`
- Test: `/Users/jo/Prog/getbill/tests/Controller/ExternalAPI/BridgeImportRollbackControllerTest.php`

- [ ] **Step 1: Write failing response tests**

For each controller, configure the downstream service to throw a uniquely identifiable `InvalidArgumentException` or `DomainException`. Assert the current response retains its 400/409 status but its public `message` differs from the exception text. Assert the logger receives the exception message only in server-side context.

- [ ] **Step 2: Run the three focused test files and confirm RED**

Run: `php bin/phpunit tests/Controller/ExternalAPI/BridgeCampaignSyncControllerTest.php tests/Controller/ExternalAPI/BridgePreviewControllerTest.php tests/Controller/ExternalAPI/BridgeImportRollbackControllerTest.php`

Expected: the responses presently contain the sentinel exception messages.

- [ ] **Step 3: Return stable generic messages and log details**

For expected validation exceptions return constant client-safe messages for 400 and 409. Add structured error logs with the exception message and safe resource identifiers. Do not alter authorization, ownership, or the status-code contract.

- [ ] **Step 4: Run the focused Bridge tests and confirm GREEN**

Run: `php bin/phpunit tests/Controller/ExternalAPI/BridgeCampaignSyncControllerTest.php tests/Controller/ExternalAPI/BridgePreviewControllerTest.php tests/Controller/ExternalAPI/BridgeImportRollbackControllerTest.php`

Expected: public responses contain no sentinel exception text, error logs do, and all existing cases pass.

### Task 6: Verify and reconcile the current board

**Files:**
- Verify: `/Users/jo/Prog/pitcrew/tests/test_forge_work.py`
- Verify: `/Users/jo/Prog/pitcrew/tests/test_dashboard.py`
- Verify: `/Users/jo/Prog/getbill` focused PHP tests

- [ ] **Step 1: Run the complete relevant Pitcrew test suite**

Run: `python3 -m unittest tests.test_forge_work tests.test_dashboard tests.test_eligibility tests.test_run_dispatcher tests.test_skill_contracts`

Expected: all tests pass.

- [ ] **Step 2: Run PHP syntax and focused security tests**

Run: `php -l src/Service/AIProviderTokenService.php && php -l src/Controller/StripeApp/StripeAppController.php && php -l src/Controller/ExternalAPI/BridgeCampaignSyncController.php && php -l src/Controller/ExternalAPI/BridgePreviewController.php && php -l src/Controller/ExternalAPI/BridgeImportRollbackController.php`

Then run the four focused PHPUnit targets from Tasks 3–5.

- [ ] **Step 3: Inspect the live board before mutation**

Run: `curl -s http://127.0.0.1:8765/api/forge-work`

Expected: `#16` has a verified merged MR and is eligible for directed `stale-sweep`; `#1`, `#2`, and `#12` have no completed MR and remain open until their fixes are merged.

- [ ] **Step 4: Create, review, and merge the scoped GetBill changes**

Open separate focused MRs for the three ticket fixes, run the configured review/validation gate, and merge only after green verification. Then invoke directed `stale-sweep` for each linked ticket. Do not use the dashboard’s inferred relationship as proof of completion.

- [ ] **Step 5: Refresh the board and record outcome**

Run: `curl -s http://127.0.0.1:8765/api/forge-work`

Expected: `#16` and each successfully merged fix are closed/done; `#1` no longer displays unrelated `!15`; tickets without a merged fix remain visibly blocked with their actual reason.
