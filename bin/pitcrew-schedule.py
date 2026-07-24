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
PROJECT_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
LABEL_PREFIX = "io.getbill.pitcrew"

SCHEDULE = (
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
    ("unblock", 1800, False, "requires a human response"),
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


def render(project: str, output_dir: Path, env: dict[str, str]) -> list[Path]:
    paths = []
    for entry in entries():
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


def install(project: str, output_dir: Path, env: dict[str, str]) -> int:
    paths = render(project, output_dir, env)
    domain = f"gui/{os.getuid()}"
    for path in paths:
        label = path.stem
        launchctl("bootout", f"{domain}/{label}", check=False)
        result = launchctl("bootstrap", domain, str(path), check=False)
        if result.returncode:
            print(result.stderr.strip() or f"failed to bootstrap {label}", file=sys.stderr)
            return result.returncode
        enabled = launchctl("enable", f"{domain}/{label}", check=False)
        if enabled.returncode:
            print(enabled.stderr.strip() or f"failed to enable {label}", file=sys.stderr)
            return enabled.returncode
        print(f"installed {label}")
    return 0


def status(project: str) -> int:
    domain = f"gui/{os.getuid()}"
    result = []
    for entry in entries():
        skill = str(entry["skill"])
        label = launchd_label(project, skill)
        inspected = launchctl("print", f"{domain}/{label}", check=False)
        result.append(
            {
                **entry,
                "label": label,
                "loaded": inspected.returncode == 0,
            }
        )
    print(json.dumps(result, indent=2))
    return 0


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(description="Manage recurring Pitcrew Codex jobs")
    commands = top.add_subparsers(dest="command", required=True)
    for command in ("list", "render", "install", "status"):
        child = commands.add_parser(command)
        child.add_argument("--project", type=validated_project, default="getbill")
        if command == "list":
            child.add_argument("--json", action="store_true")
        if command in {"render", "install"}:
            child.add_argument(
                "--output-dir",
                type=Path,
                default=Path.home() / "Library/LaunchAgents",
            )
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
    if args.command == "install":
        return install(args.project, args.output_dir.expanduser(), env)
    if args.command == "status":
        return status(args.project)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
