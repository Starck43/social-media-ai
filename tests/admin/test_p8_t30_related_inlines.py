"""Real SQLAdmin/Jinja + synthetic ORM and in-memory SQL-evaluating storage double.

No application import/startup, PostgreSQL connection, schema, root DB fixture,
provider, or secret. SQLite :memory: evaluates the actual generated predicates;
it is not a claim of PostgreSQL or owner-local integration acceptance.
"""

import sqlite3
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import httpx
import pytest
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader
from source_import_isolation import load_isolated_source
from sqladmin import ModelView
from sqlalchemy import JSON, Boolean, Column, ForeignKey, ForeignKeyConstraint, Integer, String, and_, select
from sqlalchemy.dialects import sqlite
from sqlalchemy.orm import DeclarativeBase, relationship, selectinload
from sqlalchemy.schema import CreateTable
from starlette.applications import Starlette
from starlette.requests import Request

from app.types import ActionType

ROOT = Path(__file__).resolve().parents[2]


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String)


class Platform(Base):
    __tablename__ = "platforms"
    id = Column(Integer, primary_key=True)
    name = Column(String)


class Tenant(Base):
    __tablename__ = "tenants"
    id = Column(Integer, primary_key=True)
    is_active = Column(Boolean)


class WorkspaceRole(Base):
    __tablename__ = "workspace_roles"
    id = Column(Integer, primary_key=True)
    codename = Column(String)


class TenantUser(Base):
    __tablename__ = "tenant_users"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(ForeignKey(Tenant.id))
    user_id = Column(ForeignKey(User.id))
    role_id = Column(ForeignKey(WorkspaceRole.id))
    channel = Column(String)
    is_active = Column(Boolean)


class Source(Base):
    __tablename__ = "sources"
    id = Column(Integer, primary_key=True)
    platform_id = Column(ForeignKey(Platform.id))
    tenant_id = Column(ForeignKey(Tenant.id))
    name = Column(String)
    secret_encrypted = Column(String)
    params = Column(JSON)
    platform = relationship(Platform)

    def __str__(self):
        raise AssertionError("Inline must never call row __str__")


class AgentTask(Base):
    __tablename__ = "agent_tasks"
    id = Column(Integer, primary_key=True)
    source_id = Column(ForeignKey(Source.id))
    tenant_id = Column(ForeignKey(Tenant.id))
    name = Column(String)


class LLMProvider(Base):
    __tablename__ = "llm_providers"
    __personal_scoped__ = True
    id = Column(Integer, primary_key=True)
    owner_user_id = Column(ForeignKey(User.id))
    name = Column(String)


class LLMModel(Base):
    __tablename__ = "llm_models"
    __personal_scoped__ = True
    __table_args__ = (
        ForeignKeyConstraint(["provider_id", "owner_user_id"], [LLMProvider.id, LLMProvider.owner_user_id]),
    )
    id = Column(Integer, primary_key=True)
    provider_id = Column(Integer)
    owner_user_id = Column(ForeignKey(User.id))
    name = Column(String)


class UserCredential(Base):
    __tablename__ = "user_credentials"
    id = Column(Integer, primary_key=True)
    user_id = Column(ForeignKey(User.id))
    label = Column(String)
    secret_encrypted = Column(String)


class Unknown(Base):
    __tablename__ = "unknown_records"
    id = Column(Integer, primary_key=True)
    platform_id = Column(ForeignKey(Platform.id))
    label = Column(String)


INLINE = load_isolated_source(
    "_p8_t30_inlines",
    ROOT / "app/admin/inlines.py",
    {"app.models": NS(Tenant=Tenant, TenantUser=TenantUser, WorkspaceRole=WorkspaceRole)},
)
RELATED = load_isolated_source("_p8_t30_related", ROOT / "app/admin/related.py")
AUTH = load_isolated_source(
    "_p8_t30_authorization",
    ROOT / "app/admin/authorization.py",
    {"app.models": NS(User=User)},
)


class PlatformView(ModelView, model=Platform):
    column_list = ["id", "name"]


class UserView(ModelView, model=User):
    column_list = ["id", "username"]


