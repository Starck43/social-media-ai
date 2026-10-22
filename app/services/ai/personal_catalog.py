"""Personal LLM CRUD and selection, shared by web/API/runtime.

Only trusted actor IDs are accepted. Bundles and default changes lock the user
row so concurrent writes cannot fork that user's capability defaults.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.permissions import get_current_user
from app.models import LLMModel, LLMProvider, Tenant, User

PROVIDER_FIELDS = {"id", "uid", "name", "description", "api_format", "base_url", "auth_header", "is_active", "api_key"}
MODEL_FIELDS = {
    "id", "name", "model_id", "model_type", "description", "custom_endpoint_path",
    "max_tokens", "default_temperature", "is_active", "is_default", "pricing_token_unit",
    "input_cost_per_1k", "output_cost_per_1k", "cache_read_cost_per_1m",
    "cache_write_5m_cost_per_1m", "cache_write_1h_cost_per_1m",
}


def require_actor(user_id):
    actor = get_current_user()
    if (type(user_id) is not int or user_id <= 0 or actor is None
            or not actor.is_active or actor.id != user_id):
        raise PermissionError("Personal catalog access denied")
    return actor


def _identifier(value):
    if type(value) is not int or value <= 0:
        raise ValueError("A positive numeric record ID is required")
    return value


def validate_provider_values(values, *, creating=False):
    if not isinstance(values, dict) or set(values) - PROVIDER_FIELDS:
        raise ValueError("Unknown provider fields")
    result = dict(values)
    if "id" in result:
        _identifier(result["id"])
    if creating or "uid" in result:
        uid = result.get("uid", "")
        if not isinstance(uid, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,99}", uid):
            raise ValueError("Provider UID must be a lowercase slug")
    for key, limit in (("name", 255), ("base_url", 500), ("auth_header", 200)):
        if creating and key in {"name", "base_url"} and key not in result:
            raise ValueError("Provider name and URL are required")
        if key in result:
            value = result[key]
            if key == "auth_header" and value is None:
                continue
            if not isinstance(value, str) or len(value) > limit or (key != "auth_header" and not value.strip()):
                raise ValueError("Invalid provider text field")
            result[key] = value.strip()
    if "base_url" in result:
        url = urlsplit(result["base_url"])
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.fragment:
            raise ValueError("Provider URL must be HTTPS without credentials or fragment")
        result["base_url"] = result["base_url"].rstrip("/")
    if result.get("api_format", "openai") not in {"openai", "anthropic", "custom"}:
        raise ValueError("Unsupported API format")
    if "is_active" in result and type(result["is_active"]) is not bool:
        raise ValueError("Provider active flag must be boolean")
    if "api_key" in result and (not isinstance(result["api_key"], str) or len(result["api_key"]) > 16384):
        raise ValueError("Invalid API key")
    if result.get("auth_header") is not None and any(c in result["auth_header"] for c in "\r\n"):
        raise ValueError("Invalid authorization header")
    return result


def validate_model_values(values, *, creating=False):
    if not isinstance(values, dict) or set(values) - MODEL_FIELDS:
        raise ValueError("Unknown model fields")
    result = dict(values)
    if "id" in result:
        _identifier(result["id"])
    for key in ("name", "model_id"):
        if creating or key in result:
            value = result.get(key)
            if not isinstance(value, str) or not value.strip() or len(value) > 100:
                raise ValueError("Model name and API model ID are required (max 100 characters)")
            result[key] = value.strip()
    if "model_type" in result:
        value = result["model_type"]
        caps = value.split(",") if isinstance(value, str) else []
        if not caps or set(caps) - {"text", "image", "video", "embedding", "decision"} or len(value) > 20:
            raise ValueError("Invalid model capabilities")
    if "max_tokens" in result and (type(result["max_tokens"]) is not int or not 1 <= result["max_tokens"] <= 1000000):
        raise ValueError("Invalid model token limit")
    for key in ("is_active", "is_default"):
        if key in result and type(result[key]) is not bool:
            raise ValueError("Model flags must be boolean")
    for key in ("default_temperature", "input_cost_per_1k", "output_cost_per_1k",
                "cache_read_cost_per_1m", "cache_write_5m_cost_per_1m", "cache_write_1h_cost_per_1m"):
        if key not in result:
            continue
        if result[key] is None and key.startswith("cache_"):
            continue
        try:
            value = Decimal(str(result[key]))
        except (ValueError, InvalidOperation):
            raise ValueError("Invalid model numeric value") from None
        if not value.is_finite() or value < 0 or (key == "default_temperature" and value > 2):
            raise ValueError("Invalid model numeric value")
        result[key] = value if key.startswith("cache_") else float(value)
    if "pricing_token_unit" in result and result["pricing_token_unit"] not in (1000, 1000000):
        raise ValueError("Invalid pricing unit")
    if "custom_endpoint_path" in result and result["custom_endpoint_path"] is not None:
        value = result["custom_endpoint_path"]
        if not isinstance(value, str) or len(value) > 200 or not value.startswith("/") or value.startswith("//"):
            raise ValueError("Invalid custom endpoint path")
    return result


def eligible_chat_model(model, *, user_id, allowed_types=None):
    provider = getattr(model, "provider", None)
    capabilities = set(model.capabilities)
    return bool(
        model.owner_user_id == user_id and provider and provider.owner_user_id == user_id
        and model.is_active and provider.is_active and {"text", "image"}.intersection(capabilities)
        and (allowed_types is None or capabilities <= set(allowed_types))
    )


def model_metadata(model):
    provider = model.provider
    return {
        "id": model.id, "owner_user_id": model.owner_user_id, "name": model.name, "model_id": model.model_id,
        "provider_id": model.provider_id, "provider_uid": provider.uid, "provider_name": provider.name,
        "model_type": model.model_type, "max_tokens": model.max_tokens,
        "default_temperature": model.default_temperature, "is_active": model.is_active,
        "is_default": model.is_default, "description": model.description,
        "custom_endpoint_path": model.custom_endpoint_path,
        "pricing_token_unit": model.pricing_token_unit,
        **{key: getattr(model, key) for key in MODEL_FIELDS if "cost_per_" in key},
    }


def provider_metadata(provider, models):
    return {
        "id": provider.id, "owner_user_id": provider.owner_user_id, "name": provider.name, "uid": provider.uid,
        "api_format": provider.api_format, "base_url": provider.base_url,
        "auth_header": None,  # May contain a literal token; never project credential material.
        "description": provider.description, "is_active": provider.is_active,
        "has_api_key": bool(provider.encrypted_api_key), "models": [model_metadata(m) for m in models],
    }


async def lock_owner(session, user_id):
    row = (await session.execute(select(User).where(User.id == user_id, User.is_active.is_(True)).with_for_update())).scalar_one_or_none()
    if row is None:
        raise PermissionError("Personal catalog owner is inactive")


async def set_default_in_session(session, model):
    if not model.is_active or not model.provider.is_active:
        raise ValueError("Default model and provider must be active")
    rows = (await session.execute(select(LLMModel).where(
        LLMModel.owner_user_id == model.owner_user_id, LLMModel.is_default.is_(True)
    ).with_for_update())).scalars().all()
    for other in rows:
        if other.id != model.id and set(other.capabilities) & set(model.capabilities):
            other.is_default = False
    model.is_default = True
    await session.flush()


async def catalog_metadata(*, user_id):
    require_actor(user_id)
    providers = await LLMProvider.objects.filter(owner_user_id=user_id).order_by(LLMProvider.id)
    models = await LLMModel.objects.select_related("provider").filter(owner_user_id=user_id).order_by(LLMModel.id)
    eligible = [m for m in models if eligible_chat_model(m, user_id=user_id)]
    default = min(eligible, key=lambda m: (not m.is_default, m.id), default=None)
    return {
        "providers": [provider_metadata(p, [m for m in models if m.provider_id == p.id]) for p in providers],
        "models": [model_metadata(m) for m in models],
        "effective_default_model_id": default.id if default else None,
    }


async def save_provider_bundle(*, user_id, provider, models):
    require_actor(user_id)
    if not isinstance(models, list) or len(models) > 100:
        raise ValueError("At most 100 models may be saved together")
    if not isinstance(provider, dict) or any(not isinstance(model, dict) for model in models):
        raise ValueError("Provider and model rows must be objects")
    values = validate_provider_values(provider, creating=not provider.get("id"))
    model_values = [validate_model_values(m, creating=not m.get("id")) for m in models]
    ids = [m["id"] for m in model_values if "id" in m]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate model IDs in bundle")
    from app.utils.crypto import encrypt_secret
    from sqlalchemy.exc import IntegrityError
    try:
        async with LLMProvider.objects._get_owned_session() as session:
            await lock_owner(session, user_id)
            provider_id = values.pop("id", None)
            if provider_id is not None:
                row = (await session.execute(select(LLMProvider).where(
                    LLMProvider.id == provider_id, LLMProvider.owner_user_id == user_id
                ).with_for_update())).scalar_one_or_none()
                if row is None:
                    raise PermissionError("Provider is unavailable")
            else:
                row = LLMProvider(owner_user_id=user_id)
                session.add(row)
            key = values.pop("api_key", "")
            if key:
                row.encrypted_api_key = encrypt_secret(key)
            for key, value in values.items():
                setattr(row, key, value)
            await session.flush()
            changed_defaults = []
            saved_models = []
            for data in model_values:
                identity = data.pop("id", None)
                if identity is not None:
                    model = (await session.execute(select(LLMModel).where(
                        LLMModel.id == identity, LLMModel.owner_user_id == user_id, LLMModel.provider_id == row.id
                    ).with_for_update())).scalar_one_or_none()
                    if model is None:
                        raise PermissionError("Model is unavailable in this provider")
                else:
                    model = LLMModel(owner_user_id=user_id, provider_id=row.id, provider=row)
                    session.add(model)
                for key, value in data.items():
                    setattr(model, key, value)
                saved_models.append(model)
                if data.get("is_default"):
                    changed_defaults.append(model)
            await session.flush()
            seen_caps = set()
            for model in changed_defaults:
                if seen_caps & set(model.capabilities):
                    raise ValueError("Conflicting capability defaults in bundle")
                seen_caps.update(model.capabilities)
                await set_default_in_session(session, model)
            rows = (await session.execute(select(LLMModel).where(
                LLMModel.provider_id == row.id, LLMModel.owner_user_id == user_id
            ).options(selectinload(LLMModel.provider)).order_by(LLMModel.id))).scalars().all()
            result = provider_metadata(row, rows)
            result["saved_model_ids"] = [model.id for model in saved_models]
        return result
    except IntegrityError:
        raise ValueError("Catalog UID conflicts or referenced record changed") from None


async def set_default_model(*, user_id, model_id):
    require_actor(user_id)
    _identifier(model_id)
    async with LLMModel.objects._get_owned_session() as session:
        await lock_owner(session, user_id)
        model = (await session.execute(select(LLMModel).where(
            LLMModel.id == model_id, LLMModel.owner_user_id == user_id
        ).options(selectinload(LLMModel.provider)).with_for_update())).scalar_one_or_none()
        if model is None:
            raise PermissionError("Model is unavailable")
        await set_default_in_session(session, model)
        result = model_metadata(model)
    return result


async def _workspace_types(user_id, tenant_id):
    from app.models.managers.tenant_manager import tenant_users
    memberships = await tenant_users.web_memberships(user_id)
    if not any(m.tenant_id == tenant_id for m in memberships):
        raise PermissionError("Workspace access denied")
    tenant = await Tenant.objects.get(id=tenant_id, is_active=True)
    if tenant is None:
        raise PermissionError("Workspace unavailable")
    return tenant.plan_limits().get("model_types")


async def chat_model_options(*, user_id, tenant_id):
    require_actor(user_id)
    allowed = await _workspace_types(user_id, tenant_id)
    rows = await LLMModel.objects.select_related("provider").filter(owner_user_id=user_id, is_active=True)
    eligible = [m for m in rows if eligible_chat_model(m, user_id=user_id, allowed_types=allowed)]
    eligible.sort(key=lambda m: (not m.is_default, m.id))
    default_id = eligible[0].id if eligible else None
    return [{"id": m.id, "label": f"{m.provider.name}/{m.model_id}" + (" — По умолчанию" if m.id == default_id else ""),
             "is_default": m.is_default, "is_effective_default": m.id == default_id} for m in eligible]


async def validate_chat_model(*, user_id, tenant_id, model_id):
    require_actor(user_id)
    allowed = await _workspace_types(user_id, tenant_id)
    if model_id is None:
        return None
    _identifier(model_id)
    model = await LLMModel.objects.select_related("provider").get(id=model_id, owner_user_id=user_id)
    if model is None or not eligible_chat_model(model, user_id=user_id, allowed_types=allowed):
        raise ValueError("Chat model is unavailable for this user/workspace")
    return model
