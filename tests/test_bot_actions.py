"""Tests for bot_actions: ledger, guards, write-clients, agent tools."""

import uuid

import pytest

from app.models import BotAction, AgentScenario, Platform, Source
from app.models.managers.bot_action_manager import BotActionManager
from app.services.social.guards import GuardsChecker
from app.types import BotActionStatus, BotActionType, BotTriggerType, PlatformType, SourceType


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
    s = await AgentScenario.objects.create(
        name="Test Scenario",
        trigger_type=BotTriggerType.KEYWORD_MATCH,
        trigger_config={"keywords": ["test"], "mode": "any"},
        action_type=BotActionType.COMMENT,
        rate_limit_per_hour=5,
        cooldown_seconds=60,
        requires_approval=True,
        blacklist=["baduser"],
        whitelist=None,
        is_active=True,
    )
    yield s
    await AgentScenario.objects.delete_by_id(s.id)


@pytest.fixture
async def source(platform, scenario):
    s = await Source.objects.create(
        platform_id=platform.id,
        name="Test Source",
        source_type=SourceType.CHANNEL,
        external_id="12345",
        agent_scenario_id=scenario.id,
        is_active=True,
    )
    yield s
    await Source.objects.delete_by_id(s.id)


async def _make_action(scenario_id: int, source_id: int, **overrides):
    params = dict(
        agent_scenario_id=scenario_id,
        source_id=source_id,
        action_type=BotActionType.COMMENT,
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

    async def test_bot_action_status_transitions(self, scenario, source):
        action = await _make_action(scenario.id, source.id)

        manager = BotActionManager()

        approved = await manager.approve_action(action_id=action.id, user_id=1)
        assert approved.status == BotActionStatus.APPROVED
        assert approved.confirmed_by == 1

        executed = await manager.mark_executed(action_id=action.id, result={"comment_id": 456})
        assert executed.status == BotActionStatus.EXECUTED
        assert executed.result["comment_id"] == 456

    async def test_bot_action_reject(self, scenario, source):
        action = await _make_action(scenario.id, source.id)

        manager = BotActionManager()
        rejected = await manager.reject_action(action_id=action.id, user_id=1)
        assert rejected.status == BotActionStatus.REJECTED


class TestGuardsChecker:
    async def test_blacklist_blocks_action(self, scenario):
        checker = GuardsChecker()
        allowed, reason = await checker.check(scenario, target_user="baduser")
        assert allowed is False
        assert "blacklisted" in reason.lower()

    async def test_whitelist_blocks_unlisted_user(self, platform):
        scenario = await AgentScenario.objects.create(
            name="Whitelist Scenario",
            action_type=BotActionType.COMMENT,
            whitelist=["gooduser"],
            is_active=True,
        )
        try:
            checker = GuardsChecker()
            allowed, reason = await checker.check(scenario, target_user="otheruser")
            assert allowed is False
            assert "whitelist" in reason.lower()
        finally:
            await AgentScenario.objects.delete_by_id(scenario.id)

    async def test_rate_limit_blocks(self, scenario, source):
        for _ in range(5):
            await _make_action(scenario.id, source.id, status=BotActionStatus.EXECUTED, dry_run=False)

        checker = GuardsChecker()
        allowed, reason = await checker.check(scenario)
        assert allowed is False
        assert "rate limit" in reason.lower()

    async def test_cooldown_blocks(self, scenario, source):
        await _make_action(scenario.id, source.id, status=BotActionStatus.EXECUTED, dry_run=False)

        checker = GuardsChecker()
        allowed, reason = await checker.check(scenario)
        assert allowed is False
        assert "cooldown" in reason.lower()


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
    async def test_handle_analyze_creates_actions(self, scenario, source):
        """TriggerEvaluator pre-filter passes content; BotAction created dry-run."""
        from datetime import date

        from app.jobs.handlers import handle_analyze
        from app.models import AIAnalytics
        from app.types import BotTriggerType

        await AgentScenario.objects.update_by_id(
            scenario.id,
            trigger_type=BotTriggerType.KEYWORD_MATCH,
            trigger_config={"keywords": ["test"], "mode": "any"},
        )

        analytics = await AIAnalytics.objects.create(
            source_id=source.id,
            analysis_date=date.today(),
            summary_data={"summary": "test content about something"},
            response_payload={"sentiment": {"score": -0.8}, "response": "This is a reply"},
        )

        try:
            stats = await handle_analyze({"source_ids": [source.id]})
            assert stats["sources"] == 1
            assert stats["actions_created"] >= 1

            actions = await BotAction.objects.filter(agent_scenario_id=scenario.id)
            assert len(actions) >= 1
            assert actions[0].status.name == "PENDING"
            assert actions[0].dry_run is True
        finally:
            await AIAnalytics.objects.delete_by_id(analytics.id)

    async def test_handle_analyze_without_content_skips(self, scenario, source):
        """No analytics → no actions created."""
        from app.jobs.handlers import handle_analyze

        stats = await handle_analyze({"source_ids": [source.id]})
        assert stats["sources"] == 1
        assert stats["actions_created"] == 0

    async def test_handle_analyze_respects_guards_block(self, scenario, source):
        """Guard blocks action when rate limit is exceeded."""
        from datetime import date

        from app.jobs.handlers import handle_analyze
        from app.models import AIAnalytics

        await AgentScenario.objects.update_by_id(scenario.id, rate_limit_per_hour=0)

        analytics = await AIAnalytics.objects.create(
            source_id=source.id,
            analysis_date=date.today(),
            summary_data={"summary": "test content"},
            response_payload={"sentiment": {"score": -0.8}, "response": "This is a reply"},
        )

        try:
            stats = await handle_analyze({"source_ids": [source.id]})
            assert stats["actions_created"] == 0
        finally:
            await AIAnalytics.objects.delete_by_id(analytics.id)
            await AgentScenario.objects.update_by_id(scenario.id, rate_limit_per_hour=5)


class TestBotActionManager:
    async def test_count_actions_in_window(self, scenario, source):
        for _ in range(3):
            await _make_action(scenario.id, source.id, status=BotActionStatus.EXECUTED, dry_run=False)

        manager = BotActionManager()
        count = await manager.count_actions_in_window(agent_scenario_id=scenario.id, hours=1)
        assert count == 3

    async def test_get_last_action_time(self, scenario, source):
        await _make_action(scenario.id, source.id, status=BotActionStatus.EXECUTED, dry_run=False)

        manager = BotActionManager()
        last_time = await manager.get_last_action_time(agent_scenario_id=scenario.id)
        assert last_time is not None
