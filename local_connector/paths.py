"""Selected-root and path traversal safety for the local connector."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Iterable, Iterator


class PathSafetyError(ValueError):
    """Raised when a path cannot be proven to stay inside a selected root."""


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
        "chromium",
        "mozilla",
        "firefox",
        "node_modules",
        "system volume information",
        "$recycle.bin",
        "windows",
        "program files",
        "program files (x86)",
    }
)


@dataclass(frozen=True)
class SelectedRoot:
    """A canonical directory explicitly selected by the user."""

    path: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", canonicalize_root(self.path))


def _comparison_key(path: Path | str) -> str:
    value = os.path.normpath(os.path.abspath(os.fspath(path)))
    if sys.platform.startswith(("win", "darwin")):
        return value.casefold()
    return value


def _contained(root: Path, candidate: Path) -> bool:
    root_key = _comparison_key(root)
    candidate_key = _comparison_key(candidate)
    try:
        return os.path.commonpath((root_key, candidate_key)) == root_key
    except ValueError:
        return False


def _is_link(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", None)
    return path.is_symlink() or bool(is_junction and is_junction())


def canonicalize_root(path: Path | str) -> Path:
    """Resolve and validate a user-selected directory."""

    raw = Path(path).expanduser()
    if not raw.is_absolute():
        raw = Path.cwd() / raw
    try:
        resolved = raw.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise PathSafetyError("selected root is unavailable") from exc
    if not resolved.is_dir():
        raise PathSafetyError("selected root is not a directory")
    return resolved


def _relative_parts(relative: str | os.PathLike[str]) -> tuple[str, ...]:
    value = os.fspath(relative)
    if "\x00" in value:
        raise PathSafetyError("path contains NUL")
    if "\\" in value:
        raise PathSafetyError("backslash paths are not accepted")
    candidate = PurePath(value)
    if candidate.is_absolute() or value.startswith(("/", "\\")):
        raise PathSafetyError("absolute paths are not accepted")
    parts = tuple(part for part in candidate.parts if part not in ("", "."))
    if any(part == ".." for part in parts):
        raise PathSafetyError("parent traversal is not accepted")
    if not parts:
        raise PathSafetyError("a relative file path is required")
    return parts


def resolve_relative(root: Path | str, relative: str | os.PathLike[str]) -> Path:
    """Resolve an opaque relative path and prove containment at use time."""

    canonical_root_path = canonicalize_root(root)
    parts = _relative_parts(relative)
    current = canonical_root_path
    for part in parts:
        current = current / part
        if _is_link(current):
            raise PathSafetyError("links and reparse points are not accepted")
    try:
        resolved = current.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise PathSafetyError("path cannot be canonicalized") from exc
    if not _contained(canonical_root_path, resolved):
        raise PathSafetyError("path escapes the selected root")
    if resolved.exists() and not resolved.is_file():
        raise PathSafetyError("only regular files may be indexed")
    return resolved


def _normalise_exclusions(exclusions: Iterable[str]) -> set[tuple[str, ...]]:
    result: set[tuple[str, ...]] = set()
    for exclusion in exclusions:
        result.add(tuple(part.casefold() for part in _relative_parts(exclusion)))
    return result


def iter_files(
    root: Path | str,
    *,
    excluded_paths: Iterable[str] = (),
    excluded_names: Iterable[str] = DEFAULT_EXCLUDED_NAMES,
) -> Iterator[tuple[Path, str]]:
    """Yield regular files as ``(canonical_path, relative_posix_path)`` pairs.

    Symlinks and junctions are denied rather than followed. This conservative
    policy avoids turning a later rename into an unbounded traversal.
    """

    canonical_root_path = canonicalize_root(root)
    excluded_relative = _normalise_exclusions(excluded_paths)
    excluded_name_set = {name.casefold() for name in excluded_names}

    def walk(directory: Path, relative_parts: tuple[str, ...]) -> Iterator[tuple[Path, str]]:
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name.casefold())
        except OSError as exc:
            raise PathSafetyError("selected root cannot be read") from exc
        for entry in entries:
            part = entry.name
            lowered = part.casefold()
            child_parts = relative_parts + (part,)
            if lowered.startswith(".") or lowered in excluded_name_set:
                continue
            child_relative = tuple(item.casefold() for item in child_parts)
            if any(child_relative[: len(item)] == item for item in excluded_relative):
                continue
            child = Path(entry.path)
            if entry.is_symlink() or _is_link(child):
                raise PathSafetyError("links and reparse points are not accepted")
            if entry.is_dir(follow_symlinks=False):
                yield from walk(child, child_parts)
            elif entry.is_file(follow_symlinks=False):
                resolved = resolve_relative(canonical_root_path, Path(*child_parts).as_posix())
                yield resolved, Path(*child_parts).as_posix()

    yield from walk(canonical_root_path, ())
