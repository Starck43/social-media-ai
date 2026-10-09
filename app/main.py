import logging
from contextlib import asynccontextmanager
from typing import Awaitable

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi_pagination import add_pagination
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import Response
from starlette.websockets import WebSocket

from app.admin.csrf import CSRFTokenManager
from app.api.v1 import entry
from app.core.api_scope import ApiScopeMiddleware
from app.core.config import settings
from app.core.database import async_engine, init_db
from app.core.health import router as health_router
from app.core.tenant_context import PlatformScopeMiddleware
from app.web import web_router
from app.web.middleware import TenantUIMiddleware

# Configure logging
logger = logging.getLogger(__name__)

# Настройка шаблонов
templates = Jinja2Templates(directory="app/templates")


async def rate_limit_exceeded_handler(
    request: Request | WebSocket, _: Exception
) -> Response | Awaitable[Response] | None:
    if isinstance(request, WebSocket):
        # Handle WebSocket case if needed
        return None

    return JSONResponse(status_code=429, content={"detail": "Too many requests", "error": "rate_limit_exceeded"})


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Startup
    await init_db()
    yield
    # Shutdown
    await async_engine.dispose()


def create_application() -> FastAPI:
    application = FastAPI(
        title="ИИ Ассистент",
        description="API для управления социальными сетями с ИИ-аналитикой",
        version="0.4.0",
        debug=settings.DEBUG,
        lifespan=lifespan,
    )

    # Include API routes with rate limiting
    application.include_router(entry.router, prefix="/api/v1")
    application.include_router(health_router)

    # Client-facing UI (docs/design/ui.md): pages under /app, tenant-scoped.
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

    # Operator console only (sqladmin, static, health): runs as the platform
    # owner. See app/core/tenant_context.PlatformScopeMiddleware — it excludes
    # /app/* and /api/* because those resolve their own tenant.
    application.add_middleware(PlatformScopeMiddleware)

    # Set up CORS
    # application.middleware("http")(csrf_middleware)

    # Add SessionMiddleware with a secret key
    application.add_middleware(
        SessionMiddleware,  # type: ignore[arg-type]
        secret_key=settings.SECRET_KEY,
        session_cookie="session",
        max_age=3600 * 24,
    )

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
