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
from tests.support.schema_guard import require_isolated_schema

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
    require_isolated_schema()
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


def decision_payload(doc, target):
    return {
        "expected_revision_id": doc.current_revision_id,
        "expected_updated_at": doc.updated_at,
        "expected_hash": doc.content_hash,
        "expected_status": doc.status,
        "target": target,
    }


@pytest.mark.parametrize("target", ["approved", "rejected", "revision_requested"])
async def test_owner_decides_exact_revision_and_revoked_membership_fails(doc_world, target):
    from app.models import TenantUser
    from app.services.knowledge.documents import DocumentConflict

    world = doc_world
    doc = await create_doc(world)
    with scope(world):
        pending = await document_service.transition(doc.id, decision_payload(doc, "in_review"))
        stale = decision_payload(pending, target)
        stale["expected_hash"] = "sha256:" + "0" * 64
        with pytest.raises(DocumentConflict):
            await document_service.transition(doc.id, stale)
        # The scope intentionally retains its old owner grants after membership
        # revocation: the service must reread the membership from the database.
        member_id = world.memberships[0].id
        async with async_session_maker() as session, session.begin():
            await session.execute(update(TenantUser).where(TenantUser.id == member_id).values(is_active=False))
        try:
            with pytest.raises(PermissionError):
                await document_service.transition(doc.id, decision_payload(pending, target))
        finally:
            async with async_session_maker() as session, session.begin():
                await session.execute(update(TenantUser).where(TenantUser.id == member_id).values(is_active=True))
        decided = await document_service.transition(doc.id, decision_payload(pending, target))
        assert decided.status == target and decided.decision_actor_user_id == world.users[0].id
        assert decided.approved_by == (f"user:{world.users[0].id}" if target == "approved" else None)
        with pytest.raises(DocumentConflict):
            await document_service.transition(doc.id, decision_payload(pending, target))


async def test_concurrent_owner_revocation_serializes_before_decision(doc_world):
    from app.models import TenantUser

    world = doc_world
    doc = await create_doc(world)
    with scope(world):
        pending = await document_service.transition(doc.id, decision_payload(doc, "in_review"))
        member_id = world.memberships[0].id
        async with async_session_maker() as session:
            transaction = await session.begin()
            await session.execute(update(TenantUser).where(TenantUser.id == member_id).values(is_active=False))
            task = asyncio.create_task(document_service.transition(doc.id, decision_payload(pending, "approved")))
            try:
                # The revoker holds the membership row lock until commit.
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(asyncio.shield(task), timeout=0.3)
                await transaction.commit()
                with pytest.raises(PermissionError):
                    await asyncio.wait_for(task, timeout=10)
            finally:
                if transaction.is_active:
                    await transaction.rollback()
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                async with async_session_maker() as restore, restore.begin():
                    await restore.execute(update(TenantUser).where(TenantUser.id == member_id).values(is_active=True))
        current = await document_service.get(doc.id)
        assert current.status == "in_review" and current.decision_actor_user_id is None


async def test_document_and_template_crud_and_edit_invalidates_decision(doc_world):
    from app.services.knowledge.documents import DocumentConflict

    world = doc_world
    with scope(world):
        doc = await create_doc(world)
        assert (await document_service.get(doc.id)).body_text == "first"
        assert any(item.id == doc.id for item in (await document_service.list())["items"])
        pending = await document_service.transition(doc.id, decision_payload(doc, "in_review"))
        approved = await document_service.transition(doc.id, decision_payload(pending, "approved"))
        changed = await document_service.update(
            doc.id,
            {
                "expected_revision_id": approved.current_revision_id,
                "expected_updated_at": approved.updated_at,
                "body_text": "second",
                "change_description": "synthetic edit",
            },
        )
        assert changed.version == 2 and changed.status == "draft" and changed.approval_invalidated
        assert changed.approved_by is None and changed.decision_actor_user_id is None
        template = await document_service.create_template(
            {"name": "synthetic template", "document_type": "letter", "content": "{{name}}", "variables_schema": {}}
        )
        assert (await document_service.get_template(template.id)).content == "{{name}}"
        revised = await document_service.update_template(
            template.id,
            {
                "expected_updated_at": template.updated_at,
                "name": "synthetic revised",
                "document_type": "memo",
                "content": "changed",
                "variables_schema": {},
            },
        )
        with pytest.raises(DocumentConflict):
            await document_service.delete_template(template.id, {"expected_updated_at": template.updated_at})
        assert await document_service.delete_template(template.id, {"expected_updated_at": revised.updated_at}) == {
            "deleted": True
        }
        assert await document_service.delete(
            doc.id, {"expected_revision_id": changed.current_revision_id, "expected_updated_at": changed.updated_at}
        ) == {"deleted": True}
        with pytest.raises(DocumentUnavailable):
            await document_service.get(doc.id)


async def test_user_deletion_clears_document_references_without_removing_document(doc_world):
    from app.models import User

    world = doc_world
    user = world.users[1]
    with tenant_scope(world.tenants[0].id), permission_scope(user, member_grants=workspace_grants("owner")):
        doc = await document_service.create(
            DocumentCreate(title="synthetic", document_type="memo", body_text="retained", data_boundary="cloud")
        )
        pending = await document_service.transition(doc.id, decision_payload(doc, "in_review"))
        await document_service.transition(doc.id, decision_payload(pending, "rejected"))
    async with async_session_maker() as session, session.begin():
        await session.execute(delete(User).where(User.id == user.id))
    with scope(world):
        remaining = await document_service.get(doc.id)
        assert remaining.body_text == "retained" and remaining.status == "rejected"
        assert remaining.owner_user_id is None and remaining.author_user_id is None
        assert remaining.decision_actor_user_id is None


async def test_owner_demoted_to_manager_cannot_decide_with_stale_session(doc_world):
    from app.models import TenantUser

    world = doc_world
    doc = await create_doc(world)
    with scope(world):
        pending = await document_service.transition(doc.id, decision_payload(doc, "in_review"))
        member_id = world.memberships[0].id
        async with async_session_maker() as session, session.begin():
            await session.execute(
                update(TenantUser).where(TenantUser.id == member_id).values(role_id=world.roles["manager"].id)
            )
        try:
            with pytest.raises(PermissionError):
                await document_service.transition(doc.id, decision_payload(pending, "approved"))
        finally:
            async with async_session_maker() as session, session.begin():
                await session.execute(
                    update(TenantUser).where(TenantUser.id == member_id).values(role_id=world.roles["owner"].id)
                )
        assert (await document_service.get(doc.id)).status == "in_review"
