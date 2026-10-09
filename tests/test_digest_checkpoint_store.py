"""Real PostgreSQL lock/receipt tests; no transport or LLM calls."""

import asyncio
from copy import deepcopy
from datetime import date
from uuid import uuid4

import pytest

from app.core.tenant_context import TenantContextError, tenant_scope
from app.models import DigestRun, Tenant, TenantChannel
from app.services.digest.checkpoint_store import (
    CheckpointBusy,
    CheckpointConflict,
    DeliveryAuthorizationError,
    locked_checkpoint,
)
from app.services.digest.checkpoints import CheckpointError, new_checkpoint, next_parts
from app.services.digest.html_parts import SPLITTER_VERSION, split_digest_html

pytestmark = pytest.mark.tenancy


@pytest.fixture
async def frozen_run():
    tenant = await Tenant.objects.create(slug=f"store-{uuid4().hex}", name="Checkpoint test", plan="business")
    binding = await TenantChannel.objects.create(
        tenant_id=tenant.id, channel="telegram", chat_id=f"store-{uuid4().hex}", is_active=True, is_digest_target=True
    )
    content = "<b>" + "frozen text 😀 &amp; " * 500 + "</b>"
    with tenant_scope(tenant.id):
        run = await DigestRun.objects.create(
            period="day",
            period_start=date(2026, 10, 1),
            period_end=date(2026, 10, 2),
            channel="auto",
            content=content,
            status="pending",
        )
        state = new_checkpoint(
            run_id=run.id,
            tenant_id=tenant.id,
            generation="fixed",
            content=content,
            splitter=SPLITTER_VERSION,
            targets=[
                {
                    "binding_id": binding.id,
                    "channel": "telegram",
                    "destination_id": binding.chat_id,
                    "parts": split_digest_html(content),
                }
            ],
        )
        await DigestRun.objects.update_by_id(run.id, delivery_state=state)
    try:
        yield tenant, binding, run, state
    finally:
        await Tenant.objects.delete_by_id(tenant.id)


async def test_intent_and_each_receipt_are_durable_before_next_part(frozen_run):
    tenant, binding, run, _ = frozen_run
    with tenant_scope(tenant.id):
        async with locked_checkpoint(run.id, generation="fixed") as store:
            first = await store.begin_part(0, 0)
            assert first["destination_id"] == binding.chat_id
            observed = await DigestRun.objects.get(id=run.id)
            assert observed.delivery_state["targets"][0]["parts"][0]["status"] == "in_flight"
            await store.record_outcome(0, 0, "sent", message_id="receipt-1")
            observed = await DigestRun.objects.get(id=run.id)
            assert observed.delivery_state["targets"][0]["parts"][0]["message_id"] == "receipt-1"
        async with locked_checkpoint(run.id, generation="fixed") as restarted:
            state, parts = await restarted.load()
            assert next_parts(state) == [(0, 1)]
            assert (await restarted.begin_part(0, 1))["text"] == parts[1]
            await restarted.record_outcome(0, 1, "rejected")
            assert next_parts((await restarted.load())[0]) == [(0, 1)]


async def test_other_worker_cannot_enter_even_after_receipt_commit(frozen_run):
    tenant, _, run, _ = frozen_run
    with tenant_scope(tenant.id):
        async with locked_checkpoint(run.id, generation="fixed") as store:
            await store.begin_part(0, 0)
            await store.record_outcome(0, 0, "sent", message_id="receipt")

            async def competitor():
                with pytest.raises(CheckpointBusy):
                    async with locked_checkpoint(run.id, generation="fixed"):
                        pytest.fail("second worker acquired held lock")

            await asyncio.wait_for(asyncio.create_task(competitor()), timeout=3)
        async with locked_checkpoint(run.id, generation="fixed") as after_release:
            assert next_parts((await after_release.load())[0]) == [(0, 1)]


