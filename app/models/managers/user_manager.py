from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, Optional

from fastapi import HTTPException, status
from passlib.context import CryptContext

from app.core.hashing import get_password_hash, verify_password
from app.models.managers.base_manager import BaseManager
from app.types import ActionType, PlatformRoleType

if TYPE_CHECKING:
    from app.models.user import User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
logger = logging.getLogger(__name__)


class UserManager(BaseManager):
    """Manager for User model operations."""

    def __init__(self):
        from app.models.user import User

        super().__init__(User)

    async def get_by_email(self, email: str) -> Optional["User"]:
        return await self.get(email=email)

    async def get_by_username_or_email(self, username_or_email: str) -> Optional["User"]:
        """Get user by a username or email (case-insensitive).

        Args:
                username_or_email: Username or email address to search for

        Returns:
                User object if found, None otherwise
        """
        if not username_or_email:
            return None

        # Check if the input looks like an email
        is_email = "@" in username_or_email

        if is_email:
            return await self.filter(email=username_or_email).first()
        else:
            return await self.filter(username=username_or_email).first()

    async def get_active_users(self, skip: int = 0, limit: int = 100) -> list["User"]:
        """Get paginated list of active users."""
        return await self.filter(is_active=True).offset(skip).limit(limit)

    async def create_user(self, username: str, password: str, **extra_data) -> "User":
        """Create a new user with hashed password."""
        if not all([username, password]):
            raise ValueError("Username, email and password are required")

        return await self.create(username=username, hashed_password=get_password_hash(password), **extra_data)

    async def update_user(
        self, user_id: int, update_data: dict[str, Any], current_user: Optional["User"] = None
    ) -> Optional["User"]:
        """
        Update user data.

        Args:
                user_id: ID of the user to update
                update_data: Dictionary with fields to update
                current_user: Optional user making the request (for permission checks)

        Returns:
                Updated User object or None if not found
        """
        user = await self.get(id=user_id)
        if not user:
            return None

        from app.core.permissions import get_current_user
        from app.core.tenant_context import is_bypass
        from app.models import User

        actor = current_user or get_current_user()
        if actor is None and not is_bypass():
            raise HTTPException(status_code=403, detail="Authenticated actor required")
        if set(update_data) - {"username", "email"}:
            raise HTTPException(status_code=400, detail="Unsupported profile field")
        if actor is not None:
            fresh = await User.objects.prefetch_related("role").get(id=actor.id, is_active=True)
            if fresh is None or (fresh.id != user_id and not fresh.is_platform_admin):
                raise HTTPException(status_code=403, detail="Profile update denied")

        return await self.update_by_id(user_id, **update_data)

    async def set_platform_role(self, user_id: int, role_code: PlatformRoleType, actor_id: int) -> Optional["User"]:
        """Set a user's platform role after rechecking the actor from the DB."""
        from app.models import PlatformRole
        from app.models import User as PlatformUser

        actor = await PlatformUser.objects.prefetch_related("role").get(id=actor_id)
        if actor is None or not actor.is_active:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Platform admin required")
        if role_code == PlatformRoleType.SUPERUSER or str(role_code) == PlatformRoleType.SUPERUSER.value:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Роль собственника платформы назначается только миграцией",
            )
        if not actor.is_platform_admin:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Platform admin required")
        if actor.id == user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Нельзя менять свою платформенную роль. Обратитесь к другому администратору платформы.",
            )
        target = await PlatformUser.objects.prefetch_related("role").get(id=user_id)
        if target is None:
            return None
        if target.is_superuser:
            # No console, API or CLI operation may demote or re-role the owner.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Нельзя менять платформенную роль собственника",
            )
        role = await PlatformRole.objects.get_by_codename(role_code)
        if role is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown platform role")
        updated = await self.update_by_id(user_id, role_id=role.id)
        logger.info(
            "platform_role_changed actor_id=%s target_id=%s role=%s",
            actor_id,
            user_id,
            role.codename,
        )
        return updated

    async def delete_user(self, user_id: int) -> bool:
        """
        Delete a user by ID.

        Args:
                user_id: ID of the user to delete

        Returns:
                bool: True if deletion successful, False otherwise
        """
        return await self.delete_by_id(user_id)

    async def change_password(
        self, user_id: int, current_password: str, new_password: str, current_user: Optional["User"] = None
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
        user = await self.get(id=user_id)
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

        # Only allow admins, or the user themselves to change password
        if current_user and current_user.id != user_id:
            from app.models import User

            actor = await User.objects.prefetch_related("role.permissions").get(id=current_user.id)
            if actor is None or not actor.has_admin_access() or not actor.has_perm_for("user", ActionType.UPDATE):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not enough permissions")

        # Verify current password
        if not verify_password(current_password, user.hashed_password):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Incorrect current password")

        # Update password
        await self.update_by_id(user_id, hashed_password=pwd_context.hash(new_password))
        return True
