import logging
from datetime import datetime
from pathlib import Path

import sqladmin
from fastapi import APIRouter, Request, Form, HTTPException, Depends
from fastapi.params import Body
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette import status
from starlette.templating import Jinja2Templates

from app.admin.csrf import get_csrf_manager
from app.core.config import settings
from app.core.hashing import get_password_hash, generate_temporary_password, verify_password
from app.models import User
from app.services.user.auth import get_authenticated_user

# Configure logger
logger = logging.getLogger(__name__)

# Initialize rate limiter
limiter = Limiter(
	key_func=get_remote_address,
	storage_uri=settings.REDIS_URL if hasattr(settings, 'REDIS_URL') else None
)

# Disable rate limiting in debug mode
if settings.DEBUG:
	limiter.enabled = False

# Set up templates directory
sqladmin_path = Path(sqladmin.__file__).parent

# Создаем templates объект с обоими путями
template_dirs = [
	str(Path(__file__).parent.parent / "templates"),
	str(sqladmin_path / "templates")
]

templates = Jinja2Templates(directory=template_dirs)

router = APIRouter(tags=["admin"])


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
		message = "User not found or has insufficient permissions"
	elif error == "invalid_csrf":
		message = "Security token expired. Please try again."
	elif error == "email_required":
		message = "Email address is required"

	# Get the admin instance from the request
	admin = request.app.state.admin
	admin.title = "Reset Password"

	return templates.TemplateResponse(
		"sqladmin/user/reset_password.html",
		{
			"request": request,
			"csrf_token": csrf_token,
			"admin": admin,
			"message": message,
			"error": error,
		}
	)


@router.post("/admin/reset-password", name="reset_password")
@limiter.limit(settings.RESET_PASSWORD_RATE_LIMIT)
async def reset_password(
		request: Request,
		email: str = Form(None),
		csrf_token: str = Form(None)
):
	"""
	Handle password reset with rate limiting (disabled in development) and CSRF protection.
	"""
	# Check if this is an AJAX request
	is_ajax = request.headers.get('accept') == 'application/json'

	# Skip CSRF verification in development mode
	csrf_manager = get_csrf_manager(request)
	if not csrf_token or not csrf_manager.verify_token(csrf_token):
		logger.warning(f"Invalid CSRF token in password reset for email: {email}")
		if is_ajax:
			return JSONResponse(
				status_code=status.HTTP_403_FORBIDDEN,
				content={"error": "Invalid or expired CSRF token. Please refresh the page and try again."}
			)
		return RedirectResponse(
			"/admin/reset-password?error=invalid_csrf",
			status_code=status.HTTP_303_SEE_OTHER
		)

	if not email:
		error_msg = "Email is required"
		if is_ajax:
			return JSONResponse(
				status_code=status.HTTP_400_BAD_REQUEST,
				content={"error": error_msg}
			)
		return RedirectResponse(
			"/admin/reset-password?error=email_required",
			status_code=status.HTTP_303_SEE_OTHER
		)

	try:
		user = await User.objects.get(email=email)

		if not user:
			logger.warning(f"Reset password attempt for non-admin or non-existent email: {email}")
			return RedirectResponse(
				"/admin/reset-password?error=user_not_found",
				status_code=status.HTTP_303_SEE_OTHER
			)

		# Generate temporary password
		temporary_password = generate_temporary_password()
		user.hashed_password = get_password_hash(temporary_password)
		user.updated_at = datetime.now()

		user.save()

		# In production, you would send an email with a secure link
		logger.info(f"Temporary password for {email}: {temporary_password}")

		return RedirectResponse(
			"/admin/login?message=password_reset",
			status_code=status.HTTP_303_SEE_OTHER
		)

	except Exception as e:
		logger.error(f"Error in password reset for {email}: {str(e)}", exc_info=True)
		return RedirectResponse(
			f"/admin/reset-password?error=server_error",
			status_code=status.HTTP_303_SEE_OTHER
		)


@router.get("/admin/user/change-password/{user_id}", response_class=HTMLResponse)
async def change_password_form_path(
		request: Request,
		user_id: int,
):
	"""
	Render change-password form for a given user_id.
	If current_user is not superuser and tries to edit another user -> 403.
	"""
	# Check if user is authenticated
	token = request.session.get("token")
	if "token" not in request.session:
		return RedirectResponse(
			url=f"/admin/login?next=/admin/user/change-password/{user_id}",
			status_code=status.HTTP_303_SEE_OTHER
		)

	current_user = await get_authenticated_user(token)
	if not current_user:
		# Redirect to log in with next parameter to return here after login
		return RedirectResponse(
			url=f"/admin/login?next=/admin/user/change-password/{user_id}",
			status_code=status.HTTP_303_SEE_OTHER
		)

	# permission check
	if not current_user.is_active and current_user.id != int(user_id):
		raise HTTPException(status_code=403, detail="Permission denied")

	# Store the referer URL for redirect back after password change
	referer = request.headers.get('referer', '')

	if referer and '/admin/' in referer and '/admin/user/change-password/' not in referer:
		return_url = referer.split('?')[0]
		request.session[f"return_url_{user_id}"] = return_url
	else:
		# Try to get from a session or use default
		return_url = request.session.get(f"return_url_{user_id}", f"/admin/user/details/{user_id}")

	csrf_manager = get_csrf_manager(request)
	csrf_token = csrf_manager.generate_token()
	request.session["csrf_token"] = csrf_token

	# Get the admin instance from the request
	admin = request.app.state.admin
	admin.title = "Change Password"

	messages = request.session.pop("_messages", None)

	return templates.TemplateResponse(
		"sqladmin/user/change_password.html",
		{
			"request": request,
			"user": current_user,
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
			"return_url": return_url
		},
	)