class SourceView(ModelView, model=Source):
    column_list = ["id", "name", "secret_encrypted", "params", "platform"]
    column_labels = {"name": "Источник"}
    column_details_exclude_list = ["secret_encrypted"]

    def list_query(self, request):
        # Pin that custom view predicates survive scalar projection; eager
        # loaders must not trigger a second, unscoped relationship query.
        return select(Source).where(Source.name != "view-hidden").options(selectinload(Source.platform))


class TaskView(ModelView, model=AgentTask):
    column_list = ["id", "name"]


class ProviderView(ModelView, model=LLMProvider):
    column_list = ["id", "name"]


class ModelViewForLLM(ModelView, model=LLMModel):
    column_list = ["id", "name"]


class CredentialView(ModelView, model=UserCredential):
    column_list = ["id", "label", "secret_encrypted"]


class UnknownView(ModelView, model=Unknown):
    column_list = ["id", "label"]


def actor(role="operator", rights=None, user_id=1, active=True):
    rights = rights if rights is not None else {"platform": {ActionType.VIEW}, "source": {ActionType.VIEW}}
    return NS(
        id=user_id,
        is_active=active,
        is_superuser=role == "superuser",
        has_admin_access=lambda: active and role in {"operator", "admin", "superuser"},
        model_permissions=lambda name: rights.get(name, set()),
    )


class Store:
    """No network/engine: evaluate compiled SQL solely over fabricated tables."""

    def __init__(self):
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.statements = []
        for table in Base.metadata.sorted_tables:
            self.db.execute(str(CreateTable(table).compile(dialect=sqlite.dialect())))

    def add(self, model, **values):
        columns = ", ".join(values)
        self.db.execute(
            f"INSERT INTO {model.__tablename__} ({columns}) VALUES ({', '.join('?' for _ in values)})",
            list(values.values()),
        )

    def run(self, statement):
        self.statements.append(statement)
        sql = str(statement.compile(dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True}))
        return NS(mappings=lambda: self.db.execute(sql).fetchall())

    def session(self, **kwargs):
        store = self

        class Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def execute(self, statement):
                return store.run(statement)

        return Session()


@pytest.fixture
def harness():
    store = Store()
    app = Starlette()
    backend = AUTH.AdminAuthorizationBackend()
    admin = RELATED.RelatedAdmin(app, authorization_backend=backend, templates_dir=str(ROOT / "app/templates"))
    views = {}
    for cls in (
        PlatformView,
        UserView,
        SourceView,
        TaskView,
        ProviderView,
        ModelViewForLLM,
        CredentialView,
        UnknownView,
    ):
        admin.add_view(cls)
        view = admin._views[-1]
        view.is_async = True
        view.session_maker = store.session
        views[view.model] = view
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "path": "/admin/platform/details/1",
            "headers": [],
            "query_string": b"",
            "app": app,
            "router": admin.admin.router,
        }
    )
    state = NS(store=store, app=app, admin=admin, backend=backend, views=views, request=request)
    store.add(Platform, id=1, name="VK")
    store.add(User, id=1, username="own")
    store.add(User, id=2, username="other")
    for index, role in enumerate(("owner", "admin", "manager", "analyst", "viewer", "invalid"), 1):
        store.add(WorkspaceRole, id=index, codename=role)
    for tenant_id, role_id, active, channel, tenant_active in (
        (10, 1, True, "web", True),
        (20, 5, True, "web", True),
        (30, 5, False, "web", True),
        (40, None, True, "web", True),
        (50, 6, True, "web", True),
        (60, 5, True, "telegram", True),
        (70, 5, True, "web", False),
        (80, 5, True, "web", True),
    ):
        store.add(Tenant, id=tenant_id, is_active=tenant_active)
        store.add(
            TenantUser,
            id=tenant_id,
            tenant_id=tenant_id,
            user_id=2 if tenant_id == 80 else 1,
            role_id=role_id,
            channel=channel,
            is_active=active,
        )
        store.add(
            Source,
            id=tenant_id,
            tenant_id=tenant_id,
            platform_id=1,
            name=f"source-{tenant_id}",
            secret_encrypted="MUST-NOT-RENDER",
        )
    store.add(Source, id=99, tenant_id=10, platform_id=1, name="view-hidden")
    yield state
    store.db.close()


async def grant(h, user):
    h.request.state.admin_user = user
    await h.backend.load(h.request)


