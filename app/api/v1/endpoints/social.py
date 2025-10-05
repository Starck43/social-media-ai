from fastapi import APIRouter, Depends, status, HTTPException

from app.services.user.auth import get_authenticated_user
from app.models import SocialAccount, User
from app.schemas.social import (
    SocialAccountCreate, SocialAccountInDB, SocialGroupInDB, SocialAccountUpdateAuth
)

router = APIRouter(tags=["social"])


@router.post("/accounts", response_model=SocialAccountInDB)
def create_social_account(
    *,
    account_in: SocialAccountCreate,
    current_user: User = Depends(get_authenticated_user),
):
    """
    Add a new social media account (without authentication data)
    """
    return SocialAccount.objects.create(
        user_id=current_user.id,
        platform=account_in.platform,
        platform_user_id=account_in.platform_user_id,
        is_active=account_in.is_active
    )


@router.post("/accounts/{account_id}/auth", response_model=SocialAccountInDB)
def update_social_account_auth(
    *,
    account_id: int,
    auth_data: SocialAccountUpdateAuth,
    current_user: User = Depends(get_authenticated_user),
):
    """
    Update authentication data for a social account
    """
    account = SocialAccount.objects.get(id=account_id)
    if not account or account.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Account not found")
    
    return SocialAccount.objects.update(
        account_id,
        access_token=auth_data.access_token,
        refresh_token=auth_data.refresh_token,
        token_expires_at=auth_data.token_expires
    )


@router.get("/accounts", response_model=list[SocialAccountInDB])
def read_social_accounts(
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(get_authenticated_user),
):
    """
    Retrieve social media accounts for current user
    """
    return SocialAccount.objects.get_user_accounts(user_id=current_user.id, skip=skip, limit=limit)


@router.delete("/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_social_account(
    account_id: int,
    current_user: User = Depends(get_authenticated_user),
):
    """
    Delete a social media account
    """
    SocialAccount.objects.delete_account(account_id=account_id, user_id=current_user.id)
    return {"ok": True}


@router.get("/groups", response_model=list[SocialGroupInDB])
def read_social_groups(
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(get_authenticated_user),
):
    """
    Retrieve social media groups for current user
    """
    return SocialAccount.objects.get_user_groups(user_id=current_user.id, skip=skip, limit=limit)
