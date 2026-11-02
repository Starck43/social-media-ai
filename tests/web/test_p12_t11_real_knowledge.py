"""Real PostgreSQL + assembled ASGI checks for P12-T11 (no paid providers)."""

import re
import secrets
from html.parser import HTMLParser
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import IntegrityError

from app.core.access_catalog import workspace_grants
from app.core.permissions import permission_scope
from app.core.tenant_context import tenant_scope
from app.main import create_application
from app.models import (
    DocumentRevision,
    KnowledgeDocument,
    KnowledgeSnippet,
    PlatformRole,
    Tenant,
    TenantUser,
    User,
    WorkspaceRole,
)
from app.services.knowledge.documents import compute_content_hash
from app.services.knowledge.retrieval import assemble_llm_citations_context, search_knowledge_snippets
from app.services.knowledge.workspace import resolve_context

pytestmark = pytest.mark.tenancy
PAGES = ("knowledge", "documents", "communications", "followups", "cases")
KINDS = {"knowledge": "knowledge", "communications": "communication", "followups": "followup", "cases": "case"}
CSRF = re.compile(r'name="_csrf" value="([^\"]+)"')


class DOM(HTMLParser):
    """Parse real rendered HTML without adding a new test dependency."""

    def __init__(self, html):
        super().__init__()
        self.nodes = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.nodes.append((tag, dict(attrs)))

    def select(self, selector):
        match = re.fullmatch(r'([a-z]*)\[([^=\]]+)(?:="([^"]*)")?\]', selector)
        if not match:
            return [attrs for tag, attrs in self.nodes if tag == selector]
        wanted_tag, key, value = match.groups()
        return [
            attrs
            for tag, attrs in self.nodes
            if (not wanted_tag or tag == wanted_tag) and key in attrs and (value is None or attrs[key] == value)
        ]

    def select_one(self, selector):
        return next(iter(self.select(selector)), None)


@pytest.fixture
async def actor(monkeypatch):
    tag = "p12t11" + secrets.token_hex(5)
    platform_role = await PlatformRole.objects.get(codename="user")
    user = await User.objects.create_user(
        tag, "test-password-long", email=f"{tag}@example.test", role_id=platform_role.id
    )
    tenant = await Tenant.objects.create(name=tag, slug=tag, plan="business")
    owner = await WorkspaceRole.objects.get(codename="owner")
    membership = await TenantUser.objects.create(
        tenant_id=tenant.id,
        user_id=user.id,
        channel="web",
        external_user_id=str(user.id),
        role_id=owner.id,
    )
    runtime = __import__("app.agent.runtime", fromlist=["web_session_id"])
    send = AsyncMock(return_value="Mock reply")
    monkeypatch.setattr(runtime, "web_session_id", AsyncMock(return_value=None))
    monkeypatch.setattr(runtime, "handle_web_message", send)
    app = create_application()
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", follow_redirects=False)
    try:
        login = await client.get("/app/login")
        token = CSRF.search(login.text).group(1)
        response = await client.post(
            "/app/login", data={"username": tag, "password": "test-password-long", "_csrf": token}
        )
        assert response.status_code in (302, 303), response.text
        yield SimpleNamespace(user=user, tenant=tenant, member=membership, client=client, send=send)
    finally:
        await client.aclose()
        await Tenant.objects.delete_by_id(tenant.id)
        await User.objects.delete_by_id(user.id)


async def seed(
    actor, *, boundary="cloud", scope="collection.source.view", kind="knowledge", title="Persisted evidence"
):
    with tenant_scope(actor.tenant.id):
        return await KnowledgeSnippet.objects.create(
            title=title,
            source_ref="file:///test/record.txt",
            citation_anchor="page 2",
            revision_hash="sha256:test-only",
            excerpt="Test-only stored excerpt",
            extraction_limit_note="Fixture, not production",
            data_boundary=boundary,
            acl_scope=scope,
            context_type=kind,
        )


