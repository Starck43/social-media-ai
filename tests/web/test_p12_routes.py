"""The P12 workspace pages must answer as routes, not only as callables.

P12-T03, P12-T04 and P12-T05 each shipped a handler and a template behind a
"Zero-Conflict" rule that forbade touching `app/web/__init__.py`. Their unit
tests import the handler module directly with substituted dependencies, so a
green suite proved "the function renders a template" while `/app/cases` and its
siblings returned 404 in the running application (found 2026-11-02).

These tests ask the assembled application for each path through the ASGI stack.
Calling the handler is not enough: the route table is what the browser hits.
"""

from __future__ import annotations

import re
from typing import Any, Iterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_application

P12_PAGES = ("/app/knowledge", "/app/documents", "/app/communications", "/app/followups", "/app/cases")

CSRF_RE = re.compile(r'name="_csrf" value="([^"]+)"')


def _registered_paths() -> set[str]:
    """Every path the assembled application serves, included prefixes resolved.

    Route tables are nested includes here, so a flat walk of `app.routes` sees
    only the wrappers; the prefix lives on each include's context.
    """

    def walk(route: Any, prefix: str) -> Iterator[str]:
        ctx = getattr(route, "include_context", None)
        prefix += getattr(ctx, "prefix", "") or ""
        original = getattr(route, "original_router", None)
        if original is not None:
            for child in getattr(original, "routes", ()):
                yield from walk(child, prefix)
            return
        path = getattr(route, "path", "")
        if isinstance(path, str) and path:
            yield prefix + path

    app = create_application()
    paths: set[str] = set()
    for top in app.routes:
        candidates = top.effective_candidates() if type(top).__name__ == "_IncludedRouter" else (top,)
        for candidate in candidates:
            paths |= set(walk(candidate, ""))
    return paths


@pytest.mark.parametrize("path", P12_PAGES)
def test_p12_page_is_in_the_route_table(path: str) -> None:
    assert path in _registered_paths(), f"{path} has a handler but no registered route"


async def _signed_in_client() -> tuple[AsyncClient, str]:
    import secrets

    app = create_application()
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", follow_redirects=True)
    username = f"web{secrets.token_hex(4)}"
    page = await client.get("/app/register")
    assert page.status_code == 200
    match = CSRF_RE.search(page.text)
    assert match, "no csrf input on /app/register"
    resp = await client.post(
        "/app/register",
        data={
            "username": username,
            "email": f"{username}@example.com",
            "password": "secret-password-1",
            "workspace": f"Routes {username}",
            "_csrf": match.group(1),
        },
    )
    assert resp.status_code == 200
    return client, username


async def test_p12_pages_open_for_a_signed_in_member() -> None:
    """A member with the section rights gets the page, not a redirect to login."""
    from app.models import User
    from app.models.managers.tenant_manager import TenantUserManager, tenants

    client, username = await _signed_in_client()
    user = None
    tenant_id = None
    try:
        # `_signed_in_client` registered exactly one account on this connection.
        from app.core.tenant_context import tenant_scope

        with tenant_scope(bypass=True):
            user = await User.objects.get(username=username)
        assert user is not None
        memberships = await TenantUserManager().web_memberships(user.id)
        tenant_id = memberships[0].tenant_id

        for path in P12_PAGES:
            resp = await client.get(path)
            # 404 is the defect under test; a permission refusal is a different
            # answer and is covered by the section-rights tests.
            assert resp.status_code != 404, f"{path} is not routed"
            assert resp.status_code in (200, 403), f"{path} answered {resp.status_code}"
    finally:
        if user is not None:
            await User.objects.delete_user(user.id)
        if tenant_id is not None:
            await tenants.delete_by_id(tenant_id)
