"""Bounded private traversal state, not a provider cursor or reconciliation proof.

Only canonical direct children admitted under the current LIST policy are queued.
Path deduplication prevents repeated offset pages from expanding the queue twice;
it does not make an offset listing a stable snapshot.
"""

from app.integrations.file_workspace.policy import _components

MAX_SCAN_FOLDERS = 256  # Includes the explicitly selected starting folder.
MAX_SCAN_DEPTH = 16  # Relative to that folder, not the connection root.
MAX_SCAN_PAGES = 2048


def _parts(path):
    parts = _components(path)
    if path != "disk:/" + "/".join(parts) or len(path.encode("utf-8")) > 2048:
        raise ValueError("invalid_checkpoint")
    return parts


def initial_traversal(folder):
    _parts(folder)
    return {"pending": [folder], "seen": [folder], "pages": 0}


def validate_traversal(root, value):
    """Fail closed on corrupt state. No caller/browser-supplied state is accepted."""
    base = _parts(root)
    if type(value) is not dict or set(value) != {"pending", "seen", "pages"}:
        raise ValueError("invalid_checkpoint")
    pending, seen, pages = value["pending"], value["seen"], value["pages"]
    if (
        type(pending) is not list
        or type(seen) is not list
        or not 1 <= len(seen) <= MAX_SCAN_FOLDERS
        or len(pending) > len(seen)
        or type(pages) is not int
        or not 0 <= pages <= MAX_SCAN_PAGES
        or any(type(path) is not str for path in pending + seen)
    ):
        raise ValueError("invalid_checkpoint")
    if seen[0] != root or len(set(seen)) != len(seen) or len(set(pending)) != len(pending):
        raise ValueError("invalid_checkpoint")
    if not set(pending) <= set(seen):
        raise ValueError("invalid_checkpoint")
    for path in seen:
        parts = _parts(path)
        if parts[: len(base)] != base or not len(base) <= len(parts) <= len(base) + MAX_SCAN_DEPTH:
            raise ValueError("invalid_checkpoint")
    return value


def active_folder(root, traversal):
    if traversal is None:  # Existing single-folder checkpoints remain compatible.
        return root
    pending = validate_traversal(root, traversal)["pending"]
    if not pending:
        raise ValueError("invalid_checkpoint")
    return pending[0]


def advance_traversal(root, traversal, directories, next_offset, truncated):
    """Compute the next state without mutating the loaded JSON/ORM instance.

    Called inside the same connection-locked transaction as page admission. The
    manager has already validated the page and filtered denied children.
    """
    value = validate_traversal(root, traversal)
    folder = active_folder(root, value)
    pending, seen = list(value["pending"]), list(value["seen"])
    known = set(seen)
    pages = value["pages"] + 1
    limited = truncated
    for path in directories:
        parts = _parts(path)
        parent = _parts(folder)
        if parts[:-1] != parent:
            raise ValueError("invalid_checkpoint")
        if path in known:
            continue
        if len(seen) >= MAX_SCAN_FOLDERS or len(parts) - len(_parts(root)) > MAX_SCAN_DEPTH:
            limited = True
            continue
        seen.append(path)
        known.add(path)
        pending.append(path)
    if next_offset is None and not truncated:
        pending.pop(0)
    if pending and pages >= MAX_SCAN_PAGES:
        limited = True
    updated = {"pending": pending, "seen": seen, "pages": pages}
    state = "limited" if limited else "partial" if pending else "exhausted"
    # Offset belongs to the current folder only; a newly queued folder starts at 0.
    offset = next_offset if next_offset is not None else 0
    return updated, offset, state
