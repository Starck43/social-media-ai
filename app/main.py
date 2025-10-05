import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Awaitable

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi_pagination import add_pagination
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import Response
from starlette.websockets import WebSocket

from app.api.v1 import entry
from app.core.config import settings
from app.core.database import async_engine, init_db

# Configure logging
logger = logging.getLogger(__name__)


async def rate_limit_exceeded_handler(
		request: Request | WebSocket,
		_: Exception
) -> Response | Awaitable[Response] | None:
	if isinstance(request, WebSocket):
		# Handle WebSocket case if needed
		return None

	return JSONResponse(
		status_code=429,
		content={"detail": "Too many requests", "error": "rate_limit_exceeded"}
	)


@asynccontextmanager
async def lifespan(_: FastAPI):
	# Startup
	await init_db()
	yield
	# Shutdown
	await async_engine.dispose()


def create_application() -> FastAPI:
	application = FastAPI(
		title="Social Media AI Manager",
		description="API для управления социальными сетями с AI аналитикой",
		version="1.0.0",
		debug=settings.DEBUG,
		lifespan=lifespan
	)

	# Include API routes with rate limiting
	application.include_router(entry.router, prefix="/api/v1")

	# Set up CORS
	# application.middleware("http")(csrf_middleware)

	# Add SessionMiddleware with a secret key
	application.add_middleware(
		SessionMiddleware,
		secret_key=settings.SECRET_KEY,
		session_cookie="session",
		max_age=3600  # 1 hour
	)

	if settings.BACKEND_CORS_ORIGINS:
		application.add_middleware(
			CORSMiddleware,
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
	return {
		"message": "Social Media AI Manager API",
		"version": "1.0.0",
		"docs": "/docs",
		"health": "/health",
		"environment": settings.ENVIRONMENT
	}


@app.get("/health", tags=["Health"])
async def health_check():
	# Более надежная проверка подключения к БД
	try:
		with async_engine.connect() as conn:
			conn.execute("SELECT 1")
		db_status = "connected"
	except Exception:
		db_status = "disconnected"

	return {
		"status": "ok",
		"database": db_status,
		"timestamp": datetime.now()
	}


# Только для разработки
if __name__ == "__main__":
	import uvicorn

	uvicorn.run(
		"app.main:app",
		host=settings.HOST or "0.0.0.0",
		port=settings.PORT or 8000,
		reload=settings.DEBUG or True
	)
