"""Credential management API endpoints."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import require_model_perm
from app.models import User, UserCredential
from app.schemas.credential import CredentialLoginRequest, CredentialOauthRequest, CredentialResponse
from app.services.social.platform_config import load_platform_config
from app.types import ActionType

router = APIRouter(tags=["credentials"])


@router.get("", response_model=list[CredentialResponse])
async def list_credentials(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(require_model_perm("credential", ActionType.VIEW)),
):
    """List the current user's personal credentials (secrets are encrypted, never returned)."""
    from app.models.managers.user_credential_manager import user_credentials

    rows = (
        await user_credentials.filter(user_id=current_user.id).order_by(UserCredential.id).limit(limit).offset(offset)
    )
    return [
        CredentialResponse(
            id=r.id,
            user_id=r.user_id,
            platform=r.platform,
            kind=r.kind,
            label=r.label,
            expires_at=r.expires_at,
            is_active=r.is_active,
            created_at=r.created_at,
            updated_at=r.updated_at,
        )
        for r in rows
    ]


@router.patch("/{credential_id}/disable")
@router.patch("/credentials/{credential_id}/disable", deprecated=True)
async def disable_credential(
    credential_id: int,
    current_user: User = Depends(require_model_perm("credential", ActionType.UPDATE)),
):
    """Deactivate a credential (row kept for audit)."""
    from app.models.managers.user_credential_manager import user_credentials

    row = await user_credentials.get(id=credential_id, user_id=current_user.id)
    if row is None:
        raise HTTPException(status_code=404, detail="Credential not found")

    await user_credentials.update_by_id(row.id, is_active=False)
    return {"status": "disabled", "credential_id": row.id}


@router.post("/login", response_model=CredentialResponse)
async def login_credential(
    request: CredentialLoginRequest,
    current_user: User = Depends(require_model_perm("credential", ActionType.CONFIGURE)),
):
    """Reject legacy terminal login: HTTP must never call input() or echo errors.

    A persisted workspace-consent foundation is not a completed login journey.
    The future phone/code/2FA endpoint must own its bounded encrypted attempts.
    """
    raise HTTPException(status_code=409, detail="workspace_telegram_authorization_not_available")


@router.post("/oauth", response_model=dict)
async def oauth_credential(
    request: CredentialOauthRequest,
    current_user: User = Depends(require_model_perm("credential", ActionType.CONFIGURE)),
):
    """Initiate OAuth flow for a platform (e.g. VK PKCE)."""
    if request.platform.strip().lower() != "vk":
        raise HTTPException(status_code=400, detail="Only 'vk' supports OAuth via API")

    from app.core.tenant_context import current_tenant_id
    from app.services.social.vk_oauth import build_authorize_url

    tid = current_tenant_id()
    if tid is None:
        raise HTTPException(status_code=400, detail="No active workspace")

    try:
        url = await build_authorize_url(tid, user_id=current_user.id)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {"authorize_url": url, "tenant_id": tid}


@router.get("/test", response_model=list[dict])
async def test_credentials(
    current_user: User = Depends(require_model_perm("credential", ActionType.VIEW)),
):
    """Test all platform credentials and report status."""
    import httpx

    from app.core.config import settings
    from app.services.social.credential_diagnostics import (
        credential_exception_status,
        credential_http_error,
        credential_provider_error,
    )
    from app.services.social.credentials import credential_status, resolve_token
    from app.services.social.tg_session import check_session, load_session

    results = []
    for platform, source in (await credential_status(owner_user_id=current_user.id)).items():
        if source == "missing":
            results.append({"platform": platform, "source": source, "status": "missing"})
            continue

        token = await resolve_token(platform, owner_user_id=current_user.id)
        if not token:
            results.append({"platform": platform, "source": source, "status": "no_token"})
            continue

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                if platform == "vk":
                    config = await load_platform_config("vk")
                    resp = await client.get(
                        f"{config['api_base_url'].rstrip('/')}/account.getProfileInfo",
                        params={"access_token": token, "v": config["api_version"]},
                    )
                    if not 200 <= resp.status_code < 300:
                        status = credential_http_error(resp.status_code)
                    else:
                        data = resp.json()
                        if not isinstance(data, dict):
                            status = "error: invalid_response"
                        elif "response" in data:
                            status = "ok"
                        else:
                            status = credential_provider_error("vk", data)
                elif platform == "telegram":
                    resp = await client.get(
                        f"{(await load_platform_config('telegram'))['api_base_url'].rstrip('/')}/bot{token}/getMe"
                    )
                    if not 200 <= resp.status_code < 300:
                        status = credential_http_error(resp.status_code)
                    else:
                        data = resp.json()
                        if not isinstance(data, dict):
                            status = "error: invalid_response"
                        elif data.get("ok") is True:
                            status = f"ok (bot @{data['result'].get('username', '?')})"
                        else:
                            status = credential_provider_error("telegram", data)
                elif platform == "max":
                    resp = await client.get(f"{settings.MAX_API_URL}/me", headers={"Authorization": token})
                    status = "ok" if resp.status_code == 200 else credential_http_error(resp.status_code)
                else:
                    status = "skipped"
        except Exception as e:
            status = credential_exception_status(e)

        results.append({"platform": platform, "source": source, "status": status})

    # L2 is separate from the bot token
    mtproto = await load_session(user_id=current_user.id)
    if mtproto is None:
        results.append({"platform": "telegram", "source": "mtproto", "status": "not_configured"})
    else:
        verdict = await check_session(mtproto)
        results.append({"platform": "telegram", "source": "mtproto", "status": verdict})

    return results
