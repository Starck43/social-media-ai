"""Tests for bot_actions: ledger, guards, write-clients, agent tools."""

import uuid

import pytest

from app.models import AgentScenario, AgentTask, BotAction, Platform, Role, Source, TenantUser
from app.models.managers.agent_task_manager import AgentTaskManager
from app.models.managers.bot_action_manager import BotActionManager
from app.services.social.guards import GuardsChecker
from app.types import BotActionStatus, AgentActionType, BotTriggerType, PlatformType, SourceType


def _make_platform() -> Platform:
    return Platform(
        name=f"vk_{uuid.uuid4().hex[:8]}",
        platform_type=PlatformType.VK.db_value,
        base_url="https://vk.com",
        params={},
    )


@pytest.fixture
async def platform():
    p = await Platform.objects.create(
        name=f"vk_{uuid.uuid4().hex[:8]}",
        platform_type=PlatformType.VK.db_value,
        base_url="https://vk.com",
        params={},
    )
    yield p
    await Platform.objects.delete_by_id(p.id)


@pytest.fixture
async def scenario(platform):
    # The guards moved to the task (migration 0073); a scenario now only says
    # how to analyse. The configured task below is what the guards read.
    s = await AgentScenario.objects.create(
        name="Test Scenario",
        is_active=True,
    )
    yield s
    await AgentScenario.objects.delete_by_id(s.id)


@pytest.fixture
async def task(scenario, source):
    """A task carrying the guards, as the analyze handler sees them.

    It links `source` explicitly: a task with no linked sources means "all active
    sources", which would sweep in every other source left in the test database
    rather than the one this test set up.
    """
    t = await AgentTask.objects.create(
        name="Test Task",
        cron_expr="0 * * * *",
        job_type="analyze",
        agent_scenario_id=scenario.id,
        trigger_type=BotTriggerType.KEYWORD_MATCH,
        trigger_config={"keywords": ["test"], "mode": "any"},
        action_type=AgentActionType.COMMENT,
        rate_limit_per_hour=5,
        cooldown_seconds=60,
        blacklist=["baduser"],
        whitelist=None,
        is_active=True,
    )
    # Do it here, as the docstring promises: a task with no linked sources means
    # "all active sources", so the analyze handler sweeps in whatever sources
    # other tests left behind and `stats["sources"]` depends on the test run's
    # order rather than on this fixture.
    await AgentTaskManager().set_sources(t.id, [source.id])
    yield t
    await AgentTask.objects.delete_by_id(t.id)


@pytest.fixture
async def source(platform, scenario):
    s = await Source.objects.create(
        platform_id=platform.id,
        name="Test Source",
        source_type=SourceType.CHANNEL,
        external_id="12345",
        is_active=True,
    )
    yield s
    await Source.objects.delete_by_id(s.id)


@pytest.fixture
async def approver():
    """A real `tenant_users` row to confirm actions as.

    These tests used to hardcode `user_id=1`. That only works while the test
    database happens to contain a row with id 1, and it stops being true the
    first time that row is deleted: the sequence keeps climbing and never
    reissues the id, so `bot_actions.confirmed_by` fails its foreign key. The
    failure reads like a database problem rather than a stale test constant, so
    the approver is created here and torn down with the rest of the fixtures.
    """
    viewer_role = await Role.objects.get(codename="VIEWER")
    u = await TenantUser.objects.create(
        tenant_id=1,
        channel="web",
        external_user_id=f"approver-{uuid.uuid4().hex[:12]}",
        role_id=viewer_role.id,
        is_active=True,
    )
    yield u
    await TenantUser.objects.delete_by_id(u.id)


async def _make_action(scenario_id: int, source_id: int, task_id: int | None = None, **overrides):
    params = dict(
        agent_scenario_id=scenario_id,
        agent_task_id=task_id,
        source_id=source_id,
        action_type=AgentActionType.COMMENT,
        status=BotActionStatus.PENDING,
        payload={"text": "Test comment", "post_id": 123},
        dry_run=True,
    )
    params.update(overrides)
    return await BotAction.objects.create(**params)


class TestBotActionModel:
    async def test_create_bot_action(self, scenario, source):
        action = await _make_action(scenario.id, source.id)
        assert action.id is not None
        assert action.status == BotActionStatus.PENDING
        assert action.dry_run is True
        assert action.payload["text"] == "Test comment"

    async def test_bot_action_status_transitions(self, scenario, source, approver):
        action = await _make_action(scenario.id, source.id)

        manager = BotActionManager()

        approved = await manager.approve_action(action_id=action.id, user_id=approver.id)
        assert approved.status == BotActionStatus.APPROVED
        assert approved.confirmed_by == approver.id

        executed = await manager.mark_executed(action_id=action.id, result={"comment_id": 456})
        assert executed.status == BotActionStatus.EXECUTED
        assert executed.result["comment_id"] == 456

    async def test_bot_action_reject(self, scenario, source, approver):
        action = await _make_action(scenario.id, source.id)

        manager = BotActionManager()
        rejected = await manager.reject_action(action_id=action.id, user_id=approver.id)
        assert rejected.status == BotActionStatus.REJECTED


