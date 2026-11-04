"""REST API v1 endpoints for Bitrix24 portal connections (`/api/v1/integrations/bitrix`, P14-T01)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import Field

from app.api.deps import require_model_perm
from app.api.v1.conventions import BusinessRoute, require_business_scope, validate_business_routes
from app.integrations.bitrix24 import Bitrix24Error, bitrix24_service
from app.models import User
from app.schemas.business import BusinessSchema
from app.services.user.auth import get_authenticated_user
from app.types import ActionType


class BitrixConnectionOut(BusinessSchema):
    id: int
    tenant_id: int
    owner_user_id: int
    platform: str
    provider_id: str
    auth_method: str
    label: str
    account_key: str
    config: dict[str, Any] = Field(default_factory=dict)
    is_active: bool
    state: str
    check_code: str | None = None
    checked_at: datetime | None = None


class BitrixConnectOut(BusinessSchema):
    connection_id: int | None = None
    authorize_url: str | None = None
    state: str | None = None
    connection: BitrixConnectionOut | None = None


class BitrixConnectionsListOut(BusinessSchema):
    items: list[BitrixConnectionOut]


class BitrixConnectRequest(BusinessSchema):
    portal_domain: str = Field(..., min_length=4, max_length=253)
    label: str = Field(default="", max_length=100)
    auth_method: str = Field(default="oauth2", pattern=r"^(oauth2|webhook_secret)$")
    webhook_secret: str | None = Field(default=None, max_length=4096)


router = APIRouter(
    tags=["CRM"],
    dependencies=[Depends(require_business_scope), Depends(require_model_perm("source", ActionType.VIEW))],
    route_class=BusinessRoute,
)


@router.post(
    "/connect",
    response_model=BitrixConnectOut,
    status_code=201,
    dependencies=[Depends(require_model_perm("source", ActionType.CREATE))],
)
async def connect_bitrix_portal(
    body: BitrixConnectRequest,
    request: Request,
    current_user: User = Depends(get_authenticated_user),
):
    tenant_id = getattr(request.state, "tenant_id", None)
    try:
        if body.auth_method == "webhook_secret":
            conn = await bitrix24_service.connect_admin_webhook(
                tenant_id=tenant_id,
                actor_id=current_user.id,
                portal_domain=body.portal_domain,
                webhook_secret=body.webhook_secret or "",
                label=body.label,
            )
            return BitrixConnectOut(connection_id=conn["id"], connection=BitrixConnectionOut.model_validate(conn))
        started = await bitrix24_service.start_oauth_connect(
            tenant_id=tenant_id,
            actor_id=current_user.id,
            portal_domain=body.portal_domain,
            label=body.label,
        )
        return BitrixConnectOut.model_validate(started)
    except Bitrix24Error as exc:
        raise HTTPException(status_code=exc.status_code) from None


@router.get("/connections", response_model=BitrixConnectionsListOut)
async def list_bitrix_connections(
    request: Request,
    current_user: User = Depends(get_authenticated_user),
):
    tenant_id = getattr(request.state, "tenant_id", None)
    try:
        items = await bitrix24_service.list_connections(tenant_id=tenant_id, actor_id=current_user.id)
        return BitrixConnectionsListOut(items=[BitrixConnectionOut.model_validate(i) for i in items])
    except Bitrix24Error as exc:
        raise HTTPException(status_code=exc.status_code) from None


@router.post(
    "/connections/{connection_id}/health",
    response_model=BitrixConnectionOut,
    dependencies=[Depends(require_model_perm("source", ActionType.CREATE))],
)
async def check_bitrix_connection_health(
    request: Request,
    connection_id: int = Path(gt=0),
    current_user: User = Depends(get_authenticated_user),
):
    tenant_id = getattr(request.state, "tenant_id", None)
    try:
        data = await bitrix24_service.check_health(
            tenant_id=tenant_id,
            actor_id=current_user.id,
            connection_id=connection_id,
        )
        return BitrixConnectionOut.model_validate(data)
    except Bitrix24Error as exc:
        raise HTTPException(status_code=exc.status_code) from None


@router.delete(
    "/connections/{connection_id}",
    response_model=BitrixConnectionOut,
    dependencies=[Depends(require_model_perm("source", ActionType.CREATE))],
)
async def delete_bitrix_connection(
    request: Request,
    connection_id: int = Path(gt=0),
    current_user: User = Depends(get_authenticated_user),
):
    tenant_id = getattr(request.state, "tenant_id", None)
    try:
        data = await bitrix24_service.delete_connection(
            tenant_id=tenant_id,
            actor_id=current_user.id,
            connection_id=connection_id,
        )
        return BitrixConnectionOut.model_validate(data)
    except Bitrix24Error as exc:
        raise HTTPException(status_code=exc.status_code) from None


validate_business_routes(router)
