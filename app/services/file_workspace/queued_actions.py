"""Typed read-only file-workspace actions queued via the existing Job queue (S2).

All write, upload, delete, move, share and export actions are refused at both
enqueue and execution boundaries while mutation gates stay off. File paths,
names, extracted text and credentials never enter Job.result, progress or logs.
"""

from __future__ import annotations

import asyncio
import unicodedata
from typing import Any

from sqlalchemy import select

from app.core.tenant_context import current_tenant_id, is_bypass
from app.integrations.file_workspace.plain_text import MAX_INPUT_BYTES
from app.integrations.file_workspace.policy import FileOperation
from app.integrations.file_workspace.yandex_content import CONTENT_TIMEOUT, DownloadedText, FileDownloadUnavailable
from app.jobs import progress as job_progress
from app.models import FileCatalogObject
from app.models.file_workspace.private_insights import FileInsightMount
from app.models.managers.file_catalog_manager import (
    MAX_PAGE,
    CatalogInputError,
    FileCatalogManager,
    _owned,
    _path,
)
from app.models.managers.file_content_manager import FileContentManager
from app.models.managers.file_insight_manager import POLICY_REVISION
from app.models.managers.job_manager import JobManager
from app.models.managers.workspace_connection_manager import ConnectionDenied
from app.services.file_workspace.catalog_scan import (
    ScanBusy,
    ScanChanged,
    ScanNotResumable,
    resume_catalog_scan,
    start_catalog_scan,
)
from app.services.file_workspace.file_content import FileContentService

FILE_WORKSPACE_JOB_TYPE = "file_workspace.action"
READ_ONLY_ACTIONS = frozenset({"catalog_search", "catalog_scan_step", "content_read_prep"})
DISABLED_MUTATION_ACTIONS = frozenset(
    {
        "write",
        "upload",
        "update",
        "overwrite",
        "create",
        "mkdir",
        "move",
        "rename",
        "copy",
        "delete",
        "trash",
        "share",
        "export",
        "propose_write",
        "apply_write",
    }
)
_DISABLED_MUTATION_PREFIXES = (
    "write_",
    "mutate_",
    "delete_",
    "move_",
    "rename_",
    "upload_",
    "create_",
    "update_",
    "share_",
    "export_",
    "overwrite_",
    "apply_",
    "propose_",
)
_FORBIDDEN_INPUT_KEYS = frozenset(
    {
        "_trusted_initiated_by_user_id",
        "tenant_id",
        "actor_id",
        "token",
        "access_token",
        "client",
        "reader",
        "url",
        "download_url",
        "path_override",
        "allow_writes",
        "write_mode",
    }
)

MAX_ACTION_PAGES = 10
MAX_ACTION_PAGE_SIZE = MAX_PAGE
MAX_ACTION_ITEMS = 500
MAX_ACTION_BYTES = MAX_INPUT_BYTES
MAX_ACTION_TIMEOUT = min(float(CONTENT_TIMEOUT), 10.0)
DEFAULT_ACTION_ATTEMPTS = 1
MAX_ACTION_ATTEMPTS = 3


class FileWorkspaceMutationDisabledError(PermissionError):
    """Any mutating or unapproved write action is unconditionally refused."""

    def __init__(self, code: str = "file_workspace_writes_disabled"):
        super().__init__(code)
        self.code = code


class FileWorkspaceActionError(ValueError):
    """Sanitized validation error for typed file-workspace actions."""

    def __init__(self, code: str = "file_workspace_invalid_action"):
        super().__init__(code)
        self.code = code


class FileWorkspaceDependencyError(RuntimeError):
    """Required reviewed provider dependency is not wired in this runtime."""

    def __init__(self, code: str = "file_workspace_dependency_unavailable"):
        super().__init__(code)
        self.code = code


class FileWorkspaceBudgetError(RuntimeError):
    """Execution exceeded configured attempt, page, item or byte budget."""

    def __init__(self, code: str = "file_workspace_budget_exhausted"):
        super().__init__(code)
        self.code = code


def _int_range(value: Any, low: int, high: int, *, code: str = "file_workspace_invalid_action") -> int:
    if type(value) is not int or not low <= value <= high:
        raise FileWorkspaceActionError(code)
    return value


