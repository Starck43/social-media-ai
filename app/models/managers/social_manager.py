from __future__ import annotations

from datetime import datetime
from typing import Optional, TYPE_CHECKING

from fastapi import HTTPException
from sqlalchemy import and_
from sqlalchemy.orm import Query, Mapped

from .base_manager import BaseManager

if TYPE_CHECKING:
	from ..social import SocialAccount, SocialGroup


class SocialAccountBaseManager(BaseManager['SocialAccount']):
	"""
	Custom query manager for SocialAccount model with business logic methods.
	This is the single source of truth for all social account related database operations.
	"""

	def __init__(self):
		# Use string literal to avoid circular import
		from ..social import SocialAccount as SA
		super().__init__(SA)

	def get_by_platform_user_id(
			self,
			platform: str,
			platform_user_id: str
	) -> Optional['SocialAccount']:
		"""Get social account by a platform and platform user ID."""

		return self.get_queryset().filter(
			and_(
				self.model.platform == platform,
				self.model.platform_user_id == platform_user_id
			)
		).first()

	def get_user_accounts(
			self,
			user_id: int,
			platform: Mapped[str] | None = None,
			skip: int = 0,
			limit: int = 100,
			is_active: Mapped[bool] | None = None
	) -> list['SocialAccount']:
		"""
		Get paginated list of social accounts for a user.

		Args:
			user_id: ID of the user
			platform: Optional platform filter
			skip: Amount records to skip
			limit: Maximum amount records to return
			is_active: Filter by active status if provided

		Returns:
			List of SocialAccount objects
		"""
		query = self.filter(self.model.user_id == user_id)

		if platform:
			query = query.filter(self.model.platform == platform)

		if is_active is not None:
			query = query.filter(self.model.is_active == is_active)

		return query.offset(skip).limit(limit).all()

	def create_social_account(
			self,
			user_id: int,
			platform: str,
			platform_user_id: str,
			access_token: str,
			refresh_token: str | None = None,
			token_expires_at: datetime | None = None,
			profile_data: dict | None = None,
			is_active: bool = True
	) -> 'SocialAccount':
		"""Create a new social account."""

		return self.create(
			user_id=user_id,
			platform=platform,
			platform_user_id=platform_user_id,
			access_token=access_token,
			refresh_token=refresh_token,
			token_expires_at=token_expires_at,
			profile_data=profile_data,
			is_active=is_active
		)

	def update_social_account(
			self,
			account_id: int,
			**update_data
	) -> 'SocialAccount':
		"""Update social account data."""

		return self.update_by_id(account_id, **update_data)

	def deactivate_account(self, account_id: int) -> bool:
		"""Deactivate the current social account."""
		return self.update_by_id(account_id, is_active=False) is not None

	def delete_account(self, account_id: int, user_id: int | None = None) -> bool:
		"""
		Delete a social media account by ID.

		Args:
			account_id: ID of the account to delete
			user_id: Optional user ID to verify ownership

		Returns:
			bool: True if account was deleted, False if not found

			Raises:
				HTTPException: If user doesn't have permission to delete the account
			"""

		account = self.get(id=account_id)
		if not account:
			return False

		# Verify ownership if user_id is provided
		if user_id is not None and account.user_id != user_id:
			raise HTTPException(
				status_code=403,
				detail="You don't have permission to delete this account"
			)

		return self.delete_by_id(account_id)


class SocialGroupBaseManager(BaseManager['SocialGroup']):
	"""
	Custom query manager for SocialGroup model with additional methods.
	"""

	def __init__(self):
		# Use string literal to avoid circular import
		from ..social import SocialGroup as SG
		super().__init__(SG)

	def get_by_platform_group_id(
			self,
			platform: str,
			platform_group_id: str
	) -> Optional['SocialGroup']:
		"""Get a social group by a platform and platform group ID."""

		return self.get_queryset().filter(
			and_(
				self.model.platform == platform,
				self.model.platform_group_id == platform_group_id
			)
		).first()

	def get_user_groups(
			self,
			user_id: int,
			platform: Mapped[str] | None = None
	) -> list['SocialGroup']:
		"""
		Get all social groups for a user, optionally filtered by a platform.

		Args:
			user_id: ID of the user
			platform: Optional platform filter

		Returns:
			List of SocialGroup objects
		"""

		# Get all groups where the social account's user_id matches
		query = self.has(social_account={'user_id': user_id})

		if platform:
			query = query.filter(self.model.platform == platform)

		return query.all()

	def tracking(self) -> Query:
		"""Get groups that are being tracked."""
		return self.get_queryset().filter(self.model.is_tracking)
