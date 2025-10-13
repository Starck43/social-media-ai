from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm

from app.core.logger import logger
from app.services.user.auth import get_authenticated_user, oauth2_scheme, authenticate
from app.utils.token import create_access_token, create_tokens_pair
from app.models import User
from app.schemas.token import Token, TokenWithRefresh
from app.schemas.user import UserCreate

router = APIRouter(tags=["auth"])


@router.post("/login", response_model=TokenWithRefresh)
async def login_access_token(form_data: OAuth2PasswordRequestForm = Depends()) -> dict[str, Any]:
	"""
	OAuth2 compatible token login, get access and refresh tokens.

	— **username**: The user's username or email
	— **password**: The user's password

	Returns both access and refresh tokens for authenticated requests.
	The access token is short-lived, while the refresh token can be used to
	get a new access token when it expires.
	"""
	from app.core.hashing import verify_password

	try:
		# Authenticate the user
		user = await authenticate(
			username_or_email=form_data.username,
			password=form_data.password,
			password_hasher=verify_password
		)

		if not user:
			logger.warning(f"Failed login attempt for username or email: {form_data.username}")
			raise HTTPException(
				status_code=status.HTTP_400_BAD_REQUEST,
				detail="Incorrect username/email or password",
				headers={"WWW-Authenticate": "Bearer"},
			)

		# Generate both access and refresh tokens
		tokens = create_tokens_pair(subject=str(user.id))
		logger.info(f"Successful login for user: {user.username}")

		return tokens

	except HTTPException:
		# Re-raise HTTP exceptions (like invalid credentials)
		raise

	except Exception as e:
		logger.error(f"Unexpected error during login: {str(e)}", exc_info=True)
		raise HTTPException(
			status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
			detail="An error occurred during authentication"
		) from e


@router.post("/refresh-token", response_model=Token)
async def refresh_access_token(refresh_token: str = Depends(oauth2_scheme)) -> dict[str, Any]:
	"""
	Refresh an access token using a refresh token.

	— **Authorization**: Bearer {refresh_token}

	Returns a new access token if the refresh token is valid.
	"""

	try:
		# Verify the refresh token and get the user
		user = await get_authenticated_user(token=refresh_token, token_type="refresh")

		# Create a new access token
		access_token, expires_at = create_access_token(subject=str(user.id))

		logger.info(f"Refreshed access token for user: {user.username}")
		return {
			"access_token": access_token,
			"token_type": "bearer",
			"expires_at": expires_at
		}

	except Exception as e:
		logger.error(f"Error refreshing token: {str(e)}")
		raise HTTPException(
			status_code=status.HTTP_401_UNAUTHORIZED,
			detail="Invalid refresh token",
			headers={"WWW-Authenticate": "Bearer"},
		) from e


@router.post("/register", response_model=TokenWithRefresh)
async def create_user(new_user: UserCreate) -> dict[str, Any]:
	"""
	Register a new user and return access and refresh tokens.

	Args:
		new_user: User data including:
			— username: The user's username
			— email: The user's email
			— password: The user's password
			— first_name: (Optional) The user's first name
			— last_name: (Optional) The user's last name

	Returns:
		dict: A dictionary containing:
			— access_token (str): JWT access token
			— refresh_token (str): JWT refresh token
			— token_type (str): Always “bearer”
			— expires_at (datetime): When the access token expires

	Raises:
		HTTPException: If user with the same username or email already exists
	"""
	# Check if username already exists
	if await User.objects.get_by_username_or_email(new_user.username):
		raise HTTPException(
			status_code=status.HTTP_400_BAD_REQUEST,
			detail="Username already registered",
		)

	# Check if email already exists
	if new_user.email and await User.objects.exists(email=new_user.email):
		raise HTTPException(
			status_code=status.HTTP_400_BAD_REQUEST,
			detail="Email already registered",
		)

	try:
		# Convert Pydantic model to dict and remove None values
		user_data = new_user.model_dump(exclude_unset=True)
		user = await User.objects.create_user(**user_data)

		# Generate tokens for the new user
		tokens = create_tokens_pair(subject=str(user.id))
		return tokens
	except Exception as e:
		raise HTTPException(
			status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
			detail="Error creating user"
		) from e
