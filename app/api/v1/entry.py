import asyncio

from fastapi import APIRouter, Depends, HTTPException

from app.models import Permission, User
from app.services.user.auth import get_authenticated_user

from app.api.v1.endpoints import (
	auth, user, roles, monitoring, scenarios, 
	notifications, dashboard, llm_providers, social,
    sources, tasks, credentials, llm_models, action_audit
)

router = APIRouter()
DB_PROBE_TIMEOUT_SECONDS = 2.0

# Every data router requires a bearer token. `ApiScopeMiddleware` already
# rejects unauthenticated /api/* requests, this is the defense-in-depth layer
# that survives someone registering a new router without noticing the rule.
# `/auth` stays public — it is how you get a token in the first place.
API_AUTH = [Depends(get_authenticated_user)]

router.include_router(user.router, prefix="/users", tags=["user"], dependencies=API_AUTH)
router.include_router(roles.router, prefix="/users/roles", tags=["user"], dependencies=API_AUTH)
router.include_router(auth.router, prefix="/auth", tags=["auth"])
router.include_router(monitoring.router, prefix="/monitoring", tags=["monitoring"], dependencies=API_AUTH)
router.include_router(scenarios.router, prefix="/ai", tags=["scenarios"], dependencies=API_AUTH)
router.include_router(notifications.router, prefix="/notifications", tags=["notifications"], dependencies=API_AUTH)
router.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"], dependencies=API_AUTH)
router.include_router(llm_providers.router, prefix="/llm", tags=["llm"], dependencies=API_AUTH)
router.include_router(llm_models.router, prefix="/llm", tags=["llm"], dependencies=API_AUTH)
# Public (no API_AUTH): the platform redirects the browser here after consent.
router.include_router(social.router, prefix="/social", tags=["social"])
router.include_router(sources.router, prefix="/sources", tags=["sources"], dependencies=API_AUTH)
router.include_router(tasks.router, prefix="/tasks", tags=["tasks"], dependencies=API_AUTH)
router.include_router(credentials.router, prefix="/credentials", tags=["credentials"], dependencies=API_AUTH)
router.include_router(action_audit.router, prefix="/audit", tags=["action-audit"], dependencies=API_AUTH)


# Keep the test endpoints for backward compatibility.
# The probe is a real ORM read against `Permission` (a global model, so it needs
# no tenant context) instead of raw `SELECT`s on a session: same connectivity
# signal, but it goes through the manager layer and cannot drift out of sync
# with it. The previous version also crashed, awaiting a sync `Session`.
async def _probe_database():
    """Bound the legacy ORM read; never disclose internal failure details.

    Cancellation propagates. The timeout assumes a cooperative async driver.
    """
    try:
        return await asyncio.wait_for(Permission.objects.count(), timeout=DB_PROBE_TIMEOUT_SECONDS)
    except Exception:
        raise HTTPException(
            status_code=500,
            detail="Database connection error",
        ) from None


@router.get("/test-db", include_in_schema=False, dependencies=API_AUTH)
async def test_database():
    permission_count = await _probe_database()
    return {"database": "connected", "permissions": permission_count}


@router.get("/test-auth", include_in_schema=False)
async def test_auth(current_user: User = Depends(get_authenticated_user)):
	return {"message": "Auth endpoint works", "user_id": current_user.id}


@router.get("/health", tags=["Health"])
async def health_check():
    """Legacy connectivity check; not the root readiness contract."""
    await _probe_database()
    return {"status": "ok", "database": "connected"}
