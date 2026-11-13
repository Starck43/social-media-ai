"""1C connections and workspace host approvals; never echo rejected inputs."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.exceptions import RequestValidationError, ResponseValidationError
from fastapi.responses import Response
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.integrations.onec.connections import OneCConnectionError, OneCConnectionService
from app.integrations.onec.credentials import OneCCredentialError
from app.integrations.onec.endpoint import OneCEndpointError
from app.integrations.onec.health import OneCHealthService
from app.models import User
from app.models.managers.trusted_outbound_host_manager import TrustedHostError
from app.models.managers.workspace_connection_manager import ConnectionDenied
from app.models.managers.workspace_outbound_host_manager import WorkspaceHostError, WorkspaceOutboundHostManager
from app.services.onec.commands import (
    OneCCommandAuditEntry,
    OneCCommandError,
    OneCCommandRecord,
    OneCCommandService,
    OneCWritePreviewRequest,
)
from app.services.onec.export import OneCExportError
from app.services.onec.history import OneCHistoryEntry, OneCHistoryError, OneCQueryHistoryRepository
from app.services.onec.nl_query import OneCNaturalLanguageError, OneCQueryAnswerService
from app.services.onec.query import OneCQueryError, invalidate_metadata_catalog
from app.services.user.auth import get_authenticated_user


class PrivateOneCRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def handle(request):
            try:
                return await handler(request)
            except RequestValidationError:
                raise HTTPException(422, "invalid_request") from None
            except ResponseValidationError:
                raise HTTPException(500, "response_unavailable") from None

        return handle


class ErrorOutput(BaseModel):
    detail: str


router = APIRouter(
    route_class=PrivateOneCRoute,
    responses={
        400: {"model": ErrorOutput, "description": "Fixed input or connector error code"},
        401: {"model": ErrorOutput, "description": "Bearer authentication required"},
        404: {"model": ErrorOutput, "description": "Connection or approval unavailable"},
        409: {"model": ErrorOutput, "description": "Command state or DataVersion conflict"},
        422: {"model": ErrorOutput, "description": "Invalid request; raw input is never echoed"},
        500: {"model": ErrorOutput, "description": "Response unavailable"},
    },
)


class ConnectInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=100, repr=False)
    base_url: str = Field(min_length=1, max_length=2048, repr=False)
    interface: str = Field(pattern="^(odata_standard|http_service)$")
    login: SecretStr = Field(min_length=1, max_length=320)
    password: SecretStr = Field(min_length=1, max_length=4096)


class CredentialsInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid", json_schema_extra={"examples": [{"login": "demo", "password": "synthetic-only"}]}
    )
    login: SecretStr = Field(min_length=1, max_length=320)
    password: SecretStr = Field(min_length=1, max_length=4096)


class HostInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host: str = Field(min_length=1, max_length=253)


class HostOutput(BaseModel):
    id: int
    host: str


class ConnectionOutput(BaseModel):
    id: int
    label: str
    host: str
    port: int
    base_path: str
    interface: str
    state: str
    check_code: str | None
    checked_at: datetime | None
    is_active: bool


def _scope(request, user):
    return {"tenant_id": getattr(request.state, "tenant_id", None), "actor_id": user.id}


async def _call(fn, **kwargs):
    try:
        return await fn(**kwargs)
    except ConnectionDenied:
        raise HTTPException(404, "connection_unavailable") from None
    except (OneCConnectionError, OneCCredentialError, OneCEndpointError, TrustedHostError, WorkspaceHostError) as exc:
        raise HTTPException(400, exc.code) from None


@router.post("/connect", response_model=ConnectionOutput, status_code=201)
async def connect(body: ConnectInput, request: Request, user: User = Depends(get_authenticated_user)):
    return await _call(
        OneCConnectionService().create,
        **_scope(request, user),
        label=body.label,
        base_url=body.base_url,
        interface=body.interface,
        login=body.login.get_secret_value(),
        password=body.password.get_secret_value(),
    )


@router.get("/connections", response_model=list[ConnectionOutput])
async def connections(request: Request, user: User = Depends(get_authenticated_user)):
    return await _call(OneCConnectionService().list_for_actor, **_scope(request, user))


@router.delete("/connections/{connection_id}", status_code=204)
async def disconnect(request: Request, connection_id: int = Path(gt=0), user: User = Depends(get_authenticated_user)):
    scope = _scope(request, user)
    await _call(OneCConnectionService().disable, **scope, connection_id=connection_id)
    invalidate_metadata_catalog(scope["tenant_id"], connection_id)


@router.patch("/connections/{connection_id}/credentials", response_model=ConnectionOutput)
async def replace_credentials(
    body: CredentialsInput,
    request: Request,
    connection_id: int = Path(gt=0),
    user: User = Depends(get_authenticated_user),
):
    scope = _scope(request, user)
    result = await _call(
        OneCConnectionService().replace_credentials,
        **scope,
        connection_id=connection_id,
        login=body.login.get_secret_value(),
        password=body.password.get_secret_value(),
    )
    invalidate_metadata_catalog(scope["tenant_id"], connection_id)
    return result


@router.post("/connections/{connection_id}/health-check", response_model=ConnectionOutput)
async def health_check(request: Request, connection_id: int = Path(gt=0), user: User = Depends(get_authenticated_user)):
    scope = _scope(request, user)
    result = await _call(OneCHealthService().check, **scope, connection_id=connection_id)
    invalidate_metadata_catalog(scope["tenant_id"], connection_id)
    return result


@router.get("/hosts", response_model=list[HostOutput])
async def hosts(request: Request, user: User = Depends(get_authenticated_user)):
    return await _call(WorkspaceOutboundHostManager().list_hosts, **_scope(request, user))


@router.post("/hosts", response_model=HostOutput, status_code=201)
async def approve_host(body: HostInput, request: Request, user: User = Depends(get_authenticated_user)):
    return await _call(WorkspaceOutboundHostManager().approve, **_scope(request, user), host=body.host)


@router.delete("/hosts/{host_id}", status_code=204)
async def revoke_host(request: Request, host_id: int = Path(gt=0), user: User = Depends(get_authenticated_user)):
    await _call(WorkspaceOutboundHostManager().revoke, **_scope(request, user), host_id=host_id)


class QueryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_id: int = Field(gt=0)
    question: str | None = Field(default=None, min_length=1, max_length=1000)
    history_id: int | None = Field(default=None, gt=0)
    cache_ttl_seconds: int = Field(default=0, ge=0, le=300)
    page_token: str | None = Field(default=None, max_length=256)


class ChartPoint(BaseModel):
    label: str
    value: str


class QueryOutput(BaseModel):
    connection_id: int
    history_id: int | None
    needs_clarification: bool
    clarification: str | None
    interpretation: str | None
    plan_fingerprint: str | None
    summary: str | None
    columns: list[str]
    rows: list[dict[str, str]]
    row_count: int
    truncated: bool
    chart: list[ChartPoint]
    next_page_token: str | None


class ExportInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_id: int = Field(gt=0)
    format: Literal["xlsx", "pdf"]
    question: str | None = Field(default=None, max_length=1000)
    history_id: int | None = Field(default=None, gt=0)
    title: str | None = Field(default=None, max_length=120)


class HistoryOutput(BaseModel):
    id: int
    connection_id: int
    entity_set: str
    plan_fingerprint: str
    row_count: int
    duration_ms: int
    status: str
    exported_format: str | None
    created_at: datetime


def _query_scope(request, user):
    return {"tenant_id": getattr(request.state, "tenant_id", None), "actor_id": user.id}


async def _call_query(fn, **kwargs):
    try:
        return await fn(**kwargs)
    except ConnectionDenied:
        raise HTTPException(404, "connection_unavailable") from None
    except OneCNaturalLanguageError as exc:
        raise HTTPException(400, exc.code) from None
    except OneCQueryError as exc:
        raise HTTPException(400, exc.code) from None
    except OneCExportError as exc:
        raise HTTPException(400, exc.code) from None
    except OneCHistoryError as exc:
        raise HTTPException(400, exc.code) from None


@router.post("/query", response_model=QueryOutput)
async def run_query(body: QueryInput, request: Request, user: User = Depends(get_authenticated_user)):
    scope = _query_scope(request, user)
    if body.history_id is None and not (body.question or "").strip():
        raise HTTPException(400, "question_required")
    answer = await _call_query(
        OneCQueryAnswerService().answer,
        **scope,
        connection_id=body.connection_id,
        question=body.question,
        history=OneCQueryHistoryRepository(),
        cache_ttl_seconds=body.cache_ttl_seconds,
        page_token=body.page_token,
        history_id=body.history_id,
    )
    if answer.clarification is not None:
        return QueryOutput(
            connection_id=body.connection_id,
            history_id=None,
            needs_clarification=True,
            clarification=answer.clarification,
            interpretation=None,
            plan_fingerprint=None,
            summary=None,
            columns=[],
            rows=[],
            row_count=0,
            truncated=False,
            chart=[],
            next_page_token=None,
        )
    table = answer.table
    assert table is not None
    return QueryOutput(
        connection_id=body.connection_id,
        history_id=answer.history_id,
        needs_clarification=False,
        clarification=None,
        interpretation=answer.interpretation.description if answer.interpretation else None,
        plan_fingerprint=answer.interpretation.validated.plan_fingerprint if answer.interpretation else None,
        summary=table.summary,
        columns=list(table.columns),
        rows=[dict(zip(table.columns, row)) for row in table.rows],
        row_count=len(table.rows),
        truncated=table.truncated,
        chart=[ChartPoint(label=label, value=value) for label, value in table.chart],
        next_page_token=answer.result.next_page_token if answer.result else None,
    )


@router.get("/query/history", response_model=list[HistoryOutput])
async def query_history(request: Request, user: User = Depends(get_authenticated_user)):
    entries: tuple[OneCHistoryEntry, ...] = await _call_query(
        OneCQueryHistoryRepository().list_for_actor, **_query_scope(request, user)
    )
    return [
        HistoryOutput(
            id=entry.id,
            connection_id=entry.connection_id,
            entity_set=entry.entity_set,
            plan_fingerprint=entry.plan_fingerprint,
            row_count=entry.row_count,
            duration_ms=entry.duration_ms,
            status=entry.status,
            exported_format=entry.exported_format,
            created_at=entry.created_at,
        )
        for entry in entries
    ]


@router.post("/query/export")
async def export_query(body: ExportInput, request: Request, user: User = Depends(get_authenticated_user)):
    file, _history_id = await _call_query(
        OneCQueryAnswerService().export,
        **_query_scope(request, user),
        connection_id=body.connection_id,
        format=body.format,
        history=OneCQueryHistoryRepository(),
        question=body.question,
        history_id=body.history_id,
        title=body.title,
    )
    return Response(
        content=file.content,
        media_type=file.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{file.filename}"',
            "X-Content-SHA256": file.sha256,
            "X-Row-Count": str(file.row_count),
        },
    )


class CommandPreviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_id: int = Field(gt=0)
    capability: Literal[
        "onec.document.create.v1",
        "onec.document.post.v1",
        "onec.catalog.update.v1",
    ]
    idempotency_key: str = Field(min_length=1, max_length=120)
    entity_set: str = Field(min_length=1, max_length=128)
    target_guid: str | None = Field(default=None, min_length=36, max_length=36)
    fields: dict[str, object] = Field(default_factory=dict)
    posting_mode_operational: bool = False
    default_currency: str = Field(default="RUB", min_length=3, max_length=3)


class CommandApproveInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command_id: int = Field(gt=0)
    expected_payload_hash: str = Field(min_length=71, max_length=71)
    ttl_seconds: int = Field(default=900, ge=60, le=3600)


class CommandExecuteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command_id: int = Field(gt=0)


class CommandReconcileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command_id: int = Field(gt=0)


class CommandRecordOutput(BaseModel):
    id: int
    tenant_id: int
    actor_id: int
    connection_id: int
    capability: str
    approval_policy: str
    idempotency_key: str
    external_ref: str
    entity_set: str
    target_ref: str
    target_guid: str | None
    expected_data_version: str | None
    payload_hash: str
    approved_payload_hash: str | None
    state: str
    preview_snapshot: dict[str, object]
    provider_receipt: str | None
    error_code: str | None
    reconcile_hint: str | None
    approved_by: int | None
    approved_at: datetime | None
    approval_expires_at: datetime | None
    executed_at: datetime | None
    created_at: datetime


class CommandAuditOutput(BaseModel):
    event_id: str
    command_id: int | None
    tenant_id: int
    actor_id: int | None
    action: str
    outcome: str
    capability: str | None
    entity_set: str | None
    target_ref: str | None
    state: str | None
    payload_hash: str | None
    provider_receipt: str | None
    reason_code: str | None
    created_at: datetime


_CONFLICT_COMMAND_CODES = frozenset(
    {
        "data_version_conflict",
        "command_state_conflict",
        "approval_hash_mismatch",
        "write_timeout_unknown",
    }
)


async def _call_command(fn, **kwargs):
    try:
        return await fn(**kwargs)
    except ConnectionDenied:
        raise HTTPException(404, "connection_unavailable") from None
    except OneCCommandError as exc:
        if exc.code == "command_not_found":
            raise HTTPException(404, exc.code) from None
        if exc.code in _CONFLICT_COMMAND_CODES:
            raise HTTPException(409, exc.code) from None
        raise HTTPException(400, exc.code) from None
    except OneCConnectionError as exc:
        raise HTTPException(400, exc.code) from None


@router.post("/command/preview", response_model=CommandRecordOutput)
async def preview_onec_command(
    body: CommandPreviewInput,
    request: Request,
    user: User = Depends(get_authenticated_user),
):
    record: OneCCommandRecord = await _call_command(
        OneCCommandService().preview_command,
        **_scope(request, user),
        connection_id=body.connection_id,
        request=OneCWritePreviewRequest(
            capability=body.capability,
            idempotency_key=body.idempotency_key,
            entity_set=body.entity_set,
            target_guid=body.target_guid,
            fields=body.fields,
            posting_mode_operational=body.posting_mode_operational,
            default_currency=body.default_currency,
        ),
    )
    return CommandRecordOutput(**record.to_dict())


@router.post("/command/approve", response_model=CommandRecordOutput)
async def approve_onec_command(
    body: CommandApproveInput,
    request: Request,
    user: User = Depends(get_authenticated_user),
):
    record: OneCCommandRecord = await _call_command(
        OneCCommandService().approve_command,
        **_scope(request, user),
        command_id=body.command_id,
        expected_payload_hash=body.expected_payload_hash,
        ttl_seconds=body.ttl_seconds,
    )
    return CommandRecordOutput(**record.to_dict())


@router.post("/command/execute", response_model=CommandRecordOutput)
async def execute_onec_command(
    body: CommandExecuteInput,
    request: Request,
    user: User = Depends(get_authenticated_user),
):
    record: OneCCommandRecord = await _call_command(
        OneCCommandService().execute_command,
        **_scope(request, user),
        command_id=body.command_id,
    )
    return CommandRecordOutput(**record.to_dict())


@router.post("/command/reconcile", response_model=CommandRecordOutput)
async def reconcile_onec_command(
    body: CommandReconcileInput,
    request: Request,
    user: User = Depends(get_authenticated_user),
):
    record: OneCCommandRecord = await _call_command(
        OneCCommandService().reconcile_command,
        **_scope(request, user),
        command_id=body.command_id,
    )
    return CommandRecordOutput(**record.to_dict())


@router.get("/audit-log", response_model=list[CommandAuditOutput])
async def list_onec_audit_log(
    request: Request,
    limit: int = 50,
    user: User = Depends(get_authenticated_user),
):
    entries: tuple[OneCCommandAuditEntry, ...] = await _call_command(
        OneCCommandService().list_audit_log,
        **_scope(request, user),
        limit=limit,
    )
    return [CommandAuditOutput(**entry.to_dict()) for entry in entries]
