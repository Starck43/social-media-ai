from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm

from app.core.logger import logger
from app.models import PlatformRole, User
from app.schemas.token import Token, TokenWithRefresh
from app.schemas.user import UserCreate, UserPasswordChange
from app.services.user.auth import GENERIC_LOGIN_ERROR, authenticate, get_authenticated_user, oauth2_scheme
from app.services.user.login_protection import (
    LoginLocked,
    clear_login_failures,
    client_address,
    guard_login,
    record_login_failure,
)
from app.types import PlatformRoleType
from app.utils.token import create_access_token, create_tokens_pair

router = APIRouter(tags=["auth"])


@router.post("/login", response_model=TokenWithRefresh)
async def login_access_token(request: Request, form_data: OAuth2PasswordRequestForm = Depends()) -> dict[str, Any]:
    """
    OAuth2 compatible token login, get access and refresh tokens.

    — **username**: The user's username or email
    — **password**: The user's password

    Returns both access and refresh tokens for authenticated requests.
    The access token is short-lived, while the refresh token can be used to
    get a new access token when it expires.

    Failed attempts count into the same per-account and per-address budget the
    console and the web pages use; the answer stays one generic error.
    """
    from app.core.hashing import verify_password

    address = client_address(request)
    credentials_error = HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=GENERIC_LOGIN_ERROR,
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        await guard_login(form_data.username, address)
    except LoginLocked:
        logger.warning("Login refused by the shared throttle")
        raise credentials_error

    try:
        # Authenticate the user
        user = await authenticate(
            username_or_email=form_data.username, password=form_data.password, password_hasher=verify_password
        )

        if not user:
            logger.warning(f"Failed login attempt for username or email: {form_data.username}")
            await record_login_failure(form_data.username, address)
            raise credentials_error

        # Generate both access and refresh tokens
        tokens = create_tokens_pair(subject=str(user.id))
        await clear_login_failures(form_data.username)
        logger.info(f"Successful login for user: {user.username}")

        return tokens

    except HTTPException as exc:
        # One answer for wrong credentials and an unknown/inactive account; a
        # counted failure is recorded once, never for a server fault.
        if exc is credentials_error:
            raise
        await record_login_failure(form_data.username, address)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=GENERIC_LOGIN_ERROR,
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    except Exception as e:
        logger.error(f"Unexpected error during login: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An error occurred during authentication"
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
        return {"access_token": access_token, "token_type": "bearer", "expires_at": expires_at}

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
                    Account state and platform rights are never taken from the payload.

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

    # Self-registration never grants platform rights: the account gets the
    # lowest platform role, like the web registration, and only the identity
    # fields of the payload are written.
    user_role = await PlatformRole.objects.get_by_codename(PlatformRoleType.USER)
    if user_role is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Roles are not configured",
        )

    try:
        user = await User.objects.create_user(
            username=new_user.username,
            email=new_user.email,
            password=new_user.password,
            role_id=user_role.id,
            is_active=True,
        )

        # Generate tokens for the new user
        tokens = create_tokens_pair(subject=str(user.id))
        return tokens
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error creating user") from e


@router.post("/change-password", status_code=status.HTTP_200_OK)
async def change_own_password(
    password_data: UserPasswordChange,
    current_user: User = Depends(get_authenticated_user),
) -> dict[str, str]:
    """Change the authenticated account's password without operator access."""
    success = await User.objects.change_password(
        user_id=current_user.id,
        current_password=password_data.current_password,
        new_password=password_data.new_password,
        current_user=current_user,
    )
    if not success:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Could not change password")
    return {"detail": "Password updated successfully"}
