"""Client-facing web UI (docs/API.md): routes under `/app`.

FastAPI + Jinja2 + HTMX/Alpine/Tailwind via CDN. The UI is an alternative to
the messenger chat: same tenants, same agent, same database — reached through
web memberships (`tenant_users.user_id`, see `app/web/middleware.py`).
"""

from fastapi import APIRouter

from .actions import router as actions_router
from .analytics import router as analytics_router
from .auth import router as auth_router
from .billing import router as billing_router
from .cases import router as cases_router
from .chat import router as chat_router
from .commands import router as commands_router
from .communications import router as communications_router
from .connections import router as connections_router
from .connector_accounts import router as connector_accounts_router
from .contacts import router as contacts_router
from .dashboard import router as dashboard_router
from .digests import router as digests_router
from .documents import router as documents_router
from .file_catalog import router as file_catalog_router
from .file_workspace import router as file_workspace_router
from .followups import router as followups_router
from .jobs import router as jobs_router
from .knowledge import router as knowledge_router
from .notifications import router as notifications_router
from .onboarding import router as onboarding_router
from .scenarios import router as scenarios_router
from .settings import router as settings_router
from .settings_llm import router as settings_llm_router
from .settings_oauth import router as settings_oauth_router
from .sources import router as sources_router
from .tasks import router as tasks_router
from .telegram_login import router as telegram_login_router
from .trusted_hosts import router as trusted_hosts_router
from .vk import router as vk_router

web_router = APIRouter(prefix="/app", tags=["web"])
web_router.include_router(auth_router)
web_router.include_router(actions_router)
web_router.include_router(billing_router)
web_router.include_router(analytics_router)
web_router.include_router(dashboard_router)
web_router.include_router(cases_router)
web_router.include_router(chat_router)
web_router.include_router(commands_router)
web_router.include_router(communications_router)
web_router.include_router(connections_router)
web_router.include_router(connector_accounts_router)
web_router.include_router(contacts_router)
web_router.include_router(digests_router)
web_router.include_router(documents_router)
web_router.include_router(file_catalog_router)
web_router.include_router(file_workspace_router)
web_router.include_router(followups_router)
web_router.include_router(jobs_router)
web_router.include_router(knowledge_router)
web_router.include_router(notifications_router)
web_router.include_router(onboarding_router)
web_router.include_router(scenarios_router)
web_router.include_router(settings_router)
web_router.include_router(settings_llm_router)
web_router.include_router(settings_oauth_router)
web_router.include_router(sources_router)
web_router.include_router(tasks_router)
web_router.include_router(telegram_login_router)
web_router.include_router(trusted_hosts_router)
web_router.include_router(vk_router)

__all__ = ["web_router"]
