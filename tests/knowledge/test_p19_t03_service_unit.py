"""DB-exempt service tests with explicit persistence and authorization doubles."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event
from sqlalchemy.engine.interfaces import Dialect

from app.services.knowledge import documents as docs
from app.services.knowledge.versions import VersionService, version_service


@pytest.fixture(autouse=True)
def no_db():
    def forbidden(*args, **kwargs):
        raise AssertionError("DB access forbidden")

    event.listen(Dialect, "do_connect", forbidden)
    yield
    event.remove(Dialect, "do_connect", forbidden)


@pytest.fixture
def data(monkeypatch):
    now = datetime(2026, 11, 8, tzinfo=timezone.utc)
    doc = SimpleNamespace(id=10, tenant_id=1, data_boundary="local_only", current_revision_id=11, updated_at=now)
    current = SimpleNamespace(id=11, version=3, status="approved")
    source = SimpleNamespace(
        id=8, version=1, body_text="old", content_hash=docs.compute_content_hash("old"), unsupported_terms_note="verify"
    )
    monkeypatch.setattr(docs.document_service, "_actor", AsyncMock(return_value=(1, 2, "owner", ("public",))))
    monkeypatch.setattr(docs.document_service, "_doc", AsyncMock(return_value=doc))
    monkeypatch.setattr(docs.document_service, "_revision", AsyncMock(return_value=current))
    monkeypatch.setattr(docs.document_service, "_output", AsyncMock(side_effect=lambda s, d, r, i: r))
    monkeypatch.setattr(version_service, "_by_id", AsyncMock(return_value=source))
    added = []
    session = SimpleNamespace(add=added.append, flush=AsyncMock(), refresh=AsyncMock())
    expected = {"expected_revision_id": 11, "expected_updated_at": now}
    return doc, current, source, session, added, expected


@pytest.mark.asyncio
async def test_restore_appends_even_when_body_matches_and_never_mutates_source(data):
    doc, current, source, session, added, expected = data
    result = await version_service.restore(10, 8, expected, boundary="local_only", session=session)
    assert added == [result]
    assert (result.version, result.body_text, result.content_hash) == (4, "old", source.content_hash)
    assert result.status == "draft_invalidated" and result.approved_by is None
    assert result.author_user_id == 2 and result.unsupported_terms_note == "verify"
    assert current.status == "approved" and source.body_text == "old"
    docs.document_service._doc.assert_awaited_once_with(
        session, 10, (1, 2, "owner", ("public",)), "local_only", lock=True
    )


@pytest.mark.asyncio
async def test_restore_rejects_stale_and_bad_hash_before_insert(data):
    doc, current, source, session, added, expected = data
    with pytest.raises(docs.DocumentConflict):
        await version_service.restore(
            10, 8, {**expected, "expected_revision_id": 99}, boundary="local_only", session=session
        )
    source.content_hash = "incorrect"
    with pytest.raises(docs.DocumentConflict):
        await version_service.restore(10, 8, expected, boundary="local_only", session=session)
    assert not added
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_history_boundary_and_restore_reclassification_fail_closed(data):
    doc, current, source, session, added, expected = data
    for boundary in ("cloud", "company"):
        with pytest.raises(docs.DocumentUnavailable):
            await version_service.list(10, boundary=boundary, session=session)
        with pytest.raises(docs.DocumentUnavailable):
            await version_service.diff(10, 8, 11, boundary=boundary, session=session)
        with pytest.raises(docs.DocumentUnavailable):
            await version_service.restore(10, 8, expected, boundary=boundary, session=session)
    doc.data_boundary = "cloud"
    with pytest.raises(docs.DocumentUnavailable):
        await version_service.restore(10, 8, expected, boundary="local_only", session=session)
    assert not added


@pytest.mark.asyncio
async def test_diff_uses_document_scoped_revision_lookup(data, monkeypatch):
    doc, current, source, session, added, expected = data
    other = SimpleNamespace(id=11, body_text="new")
    version_service._by_id.side_effect = [source, other]
    result = await version_service.diff(10, 8, 11, boundary="local_only", session=session)
    assert result.from_revision_id == 8 and result.to_revision_id == 11
    assert version_service._by_id.await_count == 2
    assert "".join(part.after for part in result.parts) == "new"


@pytest.mark.asyncio
async def test_revision_lookup_scopes_to_document_and_tenant(data):
    doc, current, source, session, added, expected = data
    service = VersionService()
    session.scalar = AsyncMock(return_value=None)
    with pytest.raises(docs.DocumentUnavailable):
        await service._by_id(session, doc, 8)
    statement = session.scalar.await_args.args[0]
    params = statement.compile().params
    assert set(params.values()) == {1, 8, 10}
    with pytest.raises(docs.DocumentUnavailable):
        await service._by_id(session, doc, True)


@pytest.mark.asyncio
async def test_history_pagination_scopes_query_and_limits_output(data):
    doc, current, source, session, added, expected = data
    now = datetime(2026, 11, 8, tzinfo=timezone.utc)

    def row(row_id):
        return SimpleNamespace(
            id=row_id,
            version=row_id,
            content_hash="h",
            author_user_id=2,
            author="",
            created_at=now,
            change_description=None,
        )

    session.scalars = AsyncMock(return_value=SimpleNamespace(all=lambda: [row(6), row(5), row(4)]))
    result = await version_service.list(10, boundary="local_only", before_id=7, limit=2, session=session)
    assert [item.id for item in result["items"]] == [6, 5] and result["next_before_id"] == 5
    params = session.scalars.await_args.args[0].compile().params
    assert {1, 10, 7, 3} <= set(params.values())
    with pytest.raises(ValueError):
        await version_service.list(10, boundary="local_only", limit=True, session=session)
