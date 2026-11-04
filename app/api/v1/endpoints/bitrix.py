"""REST API v1 endpoints for Bitrix24 portal connections, incremental sync, CRM cards, and greeting drafts (`/api/v1/integrations/bitrix`, P14-T01..T04)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import Field

from app.api.deps import require_model_perm
from app.api.v1.conventions import BusinessRoute, require_business_scope, validate_business_routes
from app.integrations.bitrix24 import Bitrix24Error, bitrix24_service
from app.models import User
from app.schemas.business import BusinessSchema
from app.services.crm.cards import crm_contact_card_service
from app.services.crm.sync import bitrix_sync_service
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


class BitrixSyncTriggerRequest(BusinessSchema):
    connection_id: int = Field(..., gt=0)
    entity_kinds: list[str] = Field(default_factory=lambda: ["contact", "company", "lead", "deal"])


class BitrixSyncRunOut(BusinessSchema):
    connection_id: int
    tenant_id: int
    synced_at: datetime
    counts: dict[str, int] = Field(default_factory=dict)
    watermarks: dict[str, str] = Field(default_factory=dict)
    cursors: dict[str, int] = Field(default_factory=dict)
    deferred: bool = False
    deferral_reason: str | None = None


class BitrixSyncStatusItemOut(BusinessSchema):
    connection_id: int
    portal_domain: str
    state: str
    last_synced_at: str | None = None
    last_counts: dict[str, int] = Field(default_factory=dict)
    entity_totals: dict[str, int] = Field(default_factory=dict)
    watermarks: dict[str, str] = Field(default_factory=dict)
    cursors: dict[str, int] = Field(default_factory=dict)
    deferred: bool = False
    deferral_reason: str | None = None


class BitrixSyncStatusListOut(BusinessSchema):
    items: list[BitrixSyncStatusItemOut]


class CrmContactListItemOut(BusinessSchema):
    id: int
    tenant_id: int
    connection_id: int
    entity_kind: str
    external_id: str
    contact_id: int | None = None
    title: str
    birthday: str | None = None
    birthday_has_year: bool = False
    anniversary: str | None = None
    anniversary_has_year: bool = False
    read_only_dates: bool = True
    portal_url: str | None = None
    source_modified_at: datetime | None = None
    last_synced_at: datetime


class CrmContactListOut(BusinessSchema):
    items: list[CrmContactListItemOut]
    total: int


class CrmIdentityOut(BusinessSchema):
    id: int
    kind: str
    value: str
    connection_id: int | None = None
    verified: bool = False


class CrmDealItemOut(BusinessSchema):
    id: int
    external_id: str
    title: str
    stage_code: str | None = None
    currency_code: str | None = None
    opportunity_amount: float | None = None
    is_closed: bool = False
    portal_url: str | None = None


class CrmDealsSummaryOut(BusinessSchema):
    total_count: int = 0
    open_count: int = 0
    closed_count: int = 0
    total_opportunity_amount: float = 0.0
    deals: list[CrmDealItemOut] = Field(default_factory=list)


class CrmDuplicateCandidateOut(BusinessSchema):
    candidate_contact_id: int | None = None
    candidate_crm_entity_id: int | None = None
    connection_id: int | None = None
    entity_kind: str
    external_id: str | None = None
    display_name: str
    matched_kinds: list[str] = Field(default_factory=list)
    explanation: str


class CrmContactCardOut(BusinessSchema):
    id: int
    tenant_id: int
    connection_id: int
    portal_domain: str | None = None
    entity_kind: str
    external_id: str
    contact_id: int | None = None
    title: str
    stage_code: str | None = None
    status_code: str | None = None
    company_external_id: str | None = None
    assigned_by_external_id: str | None = None
    birthday: str | None = None
    birthday_has_year: bool = False
    anniversary: str | None = None
    anniversary_has_year: bool = False
    birthday_read_only: bool = True
    anniversary_read_only: bool = True
    read_only_dates: bool = True
    personal_fields: dict[str, Any] = Field(default_factory=dict)
    custom_fields: dict[str, Any] = Field(default_factory=dict)
    portal_url: str | None = None
    source_modified_at: datetime | None = None
    last_synced_at: datetime
    identities: list[CrmIdentityOut] = Field(default_factory=list)
    deals_summary: CrmDealsSummaryOut
    duplicate_candidates: list[CrmDuplicateCandidateOut] = Field(default_factory=list)


class UpcomingGreetingEventItemOut(BusinessSchema):
    contact_id: int
    crm_entity_id: int | None = None
    display_name: str
    company_title: str | None = None
    event_type: str
    event_date: date
    year: int
    days_until: int
    is_reminder_day: bool = False
    reminder_bucket: int | None = None
    years_turning: int | None = None
    preferred_channel: str | None = None
    history_id: int | None = None
    history_status: str | None = None
    send_enabled: bool = False


class UpcomingGreetingEventsOut(BusinessSchema):
    reference_date: date
    timezone: str
    reminder_days: list[int] = Field(default_factory=lambda: [7, 3, 1])
    notifications_created: int = 0
    items: list[UpcomingGreetingEventItemOut] = Field(default_factory=list)


class GreetingDraftCreateRequest(BusinessSchema):
    contact_id: int = Field(..., gt=0)
    event_type: str = Field(default="birthday", pattern=r"^(birthday|anniversary)$")
    year: int | None = Field(default=None, ge=1970, le=2100)
    channel: str | None = Field(default=None, pattern=r"^(email|phone|telegram|max|bitrix24)$")
    tone: str = Field(default="warm_professional", max_length=40)


class GreetingDraftOut(BusinessSchema):
    id: int
    tenant_id: int
    contact_id: int
    display_name: str
    event_type: str
    event_date: date
    year: int
    channel: str | None = None
    status: str
    ai_generated: bool = True
    subject: str | None = None
    draft_text: str
    send_enabled: bool = False


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


@router.get("/contacts", response_model=CrmContactListOut)
async def list_bitrix_crm_contacts(
    request: Request,
    entity_kind: str | None = Query(default=None),
    updated_since: datetime | None = Query(default=None),
    has_birthday: bool | None = Query(default=None),
    connection_id: int | None = Query(default=None, gt=0),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: User = Depends(get_authenticated_user),
):
    tenant_id = getattr(request.state, "tenant_id", None)
    try:
        data = await crm_contact_card_service.list_crm_contacts(
            tenant_id=tenant_id,
            actor_id=current_user.id,
            entity_kind=entity_kind,
            updated_since=updated_since,
            has_birthday=has_birthday,
            connection_id=connection_id,
            limit=limit,
        )
        return CrmContactListOut.model_validate(data)
    except Bitrix24Error as exc:
        raise HTTPException(status_code=exc.status_code) from None


@router.post(
    "/contacts/sync",
    response_model=BitrixSyncRunOut,
    dependencies=[Depends(require_model_perm("source", ActionType.CREATE))],
)
async def trigger_bitrix_contacts_sync(
    body: BitrixSyncTriggerRequest,
    request: Request,
    current_user: User = Depends(get_authenticated_user),
):
    tenant_id = getattr(request.state, "tenant_id", None)
    try:
        result = await bitrix_sync_service.sync_connection(
            tenant_id=tenant_id,
            actor_id=current_user.id,
            connection_id=body.connection_id,
            entity_kinds=tuple(body.entity_kinds),
        )
        return BitrixSyncRunOut.model_validate(result)
    except Bitrix24Error as exc:
        raise HTTPException(status_code=exc.status_code) from None


@router.get("/contacts/sync/status", response_model=BitrixSyncStatusListOut)
async def get_bitrix_contacts_sync_status(
    request: Request,
    connection_id: int | None = Query(default=None, gt=0),
    current_user: User = Depends(get_authenticated_user),
):
    tenant_id = getattr(request.state, "tenant_id", None)
    try:
        data = await bitrix_sync_service.get_sync_status(
            tenant_id=tenant_id,
            actor_id=current_user.id,
            connection_id=connection_id,
        )
        return BitrixSyncStatusListOut.model_validate(data)
    except Bitrix24Error as exc:
        raise HTTPException(status_code=exc.status_code) from None


@router.get("/contacts/{crm_entity_id}", response_model=CrmContactCardOut)
async def get_bitrix_crm_contact_card(
    request: Request,
    crm_entity_id: int = Path(gt=0),
    current_user: User = Depends(get_authenticated_user),
):
    tenant_id = getattr(request.state, "tenant_id", None)
    try:
        data = await crm_contact_card_service.get_crm_contact_card(
            tenant_id=tenant_id,
            actor_id=current_user.id,
            crm_entity_id=crm_entity_id,
        )
        return CrmContactCardOut.model_validate(data)
    except Bitrix24Error as exc:
        raise HTTPException(status_code=exc.status_code) from None


validate_business_routes(router)
