from fastapi import APIRouter, Depends, HTTPException

from app.models import Permission, User
from app.services.user.auth import get_authenticated_user

from app.api.v1.endpoints import (
	auth, user, roles, monitoring, scenarios, 
	notifications, dashboard, llm_providers, social
)

router = APIRouter()

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
# Public (no API_AUTH): the platform redirects the browser here after consent.
router.include_router(social.router, prefix="/social", tags=["social"])


# Keep the test endpoints for backward compatibility.
# The probe is a real ORM read against `Permission` (a global model, so it needs
# no tenant context) instead of raw `SELECT`s on a session: same connectivity
# signal, but it goes through the manager layer and cannot drift out of sync
# with it. The previous version also crashed, awaiting a sync `Session`.
@router.get("/test-db", include_in_schema=False, dependencies=API_AUTH)
async def test_database():
    try:
        permission_count = await Permission.objects.count()
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Database connection error: {str(exc)}",
        )
    return {"database": "connected", "permissions": permission_count}


@router.get("/test-auth", include_in_schema=False)
async def test_auth(current_user: User = Depends(get_authenticated_user)):
	return {"message": "Auth endpoint works", "user_id": current_user.id}


@router.get("/health", tags=["Health"])
async def health_check():
	"""Health check endpoint"""
	try:
		await Permission.objects.count()
		return {
			"status": "ok",
			"database": "connected"
		}
	except Exception as e:
		raise HTTPException(
			status_code=500,
			detail=f"Database connection error: {str(e)}"
		)
