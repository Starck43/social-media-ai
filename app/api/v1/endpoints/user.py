from fastapi import APIRouter, Depends, HTTPException, status, Query

from app.services.user.auth import get_authenticated_user
from app.models import User
from app.schemas.user import UserUpdate, UserInDB, UserPasswordChange

router = APIRouter(tags=["user"])


@router.get("", response_model=list[UserInDB], summary="Получение списка пользователей")
async def get_users(
		skip: int = Query(0, ge=0, description="Number of records to skip"),
		limit: int = Query(100, le=100, description="Maximum number of records to return"),
		current_user: User = Depends(get_authenticated_user)
):
	"""
	Get paginated list of users (admin only)

	Returns:
		List of users with basic information
	"""
	if not current_user.is_superuser:
		raise HTTPException(
			status_code=status.HTTP_403_FORBIDDEN,
			detail="Not enough permissions"
		)

	return User.objects.get_active_users(skip=skip, limit=limit)


@router.get("/me", response_model=UserInDB, summary="Получение текущего пользователя")
async def get_current_user(
		current_user: User = Depends(get_authenticated_user)
):
	"""
	Get current user information

	Returns:
		Detailed information about authenticated user
	"""
	return current_user


@router.put("/{user_id}", response_model=UserInDB, summary="Обновление данных пользователя")
async def update_user(
		user_id: int,
		user_data: UserUpdate,
		current_user: User = Depends(get_authenticated_user)
):
	"""
	Update user information

	Returns:
		Updated user information
	"""
	# Only allow admins, or the user themselves to update
	if not current_user.is_superuser and current_user.id != user_id:
		raise HTTPException(
			status_code=status.HTTP_403_FORBIDDEN,
			detail="Not enough permissions"
		)

	# Remove password field if present (use change_password endpoint instead)
	update_data = user_data.model_dump(exclude_unset=True)
	if 'password' in update_data:
		del update_data['password']

	updated_user = User.objects.update_user(
		user_id=user_id,
		update_data=update_data,
		current_user=current_user
	)

	if not updated_user:
		raise HTTPException(
			status_code=status.HTTP_404_NOT_FOUND,
			detail="User not found"
		)

	return updated_user


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Удаление пользователя")
async def delete_user(
		user_id: int,
		current_user: User = Depends(get_authenticated_user)
):
	"""
	Delete a user (admin only)
	"""
	if not current_user.is_superuser:
		raise HTTPException(
			status_code=status.HTTP_403_FORBIDDEN,
			detail="Not enough permissions"
		)

	if not User.objects.delete_user(user_id):
		raise HTTPException(
			status_code=status.HTTP_404_NOT_FOUND,
			detail="User not found"
		)


@router.post("/{user_id}/change-password", status_code=status.HTTP_200_OK, summary="Смена пароля")
async def change_password(
		user_id: int,
		password_data: UserPasswordChange,
		current_user: User = Depends(get_authenticated_user)
):
	"""
	Change user password

	Returns:
		Success message
	"""
	success = User.objects.change_password(
		user_id=user_id,
		current_password=password_data.current_password,
		new_password=password_data.new_password,
		current_user=current_user
	)

	if not success:
		raise HTTPException(
			status_code=status.HTTP_400_BAD_REQUEST,
			detail="Could not change password"
		)

	return {"detail": "Password updated successfully"}
