"""PostgreSQL persistence and trigger verification for P20-T09 (`thread_triage_decisions`)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text

from app.core.access_catalog import workspace_grants
from app.core.config import settings
from app.core.database import async_session_maker
from app.core.permissions import permission_scope
from app.core.tenant_context import tenant_scope
from app.models import WorkspaceConnection
from app.models.communications.message import MessageThread, ThreadTriageDecision, UnifiedMessage
from app.services.communications.triage import (
    TRIAGE_CAN_WAIT,
    TRIAGE_FYI,
    TRIAGE_NEEDS_REPLY,
    get_user_triage_override,
    list_triaged_threads,
    record_user_triage_override,
    triage_thread,
)
from tests.shared.fixtures.personal_workspace import personal_world  # noqa: F401
from tests.support.schema_guard import require_isolated_schema

pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]

UTC = timezone.utc


@pytest.mark.asyncio
async def test_thread_triage_decisions_persists_across_postgresql_sessions_and_isolates_users(
    personal_world, monkeypatch  # noqa: F811
):
    """Verify B1 & B6 on real PostgreSQL: table + trigger exist, override survives new sessions, and two users do not overwrite each other."""
    require_isolated_schema()
    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", Fernet.generate_key().decode())

    tenant_1 = personal_world.tenants[0]
    tenant_2 = personal_world.tenants[1]
    user_a = personal_world.users[0]
    user_b = personal_world.users[1]
    user_t2 = personal_world.users[2]
    now = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)

    # 1. Verify table and contact_pii_marker trigger exist in the isolated PostgreSQL schema
    with tenant_scope(bypass=True):
        async with async_session_maker() as session:
            has_trigger = await session.scalar(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM pg_trigger t
                        JOIN pg_class c ON c.oid = t.tgrelid
                        JOIN pg_namespace n ON n.oid = c.relnamespace
                        WHERE n.nspname = :schema
                          AND c.relname = 'thread_triage_decisions'
                          AND t.tgname = 'contact_pii_marker'
                          AND NOT t.tgisinternal
                    )
                    """
                ),
                {"schema": settings.DB_SCHEMA},
            )
            assert has_trigger is True

    # 2. Seed a real WorkspaceConnection + MessageThread + UnifiedMessage in Session 1
    with tenant_scope(tenant_1.id), permission_scope(user_a, member_grants=workspace_grants("owner")):
        async with async_session_maker() as s1, s1.begin():
            conn = WorkspaceConnection(
                tenant_id=tenant_1.id,
                owner_user_id=user_a.id,
                schema_version=1,
                provider_id="imap_smtp",
                platform="email",
                auth_method="password",
                account_key=f"p20t09-{tenant_1.id}",
                label="Корпоративная почта",
                is_active=True,
                state="verified",
                config={},
            )
            s1.add(conn)
            await s1.flush()

            thread = MessageThread(
                tenant_id=tenant_1.id,
                connection_id=conn.id,
                channel="email",
                thread_key="p20t09-thread-1",
                subject_encrypted="Еженедельный дайджест новостей отрасли",
                participants_encrypted='["news@industry.example"]',
                last_message_at=now,
                message_count=1,
                unread_count=1,
                is_archived=False,
            )
            s1.add(thread)
            await s1.flush()

            msg = UnifiedMessage(
                tenant_id=tenant_1.id,
                connection_id=conn.id,
                thread_id=thread.id,
                channel="email",
                original_id="p20t09-msg-1",
                direction="inbound",
                sent_at=now,
                received_at=now,
                subject_encrypted="Еженедельный дайджест новостей отрасли",
                sender_encrypted="news@industry.example",
                recipient_encrypted="team@company.example",
                is_read=False,
                is_archived=False,
                is_hidden=False,
            )
            s1.add(msg)
            await s1.flush()
            thread_id = int(thread.id)

            # Baseline before override is `can_wait`
            initial = await triage_thread(s1, thread_id)
            assert initial["category"] == TRIAGE_CAN_WAIT
            assert initial["basis"] == "deterministic_baseline"

            # User A records override -> `needs_reply`
            saved_a = await record_user_triage_override(
                s1,
                tenant_id=tenant_1.id,
                user_id=user_a.id,
                thread_id=thread_id,
                category=TRIAGE_NEEDS_REPLY,
                reason="В дайджесте запрос комментария от редакции",
            )
            assert saved_a["category"] == TRIAGE_NEEDS_REPLY
            assert saved_a["is_model_fact"] is False
            assert saved_a["basis"] == "user_override"

    # 3. Session 2 (fresh PostgreSQL connection after commit): verify User A's override survived
    with tenant_scope(tenant_1.id), permission_scope(user_a, member_grants=workspace_grants("owner")):
        async with async_session_maker() as s2:
            after_a = await triage_thread(s2, thread_id)
            assert after_a["category"] == TRIAGE_NEEDS_REPLY
            assert after_a["basis"] == "user_override"
            assert after_a["is_model_fact"] is False
            assert after_a["baseline_category"] == TRIAGE_CAN_WAIT

            listed_a = await list_triaged_threads(s2, limit=10)
            matched = [it for it in listed_a["items"] if it["thread_id"] == thread_id]
            assert len(matched) == 1
            assert matched[0]["category"] == TRIAGE_NEEDS_REPLY

    # 4. User B in the same tenant sees baseline until they record their own decision, and never overwrites User A
    with tenant_scope(tenant_1.id), permission_scope(user_b, member_grants=workspace_grants("manager")):
        async with async_session_maker() as s3, s3.begin():
            before_b = await triage_thread(s3, thread_id)
            assert before_b["category"] == TRIAGE_CAN_WAIT
            assert before_b["basis"] == "deterministic_baseline"

            await record_user_triage_override(
                s3,
                tenant_id=tenant_1.id,
                user_id=user_b.id,
                thread_id=thread_id,
                category=TRIAGE_FYI,
                reason="Для меня только к сведению",
            )

    # Verify User A STILL has `needs_reply` while User B has `fyi`, and updating User A bumps `pii_updated_at` via trigger
    with tenant_scope(tenant_1.id), permission_scope(user_a, member_grants=workspace_grants("owner")):
        async with async_session_maker() as s4, s4.begin():
            ov_a = await get_user_triage_override(s4, tenant_1.id, thread_id, user_id=user_a.id)
            ov_b = await get_user_triage_override(s4, tenant_1.id, thread_id, user_id=user_b.id)
            assert ov_a is not None and ov_a["category"] == TRIAGE_NEEDS_REPLY
            assert ov_b is not None and ov_b["category"] == TRIAGE_FYI

            await record_user_triage_override(
                s4,
                tenant_id=tenant_1.id,
                user_id=user_a.id,
                thread_id=thread_id,
                category=TRIAGE_CAN_WAIT,
                reason="Отложили до следующей недели",
            )
            row_a = (
                await s4.execute(
                    text(
                        f'SELECT pii_updated_at FROM "{settings.DB_SCHEMA}".thread_triage_decisions '
                        "WHERE tenant_id = :tid AND decided_by_user_id = :uid AND thread_id = :thid"
                    ),
                    {"tid": tenant_1.id, "uid": user_a.id, "thid": thread_id},
                )
            ).scalar_one()
            assert int(row_a) >= 2

    # 5. Tenant 2 is completely isolated
    with tenant_scope(tenant_2.id), permission_scope(user_t2, member_grants=workspace_grants("owner")):
        async with async_session_maker() as s5:
            assert await get_user_triage_override(s5, tenant_2.id, thread_id, user_id=user_a.id) is None
