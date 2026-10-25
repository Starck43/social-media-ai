"""Personal provider/model forms; persistence and eligibility belong to the catalog service."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .deps import add_flash, ensure_csrf

router = APIRouter(prefix="/settings/models")
BACK = "/app/settings?tab=models"
MAX_MODELS = 50


def _actor_id(request: Request) -> int:
    user = getattr(request.state, "web_user", None)
    user_id = getattr(user, "id", None)
    if not getattr(user, "is_active", False) or type(user_id) is not int or user_id <= 0:
        raise PermissionError("Active personal identity required")
    return user_id


async def settings_llm_context(request: Request) -> dict:
    from app.services.ai.personal_catalog import catalog_metadata

    metadata = await catalog_metadata(user_id=_actor_id(request))
    tenant = getattr(request.state, "tenant", None)
    return {
        "llm_catalog": metadata,
        "llm_chat_model_id": getattr(tenant, "agent_model_id", None),
        "llm_provider_choices": [
            {key: provider[key] for key in ("id", "name", "uid", "base_url", "has_api_key")}
            for provider in metadata["providers"]
        ],
    }


def _optional_id(raw: str) -> int | None:
    if not raw:
        return None
    if not raw.isascii() or not raw.isdecimal() or int(raw) <= 0:
        raise ValueError("Invalid identifier")
    return int(raw)


def _bundle(form) -> tuple[dict, list[dict]]:
    """Accept only visible editable fields, never ownership or hidden model settings."""
    provider = {
        "name": str(form.get("provider_name", "")).strip(),
        "uid": str(form.get("provider_uid", "")).strip(),
        "base_url": str(form.get("base_url", "")).strip(),
    }
    provider_id = _optional_id(str(form.get("provider_id", "")))
    if provider_id is not None:
        provider["id"] = provider_id
    api_key = str(form.get("api_key", "")).strip()
    if api_key:
        provider["api_key"] = api_key
    if not provider["name"] or not provider["uid"]:
        raise ValueError("Provider name and UID required")
    names = form.getlist("model_name")
    api_ids = form.getlist("model_api_id")
    ids = form.getlist("model_db_id")
    if len(names) != len(api_ids) or len(names) != len(ids) or len(names) > MAX_MODELS:
        raise ValueError("Invalid model rows")
    models = []
    seen = set()
    for name, api_id, raw_id in zip(names, api_ids, ids):
        name, api_id = str(name).strip(), str(api_id).strip()
        model_id = _optional_id(str(raw_id))
        if not name and not api_id and model_id is None:
            continue
        if not name or not api_id:
            raise ValueError("Both model fields required")
        item = {"name": name, "model_id": api_id}
        if model_id is not None:
            if model_id in seen:
                raise ValueError("Duplicate model identifier")
            seen.add(model_id)
            item["id"] = model_id
        models.append(item)
    return provider, models


@router.post("/save")
async def save_bundle(request: Request):
    try:
        user_id = _actor_id(request)
    except PermissionError:
        return HTMLResponse("Недостаточно прав", status_code=403)
    form = await request.form()
    if not ensure_csrf(request, form.get("_csrf")):
        return HTMLResponse("Недействительный CSRF-токен", status_code=403)
    try:
        from app.services.ai.personal_catalog import save_provider_bundle

        provider, models = _bundle(form)
        await save_provider_bundle(user_id=user_id, provider=provider, models=models)
    except (ValueError, PermissionError):
        add_flash(request, "error", "Не удалось сохранить. Проверьте поля и доступ к провайдеру и моделям.")
    else:
        add_flash(request, "success", "Провайдер и модели сохранены")
    return RedirectResponse(BACK, status_code=303)


@router.post("/default")
async def set_default(request: Request):
    try:
        user_id = _actor_id(request)
    except PermissionError:
        return HTMLResponse("Недостаточно прав", status_code=403)
    form = await request.form()
    if not ensure_csrf(request, form.get("_csrf")):
        return HTMLResponse("Недействительный CSRF-токен", status_code=403)
    try:
        from app.services.ai.personal_catalog import set_default_model

        model_id = _optional_id(str(form.get("model_id", "")))
        if model_id is None:
            raise ValueError("Model required")
        await set_default_model(user_id=user_id, model_id=model_id)
    except (ValueError, PermissionError):
        add_flash(request, "error", "Не удалось выбрать модель. Проверьте её доступность.")
    else:
        add_flash(request, "success", "Модель по умолчанию изменена")
    return RedirectResponse(BACK, status_code=303)
