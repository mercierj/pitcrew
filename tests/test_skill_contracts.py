import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ReferenceContractTest(unittest.TestCase):
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
            "Reject symlinked runtime, project, default, and config",
            "do not read configuration twice",
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
        )

    def test_github_linear_contract(self):
        self.assert_markers(
            "references/providers/github-linear.md", "gh auth status", "Linear"
        )

    def test_gitlab_contract(self):
        self.assert_markers(
            "references/providers/gitlab.md", "glab auth status", "merge request"
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


if __name__ == "__main__":
    unittest.main()