async def test_abandoned_intent_is_never_automatically_replayed(frozen_run):
    tenant, _, run, _ = frozen_run
    with tenant_scope(tenant.id):
        with pytest.raises(RuntimeError, match="simulated crash"):
            async with locked_checkpoint(run.id, generation="fixed") as store:
                await store.begin_part(0, 0)
                raise RuntimeError("simulated crash")
        async with locked_checkpoint(run.id, generation="fixed") as restarted:
            assert next_parts((await restarted.load())[0]) == []
            with pytest.raises(CheckpointError):
                await restarted.begin_part(0, 0)
            await restarted.record_outcome(0, 0, "uncertain")


@pytest.mark.parametrize("change", ["disabled", "not_digest", "destination", "workspace"])
async def test_rechecks_authorization_for_every_next_part(frozen_run, change):
    tenant, binding, run, _ = frozen_run
    with tenant_scope(tenant.id):
        async with locked_checkpoint(run.id, generation="fixed") as store:
            await store.begin_part(0, 0)
            await store.record_outcome(0, 0, "sent", message_id="receipt")
            if change == "workspace":
                await Tenant.objects.update_by_id(tenant.id, is_active=False)
            else:
                fields = {
                    "disabled": {"is_active": False},
                    "not_digest": {"is_digest_target": False},
                    "destination": {"chat_id": "changed"},
                }[change]
                await TenantChannel.objects.update_by_id(binding.id, **fields)
            with pytest.raises(DeliveryAuthorizationError):
                await store.begin_part(0, 1)
            observed = await DigestRun.objects.get(id=run.id)
            assert observed.delivery_state["targets"][0]["parts"][1]["status"] == "pending"


async def test_acknowledgement_still_saved_after_binding_revocation(frozen_run):
    tenant, binding, run, _ = frozen_run
    with tenant_scope(tenant.id):
        async with locked_checkpoint(run.id, generation="fixed") as store:
            await store.begin_part(0, 0)
            await TenantChannel.objects.update_by_id(binding.id, is_active=False)
            await store.record_outcome(0, 0, "sent", message_id="accepted-before-revocation")
            assert (await store.load())[0]["targets"][0]["parts"][0]["status"] == "sent"
            with pytest.raises(DeliveryAuthorizationError):
                await store.begin_part(0, 1)


async def test_foreign_run_and_unscoped_bypass_never_grant_access(frozen_run):
    tenant, _, run, _ = frozen_run
    for scope in [dict(), dict(bypass=True), dict(tenant_id=tenant.id, bypass=True), dict(tenant_id=tenant.id + 999)]:
        with tenant_scope(**scope):
            with pytest.raises(TenantContextError):
                async with locked_checkpoint(run.id, generation="fixed"):
                    pytest.fail("forbidden context acquired ledger")


async def test_stale_generation_and_null_history_fail_and_release_lock(frozen_run):
    tenant, _, run, _ = frozen_run
    with tenant_scope(tenant.id):
        with pytest.raises(CheckpointError):
            async with locked_checkpoint(run.id, generation="stale"):
                pass
        async with locked_checkpoint(run.id, generation="fixed"):
            pass
        await DigestRun.objects.update_by_id(run.id, delivery_state=None)
        with pytest.raises(CheckpointError):
            async with locked_checkpoint(run.id, generation="fixed"):
                pass


async def test_changed_context_or_closed_store_cannot_write(frozen_run):
    tenant, _, run, _ = frozen_run
    with tenant_scope(tenant.id):
        async with locked_checkpoint(run.id, generation="fixed") as store:
            with tenant_scope(tenant.id + 1):
                with pytest.raises(DeliveryAuthorizationError):
                    await store.begin_part(0, 0)
        with pytest.raises(CheckpointConflict):
            await store.load()


async def test_noncooperating_checkpoint_write_is_not_clobbered(frozen_run, monkeypatch):
    tenant, _, run, original = frozen_run
    with tenant_scope(tenant.id):
        async with locked_checkpoint(run.id, generation="fixed") as store:
            real_save = store._save

            async def concurrent_write(before, after, content):
                changed = deepcopy(original)
                changed["targets"][0]["parts"][0]["status"] = "blocked"
                await DigestRun.objects.update_by_id(run.id, delivery_state=changed)
                await real_save(before, after, content)

            monkeypatch.setattr(store, "_save", concurrent_write)
            with pytest.raises(CheckpointConflict):
                await store.begin_part(0, 0)
            assert (await DigestRun.objects.get(id=run.id)).delivery_state["targets"][0]["parts"][0][
                "status"
            ] == "blocked"


