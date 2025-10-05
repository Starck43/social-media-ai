from fastapi import Request, HTTPException

from app.admin.csrf import get_csrf_manager
from app.core.config import settings


async def csrf_middleware(request: Request, call_next):
    if settings.DEBUG:
        return await call_next(request)

    if request.method in {"GET", "HEAD", "OPTIONS", "TRACE"}:
        return await call_next(request)

    # НЕ трогаем админку — формы там проверяет AuthenticationBackend.login
    skip_paths = ("/static", "/api", "/docs", "/redoc", "/health", "/admin")
    if any(request.url.path.startswith(p) for p in skip_paths):
        return await call_next(request)

    token = (
        request.headers.get("X-CSRF-Token")
        or request.headers.get("x-csrf-token")
        or request.cookies.get("csrf_token")
    )

    if not token:
        raise HTTPException(status_code=403, detail="CSRF token missing")

    csrf_manager = get_csrf_manager(request)
    if not csrf_manager or not csrf_manager.verify_token(token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")

    return await call_next(request)
