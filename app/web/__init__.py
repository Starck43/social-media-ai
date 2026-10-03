"""Client-facing web UI (docs/design/ui.md): routes under `/app`.

FastAPI + Jinja2 + HTMX/Alpine/Tailwind via CDN. The UI is an alternative to
the messenger chat: same tenants, same agent, same database — reached through
web memberships (`tenant_users.user_id`, see `app/web/middleware.py`).
"""

from fastapi import APIRouter

from .analytics import router as analytics_router
from .auth import router as auth_router
from .chat import router as chat_router
from .connections import router as connections_router
from .dashboard import router as dashboard_router
from .digests import router as digests_router
from .jobs import router as jobs_router
from .notifications import router as notifications_router
from .onboarding import router as onboarding_router
from .scenarios import router as scenarios_router
from .settings import router as settings_router
from .sources import router as sources_router
from .tasks import router as tasks_router
from .vk import router as vk_router

web_router = APIRouter(prefix="/app", tags=["web"])
web_router.include_router(auth_router)
web_router.include_router(analytics_router)
web_router.include_router(dashboard_router)
web_router.include_router(chat_router)
web_router.include_router(connections_router)
web_router.include_router(digests_router)
web_router.include_router(jobs_router)
web_router.include_router(notifications_router)
web_router.include_router(onboarding_router)
web_router.include_router(scenarios_router)
web_router.include_router(settings_router)
web_router.include_router(sources_router)
web_router.include_router(tasks_router)
web_router.include_router(vk_router)

__all__ = ["web_router"]
