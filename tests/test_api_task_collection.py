"""API: the task owns a validated collection intent.

Every write surface shares `app/services/collection/intent.py`, so the API must
store the same canonical block the web editor writes, accept the explicit clear
(`null`), and answer a malformed block with 400 instead of storing it.
"""

from __future__ import annotations

import secrets

from tests.test_api_permissions import (  # noqa: F401 — shared API harness
    _auth_headers,
    _client,
    _drop,
    _register_owner,
)


def _collection(**overrides) -> dict:
    raw = {
        "content_types": ["posts", "comments"],
        "engagement_types": ["likes"],
        "limit": 250,
        "queries": ["доставка"],
        "city": {"name": "Киров", "mode": "prefer"},
        "comments": {"max_per_post": 5, "total": 40},
        "candidates": {"limit": 25, "confirm": "auto"},
    }
    raw.update(overrides)
    return raw


async def test_create_stores_the_canonical_collection_block() -> None:
    async with await _client() as client:
        owner, tenant_id = await _register_owner(client, "ApiCollectCreate")
        headers = await _auth_headers(client, owner.username)
        name = f"api-collection-{secrets.token_hex(3)}"
        try:
            response = await client.post(
                "/api/v1/tasks",
                json={
                    "name": name,
                    "cron_expr": "@once",
                    "job_type": "analyze",
                    "payload": {"period": "week"},
                    "collection": _collection(),
                },
                headers=headers,
            )
            assert response.status_code == 201, response.text
            payload = response.json()["payload"]
            assert payload["period"] == "week"
            block = payload["collection"]
            assert block["version"] == 1
            assert block["content_types"] == ["posts", "comments"]
            assert block["engagement_types"] == ["likes"]
            assert block["limit"] == 250
            assert block["queries"] == ["доставка"]
            assert block["city"] == {"name": "Киров", "mode": "prefer"}
            assert block["comments"] == {"max_per_post": 5, "total": 40}
            assert block["candidates"] == {"limit": 25, "confirm": "auto"}
        finally:
            await _drop(owner, tenant_id)


async def test_patch_keeps_updates_and_clears_the_block() -> None:
    async with await _client() as client:
        owner, tenant_id = await _register_owner(client, "ApiCollectPatch")
        headers = await _auth_headers(client, owner.username)
        name = f"api-collection-{secrets.token_hex(3)}"
        try:
            created = await client.post(
                "/api/v1/tasks",
                json={
                    "name": name,
                    "cron_expr": "@once",
                    "job_type": "collect",
                    "collection": _collection(limit=100),
                },
                headers=headers,
            )
            assert created.status_code == 201, created.text
            task_id = created.json()["id"]

            # Updating a non-payload field keeps the stored block.
            updated = await client.patch(
                f"/api/v1/tasks/{task_id}",
                json={"tracked_users": ["vk:1"]},
                headers=headers,
            )
            assert updated.status_code == 200, updated.text
            assert updated.json()["payload"]["collection"]["limit"] == 100
            assert updated.json()["payload"]["tracked_users"] == ["vk:1"]

            # Replacing the rest of the payload keeps the task-owned block too.
            payload_update = await client.patch(
                f"/api/v1/tasks/{task_id}",
                json={"payload": {"period": "day"}},
                headers=headers,
            )
            assert payload_update.status_code == 200, payload_update.text
            assert payload_update.json()["payload"]["period"] == "day"
            assert payload_update.json()["payload"]["collection"]["limit"] == 100

            # A new block replaces it, validated through the same parser.
            replaced = await client.patch(
                f"/api/v1/tasks/{task_id}",
                json={"collection": _collection(limit=300)},
                headers=headers,
            )
            assert replaced.status_code == 200, replaced.text
            assert replaced.json()["payload"]["collection"]["limit"] == 300

            # Explicit null clears it; omitting the field would have kept it.
            cleared = await client.patch(
                f"/api/v1/tasks/{task_id}",
                json={"collection": None},
                headers=headers,
            )
            assert cleared.status_code == 200, cleared.text
            assert "collection" not in cleared.json()["payload"]
        finally:
            await _drop(owner, tenant_id)


async def test_malformed_collection_is_a_400_and_does_not_mutate() -> None:
    async with await _client() as client:
        owner, tenant_id = await _register_owner(client, "ApiCollectInvalid")
        headers = await _auth_headers(client, owner.username)
        name = f"api-collection-{secrets.token_hex(3)}"
        try:
            created = await client.post(
                "/api/v1/tasks",
                json={
                    "name": name,
                    "cron_expr": "@once",
                    "job_type": "analyze",
                    "collection": _collection(limit=250),
                },
                headers=headers,
            )
            assert created.status_code == 201, created.text
            task_id = created.json()["id"]

            response = await client.patch(
                f"/api/v1/tasks/{task_id}",
                json={"collection": {"limit": 0}},
                headers=headers,
            )
            assert response.status_code == 400
            assert "collection_limit_out_of_range" in response.json()["detail"]

            current = await client.get(f"/api/v1/tasks/{task_id}", headers=headers)
            assert current.json()["payload"]["collection"]["limit"] == 250
        finally:
            await _drop(owner, tenant_id)


async def test_job_types_without_collection_drop_the_block() -> None:
    async with await _client() as client:
        owner, tenant_id = await _register_owner(client, "ApiCollectDigest")
        headers = await _auth_headers(client, owner.username)
        name = f"api-collection-{secrets.token_hex(3)}"
        try:
            response = await client.post(
                "/api/v1/tasks",
                json={
                    "name": name,
                    "cron_expr": "@once",
                    "job_type": "digest",
                    "collection": _collection(),
                },
                headers=headers,
            )
            assert response.status_code == 201, response.text
            assert "collection" not in response.json()["payload"]
        finally:
            await _drop(owner, tenant_id)
