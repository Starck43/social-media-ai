"""Task-isolated synthetic admission evidence and no-send stale-version checks."""

from datetime import datetime, time, timezone
from decimal import Decimal
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.config import settings
from app.core.permissions import permission_scope
from app.core.tenant_context import tenant_scope
from app.models import (
    LLMModel,
    LLMProvider,
    PersonalBillingAccount,
    PersonalBillingCutover,
    PersonalLLMDispatch,
    PlatformRole,
    Tenant,
    TenantUser,
    User,
    WorkspaceRole,
)
from app.services.ai import llm_client
from app.services.ai.credential_boundary import require_current_credential_version
from app.services.billing.personal_ledger import reserve_personal_dispatch


@pytest.mark.asyncio
async def test_admission_persists_real_version_and_does_not_rewrite_it_on_rotation(monkeypatch):
    assert settings.DB_SCHEMA.startswith("test_p21t11_")
    monkeypatch.setattr(settings, "PERSONAL_BILLING_ENABLED", True)
    marker = "p21t11-" + uuid4().hex[:16]
    role = await PlatformRole.objects.get(codename="admin")
    owner_role = await WorkspaceRole.objects.get(codename="owner")
    user = await User.objects.create(
        username=marker,
        email=marker + "@example.invalid",
        hashed_password="synthetic-only",  # pragma: allowlist secret (synthetic)
        role_id=role.id,
        is_active=True,
    )
    tenant = await Tenant.objects.create(slug=marker, name="Synthetic P21-T11", plan="business")
    await TenantUser.objects.create(
        tenant_id=tenant.id,
        user_id=user.id,
        role_id=owner_role.id,
        channel="web",
        external_user_id=marker,
        is_active=True,
    )
    now = datetime.now(timezone.utc)
    account = await PersonalBillingAccount.objects.create(
        user_id=user.id,
        daily_limit_usd=Decimal(0),
        is_active=True,
        configured_at=now,
        policy_version="personal-daily-cap-v1",
    )
    await PersonalBillingCutover.objects.create(
        account_id=account.id,
        effective_at=datetime.combine(now.date(), time.min, timezone.utc),
        reviewed_by_user_id=user.id,
        review_version="synthetic-p21t11-v1",
    )
    with tenant_scope(tenant.id), permission_scope(user):
        provider = await LLMProvider.objects.create(
            owner_user_id=user.id,
            uid=marker,
            name=marker,
            api_format="openai",
            base_url="https://example.invalid/v1",
            is_active=True,
        )
        model = await LLMModel.objects.create(
            owner_user_id=user.id,
            provider_id=provider.id,
            name=marker,
            model_id=marker,
            model_type="text",
            input_cost_per_1k=1,
            output_cost_per_1k=1,
            max_tokens=64,
            is_active=True,
        )
        selected = await LLMModel.objects.select_related("provider").get(id=model.id)
        admission = await reserve_personal_dispatch(
            selected,
            purpose="analysis",
            stage="text_analysis",
            request_shape={
                "adapter": "openai_chat_completions",
                "provider_name": marker,
                "model_id": marker,
                "has_media": False,
                "uses_tools": False,
                "requested_output_tokens": 8,
            },
        )
        evidence = await PersonalLLMDispatch.objects.get(dispatch_id=admission["dispatch_id"])
        assert evidence.credential_version == admission["__credential_version"] == 1
        await LLMProvider.objects.filter(id=provider.id).update(auth_header="x-api-key")
        fresh = await LLMProvider.objects.get(id=provider.id)
        assert fresh.credential_version == 2
        with pytest.raises(PermissionError):
            require_current_credential_version(evidence.credential_version, fresh.credential_version)
        assert (await PersonalLLMDispatch.objects.get(dispatch_id=admission["dispatch_id"])).credential_version == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("accepted", [None, 1])
async def test_missing_or_stale_admission_version_releases_without_key_resolution(monkeypatch, accepted):
    validate = AsyncMock()
    release = AsyncMock()
    from app.services.ai import personal_dispatch

    monkeypatch.setattr(personal_dispatch, "validate_personal_dispatch", validate)
    monkeypatch.setattr(llm_client, "_observe_personal_failure", release)
    receipt = {"dispatch_id": "a" * 32, "__credential_version": accepted}
    with pytest.raises(PermissionError):
        await llm_client._revalidate_personal_dispatch_before_http(NS(credential_version=2), receipt)
    validate.assert_not_awaited()
    release.assert_awaited_once_with(receipt, not_dispatched=True)
