from fastapi import Depends, HTTPException
from starlette import status

from app.types import UserRoleType
from app.services.user.auth import get_authenticated_user
from app.models import User


def require_minimum_role(min_role: UserRoleType):
	"""Requires minimum role level."""

	async def checker(user: User = Depends(get_authenticated_user)) -> User:
		if not user.has_minimum_role(min_role):
			raise HTTPException(
				status_code=status.HTTP_403_FORBIDDEN,
				detail="Insufficient permissions"
			)
		return user

	return checker


def require_any_role(*roles: UserRoleType):
	""" Requires any of the specified roles."""

	async def checker(user: User = Depends(get_authenticated_user)) -> User:
		if user.is_superuser:
			return user

		if not any(user.has_role(role) for role in roles):
			raise HTTPException(
				status_code=403,
				detail=f"Requires one of: {[r.value for r in roles]}"
			)
		return user

	return checker


def require_all_roles(*roles: UserRoleType):
	"""Requires all the specified roles"""

	async def checker(user: User = Depends(get_authenticated_user)) -> User:
		if user.is_superuser:
			return user

		if not all(user.has_role(role) for role in roles):
			raise HTTPException(
				status_code=status.HTTP_403_FORBIDDEN,
				detail=f"Requires all roles: {[r.value for r in roles]}"
			)
		return user

	return checker


require_roles = require_any_role
