"""Real test-schema projections for the new P12-T18 admin views."""

from contextlib import contextmanager
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlencode

import pytest
from sqlalchemy import select, update
from starlette.exceptions import HTTPException
from starlette.requests import Request

from app.admin import connector_registry as connector_module
from app.admin import file_catalog as file_module
from app.admin import knowledge_registry as knowledge_module
from app.core.access_catalog import workspace_grants
from app.core.database import async_session_maker
from app.core.permissions import permission_scope
from app.core.tenant_context import tenant_scope
from app.models import (
    DocumentRevision,
    FileCatalogObject,
    KnowledgeDocument,
    KnowledgeSnippet,
    PersonalConnectorAuthorization,
    PlatformRole,
    User,
    WorkspaceConnection,
    WorkspaceConnectionSecret,
)
from app.utils.crypto import encrypt_secret
from tests.shared.file_catalog_fixtures import actor as catalog_actor
from tests.shared.file_catalog_fixtures import admit, data

pytestmark = pytest.mark.tenancy


class CapturingTemplates:
    async def TemplateResponse(self, request, template, context):
        self.template = template
        self.context = context
        return SimpleNamespace(headers={})


def request_for(actor, path, *, tenant_id, params=None):
    query = urlencode(params or {}).encode()
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [],
            "query_string": query,
            "session": {"app_tenant_id": str(tenant_id)},
            "state": {"admin_user": actor},
        }
    )


def knowledge_view():
    return SimpleNamespace(
        is_accessible=lambda request: knowledge_module.KnowledgeRegistryAdmin.is_accessible(None, request),
        _read=knowledge_module.KnowledgeRegistryAdmin._read,
        templates=CapturingTemplates(),
        name=knowledge_module.KnowledgeRegistryAdmin.name,
    )


def file_view():
    return SimpleNamespace(
        is_accessible=lambda request: file_module.FileCatalogAdmin.is_accessible(None, request),
        templates=CapturingTemplates(),
        name=file_module.FileCatalogAdmin.name,
    )


def connector_view():
    return SimpleNamespace(
        is_accessible=lambda request: connector_module.ConnectorRegistryAdmin.is_accessible(None, request),
        templates=CapturingTemplates(),
        name=connector_module.ConnectorRegistryAdmin.name,
    )


@contextmanager
def manager_actor(data, *, user=0, tenant=0, connection=0):
    """Use the shared fixture's real tenant/actor scope and its manager preset."""
    with catalog_actor(data, user=user, tenant=tenant, connection=connection):
        with permission_scope(data.users[user], member_grants=workspace_grants("manager")):
            yield


async def promote_fixture_actors(data):
    async with async_session_maker() as session, session.begin():
        operator_role = await session.scalar(select(PlatformRole).where(PlatformRole.codename == "superuser"))
        user_role = await session.scalar(select(PlatformRole).where(PlatformRole.codename == "user"))
        assert operator_role is not None and user_role is not None
        await session.execute(
            update(User).where(User.id.in_([user.id for user in data.users])).values(role_id=operator_role.id)
        )
    for user in data.users:
        user.role_id = operator_role.id
        user.role = operator_role
        user._model_perms_cache = None
    return user_role


async def restore_fixture_user_role(data, *, user_index, role):
    user = data.users[user_index]
    async with async_session_maker() as session, session.begin():
        await session.execute(update(User).where(User.id == user.id).values(role_id=role.id))
    user.role_id = role.id
    user.role = role
    user._model_perms_cache = None


