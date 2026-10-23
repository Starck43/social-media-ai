"""API: deleting a source is refused while a task still depends on it.

`DELETE /api/v1/sources/{id}` answers the same contract the web enforces: 409
`source_in_active_task` naming the blocking task ids while any linked task is
active, and 409 `source_is_last_task_source` naming a paused task whose last
explicit source would disappear (resuming it would fall back to every active
workspace source). A paused task with another linked source may lose one. The
successful path answers 204. Nothing is mocked — the flow runs through the real
application, JWT auth and managers.
"""

from __future__ import annotations

import secrets

from app.core.tenant_context import tenant_scope
from app.models import AgentTask
from app.models.managers.agent_task_manager import AgentTaskManager
from tests.api.test_api_permissions import (  # noqa: F401 — shared API harness
    _auth_headers,
    _client,
    _drop,
    _platform_value,
    _register_owner,
)


async def _make_source(client, headers, name: str = "Read by a task") -> int:
    """A workspace source created through the real API."""
    created = await client.post(
        "/api/v1/sources",
        json={
            "name": name,
            "platform_id": await _platform_value(),
            "source_type": "USER",
            "external_id": secrets.token_hex(6),
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


async def _links(tenant_id: int, task_id: int) -> list[int]:
    with tenant_scope(tenant_id):
        return await AgentTaskManager().get_sources(task_id)


async def test_delete_source_reports_the_active_reader_then_the_paused_last_source() -> None:
    """The refusal follows the task state; only a surplus link may be deleted."""
    async with await _client() as client:
        owner, tenant_id = await _register_owner(client, "SrcDelActive")
        headers = await _auth_headers(client, owner.username)
        source_id = None
        second_id = None
        task_id = None
        try:
            source_id = await _make_source(client, headers)

            # An active collect task names the source as its explicit scope.
            task = await client.post(
                "/api/v1/tasks",
                json={
                    "name": f"api-reader-{secrets.token_hex(3)}",
                    "cron_expr": "0 9 * * *",
                    "job_type": "collect",
                    "source_ids": [source_id],
                },
                headers=headers,
            )
            assert task.status_code == 201, task.text
            task_id = task.json()["id"]
            assert task.json()["is_active"] is True

            refused = await client.delete(f"/api/v1/sources/{source_id}", headers=headers)
            assert refused.status_code == 409, refused.text
            detail = refused.json()["detail"]
            assert detail["error"] == "source_in_active_task"
            assert detail["task_ids"] == [task_id]

            # The refusal wrote nothing: the source is still the caller's.
            kept = await client.get(f"/api/v1/sources/{source_id}", headers=headers)
            assert kept.status_code == 200
            assert await _links(tenant_id, task_id) == [source_id]

            # Once the task is paused the same delete is still refused: it is
            # now the task's only explicit source, and resuming must not widen it.
            paused = await client.patch(f"/api/v1/tasks/{task_id}/pause", json={}, headers=headers)
            assert paused.status_code == 200, paused.text
            assert paused.json()["is_active"] is False

            refused = await client.delete(f"/api/v1/sources/{source_id}", headers=headers)
            assert refused.status_code == 409, refused.text
            detail = refused.json()["detail"]
            assert detail["error"] == "source_is_last_task_source"
            assert detail["task_ids"] == [task_id]
            kept = await client.get(f"/api/v1/sources/{source_id}", headers=headers)
            assert kept.status_code == 200
            assert await _links(tenant_id, task_id) == [source_id]

            # A second explicit source makes the paused task safe to narrow:
            # the delete goes through and the surviving link stays explicit.
            second_id = await _make_source(client, headers, name="A surviving source")
            added = await client.patch(
                f"/api/v1/tasks/{task_id}",
                json={"source_ids": [second_id]},
                headers=headers,
            )
            assert added.status_code == 200, added.text
            assert await _links(tenant_id, task_id) == [source_id, second_id]

            deleted = await client.delete(f"/api/v1/sources/{source_id}", headers=headers)
            assert deleted.status_code == 204, deleted.text
            gone = await client.get(f"/api/v1/sources/{source_id}", headers=headers)
            assert gone.status_code == 404
            source_id = None
            assert await _links(tenant_id, task_id) == [second_id]
            with tenant_scope(tenant_id):
                row = await AgentTask.objects.get(id=task_id)
            assert row is not None and row.is_active is False
        finally:
            if task_id is not None:
                await client.delete(f"/api/v1/tasks/{task_id}", headers=headers)
            for leftover in (source_id, second_id):
                if leftover is not None:
                    await client.delete(f"/api/v1/sources/{leftover}", headers=headers)
            await _drop(owner, tenant_id)
