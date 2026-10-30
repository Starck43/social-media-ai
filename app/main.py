import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Awaitable

# Allow `python app/main.py` as well as `python -m app.main`: running the file
# directly puts `app/` on sys.path instead of the project root, so the
# `app.*` imports below would fail with ModuleNotFoundError.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi_pagination import add_pagination
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import PlainTextResponse, Response
from starlette.websockets import WebSocket

from app.admin.csrf import CSRFTokenManager
from app.admin.scope import AdminScopeMiddleware
from app.api.v1 import entry
from app.core.api_scope import ApiScopeMiddleware
from app.core.config import settings
from app.core.database import async_engine, init_db
from app.core.health import router as health_router
from app.core.request_context import RequestContextMiddleware
from app.core.tenant_context import PlatformScopeMiddleware, tenant_scope
from app.version import __version__
from app.web import web_router
from app.web.middleware import TenantUIMiddleware

# Configure logging
logger = logging.getLogger(__name__)

# Настройка шаблонов
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


async def rate_limit_exceeded_handler(
    request: Request | WebSocket, _: Exception
) -> Response | Awaitable[Response] | None:
    if isinstance(request, WebSocket):
        # Handle WebSocket case if needed
        return None

    return JSONResponse(status_code=429, content={"detail": "Too many requests", "error": "rate_limit_exceeded"})


async def _job_maintenance_loop() -> None:
    """Keep retention and stale-claim quarantine available in web-only mode."""
    from app.jobs.lifecycle import cleanup_terminal_job_history, reap_stale_jobs

    while True:
        try:
            quarantined = await reap_stale_jobs()
            if quarantined:
                logger.warning("job_stale_quarantined count=%s", quarantined)
            with tenant_scope(bypass=True):
                await cleanup_terminal_job_history()
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 — maintenance failure must not prevent web startup
            logger.warning("job_maintenance_failed error_code=%s", type(error).__name__)
        await asyncio.sleep(300)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Startup
    await init_db()
    maintenance = asyncio.create_task(_job_maintenance_loop(), name="job-maintenance")
    try:
        yield
    finally:
        maintenance.cancel()
        try:
            await maintenance
        except asyncio.CancelledError:
            pass
        # Shutdown
        await async_engine.dispose()


def create_application() -> FastAPI:
    application = FastAPI(
        title="AI Assistant",
        description="API для управления социальными сетями с ИИ-аналитикой",
        version=__version__,
        debug=settings.DEBUG,
        lifespan=lifespan,
    )

    async def _unhandled_error_with_request_id(request: Request, error: Exception) -> Response:
        request_id = getattr(request.state, "request_id", None)
        logger.error(
            "unhandled_http_exception request_id=%s error_type=%s",
            request_id or "unavailable",
            type(error).__name__,
        )
        headers = {"X-Request-ID": request_id} if isinstance(request_id, str) else None
        return PlainTextResponse("Internal Server Error", status_code=500, headers=headers)

    application.add_exception_handler(Exception, _unhandled_error_with_request_id)

    # Include API routes with rate limiting
    application.include_router(entry.router, prefix="/api/v1")
    application.include_router(health_router)

    # Client-facing UI (docs/API.md): pages under /app, tenant-scoped.
    # Kept out of OpenAPI docs — it is HTML, not a machine API.
    application.include_router(web_router, include_in_schema=False)

    # CSRF token issuer shared by the web UI and sqladmin forms.
    application.state.csrf_manager = CSRFTokenManager(secret_key=settings.SECRET_KEY)

    # Registered before PlatformScopeMiddleware on purpose: `add_middleware`
    # puts the newest entry outermost, so TenantUIMiddleware ends up INSIDE
    # the platform bypass and owns the tenant context for every /app request.
    application.add_middleware(TenantUIMiddleware)

    # Same trick for the machine API: /api/* is authenticated and scoped to a
    # workspace the caller is a member of (never bypass).
    application.add_middleware(ApiScopeMiddleware)
    # Correlation wraps auth too, so denied requests receive the same safe ID.
    application.add_middleware(RequestContextMiddleware)

    # Operator console only (static, health, docs): infrastructure runs as the
    # platform owner. `/admin/*` is excluded from the bypass — `AdminScopeMiddleware`
    # finishes first and binds the console to its signed-in actor.
    application.add_middleware(PlatformScopeMiddleware)

    # Console data scope, registered last so it is the OUTERMOST wrapper of these
    # two (Starlette nests in reverse order of the calls). It must run *before*
    # the platform bypass opens: it replaces the caller's scope entirely, and an
    # inner layer could only widen it again. Owner keeps the bypass
    # (`docs/ADMIN.md` — "everyone else their own").
    application.add_middleware(AdminScopeMiddleware)

    # Add SessionMiddleware with a secret key
    application.add_middleware(
        SessionMiddleware,  # type: ignore[arg-type]
        secret_key=settings.SECRET_KEY,
        session_cookie="session",
        # Only the explicit development profile permits local HTTP cookies.
        https_only=settings.ENVIRONMENT.strip().lower() != "development",
        max_age=3600 * 24,
    )

    # Set up CORS
    if settings.BACKEND_CORS_ORIGINS:
        application.add_middleware(
            CORSMiddleware,  # type: ignore[arg-type]
            allow_origins=[str(origin) for origin in settings.BACKEND_CORS_ORIGINS],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # Add pagination support
    add_pagination(application)

    if settings.ADMIN_ENABLED:
        from app.admin.setup import setup_admin

        setup_admin(application)

    return application


app = create_application()


@app.get("/", tags=["Root"])
async def root():
    return RedirectResponse("/app/")


# Только для разработки
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.HOST or "0.0.0.0", port=settings.PORT or 8000, reload=settings.DEBUG)
