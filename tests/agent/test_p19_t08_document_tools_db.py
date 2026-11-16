"""P19-T08: the chat surface against an isolated test schema with real access checks.

Happy path plus a refusal per read tool: a caller without document access in
the workspace gets the service's own `permission_denied`, and a caller with the
code-level right in a foreign workspace gets `document_unavailable` — the same
refusals `/api/v1/documents` maps to 403/404. The restore preview never
creates a revision.
"""

from __future__ import annotations

from contextlib import ExitStack

import pytest
from sqlalchemy import delete, func, select

from app.core.access_catalog import workspace_grants
from app.core.config import settings
from app.core.database import async_session_maker
from app.core.permissions import permission_scope
from app.core.tenant_context import tenant_scope
from app.models import DocumentRevision, KnowledgeDocument
from app.services.knowledge.documents import document_service
from tests.shared.fixtures.personal_workspace import personal_world  # noqa: F401

pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]


def scoped(world, workspace=0, user=None, grants=None):
    """Workspace + caller rights in the shape the chat runtime opens them."""
    stack = ExitStack()
    stack.enter_context(tenant_scope(world.tenants[workspace].id))
    stack.enter_context(
        permission_scope(
            user or world.users[0 if workspace == 0 else 2],
            member_grants=workspace_grants("owner") if grants is None else frozenset(grants),
        )
    )
    return stack


def expected(doc):
    return {"expected_revision_id": doc.current_revision_id, "expected_updated_at": doc.updated_at}


async def create(body):
    return await document_service.create(
        {"title": "synthetic", "document_type": "memo", "body_text": body, "data_boundary": "local_only"}
    )


async def revision_count(document_id, tenant_id):
    async with async_session_maker() as session:
        return await session.scalar(
            select(func.count())
            .select_from(DocumentRevision)
            .where(DocumentRevision.document_id == document_id, DocumentRevision.tenant_id == tenant_id)
        )


@pytest.fixture
async def document_world(personal_world):  # noqa: F811
    assert settings.DB_SCHEMA and settings.DB_SCHEMA != "public"
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


async def test_document_tools_are_refused_to_a_caller_without_the_member_grant(document_world):
    """The shared right between chat and API: `collection.source.view` family.

    One service call for the API would be `/api/v1/documents/{id}/versions`
    (403/404). A chat member without the document grants on that workspace is
    refused by the registry gate before the handler runs, and by the service's
    own workspace check when the registration only carries the code key.
    """
    from app.agent.tools import execute
    from app.core.permissions import has_permission, has_permission_by_codename

    with scoped(document_world):
        doc = await create("synthetic body")
        revision_id = doc.current_revision_id

    member = document_world.users[0]
    calls = (
        ("documents_history", {"document_id": doc.id}),
        ("documents_diff", {"document_id": doc.id, "from_revision_id": revision_id, "to_revision_id": revision_id}),
        ("documents_restore_preview", {"document_id": doc.id, "revision_id": revision_id}),
    )

    with scoped(document_world, user=member, grants=frozenset({("analysis.result", "view")})):
        assert not has_permission(member, "source", "view")
        assert not has_permission_by_codename(member, "collection.source.view")
        for name, arguments in calls:
            assert '"permission_denied"' in await execute(name, arguments), name

    # Same right as the endpoint (`source.view`), same answer: the service's own
    # check refuses a member whose workspace role grants no document read.
    with scoped(document_world, user=member, grants=frozenset({("source", "create")})):
        # The code key without `view`: the registry gate does not fire, so the
        # service's own `source.view` check is the one that refuses.
        assert not has_permission(member, "source", "view")
        for name, arguments in calls:
            assert '"permission_denied"' in await execute(name, arguments), name

    other = document_world.users[2]
    with scoped(document_world, workspace=1, user=other, grants=workspace_grants("owner")):
        assert has_permission_by_codename(other, "collection.source.view")
        for name, arguments in calls:
            assert '"document_unavailable"' in await execute(name, arguments), name
