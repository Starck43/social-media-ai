"""Owner-local personal catalog acceptance: real auth, ASGI, ORM and PostgreSQL.

Requires an already migrated/validated test target through normal root conftest.
No schema repair, migrations, seeds, runtime lifespan, LLM calls or paid evidence.
Fixtures create and remove only uniquely named users/catalog rows/workspaces.
Auth requests never run inside fixture permission/tenant bypass scopes.
Never execute or collect remotely, or with --noconftest.
"""

from __future__ import annotations

import re
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

# isort: split
# STOP before application imports, including collection without root conftest.
_expected_conftest = Path(__file__).resolve().parents[1] / "conftest.py"
_conftest = next(
    (module for module in tuple(sys.modules.values()) if getattr(module, "__file__", None) == str(_expected_conftest)),
    None,
)
if _conftest is None or getattr(_conftest, "TEST_SCHEMA", None) != "test_schema":
    raise RuntimeError("STOP: normal root conftest/test_schema redirect required; never use --noconftest")
_target = urlsplit(getattr(_conftest, "TEST_DATABASE_URL", ""))
if (
    _target.hostname not in ("localhost", "127.0.0.1")
    or (_target.port or 5432) != 5432
    or _target.path != "/social_manager"
):
    raise RuntimeError("STOP: only the approved local social_manager/test_schema target is allowed")
# isort: split

from app.core.permissions import operator_permission_scope, permission_scope
from app.core.tenant_context import tenant_scope
from app.main import create_application
from app.models import LLMModel, LLMProvider, PlatformRole, User
from app.models.managers.tenant_manager import TenantUserManager, tenants
from app.services.ai.personal_catalog import save_provider_bundle

pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]
PAGE = "/app/settings?tab=models"
SAVE = "/app/settings/models/save"
DEFAULT = "/app/settings/models/default"
CSRF = re.compile(r'name="_csrf" value="([^"]+)"')


@contextmanager
def fixture_scope():
    """Trusted setup/cleanup only; NEVER enclose a client request."""
    with tenant_scope(bypass=True), operator_permission_scope():
        yield


class ModelRows(HTMLParser):
    """Associate the rendered default/selected markers with their model names."""

    def __init__(self, html):
        super().__init__()
        self.rows = []
        self.current = None
        self.depth = 0
        self.strong = False
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = attrs.get("class", "").split()
        if self.current is None and tag == "div" and "llm-model-entry" in classes:
            self.current = {"name": "", "default": False, "selected": False}
            self.depth = 1
            return
        if self.current is None:
            return
        if tag == "div":
            self.depth += 1
        if tag == "strong":
            self.strong = True
        if "llm-default" in classes:
            self.current["default"] = True

    def handle_data(self, data):
        if self.current is not None:
            if self.strong:
                self.current["name"] += data
            if data.strip() == "Выбрана для чата":
                self.current["selected"] = True

    def handle_endtag(self, tag):
        if self.current is None:
            return
        if tag == "strong":
            self.strong = False
        if tag == "div":
            self.depth -= 1
            if self.depth == 0:
                self.rows.append(self.current)
                self.current = None


@dataclass
class CatalogCase:
    app: object
    marker: str = field(default_factory=lambda: "catalog-ui-" + uuid4().hex[:16])
    password: str = field(default_factory=lambda: "Synthetic-Q7!" + uuid4().hex, repr=False)
    users: list = field(default_factory=list)
    clients: dict = field(default_factory=dict)
    workspace_names: set = field(default_factory=set)

    async def add_user(self):
        with fixture_scope():
            role = await PlatformRole.objects.get(codename="user")
            assert role is not None, "BLOCKED: existing user role required; do not seed"
            username = f"{self.marker}-{len(self.users)}"
            user = await User.objects.create_user(
                username=username,
                email=username + "@fixture.invalid",
                password=self.password,
                role_id=role.id,
                is_active=True,
            )
        self.users.append(user)
        client = AsyncClient(
            transport=ASGITransport(app=self.app), base_url="http://testserver", follow_redirects=False
        )
        self.clients[user.id] = client
        response = await client.get("/app/login")
        token = self.token(response)
        response = await client.post(
            "/app/login", data={"username": username, "password": self.password, "_csrf": token}
        )
        assert response.status_code == 302
        assert not response.headers["location"].startswith("/app/login")
        assert not await TenantUserManager().web_memberships(user.id)
        return user

    @staticmethod
    def token(response):
        assert response.status_code == 200
        match = CSRF.search(response.text)
        assert match, "Expected CSRF field from the actual rendered page"
        return match.group(1)

    async def token_for(self, user, page=PAGE):
        return self.token(await self.clients[user.id].get(page))

    async def save(self, user, **changes):
        payload = {
            "_csrf": await self.token_for(user),
            "provider_name": "Provider " + self.marker,
            "provider_uid": "fixture",
            "base_url": "https://provider.fixture.invalid/v1",
            "model_db_id": ["", ""],
            "model_name": ["First " + self.marker, "Second " + self.marker],
            "model_api_id": ["api-first", "api-second"],
            **changes,
        }
        response = await self.clients[user.id].post(SAVE, data=payload)
        assert response.status_code == 303
        assert response.headers["location"] == PAGE
        return response

    async def catalog(self, user):
        """Owner-scoped persisted readback; never a request authorization stub."""
        with permission_scope(user):
            providers = await LLMProvider.objects.filter(owner_user_id=user.id).order_by(LLMProvider.id)
            models = await LLMModel.objects.filter(owner_user_id=user.id).order_by(LLMModel.id)
        return providers, models

    async def close(self):
        for client in self.clients.values():
            await client.aclose()
        with fixture_scope():
            for user in self.users:
                memberships = await TenantUserManager().web_memberships(user.id)
                for membership in memberships:
                    workspace = await tenants.get(id=membership.tenant_id)
                    assert workspace is not None and workspace.name in self.workspace_names
                    await tenants.update_by_id(workspace.id, agent_model_id=None)
                    await tenants.delete_by_id(workspace.id)
                models = await LLMModel.objects.filter(owner_user_id=user.id)
                for model in models:
                    await LLMModel.objects.delete_by_id(model.id)
                providers = await LLMProvider.objects.filter(owner_user_id=user.id)
                for provider in providers:
                    await LLMProvider.objects.delete_by_id(provider.id)
                assert user.username.startswith(self.marker)
                await User.objects.delete_user(user.id)


