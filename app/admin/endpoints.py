import logging
from datetime import datetime
from pathlib import Path

import sqladmin
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.params import Body
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette import status
from starlette.templating import Jinja2Templates

from app.admin.authorization import require_admin_perm
from app.admin.csrf import get_csrf_manager
from app.core.config import settings
from app.core.hashing import generate_temporary_password, get_password_hash, verify_password
from app.models import Tenant, User
from app.services.user.auth import get_authenticated_user
from app.types import ActionType

# Configure logger
logger = logging.getLogger(__name__)

# Initialize rate limiter
limiter = Limiter(
    key_func=get_remote_address, storage_uri=settings.REDIS_URL if hasattr(settings, "REDIS_URL") else None
)

# Disable rate limiting in debug mode
if settings.DEBUG:
    limiter.enabled = False

# Set up templates directory
sqladmin_path = Path(sqladmin.__file__).parent

# Создаем templates объект с обоими путями
template_dirs = [str(Path(__file__).parent.parent / "templates"), str(sqladmin_path / "templates")]

templates = Jinja2Templates(directory=template_dirs)

router = APIRouter(tags=["admin"])


@router.post("/admin/llm-model/test-real", name="llm_model_test_real")
async def llm_model_test_real(
    request: Request,
    pks: str = Form(...),
    prompt: str = Form(""),
    tenant_id: int = Form(...),
    csrf_token: str = Form(""),
):
    """Retire the old paid route without silently translating or replaying it."""
    if not get_csrf_manager(request).verify_token(csrf_token):
        raise HTTPException(status_code=403, detail="Недействительный CSRF токен")
    token = request.session.get("token")
    if not token:
        raise HTTPException(status_code=401, detail="Требуется вход в панель")
    user = await get_authenticated_user(token=token, token_type="access")
    if user is not None:
        user = await User.objects.prefetch_related("role.permissions").get(id=user.id)
    if user is None or not user.is_active or not user.is_platform_admin:
        raise HTTPException(status_code=403, detail="Нужны активные права администратора платформы")
    raise HTTPException(status_code=410, detail="Open the model test page and confirm a new probe explicitly")


@router.get("/admin/reset-password", name="reset_password_page")
async def reset_password_page(request: Request):
    csrf_manager = get_csrf_manager(request)
    csrf_token = csrf_manager.generate_token()

    # Store the token in the session
    request.session["csrf_token"] = csrf_token
    request.session.setdefault("_session_modified", True)

    # Get any error message from query params
    error = request.query_params.get("error")
    message = None
    if error == "user_not_found":
        message = "Пользователь не найден или недостаточно прав"
    elif error == "invalid_csrf":
        message = "Срок действия токена безопасности истёк. Попробуйте ещё раз."
    elif error == "email_required":
        message = "Необходимо указать электронную почту"

    # Get the admin instance from the request
    admin = request.app.state.admin
    admin.title = "Сброс пароля"

    return templates.TemplateResponse(
        request,
        "sqladmin/user/reset_password.html",
        {
            "csrf_token": csrf_token,
            "admin": admin,
            "message": message,
            "error": error,
        },
    )


@router.post("/admin/reset-password", name="reset_password")
@limiter.limit(settings.RESET_PASSWORD_RATE_LIMIT)
async def reset_password(request: Request, email: str = Form(None), csrf_token: str = Form(None)):
    """
    Handle password reset with rate limiting (disabled in development) and CSRF protection.
    """
    # There is no verified delivery flow for generated credentials. Keep the
    # old public recovery URL inert; operators use the authenticated password form.
    raise HTTPException(status_code=status.HTTP_410_GONE, detail="Use authenticated password administration")


