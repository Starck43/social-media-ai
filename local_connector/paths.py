"""Pinned POSIX roots and no-follow filesystem access for the scaffold."""

from __future__ import annotations

import os
import stat
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from typing import BinaryIO, Iterable, Iterator


class PathSafetyError(ValueError):
    """Raised when a selected-root boundary cannot be proved."""


DEFAULT_EXCLUDED_NAMES = frozenset(
    {
        ".ssh",
        ".gnupg",
        ".aws",
        ".config",
        "keychains",
        "keychain",
        "browser profiles",
        "browser_profiles",
        "google chrome",
        "chrome",
        "chromium",
        "microsoft edge",
        "edge",
        "mozilla",
        "firefox",
        "safari",
        "library",
        "application support",
        "login data",
        "logins.json",
        "cookies",
        "cookies.sqlite",
        "login.keychain-db",
        "node_modules",
        "system volume information",
        "$recycle.bin",
        "windows",
        "program files",
        "program files (x86)",
    }
)


def _require_posix_nofollow() -> None:
    if (
        os.name != "posix"
        or not hasattr(os, "O_NOFOLLOW")
        or os.open not in os.supports_dir_fd
        or os.stat not in os.supports_dir_fd
        or os.stat not in os.supports_follow_symlinks
    ):
        raise PathSafetyError("safe selected-root access is unavailable on this platform")


def canonicalize_root(path: Path | str) -> Path:
    """Resolve a path at the moment the user explicitly selects it."""

    raw = Path(path).expanduser()
    if not raw.is_absolute():
        raw = Path.cwd() / raw
    try:
        resolved = raw.resolve(strict=True)
        info = resolved.stat()
    except (OSError, RuntimeError) as exc:
        raise PathSafetyError("selected root is unavailable") from exc
    if not stat.S_ISDIR(info.st_mode):
        raise PathSafetyError("selected root is not a directory")
    return resolved


def _open_directory(path: Path) -> int:
    """Open every component without following a link, returning the final fd."""

    _require_posix_nofollow()
    parts = path.parts
    if not parts or parts[0] != os.sep:
        raise PathSafetyError("selected root must be absolute")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    current = os.open(os.sep, flags)
    try:
        for part in parts[1:]:
            child = os.open(part, flags, dir_fd=current)
            os.close(current)
            current = child
        return current
    except OSError as exc:
        os.close(current)
        raise PathSafetyError("selected root changed or cannot be opened safely") from exc


@dataclass(frozen=True)
class SelectedRoot:
    """A canonical directory and the filesystem identity approved by selection."""

    path: Path
    selection_path: str = field(init=False)
    device: int = field(init=False)
    inode: int = field(init=False)

    def __post_init__(self) -> None:
        lexical = Path(os.path.abspath(Path(self.path).expanduser()))
        path = canonicalize_root(self.path)
        fd = _open_directory(path)
        try:
            info = os.fstat(fd)
        finally:
            os.close(fd)
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "selection_path", str(lexical))
        object.__setattr__(self, "device", info.st_dev)
        object.__setattr__(self, "inode", info.st_ino)

    def open(self) -> int:
        fd = _open_directory(self.path)
        info = os.fstat(fd)
        if (info.st_dev, info.st_ino) != (self.device, self.inode):
            os.close(fd)
            raise PathSafetyError("selected root changed; select the folder again")
        return fd

    def verify(self) -> None:
        """Fail if the selected root path no longer reaches the approved directory."""

        fd = self.open()
        os.close(fd)


def _relative_parts(relative: str | os.PathLike[str]) -> tuple[str, ...]:
    try:
        value = os.fspath(relative)
    except TypeError as exc:
        raise PathSafetyError("relative path must be text") from exc
    if not isinstance(value, str) or "\x00" in value or "\\" in value:
        raise PathSafetyError("invalid relative path")
    candidate = PurePath(value)
    if candidate.is_absolute() or value.startswith(("/", "\\")):
        raise PathSafetyError("absolute paths are not accepted")
    parts = tuple(part for part in candidate.parts if part not in ("", "."))
    if not parts or any(part == ".." for part in parts):
        raise PathSafetyError("a safe relative file path is required")
    return parts


