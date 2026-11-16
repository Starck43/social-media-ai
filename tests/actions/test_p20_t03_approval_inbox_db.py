"""PostgreSQL authorization and cross-session approval-lock checks for P20-T03."""

import asyncio
import base64
import os

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy import func, select

from app.core.access_catalog import workspace_grants
from app.core.config import settings
from app.core.database import async_session_maker
from app.core.permissions import permission_scope
from app.core.tenant_context import tenant_scope
from app.models.actions.business_command import BusinessCommand
from app.models.contacts.core import Contact
from app.services.commands import ApprovalInboxService, BusinessCommandLedger, CommandConflict
from tests.shared.fixtures.personal_workspace import personal_world  # noqa: F401
from tests.support.schema_guard import require_isolated_schema

pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def isolated_database_gate() -> None:
    require_isolated_schema()


@pytest.fixture(autouse=True)
def pii_keys(monkeypatch):
    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "PII_INDEX_KEY", base64.urlsafe_b64encode(os.urandom(32)).decode())


@pytest_asyncio.fixture
async def purge_world_audit_events_before_world_teardown(personal_world):
    """Clear only this test's audit rows before personal_world removes its tenant."""
    yield
    from tests.p11_t12_audit_cleanup import purge_tenant_audit_events

    for tenant in personal_world.tenants:
        await purge_tenant_audit_events(tenant.id)


async def test_session_none_authorization_and_two_session_approve_lock(
    personal_world, purge_world_audit_events_before_world_teardown, monkeypatch
) -> None:
    """Use production sessions for membership/payload reads and a real row-lock race."""
    from app.services.commands import ledger as ledger_module

    monkeypatch.setattr(ledger_module, "RUNTIME_KILL_SWITCHES", {})
    monkeypatch.setattr(ledger_module, "RUNTIME_ENABLED_CAPABILITIES", {"crm.greeting_send.v1"})
    world = personal_world
    tenant_id = world.tenants[0].id
    user = world.users[0]
    inbox = ApprovalInboxService(ledger=BusinessCommandLedger())
    with tenant_scope(tenant_id), permission_scope(user, member_grants=workspace_grants("owner")):
        contact = await Contact.objects.create(display_name="P20-T03 synthetic approval target")
        command = await inbox._ledger.preview_command(
            capability="crm.greeting_send.v1",
            idempotency_key=f"p20-t03-{world.tag}-review",
            target_refs=[f"contact:{contact.id}"],
            payload={"subject": "Synthetic approval test"},
        )

        # Production callers omit session; current actor/member grants and stored
        # payload must still be loaded through an owned SQLAlchemy session.
        page = await inbox.list_items()
        assert command["id"] in {item["command_id"] for item in page["items"]}
        item = await inbox.get_item(command["id"])
        assert item["payload_hash"] == command["payload_hash"]

        first = async_session_maker()
        second = async_session_maker()
        competing = None
        try:
            # Preload the row into the second identity map. After the lock wait,
            # populate_existing must refresh this stale previewed object.
            stale = await second.scalar(
                select(BusinessCommand).where(
                    BusinessCommand.tenant_id == tenant_id,
                    BusinessCommand.id == command["id"],
                )
            )
            assert stale is not None and stale.state == "previewed"
            second_pid = await second.scalar(select(func.pg_backend_pid()))
            await first.begin()
            first_pid = await first.scalar(select(func.pg_backend_pid()))
            approved = await inbox.approve_item(
                command["id"], expected_payload_hash=command["payload_hash"], session=first
            )
            assert approved["status"] == "approved"

            competing = asyncio.create_task(
                inbox.approve_item(command["id"], expected_payload_hash=command["payload_hash"], session=second)
            )
            # The second independent transaction reaches SELECT FOR UPDATE and
            # waits until the first click commits its single-use transition.
            for _ in range(100):
                blockers = await first.scalar(select(func.pg_blocking_pids(second_pid)))
                if first_pid in blockers:
                    break
                if competing.done():
                    break
                await asyncio.sleep(0.01)
            assert first_pid in blockers, "the second approval did not block on the first transaction's row lock"
            await first.commit()
            with pytest.raises(CommandConflict, match="already approved"):
                await asyncio.wait_for(competing, timeout=5)
        finally:
            if competing is not None and not competing.done():
                competing.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await competing
            if first.in_transaction():
                await first.rollback()
            if second.in_transaction():
                await second.rollback()
            await first.close()
            await second.close()
