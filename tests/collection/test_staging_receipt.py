"""Strict staging receipts reject conflicts before the caller can claim a run identity."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.tenant_context import tenant_scope
from app.models import CollectedItem


def _row(**overrides):
    row = {
        "tenant_id": 9,
        "source_id": 17,
        "content_type": "comment",
        "external_id": "post-1:reply-2",
        "content_hash": "a" * 64,
        "selection_status": "selected",
        "text": "reply",
        "metrics": {},
    }
    row.update(overrides)
    return row


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


@pytest.mark.asyncio
async def test_collection_receipt_rejects_missing_identity_without_tenant_bypass():
    session = SimpleNamespace(execute=AsyncMock(return_value=_Rows([])), flush=AsyncMock())

    with tenant_scope(9):
        with pytest.raises(ValueError, match="content_receipt_missing"):
            await CollectedItem.objects.confirm_stored_items(session, [_row()])

    session.flush.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stored_hash", "stored_status", "incoming_hash", "incoming_status", "error"),
    [
        ("b" * 64, "selected", "a" * 64, "selected", "content_identity_conflict"),
        ("a" * 64, "needs_review", "a" * 64, "selected", "content_selection_conflict"),
    ],
)
async def test_collection_receipt_rejects_hash_or_selection_conflicts(
    stored_hash, stored_status, incoming_hash, incoming_status, error
):
    stored = SimpleNamespace(
        content_type="comment",
        external_id="post-1:reply-2",
        content_hash=stored_hash,
        selection_status=stored_status,
        metrics={},
    )
    session = SimpleNamespace(execute=AsyncMock(return_value=_Rows([stored])), flush=AsyncMock())

    with tenant_scope(9):
        with pytest.raises(ValueError, match=error):
            await CollectedItem.objects.confirm_stored_items(
                session, [_row(content_hash=incoming_hash, selection_status=incoming_status)]
            )

    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_receipt_enriches_only_missing_parent_context_and_preserves_processing_state():
    stored = SimpleNamespace(
        content_type="comment",
        external_id="post-1:reply-2",
        content_hash="a" * 64,
        selection_status="selected",
        metrics={"reactions": 3},
        text="persisted reply",
        analyze_attempts=2,
        run_id=6,
    )
    row = _row(metrics={"parent_context": {"external_id": "post-1", "text": "parent"}})
    session = SimpleNamespace(execute=AsyncMock(return_value=_Rows([stored])), flush=AsyncMock())

    with tenant_scope(9):
        await CollectedItem.objects.confirm_stored_items(session, [row])

    assert stored.metrics == {
        "reactions": 3,
        "parent_context": {"external_id": "post-1", "text": "parent"},
    }
    assert stored.text == "persisted reply"
    assert stored.analyze_attempts == 2
    assert stored.run_id == 6
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_anonymous_identity_is_confirmed_by_hash_and_null_selection_defaults_to_selected():
    stored = SimpleNamespace(
        content_type="post",
        external_id=None,
        content_hash="a" * 64,
        selection_status=None,
        metrics={},
    )
    row = _row(content_type="post", external_id=None, selection_status="selected")
    session = SimpleNamespace(execute=AsyncMock(return_value=_Rows([stored])), flush=AsyncMock())

    with tenant_scope(9):
        await CollectedItem.objects.confirm_stored_items(session, [row])

    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_an_empty_receipt_is_a_noop_without_tenant_context():
    session = SimpleNamespace(execute=AsyncMock(), flush=AsyncMock())

    await CollectedItem.objects.confirm_stored_items(session, [])

    session.execute.assert_not_awaited()
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_nonempty_receipt_fails_closed_without_a_tenant():
    session = SimpleNamespace(execute=AsyncMock(), flush=AsyncMock())

    with pytest.raises(ValueError, match="content_tenant_invalid"):
        await CollectedItem.objects.confirm_stored_items(session, [_row()])

    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_telegram_admission_rejects_a_review_to_selected_conflict_before_coverage_read():
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [("reply-2", "a" * 64, "needs_review")])))

    with tenant_scope(9):
        with pytest.raises(ValueError, match="content_selection_conflict"):
            await CollectedItem.objects.admit_items(session, [_row(external_id="reply-2")])

    session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_telegram_admission_accepts_existing_analytics_coverage_without_reinserting():
    responses = [
        SimpleNamespace(all=lambda: []),
        _Rows([{"content_hashes": ["a" * 64]}]),
    ]
    session = SimpleNamespace(execute=AsyncMock(side_effect=responses))

    with tenant_scope(9):
        count = await CollectedItem.objects.admit_items(session, [_row(content_type="post", external_id="post-1")])

    assert count == 0
    assert session.execute.await_count == 2
