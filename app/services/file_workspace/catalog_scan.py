"""Resumable, one-folder metadata pagination; no production scheduler or route.

Provider offsets are hints, not snapshot guarantees. No deletion is inferred.
The required injected client must satisfy P8-T34's reviewed network contract.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
from sqlalchemy import select

from app.integrations.file_workspace.policy import FileOperation
from app.integrations.file_workspace.yandex_disk import MetadataError
from app.models import FileCatalogScan
from app.models.managers.file_catalog_manager import (
    CatalogCapacityError,
    CatalogInputError,
    CatalogScanChanged,
    CatalogScanClaim,
    FileCatalogManager,
    _bounded_int,
    _owned,
    _path,
    _policy_key,
)
from app.models.managers.workspace_connection_manager import ConnectionDenied
from app.services.file_workspace.yandex_reader import list_owned_folder

_LEASE = timedelta(seconds=90)
_PROVIDER_ERRORS = frozenset(
    {
        "reauthorization_required",
        "rate_limited",
        "provider_unavailable",
        "response_too_large",
        "invalid_provider_response",
    }
)
_CAPACITY_ERRORS = frozenset({"catalog_quota", "observation_quota"})


class ScanBusy(Exception):
    """Another worker has an active, bounded claim."""


class ScanChanged(Exception):
    """Scan, owner or policy changed; a new authorized scan is required."""


class ScanNotResumable(Exception):
    """No current scan, or the latest scan reached a terminal state."""


def _now():
    return datetime.now(timezone.utc)


async def _row(session, tenant_id, connection_id):
    # _owned serializes all scan state changes with the same connection lock.
    return await session.scalar(
        select(FileCatalogScan).where(
            FileCatalogScan.tenant_id == tenant_id,
            FileCatalogScan.connection_id == connection_id,
        )
    )


def _valid(row, actor_id, connection, policy):
    return (
        row.actor_id == actor_id
        and row.policy_key == _policy_key(connection.config)
        and policy.permits(row.folder, FileOperation.LIST)
    )


def _projection(row):
    if row.lease_token and row.lease_until > _now():
        state = "running"
    else:
        state = row.state
    return {"state": state, "error_code": row.error_code, "next_offset": row.next_offset}


async def start_catalog_scan(*, tenant_id, actor_id, connection_id, folder, page_size=100):
    """Replace the checkpoint for one explicitly selected folder, never recurse."""
    _bounded_int(page_size, 1, 100)
    try:
        folder = _path(folder)
    except (ValueError, TypeError):
        raise CatalogInputError("invalid_folder") from None
    async with _owned(tenant_id, actor_id, connection_id) as (session, connection, policy, _):
        if not policy.permits(folder, FileOperation.LIST):
            raise ConnectionDenied()
        row = await _row(session, tenant_id, connection_id)
        if row and row.lease_token and row.lease_until > _now():
            raise ScanBusy()
        if row is None:
            row = FileCatalogScan(tenant_id=tenant_id, connection_id=connection_id)
            session.add(row)
        row.actor_id = actor_id
        row.scan_token = uuid4().hex
        row.folder = folder
        row.policy_key = _policy_key(connection.config)
        row.next_offset = 0
        row.page_size = page_size
        row.state = "partial"
        row.error_code = row.lease_token = row.lease_until = None
        return _projection(row)


async def catalog_scan_status(*, tenant_id, actor_id, connection_id):
    """Report only authorized checkpoint state; never disclose path or provider data."""
    async with _owned(tenant_id, actor_id, connection_id) as (session, connection, policy, _):
        row = await _row(session, tenant_id, connection_id)
        if row is None or row.actor_id != actor_id:
            return None
        if not _valid(row, actor_id, connection, policy):
            return {"state": "policy_changed", "error_code": None, "next_offset": None}
        return _projection(row)


async def _claim(*, tenant_id, actor_id, connection_id):
    async with _owned(tenant_id, actor_id, connection_id) as (session, connection, policy, _):
        row = await _row(session, tenant_id, connection_id)
        if row is None or row.actor_id != actor_id:
            raise ScanNotResumable()
        if not _valid(row, actor_id, connection, policy):
            raise ScanChanged()
        if row.lease_token and row.lease_until > _now():
            raise ScanBusy()
        if row.state not in {"partial", "error"}:
            raise ScanNotResumable()
        row.lease_token, row.lease_until = uuid4().hex, _now() + _LEASE
        return row.scan_token, row.lease_token, row.folder, row.next_offset, row.page_size


async def _finish_error(*, tenant_id, actor_id, connection_id, scan_token, lease_token, error):
    async with _owned(tenant_id, actor_id, connection_id) as (session, connection, policy, _):
        row = await _row(session, tenant_id, connection_id)
        if (
            row is None
            or not _valid(row, actor_id, connection, policy)
            or row.scan_token != scan_token
            or row.lease_token != lease_token
            or row.lease_until is None
            or row.lease_until <= _now()
        ):
            raise ScanChanged()
        row.state, row.error_code = "error", error
        row.lease_token = row.lease_until = None
        return _projection(row)


async def resume_catalog_scan(*, tenant_id, actor_id, connection_id, client: httpx.AsyncClient):
    """Process at most one page and atomically admit the page and advance its checkpoint.

    An interrupted worker's claim expires; a replay is safe and does not infer
    deletion. Expected provider/capacity errors retain the offset for retry.
    No caller-supplied cursor, raw provider body or default client is accepted.
    """
    if client is None:
        raise CatalogInputError("invalid_client")
    args = {"tenant_id": tenant_id, "actor_id": actor_id, "connection_id": connection_id}
    scan_token, lease_token, folder, offset, limit = await _claim(**args)
    try:
        manager = FileCatalogManager()
        ticket = await manager.begin_page(**args, folder=folder)
        page = await list_owned_folder(**args, folder=folder, client=client, offset=offset, limit=limit)
        result = await manager.admit_page(
            **args, ticket=ticket, page=page, _scan_claim=CatalogScanClaim(scan_token, lease_token, offset, limit)
        )
    except MetadataError as exc:
        code = str(exc) if str(exc) in _PROVIDER_ERRORS else "provider_unavailable"
        return await _finish_error(**args, scan_token=scan_token, lease_token=lease_token, error=code)
    except CatalogCapacityError as exc:
        code = str(exc) if str(exc) in _CAPACITY_ERRORS else "catalog_quota"
        return await _finish_error(**args, scan_token=scan_token, lease_token=lease_token, error=code)
    except CatalogInputError:
        return await _finish_error(
            **args, scan_token=scan_token, lease_token=lease_token, error="invalid_provider_response"
        )
    except CatalogScanChanged:
        raise ScanChanged() from None
    return result["scan"]