async def add_knowledge_rows(data):
    revision_bodies = (
        "PRIVATE-REVISION-CONTENT-MUST-NOT-RENDER",
        "FOREIGN-TENANT-REVISION-CONTENT",
    )
    async with async_session_maker() as session, session.begin():
        snippets = [
            KnowledgeSnippet(
                tenant_id=data.tenants[0].id,
                title="VISIBLE-SNIPPET",
                source_ref="PRIVATE-SNIPPET-SOURCE",
                citation_anchor="PRIVATE-SNIPPET-ANCHOR",
                data_boundary="cloud",
                acl_scope="collection.source.view",
                context_type="knowledge",
                revision_hash="metadata-only-current",
                excerpt="PRIVATE-SNIPPET-CONTENT-MUST-NOT-RENDER",
                extraction_limit_note="PRIVATE-SNIPPET-NOTE",
            ),
            KnowledgeSnippet(
                tenant_id=data.tenants[1].id,
                title="FOREIGN-TENANT-SNIPPET",
                source_ref="FOREIGN-SNIPPET-SOURCE",
                citation_anchor="FOREIGN-SNIPPET-ANCHOR",
                data_boundary="cloud",
                acl_scope="collection.source.view",
                context_type="knowledge",
                revision_hash="metadata-only-foreign",
                excerpt="FOREIGN-TENANT-CONTENT",
                extraction_limit_note="",
            ),
        ]
        documents = [
            KnowledgeDocument(
                tenant_id=data.tenants[0].id,
                title="VISIBLE-DOCUMENT",
                source_ref="PRIVATE-DOCUMENT-SOURCE",
                citation_anchor="PRIVATE-DOCUMENT-ANCHOR",
                data_boundary="cloud",
                acl_scope="collection.source.view",
                counterparty="Visible counterparty",
            ),
            KnowledgeDocument(
                tenant_id=data.tenants[1].id,
                title="FOREIGN-TENANT-DOCUMENT",
                source_ref="FOREIGN-DOCUMENT-SOURCE",
                citation_anchor="FOREIGN-DOCUMENT-ANCHOR",
                data_boundary="cloud",
                acl_scope="collection.source.view",
                counterparty="Foreign counterparty",
            ),
        ]
        session.add_all(snippets + documents)
        await session.flush()
        revisions = [
            DocumentRevision(
                tenant_id=data.tenants[index].id,
                document_id=document.id,
                version=1,
                content_hash="sha256:" + sha256(revision_bodies[index].encode()).hexdigest(),
                body_text=revision_bodies[index],
                status="draft",
                author="PRIVATE-REVISION-AUTHOR",
                approved_by=None,
                unsupported_terms_note="PRIVATE-REVISION-NOTE",
            )
            for index, document in enumerate(documents)
        ]
        session.add_all(revisions)


async def seed_file_catalog_and_connector_markers(data):
    labels = ("CURRENT-MOUNT", "FOREIGN-OWNER-MOUNT", "FOREIGN-TENANT-MOUNT")
    roots = (
        "disk:/PRIVATE-CONFIG-PATH-CURRENT",
        "disk:/PRIVATE-CONFIG-PATH-FOREIGN-OWNER",
        "disk:/PRIVATE-CONFIG-PATH-FOREIGN-TENANT",
    )
    async with async_session_maker() as session, session.begin():
        for index, connection_id in enumerate(data.connections):
            connection = await session.get(WorkspaceConnection, connection_id)
            assert connection is not None
            connection.label = labels[index]
            connection.account_key = f"PRIVATE-ACCOUNT-LOGIN-{index}"
            connection.config = {"file_policy": {"version": 1, "root": roots[index], "restrictions": []}}

        personal_rows = [
            PersonalConnectorAuthorization(
                owner_user_id=data.users[index].id,
                provider_id="telegram",
                auth_method="mtproto",
                subject_key=f"PRIVATE-PERSONAL-LOGIN-{index}",
                state="active",
                revision=1,
                execution_generation=0,
                secret_encrypted=encrypt_secret(f"PRIVATE-PERSONAL-CREDENTIAL-{index}"),
            )
            for index in range(2)
        ]
        session.add_all(personal_rows)
        await session.flush()
        personal_ids = [row.id for row in personal_rows]
        connection_secrets = (
            await session.scalars(
                select(WorkspaceConnectionSecret.secret_encrypted).where(
                    WorkspaceConnectionSecret.connection_id == data.connections[0]
                )
            )
        ).all()
        personal_secret_ciphertexts = (
            await session.scalars(
                select(PersonalConnectorAuthorization.secret_encrypted).where(
                    PersonalConnectorAuthorization.owner_user_id == data.users[0].id
                )
            )
        ).all()
        return {
            "labels": labels,
            "roots": roots,
            "personal_ids": personal_ids,
            "connection_secret_ciphertexts": connection_secrets,
            "personal_secret_ciphertexts": personal_secret_ciphertexts,
        }


