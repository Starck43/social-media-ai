"""Durable analysis receipt behavior; requires migration 0104 in the test schema."""

import re
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.database import new_session
from app.core.permissions import operator_permission_scope
from app.core.tenant_context import tenant_scope
from app.main import create_application
from app.models import (
    AIAnalytics,
    AgentScenario,
    AnalyzedContentReceipt,
    Platform,
    PlatformRole,
    Source,
    Tenant,
    TenantUser,
    User,
)
from app.models.managers.tenant_manager import TenantUserManager
from app.services.ai.analysis_context import analysis_fingerprint
from app.services.ai.dedup import filter_by_context_receipts, item_hash
from app.types import ActionType, PeriodType, PlatformRoleType, PlatformType, SourceType, WorkspaceRoleType

CSRF_RE = re.compile(r'name="_csrf" value="([^"]+)"')

# The real tenant guard, not the suite-wide bypass: this file gives every test
# its own workspace, and a row written into the bootstrap workspace instead
# would be shared with every other task of the deployment — the staged-retirement
# contract ("another context still needs this row") is about *this* workspace's
# contexts only.
pytestmark = pytest.mark.tenancy


@pytest.fixture
async def receipt_source():
    """Own all rows so this file works without bootstrap/conftest bypasses."""
    token = uuid.uuid4().hex[:12]
    tenant = await Tenant.objects.create(name=f"Receipt tenant {token}", slug=f"receipt-{token}")
    platform = await Platform.objects.create(
        name=f"receipt-{token}",
        platform_type=PlatformType.TELEGRAM,
        base_url="https://example.invalid",
        params={},
    )
    with operator_permission_scope(), tenant_scope(tenant.id):
        source = await Source.objects.create(
            platform_id=platform.id,
            name="Analysis receipt test",
            source_type=SourceType.CHANNEL,
            external_id=f"receipt-{token}",
            params={},
            is_active=True,
        )
    yield source
    with operator_permission_scope(), tenant_scope(tenant.id):
        await AnalyzedContentReceipt.objects.filter(source_id=source.id).delete()
        await AIAnalytics.objects.filter(source_id=source.id).delete()
        await Source.objects.filter(id=source.id).delete()
    await Platform.objects.filter(id=platform.id).delete()
    await Tenant.objects.filter(id=tenant.id).delete()


@pytest.fixture
async def receipt_context(receipt_source):
    with operator_permission_scope(), tenant_scope(receipt_source.tenant_id):
        scenario = await AgentScenario.objects.create(
            name=f"Receipt scenario {uuid.uuid4().hex[:8]}",
            analysis_types=["sentiment"],
            content_types=["text"],
            scope={},
            base_prompt="Analyze {text}",
            media_overrides={},
            summary_prompt=None,
            max_tokens=128,
            output_schema={
                "type": "object",
                "properties": {"sentiment": {"type": "string"}},
                "required": ["sentiment"],
                "additionalProperties": False,
            },
            is_active=True,
            is_default=False,
        )
    yield receipt_source, scenario
    with operator_permission_scope(), tenant_scope(receipt_source.tenant_id):
        await AgentScenario.objects.delete_by_id(scenario.id)


@pytest.mark.asyncio
async def test_receipt_survives_analytics_deletion_and_has_no_age_cutoff(receipt_context):
    receipt_source, scenario = receipt_context
    item = {"platform": "telegram", "external_id": "post-1", "text": "receipt test"}
    digest = item_hash(item)
    fingerprint = analysis_fingerprint(scenario)
    with operator_permission_scope(), tenant_scope(receipt_source.tenant_id):
        analytics = await AIAnalytics.objects.create(
            source_id=receipt_source.id,
            analysis_date=date.today(),
            period_type=PeriodType.DAY,
            analysis_fingerprint=fingerprint,
            summary_data={"content_hashes": [digest]},
        )
        session = new_session()
        try:
            async with session.begin():
                await AnalyzedContentReceipt.objects.record_successes(
                    session,
                    tenant_id=receipt_source.tenant_id,
                    source_id=receipt_source.id,
                    analysis_fingerprint=fingerprint,
                    hashes=[digest],
                    scenario_id=scenario.id,
                    analytics_id=analytics.id,
                )
        finally:
            await session.close()
        receipt = await AnalyzedContentReceipt.objects.filter(source_id=receipt_source.id).first()
        await AnalyzedContentReceipt.objects.update_by_id(
            receipt.id, analyzed_at=datetime.now(timezone.utc) - timedelta(days=45)
        )
        await AIAnalytics.objects.delete_by_id(analytics.id)
        fresh, covered = await filter_by_context_receipts(
            [item], receipt_source.id, receipt_source.tenant_id, fingerprint
        )
    assert fresh == []
    assert covered == {digest}
    with operator_permission_scope(), tenant_scope(receipt_source.tenant_id):
        receipt = await AnalyzedContentReceipt.objects.filter(source_id=receipt_source.id).first()
        assert receipt is not None and receipt.analytics_id is None


