from functools import wraps

from fastapi import HTTPException

from app.types.models import UserRole


def app_label(label: str):
	"""Декоратор для установки app_label"""

	def decorator(cls):
		cls._app_label = label
		return cls

	return decorator


def role_required(min_role: UserRole):
	def decorator(func):
		@wraps(func)
		async def wrapper(*args, **kwargs):
			# Предполагаем, что user передается как аргумент
			user = kwargs.get('user') or (args[0] if args else None)

			if not user or not hasattr(user, 'has_minimum_role'):
				raise HTTPException(403, "User authentication required")

			if user.has_minimum_role(min_role):
				return await func(*args, **kwargs)

			raise HTTPException(403, f"Requires at least {min_role.value} role")

		return wrapper

	return decorator
