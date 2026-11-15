"""Query-time OS ACL, containment, exclusion and folder policy checks."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Callable, Iterable, Protocol, runtime_checkable

from ..paths import DEFAULT_EXCLUDED_NAMES, PathSafetyError, SelectedRoot, _relative_parts
from .models import RootRegistration, SearchError


@runtime_checkable
class FolderPolicyEvaluator(Protocol):
    """Optional folder-policy consumer hook (compatible with future P12-T15 policies)."""

    def allows(self, root_id: str, relative_path: str) -> bool: ...


class FolderRuleSet:
    """Fail-closed consumer of folder-level allow/deny rules.

    Parent denial overrides any child allow; unknown/malformed inputs deny access.
    """

    def __init__(
        self,
        *,
        allowed_folders: dict[str, Iterable[str]] | None = None,
        denied_folders: dict[str, Iterable[str]] | None = None,
        default_allow: bool = True,
    ) -> None:
        self.default_allow = bool(default_allow)
        self.allowed: dict[str, tuple[tuple[str, ...], ...]] = {}
        self.denied: dict[str, tuple[tuple[str, ...], ...]] = {}
        if allowed_folders is not None:
            for root_id, paths in allowed_folders.items():
                self.allowed[str(root_id)] = tuple(_normalize_prefix(p) for p in paths)
        if denied_folders is not None:
            for root_id, paths in denied_folders.items():
                self.denied[str(root_id)] = tuple(_normalize_prefix(p) for p in paths)

    def allows(self, root_id: str, relative_path: str) -> bool:
        try:
            parts = tuple(part.casefold() for part in _relative_parts(relative_path))
        except PathSafetyError:
            return False
        for denied_prefix in self.denied.get(root_id, ()):
            if not denied_prefix or parts[: len(denied_prefix)] == denied_prefix:
                return False
        if root_id in self.allowed:
            return any(not prefix or parts[: len(prefix)] == prefix for prefix in self.allowed[root_id])
        return self.default_allow


def _normalize_prefix(folder: str) -> tuple[str, ...]:
    if not isinstance(folder, str):
        raise SearchError("folder filter must be text")
    stripped = folder.strip().strip("/")
    if not stripped or stripped == ".":
        return ()
    return tuple(part.casefold() for part in _relative_parts(stripped))


def normalize_folder_prefixes(folders: Iterable[str]) -> tuple[tuple[str, ...], ...]:
    """Validate and normalize relative folder prefixes for safe containment filtering."""

    result: list[tuple[str, ...]] = []
    for folder in folders:
        prefix = _normalize_prefix(folder)
        if prefix:
            result.append(prefix)
    return tuple(result)


def path_is_excluded(
    relative_path: str,
    *,
    excluded_paths: Iterable[str] = (),
    excluded_names: Iterable[str] = DEFAULT_EXCLUDED_NAMES,
) -> bool:
    """Return True if any path segment or prefix is excluded or unsafe."""

    try:
        parts = _relative_parts(relative_path)
    except PathSafetyError:
        return True
    lowered_parts = tuple(part.casefold() for part in parts)
    denied_names = {name.casefold() for name in excluded_names}
    if any(part.startswith(".") or part in denied_names for part in lowered_parts):
        return True
    for excluded in excluded_paths:
        try:
            prefix = tuple(part.casefold() for part in _relative_parts(excluded))
        except PathSafetyError:
            continue
        if lowered_parts[: len(prefix)] == prefix:
            return True
    return False


def policy_allows(folder_policy: object | None, root_id: str, relative_path: str) -> bool:
    """Evaluate an optional P12-T15 folder policy fail-closed."""

    if folder_policy is None:
        return True
    try:
        if hasattr(folder_policy, "allows") and callable(folder_policy.allows):
            return folder_policy.allows(root_id, relative_path) is True
        if hasattr(folder_policy, "permits") and callable(folder_policy.permits):
            # Support P12-T12 ReadOnlyFolderPolicy-style `.permits(path, operation)` consumers.
            return folder_policy.permits(f"/{relative_path}", "read") is True
        if callable(folder_policy):
            return folder_policy(root_id, relative_path) is True
    except Exception:
        return False
    return False


def _root_euid_allows_dir_traverse(info: os.stat_result) -> bool:
    if os.geteuid() != 0:
        return True
    return bool(info.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))


def _root_euid_allows_file_read(info: os.stat_result) -> bool:
    if os.geteuid() != 0:
        return True
    return bool(info.st_mode & (stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH))


def verify_root_and_file_access(
    registration: RootRegistration,
    relative_path: str,
    *,
    excluded_paths: Iterable[str] = (),
    excluded_names: Iterable[str] = DEFAULT_EXCLUDED_NAMES,
    folder_policy: object | None = None,
    os_access_check: Callable[[Path, os.stat_result], bool] | None = None,
) -> os.stat_result | None:
    """Re-verify root pinning, exclusions, policy, regular non-symlink status and OS readability.

    Returns live ``os.stat_result`` if accessible and permitted; ``None`` otherwise.
    """

    if path_is_excluded(relative_path, excluded_paths=excluded_paths, excluded_names=excluded_names):
        return None
    if not policy_allows(folder_policy, registration.root_id, relative_path):
        return None
    try:
        parts = _relative_parts(relative_path)
    except PathSafetyError:
        return None

    selected: SelectedRoot = registration.selected
    dir_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    file_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)

    try:
        current = selected.open()
    except (OSError, PathSafetyError):
        return None

    file_fd: int | None = None
    try:
        root_info = os.fstat(current)
        if not _root_euid_allows_dir_traverse(root_info):
            return None
        for part in parts[:-1]:
            child = os.open(part, dir_flags, dir_fd=current)
            os.close(current)
            current = child
            dir_info = os.fstat(current)
            if not _root_euid_allows_dir_traverse(dir_info):
                return None
        file_fd = os.open(parts[-1], file_flags, dir_fd=current)
        info = os.fstat(file_fd)
    except OSError:
        return None
    finally:
        os.close(current)
        if file_fd is not None:
            os.close(file_fd)

    if not stat.S_ISREG(info.st_mode):
        return None
    if not _root_euid_allows_file_read(info):
        return None
    if os_access_check is not None:
        try:
            if os_access_check(selected.path.joinpath(*parts), info) is not True:
                return None
        except Exception:
            return None

    # Re-verify root identity after handle walk to close root-swap TOCTOU window.
    try:
        selected.verify()
    except (OSError, PathSafetyError):
        return None
    return info
