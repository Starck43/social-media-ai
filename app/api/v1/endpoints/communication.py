"""REST API v1 endpoints for unified inbox threads, chronological messages, metadata search, and contact identity reconciliation (`/api/v1/communication`, P15-T03..T04)."""

from __future__ import annotations

from datetime import datetime

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
    dependencies=[Depends(require_business_scope), Depends(require_model_perm("contact", ActionType.VIEW))],
    route_class=BusinessRoute,
)


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
    dependencies=[Depends(require_model_perm("contact", ActionType.UPDATE))],
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
    dependencies=[Depends(require_model_perm("contact", ActionType.UPDATE))],
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
    dependencies=[Depends(require_model_perm("contact", ActionType.UPDATE))],
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
    dependencies=[Depends(require_model_perm("contact", ActionType.UPDATE))],
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
    dependencies=[Depends(require_model_perm("contact", ActionType.UPDATE))],
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


validate_business_routes(router)
