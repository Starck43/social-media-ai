from datetime import timedelta, datetime, timezone
from typing import Any, Optional

from jose import jwt

from app.core.config import settings


def create_token(
		subject: str | Any,
		token_type: str = "access",
		expires_delta: Optional[timedelta] = None
) -> tuple[str, datetime]:
	"""
	Create a JWT token (access or refresh).

	Args:
		subject: The subject of the token (user ID)
		token_type: Type of token ('access' or 'refresh')
		expires_delta: Optional expiration time delta

	Returns:
		Tuple of (encoded_token, expiration_datetime)
	"""
	now = datetime.now(timezone.utc)

	if expires_delta:
		expire = now + expires_delta
	else:
		if token_type == "refresh":
			# Refresh token expires in 30 days
			expire = now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
		else:
			# Access token uses environment-specific expiration
			token_expire_minutes = settings.get_token_expire_minutes()
			expire = now + timedelta(minutes=token_expire_minutes)

	to_encode = {
		"exp": expire,
		"sub": str(subject),
		"type": token_type
	}

	encoded_jwt = jwt.encode(
		to_encode,
		settings.SECRET_KEY,
		algorithm=settings.ALGORITHM
	)

	return encoded_jwt, expire


def create_access_token(subject: str | Any) -> tuple[str, datetime]:
	"""Create an access token."""
	return create_token(subject, "access")


def create_refresh_token(subject: str | Any) -> tuple[str, datetime]:
	"""Create a refresh token."""
	return create_token(subject, "refresh")


def create_tokens_pair(subject: str | Any) -> dict:
	"""Create both access and refresh tokens."""
	access_token, access_expires = create_access_token(subject)
	refresh_token, refresh_expires = create_refresh_token(subject)

	return {
		"access_token": access_token,
		"refresh_token": refresh_token,
		"token_type": "bearer",
		"expires_at": access_expires
	}
