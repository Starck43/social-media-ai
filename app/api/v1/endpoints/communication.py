"""REST API v1 endpoints for unified inbox threads, chronological messages, metadata search, and contact identity reconciliation (`/api/v1/communication`, P15-T03..T04)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import Field

from app.api.deps import require_model_perm
from app.api.v1.conventions import BusinessRoute, require_business_scope, validate_business_routes
from app.core.database import async_session_maker
from app.models import User
from app.models.managers.contact_manager import ContactUnavailable
from app.schemas.business import BusinessSchema
from app.services.communications.inbox import (
    InboxItemUnavailable,
    archive_thread,
    get_thread_detail,
    hide_message_locally,
    list_contact_messages,
    list_inbox_threads,
    mark_thread_read,
)
from app.services.communications.recommend import recommend_channel
from app.services.contacts.reconcile import (
    ContactMergeConflictError,
    ContactMergeError,
    merge_contacts,
    suggest_contact_merges,
    unmerge_contacts,
)
from app.services.user.auth import get_authenticated_user
from app.types import ActionType


class InboxThreadOut(BusinessSchema):
    id: int
    connection_id: int | None = None
    contact_id: int | None = None
    contact_display_name: str | None = None
    channel: str
    channel_label: str
    channel_badge: str
    thread_key: str
    subject: str | None = None
    participants: list[str] = Field(default_factory=list)
    last_message_at: datetime | None = None
    message_count: int = 0
    unread_count: int = 0
    is_read: bool = True
    is_archived: bool = False


class InboxThreadListOut(BusinessSchema):
    items: list[InboxThreadOut] = Field(default_factory=list)
    next_before_id: int | None = None


class UnifiedMessageOut(BusinessSchema):
    id: int
    thread_id: int | None = None
    contact_id: int | None = None
    connection_id: int | None = None
    channel: str
    channel_label: str
    channel_badge: str
    original_id: str
    direction: str
    sent_at: datetime
    received_at: datetime | None = None
    subject: str | None = None
    sender: str | None = None
    recipient: str | None = None
    original_url: str | None = None
    content_hash: str
    is_read: bool = False
    is_archived: bool = False
    is_hidden: bool = False
    is_unavailable: bool = False
    unavailable_at: datetime | None = None
    availability_status: str = "available"


class ContactChannelStatOut(BusinessSchema):
    channel: str
    inbound_count: int = 0
    outbound_count: int = 0
    last_inbound_at: datetime | None = None
    last_outbound_at: datetime | None = None
    last_message_at: datetime | None = None


class InboxThreadDetailOut(BusinessSchema):
    thread: InboxThreadOut
    messages: list[UnifiedMessageOut] = Field(default_factory=list)
    contact_channel_stats: list[ContactChannelStatOut] = Field(default_factory=list)


class ContactMessagesListOut(BusinessSchema):
    contact_id: int
    items: list[UnifiedMessageOut] = Field(default_factory=list)
    next_before_id: int | None = None


class MarkReadRequest(BusinessSchema):
    thread_id: int = Field(..., gt=0)
    is_read: bool = True


class MarkReadOut(BusinessSchema):
    thread_id: int
    is_read: bool
    unread_count: int
    updated_messages: int


class ArchiveThreadRequest(BusinessSchema):
    thread_id: int = Field(..., gt=0)
    is_archived: bool = True


class ArchiveThreadOut(BusinessSchema):
    thread_id: int
    is_archived: bool


class HideMessageRequest(BusinessSchema):
    message_id: int = Field(..., gt=0)
    is_hidden: bool = True


class HideMessageOut(BusinessSchema):
    message_id: int
    thread_id: int | None = None
    is_hidden: bool
    provider_deleted: bool = False


class MergeSuggestionOut(BusinessSchema):
    source_contact_id: int
    target_contact_id: int
    confidence: str
    score: float
    reasons: list[str] = Field(default_factory=list)
    conflicting_fields: list[str] = Field(default_factory=list)
    auto_merge: bool = False


class MergeSuggestionsListOut(BusinessSchema):
    items: list[MergeSuggestionOut] = Field(default_factory=list)


class ContactMergeRequest(BusinessSchema):
    source_contact_id: int = Field(..., gt=0)
    target_contact_id: int = Field(..., gt=0)
    reason: str = Field(default="manual_reconciliation", min_length=1, max_length=500)
    field_choices: dict[str, str] = Field(default_factory=dict)


class ContactMergeOut(BusinessSchema):
    journal_id: int
    source_contact_id: int
    target_contact_id: int
    moved_identities: int
    moved_messages: int
    moved_threads: int
    audit_action: str


class ContactUnmergeRequest(BusinessSchema):
    journal_id: int = Field(..., gt=0)


class ContactUnmergeOut(BusinessSchema):
    journal_id: int
    source_contact_id: int
    target_contact_id: int
    restored_identities: int
    restored_messages: int
    restored_threads: int
    audit_action: str


router = APIRouter(
    tags=["Communications"],
    dependencies=[Depends(require_business_scope), Depends(require_model_perm("comms.message", ActionType.VIEW))],
    route_class=BusinessRoute,
)


class ChannelRecommendationOut(BusinessSchema):
    channel: str | None
    confidence: float = Field(ge=0, le=1)
    reasons: list[str]


@router.get(
    "/contacts/{contact_id}/recommend-channel",
    response_model=ChannelRecommendationOut,
    dependencies=[Depends(require_model_perm("contact", ActionType.VIEW))],
)
async def get_contact_channel_recommendation(
    contact_id: int = Path(..., gt=0),
    purpose: Literal["transactional", "greeting", "marketing"] = Query(...),
):
    """Advisory only: no dispatch and no replacement for delivery authorization."""
    try:
        async with async_session_maker() as session, session.begin():
            result = await recommend_channel(session, contact_id, purpose)
            return ChannelRecommendationOut(
                channel=result.channel, confidence=result.confidence, reasons=list(result.reasons)
            )
    except PermissionError:
        raise HTTPException(status_code=403) from None
    except ContactUnavailable:
        raise HTTPException(status_code=404) from None
    except ValueError:
        raise HTTPException(status_code=400) from None


@router.get("/inbox", response_model=InboxThreadListOut)
async def get_communication_inbox(
    channel: str | None = Query(default=None),
    direction: str | None = Query(default=None),
    contact_id: int | None = Query(default=None, gt=0),
    is_read: bool | None = Query(default=None),
    is_archived: bool | None = Query(default=False),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=100),
    before_id: int | None = Query(default=None, gt=0),
):
    try:
        async with async_session_maker() as session:
            data = await list_inbox_threads(
                session,
                channel=channel,
                direction=direction,
                contact_id=contact_id,
                is_read=is_read,
                is_archived=is_archived,
                since=since,
                until=until,
                q=q,
                limit=limit,
                before_id=before_id,
            )
            return InboxThreadListOut.model_validate(data)
    except PermissionError:
        raise HTTPException(status_code=403) from None
    except ValueError:
        raise HTTPException(status_code=400) from None


@router.get("/inbox/threads/{thread_id}", response_model=InboxThreadDetailOut)
async def get_communication_thread_detail(
    thread_id: int = Path(..., gt=0),
):
    try:
        async with async_session_maker() as session:
            data = await get_thread_detail(session, thread_id)
            return InboxThreadDetailOut.model_validate(data)
    except PermissionError:
        raise HTTPException(status_code=403) from None
    except InboxItemUnavailable:
        raise HTTPException(status_code=404) from None


@router.get("/inbox/contacts/{contact_id}/messages", response_model=ContactMessagesListOut)
async def get_communication_contact_messages(
    contact_id: int = Path(..., gt=0),
    channel: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    before_id: int | None = Query(default=None, gt=0),
):
    try:
        async with async_session_maker() as session:
            data = await list_contact_messages(
                session,
                contact_id,
                channel=channel,
                limit=limit,
                before_id=before_id,
            )
            return ContactMessagesListOut.model_validate(data)
    except PermissionError:
        raise HTTPException(status_code=403) from None
    except ContactUnavailable:
        raise HTTPException(status_code=404) from None
    except ValueError:
        raise HTTPException(status_code=400) from None


@router.post(
    "/inbox/mark-read",
    response_model=MarkReadOut,
    dependencies=[Depends(require_model_perm("comms.message", ActionType.UPDATE))],
)
async def post_communication_mark_read(body: MarkReadRequest):
    try:
        async with async_session_maker() as session, session.begin():
            data = await mark_thread_read(
                session,
                thread_id=body.thread_id,
                is_read=body.is_read,
            )
            return MarkReadOut.model_validate(data)
    except PermissionError:
        raise HTTPException(status_code=403) from None
    except InboxItemUnavailable:
        raise HTTPException(status_code=404) from None


@router.post(
    "/inbox/archive",
    response_model=ArchiveThreadOut,
    dependencies=[Depends(require_model_perm("comms.message", ActionType.UPDATE))],
)
async def post_communication_archive(body: ArchiveThreadRequest):
    try:
        async with async_session_maker() as session, session.begin():
            data = await archive_thread(
                session,
                thread_id=body.thread_id,
                is_archived=body.is_archived,
            )
            return ArchiveThreadOut.model_validate(data)
    except PermissionError:
        raise HTTPException(status_code=403) from None
    except InboxItemUnavailable:
        raise HTTPException(status_code=404) from None


@router.post(
    "/inbox/hide",
    response_model=HideMessageOut,
    dependencies=[Depends(require_model_perm("comms.message", ActionType.UPDATE))],
)
async def post_communication_hide_message(body: HideMessageRequest):
    try:
        async with async_session_maker() as session, session.begin():
            data = await hide_message_locally(
                session,
                message_id=body.message_id,
                is_hidden=body.is_hidden,
            )
            return HideMessageOut.model_validate(data)
    except PermissionError:
        raise HTTPException(status_code=403) from None
    except InboxItemUnavailable:
        raise HTTPException(status_code=404) from None


@router.get("/contacts/suggestions", response_model=MergeSuggestionsListOut)
async def get_communication_contact_merge_suggestions(
    limit: int = Query(default=50, ge=1, le=100),
):
    try:
        async with async_session_maker() as session:
            items = await suggest_contact_merges(session, limit=limit)
            return MergeSuggestionsListOut(items=[MergeSuggestionOut.model_validate(i) for i in items])
    except PermissionError:
        raise HTTPException(status_code=403) from None
    except ValueError:
        raise HTTPException(status_code=400) from None


@router.post(
    "/contacts/merge",
    response_model=ContactMergeOut,
    dependencies=[Depends(require_model_perm("comms.message", ActionType.UPDATE))],
)
async def post_communication_contact_merge(
    body: ContactMergeRequest,
    current_user: User = Depends(get_authenticated_user),
):
    try:
        async with async_session_maker() as session, session.begin():
            result = await merge_contacts(
                session,
                source_contact_id=body.source_contact_id,
                target_contact_id=body.target_contact_id,
                reason=body.reason,
                field_choices=body.field_choices,
                actor_id=current_user.id,
            )
            return ContactMergeOut.model_validate(result)
    except PermissionError:
        raise HTTPException(status_code=403) from None
    except ContactUnavailable:
        raise HTTPException(status_code=404) from None
    except ContactMergeConflictError:
        raise HTTPException(status_code=409) from None
    except ContactMergeError:
        raise HTTPException(status_code=400) from None


@router.post(
    "/contacts/unmerge",
    response_model=ContactUnmergeOut,
    dependencies=[Depends(require_model_perm("comms.message", ActionType.UPDATE))],
)
async def post_communication_contact_unmerge(
    body: ContactUnmergeRequest,
    current_user: User = Depends(get_authenticated_user),
):
    try:
        async with async_session_maker() as session, session.begin():
            result = await unmerge_contacts(
                session,
                journal_id=body.journal_id,
                actor_id=current_user.id,
            )
            return ContactUnmergeOut.model_validate(result)
    except PermissionError:
        raise HTTPException(status_code=403) from None
    except ContactUnavailable:
        raise HTTPException(status_code=404) from None
    except ContactMergeConflictError:
        raise HTTPException(status_code=409) from None
    except ContactMergeError:
        raise HTTPException(status_code=400) from None


from typing import Any
from app.core.tenant_context import current_tenant_id
from app.services.communications.triage import (
    TRIAGE_CATEGORIES,
    evaluate_triage_corpus,
    list_triaged_threads,
    materialize_triage_reply_draft,
    record_user_triage_override,
    triage_thread,
)
from app.services.communications.outbox_store import OutboxConflict
from app.services.communications.reply import ReplyUnavailable


class TriageProposalOut(BusinessSchema):
    proposal_id: str
    kind: str
    label: str
    title: str
    summary: str
    editable: bool = True
    explicit_confirmation_required: bool = True
    auto_executed: bool = False
    sent_without_approval: bool = False
    fields: dict[str, Any] = Field(default_factory=dict)
    target_service: str = ""
    target_route: str = ""


class ThreadTriageOut(BusinessSchema):
    thread_id: int
    tenant_id: int
    channel: str
    channel_label: str
    contact_id: int | None = None
    contact_display_name: str | None = None
    subject: str
    category: str
    category_label: str
    reason: str
    confidence: float = Field(ge=0.0, le=1.0)
    basis: str
    is_model_fact: bool = False
    baseline_category: str
    baseline_reason: str
    recommended_priority: str
    priority_basis: str
    unread_count: int = 0
    message_count: int = 0
    last_message_at: str | None = None
    last_direction: str | None = None
    inbound_message_id: int | None = None
    assignee: dict[str, Any] | None = None
    shared_inbox_supported: bool = False
    data_boundary: str = "metadata_only"
    persisted_body_cache: bool = False
    indexed_body: bool = False
    prompt_injection_detected: bool = False
    prompt_injection_matches: list[str] = Field(default_factory=list)
    model_status: str = "baseline_only"
    model_failure: bool = False
    budget_denied: bool = False
    model_notice: str | None = None
    connection_status: str = "active"
    stale_or_unavailable: bool = False
    proposals: list[TriageProposalOut] = Field(default_factory=list)
    authoritative_ref: str
    authoritative_url: str
    chat_url: str


class TriagedThreadListOut(BusinessSchema):
    items: list[ThreadTriageOut] = Field(default_factory=list)
    counts_by_category: dict[str, int] = Field(default_factory=dict)
    skipped_revoked_connections: int = 0


class TriageOverrideRequest(BusinessSchema):
    category: Literal["needs_reply", "fyi", "can_wait", "uncertain"]
    reason: str | None = Field(default=None, max_length=240)


class TriageOverrideOut(BusinessSchema):
    tenant_id: int
    user_id: int
    thread_id: int
    category: str
    category_label: str
    reason: str
    basis: str = "user_override"
    source: str = "user_decision"
    is_model_fact: bool = False
    recorded_by_user_id: int
    updated_at: str


class TriageReplyDraftRequest(BusinessSchema):
    inbound_message_id: int = Field(..., gt=0)
    subject: str = Field(default="", max_length=998)
    body: str = Field(..., min_length=1, max_length=1_000_000)
    timezone: str = Field(default="UTC", max_length=64)
    idempotency_key: str = Field(..., min_length=1, max_length=128)
    auto_send: bool = False
    bypass_approval: bool = False


class TriageReplyDraftOut(BusinessSchema):
    draft: dict[str, Any]
    requires_outbox_approval: bool = True
    sent: bool = False
    dispatched: bool = False
    scheduled: bool = False
    source: str = "p20_t09_reply_proposal"


class TriageEvaluationOut(BusinessSchema):
    total: int
    correct: int
    accuracy: float
    per_category: dict[str, dict[str, float | int]]
    false_negatives: list[dict[str, Any]]
    false_positives: list[dict[str, Any]]
    prompt_injection_total: int
    prompt_injection_caught: int
    limitations: str
    results: list[dict[str, Any]]


@router.get("/triage", response_model=TriagedThreadListOut)
async def get_communication_triage_list(
    category: Literal["needs_reply", "fyi", "can_wait", "uncertain"] | None = Query(default=None),
    channel: Literal["email", "telegram", "max", "phone", "bitrix24"] | None = Query(default=None),
    include_archived: bool = Query(default=False),
    limit: int = Query(default=25, ge=1, le=100),
):
    try:
        async with async_session_maker() as session:
            data = await list_triaged_threads(
                session,
                category=category,
                channel=channel,
                include_archived=include_archived,
                limit=limit,
            )
            return TriagedThreadListOut.model_validate(data)
    except PermissionError:
        raise HTTPException(status_code=403) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.get("/triage/evaluation", response_model=TriageEvaluationOut)
async def get_communication_triage_evaluation():
    return TriageEvaluationOut.model_validate(evaluate_triage_corpus())


@router.get("/threads/{thread_id}/triage", response_model=ThreadTriageOut)
async def get_communication_thread_triage(
    thread_id: int = Path(..., gt=0),
):
    try:
        async with async_session_maker() as session:
            data = await triage_thread(session, thread_id)
            return ThreadTriageOut.model_validate(data)
    except PermissionError:
        raise HTTPException(status_code=403) from None
    except InboxItemUnavailable:
        raise HTTPException(status_code=404) from None


@router.post(
    "/threads/{thread_id}/triage/override",
    response_model=TriageOverrideOut,
    dependencies=[Depends(require_model_perm("comms.message", ActionType.UPDATE))],
)
async def post_communication_thread_triage_override(
    body: TriageOverrideRequest,
    thread_id: int = Path(..., gt=0),
    current_user: User = Depends(get_authenticated_user),
):
    try:
        async with async_session_maker() as session, session.begin():
            # Verify thread exists and belongs to the active tenant before recording override
            await triage_thread(session, thread_id)
            tenant_id = current_tenant_id()
            if tenant_id is None:
                raise PermissionError("Missing tenant context")
            decision = await record_user_triage_override(
                session,
                tenant_id=int(tenant_id),
                user_id=int(current_user.id),
                thread_id=thread_id,
                category=body.category,
                reason=body.reason,
            )
            return TriageOverrideOut.model_validate(decision)
    except PermissionError:
        raise HTTPException(status_code=403) from None
    except InboxItemUnavailable:
        raise HTTPException(status_code=404) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.post(
    "/threads/{thread_id}/triage/reply-draft",
    response_model=TriageReplyDraftOut,
    dependencies=[Depends(require_model_perm("comms.outbox", ActionType.UPDATE))],
)
async def post_communication_thread_triage_reply_draft(
    body: TriageReplyDraftRequest,
    thread_id: int = Path(..., gt=0),
):
    try:
        async with async_session_maker() as session, session.begin():
            result = await materialize_triage_reply_draft(
                session,
                thread_id=thread_id,
                inbound_message_id=body.inbound_message_id,
                subject=body.subject,
                body=body.body,
                timezone_name=body.timezone,
                idempotency_key=body.idempotency_key,
                auto_send=body.auto_send,
                bypass_approval=body.bypass_approval,
            )
            return TriageReplyDraftOut.model_validate(result)
    except PermissionError:
        raise HTTPException(status_code=403) from None
    except ReplyUnavailable:
        raise HTTPException(status_code=404) from None
    except (OutboxConflict, ValueError):
        raise HTTPException(status_code=409) from None


validate_business_routes(router)

