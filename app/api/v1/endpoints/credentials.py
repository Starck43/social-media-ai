"""Credential management API endpoints."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import require_model_perm
from app.models import User
from app.schemas.credential import CredentialResponse, CredentialLoginRequest, CredentialOauthRequest
from app.types import ActionType

router = APIRouter(tags=["credentials"])


@router.get("", response_model=list[CredentialResponse])
async def list_credentials(
    current_user: User = Depends(require_model_perm("credential", ActionType.VIEW)),
):
    """List the current user's personal credentials (secrets are encrypted, never returned)."""
    from app.models.managers.user_credential_manager import user_credentials

    rows = await user_credentials.filter(user_id=current_user.id)
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


@router.patch("/credentials/{credential_id}/disable")
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
    """Initiate interactive login for a platform (e.g. telegram MTProto)."""
    if request.platform.strip().lower() != "telegram":
        raise HTTPException(status_code=400, detail="Only 'telegram' supports interactive login via API")

    from app.services.social.credentials import resolve_token
    from app.services.social.tg_session import check_session, interactive_login, load_session, save_session

    api_id_raw = await resolve_token("telegram", kinds=("api_id",))
    api_hash = await resolve_token("telegram", kinds=("api_hash",))
    
    if api_id_raw and api_hash:
        api_id = int(api_id_raw)
    else:
        raise HTTPException(status_code=400, detail="Set TELEGRAM_API_ID and TELEGRAM_API_HASH in .env first")

    try:
        session = await interactive_login(api_id, api_hash)
    except ImportError:
        raise HTTPException(status_code=500, detail="telethon is not installed: pip install telethon")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Login failed: {e}")

    await save_session(current_user.id, api_id=api_id, api_hash=api_hash, session=session)
    stored = await load_session(user_id=current_user.id)
    verdict = await check_session(stored) if stored else "error: session did not resolve after save"

    # Return the stored credential
    from app.models.managers.user_credential_manager import user_credentials
    rows = await user_credentials.filter(user_id=current_user.id, platform="telegram")
    if rows:
        row = rows[-1]
        return CredentialResponse(
            id=row.id,
            user_id=row.user_id,
            platform=row.platform,
            kind=row.kind,
            label=row.label,
            expires_at=row.expires_at,
            is_active=row.is_active,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    raise HTTPException(status_code=500, detail="Session saved but credential not found")


@router.post("/oauth", response_model=dict)
async def oauth_credential(
    request: CredentialOauthRequest,
    current_user: User = Depends(require_model_perm("credential", ActionType.CONFIGURE)),
):
    """Initiate OAuth flow for a platform (e.g. VK PKCE)."""
    if request.platform.strip().lower() != "vk":
        raise HTTPException(status_code=400, detail="Only 'vk' supports OAuth via API")

    from app.services.social.vk_oauth import build_authorize_url
    from app.core.tenant_context import current_tenant_id

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
    from app.services.social.credentials import credential_status, resolve_token
    from app.services.social.tg_session import check_session, load_session
    import httpx
    from app.core.config import settings

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
                    resp = await client.get(
                        f"{settings.VK_API_BASE_URL.rstrip('/')}/account.getProfileInfo",
                        params={"access_token": token, "v": settings.VK_API_VERSION},
                    )
                    data = resp.json()
                    status = "ok" if "response" in data else f"error: {data.get('error', {}).get('error_msg', resp.status_code)}"
                elif platform == "telegram":
                    resp = await client.get(f"{settings.TELEGRAM_API_BASE_URL.rstrip('/')}/bot{token}/getMe")
                    data = resp.json()
                    status = f"ok (bot @{data['result'].get('username', '?')})" if data.get("ok") else f"error: {data.get('description', resp.status_code)}"
                elif platform == "max":
                    resp = await client.get(f"{settings.MAX_API_URL}/me", headers={"Authorization": token})
                    status = "ok" if resp.status_code == 200 else f"error: HTTP {resp.status_code}"
                else:
                    status = "skipped"
        except Exception as e:
            status = f"error: {e}"

        results.append({"platform": platform, "source": source, "status": status})

    # L2 is separate from the bot token
    mtproto = await load_session(user_id=current_user.id)
    if mtproto is None:
        results.append({"platform": "telegram", "source": "mtproto", "status": "not_configured"})
    else:
        verdict = await check_session(mtproto)
        results.append({"platform": "telegram", "source": "mtproto", "status": verdict})

    return results
