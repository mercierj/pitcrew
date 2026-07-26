#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from scripts.pitcrew_config import ConfigError, load_runtime_config, max_concurrent_for, runtime_root
    from scripts.pitcrew_run_dispatcher import RunDispatcher
    from scripts.pitcrew_run_store import RunStore
    from scripts.pitcrew_runtime_state import read_state, write_state
except ModuleNotFoundError:
    from pitcrew_config import ConfigError, load_runtime_config, max_concurrent_for, runtime_root
    from pitcrew_run_dispatcher import RunDispatcher
    from pitcrew_run_store import RunStore
    from pitcrew_runtime_state import read_state, write_state


PROJECT_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
LABEL_PREFIX = "io.getbill.pitcrew"

SCHEDULE = (
    ("architecture-run", 604800, True, ""),
    ("security-run", 86400, True, ""),
    ("product-discovery-run", 604800, True, ""),
    ("research-run", 1800, True, ""),
    ("manager-run", 3600, True, ""),
    ("implementer-run", 900, True, ""),
    ("reviewer-run", 900, True, ""),
    ("validator-run", 900, True, ""),
    ("investigate-run", 1800, True, ""),
    ("stale-sweep", 21600, True, ""),
    ("qa-run", 7200, False, "qa.test_flow_repo is not configured"),
    ("coverage-run", 43200, False, "QA flow and architecture repositories are not configured"),
    ("dev-verify-run", 900, False, "live dev flow verification is not configured"),
    ("ops-run", 600, False, "repos[].health is not configured"),
    ("unblock", 1800, True, ""),
    ("releaser-run", 900, False, "release autonomy is off for GetBill"),
)


def validated_project(value: str) -> str:
    if not PROJECT_NAME.fullmatch(value):
        raise argparse.ArgumentTypeError("project must match ^[a-z0-9][a-z0-9_-]*$")
    return value


def entries() -> list[dict[str, object]]:
    return [
        {
            "skill": skill,
            "interval_seconds": interval,
            "enabled": enabled,
            "reason": reason,
        }
        for skill, interval, enabled, reason in SCHEDULE
    ]


def enabled_entry(skill: str) -> dict[str, object]:
    for entry in entries():
        if entry["skill"] != skill:
            continue
        if not entry["enabled"]:
            raise ValueError(f"skill is disabled: {skill}")
        return entry
    raise ValueError(f"unknown skill: {skill}")


def launchd_label(project: str, skill: str) -> str:
    return f"{LABEL_PREFIX}.{project}.{skill}"


def log_path(project: str, skill: str, env: dict[str, str]) -> Path:
    codex_home = Path(env.get("CODEX_HOME", Path(env["HOME"]) / ".codex")).expanduser()
    return codex_home / "pitcrew" / project / "logs" / f"{skill}.log"


def plist_payload(project: str, entry: dict[str, object], env: dict[str, str]) -> dict:
    skill = str(entry["skill"])
    log = log_path(project, skill, env)
    path = env.get("PATH", "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin")
    environment = {
        "HOME": env["HOME"],
        "PATH": path,
        "CODEX_BIN": env.get("CODEX_BIN") or shutil.which("codex", path=path) or "codex",
    }
    if env.get("CODEX_HOME"):
        environment["CODEX_HOME"] = env["CODEX_HOME"]
    return {
        "Label": launchd_label(project, skill),
        "ProgramArguments": [
            str(ROOT / "bin/pitcrew-codex.sh"),
            skill,
            project,
            "--scheduled",
        ],
        "WorkingDirectory": str(ROOT),
        "StartInterval": int(entry["interval_seconds"]),
        "RunAtLoad": False,
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "EnvironmentVariables": environment,
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": "/dev/null",
    }


def write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            plistlib.dump(payload, handle, sort_keys=True)
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def render(
    project: str,
    output_dir: Path,
    env: dict[str, str],
    skill: str | None = None,
) -> list[Path]:
    paths = []
    selected = [enabled_entry(skill)] if skill else entries()
    for entry in selected:
        if not entry["enabled"]:
            continue
        logs_dir = log_path(project, str(entry["skill"]), env).parent
        logs_dir.mkdir(parents=True, exist_ok=True)
        logs_dir.chmod(0o700)
        path = output_dir / f"{launchd_label(project, str(entry['skill']))}.plist"
        write_atomic(path, plist_payload(project, entry, env))
        paths.append(path)
    return paths


def launchctl(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["launchctl", *args],
        text=True,
        capture_output=True,
        check=check,
    )


