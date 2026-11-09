"""Local isolated PostgreSQL checks for the revision-bound approval workflow.

No external providers; schema setup and cleanup are owner-controlled.
"""

import asyncio
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import delete, func, select, text, update

from app.core.access_catalog import workspace_grants
from app.core.config import settings
from app.core.database import async_session_maker
from app.core.permissions import permission_scope
from app.core.tenant_context import tenant_scope
from app.models import DocumentApproval, DocumentRevision, KnowledgeDocument, TenantUser
from app.services.knowledge.approvals import ApprovalConflict, approval_service
from app.services.knowledge.document_contracts import DocumentCreate
from app.services.knowledge.documents import document_service
from tests.shared.fixtures.personal_workspace import personal_world  # noqa: F401

pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]


def scope(world, user=0, workspace=0, role="owner"):
    stack = ExitStack()
    stack.enter_context(tenant_scope(world.tenants[workspace].id))
    stack.enter_context(permission_scope(world.users[user], member_grants=workspace_grants(role)))
    return stack


@pytest.fixture
async def approval_world(personal_world, monkeypatch):  # noqa: F811
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.services.notifications import service as notification_service

    messenger = AsyncMock()
    monkeypatch.setattr(notification_service, "MESSENGER_AVAILABLE", True)
    monkeypatch.setattr(
        notification_service, "messenger_service", SimpleNamespace(send_notification=messenger), raising=False
    )
    assert settings.DB_SCHEMA == "test_schema_p19t04"
    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    ids = [tenant.id for tenant in personal_world.tenants]
    try:
        yield personal_world
    finally:
        messenger.assert_not_awaited()
        async with async_session_maker() as session, session.begin():
            await session.execute(delete(DocumentApproval).where(DocumentApproval.tenant_id.in_(ids)))
            await session.execute(delete(KnowledgeDocument).where(KnowledgeDocument.tenant_id.in_(ids)))
        async with async_session_maker() as session:
            for model in (DocumentApproval, KnowledgeDocument, DocumentRevision):
                assert (
                    await session.scalar(select(func.count()).select_from(model).where(model.tenant_id.in_(ids))) == 0
                )


async def create_request(world, *, key="one", approver=1):
    with scope(world):
        doc = await document_service.create(
            DocumentCreate(title="synthetic", document_type="letter", body_text="first", data_boundary="cloud")
        )
        data = dict(
            approver_user_id=world.users[approver].id,
            expected_revision_id=doc.current_revision_id,
            expected_hash=doc.content_hash,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            idempotency_key=key,
        )
        approval = await approval_service.request(doc.id, data)
    return doc, data, approval


async def test_decision_persisted_and_identical_replay_is_idempotent(approval_world):
    world = approval_world
    doc, request, row = await create_request(world)
    with scope(world):
        assert await approval_service.request(doc.id, request) == row
    with scope(world, user=1):
        result = await approval_service.decide(
            doc.id, row["id"], {"decision": "approved", "comment": "synthetic private comment"}
        )
        assert (
            await approval_service.decide(
                doc.id, row["id"], {"decision": "approved", "comment": "synthetic private comment"}
            )
            == result
        )
        with pytest.raises(ApprovalConflict, match="already_decided"):
            await approval_service.decide(doc.id, row["id"], {"decision": "approved", "comment": "different"})
    async with async_session_maker() as session:
        rev = await session.get(DocumentRevision, doc.current_revision_id)
        assert rev.status == "approved" and rev.decision_actor_user_id == world.users[1].id
        stored = await session.scalar(
            text(f'SELECT comment_encrypted FROM "{settings.DB_SCHEMA}".document_approvals WHERE id=:id'),
            {"id": row["id"]},
        )
        assert stored.startswith("pii:v1:") and "synthetic private comment" not in stored