def _timeout_range(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FileWorkspaceActionError("file_workspace_invalid_action")
    parsed = float(value)
    if not 0.05 <= parsed <= MAX_ACTION_TIMEOUT:
        raise FileWorkspaceActionError("file_workspace_invalid_action")
    return parsed


def _is_mutation_action(action: Any) -> bool:
    if type(action) is not str:
        return False
    lowered = action.strip().lower()
    return lowered in DISABLED_MUTATION_ACTIONS or lowered.startswith(_DISABLED_MUTATION_PREFIXES)


def validate_file_workspace_action_payload(
    raw_payload: Any,
    *,
    allow_dispatcher_metadata: bool = False,
) -> dict[str, Any]:
    """Validate and normalize one typed read-only action payload; refuse writes."""
    if not isinstance(raw_payload, dict):
        raise FileWorkspaceActionError("file_workspace_invalid_action")
    if not allow_dispatcher_metadata and _FORBIDDEN_INPUT_KEYS.intersection(raw_payload):
        raise FileWorkspaceActionError("file_workspace_invalid_action")
    action = raw_payload.get("action")
    if _is_mutation_action(action):
        raise FileWorkspaceMutationDisabledError("file_workspace_writes_disabled")
    if type(action) is not str or action not in READ_ONLY_ACTIONS:
        raise FileWorkspaceActionError("file_workspace_invalid_action")

    connection_id = _int_range(raw_payload.get("connection_id"), 1, 2_147_483_647)

    if action == "catalog_search":
        allowed = {"action", "connection_id", "query", "lifecycle", "page_size", "max_pages", "max_items"}
        extra = set(raw_payload) - allowed
        if allow_dispatcher_metadata:
            extra -= {"agent_task_id", "job_id", "attempts", "_prior_result", "_trusted_initiated_by_user_id"}
        if extra:
            raise FileWorkspaceActionError("file_workspace_invalid_action")
        query = raw_payload.get("query", "")
        if type(query) is not str or len(query) > 200 or any(unicodedata.category(c).startswith("C") for c in query):
            raise FileWorkspaceActionError("file_workspace_invalid_action")
        lifecycle = raw_payload.get("lifecycle", None)
        if lifecycle == "all":
            lifecycle = None
        if lifecycle is not None and lifecycle not in ("present", "missing", "stale"):
            raise FileWorkspaceActionError("file_workspace_invalid_action")
        page_size = _int_range(raw_payload.get("page_size", 50), 1, MAX_ACTION_PAGE_SIZE)
        max_pages = _int_range(raw_payload.get("max_pages", 3), 1, MAX_ACTION_PAGES)
        max_items = _int_range(raw_payload.get("max_items", 200), 1, MAX_ACTION_ITEMS)
        return {
            "action": "catalog_search",
            "connection_id": connection_id,
            "query": query.strip(),
            "lifecycle": lifecycle,
            "page_size": page_size,
            "max_pages": max_pages,
            "max_items": max_items,
        }

    if action == "catalog_scan_step":
        allowed = {
            "action",
            "connection_id",
            "start_new",
            "folder",
            "recursive",
            "page_size",
            "max_pages",
            "max_items",
        }
        extra = set(raw_payload) - allowed
        if allow_dispatcher_metadata:
            extra -= {"agent_task_id", "job_id", "attempts", "_prior_result", "_trusted_initiated_by_user_id"}
        if extra:
            raise FileWorkspaceActionError("file_workspace_invalid_action")
        start_new = raw_payload.get("start_new", False)
        recursive = raw_payload.get("recursive", False)
        if type(start_new) is not bool or type(recursive) is not bool:
            raise FileWorkspaceActionError("file_workspace_invalid_action")
        folder = None
        if start_new:
            raw_folder = raw_payload.get("folder")
            if type(raw_folder) is not str:
                raise FileWorkspaceActionError("file_workspace_invalid_action")
            try:
                folder = _path(raw_folder)
            except (ValueError, TypeError):
                raise FileWorkspaceActionError("file_workspace_invalid_action") from None
        elif raw_payload.get("folder") is not None:
            raise FileWorkspaceActionError("file_workspace_invalid_action")
        page_size = _int_range(raw_payload.get("page_size", 50), 1, MAX_ACTION_PAGE_SIZE)
        max_pages = _int_range(raw_payload.get("max_pages", 3), 1, MAX_ACTION_PAGES)
        max_items = _int_range(raw_payload.get("max_items", MAX_ACTION_ITEMS), 1, MAX_ACTION_ITEMS)
        if page_size > max_items:
            raise FileWorkspaceActionError("file_workspace_invalid_action")
        return {
            "action": "catalog_scan_step",
            "connection_id": connection_id,
            "start_new": start_new,
            "folder": folder,
            "recursive": recursive,
            "page_size": page_size,
            "max_pages": max_pages,
            "max_items": max_items,
        }

    # content_read_prep
    allowed = {"action", "connection_id", "object_id", "start_line", "end_line", "max_bytes", "timeout_seconds"}
    extra = set(raw_payload) - allowed
    if allow_dispatcher_metadata:
        extra -= {"agent_task_id", "job_id", "attempts", "_prior_result", "_trusted_initiated_by_user_id"}
    if extra:
        raise FileWorkspaceActionError("file_workspace_invalid_action")
    object_id = _int_range(raw_payload.get("object_id"), 1, 2_147_483_647)
    start_line = _int_range(raw_payload.get("start_line", 1), 1, 1000)
    end_line = _int_range(raw_payload.get("end_line", min(start_line + 31, 1000)), start_line, 1000)
    if end_line - start_line + 1 > 32:
        raise FileWorkspaceActionError("file_workspace_invalid_action")
    max_bytes = _int_range(raw_payload.get("max_bytes", MAX_ACTION_BYTES), 1, MAX_ACTION_BYTES)
    timeout_seconds = _timeout_range(raw_payload.get("timeout_seconds", MAX_ACTION_TIMEOUT))
    return {
        "action": "content_read_prep",
        "connection_id": connection_id,
        "object_id": object_id,
        "start_line": start_line,
        "end_line": end_line,
        "max_bytes": max_bytes,
        "timeout_seconds": timeout_seconds,
    }


async def enqueue_file_workspace_action(
    *,
    initiated_by_user_id: int,
    action_payload: dict[str, Any],
    max_attempts: int = DEFAULT_ACTION_ATTEMPTS,
):
    """Validate ownership/policy and enqueue one typed read-only file action."""
    tenant_id = current_tenant_id()
    if is_bypass() or type(tenant_id) is not int or tenant_id <= 0:
        raise ConnectionDenied()
    if type(initiated_by_user_id) is not int or initiated_by_user_id <= 0:
        raise ConnectionDenied()
    if type(max_attempts) is not int or not 1 <= max_attempts <= MAX_ACTION_ATTEMPTS:
        raise FileWorkspaceActionError("file_workspace_invalid_action")

    normalized = validate_file_workspace_action_payload(action_payload, allow_dispatcher_metadata=False)

    async with _owned(tenant_id, initiated_by_user_id, normalized["connection_id"]) as (
        session,
        _connection,
        policy,
        root_key,
    ):
        if normalized["action"] == "catalog_scan_step" and normalized["start_new"]:
            if not policy.permits(normalized["folder"], FileOperation.LIST):
                raise ConnectionDenied()
        elif normalized["action"] == "content_read_prep":
            row = await session.scalar(
                select(FileCatalogObject).where(
                    FileCatalogObject.id == normalized["object_id"],
                    FileCatalogObject.tenant_id == tenant_id,
                    FileCatalogObject.connection_id == normalized["connection_id"],
                    FileCatalogObject.root_key == root_key,
                    FileCatalogObject.kind == "file",
                    FileCatalogObject.lifecycle_state == "present",
                )
            )
            if row is None or not all(policy.permits(row.path, op) for op in (FileOperation.LIST, FileOperation.READ)):
                raise ConnectionDenied()
            mount = await session.scalar(
                select(FileInsightMount).where(
                    FileInsightMount.tenant_id == tenant_id,
                    FileInsightMount.connection_id == normalized["connection_id"],
                    FileInsightMount.owner_id == initiated_by_user_id,
                )
            )
            if mount is None or mount.processing_allowed is not True or mount.policy_revision != POLICY_REVISION:
                raise PermissionError("file_workspace_consent_required")

    return await JobManager().enqueue(
        FILE_WORKSPACE_JOB_TYPE,
        payload=normalized,
        initiated_by_user_id=initiated_by_user_id,
        max_attempts=max_attempts,
    )


class _BoundedDownloadClient:
    """Enforce per-job byte cap and measure downloaded byte count safely."""

    def __init__(self, inner: Any, *, max_bytes: int):
        self.inner = inner
        self.max_bytes = max_bytes
        self.byte_count = 0
        self.budget_exceeded = False

    async def download(self, *, path: str, name: str, token: str):
        downloaded = await self.inner.download(path=path, name=name, token=token)
        if type(downloaded) is not DownloadedText or type(downloaded.payload) is not bytes:
            raise FileDownloadUnavailable()
        self.byte_count = len(downloaded.payload)
        if self.byte_count > self.max_bytes:
            self.budget_exceeded = True
            raise FileWorkspaceBudgetError("file_workspace_budget_exhausted")
        return downloaded


async def execute_file_workspace_action(
    payload: dict[str, Any],
    *,
    catalog_client: Any = None,
    content_client: Any = None,
) -> dict[str, Any]:
    """Execute one claimed typed read-only action with budgets, progress and cancellation."""
    tenant_id = current_tenant_id()
    if is_bypass() or type(tenant_id) is not int or tenant_id <= 0:
        raise ConnectionDenied()

    actor_id = payload.get("_trusted_initiated_by_user_id") if isinstance(payload, dict) else None
    if type(actor_id) is not int or actor_id <= 0:
        raise ConnectionDenied()

    attempts = payload.get("attempts") if isinstance(payload, dict) else None
    if type(attempts) is int and attempts > MAX_ACTION_ATTEMPTS:
        raise FileWorkspaceBudgetError("file_workspace_budget_exhausted")

    spec = validate_file_workspace_action_payload(payload, allow_dispatcher_metadata=True)
    action = spec["action"]
    connection_id = spec["connection_id"]

    if action == "catalog_search":
        manager = FileCatalogManager()
        matched_ids: list[int] = []
        after = 0
        pages_processed = 0
        has_more = False
        budget_exhausted = False
        for page_index in range(spec["max_pages"]):
            await job_progress.report_action(
                "catalog_search",
                total=spec["max_pages"],
                completed=page_index,
                processed_items=len(matched_ids),
                phase="running",
            )
            remaining = spec["max_items"] - len(matched_ids)
            if remaining <= 0:
                budget_exhausted = True
                break
            page = await manager.list_page(
                tenant_id=tenant_id,
                actor_id=actor_id,
                connection_id=connection_id,
                query=spec["query"],
                after=after,
                limit=min(spec["page_size"], remaining),
                lifecycle=spec["lifecycle"],
            )
            pages_processed += 1
            for item in page["items"]:
                if len(matched_ids) >= spec["max_items"]:
                    budget_exhausted = True
                    break
                matched_ids.append(int(item["id"]))
            next_after = page["next_after"]
            if next_after is None:
                has_more = False
                break
            after = next_after
            has_more = True
            if len(matched_ids) >= spec["max_items"] or pages_processed >= spec["max_pages"]:
                budget_exhausted = True
                break
        await job_progress.report_action(
            "catalog_search",
            total=spec["max_pages"],
            completed=pages_processed,
            processed_items=len(matched_ids),
            phase="done",
            budget_exhausted=budget_exhausted,
        )
        return {
            "status": "ok",
            "action": "catalog_search",
            "connection_id": connection_id,
            "pages_processed": pages_processed,
            "matched_count": len(matched_ids),
            "object_ids": matched_ids,
            "has_more": has_more,
            "budget_exhausted": budget_exhausted,
            "mutations_enabled": False,
        }

    if action == "catalog_scan_step":
        if catalog_client is None:
            raise FileWorkspaceDependencyError("file_workspace_dependency_unavailable")
        if spec["start_new"]:
            await job_progress.report_action(
                "catalog_scan_step",
                total=spec["max_pages"],
                completed=0,
                processed_items=0,
                phase="validating",
            )
            await start_catalog_scan(
                tenant_id=tenant_id,
                actor_id=actor_id,
                connection_id=connection_id,
                folder=spec["folder"],
                page_size=spec["page_size"],
                recursive=spec["recursive"],
            )

        pages_processed = 0
        budget_items_used = 0
        scan_result = {"state": "partial", "error_code": None, "next_offset": None}
        budget_exhausted = False
        for page_index in range(spec["max_pages"]):
            if budget_items_used + spec["page_size"] > spec["max_items"]:
                budget_exhausted = True
                break
            await job_progress.report_action(
                "catalog_scan_step",
                total=spec["max_pages"],
                completed=page_index,
                processed_items=budget_items_used,
                phase="running",
            )
            scan_result = await resume_catalog_scan(
                tenant_id=tenant_id,
                actor_id=actor_id,
                connection_id=connection_id,
                client=catalog_client,
            )
            pages_processed += 1
            budget_items_used += spec["page_size"]
            if scan_result["state"] in {"exhausted", "limited", "error"}:
                break
        else:
            if scan_result["state"] == "partial":
                budget_exhausted = True

        if scan_result["state"] == "partial" and not budget_exhausted and pages_processed >= spec["max_pages"]:
            budget_exhausted = True

        await job_progress.report_action(
            "catalog_scan_step",
            total=spec["max_pages"],
            completed=pages_processed,
            processed_items=budget_items_used,
            phase="done" if scan_result["state"] != "error" else "stopped",
            budget_exhausted=budget_exhausted,
        )
        status = "partial" if scan_result["state"] == "error" else "ok"
        return {
            "status": status,
            "action": "catalog_scan_step",
            "connection_id": connection_id,
            "pages_processed": pages_processed,
            "scan_state": scan_result["state"],
            "error_code": scan_result["error_code"],
            "exhausted": scan_result["state"] == "exhausted",
            "budget_exhausted": budget_exhausted,
            "mutations_enabled": False,
        }

    # content_read_prep
    if content_client is None:
        raise FileWorkspaceDependencyError("file_workspace_dependency_unavailable")

    await job_progress.report_action(
        "content_read_prep",
        total=2,
        completed=0,
        processed_items=0,
        processed_bytes=0,
        phase="validating",
    )
    bounded = _BoundedDownloadClient(content_client, max_bytes=spec["max_bytes"])
    service = FileContentService(FileContentManager(enabled=True), client=bounded)
    try:
        async with asyncio.timeout(spec["timeout_seconds"]):
            evidence = await service.preview(
                start=spec["start_line"],
                end=spec["end_line"],
                tenant_id=tenant_id,
                actor_id=actor_id,
                connection_id=connection_id,
                object_id=spec["object_id"],
            )
    except FileDownloadUnavailable:
        if bounded.budget_exceeded:
            raise FileWorkspaceBudgetError("file_workspace_budget_exhausted") from None
        raise

    selected_lines = sum(item.end - item.start + 1 for item in evidence.excerpts)
    await job_progress.report_action(
        "content_read_prep",
        total=2,
        completed=2,
        processed_items=selected_lines,
        processed_bytes=bounded.byte_count,
        phase="done",
    )
    return {
        "status": "ok",
        "action": "content_read_prep",
        "connection_id": connection_id,
        "object_id": spec["object_id"],
        "line_count": evidence.line_count,
        "selected_lines": selected_lines,
        "byte_count": bounded.byte_count,
        "parser_version": evidence.parser_version,
        "retention_available": False,
        "prepared": True,
        "mutations_enabled": False,
    }


__all__ = [
    "DEFAULT_ACTION_ATTEMPTS",
    "DISABLED_MUTATION_ACTIONS",
    "FILE_WORKSPACE_JOB_TYPE",
    "FileWorkspaceActionError",
    "FileWorkspaceBudgetError",
    "FileWorkspaceDependencyError",
    "FileWorkspaceMutationDisabledError",
    "MAX_ACTION_ATTEMPTS",
    "MAX_ACTION_BYTES",
    "MAX_ACTION_ITEMS",
    "MAX_ACTION_PAGE_SIZE",
    "MAX_ACTION_PAGES",
    "READ_ONLY_ACTIONS",
    "enqueue_file_workspace_action",
    "execute_file_workspace_action",
    "validate_file_workspace_action_payload",
]
