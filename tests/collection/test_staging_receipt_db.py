"""PostgreSQL checks for strict collection receipts on the workspace schema."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

import pytest

from app.core.permissions import service_permission_scope
from app.core.tenant_context import tenant_scope
from app.models import CollectedItem, Platform, Source, Tenant
from app.services.monitoring.staging import ContentAdmissionError, admit_telegram_items
from app.types import PlatformType, SourceType
from app.utils.collected_content import build_staged_rows

pytestmark = pytest.mark.tenancy


async def _tenant() -> Tenant:
    suffix = secrets.token_hex(6)
    return await Tenant.objects.create(name="Staging receipt", slug=f"receipt-{suffix}", plan="business")


async def _source(
    tenant_id: int,
    *,
    platform_type: PlatformType | None = None,
    source_type: SourceType = SourceType.USER,
) -> Source:
    platforms = Platform.objects.filter(is_active=True)
    if platform_type is not None:
        platforms = platforms.filter(platform_type=platform_type)
    platform = await platforms.first()
    assert platform is not None, "the test database seeds platforms"
    with tenant_scope(tenant_id), service_permission_scope("source", "create"):
        return await Source.objects.create(
            platform_id=platform.id,
            name="Receipt source",
            external_id=f"receipt-{secrets.token_hex(6)}",
            source_type=source_type,
            params={},
        )


async def _stored(
    source: Source,
    *,
    status="selected",
    external_id="post-1:reply-2",
    content_hash="a" * 64,
) -> CollectedItem:
    with tenant_scope(source.tenant_id):
        return await CollectedItem.objects.create(
            source_id=source.id,
            external_id=external_id,
            content_hash=content_hash,
            content_type="comment",
            selection_status=status,
            run_id=81,
            text="persisted text",
            metrics={"reactions": 4},
            author={},
            attachments=[],
        )


def _receipt(source: Source, **overrides):
    row = {
        "tenant_id": source.tenant_id,
        "source_id": source.id,
        "content_type": "comment",
        "external_id": "post-1:reply-2",
        "content_hash": "a" * 64,
        "selection_status": "selected",
        "text": "incoming text",
        "metrics": {"parent_context": {"external_id": "post-1", "text": "parent"}},
    }
    row.update(overrides)
    return row


async def _confirm(source: Source, receipt: dict) -> None:
    from app.core.database import async_session_maker

    session = async_session_maker()
    try:
        with tenant_scope(source.tenant_id):
            async with session.begin():
                await CollectedItem.objects.confirm_stored_items(session, [receipt])
    finally:
        await session.close()


async def test_postgres_receipt_requires_a_matching_durable_row():
    tenant = await _tenant()
    try:
        source = await _source(tenant.id)
        with pytest.raises(ValueError, match="content_receipt_missing"):
            await _confirm(source, _receipt(source))
        with tenant_scope(tenant.id):
            assert await CollectedItem.objects.filter(source_id=source.id).count() == 0
    finally:
        await Tenant.objects.delete_by_id(tenant.id)


@pytest.mark.parametrize(
    ("stored_hash", "stored_status", "receipt_hash", "receipt_status", "error"),
    [
        ("b" * 64, "selected", "a" * 64, "selected", "content_identity_conflict"),
        ("a" * 64, "needs_review", "a" * 64, "selected", "content_selection_conflict"),
    ],
)
async def test_postgres_receipt_rejects_hash_and_selection_conflicts(
    stored_hash, stored_status, receipt_hash, receipt_status, error
):
    tenant = await _tenant()
    try:
        source = await _source(tenant.id)
        stored = await _stored(source, status=stored_status, content_hash=stored_hash)

        with pytest.raises(ValueError, match=error):
            await _confirm(source, _receipt(source, content_hash=receipt_hash, selection_status=receipt_status))

        with tenant_scope(tenant.id):
            current = await CollectedItem.objects.get(id=stored.id)
        assert current is not None
        assert current.selection_status == stored_status
        assert current.metrics == {"reactions": 4}
    finally:
        await Tenant.objects.delete_by_id(tenant.id)


@pytest.mark.parametrize("stored_status", ["selected", None], ids=["selected", "legacy-null"])
async def test_postgres_receipt_adds_only_missing_parent_context_and_keeps_row_state(stored_status):
    tenant = await _tenant()
    try:
        source = await _source(tenant.id)
        stored = await _stored(source, status=stored_status)
        with tenant_scope(tenant.id):
            await CollectedItem.objects.update_by_id(stored.id, analyze_attempts=2)

        await _confirm(source, _receipt(source))

        with tenant_scope(tenant.id):
            current = await CollectedItem.objects.get(id=stored.id)
        assert current is not None
        assert current.metrics == {
            "reactions": 4,
            "parent_context": {"external_id": "post-1", "text": "parent"},
        }
        assert current.text == "persisted text"
        assert current.selection_status == stored_status
        assert current.analyze_attempts == 2
        assert current.run_id == 81
    finally:
        await Tenant.objects.delete_by_id(tenant.id)


async def test_postgres_receipt_fails_closed_without_tenant_context():
    tenant = await _tenant()
    try:
        source = await _source(tenant.id)
        from app.core.database import async_session_maker

        session = async_session_maker()
        try:
            with pytest.raises(ValueError, match="content_tenant_invalid"):
                await CollectedItem.objects.confirm_stored_items(session, [_receipt(source)])
        finally:
            await session.close()
    finally:
        await Tenant.objects.delete_by_id(tenant.id)


async def test_telegram_status_conflict_rolls_back_raw_admission_and_cursor():
    tenant = await _tenant()
    try:
        source = await _source(
            tenant.id,
            platform_type=PlatformType.TELEGRAM,
            source_type=SourceType.CHANNEL,
        )
        item = {
            "id": "2",
            "external_id": f"{source.external_id}_2",
            "platform": "telegram",
            "text": "immutable incoming message",
            "date": datetime.now(timezone.utc),
        }
        row = build_staged_rows([item], source, run_id=None)[0]
        row["selection_status"] = "needs_review"
        with tenant_scope(tenant.id):
            stored = await CollectedItem.objects.create(**row)

        with tenant_scope(tenant.id):
            with pytest.raises(ContentAdmissionError):
                await admit_telegram_items([item], source)

        with tenant_scope(tenant.id):
            current_source = await Source.objects.get(id=source.id)
            current_item = await CollectedItem.objects.get(id=stored.id)
        assert current_source is not None
        assert current_source.last_item_id is None
        assert current_source.params == {}
        assert current_item is not None
        assert current_item.selection_status == "needs_review"
        assert current_item.text == item["text"]
    finally:
        await Tenant.objects.delete_by_id(tenant.id)