async def test_conflicting_concurrent_decisions_have_one_winner(approval_world):
    world = approval_world
    doc, _, row = await create_request(world)
    with scope(world, user=1):
        outcomes = await asyncio.wait_for(
            asyncio.gather(
                approval_service.decide(doc.id, row["id"], {"decision": "approved"}),
                approval_service.decide(doc.id, row["id"], {"decision": "rejected"}),
                return_exceptions=True,
            ),
            timeout=15,
        )
    assert sum(isinstance(x, dict) for x in outcomes) == 1
    assert sum(isinstance(x, ApprovalConflict) for x in outcomes) == 1


@pytest.mark.parametrize("change", ["membership", "role", "user"])
async def test_revoked_authority_is_rechecked_with_stale_scope(approval_world, change):
    world = approval_world
    doc, _, row = await create_request(world)
    from app.models import User

    with scope(world, user=1):
        async with async_session_maker() as session, session.begin():
            if change == "user":
                await session.execute(update(User).where(User.id == world.users[1].id).values(is_active=False))
            else:
                query = update(TenantUser).where(
                    TenantUser.tenant_id == world.tenants[0].id, TenantUser.user_id == world.users[1].id
                )
                await session.execute(
                    query.values(is_active=False)
                    if change == "membership"
                    else query.values(role_id=world.roles["viewer"].id)
                )
        with pytest.raises(PermissionError):
            await approval_service.decide(doc.id, row["id"], {"decision": "approved"})


async def test_expired_decision_is_persisted_without_approval(approval_world):
    world = approval_world
    doc, _, row = await create_request(world)
    async with async_session_maker() as session, session.begin():
        await session.execute(
            update(DocumentApproval)
            .where(DocumentApproval.id == row["id"])
            .values(expires_at=DocumentApproval.requested_at + timedelta(microseconds=1))
        )
    with scope(world, user=1):
        result = await approval_service.decide(doc.id, row["id"], {"decision": "approved"})
    assert result["status"] == "expired"
    async with async_session_maker() as session:
        assert (await session.get(DocumentApproval, row["id"])).status == "expired"
        assert (await session.get(DocumentRevision, doc.current_revision_id)).status == "in_review"


async def test_edit_invalidates_pending_revision_even_if_body_hash_unchanged(approval_world):
    world = approval_world
    doc, _, row = await create_request(world)
    with scope(world):
        current = await document_service.get(doc.id)
        await document_service.update(
            doc.id,
            {
                "expected_revision_id": current.current_revision_id,
                "expected_updated_at": current.updated_at,
                "title": "different title",
            },
        )
    with scope(world, user=1), pytest.raises(ApprovalConflict, match="revision_changed"):
        await approval_service.decide(doc.id, row["id"], {"decision": "approved"})


async def test_inbox_notifies_assignee_and_requester_once(approval_world):
    from app.models import Notification, NotificationReceipt

    world = approval_world
    doc, request, row = await create_request(world)
    with scope(world):
        await approval_service.request(doc.id, request)
    with scope(world, user=1):
        await approval_service.decide(doc.id, row["id"], {"decision": "rejected"})
        await approval_service.decide(doc.id, row["id"], {"decision": "rejected"})
    async with async_session_maker() as session:
        received = (
            await session.execute(
                select(Notification.dedup_key, NotificationReceipt.user_id)
                .join(NotificationReceipt, NotificationReceipt.notification_id == Notification.id)
                .where(
                    Notification.tenant_id == world.tenants[0].id,
                    Notification.dedup_key.like(f"document-approval:{row['id']}:%"),
                )
                .order_by(Notification.dedup_key)
            )
        ).all()
    assert set(received) == {
        (f"document-approval:{row['id']}:requested", world.users[1].id),
        (f"document-approval:{row['id']}:decided", world.users[0].id),
    }


async def test_notification_failure_rolls_back_decision(approval_world, monkeypatch):
    from unittest.mock import AsyncMock

    from app.services.knowledge import approvals

    world = approval_world
    doc, _, row = await create_request(world)
    monkeypatch.setattr(approvals, "_notice", AsyncMock(side_effect=RuntimeError("synthetic notification failure")))
    with scope(world, user=1), pytest.raises(RuntimeError, match="synthetic notification failure"):
        await approval_service.decide(doc.id, row["id"], {"decision": "approved"})
    async with async_session_maker() as session:
        assert (await session.get(DocumentApproval, row["id"])).status == "pending"
        assert (await session.get(DocumentRevision, doc.current_revision_id)).status == "in_review"


