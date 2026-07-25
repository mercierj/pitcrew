"""Durable SQLite queue for Pitcrew ticket runs."""
from __future__ import annotations

import os
import sqlite3
import stat
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ACTIVE_STATES = ("queued", "running")
TERMINAL_STATES = ("succeeded", "failed", "cancelled")
ALL_STATES = ACTIVE_STATES + TERMINAL_STATES
RETENTION = timedelta(days=7)
STALE_HEARTBEAT = timedelta(seconds=30)
SCHEMA_VERSION = 1
_SOURCES = {"dashboard", "scheduled", "reconcile"}


class RunStoreError(ValueError):
    """Public error raised for invalid store input or storage failures."""


class RunConflict(RunStoreError):
    """An active run already owns the requested deduplication key."""


class RunStateError(RunStoreError):
    """The requested transition is invalid for the run's current state."""


class RunPaused(RunStoreError):
    """The project control prevents new work from being admitted or claimed."""


class RunStore:
    def __init__(self, path: Path, now: Callable[[], datetime] | None = None,
                 pid_alive: Callable[[int], bool] | None = None,
                 connect_factory: Callable[..., sqlite3.Connection] | None = None):
        self.path = Path(path)
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._pid_alive = pid_alive or self._default_pid_alive
        self._connect_factory = connect_factory or sqlite3.connect
        self._timestamp()  # fail early for a broken clock
        self._prepare_parent()
        self._initialize()

    def _prepare_parent(self) -> None:
        """Create only missing private directories; never trust symlinked ancestors."""
        absolute = self.path.absolute()
        missing: list[Path] = []
        current = absolute.parent
        while not current.exists():
            missing.append(current)
            current = current.parent
        for directory in reversed(missing):
            directory.mkdir(mode=0o700)
        parent = self.path.parent.absolute()
        if parent.resolve(strict=True) != parent:
            raise RunStoreError("database path contains a symlink")
        metadata = os.lstat(parent)
        if not stat.S_ISDIR(metadata.st_mode):
            raise RunStoreError("database parent is not a directory")
        metadata = os.lstat(self.path.parent)
        if metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
            raise RunStoreError("database parent must be private")
        try:
            database = os.lstat(self.path)
        except FileNotFoundError:
            return
        if stat.S_ISLNK(database.st_mode):
            raise RunStoreError("database path must not be a symlink")
        if not stat.S_ISREG(database.st_mode):
            raise RunStoreError("database path must be a regular file")

    @staticmethod
    def _capacities(capacities: Mapping[str, int]) -> dict[str, int]:
        if not isinstance(capacities, Mapping):
            raise RunStoreError("capacity is invalid")
        result = dict(capacities)
        for skill, value in result.items():
            if (not isinstance(skill, str) or not skill.strip() or len(skill) > 200
                    or not isinstance(value, int) or isinstance(value, bool)
                    or not 1 <= value <= 16):
                raise RunStoreError("capacity is invalid")
        return result

    @staticmethod
    def _default_pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def _connect(self) -> sqlite3.Connection:
        connection: sqlite3.Connection | None = None
        guard_fd: int | None = None
        try:
            flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
            guard_fd = os.open(self.path, flags, 0o600)
            guard = os.fstat(guard_fd)
            connection = self._connect_factory(self.path, timeout=5, isolation_level=None)
            current = os.stat(self.path, follow_symlinks=False)
            if (not stat.S_ISREG(guard.st_mode) or not stat.S_ISREG(current.st_mode)
                    or stat.S_ISLNK(current.st_mode) or guard.st_dev != current.st_dev
                    or guard.st_ino != current.st_ino or current.st_uid != os.getuid()
                    or current.st_mode & 0o777 != 0o600):
                raise OSError("database guard mismatch")
            os.close(guard_fd)
            guard_fd = None
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("PRAGMA journal_mode = WAL")
            return connection
        except sqlite3.Error as error:
            if connection is not None:
                connection.close()
            if guard_fd is not None:
                os.close(guard_fd)
            raise RunStoreError("database operation failed") from error
        except OSError as error:
            if connection is not None:
                connection.close()
            if guard_fd is not None:
                os.close(guard_fd)
            raise RunStoreError("database operation failed") from error

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, SCHEMA_VERSION):
                raise RunStoreError(f"unsupported schema version {version}")
            for statement in (
                """CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY, project TEXT NOT NULL, skill TEXT NOT NULL,
                    source TEXT NOT NULL CHECK(source IN ('dashboard','scheduled','reconcile')),
                    target TEXT, dedupe_key TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('queued','running','succeeded','failed','cancelled')),
                    queue_sequence INTEGER NOT NULL, pid INTEGER,
                    created_at TEXT NOT NULL, started_at TEXT, heartbeat_at TEXT, finished_at TEXT,
                    phase TEXT NOT NULL, cancel_requested INTEGER NOT NULL DEFAULT 0,
                    error_code TEXT, error_message TEXT, predecessor_run_id TEXT
                )""",
                """CREATE TABLE IF NOT EXISTS project_controls (
                    project TEXT PRIMARY KEY,
                    state TEXT NOT NULL CHECK(state IN ('running','stopped')),
                    generation INTEGER NOT NULL DEFAULT 0
                )""",
                "CREATE UNIQUE INDEX IF NOT EXISTS active_run_dedupe ON runs(project, dedupe_key) WHERE state IN ('queued','running')",
                "CREATE INDEX IF NOT EXISTS queue_by_project_skill ON runs(project, skill, state, queue_sequence)",
            ):
                connection.execute(statement)
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            connection.commit()
        except (sqlite3.Error, RunStoreError) as error:
            connection.rollback()
            if isinstance(error, RunStoreError):
                raise
            raise RunStoreError("database initialization failed") from error
        finally:
            connection.close()
        try:
            database = os.lstat(self.path)
            if not stat.S_ISREG(database.st_mode) or stat.S_ISLNK(database.st_mode):
                raise RunStoreError("database path must be a regular file")
            os.chmod(self.path, 0o600)
        except OSError as error:
            raise RunStoreError("database initialization failed") from error

    def _timestamp(self) -> str:
        value = self._now()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise RunStoreError("now must return a timezone-aware datetime")
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _parse_timestamp(value: Any) -> datetime:
        if not isinstance(value, str):
            raise RunStoreError("database operation failed")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise RunStoreError("database operation failed") from error
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise RunStoreError("database operation failed")
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _public(value: Any, field: str, limit: int = 200) -> str:
        if not isinstance(value, str): raise RunStoreError(f"{field} must be a string")
        value = " ".join(value.split())
        if not value or len(value) > limit: raise RunStoreError(f"{field} is invalid")
        return value

    @staticmethod
    def _row(row: sqlite3.Row | None, created: bool | None = None) -> dict[str, Any] | None:
        if row is None: return None
        value = dict(row)
        if created is not None: value["created"] = created
        return value

    def _control(self, connection: sqlite3.Connection, project: str) -> sqlite3.Row | None:
        return connection.execute("SELECT * FROM project_controls WHERE project = ?", (project,)).fetchone()

    def enqueue(self, *, project: str, skill: str, source: str, target: str | None = None,
                predecessor_run_id: str | None = None) -> dict[str, Any]:
        return self._enqueue(project, skill, source, target, predecessor_run_id, 0)

    def _enqueue(self, project: str, skill: str, source: str, target: str | None,
                 predecessor_run_id: str | None, attempt: int) -> dict[str, Any]:
        project, skill = self._public(project, "project"), self._public(skill, "skill")
        if not isinstance(source, str) or source not in _SOURCES:
            raise RunStoreError("source is invalid")
        if target is not None: target = self._public(target, "target", 1000)
        if predecessor_run_id is not None: predecessor_run_id = self._public(predecessor_run_id, "predecessor_run_id", 64)
        key = f"ticket:{target}" if target is not None else f"scheduled:{skill}"
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            control = self._control(connection, project)
            if control is not None and control["state"] == "stopped": raise RunPaused(f"project {project} is stopped")
            existing = connection.execute("SELECT * FROM runs WHERE project=? AND dedupe_key=? AND state IN ('queued','running')", (project, key)).fetchone()
            if existing:
                connection.commit(); return self._row(existing, False)  # type: ignore[return-value]
            sequence = connection.execute("SELECT COALESCE(MAX(queue_sequence), 0) + 1 FROM runs WHERE project=?", (project,)).fetchone()[0]
            run_id, timestamp = str(uuid.uuid4()), self._timestamp()
            connection.execute("INSERT INTO runs(run_id,project,skill,source,target,dedupe_key,state,queue_sequence,created_at,phase,predecessor_run_id) VALUES(?,?,?,?,?,?, 'queued',?,?, 'En attente',?)", (run_id, project, skill, source, target, key, sequence, timestamp, predecessor_run_id))
            row = connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            connection.commit(); return self._row(row, True)  # type: ignore[return-value]
        except sqlite3.IntegrityError:
            connection.rollback()
            if attempt >= 2:
                raise RunConflict("active run conflict")
            # Retry the entire admission in a fresh transaction: the winner may
            # have become terminal before we reacquire the write lock.
            connection.close()
            return self._enqueue(project, skill, source, target, predecessor_run_id, attempt + 1)
        except sqlite3.Error as error:
            connection.rollback(); raise RunStoreError("database operation failed") from error
        finally: connection.close()

    def get(self, run_id: str) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            row = connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            result = self._row(row)
            if result is not None:
                result["queue_position"] = 0
                if result["state"] == "queued":
                    result["queue_position"] = connection.execute("SELECT COUNT(*) FROM runs WHERE project=? AND skill=? AND state='queued' AND queue_sequence<=?", (result["project"], result["skill"], result["queue_sequence"])).fetchone()[0]
            return result
        except sqlite3.Error as error:
            raise RunStoreError("database operation failed") from error
        finally: connection.close()

    def list_runs(self, project: str, active_only: bool = False) -> list[dict[str, Any]]:
        project = self._public(project, "project")
        sql = "SELECT * FROM runs WHERE project=?" + (" AND state IN ('queued','running')" if active_only else "") + " ORDER BY queue_sequence, run_id"
        connection = self._connect()
        try: return [self._row(row) for row in connection.execute(sql, (project,))]  # type: ignore[misc]
        except sqlite3.Error as error: raise RunStoreError("database operation failed") from error
        finally: connection.close()

    def bind_target(self, run_id: str, target: str) -> dict[str, Any]:
        target = self._public(target, "target", 1000); key = f"ticket:{target}"; connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE"); row = connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if not row or row["state"] not in ACTIVE_STATES: raise RunStateError("run is not active")
            if row["target"] == target: connection.commit(); return self._row(row)  # type: ignore[return-value]
            if row["target"] is not None: raise RunStateError("run already has a target")
            owner = connection.execute("SELECT run_id FROM runs WHERE project=? AND dedupe_key=? AND state IN ('queued','running')", (row["project"], key)).fetchone()
            if owner: raise RunConflict(f"target is owned by {owner['run_id']}")
            connection.execute("UPDATE runs SET target=?, dedupe_key=? WHERE run_id=?", (target, key, run_id)); row = connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone(); connection.commit(); return self._row(row)  # type: ignore[return-value]
        except RunStoreError:
            connection.rollback(); raise
        except sqlite3.Error as error:
            connection.rollback(); raise RunStoreError("database operation failed") from error
        finally: connection.close()

    def claim_ready(self, *, project: str, capacities: Mapping[str, int]) -> list[dict[str, Any]]:
        project = self._public(project, "project")
        capacities = self._capacities(capacities)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            control = self._control(connection, project)
            if control is not None and control["state"] == "stopped": raise RunPaused(f"project {project} is stopped")
            timestamp, claimed = self._timestamp(), []
            for skill, maximum in capacities.items():
                running = connection.execute("SELECT COUNT(*) FROM runs WHERE project=? AND skill=? AND state='running'", (project, skill)).fetchone()[0]
                rows = connection.execute("SELECT run_id FROM runs WHERE project=? AND skill=? AND state='queued' ORDER BY queue_sequence LIMIT ?", (project, skill, max(0, maximum - running))).fetchall()
                for row in rows:
                    connection.execute("UPDATE runs SET state='running', started_at=?, heartbeat_at=?, phase='Démarrage' WHERE run_id=?", (timestamp, timestamp, row["run_id"]))
                    claimed.append(self._row(connection.execute("SELECT * FROM runs WHERE run_id=?", (row["run_id"],)).fetchone()))
            connection.commit(); return sorted(claimed, key=lambda row: (row["queue_sequence"], row["run_id"]))  # type: ignore[index]
        except RunStoreError:
            connection.rollback(); raise
        except sqlite3.Error as error:
            connection.rollback(); raise RunStoreError("database operation failed") from error
        finally: connection.close()

    def mark_pid(self, run_id: str, pid: int) -> dict[str, Any]:
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0: raise RunStoreError("pid must be a positive integer")
        return self._update_running(run_id, "pid=?", (pid,))

    def heartbeat(self, run_id: str, phase: str) -> dict[str, Any]:
        return self._update_running(run_id, "heartbeat_at=?, phase=?", (self._timestamp(), self._public(phase, "phase")))

    def _update_running(self, run_id: str, expression: str, values: tuple[Any, ...]) -> dict[str, Any]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE"); changed = connection.execute(f"UPDATE runs SET {expression} WHERE run_id=? AND state='running'", (*values, run_id)).rowcount
            if not changed: raise RunStateError("run is not running")
            row = connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone(); connection.commit(); return self._row(row)  # type: ignore[return-value]
        except RunStoreError:
            connection.rollback(); raise
        except sqlite3.Error as error:
            connection.rollback(); raise RunStoreError("database operation failed") from error
        finally: connection.close()

    def finish(self, run_id: str, *, state: str, error_code: str | None = None, error_message: str | None = None) -> dict[str, Any]:
        if state not in TERMINAL_STATES: raise RunStateError("finish state must be terminal")
        if error_code is not None: error_code = self._public(error_code, "error_code")
        if error_message is not None: error_message = self._public(error_message, "error_message", 2048)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE"); timestamp = self._timestamp()
            phases = {"succeeded": "Terminé", "failed": "Échec", "cancelled": "Annulé"}
            changed = connection.execute("UPDATE runs SET state=?, finished_at=?, heartbeat_at=?, phase=?, error_code=?, error_message=? WHERE run_id=? AND state IN ('queued','running')", (state, timestamp, timestamp, phases[state], error_code, error_message, run_id)).rowcount
            if not changed: raise RunStateError("run cannot be finished")
            row = connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone(); connection.commit(); return self._row(row)  # type: ignore[return-value]
        except RunStoreError:
            connection.rollback(); raise
        except sqlite3.Error as error:
            connection.rollback(); raise RunStoreError("database operation failed") from error
        finally: connection.close()

    def set_project_state(self, project: str, state: str) -> dict[str, Any]:
        project = self._public(project, "project")
        if not isinstance(state, str) or state not in {"running", "stopped"}:
            raise RunStoreError("project state is invalid")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE"); connection.execute("INSERT INTO project_controls(project,state,generation) VALUES(?,?,1) ON CONFLICT(project) DO UPDATE SET state=excluded.state,generation=project_controls.generation+1", (project, state)); row = self._control(connection, project); connection.commit(); return self._row(row)  # type: ignore[return-value]
        except sqlite3.Error as error: connection.rollback(); raise RunStoreError("database operation failed") from error
        finally: connection.close()

    def reconcile(self, project: str) -> list[dict[str, Any]]:
        project = self._public(project, "project"); now = self._parse_timestamp(self._timestamp()); connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE"); failed = []
            for row in connection.execute("SELECT * FROM runs WHERE project=? AND state='running'", (project,)).fetchall():
                # A live PID can have been reused; a fresh heartbeat is the authority.
                heartbeat = self._parse_timestamp(row["heartbeat_at"])
                dead = row["pid"] is not None and not self._pid_alive(row["pid"])
                if dead or now - heartbeat > STALE_HEARTBEAT:
                    connection.execute("UPDATE runs SET state='failed', finished_at=?, phase='Interrompu', error_code='interrupted', error_message='run interrupted during reconciliation' WHERE run_id=?", (self._timestamp(), row["run_id"])); failed.append(self._row(connection.execute("SELECT * FROM runs WHERE run_id=?", (row["run_id"],)).fetchone()))
            connection.commit(); return failed  # type: ignore[return-value]
        except RunStoreError:
            connection.rollback(); raise
        except sqlite3.Error as error:
            connection.rollback(); raise RunStoreError("database operation failed") from error
        finally: connection.close()

    def snapshot(self, project: str, capacities: Mapping[str, int]) -> dict[str, Any]:
        project = self._public(project, "project")
        capacities = self._capacities(capacities)
        connection = self._connect()
        try:
            connection.execute("BEGIN")
            control = self._control(connection, project)
            runs = [self._row(row) for row in connection.execute("SELECT * FROM runs WHERE project=? ORDER BY queue_sequence, run_id", (project,))]
            connection.commit()
        except sqlite3.Error as error:
            connection.rollback()
            raise RunStoreError("database operation failed") from error
        finally:
            connection.close()
        queued_positions: dict[str, int] = {}; output = []
        for run in runs:
            copy = dict(run)
            if copy["state"] == "queued": queued_positions[copy["skill"]] = queued_positions.get(copy["skill"], 0) + 1; copy["queue_position"] = queued_positions[copy["skill"]]
            else: copy["queue_position"] = 0
            output.append(copy)
        skills = set(capacities) | {run["skill"] for run in runs}; capacity = {}
        for skill in sorted(skills):
            maximum = capacities.get(skill, 0)
            capacity[skill] = {"running": sum(run["skill"] == skill and run["state"] == "running" for run in runs), "queued": sum(run["skill"] == skill and run["state"] == "queued" for run in runs), "max_concurrent": maximum}
        return {"project": project, "state": control["state"] if control else "running", "generation": control["generation"] if control else 0, "runs": output, "capacity": capacity, "has_active": any(run["state"] in ACTIVE_STATES for run in runs)}

    def request_cancel(self, project: str) -> list[dict[str, Any]]:
        project = self._public(project, "project"); connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE"); connection.execute("UPDATE runs SET cancel_requested=1 WHERE project=? AND state IN ('queued','running')", (project,)); rows = connection.execute("SELECT * FROM runs WHERE project=? AND state IN ('queued','running') ORDER BY queue_sequence", (project,)).fetchall(); connection.commit(); return [self._row(row) for row in rows]  # type: ignore[misc]
        except sqlite3.Error as error: connection.rollback(); raise RunStoreError("database operation failed") from error
        finally: connection.close()

    def purge(self, project: str) -> int:
        project = self._public(project, "project"); cutoff = (self._parse_timestamp(self._timestamp()) - RETENTION).isoformat().replace("+00:00", "Z"); connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE"); count = connection.execute("DELETE FROM runs WHERE project=? AND state IN ('succeeded','failed','cancelled') AND finished_at < ?", (project, cutoff)).rowcount; connection.commit(); return count
        except sqlite3.Error as error: connection.rollback(); raise RunStoreError("database operation failed") from error
        finally: connection.close()
