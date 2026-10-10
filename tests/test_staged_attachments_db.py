"""Real PostgreSQL/asyncpg acceptance for staged attachment snapshots.

The contract test proves the SQL that gets *built* with stdlib doubles; this
file runs the very same `CollectedItem.objects.store_items` against a live
schema with the 0089 column installed and asserts what PostgreSQL and asyncpg
actually persist. Nothing here is covered there: those checks never reach the
driver, and the JSON binding is exactly what breaks on a real one.

Run from the dedicated worktree:

    /Users/admin/Projects/social-media-ai/.venv/bin/python \\
        tests/test_staged_attachments_db.py

No pytest bootstrap, no conftest, no ensure_test_database, no schema creation
or migration — the column is expected to exist already (alembic 0089).
"""

from __future__ import annotations

import asyncio
import os
import sys
import traceback
import uuid
from datetime import datetime, timezone

from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings  # noqa: E402
from app.core.database import async_engine, new_session  # noqa: E402
from app.core.tenant_context import tenant_scope  # noqa: E402
from app.models import CollectedItem  # noqa: E402
from app.utils.content_attachments import normalize_attachments  # noqa: E402

SCHEMA = settings.DB_SCHEMA
MARKER = "synthetic-attachments"


def _row(source_id: int, external_id: str, attachments, **over):
    row = {
        "run_id": None,
        "source_id": source_id,
        "external_id": external_id,
        "content_hash": f"{MARKER}-{external_id}",
        "platform": "vk",
        "published_at": datetime.now(timezone.utc),
        "media_type": "photo",
        "attachments": attachments,
        "text": "raw text",
        "metrics": {"views": 1},
        "author": {"id": "1"},
        "permalink": None,
    }
    row.update(over)
    return row


async def _create_tenant() -> int:
    suffix = uuid.uuid4().hex[:8]
    async with async_engine.begin() as conn:
        result = await conn.execute(
            text(
                "INSERT INTO tenants (name, slug, plan, timezone, is_active, daily_cost_limit, max_sources) "
                "VALUES (:name, :slug, 'pro', 'Europe/Moscow', TRUE, 5.0, 20) RETURNING id"
            ),
            {"name": f"{MARKER}-{suffix}", "slug": f"{MARKER}-{suffix}"},
        )
        return result.scalar_one()


async def _drop_tenant(tenant_id: int) -> None:
    async with async_engine.begin() as conn:
        await conn.execute(text(f"DELETE FROM {SCHEMA}.collected_items WHERE tenant_id = {tenant_id}"))
        await conn.execute(text(f"DELETE FROM tenants WHERE id = {tenant_id}"))


async def _raw_attachments(tenant_id: int, external_id: str):
    """What PostgreSQL actually stored, bypassing the ORM entirely."""
    async with async_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    f"SELECT attachments FROM {SCHEMA}.collected_items "
                    "WHERE tenant_id = :tenant_id AND external_id = :external_id"
                ),
                {"tenant_id": tenant_id, "external_id": external_id},
            )
        ).first()
        return None if row is None else row[0]


class Check:
    def __init__(self) -> None:
        self.failures: list[str] = []

    def equal(self, actual, expected, label: str) -> None:
        if actual != expected:
            self.failures.append(f"{label}: expected {expected!r}, got {actual!r}")

    def true(self, value, label: str) -> None:
        if not value:
            self.failures.append(f"{label}: expected truthy, got {value!r}")


async def check_json_binding_roundtrip(check: Check, tenant_id: int) -> None:
    """A real list of dicts survives the driver as JSON, not as a repr string."""
    source_id = 900000 + tenant_id
    attachments = [
        {"type": "photo", "url": "https://example.com/a.jpg"},
        {"type": "video", "url": "https://example.com/b.mp4"},
    ]

    session = new_session()
    try:
        with tenant_scope(tenant_id):
            stored = await CollectedItem.objects.store_items(session, [_row(source_id, "json-ok", attachments)])
        await session.commit()
        check.equal(stored, 1, "store_items reports one inserted row")

        # The write path normalizes, so the persisted snapshot is the mapped
        # vocabulary (`photo` -> `image`), not the caller's raw input.
        expected = normalize_attachments(attachments)
        raw = await _raw_attachments(tenant_id, "json-ok")
        check.equal(raw, expected, "PostgreSQL stored the normalized list verbatim")
        check.true(isinstance(raw, list), "stored value is a JSON array, not a string")

        with tenant_scope(tenant_id):
            rows = await CollectedItem.objects.for_source(source_id)
        check.equal(len(rows), 1, "ORM readback returns the row")
        check.equal(rows[0].attachments, expected, "ORM readback returns the same list")
    finally:
        await session.close()


