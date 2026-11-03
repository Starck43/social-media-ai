"""Owner-only reuse and bounded Telegram login; never a raw-session import."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictInt

from app.core.tenant_context import current_tenant_id
from app.models import User
from app.models.managers.workspace_connection_manager import ConnectionDenied
from app.services.social.connector_authorizations import connector_authorizations
from app.services.social.telegram_login import LoginRejected, telegram_logins
from app.services.user.auth import get_authenticated_user


class PrivateSelectionRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def handle(request):
            try:
                return await handler(request)
            except RequestValidationError:
                # Unexpected input may contain a pasted key/session. Never echo
                # Pydantic's raw `input` values in a rejected selection response.
                raise HTTPException(status_code=422, detail="invalid_account_selection") from None

        return handle


router = APIRouter(tags=["connector-accounts"], route_class=PrivateSelectionRoute)


class AuthorizationSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: StrictInt = Field(gt=0)


def _scope(user):
    return {"tenant_id": current_tenant_id(), "actor_id": user.id}


async def _call(target, **arguments):
    try:
        return await target(**arguments)
    except LoginRejected as exc:
        raise HTTPException(status_code=exc.status, detail=exc.code) from None
    except ConnectionDenied:
        raise HTTPException(status_code=404, detail="connection_unavailable") from None


@router.get("")
async def list_reusable_accounts(user: User = Depends(get_authenticated_user)):
    rows, can_attach = await _call(connector_authorizations.list_reusable, **_scope(user))
    return {"accounts": rows, "can_attach": can_attach}


@router.get("/grants")
async def list_workspace_grants(user: User = Depends(get_authenticated_user)):
    return await _call(connector_authorizations.list_grants, **_scope(user))


@router.post("/{authorization_id}/attach")
async def attach_account(
    authorization_id: int, selection: AuthorizationSelection, user: User = Depends(get_authenticated_user)
):
    return await _call(
        connector_authorizations.attach,
        **_scope(user),
        authorization_id=authorization_id,
        expected_revision=selection.expected_revision,
    )


@router.post("/grants/{grant_id}/detach")
async def detach_workspace_grant(grant_id: int, user: User = Depends(get_authenticated_user)):
    return await _call(connector_authorizations.detach, **_scope(user), grant_id=grant_id)


@router.post("/{authorization_id}/revoke")
async def revoke_personal_authorization(
    authorization_id: int, selection: AuthorizationSelection, user: User = Depends(get_authenticated_user)
):
    return await _call(
        connector_authorizations.revoke,
        **_scope(user),
        authorization_id=authorization_id,
        expected_revision=selection.expected_revision,
    )


class TelegramLoginStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    phone: SecretStr


class TelegramCode(AuthorizationSelection):
    code: SecretStr


class TelegramPassword(AuthorizationSelection):
    password: SecretStr


@router.post("/telegram/login")
async def begin_telegram_login(payload: TelegramLoginStart, user: User = Depends(get_authenticated_user)):
    return await _call(
        telegram_logins.start, **_scope(user), request_id=payload.request_id, phone=payload.phone.get_secret_value()
    )


@router.get("/telegram/login/{attempt_id}")
async def telegram_login_status(attempt_id: UUID, user: User = Depends(get_authenticated_user)):
    return await _call(telegram_logins.status, **_scope(user), attempt_id=str(attempt_id))


@router.post("/telegram/login/{attempt_id}/code")
async def submit_telegram_code(attempt_id: UUID, payload: TelegramCode, user: User = Depends(get_authenticated_user)):
    return await _call(
        telegram_logins.submit,
        **_scope(user),
        attempt_id=str(attempt_id),
        expected_revision=payload.expected_revision,
        operation="code",
        secret=payload.code.get_secret_value(),
    )


@router.post("/telegram/login/{attempt_id}/password")
async def submit_telegram_password(
    attempt_id: UUID, payload: TelegramPassword, user: User = Depends(get_authenticated_user)
):
    return await _call(
        telegram_logins.submit,
        **_scope(user),
        attempt_id=str(attempt_id),
        expected_revision=payload.expected_revision,
        operation="password",
        secret=payload.password.get_secret_value(),
    )


@router.post("/telegram/login/{attempt_id}/cancel")
async def cancel_telegram_login(attempt_id: UUID, user: User = Depends(get_authenticated_user)):
    return await _call(telegram_logins.cancel, **_scope(user), attempt_id=str(attempt_id))
