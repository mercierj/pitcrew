"""Durable SQLite queue for Pitcrew ticket runs."""
from __future__ import annotations

import os
import re
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
        parent_fd = self._open_parent(create=True)
        try:
            _, name = self._database_location()
            database = os.stat(
                name,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return
        except OSError as error:
            raise RunStoreError("database operation failed") from error
        finally:
            os.close(parent_fd)
        if stat.S_ISLNK(database.st_mode):
            raise RunStoreError("database path must not be a symlink")
        if not stat.S_ISREG(database.st_mode):
            raise RunStoreError("database path must be a regular file")

    def _database_location(self) -> tuple[Path, str]:
        absolute = Path(os.path.abspath(self.path))
        if not absolute.anchor or not absolute.name:
            raise RunStoreError("database path is invalid")
        return absolute, absolute.name

    @staticmethod
    def _open_directory(parent_fd: int, name: str) -> int:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        try:
            return os.open(name, flags, dir_fd=parent_fd)
        except OSError as error:
            try:
                metadata = os.stat(
                    name,
                    dir_fd=parent_fd,
                    follow_symlinks=False,
                )
            except OSError:
                raise error
            if stat.S_ISLNK(metadata.st_mode):
                raise RunStoreError("database path contains a symlink") from error
            if not stat.S_ISDIR(metadata.st_mode):
                raise RunStoreError("database ancestor is not a directory") from error
            raise error

    def _open_parent(self, *, create: bool) -> int:
        absolute, _ = self._database_location()
        flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        current_fd: int | None = None
        try:
            current_fd = os.open(absolute.anchor, flags)
            for part in absolute.parent.parts[1:]:
                try:
                    child_fd = self._open_directory(current_fd, part)
                except FileNotFoundError:
                    if not create:
                        raise
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=current_fd)
                    except FileExistsError:
                        pass
                    child_fd = self._open_directory(current_fd, part)
                os.close(current_fd)
                current_fd = child_fd
            metadata = os.fstat(current_fd)
            if metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
                raise RunStoreError("database parent must be private")
            result = current_fd
            current_fd = None
            return result
        except OSError as error:
            raise RunStoreError("database operation failed") from error
        finally:
            if current_fd is not None:
                os.close(current_fd)

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

    def _connect(self, *, enable_wal: bool = True) -> sqlite3.Connection:
        connection: sqlite3.Connection | None = None
        guard_fd: int | None = None
        parent_fd: int | None = None
        connected = False
        try:
            absolute, name = self._database_location()
            parent_fd = self._open_parent(create=False)
            flags = (
                os.O_RDWR
                | os.O_CREAT
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0)
            )
            guard_fd = os.open(name, flags, 0o600, dir_fd=parent_fd)
            guard = os.fstat(guard_fd)
            connection = self._connect_factory(self.path, timeout=5, isolation_level=None)
            relative = os.stat(
                name,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
            public = os.stat(absolute, follow_symlinks=False)
            identities = {
                (guard.st_dev, guard.st_ino),
                (relative.st_dev, relative.st_ino),
                (public.st_dev, public.st_ino),
            }
            if (not stat.S_ISREG(guard.st_mode)
                    or not stat.S_ISREG(relative.st_mode)
                    or not stat.S_ISREG(public.st_mode)
                    or len(identities) != 1
                    or guard.st_uid != os.getuid()
                    or relative.st_uid != os.getuid()
                    or public.st_uid != os.getuid()
                    or stat.S_IMODE(guard.st_mode) != 0o600
                    or stat.S_IMODE(relative.st_mode) != 0o600
                    or stat.S_IMODE(public.st_mode) != 0o600):
                raise OSError("database guard mismatch")
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 5000")
            if enable_wal:
                journal_mode = connection.execute(
                    "PRAGMA journal_mode = WAL"
                ).fetchone()[0]
                if str(journal_mode).lower() != "wal":
                    raise sqlite3.OperationalError("could not enable WAL")
            connected = True
            return connection
        except RunStoreError:
            raise
        except Exception as error:
            raise RunStoreError("database operation failed") from error
        finally:
            if not connected and connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass
            for descriptor in (guard_fd, parent_fd):
                if descriptor is not None:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass

    def _initialize(self) -> None:
        connection = self._connect(enable_wal=False)
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
            self._validate_schema(connection)
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            connection.commit()
            journal_mode = connection.execute(
                "PRAGMA journal_mode = WAL"
            ).fetchone()[0]
            if str(journal_mode).lower() != "wal":
                raise RunStoreError("database initialization failed")
        except (sqlite3.Error, RunStoreError) as error:
            connection.rollback()
            if isinstance(error, RunStoreError):
                raise
            raise RunStoreError("database initialization failed") from error
        finally:
            connection.close()
    @staticmethod
    def _validate_schema(connection: sqlite3.Connection) -> None:
        expected_tables = {
            "runs": (
                ("run_id", "TEXT", 0, 1, None),
                ("project", "TEXT", 1, 0, None),
                ("skill", "TEXT", 1, 0, None),
                ("source", "TEXT", 1, 0, None),
                ("target", "TEXT", 0, 0, None),
                ("dedupe_key", "TEXT", 1, 0, None),
                ("state", "TEXT", 1, 0, None),
                ("queue_sequence", "INTEGER", 1, 0, None),
                ("pid", "INTEGER", 0, 0, None),
                ("created_at", "TEXT", 1, 0, None),
                ("started_at", "TEXT", 0, 0, None),
                ("heartbeat_at", "TEXT", 0, 0, None),
                ("finished_at", "TEXT", 0, 0, None),
                ("phase", "TEXT", 1, 0, None),
                ("cancel_requested", "INTEGER", 1, 0, "0"),
                ("error_code", "TEXT", 0, 0, None),
                ("error_message", "TEXT", 0, 0, None),
                ("predecessor_run_id", "TEXT", 0, 0, None),
            ),
            "project_controls": (
                ("project", "TEXT", 0, 1, None),
                ("state", "TEXT", 1, 0, None),
                ("generation", "INTEGER", 1, 0, "0"),
            ),
        }
        for table, expected in expected_tables.items():
            actual = tuple(
                (
                    row["name"],
                    row["type"].upper(),
                    row["notnull"],
                    row["pk"],
                    row["dflt_value"],
                )
                for row in connection.execute(f"PRAGMA table_info({table})")
            )
            if actual != expected:
                raise RunStoreError(f"database schema mismatch for {table}")

        expected_checks = {
            ("runs", "source"): ("dashboard", "scheduled", "reconcile"),
            ("runs", "state"): ALL_STATES,
            ("project_controls", "state"): ("running", "stopped"),
        }
        for (table, column), expected_values in expected_checks.items():
            table_row = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            if table_row is None or not isinstance(table_row["sql"], str):
                raise RunStoreError(f"database schema mismatch for {table}")
            identifier = (
                rf'(?:{re.escape(column)}|"{re.escape(column)}"|'
                rf"`{re.escape(column)}`|\[{re.escape(column)}\])"
            )
            bodies = re.findall(
                rf"check\s*\(\s*{identifier}\s+in\s*\(([^()]*)\)\s*\)",
                table_row["sql"],
                flags=re.IGNORECASE,
            )
            found = False
            for body in bodies:
                values: list[str] = []
                for token in body.split(","):
                    match = re.fullmatch(r"""\s*(['"])(.*?)\1\s*""", token)
                    if match is None:
                        break
                    values.append(match.group(2))
                else:
                    if tuple(values) == expected_values:
                        found = True
                        break
            if not found:
                raise RunStoreError(
                    f"database schema mismatch for {table}.{column} check"
                )

        indexes = {
            row["name"]: row
            for row in connection.execute("PRAGMA index_list(runs)")
        }
        active = indexes.get("active_run_dedupe")
        queue = indexes.get("queue_by_project_skill")
        if (
            active is None
            or (active["unique"], active["partial"]) != (1, 1)
            or queue is None
            or (queue["unique"], queue["partial"]) != (0, 0)
        ):
            raise RunStoreError("database schema mismatch for run indexes")
        for name, expected_columns in (
            ("active_run_dedupe", ("project", "dedupe_key")),
            (
                "queue_by_project_skill",
                ("project", "skill", "state", "queue_sequence"),
            ),
        ):
            columns = tuple(
                row["name"]
                for row in connection.execute(f"PRAGMA index_info({name})")
            )
            if columns != expected_columns:
                raise RunStoreError(f"database schema mismatch for index {name}")
        active_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
            ("active_run_dedupe",),
        ).fetchone()
        if active_sql is None or not isinstance(active_sql["sql"], str):
            raise RunStoreError("database schema mismatch for active run index")
        normalized = re.sub(r"\s+", "", active_sql["sql"]).lower()
        _, separator, predicate = normalized.partition("where")
        if separator != "where" or predicate != "statein('queued','running')":
            raise RunStoreError("database schema mismatch for active run index")

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

    @classmethod
    def _public_error(cls, value: Any) -> str:
        cls._public(value, "error_message", 2048)
        scrubbed = re.sub(
            r"(?im)(\b(?:Authorization|X-API-Key)\s*:\s*)[^\r\n]+",
            r"\1[REDACTED]",
            value,
        )
        scrubbed = re.sub(
            (
                r'(?i)("?\b(?:token|authorization|password|secret|'
                r'api[_-]?key|access[_-]?token)\b"?\s*'
                r'(?:=|:)\s*)(?:Bearer\s+)?(?:"[^"]*"|\'[^\']*\'|[^\s,}]+)'
            ),
            r"\1[REDACTED]",
            scrubbed,
        )
        return " ".join(scrubbed.split())

    @staticmethod
    def _is_active_dedupe_conflict(error: sqlite3.IntegrityError) -> bool:
        return (
            getattr(error, "sqlite_errorname", None) == "SQLITE_CONSTRAINT_UNIQUE"
            and str(error)
            == "UNIQUE constraint failed: runs.project, runs.dedupe_key"
        )

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
        except sqlite3.IntegrityError as error:
            connection.rollback()
            if not self._is_active_dedupe_conflict(error):
                raise RunStoreError("database operation failed") from error
            if attempt >= 2:
                raise RunConflict("active run conflict") from error
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
                    result["queue_position"] = connection.execute("SELECT COUNT(*) FROM runs WHERE project=? AND skill=? AND state='queued' AND queue_sequence<?", (result["project"], result["skill"], result["queue_sequence"])).fetchone()[0]
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
            try:
                changed = connection.execute(
                    "UPDATE runs SET target=?, dedupe_key=? WHERE run_id=? AND target IS NULL AND state IN ('queued','running')",
                    (target, key, run_id),
                ).rowcount
            except sqlite3.IntegrityError as error:
                if not self._is_active_dedupe_conflict(error):
                    raise RunStoreError("database operation failed") from error
                owner = connection.execute("SELECT run_id FROM runs WHERE project=? AND dedupe_key=? AND state IN ('queued','running')", (row["project"], key)).fetchone()
                if owner is None:
                    raise RunStoreError("database operation failed") from error
                raise RunConflict(f"target is owned by {owner['run_id']}") from error
            if changed != 1: raise RunStateError("run is not active or already has a target")
            row = connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone(); connection.commit(); return self._row(row)  # type: ignore[return-value]
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

    def update_phase(self, run_id: str, phase: str) -> dict[str, Any]:
        return self._update_running(run_id, "phase=?", (self._public(phase, "phase"),))

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
        if error_message is not None: error_message = self._public_error(error_message)
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
            copy = {
                field: run[field]
                for field in ("run_id", "project", "skill", "target", "state")
            }
            if copy["state"] == "queued":
                copy["queue_position"] = queued_positions.get(copy["skill"], 0)
                queued_positions[copy["skill"]] = copy["queue_position"] + 1
            else:
                copy["queue_position"] = 0
            output.append(copy)
        skills = set(capacities) | {run["skill"] for run in runs}; capacity = {}
        for skill in sorted(skills):
            maximum = capacities.get(skill, 0)
            capacity[skill] = {"running": sum(run["skill"] == skill and run["state"] == "running" for run in runs), "queued": sum(run["skill"] == skill and run["state"] == "queued" for run in runs), "max_concurrent": maximum}
        return {"project": project, "state": control["state"] if control else "running", "generation": control["generation"] if control else 0, "runs": output, "capacity": capacity, "has_active": any(run["state"] in ACTIVE_STATES for run in runs)}

    def request_cancel(self, project: str) -> list[dict[str, Any]]:
        project = self._public(project, "project"); connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE"); connection.execute("UPDATE runs SET cancel_requested=1 WHERE project=? AND state='running'", (project,)); rows = connection.execute("SELECT * FROM runs WHERE project=? AND state='running' ORDER BY queue_sequence", (project,)).fetchall(); connection.commit(); return [self._row(row) for row in rows]  # type: ignore[misc]
        except sqlite3.Error as error: connection.rollback(); raise RunStoreError("database operation failed") from error
        finally: connection.close()

    def purge(self, project: str) -> int:
        project = self._public(project, "project"); cutoff = (self._parse_timestamp(self._timestamp()) - RETENTION).isoformat().replace("+00:00", "Z"); connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE"); count = connection.execute("DELETE FROM runs WHERE project=? AND state IN ('succeeded','failed','cancelled') AND finished_at < ?", (project, cutoff)).rowcount; connection.commit(); return count
        except sqlite3.Error as error: connection.rollback(); raise RunStoreError("database operation failed") from error
        finally: connection.close()
