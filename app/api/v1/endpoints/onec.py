"""1C connections and workspace host approvals; never echo rejected inputs."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.exceptions import RequestValidationError, ResponseValidationError
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
