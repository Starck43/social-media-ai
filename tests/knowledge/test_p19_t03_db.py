"""Local PostgreSQL handoff only. Requires 0142, exclusive test_schema and inventory."""

import asyncio
from contextlib import ExitStack

import pytest
from sqlalchemy import delete, func, select

from app.core.access_catalog import workspace_grants
from app.core.config import settings
from app.core.database import async_session_maker
from app.core.permissions import permission_scope
from app.core.tenant_context import tenant_scope
from app.models import DocumentRevision, KnowledgeDocument
from app.services.knowledge.documents import DocumentConflict, DocumentUnavailable, document_service
from app.services.knowledge.versions import version_service
from tests.shared.fixtures.personal_workspace import personal_world  # noqa: F401

pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]


def scoped(world, workspace=0):
    stack = ExitStack()
    stack.enter_context(tenant_scope(world.tenants[workspace].id))
    stack.enter_context(
        permission_scope(world.users[0 if workspace == 0 else 2], member_grants=workspace_grants("owner"))
    )
    return stack


@pytest.fixture
async def version_world(personal_world):  # noqa: F811
    assert settings.DB_SCHEMA == "test_schema"
    ids = [tenant.id for tenant in personal_world.tenants]
    try:
        yield personal_world
    finally:
        async with async_session_maker() as session, session.begin():
            await session.execute(delete(KnowledgeDocument).where(KnowledgeDocument.tenant_id.in_(ids)))
        async with async_session_maker() as session:
            for model in (KnowledgeDocument, DocumentRevision):
                assert (
                    await session.scalar(select(func.count()).select_from(model).where(model.tenant_id.in_(ids))) == 0
                )


def expected(doc):
    return {"expected_revision_id": doc.current_revision_id, "expected_updated_at": doc.updated_at}


async def create(body):
    return await document_service.create(
        {"title": "synthetic", "document_type": "memo", "body_text": body, "data_boundary": "local_only"}
    )


async def test_restore_appends_and_scopes_history(version_world):
    with scoped(version_world):
        doc = await create("first")
        second = await document_service.update(doc.id, {**expected(doc), "body_text": "second"}, boundary="local_only")
        other = await create("other")
        with pytest.raises(DocumentUnavailable):
            await version_service.diff(
                doc.id, doc.current_revision_id, other.current_revision_id, boundary="local_only"
            )
        diff = await version_service.diff(
            doc.id, doc.current_revision_id, second.current_revision_id, boundary="local_only"
        )
        assert "".join(p.before for p in diff.parts) == "first" and "".join(p.after for p in diff.parts) == "second"
        restored = await version_service.restore(
            doc.id, doc.current_revision_id, expected(second), boundary="local_only"
        )
        assert restored.version == 3 and restored.body_text == "first" and restored.approval_invalidated
        assert restored.current_revision_id != doc.current_revision_id
        history = await version_service.list(doc.id, boundary="local_only")
        assert [r.version for r in history["items"]] == [3, 2, 1]
    with scoped(version_world, 1):
        with pytest.raises(DocumentUnavailable):
            await version_service.list(doc.id, boundary="local_only")


async def test_concurrent_restore_only_one_wins(version_world):
    with scoped(version_world):
        doc = await create("first")
        second = await document_service.update(doc.id, {**expected(doc), "body_text": "second"}, boundary="local_only")
        results = await asyncio.wait_for(
            asyncio.gather(
                version_service.restore(doc.id, doc.current_revision_id, expected(second), boundary="local_only"),
                version_service.restore(doc.id, doc.current_revision_id, expected(second), boundary="local_only"),
                return_exceptions=True,
            ),
            timeout=15,
        )
        assert sum(isinstance(item, DocumentConflict) for item in results) == 1
        assert sum(not isinstance(item, Exception) for item in results) == 1
        assert (await document_service.get(doc.id, boundary="local_only")).version == 3
