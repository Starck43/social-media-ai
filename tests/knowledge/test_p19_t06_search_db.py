"""LOCAL PostgreSQL behavioural checks for metadata-only document search (P19-T06).

Requires the validated ``test_schema`` at the current head revision with
exclusive writer ownership. Synthetic rows belong to ``personal_world`` only and
are removed, then verified absent, in the ``doc_world`` teardown. Search runs
through ``DocumentSearchService`` with a real session, so actor re-reads, ACL and
boundary gates, revision joins and ``has_permission`` all hit the database.
"""

from contextlib import ExitStack

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import delete, func, select, update

from app.core.access_catalog import workspace_grants
from app.core.config import settings
from app.core.database import async_session_maker
from app.core.permissions import permission_scope
from app.core.tenant_context import tenant_scope
from app.models import Contact, DocumentRevision, KnowledgeDocument, KnowledgeSnippet
from app.models.knowledge import DocumentTemplate
from app.services.knowledge.document_contracts import DocumentCreate
from app.services.knowledge.documents import compute_content_hash, document_service
from app.services.knowledge.search import document_search_service
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


async def seed_contact(world, workspace=0):
    async with async_session_maker() as session, session.begin():
        contact = Contact(tenant_id=world.tenants[workspace].id, display_name_encrypted="synthetic contact")
        session.add(contact)
        await session.flush()
        return contact.id


async def create_doc(world, *, workspace=0, **extra):
    payload = {"title": "synthetic document", "document_type": "letter", "body_text": "first", "data_boundary": "cloud"}
    payload.update(extra)
    with scope(world, workspace):
        return await document_service.create(DocumentCreate(**payload))


async def search(world, *, workspace=0, role="owner", **kwargs):
    """Search as the workspace owner or as a member granted the given role preset."""
    with (
        tenant_scope(world.tenants[workspace].id),
        permission_scope(world.users[0 if workspace == 0 else 2], member_grants=workspace_grants(role)),
    ):
        async with async_session_maker() as session:
            return await document_search_service.search(session=session, **kwargs)


async def titles(world, *, role="owner", **kwargs):
    return sorted(item.title for item in (await search(world, role=role, **kwargs))["items"])


async def test_foreign_workspace_and_foreign_tenant_documents_are_absent(doc_world):
    world = doc_world
    mine = await create_doc(world, title="synthetic mine")
    other_workspace = await create_doc(world, title="synthetic other-workspace", workspace=1)

    found = await search(world, q="synthetic")
    assert [item.id for item in found["items"]] == [mine.id]
    assert other_workspace.id not in {item.id for item in found["items"]}

    # Actor of the first user inside the second workspace sees only its document.
    found = await search(world, q="synthetic", workspace=1)
    assert [item.id for item in found["items"]] == [other_workspace.id]
    assert await titles(world, q="mine", workspace=1) == []


@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        ("local_only", {"local_only", "company", "cloud"}),
        ("company", {"company", "cloud"}),
        ("cloud", {"cloud"}),
    ],
)
async def test_boundary_request_is_capped_by_document_boundary(doc_world, requested, expected):
    world = doc_world
    for boundary in ("local_only", "company", "cloud"):
        await create_doc(world, title=f"synthetic {boundary}", data_boundary=boundary)

    found = await titles(world, q="synthetic", boundary=requested)

    assert found == sorted(f"synthetic {name}" for name in expected)


async def test_unknown_boundary_is_rejected_not_widened(doc_world):
    world = doc_world
    await create_doc(world, title="synthetic cloud")

    with pytest.raises(ValueError, match="invalid_search"):
        await search(world, boundary="everything")


def revision(world, doc, *, version, author, body="second", status="draft_invalidated"):
    return DocumentRevision(
        tenant_id=world.tenants[0].id,
        document_id=doc.id,
        version=version,
        body_text=body,
        content_hash=compute_content_hash(body),
        status=status,
        author=author,
        author_user_id=world.users[0].id,
        approved_by=None,
        unsupported_terms_note="",
    )


