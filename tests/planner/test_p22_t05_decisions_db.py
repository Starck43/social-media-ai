"""Local review ownership, expiry and version binding."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from app.core.config import settings
from app.core.database import async_session_maker
from app.models import TenantUser
from app.services.commands.task_decisions import task_decision_service as decisions
from app.services.planner.canonical_tasks import CreateTask, UpdateTask, task_service
from app.services.planner.tasks import PlannerConflict, PlannerUnavailable
from tests.planner.test_p22_t01_task_identity_db import actor, task_world  # noqa: F401
from tests.support.schema_guard import require_isolated_schema

pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]


async def test_local_reviews_have_one_outcome_and_leave_completion_explicit(task_world, monkeypatch):
    require_isolated_schema()
    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    deadline = datetime.now(timezone.utc) + timedelta(minutes=5)
    with actor(task_world, 0):
        task = await task_service.create(CreateTask(title="Synthetic review", visibility="workspace"))
        refs = [
            await decisions.propose(
                task.id,
                expected_version=task.version,
                reviewer_id=task_world.users[1].id,
                expires_at=deadline,
                reason=label,
            )
            for label in ("first proposal", "second proposal")
        ]
        assert await decisions.pending(task.id) == []
    with actor(task_world, 1):
        assert [item["reference"] for item in await decisions.pending(task.id)] == refs
        reject = await decisions.preview(task.id, refs[0], outcome="rejected")
        accept = await decisions.preview(task.id, refs[0], outcome="accepted")
        outcomes = await asyncio.gather(
            decisions.decide(task.id, confirmation=reject["confirmation"], reason="Reject this proposal"),
            decisions.decide(task.id, confirmation=accept["confirmation"], reason="Accept this proposal"),
            return_exceptions=True,
        )
        assert sum(isinstance(result, PlannerConflict) for result in outcomes) == 1
        assert len(await decisions.pending(task.id)) == 1
        assert (await task_service.get(task.id)).status == "todo"
        stale = await decisions.preview(task.id, refs[1], outcome="accepted")
    with actor(task_world, 0):
        await task_service.update(
            task.id, UpdateTask(expected_version=task.version, scope="occurrence", title="Changed input")
        )
    with actor(task_world, 1):
        with pytest.raises(PlannerConflict, match="decision_changed"):
            await decisions.decide(task.id, confirmation=stale["confirmation"], reason="Stale review")
        assert await decisions.pending(task.id) == []


async def test_local_review_expires_and_lost_membership_blocks_confirmation(task_world, monkeypatch):
    require_isolated_schema()
    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    from app.services.commands import task_decisions

    now = datetime.now(timezone.utc)
    with actor(task_world, 0):
        task = await task_service.create(CreateTask(title="Synthetic review", visibility="workspace"))
        ref = await decisions.propose(
            task.id,
            expected_version=task.version,
            reviewer_id=task_world.users[1].id,
            expires_at=now + timedelta(minutes=5),
            reason="Time sensitive",
        )
    with actor(task_world, 1):
        preview = await decisions.preview(task.id, ref, outcome="accepted")
        with monkeypatch.context() as patch:
            patch.setattr(task_decisions, "_now", lambda: now + timedelta(minutes=6))
            assert await decisions.pending(task.id) == []
            with pytest.raises(PlannerConflict, match="decision_changed"):
                await decisions.decide(task.id, confirmation=preview["confirmation"], reason="Late review")
    with actor(task_world, 2, workspace=1):
        with pytest.raises(PlannerUnavailable):
            await decisions.pending(task.id)
    async with async_session_maker() as session, session.begin():
        member = await session.scalar(
            select(TenantUser).where(
                TenantUser.tenant_id == task_world.tenants[0].id,
                TenantUser.user_id == task_world.users[1].id,
            )
        )
        member.is_active = False
    with actor(task_world, 1):
        with pytest.raises(PermissionError):
            await decisions.decide(task.id, confirmation=preview["confirmation"], reason="Revoked review")


async def test_document_review_uses_existing_ledger_and_requires_explicit_binding(task_world, monkeypatch):
    from app.models import DocumentApproval
    from app.services.knowledge.approvals import approval_service
    from app.services.knowledge.document_contracts import DocumentCreate
    from app.services.knowledge.documents import document_service

    require_isolated_schema()
    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    with actor(task_world, 0):
        task = await task_service.create(CreateTask(title="Synthetic review", visibility="workspace"))
        doc = await document_service.create(
            DocumentCreate(
                title="Synthetic document", document_type="letter", body_text="Example", data_boundary="cloud"
            )
        )
        approval = await approval_service.request(
            doc.id,
            {
                "approver_user_id": task_world.users[1].id,
                "expected_revision_id": doc.current_revision_id,
                "expected_hash": doc.content_hash,
                "expires_at": datetime.now(timezone.utc) + timedelta(minutes=5),
                "idempotency_key": "task-review",
            },
        )
        ref = f"document:{approval['id']}"
    with actor(task_world, 1):
        assert await decisions.pending(task.id) == []
    with actor(task_world, 0):
        await decisions.bind_produced_review(task.id, expected_version=task.version, reference=ref)
        assert await decisions.pending(task.id) == []
    with actor(task_world, 1):
        original = (await approval_service.pending())["items"][0]
        assert original["id"] == approval["id"]
        assert [item["reference"] for item in await decisions.pending(task.id)] == [ref]
        preview = await decisions.preview(task.id, ref, outcome="rejected")
        assert await decisions.decide(task.id, confirmation=preview["confirmation"], reason="Revise wording") == {
            "reference": ref,
            "outcome": "rejected",
        }
        assert await decisions.pending(task.id) == []
        assert (await approval_service.pending())["items"] == []
        assert (await task_service.get(task.id)).status == "todo"
    async with async_session_maker() as session:
        stored = await session.get(DocumentApproval, approval["id"])
        assert stored.comment_encrypted == "Revise wording"


async def test_task_command_decision_uses_original_ledger(task_world, monkeypatch):  # noqa: F811
    from base64 import urlsafe_b64encode
    from os import urandom

    from app.models import Contact
    from app.services.commands import ledger
    from app.services.commands.approval_inbox import approval_inbox
    from tests.p11_t12_audit_cleanup import purge_tenant_audit_events

    require_isolated_schema()
    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "PII_INDEX_KEY", urlsafe_b64encode(urandom(32)).decode())
    monkeypatch.setattr(ledger, "RUNTIME_KILL_SWITCHES", {})
    monkeypatch.setattr(ledger, "RUNTIME_ENABLED_CAPABILITIES", {"crm.greeting_send.v1"})
    tenant = task_world.tenants[0]
    try:
        with actor(task_world, 0):
            task = await task_service.create(CreateTask(title="Synthetic command review"))
            contact = await Contact.objects.create(display_name="Synthetic review contact")
            command = await approval_inbox._ledger.preview_command(
                capability="crm.greeting_send.v1",
                idempotency_key=f"p22-t05-{tenant.id}",
                target_refs=[f"contact:{contact.id}"],
                payload={"subject": "Synthetic task review"},
            )
            item = await approval_inbox.get_item(command["id"])
            reference = item["item_id"]
            assert await decisions.pending(task.id) == []
            await decisions.bind_produced_review(task.id, expected_version=task.version, reference=reference)
            (pending,) = await decisions.pending(task.id)
            assert pending["reference"] == reference
            preview = await decisions.preview(task.id, reference, outcome="rejected")
            result = await decisions.decide(
                task.id, confirmation=preview["confirmation"], reason="Declined synthetic command"
            )
            assert result["outcome"] == (await approval_inbox.get_item(command["id"]))["status"]
            assert (await approval_inbox.get_item(command["id"]))["raw_state"] == "cancelled"
            assert await decisions.pending(task.id) == []
            assert (await task_service.get(task.id)).status == "todo"
    finally:
        await purge_tenant_audit_events(tenant.id)