def scrubbed_error(message: str, fallback: str) -> str:
    if not message.strip():
        return fallback
    scrubbed = re.sub(
        r"(?im)(\bAuthorization\s*:\s*)[^\r\n]+",
        r"\1[REDACTED]",
        message,
    )
    scrubbed = re.sub(
        (
            r'(?i)("?\b(?:token|authorization|password|secret|key|credential)\b"?\s*'
            r'(?:=|:)\s*)(?:Bearer\s+)?(?:"[^"]*"|\'[^\']*\'|[^\s,}]+)'
        ),
        r"\1[REDACTED]",
        scrubbed,
    )
    scrubbed = " ".join(scrubbed.split())
    return scrubbed[:500]


def launchctl_failure(
    result: subprocess.CompletedProcess[str],
    fallback: str,
) -> int:
    print(scrubbed_error(result.stderr, fallback), file=sys.stderr)
    return result.returncode


def install(
    project: str,
    output_dir: Path,
    env: dict[str, str],
    skill: str | None = None,
) -> int:
    try:
        if read_state(project, env) == "stopped":
            print("scheduler is globally stopped", file=sys.stderr)
            return 2
    except (OSError, ValueError):
        print("scheduler execution state is unavailable", file=sys.stderr)
        return 2
    paths = render(project, output_dir, env, skill)
    domain = f"gui/{os.getuid()}"
    for path in paths:
        label = path.stem
        launchctl("bootout", f"{domain}/{label}", check=False)
        result = launchctl("bootstrap", domain, str(path), check=False)
        if result.returncode:
            return launchctl_failure(result, f"failed to bootstrap {label}")
        enabled = launchctl("enable", f"{domain}/{label}", check=False)
        if enabled.returncode:
            return launchctl_failure(enabled, f"failed to enable {label}")
        print(f"installed {label}")
    return 0


def launchd_state(output: str) -> tuple[bool, int | None]:
    running = re.search(r"^\s*state\s*=\s*running\s*$", output, re.MULTILINE) is not None
    pid_match = re.search(r"^\s*pid\s*=\s*(\d+)\s*$", output, re.MULTILINE)
    return running, int(pid_match.group(1)) if pid_match else None


def status(project: str, skill: str | None = None) -> int:
    domain = f"gui/{os.getuid()}"
    try:
        global_state = read_state(project)
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2
    result = []
    selected = [enabled_entry(skill)] if skill else entries()
    for entry in selected:
        skill = str(entry["skill"])
        label = launchd_label(project, skill)
        inspected = launchctl("print", f"{domain}/{label}", check=False)
        loaded = inspected.returncode == 0
        running, pid = launchd_state(inspected.stdout) if loaded else (False, None)
        result.append(
            {
                **entry,
                "label": label,
                "loaded": loaded,
                "running": running,
                "pid": pid,
                "global_state": global_state,
            }
        )
    print(json.dumps(result, indent=2))
    return 0


def stop(project: str, skill: str) -> int:
    enabled_entry(skill)
    label = launchd_label(project, skill)
    target = f"gui/{os.getuid()}/{label}"
    result = launchctl("bootout", target, check=False)
    if result.returncode:
        return launchctl_failure(result, f"failed to stop {label}")
    print(f"stopped {label}")
    return 0


def coordinated_components(project: str, env: dict[str, str]) -> tuple[RunStore, RunDispatcher, dict[str, int]]:
    store = RunStore(runtime_root(env) / project / "runs.sqlite3")
    # Scheduler controls have historically worked before a project is
    # configured. Keep that recovery path available with the documented
    # default capacity, then apply configured overrides when present.
    capacities = {
        str(entry["skill"]): 3
        for entry in entries()
        if entry["enabled"]
    }
    try:
        config = load_runtime_config(project, env)
    except ConfigError as error:
        # A missing config is the one compatibility case.  Any malformed or
        # unsafe configured project must fail closed instead of draining with
        # guessed capacities.
        if str(error) != "runtime config path must not be a symlink or missing":
            raise
    else:
        skills = config.get("agents", {})
        capacities = {
            skill: max_concurrent_for(config, skill)
            for skill in skills
        }
    return store, RunDispatcher(store, str(ROOT / "bin" / "pitcrew-codex.sh")), capacities