async def sections(h, model=Platform, obj=None):
    return await INLINE.related_inlines(h.views[model], obj or Platform(id=1), h.request)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["operator", "admin"])
async def test_platform_grants_do_not_widen_own_and_shared_workspace_scope(harness, role):
    await grant(harness, actor(role))
    result = await sections(harness)
    assert len(result) == 1
    assert [row[0] for row in result[0]["rows"]] == ["10", "20"]
    assert result[0]["columns"] == ["id", "Источник"]
    assert "MUST-NOT-RENDER" not in str(result)
    assert len(harness.store.statements) == 3  # parent + scoped count + page; no N+1
    parent, count, page = harness.store.statements
    assert parent._limit_clause.value == 1
    assert count._limit_clause is None
    assert page._limit_clause.value == INLINE.INLINE_LIMIT
    assert result[0]["pagination"].count == 2


@pytest.mark.asyncio
async def test_platform_owner_retains_global_workspace_inspection_not_view_hidden(harness):
    await grant(harness, actor("superuser"))
    result = await sections(harness)
    assert [row[0] for row in result[0]["rows"]] == [str(i) for i in range(10, 81, 10)]


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["user", "workspace-owner", "workspace-admin"])
async def test_workspace_membership_never_grants_console_entry(harness, role):
    await grant(harness, actor(role))
    assert await sections(harness) == []
    assert harness.store.statements == []


@pytest.mark.asyncio
@pytest.mark.parametrize("who", [None, actor(active=False), actor(user_id=0), actor(user_id="1")])
async def test_unresolved_inactive_and_invalid_actor_fail_closed(harness, who):
    if who is not None:
        await grant(harness, who)
    assert await sections(harness) == []
    assert harness.store.statements == []


@pytest.mark.asyncio
@pytest.mark.parametrize("rights", [{}, {"source": {ActionType.VIEW}}, {"platform": {ActionType.VIEW}}])
async def test_parent_and_child_grants_both_required(harness, rights):
    await grant(harness, actor(rights=rights))
    assert await sections(harness) == []
    assert len(harness.store.statements) <= 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,task_visible",
    [("owner", True), ("admin", True), ("manager", True), ("analyst", False), ("viewer", False), ("invalid", False)],
)
async def test_shared_workspace_role_must_allow_target_model_view(harness, role, task_visible):
    role_id = {"owner": 1, "admin": 2, "manager": 3, "analyst": 4, "viewer": 5, "invalid": 6}[role]
    harness.store.db.execute("UPDATE tenant_users SET role_id=? WHERE tenant_id=20", (role_id,))
    harness.store.add(AgentTask, id=1, source_id=20, tenant_id=20, name="Shared task")
    await grant(harness, actor(rights={"source": {ActionType.VIEW}, "agenttask": {ActionType.VIEW}}))
    result = await sections(harness, Source, Source(id=20, tenant_id=20))
    assert bool(result and result[0]["rows"]) is task_visible


@pytest.mark.asyncio
async def test_foreign_parent_is_not_a_bridge_to_actor_rows(harness):
    harness.store.add(AgentTask, id=1, source_id=80, tenant_id=10, name="Malformed cross-workspace link")
    await grant(harness, actor(rights={"source": {ActionType.VIEW}, "agenttask": {ActionType.VIEW}}))
    assert await sections(harness, Source, Source(id=80, tenant_id=80)) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["operator", "admin", "superuser"])
async def test_personal_catalog_and_vault_never_leak_even_for_platform_owner(harness, role):
    for uid in (1, 2):
        harness.store.add(LLMProvider, id=uid, owner_user_id=uid, name=f"provider-{uid}")
        harness.store.add(LLMModel, id=uid, provider_id=uid, owner_user_id=uid, name=f"model-{uid}")
        harness.store.add(UserCredential, id=uid, user_id=uid, label=f"vault-{uid}", secret_encrypted="SENSITIVE")
    await grant(
        harness,
        actor(role, rights={name: {ActionType.VIEW} for name in ("user", "llmprovider", "llmmodel", "usercredential")}),
    )
    own = await sections(harness, User, User(id=1))
    assert "provider-1" in str(own) and "vault-1" in str(own)
    foreign = await sections(harness, User, User(id=2))
    assert all(section["rows"] == [] for section in foreign)
    assert "SENSITIVE" not in str(own + foreign)
    assert await sections(harness, LLMProvider, LLMProvider(id=2, owner_user_id=2)) == []
    models = await sections(harness, LLMProvider, LLMProvider(id=1, owner_user_id=1))
    assert models[0]["rows"] == [["1", "model-1"]]
    assert models[0]["foreign_key"] == "provider_id, owner_user_id"