async def document(actor, *, boundary="cloud", scope="collection.source.view"):
    with tenant_scope(actor.tenant.id), permission_scope(actor.user, member_grants=workspace_grants("owner")):
        doc = await KnowledgeDocument.objects.create(
            title="Persisted document",
            source_ref="file:///test/document.txt",
            citation_anchor="section 1",
            data_boundary=boundary,
            acl_scope=scope,
            counterparty="Test counterparty",
        )
        rev = await DocumentRevision.objects.append(doc.id, "Test-only revision body", author="test actor")
    return doc, rev


@pytest.mark.parametrize("page", PAGES)
async def test_empty_pages_through_asgi_have_no_fabricated_records(actor, page):
    response = await actor.client.get(f"/app/{page}")
    assert response.status_code == 200, response.text
    dom = DOM(response.text)
    assert dom.select_one("[data-evidence-empty]")
    assert not dom.select("[data-evidence-trigger]")
    assert dom.select_one('a[href="/app/settings?tab=connections"]')
    for invented in ("ДП-2026/114", "148 500", "CASE-2026-042", "dp_114_rev3.pdf", "sha256:9c4a18f2"):
        assert invented not in response.text


@pytest.mark.parametrize(
    "role,allowed", [("owner", True), ("admin", True), ("manager", True), ("analyst", True), ("viewer", False)]
)
async def test_acl_uses_real_membership_grants_and_denies_viewer(actor, role, allowed):
    restricted = await seed(actor, scope="collection.source.export", title="Restricted export evidence")
    public = await seed(actor, title="Workspace readable evidence")
    _, restricted_revision = await document(actor, scope="collection.source.export")
    workspace_role = await WorkspaceRole.objects.get(codename=role)
    await TenantUser.objects.update_by_id(actor.member.id, role_id=workspace_role.id)
    # Every one of these roles has source.view; only actual export grants open the restricted row.
    response = await actor.client.get("/app/knowledge")
    assert response.status_code == 200
    assert public.title in response.text
    assert (restricted.title in response.text) is allowed
    document_page = await actor.client.get("/app/documents")
    assert document_page.status_code == 200
    assert (restricted_revision.content_hash in document_page.text) is allowed
    with tenant_scope(actor.tenant.id), permission_scope(actor.user, member_grants=workspace_grants(role)):
        if allowed:
            assert (await resolve_context("knowledge", str(restricted.id), "cloud"))["title"] == restricted.title
        else:
            with pytest.raises(Exception) as error:
                await resolve_context("knowledge", str(restricted.id), "cloud")
            assert error.value.status_code == 404


async def test_unresolved_or_inactive_membership_cannot_read_evidence(actor):
    await seed(actor)
    await TenantUser.objects.update_by_id(actor.member.id, role_id=None)
    response = await actor.client.get("/app/knowledge")
    assert response.status_code == 403
    await TenantUser.objects.update_by_id(actor.member.id, is_active=False)
    response = await actor.client.get("/app/knowledge")
    assert response.status_code == 302
    assert response.headers["location"] == "/app/invite"


@pytest.mark.parametrize(
    "boundary,expected",
    [("cloud", {"cloud"}), ("company", {"cloud", "company"}), ("local_only", {"cloud", "company", "local_only"})],
)
async def test_boundaries_are_filtered_before_rendering_and_prompt_assembly(actor, boundary, expected):
    for value in ("local_only", "company", "cloud"):
        await seed(actor, boundary=value, title=f"Evidence {value}")
        await document(actor, boundary=value)
    for page in ("knowledge", "documents"):
        response = await actor.client.get(f"/app/{page}?boundary={boundary}")
        assert response.status_code == 200
        dom = DOM(response.text)
        assert {row["data-evidence-boundary"] for row in dom.select("[data-evidence-trigger]")} == expected
    prompt = assemble_llm_citations_context(
        [{"title": value, "excerpt": value, "data_boundary": value} for value in ("local_only", "company", "cloud")],
        boundary,
    )
    assert ("local_only" in prompt) is ("local_only" in expected)
    assert ("company" in prompt) is ("company" in expected)
    assert (await actor.client.get("/app/knowledge?boundary=invalid")).status_code == 422
    assert search_knowledge_snippets("anything") == []


