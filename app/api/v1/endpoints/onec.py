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
from app.services.onec.export import OneCExportError
from app.services.onec.history import OneCHistoryEntry, OneCHistoryError, OneCQueryHistoryRepository
from app.services.onec.nl_query import OneCNaturalLanguageError, OneCQueryAnswerService
from app.services.onec.query import OneCQueryError
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
    await _call(OneCConnectionService().disable, **_scope(request, user), connection_id=connection_id)


@router.patch("/connections/{connection_id}/credentials", response_model=ConnectionOutput)
async def replace_credentials(
    body: CredentialsInput,
    request: Request,
    connection_id: int = Path(gt=0),
    user: User = Depends(get_authenticated_user),
):
    return await _call(
        OneCConnectionService().replace_credentials,
        **_scope(request, user),
        connection_id=connection_id,
        login=body.login.get_secret_value(),
        password=body.password.get_secret_value(),
    )


@router.post("/connections/{connection_id}/health-check", response_model=ConnectionOutput)
async def health_check(request: Request, connection_id: int = Path(gt=0), user: User = Depends(get_authenticated_user)):
    return await _call(OneCHealthService().check, **_scope(request, user), connection_id=connection_id)


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
