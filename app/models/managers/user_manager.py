from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, Optional

from fastapi import HTTPException, status
from passlib.context import CryptContext

from app.core.hashing import get_password_hash, verify_password
from app.models.managers.base_manager import BaseManager
from app.types import ActionType, PlatformRoleType, can_assign_platform_role

if TYPE_CHECKING:
    from app.models.identity.user import User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
logger = logging.getLogger(__name__)


class UserManager(BaseManager):
    """Manager for User model operations."""

    def __init__(self):
        from app.models.identity.user import User

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
        user = await self.prefetch_related("role").get(id=user_id)
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
        # The owner's profile is not another administrator's to rewrite. The
        # owner may still edit their own name and email; `set_platform_role`
        # and the password routes guard the rest.
        if self._is_platform_owner(user) and (actor is None or actor.id != user_id):
            raise HTTPException(status_code=403, detail="Нельзя изменять профиль собственника платформы")
        if actor is not None:
            fresh = await User.objects.prefetch_related("role").get(id=actor.id, is_active=True)
            if fresh is None or (fresh.id != user_id and not fresh.is_platform_admin):
                raise HTTPException(status_code=403, detail="Profile update denied")

        return await self.update_by_id(user_id, **update_data)

    async def set_platform_role(self, user_id: int, role_code: PlatformRoleType, actor_id: int) -> Optional["User"]:
        """Assign a platform role the actor is allowed to hand out.

        The rule is the rank, not the badge: an actor assigns a role strictly
        below their own, and the table right that opened the form is checked by
        the caller's route, not here. So an `admin` assigns `operator` or
        `user`, the owner assigns those too, and nobody — owner included —
        mints a second owner or touches the owner's account.
        """
        from app.models import PlatformRole
        from app.models import User as PlatformUser

        actor = await PlatformUser.objects.prefetch_related("role").get(id=actor_id)
        if actor is None or not actor.is_active:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Platform console access required")
        if not actor.has_admin_access():
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Platform console access required")
        wanted = role_code.value if isinstance(role_code, PlatformRoleType) else str(role_code).strip().lower()
        if wanted == PlatformRoleType.SUPERUSER.value:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Роль собственника платформы назначается только миграцией",
            )
        if not can_assign_platform_role(actor, wanted):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Нельзя назначить роль выше своей",
            )
        if actor.id == user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Нельзя менять свою платформенную роль. Обратитесь к другому администратору платформы.",
            )
        target = await PlatformUser.objects.prefetch_related("role").get(id=user_id)
        if target is None:
            return None
        if self._is_platform_owner(target):
            # No console, API or CLI operation may demote or re-role the owner.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Нельзя менять платформенную роль собственника",
            )
        # The target must sit below the actor too: an admin does not demote a
        # peer by rewriting the peer's account when the row has drifted.
        if not can_assign_platform_role(actor, getattr(getattr(target, "role", None), "codename", None)):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Нельзя менять роль не ниже своей",
            )
        role = await PlatformRole.objects.get_by_codename(role_code)
        if role is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown platform role")
        updated = await self._apply_role_change(user_id, role.id)
        logger.info(
            "platform_role_changed actor_id=%s target_id=%s role=%s",
            actor_id,
            user_id,
            role.codename,
        )
        return updated

    async def _apply_role_change(self, user_id: int, role_id: int) -> Optional["User"]:
        """The write itself, after every guard has passed."""
        return await self.update_by_id(user_id, role_id=role_id)

    @staticmethod
    def _is_platform_owner(account: Optional["User"]) -> bool:
        """Ownership by role, not by `is_superuser`.

        `is_superuser` folds in `is_active`, which is right for *authority* — a
        suspended owner must not keep the keys — and wrong for *protection*: a
        suspended account is the last one somebody should be allowed to rewrite
        or delete.
        """
        codename = getattr(getattr(account, "role", None), "codename", None)
        return codename == PlatformRoleType.SUPERUSER

    async def delete_user(self, user_id: int) -> bool:
        """
        Delete a user by ID.

        The platform owner is not deletable from anywhere — console, API or a
        direct manager call. The database also refuses it (catalog rows hold an
        FK to the owner), but relying on that would make the refusal depend on
        whichever row happens to reference the account; the rule belongs here.

        Args:
                user_id: ID of the user to delete

        Returns:
                bool: True if deletion successful, False otherwise
        """
        from app.models import User

        target = await User.objects.prefetch_related("role").get(id=user_id)
        if self._is_platform_owner(target):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Нельзя удалить аккаунт собственника платформы",
            )
        return await self.delete_by_id(user_id)

    async def change_password(
        self,
        user_id: int,
        current_password: str,
        new_password: str,
        current_user: Optional["User"] = None,
        *,
        is_admin_reset: Optional[bool] = None,
    ) -> bool:
        """
        Change a user's password.

        An operator resetting *another* account's password says so with
        `is_admin_reset=True`: that form needs no current password, only the
        user-update grant. Every other caller is checked as a self-change and
        must know the current password, so a caller that forgets to name the
        actor cannot slip an empty password through.

        The platform owner's password is never reset by anyone else — not by an
        admin, not anonymously — before any privilege is evaluated.

        Args:
                user_id: ID of the user
                current_password: A current password for verification
                new_password: New password to set
                current_user: Optional user making the request
                is_admin_reset: The caller already proved it may reset another account

        Returns:
                bool: True if password was changed successfully
        """
        user = await self.prefetch_related("role").get(id=user_id)
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

        by_another = current_user is not None and current_user.id != user_id
        if self._is_platform_owner(user) and (current_user is None or current_user.id != user_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нельзя изменить пароль собственника платформы")
        if by_another and is_admin_reset is not False:
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