@pytest.fixture
async def catalog_case():
    case = CatalogCase(app=create_application())
    try:
        await case.add_user()
        await case.add_user()
        yield case
    finally:
        await case.close()


async def test_memberless_regular_user_saves_multiple_models_and_never_projects_key(catalog_case):
    case = catalog_case
    owner, other = case.users
    page = await case.clients[owner.id].get(PAGE)
    assert page.status_code == 200
    assert "Модели ИИ" in page.text and "Моделей пока нет" in page.text
    key = "synthetic-key-" + case.marker
    await case.save(owner, api_key=key)
    providers, models = await case.catalog(owner)
    assert len(providers) == 1 and len(models) == 2
    assert providers[0].owner_user_id == owner.id and providers[0].uid == "fixture"
    assert providers[0].encrypted_api_key and providers[0].encrypted_api_key != key
    assert {model.model_id for model in models} == {"api-first", "api-second"}
    assert all(model.owner_user_id == owner.id and model.provider_id == providers[0].id for model in models)
    assert await case.catalog(other) == ([], [])
    page = await case.clients[owner.id].get(PAGE)
    assert key not in page.text and providers[0].encrypted_api_key not in page.text
    assert "Заменить API-ключ" in page.text
    assert "По умолчанию" in page.text
    assert not await TenantUserManager().web_memberships(owner.id)


async def test_uid_display_and_model_edits_preserve_ids_keys_hidden_fields_and_omitted_rows(catalog_case):
    case = catalog_case
    owner = case.users[0]
    await case.save(owner, api_key="synthetic-key-" + case.marker)
    providers, models = await case.catalog(owner)
    provider, first, second = providers[0], models[0], models[1]
    encrypted = provider.encrypted_api_key
    with permission_scope(owner):
        await save_provider_bundle(
            user_id=owner.id,
            provider={"id": provider.id, "api_format": "anthropic"},
            models=[{"id": first.id, "max_tokens": 1777, "input_cost_per_1k": 0.25}],
        )
    await case.save(
        owner,
        provider_id=str(provider.id),
        provider_name="Renamed " + case.marker,
        provider_uid="renamed",
        api_key="",
        model_db_id=[str(first.id)],
        model_name=["Renamed model"],
        model_api_id=["renamed-api-id"],
    )
    providers, models = await case.catalog(owner)
    assert providers[0].id == provider.id
    assert providers[0].uid == "renamed" and providers[0].name == "Renamed " + case.marker
    assert providers[0].api_format == "anthropic" and providers[0].encrypted_api_key == encrypted
    by_id = {model.id: model for model in models}
    assert set(by_id) == {first.id, second.id}
    assert by_id[first.id].name == "Renamed model" and by_id[first.id].model_id == "renamed-api-id"
    assert by_id[first.id].provider_id == provider.id and by_id[first.id].owner_user_id == owner.id
    assert by_id[first.id].max_tokens == 1777 and by_id[first.id].input_cost_per_1k == 0.25
    assert by_id[second.id].name == second.name


