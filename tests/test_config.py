import fcntl
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock
from pathlib import Path

from scripts.pitcrew_config import (
    ConfigError,
    DIRECTORY_FLAGS,
    _lock_runtime_config,
    _write_exclusive_config,
    load_runtime_config,
    load_profile,
    max_concurrent_for,
    migrate_legacy,
    runtime_root,
    update_runtime_model,
    validate,
    write_project,
)
from scripts.pitcrew_models import (
    DEFAULT_MODELS,
    DEFAULT_REASONING_EFFORTS,
    resolve_routing_mode,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/pitcrew_config.py"


class ConfigTest(unittest.TestCase):
    def test_profiles_configure_architecture_manager_source(self):
        getbill = json.loads((ROOT / "profiles/getbill.json").read_text(encoding="utf-8"))
        source = next(item for item in getbill["manager"]["sources"] if item["name"] == "architecture")
        self.assertEqual("architecture-proposals-v1", source["format"])
        self.assertEqual("pitcrew-source::architecture", source["label"])
        self.assertEqual(2, source["target_depth"])
        self.assertEqual(1, source["investigate_wip"])
        generic = json.loads((ROOT / "profiles/generic.json").read_text(encoding="utf-8"))
        self.assertEqual("$CONFIG_DIR/proposals.json", generic["proposals"]["ledger"])
        self.assertEqual("architecture-proposals-v1", generic["manager"]["sources"][0]["format"])
    def test_profiles_and_example_pin_the_complete_default_agent_mapping(self):
        for relative in (
            "profiles/getbill.json",
            "profiles/generic.json",
            "references/config.example.json",
        ):
            profile = json.loads((ROOT / relative).read_text(encoding="utf-8"))
            self.assertEqual(
                set(DEFAULT_MODELS),
                set(profile["agents"]),
                relative,
            )
            for role, model in DEFAULT_MODELS.items():
                with self.subTest(profile=relative, role=role):
                    self.assertEqual(model, profile["agents"][role]["model"])
                    self.assertEqual(
                        DEFAULT_REASONING_EFFORTS[role],
                        profile["agents"][role]["reasoning_effort"],
                    )
                    self.assertEqual("observe", profile["agents"][role]["routing_mode"])
            self.assertEqual(
                {"base_ref": "origin/preprod", "compare_ref": "origin/develop", "history_limit": 10},
                profile["preprod_review"],
            )
            validate(profile)

    def test_preprod_review_config_rejects_invalid_refs_history_and_fixed_agent_overrides(self):
        profile = json.loads((ROOT / "profiles/generic.json").read_text(encoding="utf-8"))
        for update, message in (
            ({"base_ref": "preprod"}, "preprod_review.base_ref"),
            ({"compare_ref": "origin/preprod"}, "preprod_review refs must differ"),
            ({"history_limit": 0}, "preprod_review.history_limit"),
            ({"history_limit": 51}, "preprod_review.history_limit"),
            ({"history_limit": True}, "preprod_review.history_limit"),
        ):
            with self.subTest(update=update):
                candidate = json.loads(json.dumps(profile))
                candidate["preprod_review"].update(update)
                with self.assertRaisesRegex(ConfigError, message):
                    validate(candidate)
        for field, value in (
            ("model", "gpt-5.6-terra"),
            ("reasoning_effort", "high"),
            ("routing_mode", "fixed"),
        ):
            with self.subTest(field=field):
                candidate = json.loads(json.dumps(profile))
                candidate["agents"]["preprod-review-run"][field] = value
                with self.assertRaisesRegex(ConfigError, f"agents.preprod-review-run.{field}"):
                    validate(candidate)

    def test_legacy_preprod_review_agent_defaults_routing_mode_to_observe(self):
        profile = json.loads((ROOT / "profiles/generic.json").read_text(encoding="utf-8"))
        del profile["agents"]["preprod-review-run"]["routing_mode"]

        validate(profile)
        self.assertEqual("observe", resolve_routing_mode(profile, "preprod-review-run"))

    def test_update_runtime_model_replaces_only_requested_agent_and_preserves_content(self):
        with tempfile.TemporaryDirectory() as temp:
            env = {"CODEX_HOME": str(Path(temp).resolve())}
            destination = write_project(ROOT / "profiles/getbill.json", "getbill", env)
            original = json.loads(destination.read_text(encoding="utf-8"))
            original["agents"]["research-run"].update(
                {"reasoning_effort": "high", "routing_mode": "fixed"}
            )
            destination.write_text(json.dumps(original, indent=2) + "\n", encoding="utf-8")

            update_runtime_model("getbill", "research-run", "gpt-5.6-luna", env)

            updated = json.loads(destination.read_text(encoding="utf-8"))
            original["agents"]["research-run"]["model"] = "gpt-5.6-luna"
            self.assertEqual(original, updated)

    def test_update_runtime_model_writes_private_config(self):
        with tempfile.TemporaryDirectory() as temp:
            env = {"CODEX_HOME": str(Path(temp).resolve())}
            destination = write_project(ROOT / "profiles/generic.json", "example", env)
            destination.chmod(0o644)

            update_runtime_model("example", "research-run", "gpt-5.6-luna", env)

            self.assertEqual(0o600, destination.stat().st_mode & 0o777)

    def test_update_runtime_model_rejects_unknown_skill_or_model_without_changing_config(self):
        with tempfile.TemporaryDirectory() as temp:
            env = {"CODEX_HOME": str(Path(temp).resolve())}
            destination = write_project(ROOT / "profiles/generic.json", "example", env)
            original = destination.read_text(encoding="utf-8")

            with self.assertRaisesRegex(ConfigError, "skill"):
                update_runtime_model("example", "unknown-run", "gpt-5.6-luna", env)
            with self.assertRaisesRegex(ConfigError, "model"):
                update_runtime_model("example", "research-run", "invented", env)

            self.assertEqual(original, destination.read_text(encoding="utf-8"))

    def test_update_runtime_model_rejects_symlinked_config(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "pitcrew/example"
            project.mkdir(parents=True)
            target = root / "outside.json"
            target.write_text("unchanged", encoding="utf-8")
            (project / "config.json").symlink_to(target)

            with self.assertRaisesRegex(ConfigError, "symlink"):
                update_runtime_model(
                    "example", "research-run", "gpt-5.6-luna", {"CODEX_HOME": temp}
                )
            self.assertEqual("unchanged", target.read_text(encoding="utf-8"))

    def test_update_runtime_model_keeps_existing_config_and_cleans_temp_when_replace_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            env = {"CODEX_HOME": str(Path(temp).resolve())}
            destination = write_project(ROOT / "profiles/generic.json", "example", env)
            original = destination.read_text(encoding="utf-8")

            with mock.patch("scripts.pitcrew_config.os.replace", side_effect=OSError("nope")):
                with self.assertRaisesRegex(OSError, "nope"):
                    update_runtime_model("example", "research-run", "gpt-5.6-luna", env)

            self.assertEqual(original, destination.read_text(encoding="utf-8"))
            self.assertEqual([], list(destination.parent.glob(".config-*")))

    def test_update_runtime_model_serializes_concurrent_role_updates(self):
        with tempfile.TemporaryDirectory() as temp:
            env = {"CODEX_HOME": str(Path(temp).resolve())}
            destination = write_project(ROOT / "profiles/generic.json", "example", env)
            first_write_entered = threading.Event()
            second_attempted = threading.Event()
            second_acquired = threading.Event()
            release_first_write = threading.Event()
            write_count = 0
            write_count_lock = threading.Lock()
            original_replace = __import__(
                "scripts.pitcrew_config", fromlist=["_replace_runtime_config"]
            )._replace_runtime_config
            original_flock = __import__("scripts.pitcrew_config", fromlist=["fcntl"]).fcntl.flock

            def observe_flock(lock_fd, operation):
                is_second_acquisition = (
                    threading.current_thread().name == "second-model-update"
                    and operation == fcntl.LOCK_EX
                )
                if is_second_acquisition:
                    second_attempted.set()
                original_flock(lock_fd, operation)
                if is_second_acquisition:
                    second_acquired.set()

            def delay_first_write(parent_fd, serialized):
                nonlocal write_count
                with write_count_lock:
                    write_count += 1
                    position = write_count
                if position == 1:
                    first_write_entered.set()
                    self.assertTrue(
                        release_first_write.wait(timeout=2),
                        "first update was not released",
                    )
                original_replace(parent_fd, serialized)

            errors = []

            def update(skill, model):
                try:
                    update_runtime_model("example", skill, model, env)
                except Exception as error:  # pragma: no cover - asserted below
                    errors.append(error)

            with (
                mock.patch("scripts.pitcrew_config._replace_runtime_config", delay_first_write),
                mock.patch("scripts.pitcrew_config.fcntl.flock", observe_flock),
            ):
                first = threading.Thread(
                    target=update, args=("research-run", "gpt-5.6-luna")
                )
                first.start()
                self.assertTrue(first_write_entered.wait(timeout=2), "first update did not start")
                second = threading.Thread(
                    target=update,
                    args=("qa-run", "gpt-5.6-sol"),
                    name="second-model-update",
                )
                second.start()
                self.assertTrue(
                    second_attempted.wait(timeout=2),
                    "second update did not attempt to acquire the runtime config lock",
                )
                self.assertFalse(
                    second_acquired.is_set(),
                    "second update acquired the runtime config lock while the first held it",
                )
                release_first_write.set()
                self.assertTrue(
                    second_acquired.wait(timeout=2),
                    "second update did not acquire the runtime config lock after release",
                )
                first.join(timeout=2)
                second.join(timeout=2)

            self.assertFalse(first.is_alive(), "first update thread did not terminate")
            self.assertFalse(second.is_alive(), "second update thread did not terminate")
            self.assertEqual([], errors, "concurrent update raised an exception")
            agents = json.loads(destination.read_text(encoding="utf-8"))["agents"]
            self.assertEqual(
                {
                    "model": "gpt-5.6-luna",
                    "reasoning_effort": "medium",
                    "routing_mode": "observe",
                },
                agents["research-run"],
            )
            self.assertEqual(
                {
                    "model": "gpt-5.6-sol",
                    "reasoning_effort": "medium",
                    "routing_mode": "observe",
                },
                agents["qa-run"],
            )

    def test_runtime_config_lock_closes_descriptor_when_setup_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            directory_fd = os.open(Path(temp), DIRECTORY_FLAGS)
            try:
                for target in (
                    "scripts.pitcrew_config.os.fchmod",
                    "scripts.pitcrew_config.fcntl.flock",
                ):
                    with self.subTest(target=target):
                        failed_lock_fds = []

                        def fail_setup(lock_fd, *_):
                            failed_lock_fds.append(lock_fd)
                            raise OSError("lock setup failed")

                        with (
                            mock.patch(target, side_effect=fail_setup),
                            mock.patch("scripts.pitcrew_config.os.close", wraps=os.close) as close,
                        ):
                            with self.assertRaisesRegex(ConfigError, "lock"):
                                _lock_runtime_config(directory_fd)

                        self.assertEqual(1, len(failed_lock_fds))
                        close.assert_called_once_with(failed_lock_fds[0])
            finally:
                os.close(directory_fd)

    def test_runtime_root_uses_codex_home(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(Path(temp) / "pitcrew", runtime_root({"CODEX_HOME": temp}))

    def test_runtime_root_defaults_to_dot_codex(self):
        self.assertEqual(
            Path("/tmp/example-home/.codex/pitcrew"),
            runtime_root({"HOME": "/tmp/example-home"}),
        )

    def test_getbill_profile_fails_closed(self):
        profile = load_profile(ROOT / "profiles/getbill.json")
        self.assertEqual("gitlab", profile["providers"]["forge"])
        self.assertEqual("gitlab.com", profile["gitlab"]["host"])
        self.assertEqual("getbill1/getbill", profile["gitlab"]["project_path"])
        self.assertEqual(59043683, profile["gitlab"]["project_id"])
        self.assertEqual("joachim28", profile["gitlab"]["user"])
        self.assertEqual("develop", profile["repos"][0]["default_branch"])
        self.assertEqual(
            "pitcrew-state::todo",
            profile["gitlab"]["tracker"]["states"]["todo"],
        )
        self.assertEqual(
            "pitcrew-agent",
            profile["gitlab"]["tracker"]["labels"]["agent"],
        )
        self.assertEqual(
            "research-v1",
            profile["manager"]["sources"][0]["format"],
        )
        self.assertRegex(
            profile["manager"]["risky_categories_regex"],
            r"webhook.*signature.*bypass",
        )
        self.assertNotIn("architecture_repo", profile["researcher"])
        self.assertEqual("off", profile["release"]["autonomy"])
        self.assertEqual(
            ["prod", "preprod"],
            profile["safety"]["confirm_each_remote_action"],
        )
        self.assertFalse(profile["safety"]["allow_database_writes"])
        self.assertFalse(profile["safety"]["allow_destructive_git"])

    def test_invalid_provider_is_rejected(self):
        fixture = json.loads(
            (ROOT / "tests/fixtures/invalid-config.json").read_text(encoding="utf-8")
        )
        with self.assertRaisesRegex(ConfigError, "providers.forge"):
            validate(fixture)

    def test_gitlab_provider_requires_explicit_project_and_lifecycle_mapping(self):
        profile = json.loads(
            (ROOT / "profiles/getbill.json").read_text(encoding="utf-8")
        )
        del profile["gitlab"]["tracker"]["states"]["review"]
        with self.assertRaisesRegex(ConfigError, "gitlab.tracker.states.review"):
            validate(profile)

        profile = json.loads(
            (ROOT / "profiles/getbill.json").read_text(encoding="utf-8")
        )
        del profile["gitlab"]["tracker"]["ticket_prefix"]
        with self.assertRaisesRegex(ConfigError, "gitlab.tracker.ticket_prefix"):
            validate(profile)

    def test_invalid_project_does_not_create_runtime_directories(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ConfigError, "project_name"):
                write_project(
                    ROOT / "profiles/generic.json", "../escape", {"CODEX_HOME": temp}
                )
            self.assertEqual([], list(Path(temp).iterdir()))

    def test_write_project_does_not_overwrite_existing_content_or_symlink_target(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            env = {"CODEX_HOME": temp}
            destination = root / "pitcrew" / "example" / "config.json"
            destination.parent.mkdir(parents=True)
            destination.write_text("existing", encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "refusing to overwrite"):
                write_project(ROOT / "profiles/generic.json", "example", env)
            self.assertEqual("existing", destination.read_text(encoding="utf-8"))

            destination.unlink()
            target = root / "outside.json"
            destination.symlink_to(target)
            with self.assertRaisesRegex(ConfigError, "refusing to overwrite"):
                write_project(ROOT / "profiles/generic.json", "example", env)
            self.assertTrue(destination.is_symlink())
            self.assertFalse(target.exists())

    def test_write_project_rejects_symlinked_project_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            outside = root / "outside"
            outside.mkdir()
            project_directory = root / "pitcrew" / "getbill"
            project_directory.parent.mkdir()
            project_directory.symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ConfigError, "symlink"):
                write_project(
                    ROOT / "profiles/getbill.json", "getbill", {"CODEX_HOME": temp}
                )
            self.assertFalse((outside / "config.json").exists())

    def test_write_project_rejects_symlinked_default_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime = root / "pitcrew"
            runtime.mkdir()
            target = root / "outside-default.txt"
            target.write_text("unchanged", encoding="utf-8")
            (runtime / "default.txt").symlink_to(target)
            with self.assertRaisesRegex(ConfigError, "default.txt"):
                write_project(ROOT / "profiles/generic.json", "example", {"CODEX_HOME": temp})
            self.assertEqual("unchanged", target.read_text(encoding="utf-8"))

    def test_write_project_updates_regular_default_file(self):
        with tempfile.TemporaryDirectory() as temp:
            env = {"CODEX_HOME": temp}
            write_project(ROOT / "profiles/generic.json", "first", env)
            write_project(ROOT / "profiles/generic.json", "second", env)
            self.assertEqual(
                "second\n",
                (Path(temp) / "pitcrew" / "default.txt").read_text(encoding="utf-8"),
            )

    def test_malformed_config_containers_are_rejected(self):
        valid = {
            "schema_version": 1,
            "project_name": "example",
            "providers": {"forge": "github", "tracker": "linear"},
            "repos": [],
            "release": {"autonomy": "off"},
            "safety": {
                "allow_database_writes": False,
                "allow_destructive_git": False,
                "allow_secret_reads": False,
            },
        }
        for key, value in (("release", []), ("safety", [])):
            with self.subTest(key=key):
                malformed = {**valid, key: value}
                with self.assertRaisesRegex(ConfigError, f"{key} must be an object"):
                    validate(malformed)
        with self.assertRaisesRegex(ConfigError, "config must be an object"):
            validate([])

    def test_agents_are_optional_but_overrides_must_be_known(self):
        profile = json.loads((ROOT / "profiles/generic.json").read_text(encoding="utf-8"))
        validate(profile)
        profile["agents"] = {"research-run": {"model": "gpt-5.6-terra"}}
        validate(profile)
        profile["agents"] = {
            "research-run": {
                "model": "gpt-5.6-terra",
                "reasoning_effort": "high",
                "routing_mode": "fixed",
            }
        }
        validate(profile)

        for agents, message in (
            ([], "agents must be an object"),
            ({"unknown-run": {"model": "gpt-5.6-terra"}}, "agents.unknown-run"),
            ({"research-run": []}, "agents.research-run"),
            ({"research-run": {}}, "agents.research-run.model is unsupported"),
            (
                {"research-run": {"model": "gpt-5.6-terra", "extra": True}},
                "agents.research-run.extra is unsupported",
            ),
            ({"research-run": {"model": "invented"}}, "agents.research-run.model"),
            ({"research-run": {"model": []}}, "agents.research-run.model"),
            (
                {
                    "research-run": {
                        "model": "gpt-5.6-terra",
                        "reasoning_effort": "extreme",
                    }
                },
                "agents.research-run.reasoning_effort is unsupported",
            ),
            (
                {
                    "research-run": {
                        "model": "gpt-5.6-terra",
                        "reasoning_effort": [],
                    }
                },
                "agents.research-run.reasoning_effort is unsupported",
            ),
            (
                {
                    "research-run": {
                        "model": "gpt-5.6-terra",
                        "routing_mode": "automatic",
                    }
                },
                "agents.research-run.routing_mode is unsupported",
            ),
            (
                {
                    "research-run": {
                        "model": "gpt-5.6-terra",
                        "routing_mode": [],
                    }
                },
                "agents.research-run.routing_mode is unsupported",
            ),
        ):
            with self.subTest(agents=agents):
                invalid = {**profile, "agents": agents}
                with self.assertRaisesRegex(ConfigError, message):
                    validate(invalid)

    def test_architecture_interval_is_optional_but_when_set_must_be_weekly(self):
        profile = json.loads((ROOT / "profiles/generic.json").read_text(encoding="utf-8"))
        validate(profile)
        self.assertEqual(604800, profile["architecture"]["interval_seconds"])

        legacy = {key: value for key, value in profile.items() if key != "architecture"}
        validate(legacy)

        for architecture, message in (
            ([], "architecture must be an object"),
            ({}, "architecture.interval_seconds must be exactly 604800"),
            ({"interval_seconds": 60}, "architecture.interval_seconds must be exactly 604800"),
            ({"interval_seconds": True}, "architecture.interval_seconds must be exactly 604800"),
            ({"interval_seconds": 604800.0}, "architecture.interval_seconds must be exactly 604800"),
            ({"interval_seconds": 604800, "extra": True}, "architecture.extra is unsupported"),
        ):
            with self.subTest(architecture=architecture):
                invalid = {**profile, "architecture": architecture}
                with self.assertRaisesRegex(ConfigError, message):
                    validate(invalid)

    def test_cli_resolves_reasoning_effort_and_routing_mode(self):
        with tempfile.TemporaryDirectory() as temp:
            codex_home = str(Path(temp).resolve())
            env = {"CODEX_HOME": codex_home}
            destination = write_project(ROOT / "profiles/generic.json", "example", env)
            config = json.loads(destination.read_text(encoding="utf-8"))
            config["agents"]["research-run"].update(
                {"reasoning_effort": "high", "routing_mode": "fixed"}
            )
            destination.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
            command_env = {**os.environ, "CODEX_HOME": codex_home}

            reasoning = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "reasoning",
                    "--project",
                    "example",
                    "--skill",
                    "research-run",
                ],
                text=True,
                capture_output=True,
                check=False,
                env=command_env,
            )
            reasoning_effort = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "reasoning-effort",
                    "--project",
                    "example",
                    "--skill",
                    "research-run",
                ],
                text=True,
                capture_output=True,
                check=False,
                env=command_env,
            )
            routing = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "routing-mode",
                    "--project",
                    "example",
                    "--skill",
                    "research-run",
                ],
                text=True,
                capture_output=True,
                check=False,
                env=command_env,
            )

        self.assertEqual(
            (0, "high\n", ""),
            (reasoning.returncode, reasoning.stdout, reasoning.stderr),
        )
        self.assertEqual(
            (reasoning.returncode, reasoning.stdout, reasoning.stderr),
            (
                reasoning_effort.returncode,
                reasoning_effort.stdout,
                reasoning_effort.stderr,
            ),
        )
        self.assertEqual(
            (0, "fixed\n", ""),
            (routing.returncode, routing.stdout, routing.stderr),
        )

    def test_execution_capacity_defaults_and_accepts_per_skill_overrides(self):
        profile = json.loads((ROOT / "profiles/generic.json").read_text(encoding="utf-8"))

        validate(profile)
        self.assertEqual(3, max_concurrent_for(profile, "implementer-run"))

        profile["execution"] = {
            "default_max_concurrent_per_skill": 3,
            "max_concurrent_per_skill": {"implementer-run": 4, "unblock": 2},
        }
        validate(profile)
        self.assertEqual(4, max_concurrent_for(profile, "implementer-run"))
        self.assertEqual(2, max_concurrent_for(profile, "unblock"))
        self.assertEqual(3, max_concurrent_for(profile, "research-run"))

    def test_execution_capacity_rejects_invalid_shapes_roles_and_values(self):
        profile = json.loads((ROOT / "profiles/generic.json").read_text(encoding="utf-8"))

        for value in (0, 17, True, "3"):
            with self.subTest(value=value):
                invalid = {
                    **profile,
                    "execution": {"default_max_concurrent_per_skill": value},
                }
                with self.assertRaisesRegex(
                    ConfigError,
                    "execution.default_max_concurrent_per_skill must be an integer from 1 to 16",
                ):
                    validate(invalid)

        for value in (0, 17, True, "3"):
            with self.subTest(override_value=value):
                invalid = {
                    **profile,
                    "execution": {
                        "max_concurrent_per_skill": {"implementer-run": value},
                    },
                }
                with self.assertRaisesRegex(
                    ConfigError,
                    "execution.max_concurrent_per_skill.implementer-run must be an integer from 1 to 16",
                ):
                    validate(invalid)

        for execution, message in (
            ({"max_concurrent_per_skill": {"unknown-run": 3}}, "execution role is unsupported"),
            ({"burst": 3}, "execution contains unsupported fields"),
            ([], "execution must be an object"),
            ({"max_concurrent_per_skill": []}, "execution.max_concurrent_per_skill must be an object"),
        ):
            with self.subTest(execution=execution):
                invalid = {**profile, "execution": execution}
                with self.assertRaisesRegex(ConfigError, message):
                    validate(invalid)

    def test_repositories_require_non_empty_name_and_path(self):
        valid = {
            "schema_version": 1,
            "project_name": "example",
            "providers": {"forge": "github", "tracker": "linear"},
            "repos": [{"name": "example", "path": "/tmp/example"}],
            "release": {"autonomy": "off"},
            "safety": {
                "allow_database_writes": False,
                "allow_destructive_git": False,
                "allow_secret_reads": False,
            },
        }
        for repository in (None, [], {}, {"name": "", "path": "/tmp/example"}, {"name": "example", "path": ""}):
            with self.subTest(repository=repository):
                invalid = {**valid, "repos": [repository]}
                with self.assertRaisesRegex(ConfigError, r"repos\[0\]"):
                    validate(invalid)

    def test_load_runtime_config_rejects_symlinked_runtime_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            outside = root / "outside"
            outside.mkdir()
            codex_home = root / "codex"
            runtime = codex_home / "pitcrew"
            runtime.parent.mkdir()
            runtime.symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ConfigError, "symlink"):
                load_runtime_config("getbill", {"CODEX_HOME": str(codex_home)})

            runtime.unlink()
            runtime.mkdir()
            (runtime / "getbill").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ConfigError, "symlink"):
                load_runtime_config("getbill", {"CODEX_HOME": str(codex_home)})

            (runtime / "getbill").unlink()
            project = runtime / "getbill"
            project.mkdir()
            (project / "config.json").symlink_to(root / "config.json")
            with self.assertRaisesRegex(ConfigError, "symlink"):
                load_runtime_config("getbill", {"CODEX_HOME": str(codex_home)})

    def test_cli_reports_malformed_json_without_traceback(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "broken.json"
            config.write_text("{", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "validate", str(config)],
                text=True,
                capture_output=True,
                check=False,
                env=os.environ.copy(),
            )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("pitcrew-config:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_cli_reports_non_utf8_config_without_traceback(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "broken.json"
            config.write_bytes(b"\xff")
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "validate", str(config)],
                text=True,
                capture_output=True,
                check=False,
                env=os.environ.copy(),
            )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("pitcrew-config:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_migration_copies_legacy_config_without_overwriting(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            source = root / ".claude" / "agent-loop" / "getbill"
            source.mkdir(parents=True)
            source_config = (
                ROOT / "tests/fixtures/legacy-config.json"
            ).read_text(encoding="utf-8")
            (source / "config.json").write_text(source_config, encoding="utf-8")
            codex_home = root / ".codex"
            codex_home.mkdir()
            env = {"HOME": str(root), "CODEX_HOME": str(codex_home)}

            destination = migrate_legacy("getbill", env)

            self.assertEqual(
                {
                    "project_name": "getbill",
                    "github": {"reviewer_login": "mercierj", "org": "mercierj"},
                    "linear": {"use": False},
                    "repos": [
                        {
                            "name": "getbill",
                            "path": "/Users/jo/Prog/getbill",
                            "default_branch": "main",
                            "lang": "php",
                        }
                    ],
                    "schema_version": 1,
                    "providers": {"forge": "github", "tracker": "none"},
                    "release": {"autonomy": "off"},
                    "safety": {
                        "allow_database_writes": False,
                        "allow_destructive_git": False,
                        "allow_secret_reads": False,
                    },
                },
                json.loads(destination.read_text(encoding="utf-8")),
            )
            original = destination.read_text(encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "refusing to overwrite"):
                migrate_legacy("getbill", env)
            self.assertEqual(original, destination.read_text(encoding="utf-8"))

    def test_migration_preserves_existing_release_arming(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            source = root / ".claude" / "agent-loop" / "getbill"
            source.mkdir(parents=True)
            legacy = json.loads(
                (ROOT / "tests/fixtures/legacy-config.json").read_text(encoding="utf-8")
            )
            legacy["release"] = {"autonomy": "prepare"}
            (source / "config.json").write_text(json.dumps(legacy), encoding="utf-8")

            codex_home = root / ".codex"
            codex_home.mkdir()
            destination = migrate_legacy(
                "getbill", {"HOME": str(root), "CODEX_HOME": str(codex_home)}
            )

            self.assertEqual(
                "prepare", json.loads(destination.read_text(encoding="utf-8"))["release"]["autonomy"]
            )

    def test_migration_creates_missing_nested_codex_home_securely(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            source = root / ".claude" / "agent-loop" / "getbill"
            source.mkdir(parents=True)
            (source / "config.json").write_text(
                (ROOT / "tests/fixtures/legacy-config.json").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            codex_home = root / "missing" / "nested" / "codex"

            destination = migrate_legacy(
                "getbill", {"HOME": str(root), "CODEX_HOME": str(codex_home)}
            )

            self.assertEqual(codex_home / "pitcrew" / "getbill" / "config.json", destination)
            self.assertTrue(destination.is_file())

    def test_migration_rejects_symlinked_source_and_destination_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            outside = root / "outside.json"
            outside.write_text("{}", encoding="utf-8")
            source = root / ".claude" / "agent-loop" / "getbill"
            source.mkdir(parents=True)
            (source / "config.json").symlink_to(outside)
            env = {"HOME": str(root), "CODEX_HOME": str(root / ".codex")}
            with self.assertRaisesRegex(ConfigError, "symlink"):
                migrate_legacy("getbill", env)

            (source / "config.json").unlink()
            (source / "config.json").write_text(
                (ROOT / "tests/fixtures/legacy-config.json").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            runtime = root / ".codex" / "pitcrew"
            runtime.parent.mkdir()
            runtime.symlink_to(root / "outside", target_is_directory=True)
            with self.assertRaisesRegex(ConfigError, "symlink"):
                migrate_legacy("getbill", env)

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            source = root / ".claude" / "agent-loop" / "getbill"
            source.mkdir(parents=True)
            (source / "config.json").write_text(
                (ROOT / "tests/fixtures/legacy-config.json").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            runtime = root / ".codex" / "pitcrew"
            runtime.mkdir(parents=True)
            (runtime / "getbill").symlink_to(root / "outside", target_is_directory=True)
            with self.assertRaisesRegex(ConfigError, "symlink"):
                migrate_legacy("getbill", {"HOME": str(root), "CODEX_HOME": str(root / ".codex")})

    def test_cli_migrate_validates_profile_and_reports_errors_concisely(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            source = root / ".claude" / "agent-loop" / "getbill"
            source.mkdir(parents=True)
            (source / "config.json").write_text(
                (ROOT / "tests/fixtures/legacy-config.json").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            codex_home = root / ".codex"
            codex_home.mkdir()
            environment = {**os.environ, "HOME": str(root), "CODEX_HOME": str(codex_home)}
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "migrate", "--project", "getbill"],
                text=True,
                capture_output=True,
                check=False,
                env=environment,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            destination = root / ".codex" / "pitcrew" / "getbill" / "config.json"
            self.assertTrue(destination.is_file())
            validate(json.loads(destination.read_text(encoding="utf-8")))
            source = root / ".claude" / "agent-loop" / "getbill" / "config.json"
            self.assertIn(str(source), result.stdout)
            self.assertIn(str(destination), result.stdout)
            self.assertIn("Migrated config to", result.stdout)
            self.assertLess(
                result.stdout.index(str(source)), result.stdout.index("Migrated config to")
            )

    def test_exclusive_writer_uses_open_directory_after_path_is_replaced(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            original = root / "original"
            original.mkdir()
            outside = root / "outside"
            outside.mkdir()
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            directory_fd = os.open(original, flags)
            try:
                moved = root / "moved"
                original.rename(moved)
                original.symlink_to(outside, target_is_directory=True)

                _write_exclusive_config(directory_fd, "config.json", '{"safe": true}\n')
            finally:
                os.close(directory_fd)

            self.assertEqual('{"safe": true}\n', (moved / "config.json").read_text())
            self.assertFalse((outside / "config.json").exists())

    def test_cli_migrate_rejects_unreadable_legacy_config_without_destination(self):
        for payload in ("{", b"\xff"):
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as temp:
                root = Path(temp).resolve()
                source = root / ".claude" / "agent-loop" / "getbill"
                source.mkdir(parents=True)
                config = source / "config.json"
                if isinstance(payload, bytes):
                    config.write_bytes(payload)
                else:
                    config.write_text(payload, encoding="utf-8")
                result = subprocess.run(
                    [sys.executable, str(SCRIPT), "migrate", "--project", "getbill"],
                    text=True,
                    capture_output=True,
                    check=False,
                    env={**os.environ, "HOME": str(root), "CODEX_HOME": str(root / ".codex")},
                )
                self.assertEqual(1, result.returncode)
                self.assertIn("pitcrew-config:", result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertFalse(
                    (root / ".codex" / "pitcrew" / "getbill" / "config.json").exists()
                )

    def test_migration_rejects_symlinked_home_and_codex_home_anchors(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            outside = root / "outside"
            outside.mkdir()
            legacy = outside / ".claude" / "agent-loop" / "getbill"
            legacy.mkdir(parents=True)
            (legacy / "config.json").write_text(
                (ROOT / "tests/fixtures/legacy-config.json").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            home_link = root / "home-link"
            home_link.symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ConfigError, "symlink"):
                migrate_legacy("getbill", {"HOME": str(home_link)})
            self.assertFalse((outside / ".codex" / "pitcrew" / "getbill" / "config.json").exists())

            home = root / "home"
            source = home / ".claude" / "agent-loop" / "getbill"
            source.mkdir(parents=True)
            (source / "config.json").write_text(
                (ROOT / "tests/fixtures/legacy-config.json").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            codex_link = root / "codex-link"
            codex_link.symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ConfigError, "symlink"):
                migrate_legacy("getbill", {"HOME": str(home), "CODEX_HOME": str(codex_link)})
            self.assertFalse((outside / "pitcrew" / "getbill" / "config.json").exists())


if __name__ == "__main__":
    unittest.main()
