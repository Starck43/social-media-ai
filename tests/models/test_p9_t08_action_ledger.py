"""Real PostgreSQL concurrency, actor-bound sealed approval and dry-run-only claims."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.core.permissions import operator_permission_scope, permission_scope
from app.core.tenant_context import tenant_scope
from app.models import AgentAction, AgentScenario, BotAction, Platform, Source, TenantUser
from app.types import BotActionStatus, PlatformType, SourceType
from tests.shared.fixtures.personal_workspace import personal_world

pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]


async def proposal(world, key="intent", **kwargs):
    with tenant_scope(world.tenants[0].id):
        return await AgentAction.objects.propose(
            idempotency_key=key,
            capability="business.reply",
            target_ref="document:local-1",
            payload={"text": "synthetic"},
            **kwargs,
        )


async def test_general_proposal_has_one_mapper_and_durable_idempotency(personal_world):
    w = personal_world
    assert AgentAction is BotAction
    one, two = await asyncio.gather(proposal(w), proposal(w))
    assert one[0].id == two[0].id and sorted([one[1], two[1]]) == [False, True]
    assert one[0].source_id is None and one[0].action_type is None
    with tenant_scope(w.tenants[0].id):
        with pytest.raises(ValueError):
            await AgentAction.objects.propose(
                idempotency_key="intent",
                capability="business.reply",
                target_ref="different",
                payload={"text": "synthetic"},
            )


async def test_approval_claim_and_finalize_are_actor_payload_and_token_bound(personal_world):
    w = personal_world
    row, _ = await proposal(w)
    with tenant_scope(w.tenants[0].id), permission_scope(w.users[0]):
        assert await AgentAction.objects.mark_executed(row.id) is None
        approved = await AgentAction.objects.approve_action(row.id)
        assert approved.approved_user_id == w.users[0].id and approved.approved_payload_hash == approved.payload_hash
        assert await AgentAction.objects.approve_action(row.id) is None
        claimed, token = await AgentAction.objects.claim_action(row.id)
        assert claimed.status == BotActionStatus.CLAIMED
        assert await AgentAction.objects.claim_action(row.id) is None
        assert await AgentAction.objects.mark_executed(row.id, claim_token="wrong") is None
        result = await AgentAction.objects.mark_executed(
            row.id, result={"secret": "must-not-persist"}, claim_token=token
        )
        assert result.status == BotActionStatus.EXECUTED and result.attempts == 1
        assert result.result == {"dry_run": True, "simulation_only": True, "success": True}
        assert result.executed_at is not None and result.claim_token_hash is None
        assert await AgentAction.objects.mark_executed(row.id, claim_token=token) is None


async def test_concurrent_claim_has_exactly_one_winner(personal_world):
    w = personal_world
    row, _ = await proposal(w)
    with tenant_scope(w.tenants[0].id), permission_scope(w.users[0]):
        await AgentAction.objects.approve_action(row.id)
        results = await asyncio.gather(
            AgentAction.objects.claim_action(row.id), AgentAction.objects.claim_action(row.id)
        )
        assert sum(r is not None for r in results) == 1
        current = await AgentAction.objects.get(id=row.id)
        assert current.attempts == 1


async def test_approval_and_rejection_race_cannot_both_win(personal_world):
    w = personal_world
    row, _ = await proposal(w)
    with tenant_scope(w.tenants[0].id), permission_scope(w.users[0]):
        results = await asyncio.gather(
            AgentAction.objects.approve_action(row.id), AgentAction.objects.reject_action(row.id)
        )
        assert sum(r is not None for r in results) == 1
        assert (await AgentAction.objects.get(id=row.id)).status in {BotActionStatus.APPROVED, BotActionStatus.REJECTED}


async def test_nonmember_other_actor_expiry_and_payload_tamper_fail_closed(personal_world):
    w = personal_world
    row, _ = await proposal(w)
    with tenant_scope(w.tenants[0].id):
        with pytest.raises(PermissionError):
            await AgentAction.objects.approve_action(row.id)
        with permission_scope(w.users[3]), pytest.raises(PermissionError):
            await AgentAction.objects.approve_action(row.id)
        with permission_scope(w.users[0]):
            with pytest.raises(PermissionError):
                await AgentAction.objects.approve_action(row.id, user_id=w.memberships[2].id)
            await AgentAction.objects.approve_action(row.id)
        with permission_scope(w.users[1]):
            assert await AgentAction.objects.claim_action(row.id) is None
        with permission_scope(w.users[0]):
            await AgentAction.objects.update_by_id(row.id, payload={"changed": True})
            assert await AgentAction.objects.claim_action(row.id) is None
    expired, _ = await proposal(w, key="expired")
    with tenant_scope(w.tenants[0].id), permission_scope(w.users[0]):
        await AgentAction.objects.approve_action(expired.id)
        await AgentAction.objects.update_by_id(expired.id, approval_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        assert await AgentAction.objects.claim_action(expired.id) is None


async def test_revoked_membership_cannot_finalize_and_expired_claim_is_unknown(personal_world):
    w = personal_world
    row, _ = await proposal(w)
    with tenant_scope(w.tenants[0].id), permission_scope(w.users[0]):
        await AgentAction.objects.approve_action(row.id)
        claimed, token = await AgentAction.objects.claim_action(row.id)
        await TenantUser.objects.update_by_id(w.memberships[0].id, is_active=False)
        with pytest.raises(PermissionError):
            await AgentAction.objects.mark_executed(row.id, claim_token=token)
        await TenantUser.objects.update_by_id(w.memberships[0].id, is_active=True)
        await AgentAction.objects.update_by_id(row.id, claim_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        assert await AgentAction.objects.mark_executed(row.id, claim_token=token) is None
        unknown = await AgentAction.objects.mark_unknown(row.id)
        assert unknown.status == BotActionStatus.UNKNOWN and unknown.claim_token_hash is None
        assert await AgentAction.objects.claim_action(row.id) is None


async def test_no_live_execution_and_rejected_proposals_do_not_spend_execution_budget(personal_world):
    w = personal_world
    row, _ = await proposal(w)
    with tenant_scope(w.tenants[0].id), permission_scope(w.users[0]):
        await AgentAction.objects.reject_action(row.id)
        assert await AgentAction.objects.claim_action(row.id) is None
        assert await AgentAction.objects.cancel_action(row.id) is None
    live, _ = await proposal(w, key="disabled")
    with tenant_scope(w.tenants[0].id), permission_scope(w.users[0]):
        await AgentAction.objects.update_by_id(live.id, dry_run=False, payload_hash=None)
        await AgentAction.objects.approve_action(live.id)
        assert await AgentAction.objects.claim_action(live.id) is None
        assert (await AgentAction.objects.cancel_action(live.id)).status == BotActionStatus.CANCELLED


async def test_origin_deletion_does_not_erase_action_history(personal_world):
    w = personal_world
    with tenant_scope(w.tenants[0].id), operator_permission_scope():
        scenario = await AgentScenario.objects.create(name=w.tag, is_active=True)
        platform = await Platform.objects.filter(platform_type=PlatformType.VK).first()
        source = await Source.objects.create(
            platform_id=platform.id, name=w.tag, source_type=SourceType.CHANNEL, external_id=w.tag, is_active=True
        )
        row, _ = await AgentAction.objects.propose(
            idempotency_key="history",
            capability="social.comment",
            target_ref="post:1",
            payload={"text": "synthetic"},
            source_id=source.id,
            agent_scenario_id=scenario.id,
        )
        await Source.objects.delete_by_id(source.id)
        await AgentScenario.objects.delete_by_id(scenario.id)
        current = await AgentAction.objects.get(id=row.id)
        assert current is not None and current.source_id is None and current.agent_scenario_id is None
        assert (
            current.origin_snapshot["source_id"] == source.id
            and current.origin_snapshot["agent_scenario_id"] == scenario.id
        )
