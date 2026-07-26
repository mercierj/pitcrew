import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ReferenceContractTest(unittest.TestCase):
    def test_manager_admits_each_new_agent_ticket_to_the_local_dispatcher(self):
        manager = (ROOT / "skills/manager-run/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("ADMIT_NEW_AGENT_TICKET", manager)
        self.assertIn('python3 "$PITCREW_REPO/scripts/pitcrew_run_dispatcher.py" enqueue', manager)
        self.assertIn("--skill implementer-run", manager)
        self.assertIn('--target "<canonical ticket URL>"', manager)
        self.assertIn("created or deduplicated matching", manager)
        self.assertIn("agent-route ticket", manager)
        self.assertIn("Never admit an investigate-route ticket", manager)
        runner = (ROOT / "bin/pitcrew-codex.sh").read_text(encoding="utf-8")
        self.assertIn('export PITCREW_REPO="$REPO_ROOT"', runner)

    def test_manager_architecture_proposal_contract(self):
        self.assert_markers(
            "skills/manager-run/SKILL.md",
            "architecture-proposals-v1",
            'category="architecture"',
            "finding-key is the record's stable `id`",
            "status∈{\"approved\",\"investigate\"}",
            "attach-tracker",
            "before any `state.filed[key]` or history write",
            "fallback architecture source",
            "$CONFIG_DIR/proposals.json",
            'findings_json == "$CONFIG_DIR/proposals.json"',
            '(.proposals.ledger // $fallback) | if . == "$CONFIG_DIR/proposals.json" then $fallback else . end',
        )
    def assert_markers(self, relative_path, *markers):
        contents = (ROOT / relative_path).read_text(encoding="utf-8")
        for marker in markers:
            with self.subTest(path=relative_path, marker=marker):
                self.assertIn(marker, contents)

    def test_codex_runtime_contract(self):
        self.assert_markers(
            "references/CODEX-RUNTIME.md",
            "${CODEX_HOME:-$HOME/.codex}/pitcrew",
            'CODEX_HOME_DIR="${CODEX_HOME:-$HOME/.codex}"',
            'PITCREW_ROOT="$CODEX_HOME_DIR/pitcrew"',
            'CONFIG_FILE="$CONFIG_DIR/config.json"',
            'STATE_DIR="$CONFIG_DIR/state"',
            "resolve and validate the project before repository or provider work",
            "scripts/pitcrew_config.py",
            "repo --project <project>",
            "O_NOFOLLOW",
            "Reject symlinked runtime",
            "through `--add-dir`",
            "does not claim to pin filesystem",
            "one bounded pass",
            "structured no-op",
            "Cadence remains caller-controlled",
        )

    def test_scheduled_task_contract(self):
        self.assert_markers(
            "references/SCHEDULED-TASKS.md",
            "$pitcrew:research-run",
            "$pitcrew:reviewer-run",
            "project getbill",
            "read-only",
            "high-confidence",
            "respect GetBill approval gates",
            "GetBill",
            "release scheduling is disabled",
            "first few scheduled runs",
        )

    def test_provider_contract(self):
        self.assert_markers(
            "references/PROVIDERS.md",
            "providers.forge",
            "providers.tracker",
            '"forge": "github"',
            '"tracker": "linear"',
            "config.json",
            "`none`",
            "Never fall back",
            "workspace, owner, or repository",
            "providers/github.md",
            "forge=github, tracker=github",
        )

    def test_native_github_issue_contract(self):
        self.assert_markers(
            "references/providers/github.md",
            'gh auth status --hostname "$GITHUB_HOST"',
            "providers.tracker",
            "List eligible work",
            "Inspect work",
            "Claim or transition",
            "Create change",
            "Read checks",
            "Merge change",
            "Close lifecycle",
            "pull_request",
            "lookup-before-create",
            "Never fall back",
        )

    def test_shared_change_delivery_contract(self):
        self.assert_markers(
            "references/CHANGE-DELIVERY.md",
            "Bind target before mutation",
            "Expected remote state",
            "Lookup before create",
            "Current head SHA",
            "Reviewer signed off",
            "Validator passed",
            "Human go",
            "Required CI checks",
            "Close lifecycle",
            "Uncertain result",
            "Two fix attempts",
        )

    def test_implementer_uses_shared_delivery_and_excludes_bugs(self):
        implementer = (
            ROOT / "skills/implementer-run/SKILL.md"
        ).read_text(encoding="utf-8")
        self.assertIn("references/CHANGE-DELIVERY.md", implementer)
        self.assertIn(
            "drop every ticket carrying `$BUG_LABEL` before sorting",
            implementer,
        )
        self.assertIn(
            "exclude `$BUG_LABEL` from review continuation",
            implementer,
        )
        self.assertIn(
            "exclude `$BUG_LABEL` from processing recovery",
            implementer,
        )

    def test_bugfixer_is_exclusive_evidence_first_and_human_gated(self):
        self.assert_markers(
            "skills/bugfixer-run/SKILL.md",
            "references/CHANGE-DELIVERY.md",
            'label="$AGENT_LABEL"',
            'state="$STATE_TODO"',
            "$BUG_LABEL",
            "$INVESTIGATE_LABEL",
            "bind-target",
            "valid red reproduction",
            "before modifying production code",
            "same reproduction must turn green",
            "pitcrew:bugfix:red:v1",
            "pitcrew:bugfix:green:v1",
            "pitcrew:bugfix:ready:v1",
            "pitcrew:bugfix:blocked:v1",
            "two fix attempts",
            "reviewer signed off on the current head SHA",
            "validator passed the current head SHA",
            "human `go`",
            "required CI checks are green",
            "CLOSE_LIFECYCLE",
            "structured no-op",
            "scripts/pitcrew_bugfix_lifecycle.py evaluate --snapshot <path>",
            "route_investigate",
            "only `hold_fix` permits the initial claim",
            "open_change",
            "merge_close",
            "bind the canonical ticket before any review continuation mutation",
            "Never attach the green marker to a blocked path",
        )

    def test_unblock_has_safe_sensitive_bug_return_path(self):
        self.assert_markers(
            "skills/unblock/SKILL.md",
            "sensitive-bug",
            "Authorize bounded bugfix",
            "Human pickup",
            "Reject or duplicate",
            "pitcrew:bugfix-sensitive-approved:v1",
            "SENSITIVE_APPROVED_LABEL",
            "remove `$INVESTIGATE_LABEL`",
            "restore `$AGENT_LABEL`",
            "completed investigation findings",
        )

    def test_directed_targets_accept_native_github_issues(self):
        self.assert_markers(
            "references/DIRECTED-TARGET.md",
            "https://github.com/<owner>/<repo>/issues/<number>",
            "GitHub issue",
        )

    def test_github_linear_contract(self):
        self.assert_markers(
            "references/providers/github-linear.md", "gh auth status", "Linear"
        )

    def test_gitlab_contract(self):
        self.assert_markers(
            "references/providers/gitlab.md",
            "glab auth status",
            "merge request",
            '--hostname "$GITLAB_HOST"',
        )

    def test_acting_skills_route_providers_without_fallback(self):
        skill_names = (
            "manager-run",
            "implementer-run",
            "reviewer-run",
            "validator-run",
            "unblock",
            "investigate-run",
            "stale-sweep",
            "ops-run",
            "releaser-run",
        )
        for name in skill_names:
            relative_path = f"skills/{name}/SKILL.md"
            self.assert_markers(
                relative_path,
                "providers.forge",
                "providers.tracker",
                "forge=github, tracker=github",
                "references/providers/github.md",
                "references/providers/github-linear.md",
                "pull-request",
                "references/providers/gitlab.md",
                "merge-request",
                "configured Linear team",
                "`providers.tracker` is `github` or `gitlab`",
                "`providers.tracker` is `none`",
                "structured no-op",
                "list eligible work",
                "claim work",
                "create change",
                "review change",
                "merge change",
                "close lifecycle",
                "abstract capabilities, not shell commands",
                "Never fall back",
                "provider, workspace, owner, project, repository, or environment",
            )
            text = (ROOT / relative_path).read_text(encoding="utf-8")
            for forbidden in (
                "gh pr",
                "glab mr",
                "configured forge operation",
                "CLAUDE.md",
                "the `Skill` tool",
                "github-actions[bot]",
                "GitHub Gist",
                "headRefName",
                "baseRefName",
                "headRefOid",
                "author.login",
            ):
                with self.subTest(skill=name, forbidden=forbidden):
                    self.assertNotIn(forbidden, text)
            self.assertNotRegex(
                text,
                r"configured (?:forge|tracker) [a-z_]+ operation",
                name,
            )
            self.assertNotRegex(text, r"\bPRs?\b", name)

        self.assert_markers(
            "skills/releaser-run/SKILL.md",
            "release.autonomy is `off`",
            "prod or preprod",
            "explicit approval",
            "Do not chain remote",
            "Migrations",
            "database writes",
            "mutating console commands",
            "rollback execution",
        )

    def test_getbill_acting_preflight_and_directed_targets(self):
        for name in ("implementer-run", "validator-run", "stale-sweep", "releaser-run"):
            self.assert_markers(
                f"skills/{name}/SKILL.md",
                "Re-read the repository `AGENTS.md`",
                "Preserve all unrelated working-tree changes",
                "Never create a worktree only because the checkout is dirty",
                "Read the required domain reference",
                "rebuild Graphify",
                "Stage only files changed by this crew item",
            )

        implementer = (ROOT / "skills/implementer-run/SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("git checkout -- .", implementer)
        self.assertIn("EVENTS_FILE=$(mktemp /tmp/implementer-events.XXXXXX)", implementer)
        self.assertNotIn("trap 'rm -f", implementer)

        self.assert_markers(
            "references/DIRECTED-TARGET.md",
            "github.com/<owner>/<repo>/pull/<number>",
            "/-/merge_requests/<number>",
            "linear.app/<workspace>/issue/<id>",
            "/-/issues/<number>",
            "/-/work_items/<number>",
            "<configured-repo>!<number>",
            "<configured-repo>#<number>",
            "Validate the URL host",
            "owner or group",
            "Never fall back",
        )

    def test_coordinated_ticket_mutations_bind_the_run_first(self):
        for relative_path in (
            "references/DIRECTED-TARGET.md",
            "skills/implementer-run/SKILL.md",
            "skills/unblock/SKILL.md",
            "skills/stale-sweep/SKILL.md",
        ):
            self.assert_markers(
                relative_path,
                "bind-target",
                "PITCREW_RUN_ID",
                "tracker mutation",
                "checkout write",
            )

    def test_implementer_has_sandbox_safe_clone_fallback(self):
        implementer = (ROOT / "skills/implementer-run/SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("sandbox-safe isolated clone", implementer)
        self.assertIn("git remote get-url origin", implementer)
        self.assertIn("git clone", implementer)
        self.assertIn(
            "use the configured checkout as the clone's push target", implementer
        )
        self.assertIn("permission denied", implementer)

    def test_implementer_closes_tracker_after_successful_merge(self):
        implementer = (ROOT / "skills/implementer-run/SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("CLOSE_LIFECYCLE <TICKET-id>", implementer)
        self.assertIn("verify the issue is closed", implementer)
        self.assertIn(
            "state_event=close",
            (ROOT / "references/providers/gitlab.md").read_text(encoding="utf-8"),
        )

    def test_implementer_recovers_processing_ticket_with_open_change_to_review(self):
        implementer = (ROOT / "skills/implementer-run/SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("Open change exists", implementer)
        self.assertIn("--state open", implementer)
        self.assertIn("author_identity", implementer)
        self.assertIn("target_branch", implementer)
        self.assertIn('list_runs("$PROJECT", active_only=True)', implementer)
        self.assertIn("active run owns this ticket", implementer)
        self.assertIn('state="$STATE_REVIEW_ID"', implementer)
        self.assertIn("Recovered stale $STATE_PROCESSING state", implementer)

    def test_stale_sweep_repairs_gitlab_merged_lifecycle_drift(self):
        stale = (ROOT / "skills/stale-sweep/SKILL.md").read_text(encoding="utf-8")
        gitlab = (ROOT / "references/providers/gitlab.md").read_text(
            encoding="utf-8"
        )

        for state in (
            "STATE_REVIEW",
            "STATE_PROCESSING",
            "STATE_TODO",
            "STATE_BLOCKED",
            "STATE_DONE",
        ):
            self.assertIn(
                f'LIST_ELIGIBLE_WORK(label="$AGENT_LABEL", state="${state}"',
                stale,
            )

        self.assertIn("built-in lifecycle is open", stale)
        self.assertIn("state=merged", stale)
        self.assertIn("state=closed", stale)
        self.assertIn(
            """LIST_ELIGIBLE_CHANGES \\
  --search "<TICKET-id>" \\
  --repo "$FORGE_OWNER/<repo-name>" \\
  --state merged \\""",
            stale,
        )
        self.assertIn("`state=merged` and non-null `merged_at`", stale)
        self.assertIn("`closed_at` may be null", stale)
        self.assertNotIn("non-null `closed_at`", stale)
        self.assertNotIn("both merged AND closed", stale)
        self.assertIn(
            'INSPECT_CHANGE <change-id> --json state,merged_at,closed_at',
            stale,
        )
        self.assertIn("Immediately before `CLOSE_LIFECYCLE`", stale)
        self.assertRegex(
            stale,
            r"re-confirm `state=merged` and\s+non-null `merged_at`",
        )
        self.assertIn("CLOSE_LIFECYCLE", stale)
        self.assertIn("pitcrew:stale-sweep:done:", stale)
        self.assertIn("verify both", stale)
        self.assertIn("built-in issue state is closed", stale)
        self.assertIn("`$STATE_DONE` label is present", stale)

        self.assertIn("GitLab merge request states are mutually distinct", gitlab)
        self.assertIn("`state=merged`", gitlab)
        self.assertIn("`closed_at` is normally null", gitlab)
        self.assertIn("query `state=merged` and `state=closed` separately", gitlab)

    def test_secondary_roles_are_provider_neutral_and_getbill_safe(self):
        secondary = (
            "architecture-run",
            "research-run",
            "qa-run",
            "coverage-run",
            "dev-verify-run",
        )
        for name in secondary:
            relative_path = f"skills/{name}/SKILL.md"
            self.assert_markers(
                relative_path,
                "### GetBill preflight",
                "`AGENTS.md`",
            )
            text = (ROOT / relative_path).read_text(encoding="utf-8")
            for forbidden in (
                "CLAUDE.md",
                "gh api",
                "gh pr",
                "glab mr",
                "mcp__linear",
                "Linear API",
                "git checkout -- .",
            ):
                with self.subTest(skill=name, forbidden=forbidden):
                    self.assertNotIn(forbidden, text)
            self.assertNotRegex(text, r"\bPRs?\b", name)
            if name == "dev-verify-run":
                self.assertNotIn("source the dev `.env`", text)
                self.assertIn(
                    "Never read, display, or source a secret file",
                    text,
                )
            if name == "research-run":
                self.assertIn(
                    "never create a worktree merely because the configured checkout is dirty",
                    text,
                )
                self.assertIn(
                    "exclude modified or untracked",
                    text,
                )
                self.assertIn(
                    "files from findings",
                    text,
                )
                self.assertIn("graphify-out/converted", text)
                self.assertIn("Never print or quote discovered PII", text)
                self.assertIn("research_coverage.py", text)
                self.assertIn("Coverage helper", text)
                self.assertIn("STEP 1.6. Pick the coverage area", text)
                self.assertIn("least recently visited area", text)
                self.assertIn("starts a new epoch", text)
                self.assertIn("must not\nadvance `visited`", text)
                self.assertIn("coverage_area", text)
                self.assertIn("fingerprints", text)
                self.assertIn("Git tree fingerprint", text)
            if name == "architecture-run":
                self.assertIn(
                    "A dirty or missing optional reference must not abort the pass",
                    text,
                )
                for marker in (
                    "architecture-state.json",
                    "architecture:$REPO_NAME",
                    "research_coverage.py",
                    "responsabilités mélangées",
                    "couplage framework/persistence",
                    "direction/cycles dépendances",
                    "frontières dupliquées",
                    "interfaces fuyantes",
                    "abstraction manquante prouvée",
                    "confidence >= 80%",
                    "maximum 3",
                    '"source": "architecture-run"',
                    '"category": "architecture"',
                    "architecture_category",
                    '"status": "suggested"',
                    "stable id",
                    "No code, ticket, or remote action",
                ):
                    with self.subTest(marker=marker):
                        self.assertIn(marker, text)
                for forbidden in (
                    "hygiene",
                    "hardening",
                    "security finding",
                    "documentation drift",
                    "test gap",
                ):
                    with self.subTest(forbidden=forbidden):
                        self.assertNotIn(forbidden, text)

        for name in ("research-run", "qa-run"):
            self.assert_markers(
                f"skills/{name}/SKILL.md",
                "No tracker dependency",
                "findings ledger",
            )

        for name in ("coverage-run", "dev-verify-run"):
            self.assert_markers(
                f"skills/{name}/SKILL.md",
                "### Provider dispatch — fail closed",
                "providers.forge",
                "references/providers/github-linear.md",
                "pull-request",
                "references/providers/gitlab.md",
                "merge-request",
                "abstract capabilities, not shell commands",
                "Never fall back",
                "provider, workspace, owner, project, repository, or environment",
            )

    def test_getbill_profile_contract(self):
        self.assert_markers(
            "references/profiles/getbill.md",
            "AGENTS.md",
            "/Users/jo/Prog/getbill",
            "authoritative live policy",
            "prod",
            "preprod",
            "Graphify",
            "RTK",
            "migrations, fixtures, schema mutations, raw SQL writes, and mutating `app:*`",
            "release autonomy off",
            "GitLab provider",
            "destructive git operations such as reset, restore, clean, stash, or revert",
            "CloudWatch, and SSM",
            "before **every** action against `prod` or `preprod`",
            ".env.local",
            "AWS credentials/configuration",
        )

    def test_investigation_route_does_not_require_implementer_label(self):
        investigate = (ROOT / "skills/investigate-run/SKILL.md").read_text(
            encoding="utf-8"
        )
        manager = (ROOT / "skills/manager-run/SKILL.md").read_text(encoding="utf-8")

        self.assertIn("investigate-labeled tickets", investigate)
        self.assertIn("`$INVESTIGATE_LABEL` is the routing gate", investigate)
        self.assertNotIn("must have `$AGENT_LABEL`", investigate)
        self.assertIn("NOT `$AGENT_LABEL`", manager)

    def test_all_skills_are_codex_native(self):
        forbidden = (
            "~/.claude",
            "AskUserQuestion",
            "ScheduleWakeup",
            "mcp__claude",
            "/loop",
            "~/.codex/prompts",
        )
        for skill_file in sorted((ROOT / "skills").glob("*/SKILL.md")):
            text = skill_file.read_text(encoding="utf-8")
            frontmatter = text.split("---", 2)[1]
            self.assertIn("description: Use when", frontmatter, skill_file)
            self.assertIn("references/CODEX-RUNTIME.md", text, skill_file)
            self.assertIn("references/PROVIDERS.md", text, skill_file)
            for token in forbidden:
                self.assertNotIn(token, text, f"{skill_file} contains {token}")

    def test_preprod_review_skill_is_manual_read_only_and_fail_closed(self):
        text = (ROOT / "skills/preprod-review-run/SKILL.md").read_text(encoding="utf-8")
        for marker in (
            "manual-only", "gpt-5.6-sol", "xhigh", "MANIFEST", "RESULT", "STORE",
            "prepare", "finalize", "origin/preprod", "origin/develop", "base_sha...compare_sha",
            "reviewed_files", "findings", "synthesis", "ready", "changes_required", "incomplete",
        ):
            self.assertIn(marker, text)
        for forbidden in ("issues", "comments", "tracker", "branches", "commits", "merge", "deploy", "database", "Preprod", "secrets", "working-tree"):
            self.assertIn(forbidden, text)

    def test_cross_skill_invocations_are_namespaced(self):
        skill_names = [
            path.parent.name for path in (ROOT / "skills").glob("*/SKILL.md")
        ]
        for skill_file in sorted((ROOT / "skills").glob("*/SKILL.md")):
            text = skill_file.read_text(encoding="utf-8")
            for name in skill_names:
                self.assertNotIn(f"/{name}", text, skill_file)

    def test_unattended_unblock_and_external_scheduling_contract(self):
        unblock = (ROOT / "skills/unblock/SKILL.md").read_text(encoding="utf-8")
        for marker in (
            "unattended `codex exec`",
            "Do not continue to STEP 7",
            '"status": "blocked"',
            '"question": "<exact selected question>"',
            '"choices":',
            "Atomically persist",
            "resume at STEP 7",
            "question: $question",
            "choices: $choices",
            "context: $context",
            "status: \"selecting\"",
            "pending_question.status=answered",
            "answer must match one of the stored",
            "skip STEP 6",
            "continue at STEP 7",
        ):
            self.assertIn(marker, unblock)

        forbidden_pacing = (
            ".loop.",
            "FAST_WAKEUP",
            "SLOW_HEARTBEAT",
            "SELF-PACING",
            "self-pace",
            "Heartbeat-only",
        )
        for skill_file in sorted((ROOT / "skills").glob("*/SKILL.md")):
            text = skill_file.read_text(encoding="utf-8")
            for marker in forbidden_pacing:
                self.assertNotIn(marker, text, f"{skill_file} contains {marker}")

    def test_unblock_surfaces_tickets_missing_a_bail_comment(self):
        unblock = (ROOT / "skills/unblock/SKILL.md").read_text(encoding="utf-8")
        for marker in (
            "missing-bail-context",
            "without a qualifying agent bail comment",
            "What should happen next?",
            "Send back to agent-todo with context",
            "Keep blocked",
            '"Investigate first — file a sibling research ticket" → `investigate-sibling`',
            '"Send back to agent-todo with context" → `send-back-to-agent-todo`',
            '"Close as won\'t-do" → `close-wontfix`',
            '"Keep blocked" → `keep-deferred`',
            "require non-empty notes",
            "a tracker comment or change its state before",
        ):
            self.assertIn(marker, unblock)

        self.assertNotIn("NEVER touch tickets that don't have a bail comment", unblock)


if __name__ == "__main__":
    unittest.main()