class TestGuardsChecker:
    async def test_blacklist_blocks_action(self, task):
        checker = GuardsChecker()
        allowed, reason = await checker.check(task, target_user="baduser")
        assert allowed is False
        assert "blacklisted" in reason.lower()

    async def test_whitelist_blocks_unlisted_user(self, scenario):
        task = await AgentTask.objects.create(
            name="Whitelist Task",
            cron_expr="0 * * * *",
            job_type="analyze",
            agent_scenario_id=scenario.id,
            action_type=AgentActionType.COMMENT,
            whitelist=["gooduser"],
            is_active=True,
        )
        try:
            checker = GuardsChecker()
            allowed, reason = await checker.check(task, target_user="otheruser")
            assert allowed is False
            assert "whitelist" in reason.lower()
        finally:
            await AgentTask.objects.delete_by_id(task.id)

    async def test_rate_limit_blocks(self, task, scenario, source):
        for _ in range(5):
            await _make_action(scenario.id, source.id, task.id, status=BotActionStatus.EXECUTED, dry_run=False)

        checker = GuardsChecker()
        allowed, reason = await checker.check(task)
        assert allowed is False
        assert "rate limit" in reason.lower()

    async def test_cooldown_blocks(self, task, scenario, source):
        await _make_action(scenario.id, source.id, task.id, status=BotActionStatus.EXECUTED, dry_run=False)

        checker = GuardsChecker()
        allowed, reason = await checker.check(task)
        assert allowed is False
        assert "cooldown" in reason.lower()

    async def test_rate_limit_is_per_task(self, task, scenario, source):
        """Two tasks may share a scenario; their limits must not merge.

        This is why the count moved off `agent_scenario_id`: with the old scoping
        the second task would have inherited the first one's exhausted budget.
        """
        other = await AgentTask.objects.create(
            name="Second Task",
            cron_expr="0 * * * *",
            job_type="analyze",
            agent_scenario_id=scenario.id,
            action_type=AgentActionType.COMMENT,
            rate_limit_per_hour=5,
            is_active=True,
        )
        try:
            for _ in range(5):
                await _make_action(scenario.id, source.id, task.id, status=BotActionStatus.EXECUTED, dry_run=False)
            checker = GuardsChecker()
            blocked, _ = await checker.check(task)
            allowed, reason = await checker.check(other)
            assert blocked is False
            assert allowed is True, reason
        finally:
            await AgentTask.objects.delete_by_id(other.id)

    async def test_no_task_is_allowed_through(self):
        """An ad-hoc run has no configured guards, so it is not blocked by a
        limit it never opted into."""
        checker = GuardsChecker()
        allowed, reason = await checker.check(None)
        assert allowed is True
        assert reason is None


class TestWriteClients:
    async def test_vk_post_comment_dry_run(self):
        from app.services.social.vk_client import VKClient

        client = VKClient(platform=_make_platform())
        result = await client.post_comment(
            owner_id=-123,
            post_id=456,
            message="Test comment",
            dry_run=True,
        )
        assert result["success"] is True
        assert result["dry_run"] is True
        assert result["payload"]["message"] == "Test comment"

    async def test_telegram_send_message_dry_run(self):
        from app.services.social.tg_client import TelegramClient

        class TestableTelegramClient(TelegramClient):
            async def _build_paginated_response(self, *args, **kwargs):
                return {}

            async def _extract_items_from_response(self, *args, **kwargs):
                return []

            async def _should_stop_pagination(self, *args, **kwargs):
                return True

        client = TestableTelegramClient(platform=_make_platform())
        result = await client.send_message(
            chat_id=123456,
            text="Test message",
            dry_run=True,
        )
        assert result["success"] is True
        assert result["dry_run"] is True
        assert result["payload"]["text"] == "Test message"


class TestAgentTools:
    async def test_actions_log_tool(self, scenario, source):
        for i in range(3):
            await _make_action(scenario.id, source.id, payload={"text": f"Test {i}"})

        from app.agent.toolset.actions import actions_log

        result = await actions_log(limit=10)
        assert "actions" in result
        assert len(result["actions"]) == 3

    async def test_action_send_dry_run(self, scenario, source):
        action = await _make_action(
            scenario.id,
            source.id,
            payload={"text": "Test", "owner_id": -123, "post_id": 456},
        )

        from app.agent.toolset.actions import action_send

        result = await action_send(action_id=action.id, dry_run=True)
        assert result["success"] is True
        assert result["dry_run"] is True
        assert result["status"] == "APPROVED"


