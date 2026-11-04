"""Business command preview, approval, rejection, execution and status API (P13-T04)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import Depends, HTTPException, Path, Query
from pydantic import Field

from app.api.deps import require_model_perm
from app.api.v1.conventions import business_router, require_idempotency_key, validate_business_routes
from app.schemas.business import BusinessPage, BusinessSchema
from app.services.commands import (
    DEFAULT_TTL_SECONDS,
    MAX_SCHEDULED_TTL_SECONDS,
    CommandConflict,
    CommandDenied,
    CommandNotFound,
    CommandTimeoutUnknown,
    command_ledger,
)
from app.types import ActionType


class CommandTargetOut(BusinessSchema):
    id: int
    target_ref: str
    target_idempotency_key: str
    state: str
    provider_receipt: str | None = None
    error_code: str | None = None
    executed_at: datetime | None = None


class CommandOut(BusinessSchema):
    id: int
    tenant_id: int
    capability: str
    capability_version: int
    approval_policy: str
    actor_user_id: int
    connection_id: int | None = None
    idempotency_key: str
    target_refs: list[str]
    payload_hash: str
    approved_payload_hash: str | None = None
    preview_snapshot: dict[str, Any]
    state: str
    approved_user_id: int | None = None
    approved_at: datetime | None = None
    approval_expires_at: datetime | None = None
    claimed_at: datetime | None = None
    executed_at: datetime | None = None
    outcome_summary: dict[str, Any] | None = None
    error_code: str | None = None
    reconcile_hint: str | None = None
    targets: list[CommandTargetOut] = Field(default_factory=list)


class CommandPreviewIn(BusinessSchema):
    capability: str = Field(min_length=3, max_length=80)
    target_refs: list[str] = Field(min_length=1, max_length=100)
    payload: dict[str, Any]
    preview_snapshot: dict[str, Any] | None = None
    connection_id: int | None = Field(default=None, gt=0)


class CommandApproveIn(BusinessSchema):
    expected_payload_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    ttl_seconds: int = Field(default=DEFAULT_TTL_SECONDS, ge=60, le=MAX_SCHEDULED_TTL_SECONDS)


class CommandReconcileIn(BusinessSchema):
    resolved_state: str = Field(pattern=r"^(succeeded|failed)$")
    provider_receipts: dict[str, str] | None = None


router = business_router("commands", permission=require_model_perm("botaction", ActionType.VIEW))


def _translate_error(exc: Exception) -> HTTPException:
    if isinstance(exc, CommandNotFound):
        return HTTPException(404)
    if isinstance(exc, CommandConflict):
        return HTTPException(409)
    if isinstance(exc, CommandTimeoutUnknown):
        return HTTPException(503)
    if isinstance(exc, CommandDenied):
        return HTTPException(403)
    return HTTPException(400)


@router.get("", response_model=BusinessPage[CommandOut])
async def list_commands_endpoint(
    limit: int = Query(50, ge=1, le=100),
    before_id: int | None = Query(None, gt=0),
):
    try:
        page = await command_ledger.list_commands(limit=limit, before_id=before_id)
        return BusinessPage[CommandOut].model_validate(page)
    except Exception as exc:
        raise _translate_error(exc) from None


@router.get("/{command_id}", response_model=CommandOut)
async def get_command_endpoint(command_id: int = Path(gt=0)):
    try:
        data = await command_ledger.get_command(command_id)
        return CommandOut.model_validate(data)
    except Exception as exc:
        raise _translate_error(exc) from None


@router.post(
    "/preview",
    response_model=CommandOut,
    openapi_extra={"x-creates-command": True},
    dependencies=[Depends(require_model_perm("botaction", ActionType.CREATE))],
)
async def preview_command_endpoint(
    body: CommandPreviewIn,
    idempotency_key: str = Depends(require_idempotency_key),
):
    try:
        data = await command_ledger.preview_command(
            capability=body.capability,
            idempotency_key=idempotency_key,
            target_refs=body.target_refs,
            payload=body.payload,
            preview_snapshot=body.preview_snapshot,
            connection_id=body.connection_id,
        )
        return CommandOut.model_validate(data)
    except Exception as exc:
        raise _translate_error(exc) from None


@router.post(
    "/{command_id}/approve",
    response_model=CommandOut,
    dependencies=[Depends(require_model_perm("botaction", ActionType.UPDATE))],
)
async def approve_command_endpoint(body: CommandApproveIn, command_id: int = Path(gt=0)):
    try:
        data = await command_ledger.approve_command(
            command_id,
            expected_payload_hash=body.expected_payload_hash,
            ttl_seconds=body.ttl_seconds,
        )
        return CommandOut.model_validate(data)
    except Exception as exc:
        raise _translate_error(exc) from None


@router.post(
    "/{command_id}/reject",
    response_model=CommandOut,
    dependencies=[Depends(require_model_perm("botaction", ActionType.UPDATE))],
)
async def reject_command_endpoint(command_id: int = Path(gt=0)):
    try:
        data = await command_ledger.reject_command(command_id)
        return CommandOut.model_validate(data)
    except Exception as exc:
        raise _translate_error(exc) from None


@router.post(
    "/{command_id}/execute",
    response_model=CommandOut,
    dependencies=[Depends(require_model_perm("botaction", ActionType.UPDATE))],
)
async def execute_command_endpoint(command_id: int = Path(gt=0)):
    try:
        data = await command_ledger.execute_command(command_id)
        return CommandOut.model_validate(data)
    except Exception as exc:
        raise _translate_error(exc) from None


@router.post(
    "/{command_id}/reconcile",
    response_model=CommandOut,
    dependencies=[Depends(require_model_perm("botaction", ActionType.UPDATE))],
)
async def reconcile_command_endpoint(body: CommandReconcileIn, command_id: int = Path(gt=0)):
    try:
        data = await command_ledger.reconcile_unknown(
            command_id,
            resolved_state=body.resolved_state,
            provider_receipts=body.provider_receipts,
        )
        return CommandOut.model_validate(data)
    except Exception as exc:
        raise _translate_error(exc) from None


validate_business_routes(router)
