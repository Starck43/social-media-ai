from typing import TYPE_CHECKING, Type

from fastapi import Depends, HTTPException
from starlette import status
from starlette.requests import Request

from app.models import Base
from app.services.user.auth import get_authenticated_user
from app.types.models import ActionType
from app.utils.permission import generate_permission_codename

if TYPE_CHECKING:
	from app.models import User


class BasePermission:
	"""
	Base permission class. All permissions should inherit from this class.
	"""

	def has_permission(self, user: 'User', **kwargs) -> bool:
		raise NotImplementedError("Permission classes must implement has_permission() method.")

	async def __call__(self, request: Request, user: User = Depends(get_authenticated_user)) -> bool:
		return self.has_permission(user, request=request)

	def __and__(self, other: 'BasePermission') -> 'Operand':
		return Operand(self, other, 'and')

	def __or__(self, other: 'BasePermission') -> 'Operand':
		return Operand(self, other, 'or')

	def __invert__(self) -> 'Not':
		return Not(self)


class Operand(BasePermission):
	"""Helper class for combining permissions with & and | operators."""

	def __init__(self, left: BasePermission, right: BasePermission, operator: str):
		super().__init__()
		self.left = left
		self.right = right
		self.operator = operator

	def has_permission(self, user: 'User', **kwargs) -> bool:
		if self.operator == 'and':
			return self.left.has_permission(user, **kwargs) and self.right.has_permission(user, **kwargs)
		return self.left.has_permission(user, **kwargs) or self.right.has_permission(user, **kwargs)


class Not(BasePermission):
	"""Инвертирует permission"""

	def __init__(self, permission: BasePermission):
		self.permission = permission

	def has_permission(self, user: 'User', **kwargs) -> bool:
		return not self.permission.has_permission(user, **kwargs)


class HasPerm(BasePermission):
	"""Check if user has specific permission."""

	def __init__(self, perm_codename: str):
		self.perm_codename = perm_codename

	def has_permission(self, user: 'User', **kwargs) -> bool:
		return user.has_perm(ActionType[self.perm_codename])


class IsAuthenticated(BasePermission):
	"""
	Allows access only to authenticated users.
	"""

	def has_permission(self, user: 'User', **kwargs) -> bool:
		return user is not None and user.is_active


class IsOwner(BasePermission):
	"""
	Allows access only to the owner of the object.
	The object must have a 'user_id' attribute or property.
	"""

	def has_permission(self, user: 'User', request: Request = None, obj=None) -> bool:
		"""
		Check if the user is the owner of the object.
		"""
		if not user or not user.is_active:
			return False

		# Get object from request or parameter
		target_obj = obj or getattr(request.state, 'permission_obj', None)
		if not target_obj:
			return False

		return hasattr(target_obj, 'user_id') and target_obj.user_id == user.id


class ModelPermission(BasePermission):
	"""Check if user has permission for a model."""

	def __init__(self, model_class: Type['Base'], action: ActionType):
		self.codename = generate_permission_codename(
			getattr(model_class, '_app_label', 'app'),
			model_class.__name__.lower(),
			action
		)

	def has_permission(self, user: 'User', **kwargs) -> bool:
		return user.has_perm(ActionType[self.codename])


def requires(*permissions: BasePermission):
	"""
	Factory for creating dependency with multiple permissions

	Usage:
	@router.get("/data")
	async def get_data(user: User = Depends(requires(IsAuthenticated(), IsOwner()))):
		pass
	"""

	async def dependency(request: Request, user: 'User' = Depends(get_authenticated_user)) -> 'User':
		for permission in permissions:
			if not permission.has_permission(user, request=request):
				raise HTTPException(
					status_code=status.HTTP_403_FORBIDDEN,
					detail=f"Permission denied: {permission.__class__.__name__}"
				)
		return user

	return dependency
