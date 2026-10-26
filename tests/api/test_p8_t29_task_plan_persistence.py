"""P8-T29 API persistence acceptance; real PostgreSQL through root conftest."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

import pytest

from app.core.tenant_context import tenant_scope
from app.models import Platform
from app.models.managers.agent_task_manager import AgentTaskManager
from app.tasks.cron import next_run_at
from tests.api.test_api_permissions import _auth_headers, _client, _drop, _platform_value, _register_owner


async def _vk_platform_id() -> int:
    platform = await Platform.objects.filter(platform_type="vk").first()
    assert platform is not None, "VK platform is seeded"
    return platform.id


@pytest.mark.tenancy
async def test_legacy_task_source_links_round_trip_and_survive_omitted_patch() -> None:
    """Legacy API source_ids still write links; omission preserves them on PATCH."""
    async with await _client() as client:
        owner, tenant_id = await _register_owner(client, "P8T29SourceRoundTrip")
        headers = await _auth_headers(client, owner.username)
        source_id = None
        task_id = None
        try:
            source_response = await client.post(
                "/api/v1/sources",
                json={
                    "name": f"task-plan-source-{secrets.token_hex(3)}",
                    "platform_id": await _platform_value(),
                    "source_type": "USER",
                    "external_id": secrets.token_hex(6),
                },
                headers=headers,
            )
            assert source_response.status_code == 201, source_response.text
            source_id = source_response.json()["id"]

            created = await client.post(
                "/api/v1/tasks",
                json={
                    "name": f"source-round-trip-{secrets.token_hex(3)}",
                    "cron_expr": "0 9 * * *",
                    "job_type": "collect",
                    "source_ids": [source_id],
                    "payload": {"round_trip_marker": "kept"},
                },
                headers=headers,
            )
            assert created.status_code == 201, created.text
            task_id = created.json()["id"]
            assert created.json()["payload"]["round_trip_marker"] == "kept"
            with tenant_scope(tenant_id):
                assert await AgentTaskManager().get_sources(task_id) == [source_id]

            explicit = await client.patch(
                f"/api/v1/tasks/{task_id}",
                json={"source_ids": [source_id]},
                headers=headers,
            )
            assert explicit.status_code == 200, explicit.text
            with tenant_scope(tenant_id):
                assert await AgentTaskManager().get_sources(task_id) == [source_id]

            omitted = await client.patch(
                f"/api/v1/tasks/{task_id}",
                json={"name": "renamed-source-round-trip"},
                headers=headers,
            )
            assert omitted.status_code == 200, omitted.text
            with tenant_scope(tenant_id):
                assert await AgentTaskManager().get_sources(task_id) == [source_id]
        finally:
            if task_id is not None:
                await client.delete(f"/api/v1/tasks/{task_id}", headers=headers)
            if source_id is not None:
                await client.delete(f"/api/v1/sources/{source_id}", headers=headers)
            await _drop(owner, tenant_id)


@pytest.mark.tenancy
async def test_plan_source_links_and_plan_payload_stay_in_sync() -> None:
    """Plan-owned sources cannot be changed through the legacy field alone."""
    async with await _client() as client:
        owner, tenant_id = await _register_owner(client, "P8T29PlanSourceSync")
        headers = await _auth_headers(client, owner.username)
        source_id = None
        task_id = None
        try:
            source_response = await client.post(
                "/api/v1/sources",
                json={
                    "name": f"planned-source-{secrets.token_hex(3)}",
                    "platform_id": await _vk_platform_id(),
                    "source_type": "USER",
                    "external_id": secrets.token_hex(6),
                },
                headers=headers,
            )
            assert source_response.status_code == 201, source_response.text
            source_id = source_response.json()["id"]
            plan = {
                "version": 1,
                "goal": "Collect selected source",
                "platforms": ["vk"],
                "source_scope": "selected",
                "source_ids": [source_id],
                "collection": {},
                "window": {"type": "rolling", "days": 1},
                "trigger": {"type": "schedule", "cron": "0 0 * * *", "timezone": "Asia/Yekaterinburg"},
            }
            before = datetime.now(timezone.utc)
            created = await client.post(
                "/api/v1/tasks",
                json={
                    "name": f"planned-source-sync-{secrets.token_hex(3)}",
                    "job_type": "collect",
                    "task_plan": plan,
                    "source_ids": [source_id],
                    "is_active": False,
                },
                headers=headers,
            )
            assert created.status_code == 201, created.text
            task_id = created.json()["id"]
            assert created.json()["payload"]["task_plan"]["trigger"]["timezone"] == "Asia/Yekaterinburg"
            after = datetime.now(timezone.utc)
            candidates = {
                next_run_at("0 0 * * *", "Asia/Yekaterinburg", after=before),
                next_run_at("0 0 * * *", "Asia/Yekaterinburg", after=after),
            }
            actual = datetime.fromisoformat(created.json()["next_run_at"])
            assert min(abs((actual - candidate).total_seconds()) for candidate in candidates) < 60
            with tenant_scope(tenant_id):
                assert await AgentTaskManager().get_sources(task_id) == [source_id]

            diverged = await client.patch(
                f"/api/v1/tasks/{task_id}",
                json={"source_ids": []},
                headers=headers,
            )
            assert diverged.status_code == 400, diverged.text
            with tenant_scope(tenant_id):
                assert await AgentTaskManager().get_sources(task_id) == [source_id]

            payload_update = await client.patch(
                f"/api/v1/tasks/{task_id}",
                json={"payload": {"round_trip_marker": "preserved-plan"}},
                headers=headers,
            )
            assert payload_update.status_code == 200, payload_update.text
            assert payload_update.json()["payload"]["task_plan"]["source_ids"] == [source_id]
            assert payload_update.json()["payload"]["task_plan"]["trigger"]["timezone"] == "Asia/Yekaterinburg"
            assert payload_update.json()["next_run_at"] == created.json()["next_run_at"]
            assert payload_update.json()["payload"]["round_trip_marker"] == "preserved-plan"
            with tenant_scope(tenant_id):
                assert await AgentTaskManager().get_sources(task_id) == [source_id]
        finally:
            if task_id is not None:
                await client.delete(f"/api/v1/tasks/{task_id}", headers=headers)
            if source_id is not None:
                await client.delete(f"/api/v1/sources/{source_id}", headers=headers)
            await _drop(owner, tenant_id)