async def test_revoked_persisted_approval_grant_is_rechecked(approval_world):
    from sqlalchemy import insert

    from app.models import ModelType, Permission
    from app.models.tenancy.workspace_role import workspace_role_permission as links
    from app.types import ActionType

    world = approval_world
    doc, _, row = await create_request(world)
    async with async_session_maker() as session, session.begin():
        permission_id = await session.scalar(
            select(Permission.id)
            .join(ModelType)
            .where(ModelType.resource_key == "documents.approval", Permission.action_type == ActionType.CONFIGURE)
        )
        assert permission_id is not None
        await session.execute(
            delete(links).where(links.c.role_id == world.roles["owner"].id, links.c.permission_id == permission_id)
        )
    try:
        with scope(world, user=1), pytest.raises(PermissionError):
            await approval_service.decide(doc.id, row["id"], {"decision": "approved"})
    finally:
        async with async_session_maker() as session, session.begin():
            await session.execute(insert(links).values(role_id=world.roles["owner"].id, permission_id=permission_id))


async def test_notify_creation_and_decision_rollback_together(approval_world, monkeypatch):
    from app.models import Notification, NotificationReceipt
    from app.services.notifications.service import notify

    world = approval_world
    doc, _, row = await create_request(world)
    original = notify.create

    async def create_then_fail(*args, **kwargs):
        assert kwargs["session"] is not None and kwargs["send_to_messenger"] is False
        await original(*args, **kwargs)
        raise RuntimeError("failure after notification write")

    monkeypatch.setattr(notify, "create", create_then_fail)
    with scope(world, user=1), pytest.raises(RuntimeError, match="failure after"):
        await approval_service.decide(doc.id, row["id"], {"decision": "approved"})
    async with async_session_maker() as session:
        assert (await session.get(DocumentApproval, row["id"])).status == "pending"
        assert (await session.get(DocumentRevision, doc.current_revision_id)).status == "in_review"
        event_id = await session.scalar(
            select(Notification.id).where(
                Notification.tenant_id == world.tenants[0].id,
                Notification.dedup_key == f"document-approval:{row['id']}:decided",
            )
        )
        assert event_id is None


async def test_other_tenant_cannot_decide_or_list_approval(approval_world):
    from app.services.knowledge.documents import DocumentUnavailable

    world = approval_world
    doc, _, row = await create_request(world)
    with scope(world, user=2, workspace=1):
        assert (await approval_service.pending())["items"] == []
        with pytest.raises(DocumentUnavailable):
            await approval_service.decide(doc.id, row["id"], {"decision": "approved"})
    with scope(world, user=1):
        assert [x["id"] for x in (await approval_service.pending())["items"]] == [row["id"]]


async def test_scheduled_expiry_is_bounded_idempotent_and_notifies_both(approval_world):
    from app.jobs.job_handlers.document_approval_expiry import handle_document_approval_expiry
    from app.models import Notification, NotificationReceipt

    world = approval_world
    doc, _, row = await create_request(world)
    async with async_session_maker() as session, session.begin():
        await session.execute(
            update(DocumentApproval)
            .where(DocumentApproval.id == row["id"])
            .values(expires_at=DocumentApproval.requested_at + timedelta(microseconds=1))
        )
    with tenant_scope(world.tenants[0].id):
        assert await handle_document_approval_expiry({"limit": 1}) == {"expired": 1}
        assert await handle_document_approval_expiry({"limit": 1}) == {"expired": 0}
    with scope(world, user=1):
        assert (await approval_service.pending())["items"] == []
        with pytest.raises(ApprovalConflict):
            await approval_service.decide(doc.id, row["id"], {"decision": "approved"})
    async with async_session_maker() as session:
        users = (
            await session.scalars(
                select(NotificationReceipt.user_id)
                .join(Notification)
                .where(
                    Notification.tenant_id == world.tenants[0].id,
                    Notification.dedup_key == f"document-approval:{row['id']}:expired",
                )
            )
        ).all()
        assert set(users) == {world.users[0].id, world.users[1].id}


