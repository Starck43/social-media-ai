"""Contact identity regressions for the isolated P16-T05 PostgreSQL suite."""

import asyncio

import pytest
from sqlalchemy import delete, func, select, text

from app.core.database import async_session_maker
from app.models import Contact
from app.models.audit.action_audit_event import ActionAuditEvent
from app.models.planner import PlannerEventRun, PlannerTask
from app.services.contacts.reconcile import merge_contacts, unmerge_contacts
from app.services.crm.greetings import crm_greetings_service
from tests.planner.test_p16_t05_db import configure, fire, isolated_schema, owner_scope, personal_world  # noqa: F401

pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
async def clear_synthetic_audit_before_world_teardown(personal_world):
    yield
    # The generic fixture cannot delete a tenant while its immutable audit
    # evidence is retained. Use the established retention guard only for these
    # exclusively-owned synthetic tenants, before personal_world's finalizer.
    tenant_ids = [tenant.id for tenant in personal_world.tenants]
    async with async_session_maker() as session, session.begin():
        await session.execute(text("SELECT set_config('app.action_audit_retention', 'on', true)"))
        await session.execute(delete(ActionAuditEvent).where(ActionAuditEvent.tenant_id.in_(tenant_ids)))


async def merge(world, source_id, target_id):
    with owner_scope(world):
        async with async_session_maker() as session, session.begin():
            return await merge_contacts(
                session,
                source_contact_id=source_id,
                target_contact_id=target_id,
                reason="synthetic regression",
                actor_id=world.users[0].id,
            )


async def unmerge(world, journal_id):
    with owner_scope(world):
        async with async_session_maker() as session, session.begin():
            return await unmerge_contacts(session, journal_id=journal_id, actor_id=world.users[0].id)


async def task_rows(world):
    async with async_session_maker() as session:
        return list(
            (await session.scalars(select(PlannerTask).where(PlannerTask.tenant_id == world.tenants[0].id))).all()
        )


async def test_generated_event_survives_merge_without_second_group(personal_world):
    world = personal_world
    with owner_scope(world):
        source = await Contact.objects.create(display_name="Synthetic source", birthday="03-04")
        survivor = await Contact.objects.create(display_name="Synthetic survivor")
    await configure(world)
    assert (await fire(world))["created"] == 1
    await merge(world, source.id, survivor.id)
    assert (await fire(world))["created"] == 0
    tasks = await task_rows(world)
    assert len(tasks) == 3
    assert all(t.contact_id == survivor.id for t in tasks)
    async with async_session_maker() as session:
        run = await session.scalar(select(PlannerEventRun).where(PlannerEventRun.tenant_id == world.tenants[0].id))
        assert run.contact_id == survivor.id


async def test_unmerge_does_not_regenerate_previously_closed_event(personal_world):
    world = personal_world
    with owner_scope(world):
        source = await Contact.objects.create(display_name="Synthetic source", birthday="03-04")
        survivor = await Contact.objects.create(display_name="Synthetic survivor")
    await configure(world)
    assert (await fire(world))["created"] == 1
    result = await merge(world, source.id, survivor.id)
    await unmerge(world, result["journal_id"])
    assert (await fire(world))["created"] == 0
    assert len(await task_rows(world)) == 3


async def test_both_event_groups_preserved_on_merge_without_recreation(personal_world):
    world = personal_world
    with owner_scope(world):
        source = await Contact.objects.create(display_name="Synthetic source", birthday="03-04")
        survivor = await Contact.objects.create(display_name="Synthetic survivor", birthday="03-04")
    await configure(world)
    assert (await fire(world))["created"] == 2
    original = {task.id for task in await task_rows(world)}
    assert len(original) == 6
    await merge(world, source.id, survivor.id)
    assert (await fire(world))["created"] == 0
    remaining = await task_rows(world)
    assert {task.id for task in remaining} == original
    assert all(task.contact_id == survivor.id for task in remaining)


async def test_contact_deletion_retains_orphan_ledger_and_tasks(personal_world):
    world = personal_world
    with owner_scope(world):
        contact = await Contact.objects.create(display_name="Synthetic erasure", birthday="03-04")
    await configure(world)
    assert (await fire(world))["created"] == 1
    async with async_session_maker() as session:
        run = await session.scalar(select(PlannerEventRun).where(PlannerEventRun.contact_id == contact.id))
        run_id, root_id, greeting_id = run.id, run.root_task_id, run.greeting_task_id
    with owner_scope(world):
        await Contact.objects.delete_by_id(contact.id)
    async with async_session_maker() as session:
        orphan = await session.get(PlannerEventRun, run_id)
        assert orphan is not None and orphan.contact_id is None
        assert (orphan.root_task_id, orphan.greeting_task_id) == (root_id, greeting_id)
        remaining = await session.scalar(
            select(func.count()).select_from(PlannerTask).where(PlannerTask.tenant_id == world.tenants[0].id)
        )
        assert remaining == 3
    assert (await fire(world))["created"] == 0


