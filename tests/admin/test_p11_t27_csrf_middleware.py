"""P11-T27: the console CSRF middleware must not eat the form body.

`AdminCSRFMiddleware` parses the form to find its token, and Starlette's form
parser drains the body stream. Without a replay the route handler cannot read
the payload and every console form POST answers 500 ("Stream consumed").
Source-only: no database, no console mount.
"""

from __future__ import annotations

# `tests/conftest.py` skips the suite database when only source-only modules are selected.
__source_only__ = True

import asyncio


def test_the_middleware_replays_the_form_body_to_the_route():
    """A form-token POST must reach its route with the payload intact.

    `AdminCSRFMiddleware` parses the form to find the token; Starlette's form
    parser drains the body stream, so a naive implementation 500s every console
    write with "Stream consumed". This pins the replay the S4 action POSTs rely
    on, through the real middleware stack.
    """
    import httpx
    from starlette.applications import Starlette
    from starlette.middleware.sessions import SessionMiddleware
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route

    from app.admin.csrf import AdminCSRFMiddleware, CSRFTokenManager, SESSION_ID_KEY

    async def echo(request):
        form = await request.form()
        return PlainTextResponse(f"pks={form.get('pks')}")

    app = Starlette(routes=[Route("/admin/action", echo, methods=["POST"])])
    manager = CSRFTokenManager(secret_key="test-secret")
    app.state.csrf_manager = manager

    async def seed(request):
        request.session[SESSION_ID_KEY] = "sid"
        return PlainTextResponse(manager.generate_token(session_id="sid"))

    app.router.routes.append(Route("/seed", seed))
    app.add_middleware(AdminCSRFMiddleware)
    app.add_middleware(SessionMiddleware, secret_key="test-secret")

    async def scenario():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            token = (await client.get("/seed")).text
            accepted = await client.post("/admin/action", data={"pks": "7", "csrf_token": token})
            refused = await client.post("/admin/action", data={"pks": "7", "csrf_token": "foreign"})
        return accepted, refused

    accepted, refused = asyncio.run(scenario())
    assert accepted.status_code == 200
    assert accepted.text == "pks=7"
    assert refused.status_code == 403
