import re

from app.core.config import settings


def validate_password(password: str) -> tuple[bool, str]:
	"""Validate password against complexity requirements."""

	if len(password) < settings.PASSWORD_MIN_LENGTH:
		return False, f"Пароль должен содержать минимум {settings.PASSWORD_MIN_LENGTH} символов"

	if settings.PASSWORD_REQUIRE_UPPERCASE and not re.search(r'[A-Z]', password):
		return False, "Пароль должен содержать хотя бы одну заглавную букву"

	if settings.PASSWORD_REQUIRE_LOWERCASE and not re.search(r'[a-z]', password):
		return False, "Пароль должен содержать хотя бы одну строчную букву"

	if settings.PASSWORD_REQUIRE_NUMBERS and not re.search(r'\d', password):
		return False, "Пароль должен содержать хотя бы одну цифру"

	if settings.PASSWORD_REQUIRE_SPECIAL and not re.search(r'[!@#$%^&*]', password):
		return False, "Пароль должен содержать хотя бы один спецсимвол (!@#$%^&*)"

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