@pytest.mark.asyncio
async def test_unknown_resource_is_not_implicitly_global(harness):
    harness.store.add(Unknown, id=1, platform_id=1, label="UNREGISTERED-RESOURCE")
    await grant(harness, actor("superuser"))
    result = await sections(harness)
    assert "UNREGISTERED-RESOURCE" not in str(result)


@pytest.mark.asyncio
async def test_high_volume_collections_are_not_listed_inline(harness):
    """The workspace form must not repeat the newest collected rows.

    `collected_items` is the largest table and its rows are long texts and
    media, so the section only duplicated the collection pages and made the
    edit form heavy. Exclusion is configured on the target view, not the parent, so the section
    is skipped for every console reader, the platform owner included, while a
    regular child of the same parent stays visible.
    """

    class TenantView(ModelView, model=Tenant):
        column_list = ["id", "is_active"]

    class CollectedItem(Base):
        # The harness store materialised Base.metadata before this test, so the
        # fabricated high-volume table is declared and then created here.
        __tablename__ = "collected_items"
        id = Column(Integer, primary_key=True)
        tenant_id = Column(ForeignKey(Tenant.__tablename__ + ".id"))
        text = Column(String)

    class CollectedItemView(ModelView, model=CollectedItem):
        inline_exclude = True
        column_list = ["id", "text"]

    admin = harness.views[Source]._admin_ref
    for cls in (TenantView, CollectedItemView):
        admin.add_view(cls)
        view = admin._views[-1]
        view.is_async = True
        view.session_maker = harness.store.session
        harness.views[view.model] = view
    harness.store.db.execute(str(CreateTable(CollectedItem.__table__).compile(dialect=sqlite.dialect())))
    harness.store.add(Tenant, id=1, is_active=True)
    harness.store.add(CollectedItem, id=1, tenant_id=1, text="HEAVY-COLLECTED-ROW")

    await grant(harness, actor("superuser"))
    result = await INLINE.related_inlines(harness.views[Tenant], Tenant(id=1), harness.request)

    assert "HEAVY-COLLECTED-ROW" not in str(result)
    assert all("collected_items" not in section["foreign_key"] for section in result)
    # The exclusion is targeted: the ordinary child of the same workspace still
    # renders, so the guard is not silently switching the footer off.
    assert any(section["foreign_key"] == "tenant_id" for section in result)


@pytest.mark.asyncio
async def test_limit_after_scope_and_no_unfiltered_total_or_write_controls(harness):
    for index in range(100, 140):
        harness.store.add(Source, id=index, tenant_id=10, platform_id=1, name=f"own-{index}")
    await grant(harness, actor())
    result = await sections(harness)
    assert len(result[0]["rows"]) == INLINE.INLINE_LIMIT
    assert result[0]["pagination"].has_next
    assert result[0]["pagination"].count == 42
    assert set(result[0]) == {
        "id",
        "title",
        "foreign_key",
        "relation_label",
        "column_names",
        "columns",
        "rows",
        # P8-T32 navigation contract: one permission-gated link per row plus the
        # scalar columns that may carry it. No new query, mutation or endpoint.
        "row_links",
        "link_columns",
        "pagination",
        "sizes",
    }


@pytest.mark.asyncio
async def test_raw_values_are_escaped_and_relationships_not_rendered(harness):
    harness.store.db.execute("UPDATE sources SET name=? WHERE id=10", ("<img src=x onerror=alert(1)>",))
    await grant(harness, actor())
    env = Environment(loader=FileSystemLoader(ROOT / "app/templates"), autoescape=True, enable_async=True)
    env.globals["related_inlines"] = INLINE.related_inlines
    html = await env.get_template("sqladmin/related_inlines.html").render_async(
        model_view=harness.views[Platform],
        inline_object=Platform(id=1),
        request=harness.request,
    )
    assert "&lt;img" in html and "<img" not in html
    assert "MUST-NOT-RENDER" not in html
    assert "Нет доступных записей" not in html
    assert "<form" not in html and 'type="submit"' not in html
    assert "/create" not in html and "/edit" not in html and "/delete" not in html