async def check_null_versus_empty(check: Check, tenant_id: int) -> None:
    """NULL means legacy/unknown; [] means explicitly no attachments."""
    source_id = 900000 + tenant_id

    session = new_session()
    try:
        with tenant_scope(tenant_id):
            await CollectedItem.objects.store_items(
                session,
                [
                    _row(source_id, "null-case", None),
                    _row(source_id, "empty-case", []),
                ],
            )
        await session.commit()

        null_raw = await _raw_attachments(tenant_id, "null-case")
        empty_raw = await _raw_attachments(tenant_id, "empty-case")
        check.equal(null_raw, None, "None persists as SQL NULL")
        check.equal(empty_raw, [], "[] persists as an empty JSON array")
        check.true(null_raw is not empty_raw, "NULL and [] stay distinguishable in PostgreSQL")

        with tenant_scope(tenant_id):
            rows = {r.external_id: r for r in await CollectedItem.objects.for_source(source_id)}
        check.equal(rows["null-case"].attachments, None, "ORM reads NULL back as None")
        check.equal(rows["empty-case"].attachments, [], "ORM reads [] back as an empty list")

        null_item = rows["null-case"].as_agent_item()
        empty_item = rows["empty-case"].as_agent_item()
        check.true("attachments" not in null_item, "NULL row omits attachments from the replay dict")
        check.equal(empty_item.get("attachments"), [], "[] row replays an empty attachment list")
    finally:
        await session.close()


async def check_rejected_reference_placeholder(check: Check, tenant_id: int) -> None:
    """A rejected URL keeps a placeholder so media coverage cannot vanish."""
    source_id = 900000 + tenant_id
    attachments = [
        {"type": "photo", "url": "http://insecure.example.com/a.jpg"},
        {"type": "video", "url": "https://api.telegram.org/file/b.mp4"},
        {"type": "photo", "url": "https://example.com/ok.jpg"},
    ]

    session = new_session()
    try:
        with tenant_scope(tenant_id):
            await CollectedItem.objects.store_items(session, [_row(source_id, "rejected", attachments)])
        await session.commit()

        raw = await _raw_attachments(tenant_id, "rejected")
        expected = normalize_attachments(attachments)
        check.equal(raw, expected, "stored snapshot matches the normalizer output")
        check.equal(
            [entry["url"] for entry in raw],
            [None, None, "https://example.com/ok.jpg"],
            "rejected references keep a placeholder with url=None",
        )
        check.equal(
            [entry["type"] for entry in raw],
            ["image", "video", "image"],
            "media types are mapped to the allowlisted vocabulary",
        )
    finally:
        await session.close()


async def check_tenant_scope_and_conflict(check: Check, tenant_id: int) -> None:
    """Tenant stamping and ON CONFLICT DO NOTHING still hold with the new column."""
    source_id = 900000 + tenant_id
    row = _row(source_id, "conflict-case", [{"type": "photo", "url": "https://example.com/c.jpg"}])

    session = new_session()
    try:
        with tenant_scope(tenant_id):
            first = await CollectedItem.objects.store_items(session, [row])
            replay = await CollectedItem.objects.store_items(session, [row])
        await session.commit()
        check.equal(first, 1, "first insert reports one row")
        check.true(replay in (0, 1), f"replay does not raise, reports {replay}")

        async with async_engine.connect() as conn:
            count = (
                await conn.execute(
                    text(
                        f"SELECT count(*) FROM {SCHEMA}.collected_items "
                        "WHERE tenant_id = :tenant_id AND external_id = 'conflict-case'"
                    ),
                    {"tenant_id": tenant_id},
                )
            ).scalar()
        check.equal(count, 1, "ON CONFLICT DO NOTHING kept a single row")

        # A row written outside any tenant context must be refused, not guessed.
        try:
            await CollectedItem.objects.store_items(session, [_row(source_id, "no-tenant", None)])
            check.failures.append("store_items without a tenant context was not refused")
        except Exception as exc:  # noqa: BLE001
            check.true(
                "tenant" in str(exc).lower(),
                f"refusal mentions the tenant, got: {type(exc).__name__}",
            )
    finally:
        await session.close()


async def main() -> int:
    tenant_id = await _create_tenant()
    check = Check()
    try:
        await check_json_binding_roundtrip(check, tenant_id)
        await check_null_versus_empty(check, tenant_id)
        await check_rejected_reference_placeholder(check, tenant_id)
        await check_tenant_scope_and_conflict(check, tenant_id)
    finally:
        await _drop_tenant(tenant_id)

    if check.failures:
        print("FAILED")
        for failure in check.failures:
            print(f"  - {failure}")
        return 1
    print("OK — 4 real-driver checks passed, no synthetic rows remain")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        sys.exit(2)
