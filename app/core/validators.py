import re

from app.core.config import settings


def validate_password(password: str) -> tuple[bool, str]:
	"""Validate password against complexity requirements."""

	if len(password) < settings.PASSWORD_MIN_LENGTH:
		return False, f"Password must be at least {settings.PASSWORD_MIN_LENGTH} characters long"

	if settings.PASSWORD_REQUIRE_UPPERCASE and not re.search(r'[A-Z]', password):
		return False, "Password must contain at least one uppercase letter"

	if settings.PASSWORD_REQUIRE_LOWERCASE and not re.search(r'[a-z]', password):
		return False, "Password must contain at least one lowercase letter"

	if settings.PASSWORD_REQUIRE_NUMBERS and not re.search(r'\d', password):
		return False, "Password must contain at least one number"

	if settings.PASSWORD_REQUIRE_SPECIAL and not re.search(r'[!@#$%^&*]', password):
		return False, "Password must contain at least one special character (!@#$%^&*)"

	return True, ""


def validate_password_strength(password: str) -> bool:
	if len(password) < 8:
		return False
	if not any(char.isdigit() for char in password):
		return False
	if not any(char.isupper() for char in password):
		return False
	if not any(char.islower() for char in password):
		return False
	if not any(char in '!@#$%^&*()_+-=[]{}|;:,.<>?' for char in password):
		return False
	return True