async def test_search_matches_current_revision_only(doc_world):
    world = doc_world
    doc = await create_doc(world, title="synthetic contract", body_text="old body")

    async def point_at(*, version, author):
        async with async_session_maker() as session, session.begin():
            row = revision(world, doc, version=version, author=author, body="old body")
            session.add(row)
            await session.flush()
            await session.execute(
                update(KnowledgeDocument).where(KnowledgeDocument.id == doc.id).values(current_revision_id=row.id)
            )

    # v2 is current and matches its own author.
    await point_at(version=2, author="Shared author")
    assert await titles(world, author="Shared author") == ["synthetic contract"]
    assert await titles(world, q="Shared author") == ["synthetic contract"]

    # Superseding v2 with a v3 removes the v2 author from the search surface,
    # even though the v2 row still exists.
    await point_at(version=3, author="Later author")
    assert await titles(world, author="Shared author") == []
    assert await titles(world, q="Shared author") == []
    assert await titles(world, author="Later author") == ["synthetic contract"]


async def test_contact_filter_fails_closed_without_contact_view(doc_world):
    world = doc_world
    contact_id = await seed_contact(world)
    doc = await create_doc(world, title="synthetic linked", contact_id=contact_id)

    # The owner preset carries contact.VIEW, so the filter resolves a real match.
    owner_page = await search(world, contact_id=contact_id)
    assert [item.id for item in owner_page["items"]] == [doc.id]
    assert [item.contact_id for item in owner_page["items"]] == [contact_id]

    # The viewer preset has no contact.VIEW: the filter returns an empty page
    # without disclosing whether a hidden link exists.
    assert await search(world, role="viewer", contact_id=contact_id) == {
        "items": [],
        "limit": 50,
        "next_before_id": None,
    }
    # The document itself stays visible to the viewer, but its contact is redacted.
    viewer_page = await search(world, role="viewer", q="synthetic linked")
    assert [item.id for item in viewer_page["items"]] == [doc.id]
    assert [item.contact_id for item in viewer_page["items"]] == [None]


async def test_content_search_is_reported_unavailable_and_never_matches_body(doc_world):
    world = doc_world
    await create_doc(world, title="synthetic header", body_text="confidential needle")

    # The API rejects an explicit content request instead of silently downgrading it.
    response = await content_search_response()
    assert response.status_code == 422
    assert response.json()["code"] == "content_search_unavailable"
    assert response.headers["X-Request-ID"] == response.json()["request_id"]

    # A free-text query never reaches the body: only metadata matches.
    assert await titles(world, q="confidential") == []
    assert await titles(world, q="needle") == []
    assert await titles(world, q="synthetic header") == ["synthetic header"]


async def test_cursor_pagination_loses_and_duplicates_nothing(doc_world):
    world = doc_world
    created = [await create_doc(world, title=f"synthetic page {index}") for index in range(5)]
    expected = {doc.id for doc in created}

    seen, cursor, pages = [], None, 0
    while True:
        page = await search(world, q="synthetic page", limit=2, before_id=cursor)
        pages += 1
        seen.extend(item.id for item in page["items"])
        if page["next_before_id"] is None:
            break
        cursor = page["next_before_id"]

    assert pages == 3
    assert len(seen) == len(set(seen)) == 5
    assert set(seen) == expected
    # Newest first, strictly descending across page boundaries.
    assert seen == sorted(seen, reverse=True)


async def content_search_response():
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from app.api.v1.endpoints import documents as api

    app = FastAPI()
    app.include_router(api.router, prefix="/api/v1")

    async def allow():
        return None

    for route in api.router.routes:
        if not hasattr(route, "dependant"):
            continue
        for dependency in route.dependant.dependencies:
            app.dependency_overrides[dependency.call] = allow
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get("/api/v1/documents", params={"content_search": "true", "q": "needle"})
