"""Client-facing web UI (docs/design/ui.md): routes under `/app`.

FastAPI + Jinja2 + HTMX/Alpine/Tailwind via CDN. The UI is an alternative to
the messenger chat: same tenants, same agent, same database — reached through
web memberships (`tenant_users.user_id`, see `app/web/middleware.py`).
"""

from fastapi import APIRouter

from .auth import router as auth_router
from .dashboard import router as dashboard_router
from .scenarios import router as scenarios_router
from .sources import router as sources_router

web_router = APIRouter(prefix="/app", tags=["web"])
web_router.include_router(auth_router)
web_router.include_router(dashboard_router)
web_router.include_router(scenarios_router)
web_router.include_router(sources_router)

__all__ = ["web_router"]
