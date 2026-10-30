from pathlib import Path

from jinja2 import pass_context
from sqlalchemy.ext.asyncio import async_sessionmaker
from starlette.staticfiles import StaticFiles

from app.admin.authorization import AdminAuthorizationBackend
from app.admin.csrf import CSRFTokenManager, issue_token
from app.admin.session import AdminAsyncSession
from app.core.config import settings
from app.core.database import async_engine
from app.templates.filters import register_template_filters

from .auth import AdminAuthBackend
from .inlines import related_inlines
from .registry import TableRegistryAdmin
from .related import RelatedAdmin, related_controls, related_token
from .views import (
    AgentScenarioAdmin,
    AgentTaskAdmin,
    AIAnalyticsAdmin,
    BotActionAdmin,
    CollectedItemAdmin,
    JobAdmin,
    LLMModelAdmin,
    LLMProviderAdmin,
    NotificationAdmin,
    PermissionAdmin,
    PlatformAdmin,
    RoleAdmin,
    SourceAdmin,
    TenantAdmin,
    UserAdmin,
    UserCredentialAdmin,
)

# Get the project root directory
PROJECT_ROOT = Path(__file__).parent.parent.parent


def _render_analysis(summary_data):
    """Shared `summary_data` → display structure, used by the admin detail page.

    The same helper the web UI (`/app/analytics/{id}`) renders through, so the
    two surfaces extract and display the stored analysis identically.
    """
    from app.services.ai.analysis_render import render_analysis

    return render_analysis(summary_data)


def setup_admin(app):
    """Initialize SQLAdmin with all views."""

    # --- CSRF ---
    # Reuse the manager `app/main.py` already published: one token issuer per
    # process, so every surface verifies the tokens any of them minted.
    csrf_manager = getattr(app.state, "csrf_manager", None) or CSRFTokenManager(secret_key=settings.SECRET_KEY)
    app.state.csrf_manager = csrf_manager

    from .endpoints import router as admin_router

    app.include_router(admin_router)

    # --- AdminAuthBackend ---
    authentication_backend = AdminAuthBackend(secret_key=settings.SECRET_KEY, csrf_manager=app.state.csrf_manager)

    # --- SQLAdmin instance ---
    admin = RelatedAdmin(
        app=app,
        engine=async_engine,
        session_maker=async_sessionmaker(async_engine, class_=AdminAsyncSession, expire_on_commit=False),
        authentication_backend=authentication_backend,
        authorization_backend=AdminAuthorizationBackend(),
        base_url="/admin",
        title="AI Assistant",
        logo_url="/static/logo.png",
        templates_dir=str(PROJECT_ROOT / "app" / "templates"),
    )

    @pass_context
    def _csrf_token(context):
        # Templates render per request, so the token binds to that session;
        # without a request (e.g. shell rendering) fall back to an unbound token.
        request = context.get("request")
        if request is None:
            return csrf_manager.generate_token()
        return issue_token(request)

    admin.templates.env.globals.update(
        {
            "csrf_token": _csrf_token,
            "settings": settings,
            "debug": settings.DEBUG,
            "render_analysis": _render_analysis,
            "related_controls": related_controls,
            "related_inlines": related_inlines,
            "related_token": related_token,
        }
    )

    if admin.templates.env is not None:
        register_template_filters(admin.templates.env)

    app.mount("/static", StaticFiles(directory=str(Path(__file__).resolve().parents[1] / "static")), name="static")
    app.state.admin = admin
    if not hasattr(app, "extra"):
        app.extra = {}
    app.extra["admin"] = admin

    # --- Views configs ---
    view_configs = [
        UserAdmin,
        RoleAdmin,
        PermissionAdmin,
        PlatformAdmin,
        SourceAdmin,
        CollectedItemAdmin,
        AgentScenarioAdmin,
        AgentTaskAdmin,
        BotActionAdmin,
        AIAnalyticsAdmin,
        JobAdmin,
        NotificationAdmin,
        LLMProviderAdmin,
        LLMModelAdmin,
        TenantAdmin,
        UserCredentialAdmin,
    ]

    # The owner's read-only map of every table, first in the menu so the widest
    # surface is not buried under the model pages it summarises.
    admin.add_view(TableRegistryAdmin)
    for view_class in view_configs:
        admin.add_view(view_class)

    return admin