async def test_generation_and_merge_are_serialized(personal_world, monkeypatch):
    world = personal_world
    with owner_scope(world):
        source = await Contact.objects.create(display_name="Synthetic source", birthday="03-04")
        survivor = await Contact.objects.create(display_name="Synthetic survivor")
    await configure(world)
    listed = asyncio.Event()
    proceed = asyncio.Event()
    original = crm_greetings_service.list_upcoming_events

    async def paused_list(*args, **kwargs):
        result = await original(*args, **kwargs)
        listed.set()
        await proceed.wait()
        return result

    monkeypatch.setattr(crm_greetings_service, "list_upcoming_events", paused_list)
    run = asyncio.create_task(fire(world))
    await asyncio.wait_for(listed.wait(), timeout=10)
    merging = asyncio.create_task(merge(world, source.id, survivor.id))
    await asyncio.sleep(0.1)  # merge proceeds or waits on the generator's contact lock
    proceed.set()
    await asyncio.wait_for(asyncio.gather(run, merging), timeout=10)
    assert (await fire(world))["created"] == 0
    assert len(await task_rows(world)) == 3


async def test_two_contact_deletions_preserve_both_orphan_runs(personal_world):
    """Erasing another contact in the same year must not be blocked by the first."""
    world = personal_world
    with owner_scope(world):
        first = await Contact.objects.create(display_name="Synthetic first", birthday="03-04")
        second = await Contact.objects.create(display_name="Synthetic second", birthday="03-04")
    await configure(world)
    assert (await fire(world))["created"] == 2
    with owner_scope(world):
        await Contact.objects.delete_by_id(first.id)
        await Contact.objects.delete_by_id(second.id)
    async with async_session_maker() as session:
        runs = list(
            (
                await session.scalars(select(PlannerEventRun).where(PlannerEventRun.tenant_id == world.tenants[0].id))
            ).all()
        )
        assert len(runs) == 2 and all(run.contact_id is None for run in runs)
    assert len(await task_rows(world)) == 6


async def test_event_generated_while_merged_stays_closed_after_unmerge(personal_world):
    world = personal_world
    with owner_scope(world):
        source = await Contact.objects.create(display_name="Synthetic source", birthday="03-04")
        survivor = await Contact.objects.create(display_name="Synthetic survivor", birthday="03-04")
    await configure(world)
    result = await merge(world, source.id, survivor.id)
    assert (await fire(world))["created"] == 1
    original = {task.id for task in await task_rows(world)}
    await unmerge(world, result["journal_id"])
    assert (await fire(world))["created"] == 0
    assert {task.id for task in await task_rows(world)} == original


async def test_contact_set_null_is_visible_in_deleting_transaction_and_after_commit(personal_world):
    """A concurrent snapshot sees both old rows, never a committed dangling reference."""
    world = personal_world
    with owner_scope(world):
        contact = await Contact.objects.create(display_name="Synthetic MVCC erasure", birthday="03-04")
    await configure(world)
    assert (await fire(world))["created"] == 1
    async with async_session_maker() as session:
        run_id = await session.scalar(select(PlannerEventRun.id).where(PlannerEventRun.contact_id == contact.id))
    async with async_session_maker() as deleting, async_session_maker() as observing:
        async with deleting.begin():
            await deleting.execute(
                delete(Contact).where(Contact.id == contact.id, Contact.tenant_id == world.tenants[0].id)
            )
            inside = await deleting.scalar(select(PlannerEventRun.contact_id).where(PlannerEventRun.id == run_id))
            assert inside is None
            old_pair = (
                await observing.execute(
                    select(PlannerEventRun.contact_id, Contact.id)
                    .outerjoin(Contact, Contact.id == PlannerEventRun.contact_id)
                    .where(PlannerEventRun.id == run_id)
                )
            ).one()
            assert old_pair == (contact.id, contact.id)
        after = (
            await observing.execute(
                select(PlannerEventRun.contact_id, Contact.id)
                .outerjoin(Contact, Contact.id == PlannerEventRun.contact_id)
                .where(PlannerEventRun.id == run_id)
            )
        ).one()
        assert after == (None, None)


