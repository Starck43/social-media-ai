"""Versioned-slot connector installer, automatic update feed and verified rollback.

Layout beneath ``root`` (every component is opened one level at a time with
``O_NOFOLLOW|O_DIRECTORY`` from an ``O_NOFOLLOW`` root dirfd, so no ancestor can
be retargeted while its children are resolved)::

    state.json          fsynced pointer record: active/previous/high water/slots
    lock                flock held for the whole manager session
    objects/ab/<slot>   retained signed archive and release identity
    slots/<digest>/     extracted release; digest = sha256(tagged manifest+sig+archive)
    staging/            per-update scratch (exclusive), pruned when stale

Invariants:

* the trust anchor is supplied by bundled configuration only; nothing here
  provisions a key, a feed or a production endpoint;
* :mod:`local_connector.packaging.updates` authenticates the exact manifest
  bytes before parsing, and the *same* in-memory archive bytes are digested,
  retained and extracted - no path is re-read after verification;
* the high-water version only rises, so a rollback can never re-open a downgrade;
* rollback may only target the recorded, signed, previously installed slot; its
  archived identity is re-authenticated at use even when the feed lifetime has
  expired, and an expired feed can never introduce a *new* installation;
* the pointer flips only after the staged frozen launcher passes the signed
  ``--self-test`` health probe; any failure leaves the current slot, index and
  data untouched;
* Windows fails closed until the sibling ``windows_fs`` integration lands; no
  ``chmod``-only ACL emulation is attempted.
"""

from __future__ import annotations

import errno

try:
    import fcntl
except ImportError:  # Windows must import safely and deny unsupported installation.
    fcntl = None
import hashlib
import json
import os
import re
import ssl
import stat
import struct
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from local_connector.packaging import updates
from local_connector.paths import _open_directory

__all__ = ["UpdateManager", "UpdaterError", "UpdateRejected", "MAX_SLOTS"]

UpdateRejected = updates.UpdateRejected

# ZIP bounds, defence in depth on top of the signed artifact size bound.
MAX_ENTRIES = 4096
MAX_ENTRY_BYTES = 256 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_SLOTS = 3
MAX_STATE_BYTES = 64 * 1024
SELF_TEST_TIMEOUT_SECONDS = 120
STATE_SCHEMA = 1
LAUNCHER = "ai-assistant-connector"
SELF_TEST_FLAG = "--self-test"
# Members shipped without the unix execute bit (frozen macOS app-bundle output
# keeps its COFF signature there); only these become private 0o755 at extraction.
NON_EXECUTABLE_SUFFIXES = ()
TOLERATED_ERRNOS = frozenset({errno.ELOOP, errno.ENOTDIR, errno.EXDEV, errno.EMLINK, errno.ENOTEMPTY})

_STAGING = re.compile(r"stage-[0-9a-f]{32}")
_SLOT = re.compile(r"[0-9a-f]{64}")
_DRIVE = re.compile(r"^[A-Za-z]:")

# Fixed read-only HTTPS feed policy.
DEFAULT_TIMEOUT_SECONDS = 30.0


class UpdaterError(ValueError):
    """Fixed, sanitized updater denial; never echoes attacker content."""


def _fsync_dir(fd: int) -> None:
    try:
        os.fsync(fd)
    except OSError as exc:  # some filesystems reject directory fsync
        if exc.errno not in (errno.EINVAL, errno.ENOTSUP, errno.EBADF):
            raise


def _oserror(exc: OSError) -> UpdaterError:
    reason = getattr(exc, "reason", None)
    if isinstance(reason, str) and reason and exc.errno in TOLERATED_ERRNOS:
        return UpdaterError(reason)
    return UpdaterError("update state access denied")


def _open_dir(parent: int, name: str) -> int:
    return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)


# --------------------------------------------------------------------------- safe tree


