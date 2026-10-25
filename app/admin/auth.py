import logging
from typing import Optional

from fastapi import HTTPException, Request
from sqladmin.authentication import AuthenticationBackend

from app.admin.csrf import CSRFTokenManager
from app.core.config import settings
from app.core.hashing import verify_password
from app.models import User
from app.services.user.auth import get_authenticated_user
from app.services.user.login_protection import (
    LoginLocked,
    clear_login_failures,
    client_address,
    guard_login,
    record_login_failure,
    verify_dummy_password,
)
from app.utils.token import create_access_token

logger = logging.getLogger(__name__)


class AdminAuthBackend(AuthenticationBackend):
    """Authentication backend for SQLAdmin with CSRF protection."""

    def __init__(self, secret_key: Optional[str] = None, csrf_manager: Optional[CSRFTokenManager] = None):
        super().__init__(secret_key=secret_key or settings.SECRET_KEY)
        self.csrf_manager = csrf_manager
        logger.info("✅ Authentication backend initialized")

    async def login(self, request: Request) -> bool:
        """Handle login form submission."""
        form = await request.form()
        username: str = form.get("username")
        password: str = form.get("password")
        address = client_address(request)

        logger.info("🔑 Admin login attempt username=%s", username)

        csrf_manager = getattr(request.app.state, "csrf_manager", self.csrf_manager)
        csrf_token = form.get("csrf_token")

        if not csrf_manager or not csrf_manager.verify_token(csrf_token):
            logger.warning("❌ Invalid CSRF token in login attempt")
            request.session["login_error"] = "Недействительный токен безопасности"
            return False

        if not username or not password:
            request.session["login_error"] = "Введите имя пользователя и пароль"
            return False

        # The budget is shared with the web and API login pages and stored in
        # the database, so a new cookie does not give the visitor a fresh try.
        try:
            await guard_login(username, address)
        except LoginLocked:
            logger.warning("Admin login refused by the shared throttle")
            request.session["login_error"] = "Неверное имя пользователя или пароль"
            return False

        try:
            user = await User.objects.get_by_username_or_email(username)
            if user and verify_password(password, user.hashed_password) and user.is_active:
                user = await User.objects.prefetch_related("role.permissions").get(id=user.id)
                if user is None or not user.has_admin_access():
                    request.session["login_error"] = "Нет доступа к панели управления"
                    return False
                access_token, _ = create_access_token(subject=str(user.id))
                request.session.update(
                    {
                        "token": access_token,
                        "user_id": str(user.id),
                        "username": user.username,
                    }
                )

                if "login_error" in request.session:
                    del request.session["login_error"]

                await clear_login_failures(username)
                return True
            if user is None:
                # Unknown account: spend the same verification work as a real one.
                await verify_dummy_password(password)
        except Exception as e:
            logger.error("Login error: %s", e)

        await record_login_failure(username, address)
        request.session["login_error"] = "Неверное имя пользователя или пароль"
        return False

    async def logout(self, request: Request) -> bool:
        """Clear session on logout."""
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> bool:
        """Check if user is authenticated via session token.

        Only active platform operators and administrators may enter. Model and
        action grants are checked separately against the verified identity.
        """
        token = request.session.get("token")
        if not token:
            return False
        try:
            user = await get_authenticated_user(token=token)
            if user is None or not user.is_active:
                request.session.clear()
                return False
            loaded = await User.objects.prefetch_related("role.permissions").get(id=user.id)
            if loaded is None or not loaded.has_admin_access():
                request.session.clear()
                return False
            request.state.admin_user = loaded
            request.session["user_id"] = str(loaded.id)
            request.session["username"] = loaded.username
            return True
        except HTTPException:
            request.session.clear()
            return False
