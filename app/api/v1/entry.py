from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.core.database import get_db
from app.models import User
from app.services.user.auth import get_authenticated_user

from app.api.v1.endpoints import auth, user, social, roles

router = APIRouter()

router.include_router(user.router, prefix="/users", tags=["user"])
router.include_router(roles.router, prefix="/users/roles", tags=["user"])
router.include_router(auth.router, prefix="/auth", tags=["auth"])
router.include_router(social.router, prefix="/social", tags=["social"])
# router.include_router(ai.router, prefix="/ai", tags=["ai"])


# Keep the test endpoints for backward compatibility
@router.get("/test-db", include_in_schema=False)
async def test_database(db: Session = Depends(get_db)):
	result = db.execute(text("SELECT version()"))
	version = result.scalar()
	return {"database": "connected", "version": version}


@router.get("/test-auth", include_in_schema=False)
async def test_auth(current_user: User = Depends(get_authenticated_user)):
	return {"message": "Auth endpoint works", "user_id": current_user.id}


@router.get("/health", tags=["Health"])
async def health_check(db: Session = Depends(get_db)):
	"""Health check endpoint"""
	try:
		# Check database connection
		db.execute(text("SELECT 1"))
		return {
			"status": "ok",
			"database": "connected"
		}
	except Exception as e:
		raise HTTPException(
			status_code=500,
			detail=f"Database connection error: {str(e)}"
		)