class TestAnalyzeHandler:
    async def test_handle_analyze_creates_actions(self, task, scenario, source):
        """TriggerEvaluator pre-filter passes content; BotAction created dry-run.

        The trigger and the action come from the task, so the run must be
        dispatched with `agent_task_id` — that is the only way the handler learns
        what to act on.
        """
        from datetime import date

        from app.jobs.handlers import handle_analyze
        from app.models import AIAnalytics

        analytics = await AIAnalytics.objects.create(
            source_id=source.id,
            analysis_date=date.today(),
            summary_data={"summary": "test content about something"},
            response_payload={"sentiment": {"score": -0.8}, "response": "This is a reply"},
        )

        try:
            stats = await handle_analyze({"source_ids": [source.id], "agent_task_id": task.id})
            assert stats["sources"] == 1
            assert stats["actions_created"] >= 1

            actions = await BotAction.objects.filter(agent_scenario_id=scenario.id)
            assert len(actions) >= 1
            assert actions[0].status.name == "PENDING"
            assert actions[0].dry_run is True
            # Stamped so the task's own rate limit applies to this action later.
            assert actions[0].agent_task_id == task.id
        finally:
            await AIAnalytics.objects.delete_by_id(analytics.id)

    async def test_handle_analyze_without_content_skips(self, scenario, source):
        """No analytics → no actions created."""
        from app.jobs.handlers import handle_analyze

        stats = await handle_analyze({"source_ids": [source.id], "scenario_id": scenario.id})
        assert stats["sources"] == 1
        assert stats["actions_created"] == 0

    async def test_handle_analyze_without_action_type_creates_nothing(self, task, source):
        """A task with no `action_type` analyses and stops.

        The action used to be read off the scenario, where it defaulted to
        COMMENT. With it on the task there is no such default: acting is something
        the owner configures, and inventing a COMMENT here would comment on
        everything the trigger matched.
        """
        from datetime import date

        from app.jobs.handlers import handle_analyze
        from app.models import AIAnalytics

        await AgentTask.objects.update_by_id(task.id, action_type=None)

        analytics = await AIAnalytics.objects.create(
            source_id=source.id,
            analysis_date=date.today(),
            summary_data={"summary": "test content"},
            response_payload={"sentiment": {"score": -0.8}, "response": "reply"},
        )
        try:
            stats = await handle_analyze({"source_ids": [source.id], "agent_task_id": task.id})
            assert stats["actions_created"] == 0
        finally:
            await AIAnalytics.objects.delete_by_id(analytics.id)

    async def test_handle_analyze_respects_guards_block(self, task, scenario, source):
        """Guard blocks action when rate limit is exceeded."""
        from datetime import date

        from app.jobs.handlers import handle_analyze
        from app.models import AIAnalytics

        await AgentTask.objects.update_by_id(task.id, rate_limit_per_hour=0)

        analytics = await AIAnalytics.objects.create(
            source_id=source.id,
            analysis_date=date.today(),
            summary_data={"summary": "test content"},
            response_payload={"sentiment": {"score": -0.8}, "response": "This is a reply"},
        )

        try:
            stats = await handle_analyze({"source_ids": [source.id], "agent_task_id": task.id})
            assert stats["actions_created"] == 0
        finally:
            await AIAnalytics.objects.delete_by_id(analytics.id)


class TestBotActionManager:
    async def test_count_actions_in_window(self, task, scenario, source):
        for _ in range(3):
            await _make_action(scenario.id, source.id, task.id, status=BotActionStatus.EXECUTED, dry_run=False)

        manager = BotActionManager()
        count = await manager.count_actions_in_window(agent_task_id=task.id, hours=1)
        assert count == 3

    async def test_count_ignores_other_tasks_actions(self, task, scenario, source):
        """The count is the task's budget, not the scenario's."""
        other = await AgentTask.objects.create(
            name="Other Task",
            cron_expr="0 * * * *",
            job_type="analyze",
            agent_scenario_id=scenario.id,
            is_active=True,
        )
        try:
            for _ in range(3):
                await _make_action(scenario.id, source.id, other.id, status=BotActionStatus.EXECUTED, dry_run=False)
            manager = BotActionManager()
            assert await manager.count_actions_in_window(agent_task_id=task.id, hours=1) == 0
            assert await manager.count_actions_in_window(agent_task_id=other.id, hours=1) == 3
        finally:
            await AgentTask.objects.delete_by_id(other.id)

    async def test_get_last_action_time(self, task, scenario, source):
        await _make_action(scenario.id, source.id, task.id, status=BotActionStatus.EXECUTED, dry_run=False)

        manager = BotActionManager()
        last_time = await manager.get_last_action_time(agent_task_id=task.id)
        assert last_time is not None
