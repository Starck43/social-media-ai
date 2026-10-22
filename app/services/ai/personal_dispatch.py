"""Fresh credential authorization before provider traffic; no global fallback."""
from app.core.personal_catalog_context import assert_catalog_model, current_catalog_owner_id
from app.core.tenant_context import current_tenant_id, is_bypass
from app.models import LLMModel, User


async def validate_personal_dispatch(client):
    assert_catalog_model(client.model)
    owner_id = current_catalog_owner_id()
    owner = await User.objects.get(id=owner_id, is_active=True)
    if owner is None:
        raise PermissionError("Personal catalog owner is inactive")
    tenant_id = current_tenant_id()
    if not is_bypass() and tenant_id is not None:
        from app.models.managers.tenant_manager import tenant_users
        members = await tenant_users.web_memberships(owner_id)
        if not any(member.tenant_id == tenant_id for member in members):
            raise PermissionError("Catalog owner's workspace membership was revoked")
    fresh = await LLMModel.objects.select_related("provider").get(id=client.model.id, owner_user_id=owner_id, is_active=True)
    if fresh is None or not fresh.provider.is_active or fresh.provider.owner_user_id != owner_id:
        raise PermissionError("Selected personal model is unavailable")
    if tenant_id is not None:
        from app.models import Tenant
        tenant = await Tenant.objects.get(id=tenant_id, is_active=True)
        allowed = tenant.plan_limits().get("model_types") if tenant is not None else set()
        if tenant is None or (allowed is not None and not set(fresh.capabilities) <= set(allowed)):
            raise PermissionError("Model capabilities are unavailable in this workspace")
    if (fresh.model_id != client.model_name or fresh.provider.base_url.rstrip("/") != client.base_url
            or fresh.provider.api_format != client.provider.api_format
            or fresh.provider.auth_header != client.provider.auth_header
            or fresh.provider.get_api_key() != client.api_key):
        raise PermissionError("Provider configuration changed; reload before dispatch")
