"""API: deleting a source is refused while an active task still reads it.

`DELETE /api/v1/sources/{id}` answers the same contract the web enforces: 409
`source_in_active_task` naming the blocking task ids while any linked task is
active, and 204 once the operator stops the task. Nothing is mocked — the
flow runs through the real application, JWT auth and managers.
"""

from __future__ import annotations

import secrets

from tests.api.test_api_permissions import (  # noqa: F401 — shared API harness
    _auth_headers,
    _client,
    _drop,
    _platform_value,
    _register_owner,
)


async def _make_source(client, headers) -> int:
    """A workspace source created through the real API."""
    created = await client.post(
        "/api/v1/sources",
        json={
            "name": "Read by a task",
            "platform_id": await _platform_value(),
            "source_type": "USER",
            "external_id": secrets.token_hex(6),
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


async def test_delete_source_answers_409_while_an_active_task_reads_it() -> None:
    """The refusal names the task ids; the row survives until the task stops."""
    async with await _client() as client:
        owner, tenant_id = await _register_owner(client, "SrcDelActive")
        headers = await _auth_headers(client, owner.username)
        source_id = None
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

            # Once the task is paused the same delete goes through.
            paused = await client.patch(f"/api/v1/tasks/{task_id}/pause", json={}, headers=headers)
            assert paused.status_code == 200, paused.text
            assert paused.json()["is_active"] is False

            deleted = await client.delete(f"/api/v1/sources/{source_id}", headers=headers)
            assert deleted.status_code == 204, deleted.text
            gone = await client.get(f"/api/v1/sources/{source_id}", headers=headers)
            assert gone.status_code == 404
            source_id = None
        finally:
            if source_id is not None:
                await client.delete(f"/api/v1/sources/{source_id}", headers=headers)
            await _drop(owner, tenant_id)
