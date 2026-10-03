"""Backwards-compatible aliases for the old VK login routes.

The login/logout buttons moved to `/app/settings?tab=connections`, which is
where every platform's personal authorization lives now
(`app/web/connections.py`). These routes stay so a bookmark, an open tab or the
API-driven flow keeps working — they simply delegate.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from .connections import connection_authorize, connection_disconnect

router = APIRouter()


@router.get("/vk/oauth", include_in_schema=False)
async def vk_oauth_start(request: Request):
    """Redirect to VK to authorize the L2 (user) token."""
    return await connection_authorize(request, "vk")


@router.post("/vk/logout", include_in_schema=False)
async def vk_logout(request: Request):
    """Delete the caller's VK L2 user token from the personal vault."""
    return await connection_disconnect(request, "vk")
