"""Local real-ASGI digest-send acceptance with DB membership and signed cookies.

Requires current root conftest and the sibling PostgreSQL module. No lifespan
is started: ASGITransport exercises HTTP middleware/routes without runtime owners.
Only external publication/model continuation are doubled by digest_effects.
"""

from __future__ import annotations

import re
from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient

# isort: split
# Pytest's default prepend mode adds the tests directory (no tests package).
# Explicit fixture import makes this module independently selectable by path.
from test_digest_send_postgres_acceptance import (  # noqa: F401
    actor,
    digest_effects,
    owned_workspace,
)

# isort: split
# The sibling module validates root-conftest/target BEFORE any app import here.
from app.main import create_application

pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]
CSRF_RE = re.compile(r'name="_csrf" value="([^"]+)"')
DENIED = "Недостаточно прав для этого действия"
EXPIRED = "Сессия истекла, попробуйте ещё раз"
FORM = {"period": "week", "group_by": "sources", "time_breakdown": "true"}
EXPECTED = {"period": "week", "force": True, "group_by": "sources", "time_breakdown": True}


async def csrf(client, path):
    response = await client.get(path)
    assert response.status_code == 200, (path, response.status_code)
    match = CSRF_RE.search(response.text)
    assert match is not None, f"Missing CSRF input on {path}"
    return match.group(1), response


@asynccontextmanager
async def logged_in(subject):
    # HTTPS also handles a secure session cookie outside development settings.
    async with AsyncClient(
        transport=ASGITransport(app=create_application()),
        base_url="https://testserver",
        follow_redirects=False,
    ) as client:
        token, _ = await csrf(client, "/app/login")
        response = await client.post(
            "/app/login",
            data={"username": subject.user.username, "password": subject.workspace.password, "_csrf": token},
        )
        assert response.status_code == 302
        assert response.headers["location"] == "/app/"
        # A login redirect alone does not prove a usable authenticated session.
        token, page = await csrf(client, "/app/digests")
        yield client, token, page


async def post_send(client, token, **extra):
    data = {**FORM, **extra}
    if token is not None:
        data["_csrf"] = token
    return await client.post("/app/digests/send-now", data=data)


async def assert_flash_redirect(client, response, message):
    assert response.status_code == 302
    assert response.headers["location"] == "/app/digests"
    page = await client.get("/app/digests")
    assert page.status_code == 200
    assert message in page.text
    return page


@pytest.mark.parametrize(
    "role,allowed",
    [
        ("owner", True),
        ("admin", True),
        ("analyst", True),
        ("manager", False),
        ("viewer", False),
    ],
)
async def test_real_web_presets_and_ai_independence(role, allowed, digest_effects):
    async with owned_workspace() as workspace:
        # Explicitly disabled AI does not remove independent web digest rights.
        subject = await actor(workspace, role, ai=False)
        async with logged_in(subject) as (client, token, page):
            assert ('action="/app/digests/send-now"' in page.text) is allowed
            response = await post_send(client, token)
            if allowed:
                await assert_flash_redirect(client, response, "Дайджест отправлен в каналы")
                digest_effects.publisher.assert_awaited_once_with(**EXPECTED)
                assert digest_effects.seen == [(workspace.tenant_id, EXPECTED)]
            else:
                await assert_flash_redirect(client, response, DENIED)
                digest_effects.publisher.assert_not_awaited()


async def test_real_platform_admin_viewer_still_cannot_send(digest_effects):
    async with owned_workspace() as workspace:
        subject = await actor(workspace, "viewer", platform="admin")
        async with logged_in(subject) as (client, token, _):
            response = await post_send(client, token)
            await assert_flash_redirect(client, response, DENIED)
            digest_effects.publisher.assert_not_awaited()


@pytest.mark.parametrize("invalid", ["missing", "invalid", "tampered"])
async def test_real_http_csrf_refuses_authorized_create(invalid, digest_effects):
    async with owned_workspace() as workspace:
        subject = await actor(workspace)
        async with logged_in(subject) as (client, token, page):
            assert 'action="/app/digests/send-now"' in page.text
            supplied = {"missing": None, "invalid": "not-a-signed-token", "tampered": token + "tampered"}[invalid]
            response = await post_send(client, supplied)
            await assert_flash_redirect(client, response, EXPIRED)
            digest_effects.publisher.assert_not_awaited()


@pytest.mark.parametrize("change", ["viewer", "inactive", "missing"])
async def test_real_middleware_refreshes_membership_between_get_and_post(change, digest_effects):
    async with owned_workspace() as workspace:
        subject = await actor(workspace)
        async with logged_in(subject) as (client, token, page):
            assert 'action="/app/digests/send-now"' in page.text
            await workspace.change(subject.membership, change)
            response = await post_send(client, token)
            if change == "viewer":
                await assert_flash_redirect(client, response, DENIED)
            else:
                # No active membership: middleware refuses BEFORE the send route.
                assert response.status_code == 302
                assert response.headers["location"] == "/app/invite"
            digest_effects.publisher.assert_not_awaited()


async def test_posted_foreign_workspace_cannot_widen_real_http_scope(digest_effects):
    async with owned_workspace() as workspace, owned_workspace() as foreign:
        subject = await actor(workspace)
        async with logged_in(subject) as (client, token, _):
            response = await post_send(client, token, tenant_id=str(foreign.tenant_id))
            # Current contract ignores the untrusted selector for an ordinary member.
            await assert_flash_redirect(client, response, "Дайджест отправлен в каналы")
            digest_effects.publisher.assert_awaited_once_with(**EXPECTED)
            assert digest_effects.seen == [(workspace.tenant_id, EXPECTED)]
            assert all(tenant_id != foreign.tenant_id for tenant_id, _ in digest_effects.seen)