class _Tree:
    """Owner-private ``root`` bound to one verified directory fd for the session."""

    def __init__(self, root: Path) -> None:
        if os.name != "posix" or fcntl is None:
            raise UpdaterError("native Windows updater integration is not available")
        try:
            path = os.path.abspath(os.fspath(root))
            fd = _open_directory(Path(path))
        except (TypeError, ValueError, OSError) as exc:
            if isinstance(exc, OSError) and exc.errno not in (
                errno.ENOENT,
                errno.ELOOP,
                errno.ENOTDIR,
                errno.EACCES,
                errno.EPERM,
            ):
                raise UpdaterError("update state access denied") from None
            raise UpdaterError("update root must be an owner-private directory") from None
        try:
            self._verify_dir(os.fstat(fd))
        except BaseException:
            os.close(fd)
            raise
        self.fd = fd
        self.root = Path(path)

    def verify_binding(self) -> None:
        try:
            fd = _open_directory(self.root)
            try:
                before, after = os.fstat(self.fd), os.fstat(fd)
                if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                    raise UpdaterError("update root changed")
                self._verify_dir(after)
            finally:
                os.close(fd)
        except (OSError, ValueError):
            raise UpdaterError("update root changed") from None

    @staticmethod
    def _verify_dir(st: os.stat_result) -> None:
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid() or st.st_mode & 0o177:
            raise UpdaterError("update state directory is not owner-private")

    def close(self) -> None:
        fd, self.fd = self.fd, -1
        if fd >= 0:
            os.close(fd)

    # -- directory walk -------------------------------------------------

    def open_dir(self, rel: str, *, create: bool = False) -> int:
        self.verify_binding()
        fd = os.dup(self.fd)
        try:
            for name in PurePosixPath(rel).parts:
                try:
                    child = _open_dir(fd, name)
                except FileNotFoundError:
                    if not create:
                        raise
                    try:
                        os.mkdir(name, 0o700, dir_fd=fd)
                    except FileExistsError:
                        pass  # a concurrent creator; validated below
                    _fsync_dir(fd)
                    child = _open_dir(fd, name)
                self._verify_dir(os.fstat(child))
                os.close(fd)
                fd = child
        except BaseException:
            os.close(fd)
            raise
        return fd

    def _parent(self, rel: str, *, create: bool) -> tuple[int, str]:
        parts = PurePosixPath(rel).parts
        return self.open_dir("/".join(parts[:-1]), create=create), parts[-1]

    # -- file operations -------------------------------------------------

    def _open_new(self, parent: int, name: str, mode: int) -> int:
        try:
            return os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                mode,
                dir_fd=parent,
            )
        except OSError as exc:
            raise _oserror(exc) from None

    def write_new(self, rel: str, data: bytes, *, mode: int = 0o600) -> None:
        parent, name = self._parent(rel, create=True)
        try:
            self.new_file_at(parent, name, data, mode=mode)
        finally:
            os.close(parent)

    def new_file_at(self, parent: int, name: str, data: bytes, *, mode: int = 0o600) -> None:
        fd = self._open_new(parent, name, mode)
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view) :]
            os.fsync(fd)
        except OSError as exc:
            raise _oserror(exc) from None
        finally:
            os.close(fd)

    def read(self, rel: str, limit: int) -> bytes:
        parent, name = self._parent(rel, create=False)
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0), dir_fd=parent)
        except OSError as exc:
            raise _oserror(exc) from None
        finally:
            os.close(parent)
        try:
            before = os.fstat(fd)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.geteuid()
                or before.st_mode & 0o077
                or before.st_nlink != 1
            ):
                raise UpdaterError("update state access denied")
            chunks: list[bytes] = []
            total = 0
            while chunk := os.read(fd, min(1 << 20, limit + 1 - total)):
                total += len(chunk)
                if total > limit:
                    raise UpdaterError("update state access denied")
                chunks.append(chunk)
            after = os.fstat(fd)
            if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ):
                raise UpdaterError("update state changed during read")
            return b"".join(chunks)
        except OSError as exc:
            raise _oserror(exc) from None
        finally:
            os.close(fd)

    def exists(self, rel: str) -> bool:
        try:
            parent, name = self._parent(rel, create=False)
        except (FileNotFoundError, NotADirectoryError):
            return False
        try:
            st = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except (FileNotFoundError, NotADirectoryError):
            return False
        finally:
            os.close(parent)
        return stat.S_ISREG(st.st_mode)

    def unlink(self, rel: str, *, missing_ok: bool = True) -> None:
        parent, name = self._parent(rel, create=False)
        try:
            os.unlink(name, dir_fd=parent)
            _fsync_dir(parent)
        except FileNotFoundError:
            if not missing_ok:
                raise UpdaterError("update state access denied") from None
        except OSError as exc:
            raise _oserror(exc) from None
        finally:
            os.close(parent)

    def replace(self, source: str, target: str) -> None:
        source_parts = PurePosixPath(source).parts
        target_parts = PurePosixPath(target).parts
        if len(target_parts) != len(source_parts):
            raise UpdaterError("update state access denied")
        source_parent = self.open_dir("/".join(source_parts[:-1]))
        target_parent = self.open_dir("/".join(target_parts[:-1]))
        try:
            os.replace(source_parts[-1], target_parts[-1], src_dir_fd=source_parent, dst_dir_fd=target_parent)
            _fsync_dir(target_parent)
        except OSError as exc:
            raise _oserror(exc) from None
        finally:
            os.close(source_parent)
            os.close(target_parent)

    def listdir(self, rel: str, *, create: bool = False) -> list[tuple[str, int]]:
        fd = self.open_dir(rel, create=create)
        try:
            return [(name, os.stat(name, dir_fd=fd, follow_symlinks=False).st_mode) for name in os.listdir(fd)]
        finally:
            os.close(fd)

    def remove_tree(self, rel: str) -> None:
        """Delete one owned ``slots/<digest>`` or ``staging/stage-<hex>`` tree."""

        parts = PurePosixPath(rel).parts
        owned = (len(parts) == 2 and parts[0] == "slots" and _SLOT.fullmatch(parts[1])) or (
            len(parts) == 2 and parts[0] == "staging" and _STAGING.fullmatch(parts[1])
        )
        if not owned:
            raise UpdaterError("only owned slot or staging names may be removed")

        def clear(parent: int, name: str, depth: int) -> None:
            if depth > 32:
                raise UpdaterError("update state directory is too deep")
            fd = _open_dir(parent, name)
            try:
                _Tree._verify_dir(os.fstat(fd))
                for child in os.listdir(fd):
                    st = os.stat(child, dir_fd=fd, follow_symlinks=False)
                    if stat.S_ISDIR(st.st_mode):
                        clear(fd, child, depth + 1)
                    else:
                        os.unlink(child, dir_fd=fd)
            finally:
                os.close(fd)
            os.rmdir(name, dir_fd=parent)

        parent, name = self._parent(rel, create=False)
        try:
            clear(parent, name, 0)
            _fsync_dir(parent)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise _oserror(exc) from None
        finally:
            os.close(parent)

    def path(self, *parts: str) -> Path:
        return self.root.joinpath(*parts)