@router.get("/admin/user/change-password/{user_id}", response_class=HTMLResponse)
async def change_password_form_path(
    request: Request,
    user_id: int,
):
    """
    Render change-password form for a given user_id.
    Only an operator may use this panel; changing another account also requires
    the user-update grant.
    """
    # Check if user is authenticated
    token = request.session.get("token")
    if "token" not in request.session:
        return RedirectResponse(
            url=f"/admin/login?next=/admin/user/change-password/{user_id}", status_code=status.HTTP_303_SEE_OTHER
        )

    current_user = await get_authenticated_user(token)
    if current_user is not None:
        current_user = await User.objects.prefetch_related("role.permissions").get(id=current_user.id)
    if not current_user:
        # Redirect to log in with next parameter to return here after login
        return RedirectResponse(
            url=f"/admin/login?next=/admin/user/change-password/{user_id}", status_code=status.HTTP_303_SEE_OTHER
        )
    if not current_user.has_admin_access():
        raise HTTPException(status_code=403, detail="Доступ запрещён")

    # Permission: an operator may always change *their own* password; changing
    # someone else's is a change to that user record and needs the right for it.
    is_admin_reset = current_user.id != int(user_id)
    if is_admin_reset:
        if not current_user.is_platform_admin:
            raise HTTPException(status_code=403, detail="Platform administrator required for password reset")
        await require_admin_perm(request, "user", ActionType.UPDATE)

    target_user = await User.objects.get(id=int(user_id))
    if not target_user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    # Store the referer URL for redirect back after password change
    referer = request.headers.get("referer", "")

    if referer and "/admin/" in referer and "/admin/user/change-password/" not in referer:
        return_url = referer.split("?")[0]
        request.session[f"return_url_{user_id}"] = return_url
    else:
        # Try to get from a session or use default
        return_url = request.session.get(f"return_url_{user_id}", f"/admin/user/details/{user_id}")

    csrf_manager = get_csrf_manager(request)
    csrf_token = csrf_manager.generate_token()
    request.session["csrf_token"] = csrf_token

    # Get the admin instance from the request
    admin = request.app.state.admin
    admin.title = "Смена пароля"

    messages = request.session.pop("_messages", None)

    return templates.TemplateResponse(
        request,
        "sqladmin/user/change_password.html",
        {
            "user": current_user,
            "target_user": target_user,
            "is_admin_reset": is_admin_reset,
            "csrf_token": csrf_token,
            "admin": admin,
            "messages": messages,
            "password_settings": {
                "PASSWORD_MIN_LENGTH": settings.PASSWORD_MIN_LENGTH,
                "PASSWORD_REQUIRE_UPPERCASE": settings.PASSWORD_REQUIRE_UPPERCASE,
                "PASSWORD_REQUIRE_LOWERCASE": settings.PASSWORD_REQUIRE_LOWERCASE,
                "PASSWORD_REQUIRE_NUMBERS": settings.PASSWORD_REQUIRE_NUMBERS,
                "PASSWORD_REQUIRE_SPECIAL": settings.PASSWORD_REQUIRE_SPECIAL,
            },
            "return_url": return_url,
        },
    )