@pytest.mark.parametrize("page", PAGES)
async def test_persisted_rows_expose_complete_dom_and_real_handoff(actor, page):
    if page == "documents":
        _, row = await document(actor)
        kind = "document"
    else:
        kind = KINDS[page]
        row = await seed(actor, kind=kind, title='Evidence <untrusted> "quoted"')
    response = await actor.client.get(f"/app/{page}")
    assert response.status_code == 200
    dom = DOM(response.text)
    trigger = dom.select_one("button[data-evidence-trigger]")
    assert trigger
    for field in ("title", "ref", "hash", "anchor", "boundary", "context-type", "context-id"):
        assert trigger.get(f"data-evidence-{field}")
    assert trigger["data-evidence-context-type"] == kind
    assert trigger["data-evidence-context-id"] == str(row.id)
    assert not dom.select("untrusted")
    url = dom.select_one("a[data-evidence-chat-link]")["href"]
    assert parse_qs(urlsplit(url).query) == {"context_type": [kind], "context_id": [str(row.id)], "boundary": ["cloud"]}
    handoff = await actor.client.get(
        url + "&context_title=FORGED&context_ref=FORGED", headers={"accept": "application/json"}
    )
    assert handoff.status_code == 200
    context = handoff.json()["scoped_context"]
    assert context["ref"] == trigger["data-evidence-ref"]
    assert context["title"] == trigger["data-evidence-title"]
    assert context["revision_hash"] == trigger["data-evidence-hash"]


async def test_cross_workspace_ids_are_unreadable_in_catalog_and_chat(actor):
    other = await Tenant.objects.create(name="other", slug="p12other" + secrets.token_hex(5), plan="business")
    try:
        stranger = SimpleNamespace(tenant=other, user=actor.user)
        row = await seed(stranger, title="Other tenant secret")
        _, revision = await document(stranger)
        response = await actor.client.get("/app/knowledge?boundary=local_only&q=Other")
        assert "Other tenant secret" not in response.text
        for kind, row_id in (("knowledge", row.id), ("document", revision.id)):
            url = f"/app/chat?context_type={kind}&context_id={row_id}&boundary=cloud"
            assert (await actor.client.get(url)).status_code == 404
            token = CSRF.search((await actor.client.get("/app/chat")).text).group(1)
            response = await actor.client.post(
                "/app/chat",
                data={"_csrf": token, "text": "test", "context_type": kind, "context_id": row_id, "boundary": "cloud"},
            )
            assert response.status_code == 404
        actor.send.assert_not_awaited()
    finally:
        await Tenant.objects.delete_by_id(other.id)


@pytest.mark.parametrize("boundary", ("local_only", "company"))
async def test_noncloud_handoff_never_calls_cloud_runtime_even_if_post_fields_are_stripped(actor, boundary):
    row = await seed(actor, boundary=boundary)
    page = await actor.client.get(f"/app/chat?context_type=knowledge&context_id={row.id}&boundary={boundary}")
    assert page.status_code == 200
    token = CSRF.search(page.text).group(1)
    for params in (
        {},
        {"context_type": "knowledge", "context_id": row.id, "boundary": "cloud"},
        {"context_type": "knowledge", "context_id": row.id, "boundary": boundary},
    ):
        response = await actor.client.post("/app/chat", data={"_csrf": token, "text": "test", **params})
        assert response.status_code == 403
    # Direct forged cloud handoff cannot load local/company evidence either.
    assert (
        await actor.client.get(f"/app/chat?context_type=knowledge&context_id={row.id}&boundary=cloud")
    ).status_code == 404
    actor.send.assert_not_awaited()


