"""Local PostgreSQL handoff only. Requires 0142, exclusive test_schema and inventory."""

import asyncio
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, func, select

from app.core.access_catalog import workspace_grants
from app.core.database import async_session_maker
from app.core.permissions import permission_scope
from app.core.tenant_context import tenant_scope
from app.models import DocumentRevision, KnowledgeDocument
from app.services.knowledge.approvals import approval_service
from app.services.knowledge.documents import DocumentConflict, DocumentUnavailable, document_service
from app.services.knowledge.versions import version_service
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
async def version_world(personal_world):  # noqa: F811
    require_isolated_schema()
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


def decision(doc, target):
    return {
        **expected(doc),
        "expected_hash": doc.content_hash,
        "expected_status": doc.status,
        "target": target,
    }


async def decide_via_approval(world, doc, target):
    """P19-T04: decisions go through the revision-bound approval, never transition."""
    approval = await approval_service.request(
        doc.id,
        {
            "approver_user_id": world.users[0].id,
            "expected_revision_id": doc.current_revision_id,
            "expected_hash": doc.content_hash,
            "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
            "idempotency_key": f"p19-t03-{doc.id}-{target}",
        },
        boundary="local_only",
    )
    await approval_service.decide(doc.id, approval["id"], {"decision": target}, boundary="local_only")
    return await document_service.get(doc.id, boundary="local_only")


async def test_restore_preserves_approved_history_and_invalidates_current(version_world):
    with scoped(version_world):
        doc = await create("original")
        changed = await document_service.update(
            doc.id, {**expected(doc), "body_text": "approved body"}, boundary="local_only"
        )
        pending = await document_service.transition(doc.id, decision(changed, "in_review"), boundary="local_only")
        approved = await decide_via_approval(version_world, pending, "approved")
        restored = await version_service.restore(
            doc.id, doc.current_revision_id, expected(approved), boundary="local_only"
        )
        assert restored.version == 3 and restored.body_text == "original"
        assert restored.approval_invalidated and restored.approved_by is None
        async with async_session_maker() as session:
            revisions = (
                await session.scalars(
                    select(DocumentRevision)
                    .where(DocumentRevision.document_id == doc.id)
                    .order_by(DocumentRevision.version)
                )
            ).all()
            assert [(r.version, r.body_text) for r in revisions] == [
                (1, "original"),
                (2, "approved body"),
                (3, "original"),
            ]
            assert revisions[1].status == "approved" and revisions[1].approved_by is not None
            assert revisions[0].id == doc.current_revision_id and revisions[1].id == approved.current_revision_id


async def test_restore_racing_edit_has_one_winner(version_world):
    with scoped(version_world):
        doc = await create("first")
        second = await document_service.update(doc.id, {**expected(doc), "body_text": "second"}, boundary="local_only")
        results = await asyncio.wait_for(
            asyncio.gather(
                version_service.restore(doc.id, doc.current_revision_id, expected(second), boundary="local_only"),
                document_service.update(doc.id, {**expected(second), "body_text": "third"}, boundary="local_only"),
                return_exceptions=True,
            ),
            timeout=15,
        )
        assert sum(isinstance(r, DocumentConflict) for r in results) == 1
        assert sum(not isinstance(r, Exception) for r in results) == 1
        assert (await document_service.get(doc.id, boundary="local_only")).version == 3


async def test_membership_revocation_serializes_before_restore(version_world):
    from sqlalchemy import update

    from app.models import TenantUser

    with scoped(version_world):
        doc = await create("first")
        second = await document_service.update(doc.id, {**expected(doc), "body_text": "second"}, boundary="local_only")
        member_id = version_world.memberships[0].id
        async with async_session_maker() as session:
            transaction = await session.begin()
            await session.execute(update(TenantUser).where(TenantUser.id == member_id).values(is_active=False))
            task = asyncio.create_task(
                version_service.restore(doc.id, doc.current_revision_id, expected(second), boundary="local_only")
            )
            try:
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
        assert (await document_service.get(doc.id, boundary="local_only")).version == 2


async def test_boundary_denies_cloud_history_and_restoration(version_world):
    with scoped(version_world):
        doc = await document_service.create(
            {"title": "synthetic cloud", "document_type": "memo", "body_text": "original", "data_boundary": "cloud"}
        )
        with pytest.raises(DocumentUnavailable):
            await version_service.list(doc.id, boundary="cloud")
        with pytest.raises(DocumentUnavailable):
            await version_service.diff(doc.id, doc.current_revision_id, doc.current_revision_id, boundary="cloud")
        with pytest.raises(DocumentUnavailable):
            await version_service.restore(doc.id, doc.current_revision_id, expected(doc), boundary="local_only")
        assert (await document_service.get(doc.id, boundary="cloud")).version == 1


async def test_http_history_access_and_web_csrf_with_real_database(version_world):
    """Exercise real dependencies/services with injected identity, not production auth middleware."""
    from fastapi import FastAPI, Request
    from httpx import ASGITransport, AsyncClient

    from app.admin.csrf import CSRFTokenManager
    from app.api.v1.endpoints.documents import router as api_router
    from app.core.tenant_context import current_tenant_id
    from app.services.user.auth import get_authenticated_user
    from app.web.documents import router as web_router

    app = FastAPI()
    app.state.csrf_manager = CSRFTokenManager("synthetic-test-secret")
    app.include_router(api_router, prefix="/api/v1")
    app.include_router(web_router, prefix="/app")
    actor = version_world.users[0]

    async def identity():
        return actor

    app.dependency_overrides[get_authenticated_user] = identity

    @app.middleware("http")
    async def inject_request_state(request: Request, call_next):
        request.state.api_user = actor
        request.state.tenant_id = current_tenant_id()
        return await call_next(request)

    with scoped(version_world):
        doc = await create("first")
        second = await document_service.update(doc.id, {**expected(doc), "body_text": "second"}, boundary="local_only")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            url = f"/api/v1/documents/{doc.id}/versions"
            response = await client.get(url, params={"boundary": "local_only"})
            assert response.status_code == 200 and len(response.json()["items"]) == 2
            assert (await client.get(url, params={"boundary": "cloud"})).status_code == 404
            form = {
                "boundary": "local_only",
                "expected_revision_id": str(second.current_revision_id),
                "expected_updated_at": second.updated_at.isoformat(),
            }
            restore_url = f"/app/documents/manage/{doc.id}/restore/{doc.current_revision_id}"
            assert (await client.post(restore_url, data={**form, "_csrf": "invalid"})).status_code == 403
            assert (await document_service.get(doc.id, boundary="local_only")).version == 2
            response = await client.post(restore_url, data={**form, "_csrf": app.state.csrf_manager.generate_token()})
            assert response.status_code == 303
            assert (await document_service.get(doc.id, boundary="local_only")).version == 3
        with scoped(version_world, 1):
            # Keep the injected identity identical: the foreign owner is not this actor.
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get(url, params={"boundary": "local_only"})
                assert response.status_code in (403, 404)
                assert "first" not in response.text and "second" not in response.text