async def test_generation_and_merge_lock_order_avoids_deadlock(personal_world, monkeypatch):
    """Generation holds tenant/contact locks before the merge can take either."""
    world = personal_world
    with owner_scope(world):
        source = await Contact.objects.create(display_name="Synthetic source", birthday="03-04")
        survivor = await Contact.objects.create(display_name="Synthetic survivor")
    await configure(world)
    listed, proceed = asyncio.Event(), asyncio.Event()
    original = crm_greetings_service.list_upcoming_events

    async def paused_list(*args, **kwargs):
        result = await original(*args, **kwargs)
        listed.set()
        await proceed.wait()
        return result

    monkeypatch.setattr(crm_greetings_service, "list_upcoming_events", paused_list)
    running = asyncio.create_task(fire(world))
    await asyncio.wait_for(listed.wait(), timeout=10)
    merging = asyncio.create_task(merge(world, source.id, survivor.id))
    try:
        await asyncio.sleep(0.1)
        assert not merging.done(), "merge should wait for the generator's tenant lock"
        proceed.set()
        result, merged = await asyncio.wait_for(asyncio.gather(running, merging), timeout=10)
        assert result["created"] == 1 and merged["source_contact_id"] == source.id
    finally:
        proceed.set()
        for task in (running, merging):
            if not task.done():
                task.cancel()
        await asyncio.gather(running, merging, return_exceptions=True)


async def test_both_merged_groups_keep_greeting_actions(personal_world, monkeypatch):
    from unittest.mock import AsyncMock
    from app.services.planner.events import event_planning_service

    world = personal_world
    with owner_scope(world):
        source = await Contact.objects.create(display_name="Synthetic source", birthday="03-04")
        survivor = await Contact.objects.create(display_name="Synthetic survivor", birthday="03-04")
    await configure(world)
    assert (await fire(world))["created"] == 2
    async with async_session_maker() as session:
        original = set(
            (
                await session.scalars(
                    select(PlannerEventRun.greeting_task_id).where(PlannerEventRun.tenant_id == world.tenants[0].id)
                )
            ).all()
        )
    assert len(original) == 2
    await merge(world, source.id, survivor.id)
    generate = AsyncMock(return_value={"draft_text": "synthetic", "send_enabled": False})
    monkeypatch.setattr(crm_greetings_service, "generate_greeting_draft", generate)
    with owner_scope(world):
        async with async_session_maker() as session, session.begin():
            assert await event_planning_service.greeting_task_ids(list(original), session=session) == original
            for task_id in original:
                result = await event_planning_service.draft_for_task(task_id, session=session)
                assert result["send_enabled"] is False
    assert generate.await_count == 2
    assert all(call.kwargs["contact_id"] == survivor.id for call in generate.await_args_list)


async def test_repeated_merge_and_unmerge_preserves_all_groups(personal_world, monkeypatch):
    from unittest.mock import AsyncMock
    from app.services.planner.events import event_planning_service

    world = personal_world
    with owner_scope(world):
        first = await Contact.objects.create(display_name="Synthetic first", birthday="03-04")
        second = await Contact.objects.create(display_name="Synthetic second", birthday="03-04")
        third = await Contact.objects.create(display_name="Synthetic third", birthday="03-04")
    await configure(world)
    assert (await fire(world))["created"] == 3
    original = {task.id for task in await task_rows(world)}
    assert len(original) == 9
    result1 = await merge(world, first.id, second.id)
    result2 = await merge(world, second.id, third.id)
    assert (await fire(world))["created"] == 0
    tasks = await task_rows(world)
    assert {task.id for task in tasks} == original
    assert all(task.contact_id == third.id for task in tasks)
    fake = AsyncMock(return_value={"send_enabled": False})
    monkeypatch.setattr(crm_greetings_service, "generate_greeting_draft", fake)
    with owner_scope(world):
        async with async_session_maker() as session, session.begin():
            greeting_ids = await event_planning_service.greeting_task_ids(list(original), session=session)
            assert len(greeting_ids) == 3
            for task_id in greeting_ids:
                await event_planning_service.draft_for_task(task_id, session=session)
    assert fake.await_count == 3
    await unmerge(world, result2["journal_id"])
    await unmerge(world, result1["journal_id"])
    assert (await fire(world))["created"] == 0
    assert {task.id for task in await task_rows(world)} == original


