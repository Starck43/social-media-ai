"""Owner-only reuse and bounded Telegram login; never a raw-session import."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.exceptions import RequestValidationError, ResponseValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictInt

from app.core.tenant_context import current_tenant_id
from app.models import User
from app.models.managers.workspace_connection_manager import ConnectionDenied
from app.schemas.connector_account import (
    ConnectorAccountError,
    ConnectorAccountMetadata,
    ReusableConnectorAccounts,
    TelegramLoginMetadata,
    TelegramSourceCatalog,
    TelegramSourceMetadata,
    TelegramSourcePicker,
    WorkspaceGrantMetadata,
)
from app.services.social.connector_authorizations import connector_authorizations
from app.services.social.telegram_login import LoginRejected, telegram_logins
from app.services.social.telegram_source_bindings import SourceBindingUnconfirmed, telegram_source_bindings
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
            except ResponseValidationError:
                # A broken internal projection may contain secret fields too.
                # Do not expose Pydantic input/errors in the response or traceback.
                raise HTTPException(status_code=500, detail="account_response_unavailable") from None

        return handle


router = APIRouter(
    tags=["connector-accounts"],
    route_class=PrivateSelectionRoute,
    responses={
        401: {"model": ConnectorAccountError, "description": "Active access Bearer JWT required"},
        403: {"model": ConnectorAccountError, "description": "Workspace access denied where membership is required"},
        404: {"model": ConnectorAccountError, "description": "Opaque unavailable account, grant or attempt selection"},
        422: {"model": ConnectorAccountError, "description": "Fixed validation code; rejected input is never echoed"},
        500: {"model": ConnectorAccountError, "description": "Invalid internal response; no raw validation detail"},
    },
)
_LOGIN_RESPONSES = {
    409: {"model": ConnectorAccountError, "description": "Conflicting phase/revision or existing pending login"},
    503: {"model": ConnectorAccountError, "description": "Local login configuration or private logging unavailable"},
}


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
    except SourceBindingUnconfirmed:
        raise HTTPException(status_code=503, detail="source_binding_unconfirmed") from None


@router.get("", response_model=ReusableConnectorAccounts)
async def list_reusable_accounts(user: User = Depends(get_authenticated_user)):
    rows, can_attach = await _call(connector_authorizations.list_reusable, **_scope(user))
    return {"accounts": rows, "can_attach": can_attach}


@router.get("/personal", response_model=list[ConnectorAccountMetadata])
async def list_personal_accounts(user: User = Depends(get_authenticated_user)):
    """Account-level self-service metadata; no workspace is read or selected."""
    return await _call(connector_authorizations.list_personal, actor_id=user.id)


@router.get("/grants", response_model=list[WorkspaceGrantMetadata])
async def list_workspace_grants(user: User = Depends(get_authenticated_user)):
    return await _call(connector_authorizations.list_grants, **_scope(user))


class TelegramSourceSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_binding: str = Field(pattern=r"^[0-9a-f]{64}$")
    grant_id: StrictInt = Field(gt=0)
    authorization_id: StrictInt = Field(gt=0)
    authorization_revision: StrictInt = Field(gt=0)


class TelegramSourceClear(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_binding: str = Field(pattern=r"^[0-9a-f]{64}$")


@router.get("/telegram/sources", response_model=TelegramSourceCatalog)
async def list_telegram_sources(after_id: int = 0, user: User = Depends(get_authenticated_user)):
    return await _call(telegram_source_bindings.list_sources, **_scope(user), after_id=after_id)


@router.get("/telegram/sources/{source_id}", response_model=TelegramSourcePicker)
async def telegram_source_picker(source_id: int, user: User = Depends(get_authenticated_user)):
    return await _call(telegram_source_bindings.get, **_scope(user), source_id=source_id)


@router.put("/telegram/sources/{source_id}", response_model=TelegramSourceMetadata)
async def select_telegram_source_account(
    source_id: int, selection: TelegramSourceSelection, user: User = Depends(get_authenticated_user)
):
    return await _call(telegram_source_bindings.set, **_scope(user), source_id=source_id, **selection.model_dump())


@router.post("/telegram/sources/{source_id}/clear", response_model=TelegramSourceMetadata)
async def clear_telegram_source_account(
    source_id: int, selection: TelegramSourceClear, user: User = Depends(get_authenticated_user)
):
    return await _call(telegram_source_bindings.set, **_scope(user), source_id=source_id, **selection.model_dump())


@router.post("/{authorization_id}/attach", response_model=WorkspaceGrantMetadata)
async def attach_account(
    authorization_id: int, selection: AuthorizationSelection, user: User = Depends(get_authenticated_user)
):
    return await _call(
        connector_authorizations.attach,
        **_scope(user),
        authorization_id=authorization_id,
        expected_revision=selection.expected_revision,
    )


@router.post("/grants/{grant_id}/detach", response_model=WorkspaceGrantMetadata)
async def detach_workspace_grant(grant_id: int, user: User = Depends(get_authenticated_user)):
    return await _call(connector_authorizations.detach, **_scope(user), grant_id=grant_id)


@router.post("/{authorization_id}/revoke", response_model=ConnectorAccountMetadata)
async def revoke_personal_authorization(
    authorization_id: int, selection: AuthorizationSelection, user: User = Depends(get_authenticated_user)
):
    return await _call(
        connector_authorizations.revoke_personal,
        actor_id=user.id,
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


@router.post(
    "/telegram/login",
    response_model=TelegramLoginMetadata,
    responses={
        **_LOGIN_RESPONSES,
        429: {"model": ConnectorAccountError, "description": "Login start rate limit; no blind provider retry"},
    },
)
async def begin_telegram_login(payload: TelegramLoginStart, user: User = Depends(get_authenticated_user)):
    return await _call(
        telegram_logins.start, **_scope(user), request_id=payload.request_id, phone=payload.phone.get_secret_value()
    )


@router.get("/telegram/login/{attempt_id}", response_model=TelegramLoginMetadata)
async def telegram_login_status(attempt_id: UUID, user: User = Depends(get_authenticated_user)):
    return await _call(telegram_logins.status, **_scope(user), attempt_id=str(attempt_id))


@router.post("/telegram/login/{attempt_id}/code", response_model=TelegramLoginMetadata, responses=_LOGIN_RESPONSES)
async def submit_telegram_code(attempt_id: UUID, payload: TelegramCode, user: User = Depends(get_authenticated_user)):
    return await _call(
        telegram_logins.submit,
        **_scope(user),
        attempt_id=str(attempt_id),
        expected_revision=payload.expected_revision,
        operation="code",
        secret=payload.code.get_secret_value(),
    )


@router.post("/telegram/login/{attempt_id}/password", response_model=TelegramLoginMetadata, responses=_LOGIN_RESPONSES)
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


@router.post("/telegram/login/{attempt_id}/cancel", response_model=TelegramLoginMetadata)
async def cancel_telegram_login(attempt_id: UUID, user: User = Depends(get_authenticated_user)):
    return await _call(telegram_logins.cancel, **_scope(user), attempt_id=str(attempt_id))
