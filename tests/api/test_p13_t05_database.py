"""Local-runner only: requires root fixtures and migrated exclusive test_schema.

Never include in the remote --noconftest scope. Uses existing synthetic-member
helpers and session factory; no ad-hoc engine or public records.
"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.database import async_session_maker
from app.core.tenant_context import tenant_scope
from app.models import ActionAuditEvent
from tests.api.test_api_permissions import _client, _drop, _register_owner
from tests.api.test_p11_t12_action_audit import _purge

pytestmark = pytest.mark.tenancy


async def test_business_audit_database_rejects_unknown_codes_and_retains_legacy():
    owner = tenant_id = None
    async with await _client() as client:
        try:
            owner, tenant_id = await _register_owner(client, "P13T05Audit")
            with tenant_scope(tenant_id):
                async with async_session_maker() as session:
                    async with session.begin():
                        legacy = await ActionAuditEvent.objects.record_success(
                            session=session,
                            actor_kind="user",
                            actor_id=owner.id,
                            action="scheduling.task_admit",
                            resource_kind="agent_task",
                            resource_id=1,
                        )
                        business = await ActionAuditEvent.objects.record_success(
                            session=session,
                            actor_kind="user",
                            actor_id=owner.id,
                            action="files.connector.register",
                            resource_kind="local_connector",
                            resource_id=2,
                        )
                        assert legacy.id and business.id
                from app.core.config import settings

                # Source-owned schema, values bound; exercise the DB CHECK, not manager validation.
                table = '"' + settings.DB_SCHEMA.replace('"', '""') + '".action_audit_events'
                for action, resource, outcome, reason in (
                    ("files.connector.unregistered", "local_connector", "succeeded", None),
                    ("files.connector.register", "agent_task", "succeeded", None),
                    ("files.connector.register", None, "succeeded", None),
                    ("files.connector.register", "local_connector", "unknown", None),
                    ("files.connector.register", "local_connector", "unknown", "private-provider-content"),
                ):
                    async with async_session_maker() as session:
                        with pytest.raises(DBAPIError):
                            async with session.begin():
                                await session.execute(
                                    text(
                                        f"INSERT INTO {table} "
                                        "(tenant_id,event_id,actor_kind,actor_id,action,resource_kind,resource_id,outcome,reason_code) "
                                        "VALUES (:tenant,:event,'user',:actor,:action,:resource,'1',:outcome,:reason)"
                                    ),
                                    dict(
                                        tenant=tenant_id,
                                        event=uuid.uuid4().hex,
                                        actor=str(owner.id),
                                        action=action,
                                        resource=resource,
                                        outcome=outcome,
                                        reason=reason,
                                    ),
                                )
        finally:
            if tenant_id is not None:
                await _purge([tenant_id])
            await _drop(owner, tenant_id)