async def test_merge_first_serializes_generation(personal_world, monkeypatch):
    import app.services.contacts.reconcile as reconcile

    world = personal_world
    with owner_scope(world):
        source = await Contact.objects.create(display_name="Synthetic source", birthday="03-04")
        survivor = await Contact.objects.create(display_name="Synthetic survivor")
    await configure(world)
    reached, proceed = asyncio.Event(), asyncio.Event()
    original = reconcile._record_audit

    async def paused_audit(*args, **kwargs):
        reached.set()
        await proceed.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(reconcile, "_record_audit", paused_audit)
    merging = asyncio.create_task(merge(world, source.id, survivor.id))
    await asyncio.wait_for(reached.wait(), timeout=10)
    running = asyncio.create_task(fire(world))
    try:
        await asyncio.sleep(0.1)
        assert not running.done()
        proceed.set()
        _, generated = await asyncio.wait_for(asyncio.gather(merging, running), timeout=10)
        assert generated["created"] == 1
        assert (await fire(world))["created"] == 0
        tasks = await task_rows(world)
        assert len(tasks) == 3 and all(task.contact_id == survivor.id for task in tasks)
    finally:
        proceed.set()
        for task in (merging, running):
            if not task.done():
                task.cancel()
        await asyncio.gather(merging, running, return_exceptions=True)


async def test_deleting_extra_group_task_preserves_closed_event(personal_world):
    from app.models.planner.events import PlannerEventGroup

    world = personal_world
    with owner_scope(world):
        source = await Contact.objects.create(display_name="Synthetic source", birthday="03-04")
        survivor = await Contact.objects.create(display_name="Synthetic survivor", birthday="03-04")
    await configure(world)
    assert (await fire(world))["created"] == 2
    await merge(world, source.id, survivor.id)
    async with async_session_maker() as session:
        group = await session.scalar(
            select(PlannerEventGroup).where(PlannerEventGroup.tenant_id == world.tenants[0].id)
        )
        group_id, root_id = group.id, group.root_task_id
    async with async_session_maker() as session, session.begin():
        await session.execute(
            delete(PlannerTask).where(PlannerTask.id == root_id, PlannerTask.tenant_id == world.tenants[0].id)
        )
    async with async_session_maker() as session:
        group = await session.get(PlannerEventGroup, group_id)
        assert group is not None and group.root_task_id is None and group.greeting_task_id is None
    assert (await fire(world))["created"] == 0


async def test_erasing_enabler_disables_without_reassignment(personal_world):
    from app.models import User
    from app.models.planner import PlannerEventSettings

    world = personal_world
    with owner_scope(world):
        contact = await Contact.objects.create(display_name="Synthetic event", birthday="03-04")
    await configure(world)
    assert (await fire(world))["created"] == 1
    async with async_session_maker() as session, session.begin():
        await session.execute(delete(User).where(User.id == world.users[0].id))
    async with async_session_maker() as session:
        config = await session.get(PlannerEventSettings, world.tenants[0].id)
        run = await session.scalar(select(PlannerEventRun).where(PlannerEventRun.contact_id == contact.id))
        assert config.owner_user_id is None
        assert run is not None and run.root_task_id is None and run.greeting_task_id is None
    assert (await fire(world)) == {"status": "disabled", "created": 0}


async def test_claim_cannot_point_to_a_different_event_or_workspace(personal_world):
    from sqlalchemy.exc import IntegrityError
    from app.models.planner.events import PlannerEventClaim

    world = personal_world
    with owner_scope(world):
        contact = await Contact.objects.create(display_name="Synthetic event", birthday="03-04")
    await configure(world)
    assert (await fire(world))["created"] == 1
    async with async_session_maker() as session:
        run = await session.scalar(select(PlannerEventRun).where(PlannerEventRun.contact_id == contact.id))
        run_id, event_type, year = run.id, run.event_type, run.year
    with pytest.raises(IntegrityError):
        async with async_session_maker() as session, session.begin():
            session.add(
                PlannerEventClaim(
                    tenant_id=world.tenants[0].id,
                    contact_id=contact.id,
                    run_id=run_id,
                    event_type=event_type,
                    year=year + 1,
                )
            )
    with pytest.raises(IntegrityError):
        async with async_session_maker() as session, session.begin():
            session.add(
                PlannerEventClaim(
                    tenant_id=world.tenants[1].id,
                    contact_id=contact.id,
                    run_id=run_id,
                    event_type=event_type,
                    year=year,
                )
            )