@router.post("/admin/user/change-password/{user_id}", response_class=HTMLResponse)
@limiter.limit(settings.CHANGE_PASSWORD_RATE_LIMIT)
async def change_password_submit_path(
		request: Request,
		user_id: int,
		current_password: str = Form(...),
		new_password: str = Form(...),
		confirm_password: str = Form(...),
		csrf_token: str = Form(...),
):
	logger.info("POST request received for password change")

	# Check if user is authenticated
	token = request.session.get("token")
	current_user = await get_authenticated_user(token)

	if not token or not current_user:
		return RedirectResponse(
			url=f"/admin/login?next=/admin/user/change-password/{user_id}",
			status_code=status.HTTP_303_SEE_OTHER
		)

	if not current_user.is_active:
		raise HTTPException(status_code=403, detail="Permission denied")

	# Initialize messages list
	if "_messages" not in request.session:
		request.session["_messages"] = []

	# CSRF check
	csrf_manager = get_csrf_manager(request)
	if not csrf_token or not csrf_manager.verify_token(csrf_token):
		request.session["_messages"].append("Invalid or missing security token. Please try again.")
		return RedirectResponse(url=f"/admin/user/change-password/{user_id}", status_code=status.HTTP_303_SEE_OTHER)

	# Basic validation
	errors = []

	if not all([current_password, new_password, confirm_password]):
		errors.append("All password fields are required")

	if new_password != confirm_password:
		errors.append("Passwords do not match")

	if current_user.id != int(user_id):
		errors.append("User not found")

	# Check if new password is different from current
	if new_password == current_password:
		errors.append("New password must be different from current password")

	# Validate password complexity using the existing validator
	from app.core.validators import validate_password
	is_valid, complexity_error = validate_password(new_password)
	if not is_valid:
		errors.append(complexity_error)

	# Verify current password
	if not verify_password(current_password, current_user.hashed_password):
		errors.append("Current password is incorrect")

	if errors:
		request.session["_messages"].extend(errors)
		return RedirectResponse(url=f"/admin/user/change-password/{user_id}", status_code=status.HTTP_303_SEE_OTHER)

	# Perform the password update
	try:
		# Update password
		current_user.hashed_password = get_password_hash(new_password),
		current_user.updated_at = datetime.now()
		current_user.save()

		del request.session["_messages"]

		# Get the return URL from a hidden field or referer
		redirect_url = request.headers.get('referer', '')

		# Remove any query parameters
		if '?' in redirect_url:
			redirect_url = redirect_url.split('?')[0]

		# Don't redirect back to change password page
		if '/admin/user/change-password/' in redirect_url:
			# Try to get the original return URL from a session or default to user details
			redirect_url = request.session.get(f"return_url_{user_id}", f"/admin/user/details/{user_id}")

		# If no valid return URL, default to user list
		if not redirect_url or '/admin/' not in redirect_url:
			redirect_url = '/admin/user/list'

		response = RedirectResponse(
			redirect_url,
			status_code=status.HTTP_303_SEE_OTHER
		)

		# Add cache control headers
		response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
		response.headers["Pragma"] = "no-cache"
		response.headers["Expires"] = "0"

		return response

	except Exception as e:
		logger.error(f"Error changing password: {str(e)}", exc_info=True)
		request.session["_messages"].append("Server error. Please try again.")
		return RedirectResponse(url=f"/admin/user/change-password/{user_id}", status_code=303)


@router.post("/admin/verify-current-password")
async def verify_current_password(
		request: Request,
		data: dict = Body(...)
):
	"""AJAX endpoint to verify current password"""
	token = request.session.get("token")
	if not token:
		return {"valid": False}

	current_user = await get_authenticated_user(token)
	if not current_user:
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
		return {"valid": False, "error": "An error occurred while verifying password"}


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
		return RedirectResponse(
			url=f"/admin/login?next=/dashboard",
			status_code=status.HTTP_303_SEE_OTHER
		)
	
	try:
		# Get user from session token
		current_user = await get_authenticated_user(token=token, token_type="access")
		
		if not current_user or not current_user.is_active:
			request.session.clear()
			return RedirectResponse(
				url=f"/admin/login?next=/dashboard",
				status_code=status.HTTP_303_SEE_OTHER
			)
		
		logger.info(f"User {current_user.username} accessing analytics dashboard")
		
		return templates.TemplateResponse(
			"analytics_dashboard.html",
			{"request": request, "user": current_user}
		)
		
	except Exception as e:
		logger.error(f"Dashboard authentication error: {e}")
		request.session.clear()
		return RedirectResponse(
			url=f"/admin/login?next=/dashboard",
			status_code=status.HTTP_303_SEE_OTHER
		)


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
		return RedirectResponse(
			url=f"/admin/login?next=/dashboard/topic-chains",
			status_code=status.HTTP_303_SEE_OTHER
		)
	
	try:
		current_user = await get_authenticated_user(token=token, token_type="access")
		
		if not current_user or not current_user.is_active:
			request.session.clear()
			return RedirectResponse(
				url=f"/admin/login?next=/dashboard/topic-chains",
				status_code=status.HTTP_303_SEE_OTHER
			)
		
		logger.info(f"User {current_user.username} accessing topic chains dashboard")
		
		return templates.TemplateResponse(
			"topic_chains_dashboard.html",
			{"request": request, "user": current_user}
		)
		
	except Exception as e:
		logger.error(f"Topic chains dashboard authentication error: {e}")
		request.session.clear()
		return RedirectResponse(
			url=f"/admin/login?next=/dashboard/topic-chains",
			status_code=status.HTTP_303_SEE_OTHER
		)