@pytest.mark.asyncio
async def test_receipt_is_specific_to_analysis_context(receipt_context):
    receipt_source, scenario = receipt_context
    item = {"platform": "telegram", "external_id": "post-2", "text": "context test"}
    first = analysis_fingerprint(scenario, task_payload={"brands": ["Acme"]})
    second = analysis_fingerprint(scenario, task_payload={"brands": ["Globex"]})
    digest = item_hash(item)
    with operator_permission_scope(), tenant_scope(receipt_source.tenant_id):
        analytics = await AIAnalytics.objects.create(
            source_id=receipt_source.id,
            analysis_date=date.today(),
            period_type=PeriodType.DAY,
            analysis_fingerprint=first,
            summary_data={"content_hashes": [digest]},
        )
        session = new_session()
        try:
            async with session.begin():
                await AnalyzedContentReceipt.objects.record_successes(
                    session,
                    tenant_id=receipt_source.tenant_id,
                    source_id=receipt_source.id,
                    analysis_fingerprint=first,
                    hashes=[digest],
                    scenario_id=scenario.id,
                    analytics_id=analytics.id,
                )
        finally:
            await session.close()
        filtered, covered = await filter_by_context_receipts(
            [item], receipt_source.id, receipt_source.tenant_id, second
        )
    assert filtered == [item]
    assert covered == set()


@pytest.mark.asyncio
@pytest.mark.parametrize("force_reanalyze", [False, True])
async def test_deleted_analytics_receipt_skips_analyzer_before_provider(
    receipt_context, monkeypatch, force_reanalyze
):
    from unittest.mock import AsyncMock

    from app.services.ai.scenario_schema import compile_scenario_output

    receipt_source, scenario = receipt_context
    item = {"platform": "telegram", "external_id": "post-provider-skip", "text": "already analyzed"}
    digest = item_hash(item)
    fingerprint = analysis_fingerprint(scenario, output_schema=compile_scenario_output(scenario).schema)
    with operator_permission_scope(), tenant_scope(receipt_source.tenant_id):
        analytics = await AIAnalytics.objects.create(
            source_id=receipt_source.id,
            analysis_date=date.today(),
            period_type=PeriodType.DAY,
            analysis_fingerprint=fingerprint,
            summary_data={"content_hashes": [digest]},
        )
        session = new_session()
        try:
            async with session.begin():
                await AnalyzedContentReceipt.objects.record_successes(
                    session,
                    tenant_id=receipt_source.tenant_id,
                    source_id=receipt_source.id,
                    analysis_fingerprint=fingerprint,
                    hashes=[digest],
                    scenario_id=scenario.id,
                    analytics_id=analytics.id,
                )
        finally:
            await session.close()
        await AIAnalytics.objects.delete_by_id(analytics.id)

        from app.services.ai.analyzer import AIAnalyzer

        analyzer = AIAnalyzer()
        provider_path = AsyncMock()
        monkeypatch.setattr(analyzer, "_analyze_text", provider_path)
        assert (
            await analyzer.analyze_content(
                [item], receipt_source, agent_scenario=scenario, force_reanalyze=force_reanalyze
            )
            == []
        )
        assert analyzer.completed_hashes == {digest}
        provider_path.assert_not_awaited()