def _open_relative(root: SelectedRoot, relative: str | os.PathLike[str]) -> int:
    parts = _relative_parts(relative)
    current = root.open()
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    file_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
    try:
        for part in parts[:-1]:
            child = os.open(part, directory_flags, dir_fd=current)
            os.close(current)
            current = child
        result = os.open(parts[-1], file_flags, dir_fd=current)
    except OSError as exc:
        raise PathSafetyError("links, special files, or unavailable paths are not accepted") from exc
    finally:
        os.close(current)
    info = os.fstat(result)
    if not stat.S_ISREG(info.st_mode):
        os.close(result)
        raise PathSafetyError("only regular files may be indexed")
    return result


@contextmanager
def open_selected_file(root: SelectedRoot, relative: str | os.PathLike[str]) -> Iterator[BinaryIO]:
    """Yield one descriptor opened beneath the pinned root without following links."""

    fd = _open_relative(root, relative)
    before = os.fstat(fd)
    stream = os.fdopen(fd, "rb")
    try:
        yield stream
        after = os.fstat(stream.fileno())
        signature_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        signature_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        if signature_before != signature_after:
            raise PathSafetyError("file changed while it was being indexed")
        root.verify()
        current_fd = _open_relative(root, relative)
        try:
            current = os.fstat(current_fd)
            if (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino):
                raise PathSafetyError("file was replaced while it was being indexed")
        finally:
            os.close(current_fd)
    finally:
        stream.close()


def resolve_relative(root: SelectedRoot | Path | str, relative: str | os.PathLike[str]) -> Path:
    """Validate the path by opening it safely and return its display path."""

    selected = root if isinstance(root, SelectedRoot) else SelectedRoot(Path(root))
    fd = _open_relative(selected, relative)
    os.close(fd)
    return selected.path.joinpath(*_relative_parts(relative))


def _normalise_exclusions(exclusions: Iterable[str]) -> set[tuple[str, ...]]:
    result: set[tuple[str, ...]] = set()
    for exclusion in exclusions:
        result.add(tuple(part.casefold() for part in _relative_parts(exclusion)))
    return result


def iter_files(
    root: SelectedRoot | Path | str,
    *,
    excluded_paths: Iterable[str] = (),
    excluded_names: Iterable[str] = DEFAULT_EXCLUDED_NAMES,
) -> Iterator[tuple[Path, str]]:
    """Walk a pinned root using no-follow directory and regular-file handles."""

    selected = root if isinstance(root, SelectedRoot) else SelectedRoot(Path(root))
    excluded_relative = _normalise_exclusions(excluded_paths)
    excluded_name_set = {name.casefold() for name in excluded_names}

    def walk(directory_fd: int, relative_parts: tuple[str, ...]) -> Iterator[tuple[Path, str]]:
        try:
            with os.scandir(directory_fd) as entries:
                names = sorted((entry.name for entry in entries), key=str.casefold)
        except OSError as exc:
            raise PathSafetyError("selected root cannot be read") from exc
        for name in names:
            lowered = name.casefold()
            child_parts = relative_parts + (name,)
            if lowered.startswith(".") or lowered in excluded_name_set:
                continue
            child_relative = tuple(part.casefold() for part in child_parts)
            if any(child_relative[: len(item)] == item for item in excluded_relative):
                continue
            try:
                entry_info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            except OSError as exc:
                raise PathSafetyError("selected root changed while it was being read") from exc
            if stat.S_ISLNK(entry_info.st_mode):
                raise PathSafetyError("links and reparse points are not accepted")
            directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
            if stat.S_ISDIR(entry_info.st_mode):
                try:
                    child_fd = os.open(name, directory_flags, dir_fd=directory_fd)
                except OSError as exc:
                    raise PathSafetyError("selected root directory changed during traversal") from exc
                actual = os.fstat(child_fd)
                if (actual.st_dev, actual.st_ino) != (entry_info.st_dev, entry_info.st_ino):
                    os.close(child_fd)
                    raise PathSafetyError("selected root directory changed during traversal")
                try:
                    yield from walk(child_fd, child_parts)
                finally:
                    os.close(child_fd)
            elif stat.S_ISREG(entry_info.st_mode):
                relative_path = Path(*child_parts).as_posix()
                yield selected.path / Path(*child_parts), relative_path
            else:
                raise PathSafetyError("special files are not accepted")

    root_fd = selected.open()
    try:
        yield from walk(root_fd, ())
        selected.verify()
    finally:
        os.close(root_fd)
