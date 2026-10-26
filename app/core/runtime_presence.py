"""Opt-in, same-host runtime presence; never a queue/provider health claim.

The API and sole runtime must use the same trusted local POSIX directory and
kernel lock namespace. This is not a distributed lease or an authorization gate.
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import math
import os
import re
import stat
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

HEARTBEAT_SECONDS = 15
STALE_SECONDS = 60
MAX_RECORD_BYTES = 4096


class RuntimePresenceError(RuntimeError):
    """Content-free runtime observation/ownership failure."""


def _open_regular(path: Path, flags: int):
    """Refuse symlinks/FIFOs/devices; a bad local path must not hang a probe."""
    fd = os.open(path, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("runtime_presence_not_regular")
        return os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("runtime_presence_duplicate_key")
        result[key] = value
    return result


class RuntimePresence:
    """Hold one local owner lock from before bootstrap until final shutdown."""

    def __init__(self, directory: Path, *, scheduler_enabled: bool):
        self.directory = directory
        self.scheduler_enabled = scheduler_enabled
        self.runtime_id = uuid.uuid4().hex
        self._lock = None

    def __enter__(self):
        try:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            self._lock = _open_regular(self.directory / "owner.lock", os.O_RDWR | os.O_CREAT)
            os.fchmod(self._lock.fileno(), 0o600)
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            if self._lock is not None:
                self._lock.close()
                self._lock = None
            raise RuntimePresenceError("runtime_presence_ownership_unavailable") from None
        try:
            self.write("starting")
        except BaseException:
            self._lock.close()
            self._lock = None
            raise
        return self

    def write(self, phase: str) -> None:
        """Replace a bounded record atomically, keeping the lock inode stable."""
        if self._lock is None or phase not in ("starting", "running", "stopped"):
            raise RuntimePresenceError("runtime_presence_not_owned")
        record = dict(
            version=1,
            runtime_id=self.runtime_id,
            phase=phase,
            heartbeat_at=time.time(),
            scheduler_enabled=self.scheduler_enabled,
        )
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.directory, delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(record, stream)
            os.replace(temporary, self.directory / "presence.json")
        except OSError:
            raise RuntimePresenceError("runtime_presence_write_failed") from None
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    raise RuntimePresenceError("runtime_presence_cleanup_failed") from None

    async def heartbeat(self) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            self.write("running")

    def __exit__(self, *_exc):
        try:
            self.write("stopped")
        except RuntimePresenceError:
            # Releasing ownership is stronger absence evidence than the record.
            pass
        finally:
            if self._lock is not None:
                self._lock.close()
                self._lock = None


def runtime_signal(directory: Path | None, *, now: float | None = None) -> dict:
    """Read only; missing/inaccessible/invalid evidence never becomes green.

    A held lock and a recent running record in this same local directory prove
    only event-loop presence. Do not infer component progress, DB or delivery.
    """
    result = {"component": "app.runtime", "state": "unconfigured"}
    if directory is None:
        return result
    try:
        with _open_regular(directory / "owner.lock", os.O_RDONLY) as owner:
            try:
                fcntl.flock(owner, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                pass
            else:
                # Shared probe locks do not masquerade as a runtime owner
                # to concurrent readers. Close releases it; never unlink it.
                return {**result, "state": "stopped"}
            with _open_regular(directory / "presence.json", os.O_RDONLY) as stream:
                raw = stream.read(MAX_RECORD_BYTES + 1)
            if len(raw) > MAX_RECORD_BYTES:
                return {**result, "state": "invalid"}
            record = json.loads(raw, object_pairs_hook=_unique_object)
    except FileNotFoundError:
        return {**result, "state": "missing"}
    except (OSError, ValueError, UnicodeError, RecursionError):
        return {**result, "state": "unavailable"}
    if (
        not isinstance(record, dict)
        or set(record) != {"version", "runtime_id", "phase", "heartbeat_at", "scheduler_enabled"}
        or type(record["version"]) is not int
        or record["version"] != 1
        or not isinstance(record["runtime_id"], str)
        or not re.fullmatch(r"[0-9a-f]{32}", record["runtime_id"])
        or record["phase"] not in ("starting", "running", "stopped")
        or type(record["scheduler_enabled"]) is not bool
        or type(record["heartbeat_at"]) not in (int, float)
    ):
        return {**result, "state": "invalid"}
    stamp = record["heartbeat_at"]
    current = time.time() if now is None else now
    if not 0 <= stamp <= current or not math.isfinite(stamp):
        return {**result, "state": "invalid"}
    age = current - stamp
    state = record["phase"]
    if age > STALE_SECONDS:
        state = "stale"
    elif state == "running":
        state = "fresh"
    return {
        **result,
        "state": state,
        "runtime_id": record["runtime_id"],
        "heartbeat_at": datetime.fromtimestamp(stamp, timezone.utc).isoformat(),
        "age_seconds": round(age, 3),
        "scheduler_enabled": record["scheduler_enabled"],
    }


@contextmanager
def runtime_owner(directory: Path | None, *, scheduler_enabled: bool):
    """Leave existing deployments unchanged until the local signal is enabled."""
    if directory is None:
        yield None
    else:
        with RuntimePresence(directory, scheduler_enabled=scheduler_enabled) as owner:
            yield owner