def stop_all(
    project: str,
    env: dict[str, str] | None = None,
    components_factory=coordinated_components,
) -> int:
    values = dict(os.environ) if env is None else env
    store: RunStore | None = None
    dispatcher: RunDispatcher | None = None
    first_failure = 0
    try:
        store, dispatcher, _ = components_factory(project, values)
    except Exception:
        first_failure = 2
        print("coordinated state is unavailable", file=sys.stderr)
    # The SQLite control row is the admission gate.  It must close before the
    # compatibility flag and process cancellation so an enqueue racing a stop
    # can never start a newly admitted worker after the stop takes effect.
    if store is not None:
        try:
            store.set_project_state(project, "stopped")
        except Exception:
            first_failure = first_failure or 2
            print("coordinated admission could not be closed", file=sys.stderr)
    try:
        write_state(project, "stopped", values)
    except Exception:
        # Do not leave live workers or launchd jobs running merely because the
        # compatibility flag could not be made durable; report failure after
        # the mandatory cleanup below.
        first_failure = first_failure or 2
        print("scheduler execution state could not be persisted", file=sys.stderr)
    if dispatcher is None:
        first_failure = first_failure or 2
        print("coordinated workers could not be cancelled", file=sys.stderr)
    else:
        try:
            dispatcher.cancel_running(project=project, error_code="global_stop")
        except Exception:
            first_failure = first_failure or 2
            print("coordinated workers could not all be cancelled", file=sys.stderr)
    domain = f"gui/{os.getuid()}"
    for entry in entries():
        if not entry["enabled"]:
            continue
        label = launchd_label(project, str(entry["skill"]))
        try:
            result = launchctl("bootout", f"{domain}/{label}", check=False)
        except Exception:
            first_failure = first_failure or 2
            continue
        if result.returncode and not first_failure:
            first_failure = result.returncode
    if first_failure:
        print("one or more scheduled agents could not be stopped", file=sys.stderr)
    return first_failure


def resume_all(
    project: str,
    output_dir: Path,
    env: dict[str, str],
    components_factory=coordinated_components,
) -> int:
    write_state(project, "running", env)
    result = install(project, output_dir, env)
    if result:
        # SQLite remains stopped until below. Keep its compatibility flag in
        # lockstep if launchd could not be installed.
        write_state(project, "stopped", env)
        return result
    store: RunStore | None = None
    dispatcher: RunDispatcher | None = None
    try:
        store, dispatcher, capacities = components_factory(project, env)
        store.set_project_state(project, "running")
        dispatcher.reconcile_and_drain(project=project, capacities=capacities)
    except Exception:
        # Close admission before reaping workers from a partial drain, then
        # restore the compatibility flag. Cleanup must not mask the original
        # recovery failure.
        if store is not None:
            try:
                store.set_project_state(project, "stopped")
            except Exception:
                pass
        if dispatcher is not None:
            try:
                dispatcher.cancel_running(
                    project=project,
                    error_code="resume_failed",
                )
            except Exception:
                pass
        try:
            write_state(project, "stopped", env)
        except Exception:
            pass
        raise
    return 0


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(description="Manage recurring Pitcrew Codex jobs")
    commands = top.add_subparsers(dest="command", required=True)
    for command in ("list", "render", "install", "status", "stop", "stop-all", "resume-all"):
        child = commands.add_parser(command)
        child.add_argument("--project", type=validated_project, default="getbill")
        if command == "list":
            child.add_argument("--json", action="store_true")
        if command in {"render", "install", "resume-all"}:
            child.add_argument(
                "--output-dir",
                type=Path,
                default=Path.home() / "Library/LaunchAgents",
            )
        if command in {"install", "status"}:
            child.add_argument("--skill")
        if command == "stop":
            child.add_argument("--skill", required=True)
    return top


def main() -> int:
    args = parser().parse_args()
    env = dict(os.environ)
    if args.command == "list":
        if args.json:
            print(json.dumps(entries(), indent=2))
        else:
            for entry in entries():
                state = "enabled" if entry["enabled"] else f"disabled: {entry['reason']}"
                print(f"{entry['skill']}: every {entry['interval_seconds']}s ({state})")
        return 0
    if args.command == "render":
        for path in render(args.project, args.output_dir.expanduser(), env):
            print(path)
        return 0
    try:
        if args.command == "install":
            return install(args.project, args.output_dir.expanduser(), env, args.skill)
        if args.command == "status":
            return status(args.project, args.skill)
        if args.command == "stop":
            return stop(args.project, args.skill)
        if args.command == "stop-all":
            return stop_all(args.project, env)
        if args.command == "resume-all":
            return resume_all(args.project, args.output_dir.expanduser(), env)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