async def test_legacy_owner_decision_cannot_bypass_approval(approval_world):
    from app.services.knowledge.documents import DocumentConflict

    world = approval_world
    doc, _, row = await create_request(world)
    with scope(world):
        current = await document_service.get(doc.id)
        with pytest.raises(DocumentConflict, match="approval_request_required"):
            await document_service.transition(
                doc.id,
                {
                    "expected_revision_id": current.current_revision_id,
                    "expected_updated_at": current.updated_at,
                    "expected_hash": current.content_hash,
                    "expected_status": "in_review",
                    "target": "approved",
                },
            )


async def test_transactional_notification_refuses_immediate_messenger(approval_world):
    from app.services.notifications.service import notify
    from app.types import NotificationType

    with scope(approval_world), pytest.raises(ValueError, match="messenger_send_requires_post_commit"):
        async with async_session_maker() as session, session.begin():
            await notify.create(
                "synthetic",
                "synthetic",
                NotificationType.SYSTEM_UPDATE,
                recipient_user_ids=[approval_world.users[1].id],
                send_to_messenger=True,
                session=session,
            )


async def test_real_authenticated_http_workflow_and_revoked_member(approval_world):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from app.api.v1.endpoints.documents import router
    from app.core.api_scope import ApiScopeMiddleware
    from app.utils.token import create_access_token

    world = approval_world
    with scope(world):
        doc = await document_service.create(
            DocumentCreate(title="synthetic HTTP", document_type="memo", body_text="body", data_boundary="cloud")
        )
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.add_middleware(ApiScopeMiddleware)

    def headers(index, tenant=0):
        return {
            "Authorization": "Bearer " + create_access_token(subject=str(world.users[index].id))[0],
            "X-Tenant-Id": str(world.tenants[tenant].id),
        }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test.invalid") as client:
        assert (await client.get("/api/v1/documents/approvals/pending")).status_code == 401
        request = await client.post(
            f"/api/v1/documents/{doc.id}/request-approval",
            headers=headers(0),
            json={
                "approver_user_id": world.users[1].id,
                "expected_revision_id": doc.current_revision_id,
                "expected_hash": doc.content_hash,
                "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                "idempotency_key": "real-auth-http",
            },
        )
        assert request.status_code == 201, request.text
        approval_id = request.json()["id"]
        pending = await client.get("/api/v1/documents/approvals/pending", headers=headers(1))
        assert pending.status_code == 200, pending.text
        assert [item["id"] for item in pending.json()["items"]] == [approval_id]
        foreign = await client.post(
            f"/api/v1/documents/{doc.id}/approvals/{approval_id}/approve", headers=headers(2, 1), json={}
        )
        assert foreign.status_code == 404, foreign.text
        async with async_session_maker() as session, session.begin():
            await session.execute(
                update(TenantUser).where(TenantUser.id == world.memberships[2].id).values(is_active=False)
            )
        denied = await client.post(
            f"/api/v1/documents/{doc.id}/approvals/{approval_id}/approve", headers=headers(1), json={}
        )
        assert denied.status_code == 403, denied.text
        async with async_session_maker() as session, session.begin():
            await session.execute(
                update(TenantUser).where(TenantUser.id == world.memberships[2].id).values(is_active=True)
            )
        decided = await client.post(
            f"/api/v1/documents/{doc.id}/approvals/{approval_id}/approve",
            headers=headers(1),
            json={"comment": "synthetic"},
        )
        assert decided.status_code == 200, decided.text
        assert decided.json()["status"] == "approved"
        repeated = await client.post(
            f"/api/v1/documents/{doc.id}/approvals/{approval_id}/approve",
            headers=headers(1),
            json={"comment": "synthetic"},
        )
        assert repeated.status_code == 200 and repeated.json() == decided.json()
        conflict = await client.post(
            f"/api/v1/documents/{doc.id}/approvals/{approval_id}/reject", headers=headers(1), json={}
        )
        assert conflict.status_code == 409


