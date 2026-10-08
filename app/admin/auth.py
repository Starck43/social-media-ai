import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Request, HTTPException
from sqladmin.authentication import AuthenticationBackend

from app.admin.csrf import CSRFTokenManager
from app.core.config import settings
from app.core.hashing import verify_password
from app.models import User
from app.services.user.auth import get_authenticated_user
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

        try:
            # Check login attempts and lockout
            await self.check_login_lockout(request, username)

            user = await User.objects.get_by_username_or_email(username)
            if user and verify_password(password, user.hashed_password) and user.is_active:
                access_token, _ = create_access_token(subject=str(user.id))
                request.session.update({
                    "token": access_token,
                    "user_id": str(user.id),
                    "username": user.username,
                })

                if "login_error" in request.session:
                    del request.session["login_error"]

                # Reset login attempts on successful login
                attempt_key = f"login_attempts:{username}"
                if attempt_key in request.session:
                    del request.session[attempt_key]

                return True
        except Exception as e:
            logger.error("Login error: %s", e)
            # Track failed attempt
            await self.track_login_attempt(request, username)

        request.session["login_error"] = "Неверное имя пользователя или пароль"
        return False

    async def logout(self, request: Request) -> bool:
        """Clear session on logout."""
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> bool:
        """Check if user is authenticated via session token.

        Only superusers and roles with at least one model `view` permission may
        enter the operator console; the role and its permissions are eager-
        loaded so the check and the later per-model authorization can read them.
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
            return True
        except HTTPException:
            request.session.clear()
            return False

    @staticmethod
    async def track_login_attempt(request: Request, username: str):
        if settings.DEBUG:
            return

        attempt_key = f"login_attempts:{username}"
        current_attempts = request.session.get(attempt_key, 0)
        request.session[attempt_key] = current_attempts + 1

        if current_attempts >= settings.MAX_LOGIN_ATTEMPTS:
            lockout_key = f"login_lockout:{username}"
            request.session[lockout_key] = datetime.now().isoformat()
            raise HTTPException(status_code=429, detail="Слишком много попыток входа")

    @staticmethod
    async def check_login_lockout(request: Request, username: str):
        if settings.DEBUG:
            return

        lockout_key = f"login_lockout:{username}"
        lockout_time = request.session.get(lockout_key)
        if lockout_time:
            lockout_time = datetime.fromisoformat(lockout_time)
            if datetime.now() < lockout_time + timedelta(minutes=settings.LOGIN_TIMEOUT_MINUTES):
                raise HTTPException(status_code=429, detail="Учётная запись временно заблокирована")