# --------------------------------------------------------------------------- state


@dataclass(frozen=True)
class _Slot:
    digest: str
    version: str
    manifest_sha256: str
    archive_sha256: str
    platform: str


@dataclass(frozen=True)
class _State:
    active: str
    previous: str | None
    high_water: str
    slots: dict[str, _Slot]


def _slot_dict(slot: _Slot) -> dict[str, str]:
    return {
        "version": slot.version,
        "manifest_sha256": slot.manifest_sha256,
        "archive_sha256": slot.archive_sha256,
        "platform": slot.platform,
    }


class UpdateManager:
    """Install signed releases into slots, verify health, and roll back safely."""

    def __init__(self, root: Path, trusted_key: bytes, platform: str, initial_version: str):
        if not isinstance(trusted_key, bytes) or len(trusted_key) != 32 or trusted_key == bytes(32):
            raise UpdaterError("bundled trust anchor required")
        if platform not in updates.PLATFORMS:
            raise UpdaterError("unsupported platform")
        try:
            self._initial_version = updates._version(initial_version)
        except updates.UpdateRejected:
            raise UpdaterError("invalid initial version") from None
        self._platform = platform
        self._key = trusted_key
        self._health: Callable[[Path, _Slot], bool] = self._probe_health
        self._clock: Callable[[], int] = lambda: int(time.time())
        self._timeout = DEFAULT_TIMEOUT_SECONDS
        self._mutex = threading.RLock()
        self._opener: Callable[..., object] = lambda url, timeout: urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ssl.create_default_context()), _NoRedirect()
        ).open(url, timeout=timeout)
        self._tree = _Tree(Path(root))
        try:
            self._fd = os.dup(self._tree.fd)
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            for name in ("objects", "slots", "staging"):
                os.close(self._tree.open_dir(name, create=True))
            self._state = self._load_state()
            if self._state is None:
                self._bootstrap_state()
        except BaseException:
            self.close()
            raise

    # -- public API ------------------------------------------------------

    def install(self, manifest_path: Path, signature_path: Path, artifact_path: Path, *, initial: bool = False) -> str:
        with self._mutex:
            return self._install(manifest_path, signature_path, artifact_path, initial=initial)

    def _install(self, manifest_path: Path, signature_path: Path, artifact_path: Path, *, initial: bool) -> str:
        """Authenticate, retain, extract and health-check one signed release.

        Returns the slot digest. The active pointer flips only after the staged
        binary passes its self-test; otherwise nothing about the current slot,
        index or data changes.
        """

        raw = self._bounded_read(manifest_path, updates.MAX_MANIFEST_BYTES)
        signature = self._bounded_read(signature_path, 64)
        state = self._state
        released = updates.verify_manifest(
            raw,
            signature,
            trusted_key=self._key,
            platform=self._platform,
            current_version="0.0.0" if initial and not state.active else state.high_water,
            now=self._clock(),
        )
        installed = ".".join(map(str, released.version))
        if type(initial) is not bool or (initial and (state.active or released.version != self._initial_version)):
            raise UpdaterError("unexpected initial installation")
        artifact = self._bounded_read(artifact_path, released.size)
        updates.verify_artifact(released, artifact)
        slot = _Slot(
            digest=hashlib.sha256(b"slot1" + struct.pack(">I", len(raw)) + raw + signature + artifact).hexdigest(),
            version=installed,
            manifest_sha256=hashlib.sha256(raw).hexdigest(),
            archive_sha256=released.sha256,
            platform=self._platform,
        )
        self._retain(slot, raw, signature, artifact)
        if slot.digest not in self._slot_dirs():
            staging = self._extract(slot, artifact)
            self._install_staged(staging, slot)
        elif slot.digest != state.active:
            self._activate(slot)
        self._prune()
        return slot.digest

    def rollback(self, *, confirmed: bool) -> str:
        with self._mutex:
            return self._rollback(confirmed=confirmed)

    def _rollback(self, *, confirmed: bool) -> str:
        """Re-activate the verified previously installed slot; never a downgrade path."""

        if confirmed is not True:
            raise UpdaterError("rollback requires explicit confirmation")
        state = self._state
        if state.previous is None or state.previous == state.active:
            raise UpdaterError("no verified previous slot")
        slot = state.slots.get(state.previous)
        if slot is None or not self._slot_intact(state.previous, slot):
            raise UpdaterError("previous slot is not a known installed release")
        self._activate(slot)
        return slot.digest

    def active_binary(self) -> Path | None:
        """Private path of the active launcher, or ``None`` before any install."""

        state = self._state
        if state.slots.get(state.active) is None:
            return None
        if not self._slot_intact(state.active, state.slots[state.active]):
            raise UpdaterError("active slot authentication failed")
        return self._tree.path("slots", state.active, LAUNCHER)

    def automatic_update(self, feed_url: str) -> str | None:
        with self._mutex:
            return self._automatic_update(feed_url)

    def _automatic_update(self, feed_url: str) -> str | None:
        """Read-only HTTPS update; returns the installed digest when one was accepted."""

        base = _feed_base(feed_url)
        raw = self._download(f"{base}/{self._platform}/manifest.json", updates.MAX_MANIFEST_BYTES)
        signature = self._download(f"{base}/{self._platform}/manifest.json.sig", 64)
        released = updates.verify_manifest(
            raw,
            signature,
            trusted_key=self._key,
            platform=self._platform,
            current_version="0.0.0",
            now=self._clock(),
        )
        if released.version == updates._version(self._state.high_water):
            return None
        if released.version < updates._version(self._state.high_water):
            raise UpdateRejected("update is not newer")
        artifact = self._download(f"{base}/artifacts/{released.sha256}.zip", released.size)
        updates.verify_artifact(released, artifact)
        with tempfile.TemporaryDirectory(prefix="p18-t07-feed-", dir=self._tree.path("staging")) as scratch:
            folder = Path(scratch)
            for name, data in (
                ("manifest.json", raw),
                ("manifest.json.sig", signature),
                ("artifact.zip", artifact),
            ):
                (folder / name).write_bytes(data)
            return self.install(
                folder / "manifest.json",
                folder / "manifest.json.sig",
                folder / "artifact.zip",
            )

    def close(self) -> None:
        fd = getattr(self, "_fd", -1)
        if isinstance(fd, int) and fd >= 0:
            self._fd = -1
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except (OSError, AttributeError):
                pass
            os.close(fd)
        tree = getattr(self, "_tree", None)
        if tree is not None:
            tree.close()

    def __enter__(self) -> "UpdateManager":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # -- helpers: input, retention, health -------------------------------

    @staticmethod
    def _bounded_read(path: Path, limit: int) -> bytes:
        try:
            fd = os.open(os.fspath(path), os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
        except (OSError, TypeError, ValueError):
            raise UpdaterError("update input is not a regular private file") from None
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise UpdaterError("update input is not a regular private file")
            try:
                chunks = []
                total = 0
                while chunk := os.read(fd, min(1 << 20, limit + 1 - total)):
                    chunks.append(chunk)
                    total += len(chunk)
                    if total > limit:
                        raise UpdaterError("update input exceeds read budget")
                data = b"".join(chunks)
            except OSError:
                raise UpdaterError("update input is not a regular private file") from None
            if len(data) > limit:
                raise UpdaterError("update input is not a regular private file")
            return data
        finally:
            os.close(fd)

    def _retain(self, slot: _Slot, raw: bytes, signature: bytes, artifact: bytes) -> None:
        rel = f"objects/{slot.digest[:2]}/{slot.digest}"
        if self._tree.exists(rel):
            if hashlib.sha256(self._tree.read(rel, updates.MAX_ARTIFACT_BYTES)).hexdigest() != slot.archive_sha256:
                raise UpdaterError("retained artifact is corrupt")
        else:
            self._tree.write_new(rel, artifact, mode=0o400)
        metadata = json.dumps(
            {
                "version": slot.version,
                "manifest_sha256": slot.manifest_sha256,
                "archive_sha256": slot.archive_sha256,
                "platform": slot.platform,
                "manifest": raw.hex(),
                "signature": signature.hex(),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        if self._tree.exists(rel + ".json"):
            if self._tree.read(rel + ".json", MAX_STATE_BYTES) != metadata:
                raise UpdaterError("retained identity is corrupt")
        else:
            self._tree.write_new(rel + ".json", metadata, mode=0o400)

    def _probe_health(self, binary: Path, slot: _Slot) -> bool:
        """Run the signed frozen launcher's self-test; never the unsigned tree."""

        env = {"PATH": "/usr/bin:/bin", "LANG": "C", "HOME": str(self._tree.path())}
        try:
            result = subprocess.run(
                [str(binary), SELF_TEST_FLAG],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=SELF_TEST_TIMEOUT_SECONDS,
                env=env,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return result.returncode == 0

    # -- helpers: state --------------------------------------------------

    def _load_state(self) -> _State | None:
        if "state.json" not in {name for name, _ in self._tree.listdir("")}:
            return None
        try:
            raw = self._tree.read("state.json", MAX_STATE_BYTES)
        except FileNotFoundError:
            return None
        try:
            payload = json.loads(raw.decode("utf-8"), object_pairs_hook=updates._object)
        except (UnicodeError, ValueError):
            raise UpdaterError("install state is corrupt") from None
        if type(payload) is not dict or set(payload) != {"schema", "active", "previous", "high_water", "slots"}:
            raise UpdaterError("install state is corrupt")
        if type(payload["schema"]) is not int or payload["schema"] != STATE_SCHEMA:
            raise UpdaterError("install state is corrupt")
        previous = payload["previous"]
        if previous != "" and (not isinstance(previous, str) or _SLOT.fullmatch(previous) is None):
            raise UpdaterError("install state is corrupt")
        slots = payload["slots"]
        if type(slots) is not dict:
            raise UpdaterError("install state is corrupt")
        parsed: dict[str, _Slot] = {}
        for digest, entry in slots.items():
            if _SLOT.fullmatch(digest) is None or type(entry) is not dict:
                raise UpdaterError("install state is corrupt")
            if set(entry) != {"version", "manifest_sha256", "archive_sha256", "platform"}:
                raise UpdaterError("install state is corrupt")
            if any(type(value) is not str for value in entry.values()):
                raise UpdaterError("install state is corrupt")
            try:
                updates._version(entry["version"])
            except updates.UpdateRejected:
                raise UpdaterError("install state is corrupt") from None
            if re.fullmatch(r"[0-9a-f]{64}", entry["manifest_sha256"]) is None:
                raise UpdaterError("install state is corrupt")
            if re.fullmatch(r"[0-9a-f]{64}", entry["archive_sha256"]) is None:
                raise UpdaterError("install state is corrupt")
            if entry["platform"] != self._platform:
                raise UpdaterError("install state is corrupt")
            parsed[digest] = _Slot(
                digest, entry["version"], entry["manifest_sha256"], entry["archive_sha256"], entry["platform"]
            )
        active = payload["active"]
        high_water = payload["high_water"]
        if not isinstance(active, str) or not isinstance(high_water, str):
            raise UpdaterError("install state is corrupt")
        if (active == "") != (not parsed):
            raise UpdaterError("install state is corrupt")
        if active and active not in parsed:
            raise UpdaterError("install state is corrupt")
        try:
            floor = updates._version(high_water)
        except updates.UpdateRejected:
            raise UpdaterError("install state is corrupt") from None
        versions = {updates._version(slot.version) for slot in parsed.values()}
        if floor < max(versions, default=self._initial_version):
            raise UpdaterError("install state is corrupt")
        if active and previous and previous not in parsed:
            raise UpdaterError("install state is corrupt")
        if active and previous == active:
            previous = ""
        return _State(active, previous if active and previous else None, high_water, parsed)

    def _write_state(self, state: _State) -> None:
        payload = json.dumps(
            {
                "schema": STATE_SCHEMA,
                "active": state.active,
                "previous": state.previous or "",
                "high_water": state.high_water,
                "slots": {digest: _slot_dict(slot) for digest, slot in sorted(state.slots.items())},
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        temporary = f"state-{os.urandom(16).hex()}.tmp"
        self._tree.write_new(temporary, payload, mode=0o600)
        self._tree.replace(temporary, "state.json")
        self._state = state

    def _bootstrap_state(self) -> None:
        if self._tree.listdir("slots") or self._tree.listdir("objects") or self._tree.listdir("staging"):
            raise UpdaterError("install state is missing while slots exist")
        self._state = _State(active="", previous=None, high_water=".".join(map(str, self._initial_version)), slots={})
        self._write_state(self._state)

    # -- helpers: slots --------------------------------------------------

    def _slot_dirs(self) -> set[str]:
        return {
            name
            for name, mode in self._tree.listdir("slots", create=True)
            if _SLOT.fullmatch(name) and stat.S_ISDIR(mode)
        }

    def _extract(self, slot: _Slot, artifact: bytes) -> str:
        staging = f"stage-{os.urandom(16).hex()}"
        root_fd = self._tree.open_dir(f"staging/{staging}", create=True)
        try:
            self._extract_tree(root_fd, artifact)
        except BaseException:
            self._tree.remove_tree(f"staging/{staging}")
            raise
        finally:
            os.close(root_fd)
        if not self._tree.exists(f"staging/{staging}/{LAUNCHER}"):
            self._tree.remove_tree(f"staging/{staging}")
            raise UpdaterError("update archive has no launcher")
        return staging

    def _extract_tree(self, root_fd: int, artifact: bytes) -> None:
        import io

        try:
            archive = zipfile.ZipFile(io.BytesIO(artifact))
        except (zipfile.BadZipFile, ValueError):
            raise UpdaterError("update archive is not a ZIP file") from None
        with archive:
            infos = archive.infolist()
            if len(infos) > MAX_ENTRIES:
                raise UpdaterError("update archive exceeds extraction bounds")
            seen: set[str] = set()
            total = 0
            for info in infos:
                parts = _member_parts(info.filename)
                folded = "/".join(parts).casefold()
                if folded in seen:
                    raise UpdaterError("update archive member collides")
                seen.add(folded)
                _validate_member_type(info)
                if _is_dir(info):
                    continue
                if info.file_size > MAX_ENTRY_BYTES:
                    raise UpdaterError("update archive exceeds extraction bounds")
                total += info.file_size
                if total > MAX_TOTAL_BYTES:
                    raise UpdaterError("update archive exceeds extraction bounds")
                data = archive.read(info)
                if len(data) != info.file_size:
                    raise UpdaterError("update archive member is truncated")
                mode = 0o700 if _is_executable(info, parts[-1]) else 0o600
                self._write_member(root_fd, parts, data, mode)

    def _write_member(self, root_fd: int, parts: tuple[str, ...], data: bytes, mode: int) -> None:
        fd = os.dup(root_fd)
        try:
            for name in parts[:-1]:
                try:
                    child = _open_dir(fd, name)
                except FileNotFoundError:
                    os.mkdir(name, 0o700, dir_fd=fd)
                    child = _open_dir(fd, name)
                if not stat.S_ISDIR(os.fstat(child).st_mode):
                    raise UpdaterError("update archive member is not a directory")
                os.close(fd)
                fd = child
            leaf = os.open(
                parts[-1],
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                mode,
                dir_fd=fd,
            )
            try:
                view = memoryview(data)
                while view:
                    view = view[os.write(leaf, view) :]
                os.fsync(leaf)
                os.fchmod(leaf, mode)
            finally:
                os.close(leaf)
        except OSError as exc:
            raise _oserror(exc) from None
        finally:
            os.close(fd)

    def _slot_intact(self, digest: str, slot: _Slot) -> bool:
        if digest not in self._slot_dirs():
            return False
        rel = f"objects/{slot.digest[:2]}/{slot.digest}"
        try:
            artifact = self._tree.read(rel, updates.MAX_ARTIFACT_BYTES)
            metadata = json.loads(self._tree.read(rel + ".json", MAX_STATE_BYTES).decode())
        except (OSError, UpdaterError, UnicodeError, ValueError):
            return False
        try:
            if hashlib.sha256(artifact).hexdigest() != slot.archive_sha256:
                return False
            raw = bytes.fromhex(metadata["manifest"])
            signature = bytes.fromhex(metadata["signature"])
        except (KeyError, TypeError, ValueError):
            return False
        if (
            hashlib.sha256(raw).hexdigest() != slot.manifest_sha256
            or metadata.get("version") != slot.version
            or metadata.get("platform") != slot.platform
        ):
            return False
        try:
            # Known installed identity: authenticity and lifetime are re-checked
            # with trusted time set to the signed issue instant, so an expired but
            # genuine release stays rollback-able and nothing else becomes valid.
            released = updates.verify_manifest(
                raw,
                signature,
                trusted_key=self._key,
                platform=self._platform,
                current_version="0.0.0",
                now=json.loads(raw)["issued_at"],
            )
        except (updates.UpdateRejected, ValueError, KeyError, TypeError):
            return False
        authenticated = (
            ".".join(map(str, released.version)) == slot.version
            and released.sha256 == slot.archive_sha256
            and released.size == len(artifact)
            and hashlib.sha256(b"slot1" + struct.pack(">I", len(raw)) + raw + signature + artifact).hexdigest()
            == digest
        )
        return authenticated and self._extracted_intact(slot, artifact)

    def _extracted_intact(self, slot: _Slot, artifact: bytes) -> bool:
        """Reauthenticate every installed file, not just the retained ZIP."""
        import io

        expected: dict[str, bytes] = {}
        directories: set[str] = set()
        try:
            with zipfile.ZipFile(io.BytesIO(artifact)) as archive:
                for info in archive.infolist():
                    parts = _member_parts(info.filename)
                    _validate_member_type(info)
                    if _is_dir(info):
                        continue
                    _is_executable(info, parts[-1])
                    name = "/".join(parts)
                    expected[name] = hashlib.sha256(archive.read(info)).digest()
                    executable = _is_executable(info, parts[-1])
                    mode = stat.S_IMODE(os.lstat(self._tree.path("slots", slot.digest, *parts)).st_mode)
                    if mode != (0o700 if executable else 0o600):
                        return False
                    directories.update("/".join(parts[:i]) for i in range(1, len(parts)))
            seen: set[str] = set()

            def walk(relative: str) -> None:
                for name, mode in self._tree.listdir(f"slots/{slot.digest}/{relative}".rstrip("/")):
                    child = f"{relative}/{name}".lstrip("/")
                    if stat.S_ISDIR(mode):
                        if child not in directories:
                            raise UpdaterError("unexpected slot directory")
                        walk(child)
                    elif stat.S_ISREG(mode) and child in expected:
                        raw = self._tree.read(f"slots/{slot.digest}/{child}", MAX_ENTRY_BYTES)
                        if hashlib.sha256(raw).digest() != expected[child]:
                            raise UpdaterError("installed file authentication failed")
                        seen.add(child)
                    else:
                        raise UpdaterError("unexpected slot entry")

            walk("")
            return seen == set(expected)
        except (OSError, UpdaterError, zipfile.BadZipFile, ValueError):
            return False

    def _install_staged(self, staging: str, slot: _Slot) -> None:
        self._tree.replace(f"staging/{staging}", f"slots/{slot.digest}")
        self._activate(slot)

    def _activate(self, slot: _Slot) -> None:
        state = self._state
        if not self._slot_intact(slot.digest, slot):
            raise UpdaterError("slot authentication failed")
        binary = self._tree.path("slots", slot.digest, LAUNCHER)
        try:
            executable = stat.S_ISREG(os.lstat(binary).st_mode) and os.access(binary, os.X_OK)
        except OSError:
            executable = False
        if not executable or not self._health(binary, slot):
            raise UpdaterError("release failed its health self-test")
        if not self._slot_intact(slot.digest, slot):
            raise UpdaterError("slot changed during health check")
        floor = max(updates._version(state.high_water), updates._version(slot.version))
        slots = dict(state.slots)
        slots[slot.digest] = slot
        previous = state.active if state.active != slot.digest else state.previous
        self._write_state(_State(slot.digest, previous, ".".join(map(str, floor)), slots))

    def _prune(self) -> None:
        state = self._state
        keep = {state.active, state.previous} | {
            digest
            for digest in state.slots
            if updates._version(state.slots[digest].version) == updates._version(state.high_water)
        }
        keep.update(
            sorted(state.slots, key=lambda digest: updates._version(state.slots[digest].version), reverse=True)[
                :MAX_SLOTS
            ]
        )
        removed = {name for name in self._slot_dirs() if name not in keep}
        for name in removed:
            self._tree.remove_tree(f"slots/{name}")
        for name, mode in self._tree.listdir("staging", create=True):
            if _STAGING.fullmatch(name) and stat.S_ISDIR(mode):
                self._tree.remove_tree(f"staging/{name}")
        if removed:
            slots = {digest: slot for digest, slot in state.slots.items() if digest not in removed}
            self._write_state(_State(state.active, state.previous, state.high_water, slots))
        retained = set(self._state.slots)
        for name, mode in self._tree.listdir("objects", create=True):
            if len(name) != 2 or re.fullmatch(r"[0-9a-f]{2}", name) is None or not stat.S_ISDIR(mode):
                continue
            for entry, entry_mode in self._tree.listdir(f"objects/{name}"):
                base = entry.removesuffix(".json")
                if stat.S_ISREG(entry_mode) and _SLOT.fullmatch(base) and base not in retained:
                    self._tree.unlink(f"objects/{name}/{entry}")

    # -- helpers: network ------------------------------------------------

    def _download(self, url: str, limit: int) -> bytes:
        deadline = time.monotonic() + self._timeout
        try:
            with self._opener(url, self._timeout) as response:
                if response.status != 200 or response.geturl() != url:
                    raise UpdaterError("unexpected update feed response")
                body = bytearray()
                while chunk := response.read(min(64 * 1024, limit + 1 - len(body))):
                    body += chunk
                    if len(body) > limit or time.monotonic() > deadline:
                        raise UpdaterError("update feed exceeded its read budget")
                return bytes(body)
        except (urllib.error.URLError, OSError, ValueError, ssl.SSLError):
            raise UpdaterError("update feed unavailable") from None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Reject every redirect: the feed is a fixed, non-negotiable origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        raise UpdaterError("update feed redirect is not accepted")


def _feed_base(feed_url: str) -> str:
    if not isinstance(feed_url, str) or len(feed_url) > 2048:
        raise UpdaterError("fixed HTTPS update feed required")
    try:
        parsed = urllib.parse.urlsplit(feed_url)
        port = parsed.port
    except ValueError:
        raise UpdaterError("fixed HTTPS update feed required") from None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or port not in (None, 443)
        or "\\" in feed_url
        or "%" in feed_url
        or any(part in (".", "..") for part in parsed.path.split("/"))
        or any(ord(char) < 0x20 or ord(char) == 0x7F for char in feed_url)
    ):
        raise UpdaterError("fixed HTTPS update feed required")
    return feed_url.rstrip("/")


def _member_parts(name: str) -> tuple[str, ...]:
    if not name or "\x00" in name or "\\" in name or _DRIVE.match(name) or name.startswith("/"):
        raise UpdaterError("update archive member is not a safe relative path")
    if any(ord(char) < 0x20 for char in name):
        raise UpdaterError("update archive member is not a safe relative path")
    parts = tuple(name.removesuffix("/").split("/"))
    if (
        not parts
        or len(parts) > 32
        or any(part in ("", ".", "..") or ":" in part or part.endswith((".", " ")) for part in parts)
    ):
        raise UpdaterError("update archive member is not a safe relative path")
    return parts


def _is_dir(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0xFFFF
    if mode & 0o170000 not in (0, stat.S_IFREG, stat.S_IFDIR) or info.external_attr & 0x0400:
        raise UpdaterError("update archive member is not a regular file")
    if (stat.S_ISDIR(mode) or info.external_attr & 0x10) and not info.filename.endswith("/"):
        raise UpdaterError("update archive directory name is invalid")
    return info.filename.endswith("/") or stat.S_ISDIR(mode) or bool(info.external_attr & 0x10)


def _validate_member_type(info: zipfile.ZipInfo) -> None:
    mode = (info.external_attr >> 16) & 0o170000
    directory = info.filename.endswith("/")
    if (
        info.external_attr & 0x0400
        or mode not in (0, stat.S_IFREG, stat.S_IFDIR)
        or ((mode == stat.S_IFDIR or bool(info.external_attr & 0x10)) and not directory)
        or (directory and (mode == stat.S_IFREG or info.file_size))
        or info.flag_bits & 1
    ):
        raise UpdaterError("update archive member is not a regular file or directory")


def _is_executable(info: zipfile.ZipInfo, leaf: str) -> bool:
    mode = (info.external_attr >> 16) & 0xFFFF
    kind = info.external_attr & 0xFFFF  # low MS-DOS attribute word
    if kind & 0x0400 or kind & 0x10:  # reparse point / directory attribute on a file name
        raise UpdaterError("update archive member is not a regular file")
    if mode & 0o170000 not in (0, stat.S_IFREG):
        raise UpdaterError("update archive member is not a regular file")
    return bool(mode & 0o111) or leaf.endswith(NON_EXECUTABLE_SUFFIXES)