async def test_real_web_session_csrf_and_approval_forms(approval_world):
    import re

    from httpx import ASGITransport, AsyncClient

    from app.main import create_application
    from tests.shared.web_session import session_cookie

    world = approval_world
    with scope(world):
        doc = await document_service.create(
            DocumentCreate(title="synthetic web", document_type="memo", body_text="body", data_boundary="cloud")
        )
    app = create_application()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://testserver.invalid", follow_redirects=False
    ) as client:
        client.cookies.set("session", session_cookie(world.users[0], world.tenants[0].id), domain="testserver.invalid")
        page = await client.get(f"/app/documents/manage/{doc.id}?boundary=cloud")
        assert page.status_code == 200, page.text
        assert "не является электронной подписью" in page.text
        key = re.search(r'name="idempotency_key" value="([^"]+)"', page.text).group(1)
        payload = {
            "boundary": "cloud",
            "approver_user_id": str(world.users[1].id),
            "expected_revision_id": str(doc.current_revision_id),
            "expected_hash": doc.content_hash,
            "idempotency_key": key,
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        }
        url = f"/app/documents/manage/{doc.id}/request-approval"
        assert (await client.post(url, data={**payload, "_csrf": "bad"})).status_code == 403
        response = await client.post(url, data={**payload, "_csrf": app.state.csrf_manager.generate_token()})
        assert response.status_code == 303, response.text
        client.cookies.set("session", session_cookie(world.users[1], world.tenants[0].id), domain="testserver.invalid")
        inbox = await client.get("/app/documents/manage?boundary=cloud")
        assert inbox.status_code == 200, inbox.text
        match = re.search(rf"/app/documents/manage/{doc.id}/approvals/(\d+)/decision", inbox.text)
        assert match is not None
        decision_url = f"/app/documents/manage/{doc.id}/approvals/{match.group(1)}/decision"
        assert (
            await client.post(decision_url, data={"boundary": "cloud", "decision": "approved", "_csrf": "bad"})
        ).status_code == 403
        response = await client.post(
            decision_url,
            data={"boundary": "cloud", "decision": "approved", "_csrf": app.state.csrf_manager.generate_token()},
        )
        assert response.status_code == 303, response.text
        with scope(world, user=1):
            current = await document_service.get(doc.id)
            assert current.status == "approved" and current.decision_actor_user_id == world.users[1].id


async def test_changed_idempotency_intent_and_wrong_approver_are_refused(approval_world):
    world = approval_world
    doc, request, row = await create_request(world)
    with scope(world), pytest.raises(ApprovalConflict, match="request_key_reused"):
        await approval_service.request(doc.id, {**request, "expires_at": request["expires_at"] + timedelta(minutes=1)})
    with scope(world), pytest.raises(PermissionError, match="approval_denied"):
        await approval_service.decide(doc.id, row["id"], {"decision": "approved"})
    async with async_session_maker() as session:
        assert (await session.get(DocumentApproval, row["id"])).status == "pending"


async def test_expiry_dispatch_and_pending_list_validate_scope_and_limits(approval_world):
    with tenant_scope(bypass=True), pytest.raises(PermissionError):
        await approval_service.expire_due()
    with tenant_scope(approval_world.tenants[0].id):
        for limit in (0, 101, True):
            with pytest.raises(ValueError, match="invalid_expiry_limit"):
                await approval_service.expire_due(limit=limit)
    with scope(approval_world, user=1):
        for kwargs in ({"limit": 0}, {"limit": 101}, {"before_id": 0}, {"boundary": "not-a-boundary"}):
            with pytest.raises(ValueError, match="invalid_query"):
                await approval_service.pending(**kwargs)