@pytest.mark.asyncio
async def test_analytics_and_receipt_rollback_together(receipt_context):
    receipt_source, scenario = receipt_context
    digest = "c" * 64
    fingerprint = analysis_fingerprint(scenario)
    with tenant_scope(receipt_source.tenant_id):
        session = new_session()
        try:
            with pytest.raises(RuntimeError, match="rollback probe"):
                async with session.begin():
                    analytics = await AIAnalytics.objects.create(
                        session=session,
                        source_id=receipt_source.id,
                        analysis_date=date.today(),
                        period_type=PeriodType.DAY,
                        analysis_fingerprint=fingerprint,
                        summary_data={"content_hashes": [digest]},
                    )
                    await AnalyzedContentReceipt.objects.record_successes(
                        session,
                        tenant_id=receipt_source.tenant_id,
                        source_id=receipt_source.id,
                        analysis_fingerprint=fingerprint,
                        hashes=[digest],
                        scenario_id=scenario.id,
                        analytics_id=analytics.id,
                    )
                    raise RuntimeError("rollback probe")
        finally:
            await session.close()

        assert await AIAnalytics.objects.filter(
            source_id=receipt_source.id, analysis_fingerprint=fingerprint
        ).first() is None
        assert await AnalyzedContentReceipt.objects.filter(
            source_id=receipt_source.id, content_hash=digest, analysis_fingerprint=fingerprint
        ).first() is None


@pytest.mark.asyncio
async def test_period_rollups_keep_same_day_analysis_contexts_separate(receipt_context):
    from app.models.managers.ai_analytics_manager import AIAnalyticsManager

    receipt_source, scenario = receipt_context
    day = date(2026, 2, 1)
    fingerprints = {
        analysis_fingerprint(scenario, task_payload={"brands": [brand]}): posts
        for brand, posts in (("Acme", 2), ("Globex", 7))
    }
    with tenant_scope(receipt_source.tenant_id):
        for fingerprint, posts in fingerprints.items():
            await AIAnalytics.objects.create(
                source_id=receipt_source.id,
                analysis_date=day,
                period_type=PeriodType.DAY,
                analysis_fingerprint=fingerprint,
                summary_data={"content_statistics": {"total_posts": posts, "messages_count": posts}},
            )

        written = await AIAnalyticsManager().build_period_rollups(PeriodType.WEEK, day, date(2026, 2, 7))
        rollups = await AIAnalytics.objects.filter(
            source_id=receipt_source.id, analysis_date=day, period_type=PeriodType.WEEK
        )
        assert written == 2
        assert {row.analysis_fingerprint for row in rollups} == set(fingerprints)
        assert {
            row.analysis_fingerprint: row.summary_data["content_statistics"]["total_posts"] for row in rollups
        } == fingerprints
        await AIAnalytics.objects.filter(source_id=receipt_source.id).delete()


