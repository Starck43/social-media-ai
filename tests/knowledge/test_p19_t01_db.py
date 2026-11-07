"""LOCAL PostgreSQL handoff ONLY; never run in a remote no-DB sandbox.

Requires revision 0142 on the owner's isolated test_schema with exclusive writer
ownership. The owner must inventory before/after and run the migration cycle.
"""

import asyncio
from contextlib import ExitStack

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from app.core.access_catalog import workspace_grants
from app.core.config import settings
from app.core.database import async_session_maker
from app.core.permissions import permission_scope
from app.core.tenant_context import tenant_scope
from app.models import Contact, DocumentRevision, KnowledgeDocument, KnowledgeSnippet
from app.models.knowledge import DocumentTemplate
from app.services.knowledge.document_contracts import DocumentCreate
from app.services.knowledge.documents import DocumentUnavailable, document_service
from tests.shared.fixtures.personal_workspace import personal_world  # noqa: F401

pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]


def scope(world, workspace=0):
    stack = ExitStack()
    stack.enter_context(tenant_scope(world.tenants[workspace].id))
    stack.enter_context(
        permission_scope(world.users[0 if workspace == 0 else 2], member_grants=workspace_grants("owner"))
    )
    return stack


@pytest.fixture
async def doc_world(personal_world, monkeypatch):  # noqa: F811
    assert settings.DB_SCHEMA == "test_schema"
    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    world = personal_world
    ids = [tenant.id for tenant in world.tenants]
    try:
        yield world
    finally:
        # Delete only synthetic records owned by personal_world; then verify.
        async with async_session_maker() as session, session.begin():
            for model in (DocumentTemplate, KnowledgeDocument, KnowledgeSnippet, Contact):
                await session.execute(delete(model).where(model.tenant_id.in_(ids)))
        async with async_session_maker() as session:
            for model in (DocumentTemplate, DocumentRevision, KnowledgeDocument, KnowledgeSnippet, Contact):
                count = await session.scalar(select(func.count()).select_from(model).where(model.tenant_id.in_(ids)))
                assert count == 0


async def seed_links(world, *, context="case", boundary="cloud", acl="collection.source.view", workspace=0):
    tenant = world.tenants[workspace].id
    async with async_session_maker() as session, session.begin():
        contact = Contact(tenant_id=tenant, display_name_encrypted="synthetic contact")
        snippet = KnowledgeSnippet(
            tenant_id=tenant,
            title="synthetic evidence",
            source_ref="synthetic:case",
            citation_anchor="",
            data_boundary=boundary,
            acl_scope=acl,
            context_type=context,
            revision_hash="sha256:test",
            excerpt="synthetic",
            extraction_limit_note="",
        )
        session.add_all((contact, snippet))
        await session.flush()
        return contact.id, snippet.id


async def create_doc(world, **extra):
    with scope(world):
        return await document_service.create(
            DocumentCreate(
                title="synthetic document", document_type="letter", body_text="first", data_boundary="cloud", **extra
            )
        )


@pytest.mark.asyncio
async def test_tenant_acl_context_and_revision_immutability(doc_world):
    world = doc_world
    _, evidence_id = await seed_links(world)
    _, wrong_context = await seed_links(world, context="knowledge")
    _, foreign_evidence = await seed_links(world, workspace=1)
    doc = await create_doc(world, case_evidence_id=evidence_id)
    with scope(world):
        for forbidden in (wrong_context, foreign_evidence):
            with pytest.raises(DocumentUnavailable):
                await document_service.create(
                    DocumentCreate(
                        title="forbidden",
                        document_type="memo",
                        body_text="x",
                        data_boundary="cloud",
                        case_evidence_id=forbidden,
                    )
                )
        changed = await document_service.update(
            doc.id,
            {
                "expected_revision_id": doc.current_revision_id,
                "expected_updated_at": doc.updated_at,
                "body_text": "second",
            },
        )
        assert changed.version == 2 and changed.approval_invalidated and changed.approved_by is None
        async with async_session_maker() as session:
            with pytest.raises(IntegrityError):
                await session.execute(
                    update(DocumentRevision)
                    .where(DocumentRevision.id == doc.current_revision_id)
                    .values(body_text="tampered")
                )
                await session.commit()
    with scope(world, workspace=1):
        with pytest.raises(DocumentUnavailable):
            await document_service.get(doc.id)


@pytest.mark.asyncio
async def test_deleted_contact_and_evidence_leave_document(doc_world):
    world = doc_world
    contact_id, evidence_id = await seed_links(world)
    doc = await create_doc(world, contact_id=contact_id, case_evidence_id=evidence_id)
    async with async_session_maker() as session, session.begin():
        await session.execute(delete(Contact).where(Contact.id == contact_id, Contact.tenant_id == world.tenants[0].id))
        await session.execute(
            delete(KnowledgeSnippet).where(
                KnowledgeSnippet.id == evidence_id, KnowledgeSnippet.tenant_id == world.tenants[0].id
            )
        )
    with scope(world):
        current = await document_service.get(doc.id)
        assert current.contact_id is None and current.case_evidence_id is None and current.body_text == "first"


@pytest.mark.asyncio
async def test_concurrent_evidence_deletion_and_edit_preserves_document(doc_world):
    world = doc_world
    _, evidence_id = await seed_links(world)
    doc = await create_doc(world, case_evidence_id=evidence_id)

    async def erase():
        async with async_session_maker() as session, session.begin():
            await session.execute(
                delete(KnowledgeSnippet).where(
                    KnowledgeSnippet.id == evidence_id, KnowledgeSnippet.tenant_id == world.tenants[0].id
                )
            )

    async def edit():
        return await document_service.update(
            doc.id,
            {
                "expected_revision_id": doc.current_revision_id,
                "expected_updated_at": doc.updated_at,
                "title": "concurrent edit",
            },
        )

    with scope(world):
        removed, changed = await asyncio.wait_for(asyncio.gather(erase(), edit(), return_exceptions=True), timeout=15)
        assert removed is None
        assert not isinstance(changed, Exception) or isinstance(changed, DocumentUnavailable)
        current = await document_service.get(doc.id)
        assert current.case_evidence_id is None and current.body_text == "first"