async def test_concurrent_same_key_for_different_documents_conflicts_cleanly(approval_world):
    world = approval_world
    with scope(world):
        first = await document_service.create(
            DocumentCreate(title="first", document_type="letter", body_text="one", data_boundary="cloud")
        )
        second = await document_service.create(
            DocumentCreate(title="second", document_type="letter", body_text="two", data_boundary="cloud")
        )
        expires = datetime.now(timezone.utc) + timedelta(hours=1)

        def payload(doc):
            return dict(
                approver_user_id=world.users[1].id,
                expected_revision_id=doc.current_revision_id,
                expected_hash=doc.content_hash,
                expires_at=expires,
                idempotency_key="concurrent-key",
            )

        outcomes = await asyncio.wait_for(
            asyncio.gather(
                approval_service.request(first.id, payload(first)),
                approval_service.request(second.id, payload(second)),
                return_exceptions=True,
            ),
            timeout=15,
        )
    assert sum(isinstance(x, dict) for x in outcomes) == 1
    assert sum(isinstance(x, ApprovalConflict) and str(x) == "request_key_reused" for x in outcomes) == 1


async def test_concurrent_identical_requests_return_same_result(approval_world):
    world = approval_world
    with scope(world):
        doc = await document_service.create(
            DocumentCreate(title="same", document_type="letter", body_text="one", data_boundary="cloud")
        )
        payload = dict(
            approver_user_id=world.users[1].id,
            expected_revision_id=doc.current_revision_id,
            expected_hash=doc.content_hash,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            idempotency_key="same-key",
        )
        results = await asyncio.wait_for(
            asyncio.gather(approval_service.request(doc.id, payload), approval_service.request(doc.id, payload)),
            timeout=15,
        )
    assert results[0] == results[1]


async def test_pending_only_shows_current_unexpired_assigned_approval(approval_world):
    world = approval_world
    doc, _, row = await create_request(world)
    with scope(world, user=1):
        assert [x["id"] for x in (await approval_service.pending())["items"]] == [row["id"]]
    with scope(world):
        assert not (await approval_service.pending())["items"]
        current = await document_service.get(doc.id)
        await document_service.update(
            doc.id,
            {
                "expected_revision_id": current.current_revision_id,
                "expected_updated_at": current.updated_at,
                "title": "new metadata",
            },
        )
    with scope(world, user=1):
        assert not (await approval_service.pending())["items"]


async def test_pending_excludes_expired_and_rechecks_access(approval_world):
    world = approval_world
    _, _, row = await create_request(world)
    async with async_session_maker() as session, session.begin():
        await session.execute(
            update(DocumentApproval)
            .where(DocumentApproval.id == row["id"])
            .values(expires_at=DocumentApproval.requested_at + timedelta(microseconds=1))
        )
    with scope(world, user=1):
        assert not (await approval_service.pending())["items"]
    async with async_session_maker() as session, session.begin():
        await session.execute(
            update(TenantUser)
            .where(TenantUser.tenant_id == world.tenants[0].id, TenantUser.user_id == world.users[1].id)
            .values(is_active=False)
        )
    with scope(world, user=1), pytest.raises(PermissionError):
        await approval_service.pending()


async def test_inbox_links_recheck_document_rights(approval_world):
    from app.services.notifications.inbox import PersonalInbox

    world = approval_world
    doc, _, row = await create_request(world)
    with scope(world, user=1):
        inbox = PersonalInbox(world.users[1])
        entries = await inbox.entries()
        event = next(e for e in entries if e.event.dedup_key == f"document-approval:{row['id']}:requested")
        assert event.destination == f"/app/documents/manage/{doc.id}?boundary=cloud"
    async with async_session_maker() as session, session.begin():
        await session.execute(
            update(TenantUser).where(TenantUser.id == world.memberships[2].id).values(is_active=False)
        )
    with scope(world, user=1):
        assert not (await PersonalInbox(world.users[1]).entries())
