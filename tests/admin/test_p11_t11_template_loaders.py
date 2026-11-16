"""SQLAdmin action tests prove moved modules still load app templates first.

The real ASGI routes and local Jinja templates are exercised; only collection is
stubbed so these tests never call a provider.
"""

from __future__ import annotations

import secrets

import pytest

from app.models import Platform, Source
from app.types import PlatformType, SourceType
from tests.admin.test_admin_authorization import _operator, _operator_with_workspace, _role, _signed_in

pytestmark = pytest.mark.asyncio


async def _workspace_actor(role) -> tuple[str, int]:
    from app.models import User
    from app.models.managers.tenant_manager import TenantUserManager

    username = await _operator_with_workspace(role)
    user = await User.objects.get(username=username)
    memberships = await TenantUserManager().web_memberships(user.id)
    assert len(memberships) == 1
    return username, memberships[0].tenant_id


async def _source(tenant_id: int) -> Source:
    platform = await Platform.objects.filter(platform_type=PlatformType.VK, is_active=True).first()
    assert platform is not None
    return await Source.objects.create(
        tenant_id=tenant_id,
        platform_id=platform.id,
        name=f"template-loader-{secrets.token_hex(4)}",
        source_type=SourceType.GROUP,
        external_id=secrets.token_hex(6),
        params={"collection": {"limit": 7}},
        is_active=True,
    )


@pytest.mark.parametrize("failure", [False, True])
async def test_source_action_loads_local_standalone_template(monkeypatch, failure: bool) -> None:
    username, tenant_id = await _workspace_actor(await _role("ADMIN"))
    source = await _source(tenant_id)
    collected: list[tuple[int, int, int, str]] = []

    class FakeClient:
        async def collect_data(self, *, source, content_type):
            collected.append(
                (source.id, source.params["collection"]["offset"], source.params["collection"]["count"], content_type)
            )
            if failure:
                raise ValueError("safe test collection failure")
            return []

    client = FakeClient()
    monkeypatch.setattr("app.services.social.factory.get_social_client", lambda platform: client)

    async with _signed_in(username) as browser:
        response = await browser.get(f"/admin/source/action/check-source?pks={source.id}&page=2&per_page=10")

    assert response.status_code == 200
    assert "Проверка источника" in response.text
    assert source.name in response.text
    assert len(collected) == 1
    assert collected[0] == (source.id, 10, 10, "posts")
    if failure:
        assert "safe test collection failure" in response.text
        assert "Ошибка" in response.text
    else:
        assert "Всего постов" in response.text
        assert "0" in response.text


async def test_source_action_still_refuses_admin_without_workspace(monkeypatch) -> None:
    source = await _source((await _workspace_actor(await _role("ADMIN")))[1])
    username = await _operator(await _role("ADMIN"))

    def unexpected_provider_call(platform):
        raise AssertionError("refused actor must not reach provider")

    monkeypatch.setattr("app.services.social.factory.get_social_client", unexpected_provider_call)

    async with _signed_in(username) as browser:
        response = await browser.get(f"/admin/source/action/check-source?pks={source.id}")

    assert response.status_code == 403