@pytest.mark.asyncio
async def test_raw_cleanup_honors_other_context_date_window(receipt_context):
    """Rows inside another context's unfinished window stay staged; the rest go.

    Regression: the pending-context check treated a completed run as still
    pending, so a row the other context had already analysed stayed staged
    forever and every later cleanup protected it again.
    """
    from datetime import datetime, timezone

    from app.models import AgentTask, CollectedItem
    from app.models.managers.agent_task_manager import AgentTaskManager
    from app.services.ai.scenario_schema import compile_scenario_output

    receipt_source, scenario = receipt_context
    inside_hash, outside_hash = "1" * 64, "2" * 64
    with operator_permission_scope(), tenant_scope(receipt_source.tenant_id):
        # An active task is another analysis context for this source.
        task = await AgentTask.objects.create(
            name=f"Receipt window task {uuid.uuid4().hex[:8]}",
            job_type="analyze",
            cron_expr="@once",
            payload={
                "brands": ["Other context"],
                "cli_dates": {"start_date": "2026-06-01", "end_date": "2026-06-30"},
            },
            agent_scenario_id=scenario.id,
            is_active=True,
        )
        await AgentTaskManager().set_sources(task.id, [receipt_source.id])
        other_fingerprint = analysis_fingerprint(
            scenario,
            task_payload=task.payload,
            trigger_config=task.trigger_config,
            output_schema=compile_scenario_output(scenario).schema,
        )
        for external, digest, published in (
            ("receipt-window-inside", inside_hash, datetime(2026, 6, 15, tzinfo=timezone.utc)),
            ("receipt-window-outside", outside_hash, datetime(2010, 5, 22, tzinfo=timezone.utc)),
        ):
            await CollectedItem.objects.create(
                source_id=receipt_source.id,
                external_id=external,
                content_hash=digest,
                platform="telegram",
                published_at=published,
                text=external,
            )
        # Give the run that deletes its own context fingerprint, so the other
        # task's fingerprint is not mistaken for "current" and skipped.
        current_fingerprint = "a" * 64
        session = new_session()
        try:
            async with session.begin():
                deleted = await CollectedItem.objects.delete_hashes(
                    session,
                    receipt_source.id,
                    [inside_hash, outside_hash],
                    tenant_id=receipt_source.tenant_id,
                    analysis_fingerprint=current_fingerprint,
                )
        finally:
            await session.close()

        remaining = await CollectedItem.objects.filter(source_id=receipt_source.id)
        await AgentTask.objects.delete_by_id(task.id)
        # The row inside the other context's window is still its backlog and
        # stays staged; the row outside that window is retired by this run.
        assert {row.content_hash for row in remaining} == {inside_hash}
        await CollectedItem.objects.filter(source_id=receipt_source.id).delete()


@pytest.mark.asyncio
async def test_receipt_only_handler_skip_does_not_replay_actions(receipt_context, monkeypatch):
    from unittest.mock import AsyncMock

    from app.jobs import handlers
    from app.models import AgentTask, BotAction, CollectedItem
    from app.models.managers.agent_task_manager import AgentTaskManager
    from app.services.ai import analyzer as analyzer_module
    from app.services.ai.dedup import item_hash
    from app.services.ai.trigger_evaluator import trigger_evaluator
    from app.services.social.guards import guards_checker
    from app.types import AgentActionType

    receipt_source, scenario = receipt_context
    item = {"platform": "telegram", "external_id": "receipt-handler-item", "text": "covered content"}
    digest = item_hash(item)
    with operator_permission_scope(), tenant_scope(receipt_source.tenant_id):
        await CollectedItem.objects.create(
            source_id=receipt_source.id,
            external_id=item["external_id"],
            content_hash=digest,
            platform=item["platform"],
            text=item["text"],
        )
        task = await AgentTask.objects.create(
            name=f"Receipt action task {uuid.uuid4().hex[:8]}",
            job_type="analyze",
            cron_expr="@once",
            payload={},
            agent_scenario_id=scenario.id,
            action_type=AgentActionType.COMMENT,
            trigger_config={"keywords": ["covered"]},
            is_active=True,
        )
        await AgentTaskManager().set_sources(task.id, [receipt_source.id])
        old = await AIAnalytics.objects.create(
            source_id=receipt_source.id,
            analysis_date=date.today(),
            summary_data={"summary": "old result", "recent_posts": []},
            response_payload={"response": "old response"},
        )

        async def receipt_hit(analyzer, content, source, **kwargs):
            analyzer.completed_hashes = {digest}
            analyzer.current_fingerprint = "f" * 64
            return None

        monkeypatch.setattr(analyzer_module.AIAnalyzer, "analyze_content", receipt_hit)
        monkeypatch.setattr(handlers, "_retire_staged", AsyncMock(return_value=0))
        monkeypatch.setattr(handlers, "_count_failed_staged", AsyncMock(return_value=0))
        monkeypatch.setattr(trigger_evaluator, "should_analyze", AsyncMock(side_effect=lambda content, _task: content))
        monkeypatch.setattr(trigger_evaluator, "should_act", AsyncMock(return_value=True))
        monkeypatch.setattr(guards_checker, "check", AsyncMock(return_value=(True, None)))
        action_create = AsyncMock(side_effect=AssertionError("receipt-only skip replayed an old action"))
        monkeypatch.setattr(BotAction.objects, "create", action_create)
        try:
            result = await handlers.handle_analyze({"agent_task_id": task.id})
        finally:
            await AIAnalytics.objects.delete_by_id(old.id)
            await AgentTask.objects.delete_by_id(task.id)
            await CollectedItem.objects.filter(source_id=receipt_source.id).delete()
    assert result["actions_created"] == 0
    assert result["analyzed"] == 0
    action_create.assert_not_awaited()


