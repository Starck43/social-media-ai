"""Fail-closed folder policy for the read-only Yandex Disk pilot.

This is a path/capability guard, not an actor, tenant, connection or provider
ACL. Callers must recheck those separately for each operation. No provider calls,
policy persistence or settings UI are implemented by this module.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from enum import Enum


class FileOperation(str, Enum):
    LIST = "list"
    READ = "read"
    CREATE = "create"
    MODIFY = "modify"
    DELETE = "delete"


_READ_OPERATIONS = frozenset({FileOperation.LIST, FileOperation.READ})


def _components(path: str) -> tuple[str, ...]:
    """Parse a raw decoded Disk path, accepting only unambiguous absolute paths.

    Percent escapes are deliberately unsupported, including literal percent
    characters, until the provider boundary has a single audited decoder.
    Reject rather than normalize path traversal, alternate separators or controls.
    """
    if not isinstance(path, str) or len(path) > 32760:
        raise ValueError("invalid disk path")
    if path.startswith("disk:/"):
        path = path[5:]
    if not path.startswith("/") or "%" in path or "\\" in path:
        raise ValueError("invalid disk path")
    if any(unicodedata.category(char).startswith("C") for char in path):
        raise ValueError("invalid disk path")
    if path == "/":
        return ()
    parts = tuple(path[1:].split("/"))
    if any(not part or part in {".", ".."} for part in parts):
        raise ValueError("invalid disk path")
    return parts


def _within(path: tuple[str, ...], root: tuple[str, ...]) -> bool:
    return len(path) >= len(root) and path[: len(root)] == root


@dataclass(frozen=True)
class FolderRestriction:
    """Operations denied for a folder and all its descendants."""

    path: str
    denied: frozenset[FileOperation]


class ReadOnlyFolderPolicy:
    """A selected root with restrictive-only inherited folder exceptions.

    For directory listings callers must apply this policy to each returned item,
    not just to the directory request. A READ decision does not imply LIST, or
    vice versa. Treat authorization changes as invalidating cached decisions.
    """

    def __init__(self, root: str, restrictions: tuple[FolderRestriction, ...] = ()):
        self._root = _components(root)
        compiled = []
        for restriction in restrictions:
            if not isinstance(restriction, FolderRestriction):
                raise ValueError("invalid folder restriction")
            folder = _components(restriction.path)
            if not _within(folder, self._root):
                raise ValueError("folder restriction outside selected root")
            if not isinstance(restriction.denied, frozenset) or any(
                not isinstance(operation, FileOperation) for operation in restriction.denied
            ):
                raise ValueError("invalid denied operations")
            compiled.append((folder, restriction.denied))
        self._restrictions = tuple(compiled)

    def binding(self) -> str:
        """Canonical text of the root and every restriction.

        Callers that issue a token under a policy bind this text so a changed root
        or restriction set invalidates previously issued tokens.
        """
        parts = ["disk:/" + "/".join(self._root)]
        for folder, denied in self._restrictions:
            parts.append("disk:/" + "/".join(folder) + ":" + ",".join(sorted(op.value for op in denied)))
        return "|".join(parts)

    def permits(self, path: str, operation: FileOperation) -> bool:
        """Deny invalid paths, out-of-root resources, unknown or write operations."""
        if not isinstance(operation, FileOperation) or operation not in _READ_OPERATIONS:
            return False
        try:
            target = _components(path)
        except ValueError:
            return False
        if not _within(target, self._root):
            return False
        return not any(_within(target, folder) and operation in denied for folder, denied in self._restrictions)