async def test_cancellation_releases_lock_but_retains_durable_intent(frozen_run):
    tenant, _, run, _ = frozen_run
    with tenant_scope(tenant.id):
        with pytest.raises(asyncio.CancelledError):
            async with locked_checkpoint(run.id, generation="fixed") as store:
                await store.begin_part(0, 0)
                raise asyncio.CancelledError
        async with locked_checkpoint(run.id, generation="fixed") as restarted:
            assert next_parts((await restarted.load())[0]) == []


@pytest.mark.parametrize("phase", ["intent", "receipt"])
async def test_failed_commit_poisons_context_and_does_not_commit_on_later_read(frozen_run, monkeypatch, phase):
    from sqlalchemy.ext.asyncio import AsyncConnection

    tenant, _, run, _ = frozen_run
    real_commit = AsyncConnection.commit
    failure = {"armed": False}
    with tenant_scope(tenant.id):
        async with locked_checkpoint(run.id, generation="fixed") as store:
            if phase == "receipt":
                await store.begin_part(0, 0)

            async def fail_once(connection):
                if connection is store._connection and failure["armed"]:
                    failure["armed"] = False
                    raise RuntimeError("simulated commit failure")
                return await real_commit(connection)

            monkeypatch.setattr(AsyncConnection, "commit", fail_once)
            failure["armed"] = True
            with pytest.raises(RuntimeError, match="simulated commit failure"):
                if phase == "intent":
                    await store.begin_part(0, 0)
                else:
                    await store.record_outcome(0, 0, "sent", message_id="remote-accepted")
            with pytest.raises(CheckpointConflict):
                await store.load()
        async with locked_checkpoint(run.id, generation="fixed") as restarted:
            state, _ = await restarted.load()
            assert next_parts(state) == ([(0, 0)] if phase == "intent" else [])
            assert state["targets"][0]["parts"][0]["message_id"] is None


async def test_cancelled_lock_acquisition_invalidates_unknown_session(monkeypatch):
    from types import SimpleNamespace

    from app.services.digest import checkpoint_store as module

    class UnknownLockConnection:
        invalidated = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def scalar(self, statement):
            return 1  # ownership precheck

        async def commit(self):
            pass

        async def execute(self, statement, params):
            # Simulate cancellation after server acquired lock, before result.
            raise asyncio.CancelledError

        async def invalidate(self):
            self.invalidated = True

    connection = UnknownLockConnection()
    monkeypatch.setattr(module, "async_engine", SimpleNamespace(connect=lambda: connection))
    with tenant_scope(1):
        with pytest.raises(asyncio.CancelledError):
            async with locked_checkpoint(1, generation="fixed"):
                pass
    assert connection.invalidated is True


async def test_foreign_frozen_binding_cannot_authorize_send(frozen_run):
    tenant, _, run, original = frozen_run
    other = await Tenant.objects.create(slug=f"other-store-{uuid4().hex}", name="Other", plan="business")
    try:
        foreign = await TenantChannel.objects.create(
            tenant_id=other.id,
            channel="telegram",
            chat_id=f"other-{uuid4().hex}",
            is_active=True,
            is_digest_target=True,
        )
        altered = deepcopy(original)
        altered["targets"][0].update(binding_id=foreign.id, destination_id=foreign.chat_id)
        with tenant_scope(tenant.id):
            await DigestRun.objects.update_by_id(run.id, delivery_state=altered)
            async with locked_checkpoint(run.id, generation="fixed") as store:
                with pytest.raises(DeliveryAuthorizationError):
                    await store.begin_part(0, 0)
                assert (await store.load())[0]["targets"][0]["parts"][0]["status"] == "pending"
    finally:
        await Tenant.objects.delete_by_id(other.id)