@router.post("/admin/user/change-password/{user_id}", response_class=HTMLResponse)
@limiter.limit(settings.CHANGE_PASSWORD_RATE_LIMIT)
async def change_password_submit_path(
    request: Request,
    user_id: int,
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    csrf_token: str = Form(...),
    current_password: str = Form(None),
):
    logger.info("POST request received for password change")

    # Check if user is authenticated
    token = request.session.get("token")
    current_user = await get_authenticated_user(token)
    if current_user is not None:
        current_user = await User.objects.prefetch_related("role.permissions").get(id=current_user.id)

    if not token or not current_user:
        return RedirectResponse(
            url=f"/admin/login?next=/admin/user/change-password/{user_id}", status_code=status.HTTP_303_SEE_OTHER
        )

    if not current_user.is_active:
        raise HTTPException(status_code=403, detail="Доступ запрещён")
    if not current_user.has_admin_access():
        raise HTTPException(status_code=403, detail="Доступ запрещён")

    # Initialize messages list
    if "_messages" not in request.session:
        request.session["_messages"] = []

    # CSRF check
    csrf_manager = get_csrf_manager(request)
    if not csrf_token or not csrf_manager.verify_token(csrf_token):
        request.session["_messages"].append(
            "Отсутствующий или недействительный токен безопасности. Попробуйте ещё раз."
        )
        return RedirectResponse(url=f"/admin/user/change-password/{user_id}", status_code=status.HTTP_303_SEE_OTHER)

    # An operator may always change their own password; changing someone
    # else's requires the UPDATE right on users and skips the current-password
    # check (the admin does not know it).
    is_admin_reset = current_user.id != int(user_id)
    target_id = int(user_id)
    if is_admin_reset:
        if not current_user.is_platform_admin:
            raise HTTPException(status_code=403, detail="Platform administrator required for password reset")
        await require_admin_perm(request, "user", ActionType.UPDATE)
        target_user = await User.objects.get(id=target_id)
        if not target_user:
            request.session["_messages"].append("Пользователь не найден")
            return RedirectResponse(url=f"/admin/user/change-password/{user_id}", status_code=status.HTTP_303_SEE_OTHER)

    # Basic validation
    errors = []

    if not is_admin_reset and not current_password:
        errors.append("Заполните все поля пароля")

    if not all([new_password, confirm_password]):
        errors.append("Заполните все поля пароля")

    if new_password != confirm_password:
        errors.append("Пароли не совпадают")

    # Check if new password is different from current (self-change only)
    if not is_admin_reset and new_password == current_password:
        errors.append("Новый пароль должен отличаться от текущего")

    # Validate password complexity using the existing validator
    from app.core.validators import validate_password

    is_valid, complexity_error = validate_password(new_password)
    if not is_valid:
        errors.append(complexity_error)

    # Verify current password (self-change only)
    if not is_admin_reset and not verify_password(current_password, current_user.hashed_password):
        errors.append("Текущий пароль неверен")

    if errors:
        request.session["_messages"].extend(errors)
        return RedirectResponse(url=f"/admin/user/change-password/{user_id}", status_code=status.HTTP_303_SEE_OTHER)

    # Perform the password update
    try:
        # Update password
        await User.objects.update_by_id(
            target_id,
            hashed_password=get_password_hash(new_password),
            updated_at=datetime.now(),
        )

        del request.session["_messages"]

        # Get the return URL from a hidden field or referer
        redirect_url = request.headers.get("referer", "")

        # Remove any query parameters
        if "?" in redirect_url:
            redirect_url = redirect_url.split("?")[0]

        # Don't redirect back to change password page
        if "/admin/user/change-password/" in redirect_url:
            # Try to get the original return URL from a session or default to user details
            redirect_url = request.session.get(f"return_url_{user_id}", f"/admin/user/details/{user_id}")

        # If no valid return URL, default to user list
        if not redirect_url or "/admin/" not in redirect_url:
            redirect_url = "/admin/user/list"

        response = RedirectResponse(redirect_url, status_code=status.HTTP_303_SEE_OTHER)

        # Add cache control headers
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"

        return response

    except Exception as e:
        logger.error(f"Error changing password: {str(e)}", exc_info=True)
        request.session["_messages"].append("Ошибка сервера. Попробуйте ещё раз.")
        return RedirectResponse(url=f"/admin/user/change-password/{user_id}", status_code=303)


@router.post("/admin/verify-current-password")
async def verify_current_password(request: Request, data: dict = Body(...)):
    """AJAX endpoint to verify current password"""
    token = request.session.get("token")
    if not token:
        return {"valid": False}

    authenticated = await get_authenticated_user(token)
    current_user = (
        await User.objects.prefetch_related("role.permissions").get(id=authenticated.id)
        if authenticated is not None
        else None
    )
    if not current_user:
        return {"valid": False}
    if not current_user.has_admin_access():
        return {"valid": False}

    current_password = data.get("current_password")
    if not current_password:
        return {"valid": False}

    try:
        # Get fresh user data
        user = await User.objects.get(id=current_user.id)
        is_valid = verify_password(current_password, user.hashed_password)
        return {"valid": is_valid}
    except Exception as e:
        logger.error(f"Error verifying password: {str(e)}", exc_info=True)
        return {"valid": False, "error": "Произошла ошибка при проверке пароля"}