@pytest.mark.asyncio
async def test_owner_can_delete_analytics_and_receipt_still_skips(receipt_context):
    receipt_source, scenario = receipt_context
    token_id = uuid.uuid4().hex[:10]
    username = f"receipt_delete_{token_id}"
    password = "receipt-delete-password-1"
    user = membership = None
    analytics_id = None
    item = {"platform": "telegram", "external_id": f"delete-{token_id}", "text": "retained receipt"}
    digest = item_hash(item)
    fingerprint = analysis_fingerprint(scenario)
    chain_id = f"receipt-delete-{token_id}"

    try:
        with operator_permission_scope(), tenant_scope(receipt_source.tenant_id):
            admin_role = await PlatformRole.objects.filter(codename=PlatformRoleType.ADMIN.value).first()
            assert admin_role is not None
            user = await User.objects.create_user(
                username=username,
                password=password,
                email=f"{username}@example.invalid",
                role_id=admin_role.id,
            )
            membership = await TenantUserManager().add_web_member(
                tenant_id=receipt_source.tenant_id,
                user_id=user.id,
                role=WorkspaceRoleType.OWNER.value,
            )
            analytics = await AIAnalytics.objects.create(
                source_id=receipt_source.id,
                analysis_date=date.today(),
                period_type=PeriodType.DAY,
                topic_chain_id=chain_id,
                analysis_fingerprint=fingerprint,
                summary_data={"content_hashes": [digest]},
            )
            analytics_id = analytics.id
            session = new_session()
            try:
                async with session.begin():
                    await AnalyzedContentReceipt.objects.record_successes(
                        session,
                        tenant_id=receipt_source.tenant_id,
                        source_id=receipt_source.id,
                        analysis_fingerprint=fingerprint,
                        hashes=[digest],
                        scenario_id=scenario.id,
                        analytics_id=analytics.id,
                    )
            finally:
                await session.close()

        loaded_user = await User.objects.prefetch_related("role.permissions").get(id=user.id)
        assert loaded_user is not None and loaded_user.has_perm_for("aianalytics", ActionType.DELETE)

        app = create_application()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver", follow_redirects=True
        ) as client:
            login_page = await client.get("/app/login")
            assert login_page.status_code == 200
            login_match = CSRF_RE.search(login_page.text)
            assert login_match is not None
            login = await client.post(
                "/app/login",
                data={"username": username, "password": password, "_csrf": login_match.group(1)},
            )
            assert login.status_code == 200

            chain_page = await client.get(f"/app/analytics/chains/{chain_id}")
            assert chain_page.status_code == 200
            page_match = CSRF_RE.search(chain_page.text)
            assert page_match is not None
            deleted = await client.post(
                f"/app/analytics/{analytics_id}/delete",
                data={
                    "_csrf": page_match.group(1),
                    "tenant_id": str(receipt_source.tenant_id),
                },
            )
            assert deleted.status_code == 200

        with operator_permission_scope(), tenant_scope(receipt_source.tenant_id):
            assert await AIAnalytics.objects.filter(id=analytics_id).first() is None
            receipt = await AnalyzedContentReceipt.objects.filter(
                source_id=receipt_source.id,
                content_hash=digest,
                analysis_fingerprint=fingerprint,
            ).first()
            assert receipt is not None and receipt.analytics_id is None
            remaining, covered = await filter_by_context_receipts(
                [item], receipt_source.id, receipt_source.tenant_id, fingerprint
            )
        assert remaining == [] and covered == {digest}
    finally:
        if membership is not None:
            with operator_permission_scope(), tenant_scope(receipt_source.tenant_id):
                await TenantUser.objects.filter(id=membership.id).delete()
        if user is not None:
            with operator_permission_scope():
                await User.objects.delete_user(user.id)
