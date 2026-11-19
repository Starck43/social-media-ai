"""Runtime job handler for typed read-only file-workspace actions (`file_workspace.action`, S2)."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from app.integrations.file_workspace.yandex_content import FileDownloadUnavailable
from app.jobs.progress import ProgressStopped
from app.models.managers.file_catalog_manager import CatalogInputError
from app.models.managers.workspace_connection_manager import ConnectionDenied
from app.services.file_workspace.catalog_scan import ScanBusy, ScanChanged, ScanNotResumable
from app.services.file_workspace.queued_actions import (
    FileWorkspaceActionError,
    FileWorkspaceBudgetError,
    FileWorkspaceDependencyError,
    FileWorkspaceMutationDisabledError,
    execute_file_workspace_action,
)

_RUNTIME_DEPS: ContextVar[tuple[Any, Any]] = ContextVar("file_workspace_action_deps", default=(None, None))


@contextmanager
def file_workspace_action_runtime(*, catalog_client: Any = None, content_client: Any = None):
    """Bind reviewed provider clients explicitly for one runtime/test scope.

    Default remains `(None, None)` so unconfigured production workers fail closed
    rather than talking to an unreviewed external client.
    """
    token = _RUNTIME_DEPS.set((catalog_client, content_client))
    try:
        yield
    finally:
        _RUNTIME_DEPS.reset(token)


async def handle_file_workspace_action(
    payload: dict[str, Any],
    *,
    catalog_client: Any = None,
    content_client: Any = None,
) -> dict[str, Any]:
    """Execute `file_workspace.action` inside the dispatcher's claimed tenant scope."""
    bound_catalog, bound_content = _RUNTIME_DEPS.get()
    resolved_catalog = catalog_client if catalog_client is not None else bound_catalog
    resolved_content = content_client if content_client is not None else bound_content
    raw_action = payload.get("action") if isinstance(payload, dict) else None
    action = raw_action if type(raw_action) is str and len(raw_action) <= 40 else "unknown"
    try:
        return await execute_file_workspace_action(
            payload,
            catalog_client=resolved_catalog,
            content_client=resolved_content,
        )
    except ProgressStopped:
        raise
    except FileWorkspaceMutationDisabledError:
        return {"status": "failed", "error": "file_workspace_writes_disabled", "action": action}
    except FileWorkspaceDependencyError:
        return {"status": "failed", "error": "file_workspace_dependency_unavailable", "action": action}
    except FileWorkspaceBudgetError:
        return {"status": "failed", "error": "file_workspace_budget_exhausted", "action": action}
    except FileWorkspaceActionError:
        return {"status": "failed", "error": "file_workspace_invalid_action", "action": action}
    except CatalogInputError:
        return {"status": "failed", "error": "file_workspace_invalid_action", "action": action}
    except ConnectionDenied:
        return {"status": "failed", "error": "file_workspace_access_denied", "action": action}
    except ScanChanged:
        return {"status": "failed", "error": "file_workspace_scan_changed", "action": action}
    except ScanBusy:
        return {"status": "failed", "error": "file_workspace_scan_busy", "action": action}
    except ScanNotResumable:
        return {"status": "failed", "error": "file_workspace_scan_not_resumable", "action": action}
    except FileDownloadUnavailable:
        return {"status": "failed", "error": "file_workspace_read_unavailable", "action": action}
    except TimeoutError:
        return {"status": "failed", "error": "file_workspace_read_unavailable", "action": action}