@router.get("/dashboard", response_class=HTMLResponse, name="analytics_dashboard")
async def analytics_dashboard(request: Request):
    """
    Analytics Dashboard - главная страница с агрегированной аналитикой.

    Требует аутентификации через session (из админки).
    Если не залогинен - редиректит на /admin/login

    Отображает:
    - Тренды тональности
    - Топ темы
    - LLM статистику и затраты
    - Контент-микс
    - Метрики вовлеченности
    """
    # Check session authentication
    token = request.session.get("token")

    if not token:
        # Redirect to admin login with next parameter
        return RedirectResponse(url=f"/admin/login?next=/dashboard", status_code=status.HTTP_303_SEE_OTHER)

    # The dashboard reads the analytics model from outside the sqladmin view tree,
    # so it asks the console the same question. Placed before the `try` on
    # purpose: a refused operator has to get a 403, not the catch-all below,
    # which would sign them out and send them to the login form.
    await require_admin_perm(request, "aianalytics", ActionType.VIEW)

    try:
        # Get user from session token
        current_user = await get_authenticated_user(token=token, token_type="access")

        if not current_user or not current_user.is_active:
            request.session.clear()
            return RedirectResponse(url=f"/admin/login?next=/dashboard", status_code=status.HTTP_303_SEE_OTHER)

        logger.info(f"User {current_user.username} accessing analytics dashboard")

        return templates.TemplateResponse(request, "analytics_dashboard.html", {"user": current_user})

    except Exception as e:
        logger.error(f"Dashboard authentication error: {e}")
        request.session.clear()
        return RedirectResponse(url=f"/admin/login?next=/dashboard", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/dashboard/topic-chains", response_class=HTMLResponse, name="topic_chains_dashboard")
async def topic_chains_dashboard(request: Request):
    """
    Topic Chains Dashboard - просмотр цепочек тем с эволюцией.

    Требует аутентификации через session (из админки).

    Отображает:
    - Список цепочек тем
    - Эволюцию тем во времени
    - Ссылки на источники в соцсетях
    - Временную шкалу анализов
    """
    # Check session authentication
    token = request.session.get("token")

    if not token:
        return RedirectResponse(url=f"/admin/login?next=/dashboard/topic-chains", status_code=status.HTTP_303_SEE_OTHER)

    # Same as the analytics dashboard above — and for the same reason it sits
    # outside the `try`: the topic chains are built from the analyses, so they
    # need the same right on the same model, and a refusal must stay a refusal.
    await require_admin_perm(request, "aianalytics", ActionType.VIEW)

    try:
        current_user = await get_authenticated_user(token=token, token_type="access")

        if not current_user or not current_user.is_active:
            request.session.clear()
            return RedirectResponse(
                url=f"/admin/login?next=/dashboard/topic-chains", status_code=status.HTTP_303_SEE_OTHER
            )

        logger.info(f"User {current_user.username} accessing topic chains dashboard")

        return templates.TemplateResponse(request, "topic_chains_dashboard.html", {"user": current_user})

    except Exception as e:
        logger.error(f"Topic chains dashboard authentication error: {e}")
        request.session.clear()
        return RedirectResponse(url=f"/admin/login?next=/dashboard/topic-chains", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/admin/llm-model/test", response_class=HTMLResponse)
async def submit_llm_model_test(request: Request):
    """Explicit operator-only model test; GET action URLs remain read-only."""
    user = await require_admin_perm(request, "llmmodel", ActionType.CONFIGURE)
    form = await request.form()
    token = form.get("csrf_token")
    if not isinstance(token, str) or not get_csrf_manager(request).verify_token(token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
    if form.get("test_type") == "real" and form.get("confirm_real") != "yes":
        raise HTTPException(status_code=422, detail="Confirm the paid probe explicitly")
    from app.admin.actions import LLMModelActions

    if form.get("test_type") == "real":
        if not settings.PERSONAL_BILLING_ENABLED:
            raise HTTPException(status_code=409, detail="Личный учёт расходов ещё не включён")
        if not user.is_platform_admin:
            raise HTTPException(status_code=403, detail="Personal admin probes require a platform administrator")
        try:
            tenant_id = int(str(form.get("tenant_id", "")))
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="Select an active execution workspace") from None
        if tenant_id <= 0:
            raise HTTPException(status_code=422, detail="Select an active execution workspace")
        tenant = await Tenant.objects.get(id=tenant_id)
        if tenant is None or not tenant.is_active:
            raise HTTPException(status_code=422, detail="Select an active execution workspace")

        from app.core.permissions import permission_scope
        from app.core.tenant_context import tenant_scope

        # Workspace is explicit; payer identity is never accepted from the form.
        request.state.probe_tenant_id = tenant.id
        with tenant_scope(tenant.id), permission_scope(user):
            return await LLMModelActions.submit_test(
                request.app.state.admin, request, str(form.get("model_id", "")), "real", ""
            )

    return await LLMModelActions.submit_test(
        request.app.state.admin,
        request,
        str(form.get("model_id", "")),
        str(form.get("test_type", "")),
        str(form.get("prompt", "")),
    )
