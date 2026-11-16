from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import require_model_perm, require_platform_role
from app.models import User
from app.schemas.user import PlatformRoleUpdate, UserInDB, UserPasswordChange, UserUpdate
from app.services.user.auth import get_authenticated_user
from app.types import ActionType, PlatformRoleType

router = APIRouter(tags=["user"])


async def _require_user_admin(user: User, action: ActionType) -> User:
    actor = await User.objects.prefetch_related("role.permissions").get(id=user.id)
    if actor is None or not actor.has_admin_access() or not actor.has_perm_for("user", action):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Missing platform user permission")
    return actor


@router.get("", response_model=list[UserInDB], summary="Получение списка пользователей")
async def get_users(
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(100, ge=1, le=100, description="Maximum number of records to return"),
    _current_user: User = Depends(require_model_perm("user", ActionType.VIEW)),
):
    """List platform accounts for operators with the user-view grant."""
    return await User.objects.get_active_users(skip=skip, limit=limit)


@router.get("/me", response_model=UserInDB, summary="Получение текущего пользователя")
async def get_current_user(current_user: User = Depends(get_authenticated_user)):
    return current_user


@router.put("/me", response_model=UserInDB, summary="Обновление своего профиля")
async def update_my_profile(
    user_data: UserUpdate,
    current_user: User = Depends(get_authenticated_user),
):
    updated_user = await User.objects.update_user(
        user_id=current_user.id,
        update_data=user_data.model_dump(exclude_unset=True),
        current_user=current_user,
    )
    if updated_user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return updated_user


@router.put("/{user_id}", response_model=UserInDB, summary="Обновление данных пользователя")
async def update_user(
    user_id: int,
    user_data: UserUpdate,
    current_user: User = Depends(require_model_perm("user", ActionType.UPDATE)),
):
    updated_user = await User.objects.update_user(
        user_id=user_id,
        update_data=user_data.model_dump(exclude_unset=True),
        current_user=current_user,
    )
    if updated_user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return updated_user


@router.put("/{user_id}/platform-role", response_model=UserInDB, summary="Изменение платформенной роли")
async def update_platform_role(
    user_id: int,
    role_data: PlatformRoleUpdate,
    current_user: User = Depends(require_platform_role(PlatformRoleType.ADMIN)),
):
    updated_user = await User.objects.set_platform_role(
        user_id=user_id,
        role_code=role_data.role,
        actor_id=current_user.id,
    )
    if updated_user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return updated_user


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Удаление пользователя")
async def delete_user(
    user_id: int,
    _current_user: User = Depends(require_model_perm("user", ActionType.DELETE)),
):
    if not await User.objects.delete_user(user_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")


@router.post("/{user_id}/change-password", status_code=status.HTTP_200_OK, summary="Смена пароля пользователя")
async def change_password(
    user_id: int,
    password_data: UserPasswordChange,
    current_user: User = Depends(require_model_perm("user", ActionType.UPDATE)),
):
    actor = await _require_user_admin(current_user, ActionType.UPDATE)
    success = await User.objects.change_password(
        user_id=user_id,
        current_password=password_data.current_password,
        new_password=password_data.new_password,
        current_user=actor,
        # This route already proved the `user.update` grant; the owner's guard
        # still refuses the reset inside the manager.
        is_admin_reset=user_id != actor.id,
    )
    if not success:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Could not change password")
    return {"detail": "Password updated successfully"}
