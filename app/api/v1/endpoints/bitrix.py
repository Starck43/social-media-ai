"""REST endpoints for Bitrix24 connections, sync, CRM cards, greetings, and batch messaging.

Mounted at `/api/v1/integrations/bitrix` (P14-T01..T06).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, Request
from pydantic import Field

from app.api.deps import require_model_perm
from app.api.v1.conventions import BusinessRoute, require_business_scope, validate_business_routes
from app.integrations.bitrix24 import Bitrix24Error, bitrix24_service
from app.models import User
from app.schemas.business import BusinessSchema
from app.services.crm.cards import crm_contact_card_service
from app.services.crm.greetings import crm_greetings_service
from app.services.crm.messaging import crm_messaging_service
from app.services.crm.sync import bitrix_sync_service
from app.services.user.auth import get_authenticated_user
from app.types import ActionType


def _bitrix_http_error(exc: Bitrix24Error) -> HTTPException:
    # Only typed, value-safe trust refusals may expose their host-facing message.
    trust_codes = {"host_not_trusted", "address_not_authorized", "unsafe_address", "untrusted_oauth_host"}
    if exc.status_code == 422 and exc.code in trust_codes:
        return HTTPException(status_code=422, detail={"code": exc.code, "message": str(exc)})
    return HTTPException(status_code=exc.status_code)


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


class GreetingSendRequest(BusinessSchema):
    greeting_id: int | None = Field(default=None, gt=0)
    contact_id: int | None = Field(default=None, gt=0)
    event_type: str = Field(default="birthday", pattern=r"^(birthday|anniversary)$")
    year: int | None = Field(default=None, ge=1970, le=2100)
    channel: str | None = Field(default=None, pattern=r"^(email|telegram|max)$")
    connection_id: int = Field(..., gt=0)
    identity_id: int | None = Field(default=None, gt=0)
    scheduled_at: datetime | None = None
    ttl_seconds: int = Field(default=86400, ge=60, le=2592000)
    timezone: str = Field(default="Europe/Moscow", min_length=1, max_length=64)
    idempotency_key: str | None = Field(default=None, max_length=96)


class GreetingSendOut(BusinessSchema):
    id: int
    tenant_id: int
    contact_id: int
    event_type: str
    event_date: date
    year: int
    channel: str
    status: str
    command_id: int | None = None
    outbox_message_id: int
    outbox_target_id: int
    outbox_state: str
    scheduled_at: datetime | None = None
    unsubscribe_url: str
    send_enabled: bool = True


class GreetingDeliveryStatusOut(BusinessSchema):
    id: int
    tenant_id: int
    contact_id: int
    event_type: str
    event_date: date
    year: int
    channel: str | None = None
    status: str
    command_id: int | None = None
    sent_at: datetime | None = None
    outbox_message_id: int | None = None
    outbox_target_id: int | None = None
    outbox_state: str | None = None
    provider_message_id: str | None = None
    error_code: str | None = None


class ManualConsentRecordRequest(BusinessSchema):
    channel: str = Field(..., pattern=r"^(email|telegram|max)$")
    purpose: Literal["transactional", "greeting", "marketing"] = "marketing"
    action: Literal["grant", "withdraw"] = "grant"
    basis: str = Field(..., min_length=1, max_length=500)
    source_evidence: str = Field(..., min_length=1, max_length=2000)
    occurred_at: datetime | None = None


class ManualConsentRecordOut(BusinessSchema):
    id: int
    tenant_id: int
    contact_id: int
    channel: str
    purpose: str
    action: str
    granted_at: datetime | None = None
    withdrawn_at: datetime | None = None
    recorded_by_user_id: int
    audit_event_id: int | None = None


class BatchSendRequest(BusinessSchema):
    contact_ids: list[int] = Field(..., min_length=1, max_length=500)
    channel: str = Field(..., pattern=r"^(email|telegram|max)$")
    connection_id: int = Field(..., gt=0)
    subject_template: str = Field(default="", max_length=500)
    body_template: str = Field(..., min_length=1, max_length=16000)
    purpose: Literal["marketing", "greeting"] = "marketing"
    scheduled_at: datetime | None = None
    ttl_seconds: int = Field(default=86400, ge=60, le=2592000)
    timezone: str = Field(default="Europe/Moscow", min_length=1, max_length=64)
    idempotency_key: str = Field(..., pattern=r"^[A-Za-z0-9_.:-]{1,96}$")


class BatchTargetItemOut(BusinessSchema):
    contact_id: int
    status: str
    reason: str | None = None
    outbox_message_id: int | None = None
    outbox_target_id: int | None = None
    command_id: int | None = None
    provider_message_id: str | None = None
    error_code: str | None = None
    unsubscribe_url: str | None = None


class BatchSendOut(BusinessSchema):
    batch_id: int
    tenant_id: int
    channel: str
    purpose: str
    state: str
    requested_count: int
    scheduled_count: int
    skipped_no_consent_count: int
    skipped_no_consent_contact_ids: list[int] = Field(default_factory=list)
    skipped_no_route_count: int
    skipped_no_route_contact_ids: list[int] = Field(default_factory=list)
    coverage_warning: str | None = None
    counts: dict[str, int] = Field(default_factory=dict)
    items: list[BatchTargetItemOut] = Field(default_factory=list)


class BatchStatusOut(BusinessSchema):
    batch_id: int
    tenant_id: int
    channel: str
    purpose: str
    state: str
    requested_count: int
    scheduled_count: int
    skipped_no_consent_count: int
    skipped_no_consent_contact_ids: list[int] = Field(default_factory=list)
    skipped_no_route_count: int
    skipped_no_route_contact_ids: list[int] = Field(default_factory=list)
    coverage_warning: str | None = None
    counts: dict[str, int] = Field(default_factory=dict)
    items: list[BatchTargetItemOut] = Field(default_factory=list)


class OneClickUnsubscribeRequest(BusinessSchema):
    token: str = Field(..., min_length=8, max_length=1024)


class OneClickUnsubscribeOut(BusinessSchema):
    unsubscribed: bool = True
    tenant_id: int
    contact_id: int
    channel: str
    purpose: str
    withdrawn_at: datetime
    cancelled_outbox_targets: int = 0
    message: str


router = APIRouter(
    tags=["CRM"],
    dependencies=[Depends(require_business_scope), Depends(require_model_perm("source", ActionType.VIEW))],
    route_class=BusinessRoute,
)

# Public router (mounted without API_AUTH): one-click unsubscribe requires no login (D16).
public_router = APIRouter(
    tags=["CRM"],
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
        raise _bitrix_http_error(exc) from None


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
        raise _bitrix_http_error(exc) from None


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
        raise _bitrix_http_error(exc) from None


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
        raise _bitrix_http_error(exc) from None


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
        raise _bitrix_http_error(exc) from None


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
        raise _bitrix_http_error(exc) from None


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
        raise _bitrix_http_error(exc) from None


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
        raise _bitrix_http_error(exc) from None


@router.post(
    "/contacts/{contact_id}/consents",
    response_model=ManualConsentRecordOut,
    status_code=201,
    dependencies=[Depends(require_model_perm("contact", ActionType.UPDATE))],
)
async def record_bitrix_contact_consent(
    body: ManualConsentRecordRequest,
    request: Request,
    contact_id: int = Path(gt=0),
    current_user: User = Depends(get_authenticated_user),
):
    tenant_id = getattr(request.state, "tenant_id", None)
    try:
        data = await crm_messaging_service.record_manual_consent(
            tenant_id=tenant_id,
            actor_id=current_user.id,
            contact_id=contact_id,
            channel=body.channel,
            purpose=body.purpose,
            action=body.action,
            basis=body.basis,
            source_evidence=body.source_evidence,
            occurred_at=body.occurred_at,
        )
        return ManualConsentRecordOut.model_validate(data)
    except Bitrix24Error as exc:
        raise _bitrix_http_error(exc) from None


@router.get("/greetings/upcoming", response_model=UpcomingGreetingEventsOut)
async def list_bitrix_upcoming_greetings(
    request: Request,
    horizon_days: int = Query(default=14, ge=1, le=366),
    reminder_days_only: bool = Query(default=False),
    current_user: User = Depends(get_authenticated_user),
):
    tenant_id = getattr(request.state, "tenant_id", None)
    try:
        data = await crm_greetings_service.list_upcoming_events(
            tenant_id=tenant_id,
            actor_id=current_user.id,
            horizon_days=horizon_days,
            reminder_days_only=reminder_days_only,
        )
        return UpcomingGreetingEventsOut.model_validate(data)
    except Bitrix24Error as exc:
        raise _bitrix_http_error(exc) from None


@router.post(
    "/greetings/draft",
    response_model=GreetingDraftOut,
    status_code=201,
    dependencies=[Depends(require_model_perm("source", ActionType.CREATE))],
)
async def create_bitrix_greeting_draft(
    body: GreetingDraftCreateRequest,
    request: Request,
    current_user: User = Depends(get_authenticated_user),
):
    tenant_id = getattr(request.state, "tenant_id", None)
    try:
        data = await crm_greetings_service.generate_greeting_draft(
            tenant_id=tenant_id,
            actor_id=current_user.id,
            contact_id=body.contact_id,
            event_type=body.event_type,
            year=body.year,
            channel=body.channel,
            tone=body.tone,
        )
        return GreetingDraftOut.model_validate(data)
    except Bitrix24Error as exc:
        raise _bitrix_http_error(exc) from None


@router.post(
    "/greetings/send",
    response_model=GreetingSendOut,
    dependencies=[Depends(require_model_perm("comms.outbox", ActionType.CONFIGURE))],
)
async def send_bitrix_greeting(
    body: GreetingSendRequest,
    request: Request,
    current_user: User = Depends(get_authenticated_user),
):
    tenant_id = getattr(request.state, "tenant_id", None)
    try:
        data = await crm_greetings_service.send_greeting(
            tenant_id=tenant_id,
            actor_id=current_user.id,
            connection_id=body.connection_id,
            greeting_id=body.greeting_id,
            contact_id=body.contact_id,
            event_type=body.event_type,
            year=body.year,
            channel=body.channel,
            identity_id=body.identity_id,
            scheduled_at=body.scheduled_at,
            ttl_seconds=body.ttl_seconds,
            timezone_name=body.timezone,
            idempotency_key=body.idempotency_key,
        )
        return GreetingSendOut.model_validate(data)
    except Bitrix24Error as exc:
        raise _bitrix_http_error(exc) from None


@router.get(
    "/greetings/{greeting_id}/status",
    response_model=GreetingDeliveryStatusOut,
    dependencies=[Depends(require_model_perm("comms.outbox", ActionType.VIEW))],
)
async def get_bitrix_greeting_delivery_status(
    request: Request,
    greeting_id: int = Path(gt=0),
    current_user: User = Depends(get_authenticated_user),
):
    tenant_id = getattr(request.state, "tenant_id", None)
    try:
        data = await crm_greetings_service.get_greeting_delivery_status(
            tenant_id=tenant_id,
            actor_id=current_user.id,
            greeting_id=greeting_id,
        )
        return GreetingDeliveryStatusOut.model_validate(data)
    except Bitrix24Error as exc:
        raise _bitrix_http_error(exc) from None


@router.post(
    "/messaging/batch",
    response_model=BatchSendOut,
    status_code=202,
    dependencies=[Depends(require_model_perm("comms.outbox", ActionType.CONFIGURE))],
)
async def send_bitrix_batch_messages(
    body: BatchSendRequest,
    request: Request,
    current_user: User = Depends(get_authenticated_user),
):
    tenant_id = getattr(request.state, "tenant_id", None)
    try:
        data = await crm_messaging_service.send_batch_messages(
            tenant_id=tenant_id,
            actor_id=current_user.id,
            contact_ids=body.contact_ids,
            channel=body.channel,
            connection_id=body.connection_id,
            subject_template=body.subject_template,
            body_template=body.body_template,
            purpose=body.purpose,
            scheduled_at=body.scheduled_at,
            ttl_seconds=body.ttl_seconds,
            timezone_name=body.timezone,
            idempotency_key=body.idempotency_key,
        )
        return BatchSendOut.model_validate(data)
    except Bitrix24Error as exc:
        raise _bitrix_http_error(exc) from None


@router.get(
    "/messaging/status/{batch_id}",
    response_model=BatchStatusOut,
    dependencies=[Depends(require_model_perm("comms.outbox", ActionType.VIEW))],
)
async def get_bitrix_batch_messaging_status(
    request: Request,
    batch_id: int = Path(gt=0),
    current_user: User = Depends(get_authenticated_user),
):
    tenant_id = getattr(request.state, "tenant_id", None)
    try:
        data = await crm_messaging_service.get_batch_status(
            tenant_id=tenant_id,
            actor_id=current_user.id,
            batch_id=batch_id,
        )
        return BatchStatusOut.model_validate(data)
    except Bitrix24Error as exc:
        raise _bitrix_http_error(exc) from None


@public_router.get("/messaging/unsubscribe", response_model=OneClickUnsubscribeOut)
async def get_one_click_unsubscribe(
    token: str = Query(..., min_length=8, max_length=1024),
):
    try:
        data = await crm_messaging_service.process_one_click_unsubscribe(token)
        return OneClickUnsubscribeOut.model_validate(data)
    except Bitrix24Error as exc:
        raise _bitrix_http_error(exc) from None


@public_router.post("/messaging/unsubscribe", response_model=OneClickUnsubscribeOut)
async def post_one_click_unsubscribe(
    token: str | None = Query(default=None, max_length=1024),
    body: OneClickUnsubscribeRequest | None = Body(default=None),
):
    resolved_token = token or (body.token if body is not None else "")
    if not resolved_token:
        raise HTTPException(status_code=422)
    try:
        data = await crm_messaging_service.process_one_click_unsubscribe(resolved_token)
        return OneClickUnsubscribeOut.model_validate(data)
    except Bitrix24Error as exc:
        raise _bitrix_http_error(exc) from None


validate_business_routes(router)
validate_business_routes(public_router)
