from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from pydantic import ValidationError
from starlette import status
from starlette.responses import RedirectResponse

from app.core.config import settings
from app.models import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=settings.TOKEN_URL, auto_error=False)


async def get_authenticated_user(
		token: str = Depends(oauth2_scheme),
		token_type: str = "access"
) -> 'User':
	"""
	Get the current authenticated user using a token.

	Args:
		token: JWT token
		token_type: Expected token type ('access' or 'refresh')

	Returns:
		User object if token is valid

	Raises:
		HTTPException: If token is invalid or user not found
	"""
	credentials_exception = HTTPException(
		status_code=status.HTTP_401_UNAUTHORIZED,
		detail="Could not validate credentials",
		headers={"WWW-Authenticate": "Bearer"},
	)
	
	# Check if token is None (from auto_error=False)
	if token is None:
		raise credentials_exception

	try:
		payload = jwt.decode(
			token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
		)

		# Verify token type
		if payload.get("type") != token_type:
			raise credentials_exception

		user_id: str = payload.get("sub")
		if user_id is None:
			raise credentials_exception

	except (JWTError, ValidationError) as e:
		raise credentials_exception from e

	user = await User.objects.get(id=int(user_id))
	if user is None or not user.is_active:
		raise credentials_exception

	return user


async def authenticate(username_or_email: str, password: str, password_hasher: callable) -> User:
	"""Authenticate a user by a username/email and password.

	Args:
		username_or_email: Username or email of the user
		password: Plain text password
		password_hasher: Callable that verifies the password against the hash

	Returns:
		User object if authentication succeeds, None otherwise

	Raises:
		HTTPException: If user is not found, password is incorrect, or user is inactive
	"""
	if not username_or_email or not password:
		raise HTTPException(
			status_code=status.HTTP_400_BAD_REQUEST,
			detail="Username/email and password are required"
		)

	user = await User.objects.get_by_username_or_email(username_or_email)

	if not user:
		# Don't reveal whether the user exists or not for security
		password_hasher("dummy_password", "dummy_hash")  # Prevent timing attacks
		raise HTTPException(
			status_code=status.HTTP_400_BAD_REQUEST,
			detail="Incorrect username/email or password",
		)

	if not password_hasher(password, user.hashed_password):
		raise HTTPException(
			status_code=status.HTTP_400_BAD_REQUEST,
			detail="Incorrect username/email or password",
		)

	if not user.is_active:
		raise HTTPException(
			status_code=status.HTTP_400_BAD_REQUEST,
			detail="Inactive user"
		)

	return user


async def get_session_user(request: Request) -> 'User':
	"""
	Get authenticated user from session (for web pages like dashboard).
	
	Args:
		request: Starlette Request object with session
	
	Returns:
		User object if authenticated
	
	Raises:
		HTTPException: Redirects to login if not authenticated
	"""
	token = request.session.get("token")
	
	if not token:
		# Redirect to admin login with next parameter
		raise HTTPException(
			status_code=status.HTTP_307_TEMPORARY_REDIRECT,
			headers={"Location": f"/admin/login?next={request.url.path}"}
		)
	
	try:
		# Reuse existing authentication logic
		user = await get_authenticated_user(token=token, token_type="access")
		
		if not user or not user.is_active:
			# Clear invalid session
			request.session.clear()
			raise HTTPException(
				status_code=status.HTTP_307_TEMPORARY_REDIRECT,
				headers={"Location": f"/admin/login?next={request.url.path}"}
			)
		
		return user
		
	except (JWTError, ValidationError, HTTPException):
		# Invalid token - clear session and redirect
		request.session.clear()
		raise HTTPException(
			status_code=status.HTTP_307_TEMPORARY_REDIRECT,
			headers={"Location": f"/admin/login?next={request.url.path}"}
		)