@pytest.mark.asyncio
async def test_admin_projections_use_real_tenant_managers_and_hide_private_fields(data, monkeypatch):
    normal_role = await promote_fixture_actors(data)
    markers = await seed_file_catalog_and_connector_markers(data)
    await add_knowledge_rows(data)
    current_tenant_id = data.tenants[0].id
    foreign_tenant_connection_id = data.connections[2]
    private_values = (
        "PRIVATE-SNIPPET-SOURCE",
        "PRIVATE-SNIPPET-ANCHOR",
        "PRIVATE-SNIPPET-CONTENT-MUST-NOT-RENDER",
        "PRIVATE-SNIPPET-NOTE",
        "PRIVATE-DOCUMENT-SOURCE",
        "PRIVATE-DOCUMENT-ANCHOR",
        "PRIVATE-REVISION-CONTENT-MUST-NOT-RENDER",
        "PRIVATE-REVISION-AUTHOR",
        "PRIVATE-REVISION-NOTE",
        "PRIVATE-PERSONAL-LOGIN-0",
        "PRIVATE-PERSONAL-LOGIN-1",
        "PRIVATE-PERSONAL-CREDENTIAL-0",
        "PRIVATE-PERSONAL-CREDENTIAL-1",
        "PRIVATE-ACCOUNT-LOGIN-0",
        "PRIVATE-ACCOUNT-LOGIN-1",
        "p12t12-synthetic-token",
        *markers["connection_secret_ciphertexts"],
        *markers["personal_secret_ciphertexts"],
        *markers["roots"],
    )

    # Seed catalog metadata through the same guarded manager used by the UI.
    for user_index, tenant_index, connection_index, name in (
        (0, 0, 0, "VISIBLE-FILE-METADATA"),
        (1, 0, 1, "FOREIGN-OWNER-FILE-METADATA"),
        (0, 1, 2, "FOREIGN-TENANT-FILE-METADATA"),
    ):
        with catalog_actor(data, user=user_index, tenant=tenant_index, connection=connection_index) as args:
            await admit(args, name, folder=markers["roots"][connection_index])

    actor = data.users[0]
    with manager_actor(data, tenant=0):
        for kind, visible_title, foreign_title in (
            ("snippets", "VISIBLE-SNIPPET", "FOREIGN-TENANT-SNIPPET"),
            ("documents", "VISIBLE-DOCUMENT", "FOREIGN-TENANT-DOCUMENT"),
        ):
            view = knowledge_view()
            response = await knowledge_module.KnowledgeRegistryAdmin.knowledge_registry(
                view,
                request_for(actor, "/admin/knowledge-registry", tenant_id=current_tenant_id, params={"kind": kind}),
            )
            rendered = repr(view.templates.context)
            assert visible_title in rendered and foreign_title not in rendered
            assert not any(value and value in rendered for value in private_values)
            assert response.headers["Cache-Control"] == "no-store"

        revision_view = knowledge_view()
        await knowledge_module.KnowledgeRegistryAdmin.knowledge_registry(
            revision_view,
            request_for(
                actor,
                "/admin/knowledge-registry",
                tenant_id=current_tenant_id,
                params={"kind": "revisions"},
            ),
        )
        revisions_rendered = repr(revision_view.templates.context)
        assert len(revision_view.templates.context["rows"]) == 1
        assert "FOREIGN-TENANT-REVISION-CONTENT" not in revisions_rendered
        assert not any(value and value in revisions_rendered for value in private_values)

        monkeypatch.setattr(file_module.settings, "FILE_CATALOG_UI_ENABLED", True)
        file_listing = file_view()
        response = await file_module.FileCatalogAdmin.file_catalog(
            file_listing,
            request_for(actor, "/admin/file-catalog", tenant_id=current_tenant_id),
        )
        listing_rendered = repr(file_listing.templates.context)
        assert file_listing.templates.context["mounts"] == [{"id": data.connections[0], "label": markers["labels"][0]}]
        assert any(row["name"] == "VISIBLE-FILE-METADATA" for row in file_listing.templates.context["files"])
        assert "FOREIGN-OWNER-FILE-METADATA" not in listing_rendered
        assert "FOREIGN-TENANT-FILE-METADATA" not in listing_rendered
        assert markers["labels"][1] not in listing_rendered and markers["labels"][2] not in listing_rendered
        assert not any(value and value in listing_rendered for value in private_values)
        assert response.headers["X-Robots-Tag"] == "noindex, nofollow"

        async with async_session_maker() as session:
            object_id = await session.scalar(
                select(FileCatalogObject.id)
                .where(FileCatalogObject.connection_id == data.connections[0])
                .order_by(FileCatalogObject.id)
                .limit(1)
            )
        assert object_id is not None
        file_detail = file_view()
        await file_module.FileCatalogAdmin.file_catalog(
            file_detail,
            request_for(
                actor,
                "/admin/file-catalog",
                tenant_id=current_tenant_id,
                params={"connection_id": str(data.connections[0]), "object_id": str(object_id)},
            ),
        )
        detail_rendered = repr(file_detail.templates.context)
        assert file_detail.templates.context["detail"]["name"] == "VISIBLE-FILE-METADATA"
        assert not any(value and value in detail_rendered for value in private_values)

        # The file catalog requires strict file_policy-only configuration. Add the
        # private login marker only after file metadata has been projected.
        async with async_session_maker() as session, session.begin():
            connection = await session.get(WorkspaceConnection, data.connections[0])
            assert connection is not None
            connection.config = {
                **connection.config,
                "account_login": "PRIVATE-ACCOUNT-LOGIN-0",
            }

        connector_listing = connector_view()
        response = await connector_module.ConnectorRegistryAdmin.personal_connectors(
            connector_listing,
            request_for(actor, "/admin/personal-connectors", tenant_id=current_tenant_id),
        )
        connector_rendered = repr(connector_listing.templates.context)
        assert [row["id"] for row in connector_listing.templates.context["accounts"]] == [markers["personal_ids"][0]]
        assert [row["id"] for row in connector_listing.templates.context["connections"]] == [data.connections[0]]
        assert markers["labels"][1] not in connector_rendered and markers["labels"][2] not in connector_rendered
        assert not any(value and value in connector_rendered for value in private_values)
        assert response.headers["Cache-Control"] == "no-store"

    # A foreign connection is not selectable, and no list/detail query may follow it.
    file_view_with_stale_connection = file_view()
    list_page = AsyncMock()
    detail = AsyncMock()
    monkeypatch.setattr(file_module.catalog, "list_page", list_page)
    monkeypatch.setattr(file_module.catalog, "detail", detail)
    with manager_actor(data, tenant=0), pytest.raises(HTTPException) as exc:
        await file_module.FileCatalogAdmin.file_catalog(
            file_view_with_stale_connection,
            request_for(
                actor,
                "/admin/file-catalog",
                tenant_id=current_tenant_id,
                params={"connection_id": str(foreign_tenant_connection_id)},
            ),
        )
    assert exc.value.status_code == 404
    list_page.assert_not_awaited()
    detail.assert_not_awaited()

    # A stale superuser-selected workspace is refused before evidence managers read.
    stale_view = knowledge_view()
    read = AsyncMock()
    stale_view._read = read
    with tenant_scope(bypass=True), permission_scope(actor), pytest.raises(HTTPException) as exc:
        await knowledge_module.KnowledgeRegistryAdmin.knowledge_registry(
            stale_view,
            request_for(actor, "/admin/knowledge-registry", tenant_id=2_147_483_647),
        )
    assert exc.value.status_code == 403
    read.assert_not_awaited()

    # A workspace member without platform-console rights is denied before reads.
    await restore_fixture_user_role(data, user_index=1, role=normal_role)
    denied_actor = data.users[1]
    denied_knowledge = knowledge_view()
    denied_knowledge._read = AsyncMock()
    with catalog_actor(data, user=1, tenant=0), pytest.raises(HTTPException) as exc:
        await knowledge_module.KnowledgeRegistryAdmin.knowledge_registry(
            denied_knowledge,
            request_for(denied_actor, "/admin/knowledge-registry", tenant_id=current_tenant_id),
        )
    assert exc.value.status_code == 403
    denied_knowledge._read.assert_not_awaited()

    # Feature-flag denial is also before catalog reads, even for a full admin.
    denied_file = file_view()
    connections = AsyncMock()
    monkeypatch.setattr(file_module.catalog, "connections", connections)
    monkeypatch.setattr(file_module.settings, "FILE_CATALOG_UI_ENABLED", False)
    with catalog_actor(data, tenant=0), pytest.raises(HTTPException) as exc:
        await file_module.FileCatalogAdmin.file_catalog(
            denied_file,
            request_for(actor, "/admin/file-catalog", tenant_id=current_tenant_id),
        )
    assert exc.value.status_code == 404
    connections.assert_not_awaited()

    denied_connector = connector_view()
    list_personal = AsyncMock()
    monkeypatch.setattr(connector_module.connector_authorizations, "list_personal", list_personal)
    with catalog_actor(data, user=1, tenant=0), pytest.raises(HTTPException) as exc:
        await connector_module.ConnectorRegistryAdmin.personal_connectors(
            denied_connector,
            request_for(denied_actor, "/admin/personal-connectors", tenant_id=current_tenant_id),
        )
    assert exc.value.status_code == 403
    list_personal.assert_not_awaited()
