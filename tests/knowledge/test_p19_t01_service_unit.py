"""DB-exempt service contracts; integration and locking need real PostgreSQL."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from sqlalchemy import event
from sqlalchemy.engine.interfaces import Dialect

from app.services.knowledge import documents as mod
from app.services.knowledge.document_contracts import DocumentCreate


@pytest.fixture(autouse=True)
def no_db():
    def forbidden(*args, **kwargs):
        raise AssertionError("DB access forbidden")

    event.listen(Dialect, "do_connect", forbidden)
    yield
    event.remove(Dialect, "do_connect", forbidden)


@pytest.fixture
def snapshot():
    now = datetime(2026, 11, 7, tzinfo=timezone.utc)
    doc = SimpleNamespace(
        id=10,
        tenant_id=1,
        title="Letter",
        document_type="letter",
        counterparty="",
        tags=[],
        contact_id=None,
        case_evidence_id=None,
        owner_user_id=2,
        data_boundary="cloud",
        updated_at=now,
        current_revision_id=11,
    )
    rev = SimpleNamespace(
        id=11,
        version=1,
        status="draft",
        content_hash=mod.compute_content_hash("body"),
        body_text="body",
        change_description=None,
        approved_by=None,
        author_user_id=2,
        unsupported_terms_note="Existing warning",
    )
    return doc, rev


def service_fixture(monkeypatch, doc, rev):
    service = mod.DocumentService()
    monkeypatch.setattr(
        service, "_actor", AsyncMock(return_value=(1, 2, "owner", ("public", "collection.source.view")))
    )
    monkeypatch.setattr(service, "_doc", AsyncMock(return_value=doc))
    monkeypatch.setattr(service, "_revision", AsyncMock(return_value=rev))
    monkeypatch.setattr(service, "_references", AsyncMock())
    return service


def version(doc, rev):
    return {"expected_revision_id": rev.id, "expected_updated_at": doc.updated_at}


def test_input_rejects_unknown_invalid_and_unbounded_fields():
    for values in (
        {"title": " ", "document_type": "letter", "body_text": "x"},
        {"title": "x", "document_type": "case", "body_text": "x"},
        {"title": "x", "document_type": "letter", "body_text": "x\x00"},
        {"title": "x", "document_type": "letter", "body_text": "x", "contact_id": True},
        {"title": "x", "document_type": "letter", "body_text": "x", "tags": ["a", "a"]},
        {"title": "x", "document_type": "letter", "body_text": "x", "case_id": 1},
    ):
        with pytest.raises(ValidationError):
            DocumentCreate.model_validate(values)


def test_projection_maps_invalidated_and_preserves_legacy_null_type(snapshot):
    doc, rev = snapshot
    doc.document_type = None
    rev.status = "draft_invalidated"
    result = mod._project(doc, rev)
    assert result.status == "draft" and result.approval_invalidated is True and result.document_type is None


@pytest.mark.asyncio
async def test_update_appends_new_revision_without_rewriting_old(monkeypatch, snapshot):
    doc, rev = snapshot
    service = service_fixture(monkeypatch, doc, rev)
    added = []
    session = SimpleNamespace(add=lambda row: added.append(row), flush=AsyncMock(), refresh=AsyncMock())

    async def flush():
        added[0].id = 12

    session.flush.side_effect = flush
    result = await service.update(
        10,
        {**version(doc, rev), "body_text": "new body", "change_description": "edit"},
        boundary="cloud",
        session=session,
    )
    assert rev.body_text == "body" and rev.status == "draft"
    assert len(added) == 1 and added[0].status == "draft_invalidated"
    assert added[0].author_user_id == 2 and added[0].author == ""
    assert result.body_text == "new body" and result.approval_invalidated and result.approved_by is None
    assert added[0].unsupported_terms_note == "Existing warning"


@pytest.mark.asyncio
async def test_stale_update_is_rejected_before_mutating(monkeypatch, snapshot):
    doc, rev = snapshot
    service = service_fixture(monkeypatch, doc, rev)
    session = SimpleNamespace(add=lambda row: pytest.fail("unexpected insert"), flush=AsyncMock(), refresh=AsyncMock())
    with pytest.raises(mod.DocumentConflict):
        await service.update(10, {**version(doc, rev), "expected_revision_id": 99, "body_text": "new"}, session=session)
    assert rev.body_text == "body" and doc.title == "Letter"
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_transition_validates_exact_revision_status_and_hash(monkeypatch, snapshot):
    doc, rev = snapshot
    service = service_fixture(monkeypatch, doc, rev)
    session = SimpleNamespace(flush=AsyncMock(), refresh=AsyncMock())
    data = {**version(doc, rev), "expected_hash": rev.content_hash, "expected_status": "draft", "target": "in_review"}
    result = await service.transition(10, data, session=session)
    assert result.status == "in_review" and rev.status == "in_review"
    with pytest.raises(mod.DocumentConflict):
        await service.transition(10, data, session=session)
    assert rev.status == "in_review"


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["approved", "rejected", "revision_requested"])
async def test_final_review_decision_fails_closed_without_approval_authority(monkeypatch, snapshot, target):
    doc, rev = snapshot
    rev.status = "in_review"
    service = service_fixture(monkeypatch, doc, rev)
    session = SimpleNamespace(flush=AsyncMock(), refresh=AsyncMock())
    data = {**version(doc, rev), "expected_hash": rev.content_hash, "expected_status": "in_review", "target": target}
    with pytest.raises(PermissionError):
        await service.transition(10, data, session=session)
    assert rev.status == "in_review" and rev.approved_by is None
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_case_reference_filters_context_acl_tenant_and_boundary(snapshot):
    doc, rev = snapshot
    data = DocumentCreate(title="x", document_type="memo", body_text="x", data_boundary="cloud", case_evidence_id=4)
    session = SimpleNamespace(scalar=AsyncMock(return_value=4))
    await mod.DocumentService()._references(session, data, (1, 2, "owner", ("collection.source.view", "public")))
    query = session.scalar.await_args.args[0]
    text = str(query)
    params = query.compile().params
    assert all(name in text for name in ("tenant_id", "context_type", "acl_scope", "data_boundary"))
    assert "case" in params.values() and "cloud" in list(params.values())[-1]
    session.scalar.return_value = None
    with pytest.raises(mod.DocumentUnavailable):
        await mod.DocumentService()._references(session, data, (1, 2, "owner", ("collection.source.view", "public")))


@pytest.mark.asyncio
async def test_actor_rechecks_membership_and_scope(monkeypatch):
    from app.core import permissions, tenant_context
    from app.types import ActionType

    actor = SimpleNamespace(id=9, is_active=True)
    monkeypatch.setattr(tenant_context, "is_bypass", lambda: False)
    monkeypatch.setattr(tenant_context, "current_tenant_id", lambda: 7)
    monkeypatch.setattr(permissions, "get_current_user", lambda: actor)
    monkeypatch.setattr(permissions, "has_permission", lambda *args: True)
    session = SimpleNamespace(scalar=AsyncMock(side_effect=[9, 7, SimpleNamespace(role_id=3), "owner"]))
    identity = await mod.DocumentService()._actor(session, ActionType.UPDATE)
    assert identity[:3] == (7, 9, "owner") and "collection.source.view" in identity[3]
    session.scalar.side_effect = [None, 7, SimpleNamespace(role_id=3), "owner"]
    with pytest.raises(PermissionError):
        await mod.DocumentService()._actor(session, ActionType.UPDATE)
    session.scalar.side_effect = [9, 7, None]
    with pytest.raises(PermissionError):
        await mod.DocumentService()._actor(session, ActionType.VIEW)
    monkeypatch.setattr(tenant_context, "is_bypass", lambda: True)
    with pytest.raises(PermissionError):
        await mod.DocumentService()._actor(session, ActionType.VIEW)


@pytest.mark.asyncio
async def test_document_lookup_is_tenant_and_acl_fenced(snapshot):
    doc, rev = snapshot
    service = mod.DocumentService()
    session = SimpleNamespace(scalar=AsyncMock(return_value=doc))
    identity = (1, 2, "owner", ("collection.source.view", "public"))
    assert await service._doc(session, 10, identity, "cloud", lock=True) == doc
    stmt = session.scalar.await_args.args[0]
    sql, params = str(stmt), stmt.compile().params
    assert all(field in sql for field in ("tenant_id", "acl_scope", "data_boundary"))
    assert 1 in params.values() and 10 in params.values()
    with pytest.raises(mod.DocumentUnavailable):
        await service._doc(session, 0, identity, "cloud")
    session.scalar.return_value = None
    with pytest.raises(mod.DocumentUnavailable):
        await service._doc(session, 10, identity, "cloud")
    with pytest.raises(mod.DocumentUnavailable):
        await service._revision(session, doc)
    session.scalar.return_value = rev
    assert await service._revision(session, doc) == rev


@pytest.mark.asyncio
async def test_output_redacts_inaccessible_relations(monkeypatch, snapshot):
    from app.core import permissions

    doc, rev = snapshot
    doc.contact_id = 3
    doc.case_evidence_id = 4
    monkeypatch.setattr(permissions, "get_current_user", lambda: object())
    monkeypatch.setattr(permissions, "has_permission", lambda *args: False)
    session = SimpleNamespace(scalar=AsyncMock(return_value=None))
    result = await mod.DocumentService()._output(session, doc, rev, (1, 2, "viewer", ("public",)))
    assert result.contact_id is None and result.case_evidence_id is None
    assert doc.contact_id == 3 and doc.case_evidence_id == 4


@pytest.mark.asyncio
async def test_reference_validation_checks_owner_contact_and_evidence(monkeypatch):
    from app.core import permissions

    monkeypatch.setattr(permissions, "get_current_user", lambda: object())
    monkeypatch.setattr(permissions, "has_permission", lambda *args: True)
    data = DocumentCreate(
        title="x",
        document_type="memo",
        body_text="x",
        data_boundary="cloud",
        owner_user_id=2,
        contact_id=3,
        case_evidence_id=4,
    )
    service = mod.DocumentService()
    identity = (1, 2, "owner", ("public", "collection.source.view"))
    session = SimpleNamespace(scalar=AsyncMock(side_effect=[2, 3, 4]))
    await service._references(session, data, identity)
    assert session.scalar.await_count == 3
    session.scalar.side_effect = [None]
    with pytest.raises(mod.DocumentUnavailable):
        await service._references(session, data, identity)
    session.scalar.side_effect = [2, None]
    with pytest.raises(mod.DocumentUnavailable):
        await service._references(session, data, identity)
    monkeypatch.setattr(permissions, "has_permission", lambda *args: False)
    session.scalar.side_effect = [2]
    with pytest.raises(PermissionError):
        await service._references(session, data, identity)


@pytest.mark.asyncio
async def test_create_get_list_delete_and_stale_delete(monkeypatch, snapshot):
    from app.models import DocumentRevision, KnowledgeDocument

    doc, rev = snapshot
    service = service_fixture(monkeypatch, doc, rev)
    monkeypatch.setattr(service, "_output", AsyncMock(return_value="projected"))
    added = []
    session = SimpleNamespace(
        add=lambda row: added.append(row), flush=AsyncMock(), refresh=AsyncMock(), delete=AsyncMock()
    )

    async def flush():
        for row in added:
            if isinstance(row, KnowledgeDocument):
                row.id = 10
            if isinstance(row, DocumentRevision):
                row.id = 11

    session.flush.side_effect = flush
    assert (
        await service.create({"title": "new", "document_type": "memo", "body_text": "body"}, session=session)
        == "projected"
    )
    assert len(added) == 2 and added[1].status == "draft" and added[1].author_user_id == 2
    assert await service.get(10, session=session) == "projected"
    session.scalars = AsyncMock(return_value=SimpleNamespace(all=lambda: [doc]))
    page = await service.list(session=session)
    assert page["items"] == ["projected"] and page["next_before_id"] is None
    with pytest.raises(ValueError):
        await service.list(limit=0, session=session)
    with pytest.raises(mod.DocumentConflict):
        await service.delete(10, {**version(doc, rev), "expected_revision_id": 99}, session=session)
    assert await service.delete(10, version(doc, rev), session=session) == {"deleted": True}
    session.delete.assert_awaited_once_with(doc)


@pytest.mark.asyncio
async def test_templates_are_scoped_and_version_checked(monkeypatch):
    from app.models.knowledge import DocumentTemplate
    from app.services.knowledge.document_contracts import TemplateCreate

    now = datetime(2026, 11, 7, tzinfo=timezone.utc)
    template = SimpleNamespace(
        id=10, tenant_id=1, name="Test", document_type="memo", content="x", variables_schema={}, updated_at=now
    )
    service = mod.DocumentService()
    monkeypatch.setattr(service, "_actor", AsyncMock(return_value=(1, 2, "owner", ("public",))))
    session = SimpleNamespace(
        scalar=AsyncMock(return_value=template),
        scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [template])),
        flush=AsyncMock(),
        refresh=AsyncMock(),
        delete=AsyncMock(),
        add=lambda row: None,
    )
    assert (await service.get_template(10, session=session)).name == "Test"
    page = await service.list_templates(session=session)
    assert len(page["items"]) == 1
    with pytest.raises(ValueError):
        await service.list_templates(limit=0, session=session)
    with pytest.raises(mod.DocumentUnavailable):
        await service._template(session, -1, (1, 2, "owner", ()))
    data = {
        "name": "New",
        "document_type": "memo",
        "content": "new",
        "variables_schema": {},
        "expected_updated_at": now,
    }
    assert (await service.update_template(10, data, session=session)).name == "New"
    with pytest.raises(mod.DocumentConflict):
        await service.delete_template(
            10, {"expected_updated_at": datetime(2020, 1, 1, tzinfo=timezone.utc)}, session=session
        )
    assert await service.delete_template(10, {"expected_updated_at": now}, session=session) == {"deleted": True}
    created = []
    session.add = lambda row: created.append(row)

    async def flush():
        created[0].id, created[0].updated_at = 20, now

    session.flush.side_effect = flush
    result = await service.create_template(
        TemplateCreate(name="Created", document_type="memo", content="body"), session=session
    )
    assert result.id == 20 and isinstance(created[0], DocumentTemplate)
    with pytest.raises(ValidationError):
        TemplateCreate(name=" ", document_type="memo", content="x")
