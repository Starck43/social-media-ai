"""Durable device-scoped digest-bound replay ledger with clock-rollback and capacity fences."""

from __future__ import annotations

try:
    import fcntl
except ImportError:  # pragma: no cover - unsupported platforms fail closed below
    fcntl = None
import os
import sqlite3
import stat
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ..index import IndexStore
from .models import REPLAY_CAPACITY, REPLAY_SECONDS, SearchProtocolError


@dataclass(frozen=True)
class ReplayDecision:
    """Outcome of a replay reservation or duplicate-request lookup."""

    status: Literal["reserved", "duplicate"]
    state: Literal["in_flight", "completed", "unknown"]
    response_json: str | None = None
    device_epoch: int = 0
    consent_epoch: int = 0
    policy_epoch: int = 0


class ReplayLedger:
    """Persistent digest-bound replay ledger scoped to device_id."""

    def __init__(
        self,
        database_path: Path | str | None = None,
        *,
        store: IndexStore | None = None,
        capacity: int = REPLAY_CAPACITY,
        ttl_seconds: int = REPLAY_SECONDS,
        clock_skew_seconds: int = 5,
    ) -> None:
        if (database_path is None) == (store is None):
            raise ValueError("specify exactly one of database_path or store")
        if capacity <= 0 or ttl_seconds <= 0 or clock_skew_seconds < 0:
            raise ValueError("invalid replay ledger bounds")
        self.capacity = capacity
        self.ttl_seconds = ttl_seconds
        self.clock_skew_seconds = clock_skew_seconds
        self._owns_connection = store is None
        self._closed = False
        self._lock_fd: int | None = None

        if store is not None:
            self.lock = store.lock
            self.connection = store.connection
            self.database_path = store.database_path
            with self.lock, self.connection:
                self._create_schema()
            return

        if os.name != "posix" or fcntl is None or not hasattr(os, "O_NOFOLLOW"):
            raise PermissionError("private replay storage is unavailable on this platform")
        self.database_path = Path(os.path.abspath(Path(database_path).expanduser()))
        IndexStore._prepare_private_parent(self.database_path.parent)
        if self.database_path.is_symlink():
            raise PermissionError("replay ledger database must not be a symbolic link")
        if self.database_path.exists():
            info = self.database_path.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise PermissionError("existing replay ledger database is not owner-private")
        else:
            fd = os.open(
                self.database_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                0o600,
            )
            os.close(fd)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{self.database_path}{suffix}")
            if sidecar.is_symlink():
                raise PermissionError("replay ledger sidecar must not be a symbolic link")
            if sidecar.exists():
                info = sidecar.stat()
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
                    raise PermissionError("existing replay ledger sidecar is not owner-private")

        self.lock = threading.RLock()
        lock_path = Path(f"{self.database_path}.lock")
        if lock_path.is_symlink():
            raise PermissionError("replay ledger lock must not be a symbolic link")
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        lock_info = os.fstat(lock_fd)
        if lock_info.st_uid != os.getuid() or stat.S_IMODE(lock_info.st_mode) & 0o077:
            os.close(lock_fd)
            raise PermissionError("replay ledger lock is not owner-private")
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(lock_fd)
            raise PermissionError("replay ledger is already in use") from exc
        self._lock_fd = lock_fd

        try:
            self.connection = sqlite3.connect(self.database_path, check_same_thread=False)
            self.database_path.chmod(0o600)
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA foreign_keys = ON")
            self.connection.execute("PRAGMA secure_delete = ON")
            self.connection.execute("PRAGMA journal_mode = WAL")
            with self.connection:
                self._create_schema()
        except BaseException:
            if hasattr(self, "connection"):
                self.connection.close()
            if self._lock_fd is not None:
                os.close(self._lock_fd)
                self._lock_fd = None
            raise

    def _create_schema(self) -> None:
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS command_replay_ledger (
                device_id INTEGER NOT NULL,
                request_id TEXT NOT NULL,
                request_digest TEXT NOT NULL,
                operation TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('in_flight', 'completed', 'unknown')),
                issued_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                response_json TEXT,
                device_epoch INTEGER NOT NULL,
                consent_epoch INTEGER NOT NULL,
                policy_epoch INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                PRIMARY KEY (device_id, request_id)
            );
            CREATE INDEX IF NOT EXISTS ix_replay_expiry
                ON command_replay_ledger(device_id, expires_at);
            CREATE TABLE IF NOT EXISTS replay_clock_state (
                device_id INTEGER PRIMARY KEY,
                last_wall_time INTEGER NOT NULL
            );
            """)

    def close(self) -> None:
        with self.lock:
            if self._closed or not self._owns_connection:
                self._closed = True
                return
            try:
                self.connection.close()
            finally:
                if self._lock_fd is not None:
                    os.close(self._lock_fd)
                    self._lock_fd = None
                self._closed = True

    def reserve(
        self,
        *,
        device_id: int,
        request_id: str,
        digest: str,
        operation: str,
        issued_at: int,
        device_epoch: int,
        consent_epoch: int,
        policy_epoch: int,
        now: int | None = None,
    ) -> ReplayDecision:
        clock = int(time.time()) if now is None else now
        if type(clock) is not int or clock <= 0:
            raise SearchProtocolError("unavailable")

        with self.lock:
            if self._closed:
                raise SearchProtocolError("unavailable")
            with self.connection:
                clock_row = self.connection.execute(
                    "SELECT last_wall_time FROM replay_clock_state WHERE device_id = ?",
                    (device_id,),
                ).fetchone()
                if clock_row is not None:
                    last_wall = int(clock_row[0])
                    if clock < last_wall - self.clock_skew_seconds:
                        raise SearchProtocolError("unavailable")
                    if clock > last_wall:
                        self.connection.execute(
                            "UPDATE replay_clock_state SET last_wall_time = ? WHERE device_id = ?",
                            (clock, device_id),
                        )
                else:
                    self.connection.execute(
                        "INSERT INTO replay_clock_state(device_id, last_wall_time) VALUES (?, ?)",
                        (device_id, clock),
                    )

                self.connection.execute(
                    "DELETE FROM command_replay_ledger WHERE device_id = ? AND expires_at <= ?",
                    (device_id, clock),
                )

                existing = self.connection.execute(
                    """
                    SELECT request_digest, state, response_json, device_epoch, consent_epoch, policy_epoch
                    FROM command_replay_ledger
                    WHERE device_id = ? AND request_id = ?
                    """,
                    (device_id, request_id),
                ).fetchone()
                if existing is not None:
                    if str(existing["request_digest"]) != digest:
                        raise SearchProtocolError("denied")
                    return ReplayDecision(
                        status="duplicate",
                        state=str(existing["state"]),  # type: ignore[arg-type]
                        response_json=str(existing["response_json"]) if existing["response_json"] is not None else None,
                        device_epoch=int(existing["device_epoch"]),
                        consent_epoch=int(existing["consent_epoch"]),
                        policy_epoch=int(existing["policy_epoch"]),
                    )

                count_row = self.connection.execute(
                    "SELECT COUNT(*) FROM command_replay_ledger WHERE device_id = ?",
                    (device_id,),
                ).fetchone()
                if int(count_row[0]) >= self.capacity:
                    raise SearchProtocolError("limit_exceeded")

                self.connection.execute(
                    """
                    INSERT INTO command_replay_ledger(
                        device_id, request_id, request_digest, operation, state,
                        issued_at, expires_at, response_json,
                        device_epoch, consent_epoch, policy_epoch, created_at
                    ) VALUES (?, ?, ?, ?, 'in_flight', ?, ?, NULL, ?, ?, ?, ?)
                    """,
                    (
                        device_id,
                        request_id,
                        digest,
                        operation,
                        issued_at,
                        clock + self.ttl_seconds,
                        device_epoch,
                        consent_epoch,
                        policy_epoch,
                        clock,
                    ),
                )
        return ReplayDecision(
            status="reserved",
            state="in_flight",
            device_epoch=device_epoch,
            consent_epoch=consent_epoch,
            policy_epoch=policy_epoch,
        )

    def complete(self, *, device_id: int, request_id: str, digest: str, response_json: str) -> None:
        with self.lock:
            if self._closed:
                return
            with self.connection:
                self.connection.execute(
                    """
                    UPDATE command_replay_ledger
                    SET state = 'completed', response_json = ?
                    WHERE device_id = ? AND request_id = ? AND request_digest = ? AND state = 'in_flight'
                    """,
                    (response_json, device_id, request_id, digest),
                )

    def mark_unknown(self, *, device_id: int, request_id: str, digest: str) -> None:
        with self.lock:
            if self._closed:
                return
            with self.connection:
                self.connection.execute(
                    """
                    UPDATE command_replay_ledger
                    SET state = 'unknown', response_json = NULL
                    WHERE device_id = ? AND request_id = ? AND request_digest = ?
                    """,
                    (device_id, request_id, digest),
                )

    def invalidate_device(self, device_id: int) -> None:
        """Invalidate stored replay results after device revocation or epoch advance."""

        with self.lock:
            if self._closed:
                return
            with self.connection:
                self.connection.execute(
                    """
                    UPDATE command_replay_ledger
                    SET state = 'unknown', response_json = NULL
                    WHERE device_id = ?
                    """,
                    (device_id,),
                )