async def test_foreign_provider_and_nth_foreign_model_cannot_mutate_or_partially_save(catalog_case):
    case = catalog_case
    owner, other = case.users
    await case.save(owner)
    await case.save(other, provider_name="Other " + case.marker)
    own_providers, own_models = await case.catalog(owner)
    other_providers, other_models = await case.catalog(other)
    await case.save(owner, provider_id=str(other_providers[0].id), provider_name="Foreign provider attempt")
    assert (await case.catalog(other))[0][0].name == "Other " + case.marker
    assert len((await case.catalog(owner))[0]) == 1
    await case.save(
        owner,
        provider_id=str(own_providers[0].id),
        provider_name="Must roll back",
        provider_uid="must-roll-back",
        model_db_id=[str(own_models[0].id), str(other_models[0].id)],
        model_name=["Must roll back model", "Foreign model"],
        model_api_id=["must-roll-back-api", "foreign-api"],
    )
    providers, models = await case.catalog(owner)
    assert providers[0].name == own_providers[0].name and providers[0].uid == "fixture"
    assert [(model.id, model.name, model.model_id) for model in models] == [
        (model.id, model.name, model.model_id) for model in own_models
    ]
    assert [(model.id, model.name) for model in (await case.catalog(other))[1]] == [
        (model.id, model.name) for model in other_models
    ]
    page = await case.clients[owner.id].get(PAGE)
    assert "Other " + case.marker not in page.text


@pytest.mark.parametrize("path", [SAVE, DEFAULT])
async def test_invalid_csrf_does_not_persist_or_change_default(catalog_case, path):
    case = catalog_case
    owner = case.users[0]
    await case.save(owner)
    providers, models = await case.catalog(owner)
    response = await case.clients[owner.id].post(
        path,
        data={
            "_csrf": "invalid-synthetic-csrf",
            "provider_id": str(providers[0].id),
            "provider_name": "Must not persist",
            "provider_uid": "must-not-persist",
            "base_url": "https://other.fixture.invalid/v1",
            "model_id": str(models[1].id),
        },
    )
    assert response.status_code == 403
    fresh_providers, fresh_models = await case.catalog(owner)
    assert fresh_providers[0].name == providers[0].name and fresh_providers[0].uid == providers[0].uid
    assert [(model.id, model.is_default) for model in fresh_models] == [
        (model.id, model.is_default) for model in models
    ]


async def test_default_is_personal_and_chat_selection_has_an_independent_numeric_marker(catalog_case):
    case = catalog_case
    owner, other = case.users
    await case.save(owner)
    await case.save(other, provider_name="Other " + case.marker)
    _, models = await case.catalog(owner)
    _, foreign = await case.catalog(other)
    for user, model in ((other, foreign[0]), (owner, models[0])):
        response = await case.clients[user.id].post(
            DEFAULT, data={"_csrf": await case.token_for(user), "model_id": str(model.id)}
        )
        assert response.status_code == 303
    response = await case.clients[owner.id].post(
        DEFAULT, data={"_csrf": await case.token_for(owner), "model_id": str(foreign[0].id)}
    )
    assert response.status_code == 303
    assert [(model.id, model.is_default) for model in (await case.catalog(owner))[1]] == [
        (models[0].id, True),
        (models[1].id, False),
    ]
    assert (await case.catalog(other))[1][0].is_default is True
    workspace_name = "Workspace " + case.marker
    case.workspace_names.add(workspace_name)
    response = await case.clients[owner.id].post(
        "/app/workspaces/new", data={"_csrf": await case.token_for(owner), "name": workspace_name}
    )
    assert response.status_code == 302
    memberships = await TenantUserManager().web_memberships(owner.id)
    assert len(memberships) == 1
    token = await case.token_for(owner, "/app/settings?tab=agent")
    response = await case.clients[owner.id].post(
        "/app/settings/agent", data={"_csrf": token, "agent_model_id": str(models[1].id)}
    )
    assert response.status_code == 302
    with fixture_scope():
        workspace = await tenants.get(id=memberships[0].tenant_id)
    assert workspace.agent_model_id == models[1].id
    page = await case.clients[owner.id].get(PAGE)
    assert page.status_code == 200
    rows = {row["name"]: row for row in ModelRows(page.text).rows}
    assert rows[models[0].name]["default"] is True and rows[models[0].name]["selected"] is False
    assert rows[models[1].name]["selected"] is True and rows[models[1].name]["default"] is False
    page = await case.clients[owner.id].get("/app/settings?tab=agent")
    assert f'name="agent_model_id" value="{models[1].id}"' in page.text


async def test_revoked_active_session_cannot_write_personal_catalog(catalog_case):
    case = catalog_case
    owner = case.users[0]
    token = await case.token_for(owner)
    with fixture_scope():
        await User.objects.update_by_id(owner.id, is_active=False)
    for path, data in (
        (PAGE, None),
        (SAVE, {"_csrf": token, "provider_name": "Must not persist", "provider_uid": "must-not-persist"}),
        (DEFAULT, {"_csrf": token, "model_id": "1"}),
    ):
        response = (
            await case.clients[owner.id].get(path)
            if data is None
            else await case.clients[owner.id].post(path, data=data)
        )
        assert response.status_code == 302
        assert response.headers["location"].startswith("/app/login")
    with fixture_scope():
        assert not await LLMProvider.objects.filter(owner_user_id=owner.id)
        assert not await LLMModel.objects.filter(owner_user_id=owner.id)