@pytest.mark.asyncio
async def test_can_view_and_custom_parent_checker_are_enforced(harness):
    await grant(harness, actor())
    harness.views[Source].can_view_details = False
    assert await sections(harness) == []
    harness.views[Source].can_view_details = True
    harness.views[Platform].has_permission = lambda *args: False
    assert await sections(harness) == []


@pytest.mark.asyncio
async def test_unsaved_parent_is_not_queried(harness):
    await grant(harness, actor())
    assert await sections(harness, Platform, Platform()) == []
    assert harness.store.statements == []


def test_multiple_and_compound_foreign_keys_are_not_collapsed():
    provider = LLMProvider(id=7, owner_user_id=1)
    found = list(INLINE.reverse_foreign_keys(provider, LLMModel))
    assert len(found) == 1
    expression = str(found[0][1].compile(compile_kwargs={"literal_binds": True}))
    assert "provider_id = 7" in expression and "owner_user_id = 1" in expression
    found = list(INLINE.reverse_foreign_keys(User(id=1), LLMModel))
    assert len(found) == 1 and found[0][0] == "owner_user_id"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/admin/platform/details/1", "/admin/platform/edit/1"])
async def test_actual_sqladmin_routes_render_footer_after_custom_object_content(harness, path):
    h = harness
    view = h.views[Platform]
    view.get_object_for_details = AsyncMock(return_value=Platform(id=1, name="VK"))
    view.get_object_for_edit = AsyncMock(return_value=Platform(id=1, name="VK"))
    from wtforms import Form, StringField

    class MinimalForm(Form):
        name = StringField()

    view.scaffold_form = AsyncMock(return_value=MinimalForm)
    h.admin.templates.env.globals["related_inlines"] = INLINE.related_inlines
    # Keep the real shared layout and inline partial. Only unrelated base shell
    # assets/menu/session are replaced; no handler or inline policy is stubbed.
    loader = DictLoader(
        {
            "sqladmin/base.html": "{% block body %}{% endblock %}",
            "sqladmin/_macros.html": "{% macro display_menu(menu, request) %}{% endmacro %}",
            "custom-object.html": '{% extends "sqladmin/layout.html" %}{% block main %}' "{% endblock %}",
            "object-content.html": '{% extends "sqladmin/layout.html" %}'
            "{% block content %}<div>OBJECT-CONTENT</div>{% endblock %}",
            # Global route lookup needs a static route normally mounted by setup.
        }
    )
    h.admin.templates.env.loader = ChoiceLoader([loader, h.admin.templates.env.loader])
    view.details_template = view.edit_template = "object-content.html"
    user = actor(rights={"platform": {ActionType.VIEW, ActionType.UPDATE}, "source": {ActionType.VIEW}})
    user.username = "operator"
    user.role = NS(label="Operator")

    async def identity(request, call_next):
        request.state.admin_user = user
        request.scope["session"] = {}
        return await call_next(request)

    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import Response

    h.app.add_middleware(BaseHTTPMiddleware, dispatch=identity)

    from starlette.routing import Route

    h.app.router.routes.append(Route("/static/{path:path}", lambda request: Response(""), name="static"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(h.app), base_url="http://offline.test") as client:
        response = await client.get(path)
    assert response.status_code == 200
    assert response.text.index("OBJECT-CONTENT") < response.text.index("data-admin-related-inlines")
    assert "source-10" in response.text and "source-20" in response.text
    assert "source-80" not in response.text


def test_template_hook_covers_custom_templates_without_duplication():
    layout = (ROOT / "app/templates/sqladmin/layout.html").read_text()
    assert layout.count("include 'sqladmin/related_inlines.html'") == 1
    assert "('details', 'edit')" in layout
    for template in ("source_details.html", "ai_analytics_detail.html", "llm_edit.html", "base_form.html"):
        assert 'extends "sqladmin/layout.html"' in (ROOT / "app/templates/sqladmin" / template).read_text()


@pytest.mark.asyncio
async def test_membership_revocation_is_fresh_on_each_read(harness):
    await grant(harness, actor())
    before = await sections(harness)
    assert [row[0] for row in before[0]["rows"]] == ["10", "20"]
    harness.store.db.execute("UPDATE tenant_users SET is_active=0 WHERE tenant_id=20")
    after = await sections(harness)
    assert [row[0] for row in after[0]["rows"]] == ["10"]


@pytest.mark.asyncio
async def test_sync_sqladmin_session_path_uses_same_scoped_projection(harness):
    from contextlib import contextmanager

    @contextmanager
    def session(**kwargs):
        yield NS(execute=harness.store.run)

    for view in harness.views.values():
        view.is_async = False
        view.session_maker = session
    await grant(harness, actor())
    result = await sections(harness)
    assert [row[0] for row in result[0]["rows"]] == ["10", "20"]


@pytest.mark.asyncio
async def test_empty_scoped_list_does_not_claim_global_emptiness(harness):
    harness.store.db.execute("UPDATE tenant_users SET is_active=0")
    await grant(harness, actor())
    result = await sections(harness)
    assert result[0]["rows"] == []
    env = Environment(loader=FileSystemLoader(ROOT / "app/templates"), autoescape=True, enable_async=True)
    env.globals["related_inlines"] = INLINE.related_inlines
    html = await env.get_template("sqladmin/related_inlines.html").render_async(
        model_view=harness.views[Platform],
        inline_object=Platform(id=1),
        request=harness.request,
    )
    assert "Нет доступных записей" in html
    assert "source-80" not in html


def test_multiple_independent_foreign_keys_to_one_parent_are_separate():
    class DualLink(Base):
        __tablename__ = "dual_link"
        id = Column(Integer, primary_key=True)
        sender_id = Column(ForeignKey(User.id))
        receiver_id = Column(ForeignKey(User.id))

    constraints = list(INLINE.reverse_foreign_keys(User(id=1), DualLink))
    assert [name for name, _ in constraints] == ["receiver_id", "sender_id"]


def test_cell_formatters_never_call_arbitrary_object_string_or_emit_unbounded_values():
    from datetime import date, datetime

    class Unsafe:
        def __str__(self):
            raise AssertionError("must not stringify arbitrary objects")

    assert INLINE.cell_text(Unsafe()) == "—"
    assert INLINE.cell_text(None) == "—"
    assert INLINE.cell_text(True) == "Да"
    assert INLINE.cell_text(date(2026, 10, 25)) == "25.10.2026"
    assert INLINE.cell_text(datetime(2026, 10, 25, 20, 6)) == "25.10.2026 20:06"
    assert len(INLINE.cell_text("x" * 1000)) == INLINE.CELL_LIMIT + 1


def test_canonical_registered_views_compile_without_database_connections(monkeypatch):
    """Real project metadata/managers, no startup/connection/storage execution."""
    import socket

    from fastapi import FastAPI
    from sqlalchemy import true
    from sqlalchemy.dialects.postgresql import dialect

    def refuse(*args, **kwargs):
        raise AssertionError("metadata validation must never connect")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    from app.admin.inlines import projection_columns, reverse_foreign_keys, scoped_projection
    from app.admin.setup import setup_admin
    from app.core.tenant_context import tenant_scope

    app = FastAPI()
    admin = setup_admin(app)  # registers routes only; no lifespan/runtime
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/admin/source/details/1",
            "headers": [],
            "app": app,
            "session": {},
            "path_params": {"pk": "1"},
        }
    )
    request.state.admin_user = actor()
    compiled = []
    fk_pairs = 0
    with tenant_scope(bypass=True):  # match the existing PlatformScopeMiddleware
        for view in admin._views:
            if not getattr(view, "model", None):
                continue
            names = projection_columns(view)
            assert names, view.identity
            scoped_projection(view, request, request.state.admin_user, names, true()).compile(dialect=dialect())
            compiled.append(view.identity)
            obj = view.model()
            for column in view.pk_columns:
                setattr(obj, column.key, 1)
            if hasattr(obj, "owner_user_id"):
                obj.owner_user_id = 1
            for target in admin._views:
                if getattr(target, "model", None):
                    fk_pairs += len(list(reverse_foreign_keys(obj, target.model)))
    assert len(compiled) == 16
    assert fk_pairs == 28
    assert admin.templates.env.globals["related_inlines"].__name__ == "related_inlines"
