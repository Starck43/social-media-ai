"""Coverage for previously unreachable branches in the document service.

Targets in ``app/services/knowledge/documents.py``:

* ``update`` -> ``DocumentConflict("description_requires_new_revision")`` when
  only ``change_description`` is supplied and nothing material changed;
* ``transition`` -> ``DocumentConflict("invalid_transition")`` for a target that
  is schema-valid but not allowed from the current status;
* ``list`` -> skip legacy documents whose ``current_revision_id`` points at no
  readable revision (``DocumentUnavailable``).

Requires an isolated PostgreSQL schema (``DB_TEST_SCHEMA``); the guard refuses the working one.
"""

from contextlib import ExitStack

import pytest
from sqlalchemy import delete, func, select

from app.core.access_catalog import workspace_grants
from app.core.database import async_session_maker
from app.core.permissions import permission_scope
from app.core.tenant_context import tenant_scope
from app.models import DocumentRevision, KnowledgeDocument
from app.services.knowledge.documents import DocumentConflict, document_service
from tests.shared.fixtures.personal_workspace import personal_world  # noqa: F401
from tests.support.schema_guard import require_isolated_schema

pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]


def scoped(world, workspace=0):
    stack = ExitStack()
    stack.enter_context(tenant_scope(world.tenants[workspace].id))
    stack.enter_context(
        permission_scope(world.users[0 if workspace == 0 else 2], member_grants=workspace_grants("owner"))
    )
    return stack


@pytest.fixture
async def coverage_world(personal_world):  # noqa: F811
    require_isolated_schema()
    ids = [tenant.id for tenant in personal_world.tenants]
    try:
        yield personal_world
    finally:
        # Tenant deletion cascades, but verify no synthetic rows survive.
        async with async_session_maker() as session, session.begin():
            await session.execute(delete(KnowledgeDocument).where(KnowledgeDocument.tenant_id.in_(ids)))
        async with async_session_maker() as session:
            for model in (KnowledgeDocument, DocumentRevision):
                assert (
                    await session.scalar(select(func.count()).select_from(model).where(model.tenant_id.in_(ids))) == 0
                )


def expected(doc):
    return {"expected_revision_id": doc.current_revision_id, "expected_updated_at": doc.updated_at}


def decision(doc, target):
    return {
        **expected(doc),
        "expected_hash": doc.content_hash,
        "expected_status": doc.status,
        "target": target,
    }


async def create(body, **extra):
    return await document_service.create(
        {"title": "synthetic", "document_type": "memo", "body_text": body, "data_boundary": "local_only", **extra}
    )


async def test_update_only_change_description_requires_new_revision(coverage_world):
    """Line 566: a description-only edit cannot be stored beside an unchanged body."""
    with scoped(coverage_world):
        doc = await create("original")
        pending = await document_service.transition(doc.id, decision(doc, "in_review"), boundary="local_only")
        assert pending.status == "in_review"

        with pytest.raises(DocumentConflict) as excinfo:
            await document_service.update(
                doc.id, {**expected(pending), "change_description": "note only"}, boundary="local_only"
            )
        assert excinfo.value.args[0] == "description_requires_new_revision"
        # The rejection is soft state policy: the stored revision is untouched.
        after = await document_service.get(doc.id, boundary="local_only")
        assert after.version == 1 and after.status == "in_review"

        # A material change carrying the same description succeeds and bumps version.
        updated = await document_service.update(
            doc.id,
            {**expected(pending), "body_text": "changed", "change_description": "note only"},
            boundary="local_only",
        )
        assert updated.version == 2 and updated.change_description == "note only"


async def test_transition_invalid_target_for_status(coverage_world):
    """Line 593: an approved revision cannot reopen without a new revision."""
    with scoped(coverage_world):
        doc = await create("original")
        pending = await document_service.transition(doc.id, decision(doc, "in_review"), boundary="local_only")
        approved = await document_service.transition(doc.id, decision(pending, "approved"), boundary="local_only")
        assert approved.status == "approved" and approved.approved_by is not None

        # "in_review" passes the Literal schema yet is not in the approved graph.
        with pytest.raises(DocumentConflict) as excinfo:
            await document_service.transition(doc.id, decision(approved, "in_review"), boundary="local_only")
        assert excinfo.value.args[0] == "invalid_transition"

        # Decision state and attribution survive the rejected transition.
        after = await document_service.get(doc.id, boundary="local_only")
        assert after.status == "approved" and after.approved_by == approved.approved_by


async def test_transition_terminal_state_blocks_any_target(coverage_world):
    """Line 593 from a terminal status: every allowed target is now invalid."""
    with scoped(coverage_world):
        doc = await create("original")
        pending = await document_service.transition(doc.id, decision(doc, "in_review"), boundary="local_only")
        rejected = await document_service.transition(doc.id, decision(pending, "rejected"), boundary="local_only")
        assert rejected.status == "rejected"

        for target in ("in_review", "revision_requested"):
            with pytest.raises(DocumentConflict) as excinfo:
                await document_service.transition(doc.id, decision(rejected, target), boundary="local_only")
            assert excinfo.value.args[0] == "invalid_transition"

        assert (await document_service.get(doc.id, boundary="local_only")).status == "rejected"


async def test_list_skips_legacy_document_without_readable_revision(coverage_world):
    """Line 502: legacy rows survive storage but stay out of the listing."""
    with scoped(coverage_world):
        live = await create("readable")
        tenant_id = coverage_world.tenants[0].id
        async with async_session_maker() as session, session.begin():
            legacy = KnowledgeDocument(
                tenant_id=tenant_id,
                title="legacy synthetic",
                document_type="memo",
                source_ref="legacy",
                data_boundary="local_only",
                acl_scope="collection.source.view",
                current_revision_id=None,
            )
            session.add(legacy)
        async with async_session_maker() as session:
            stored = await session.scalar(
                select(KnowledgeDocument).where(KnowledgeDocument.title == "legacy synthetic")
            )
            assert stored is not None and stored.current_revision_id is None

        page = await document_service.list(boundary="local_only", session=None)
        ids = [item.id for item in page["items"]]
        assert live.id in ids
        assert stored.id not in ids

        # The legacy row is skipped, not deleted: it is still stored.
        async with async_session_maker() as session:
            assert (
                await session.scalar(
                    select(func.count()).select_from(KnowledgeDocument).where(KnowledgeDocument.id == stored.id)
                )
                == 1
            )
