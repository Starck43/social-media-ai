from __future__ import annotations

from typing import Optional, Dict, Any, TYPE_CHECKING

from fastapi import HTTPException, status
from passlib.context import CryptContext
from sqlalchemy import select, or_
from sqlalchemy.exc import SQLAlchemyError

from app.core.hashing import get_password_hash, verify_password
from app.models.managers.base_manager import BaseManager

if TYPE_CHECKING:
	from app.models.user import User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class UserManager(BaseManager['User']):
	"""Manager for User model operations."""

	def __init__(self):
		from app.models.user import User
		super().__init__(User)

	async def get_by_email(self, email: str) -> Optional['User']:
		return await self.model.objects.get(email=email)

	async def get_by_username_or_email(self, username_or_email: str) -> Optional['User']:
		"""Get user by a username or email (case-insensitive).

		Args:
			username_or_email: Username or email address to search for

		Returns:
			User object if found, None otherwise
		"""
		if not username_or_email:
			return None

		# Check if the input looks like an email
		is_email = '@' in username_or_email

		if is_email:
			return await self.model.objects.get(email=username_or_email)
		else:
			return await self.model.objects.get(username=username_or_email)

	def get_active_users(self, skip: int = 0, limit: int = 100) -> list['User']:
		"""Get paginated list of active users."""
		return self.filter(self.model.is_active).offset(skip).limit(limit).all()

	def create_user(self, username: str, password: str, **extra_data) -> 'User':
		"""Create a new user with hashed password."""
		if not all([username, password]):
			raise ValueError("Username, email and password are required")

		return self.create(
			username=username,
			hashed_password=get_password_hash(password),
			**extra_data
		)

	def update_user(
			self,
			user_id: int,
			update_data: Dict[str, Any],
			current_user: Optional['User'] = None
	) -> Optional['User']:
		"""
		Update user data.

		Args:
			user_id: ID of the user to update
			update_data: Dictionary with fields to update
			current_user: Optional user making the request (for permission checks)

		Returns:
			Updated User object or None if not found
		"""
		user = self.get_queryset().get(id=user_id)
		if not user:
			return None

		# Only allow admins, or the user themselves to update
		if current_user and not current_user.is_superuser and current_user.id != user_id:
			raise HTTPException(
				status_code=status.HTTP_403_FORBIDDEN,
				detail="Not enough permissions"
			)

		# Handle password updates separately
		if 'password' in update_data:
			update_data['hashed_password'] = pwd_context.hash(update_data.pop('password'))

		return self.update_by_id(user_id, **update_data)

	def delete_user(self, user_id: int) -> bool:
		"""
		Delete a user by ID.
		
		Args:
			user_id: ID of the user to delete
			
		Returns:
			bool: True if deletion successful, False otherwise
		"""
		return bool(self.delete_by_id(user_id))

	def change_password(
			self,
			user_id: int,
			current_password: str,
			new_password: str,
			current_user: Optional['User'] = None
	) -> bool:
		"""
		Change a user's password.
		
		Args:
			user_id: ID of the user
			current_password: A current password for verification
			new_password: New password to set
			current_user: Optional user making the request
			
		Returns:
			bool: True if password was changed successfully
		"""
		user = self.get_queryset().get(id=user_id)
		if not user:
			raise HTTPException(
				status_code=status.HTTP_404_NOT_FOUND,
				detail="User not found"
			)

		# Only allow admins, or the user themselves to change password
		if current_user and not current_user.is_superuser and current_user.id != user_id:
			raise HTTPException(
				status_code=status.HTTP_403_FORBIDDEN,
				detail="Not enough permissions"
			)

		# Verify current password
		if not verify_password(current_password, user.hashed_password):
			raise HTTPException(
				status_code=status.HTTP_400_BAD_REQUEST,
				detail="Incorrect current password"
			)

		# Update password
		self.update_by_id(
			user_id,
			hashed_password=pwd_context.hash(new_password)
		)
		return True