async def test_cloud_chat_uses_stored_context_and_rechecks_acl_on_post(actor):
    row = await seed(actor, scope="collection.source.export")
    url = f"/app/chat?context_type=knowledge&context_id={row.id}&boundary=cloud"
    page = await actor.client.get(url)
    token = CSRF.search(page.text).group(1)
    response = await actor.client.post(
        "/app/chat", data={"_csrf": token, "text": "Question"}, headers={"accept": "application/json"}
    )
    assert response.status_code == 200, response.text
    actor.send.assert_awaited_once()
    assert row.excerpt in actor.send.call_args.args[0]
    assert row.revision_hash in actor.send.call_args.args[0]
    viewer = await WorkspaceRole.objects.get(codename="viewer")
    await TenantUser.objects.update_by_id(actor.member.id, role_id=viewer.id, ai_chat_enabled=True)
    response = await actor.client.post("/app/chat", data={"_csrf": token, "text": "Second question"})
    assert response.status_code == 404
    assert actor.send.await_count == 1


async def test_document_revisions_persist_hash_and_invalidate_previous_approval(actor):
    doc, first = await document(actor)
    with tenant_scope(actor.tenant.id), permission_scope(actor.user, member_grants=workspace_grants("owner")):
        await DocumentRevision.objects.update_by_id(first.id, status="approved", approved_by="test approver")
        second = await DocumentRevision.objects.append(doc.id, "Updated test-only body")
        assert second.version == first.version + 1
        assert second.content_hash == compute_content_hash("Updated test-only body")
        assert second.status == "draft_invalidated" and second.approved_by is None
        old = await DocumentRevision.objects.get(id=first.id)
        assert old.body_text == first.body_text and old.status == "approved"
    response = await actor.client.get("/app/documents")
    assert second.content_hash in response.text
    assert first.content_hash not in response.text


async def test_revision_content_is_immutable_and_hash_checked_by_postgres(actor):
    doc, revision = await document(actor)
    with tenant_scope(actor.tenant.id):
        with pytest.raises(IntegrityError):
            await DocumentRevision.objects.update_by_id(
                revision.id,
                body_text="Silently rewritten",
                content_hash=compute_content_hash("Silently rewritten"),
            )
        with pytest.raises(IntegrityError):
            await DocumentRevision.objects.create(
                document_id=doc.id,
                version=2,
                body_text="Wrong digest",
                content_hash="sha256:wrong",
            )
        saved = await DocumentRevision.objects.get(id=revision.id)
        assert saved.body_text == revision.body_text
        assert saved.content_hash == compute_content_hash(saved.body_text)


async def test_concurrent_revision_appends_have_distinct_serial_versions(actor):
    import asyncio

    doc, first = await document(actor)
    with tenant_scope(actor.tenant.id), permission_scope(actor.user, member_grants=workspace_grants("owner")):
        revisions = await asyncio.gather(
            DocumentRevision.objects.append(doc.id, "Concurrent body A"),
            DocumentRevision.objects.append(doc.id, "Concurrent body B"),
        )
        assert {r.version for r in revisions} == {first.version + 1, first.version + 2}
        assert all(r.status == "draft_invalidated" and r.approved_by is None for r in revisions)


async def test_revision_foreign_key_cannot_bind_another_workspace_document(actor):
    doc, _ = await document(actor)
    other = await Tenant.objects.create(name="other", slug="p12other" + secrets.token_hex(5), plan="business")
    try:
        with tenant_scope(other.id):
            with pytest.raises(IntegrityError):
                await DocumentRevision.objects.create(
                    document_id=doc.id,
                    version=99,
                    body_text="Cross-workspace body",
                    content_hash=compute_content_hash("Cross-workspace body"),
                )
    finally:
        await Tenant.objects.delete_by_id(other.id)


async def test_transcript_refresh_cannot_clear_noncloud_guard(actor):
    row = await seed(actor, boundary="local_only")
    page = await actor.client.get(f"/app/chat?context_type=knowledge&context_id={row.id}&boundary=local_only")
    token = CSRF.search(page.text).group(1)
    response = await actor.client.get("/app/chat", headers={"accept": "application/json"})
    assert response.status_code == 200
    response = await actor.client.post("/app/chat", data={"_csrf": token, "text": "No context fields"})
    assert response.status_code == 403
    actor.send.assert_not_awaited()
