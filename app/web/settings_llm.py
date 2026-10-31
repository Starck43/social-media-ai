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

    user_id = _actor_id(request)
    metadata = await catalog_metadata(user_id=user_id)
    # These are aggregate provider invocation counters, not test-only counts.
    from app.models.managers.llm_model_manager import LLMModelManager

    counters = {
        model.id: (int(model.use_count or 0), int(model.fail_count or 0), model.last_error_at, model.last_success_at)
        for model in await LLMModelManager().filter(owner_user_id=user_id)
    }
    for model in metadata["models"]:
        uses, failures, last_error, last_success = counters.get(model["id"], (0, 0, None, None))
        model["use_count"], model["fail_count"] = uses, failures
        model["success_count"] = max(0, uses - failures)
        model["has_recent_error"] = last_error is not None and (last_success is None or last_error > last_success)
    for provider in metadata["providers"]:
        for model in provider["models"]:
            uses, failures, last_error, last_success = counters.get(model["id"], (0, 0, None, None))
            model["use_count"], model["fail_count"] = uses, failures
            model["success_count"] = max(0, uses - failures)
            model["has_recent_error"] = last_error is not None and (last_success is None or last_error > last_success)
    probe_result = request.session.get("settings_model_probe_result")
    if not isinstance(probe_result, dict) or probe_result.get("owner_user_id") != user_id:
        probe_result = None
    tenant = getattr(request.state, "tenant", None)
    return {
        "llm_catalog": metadata,
        "llm_chat_model_id": getattr(tenant, "agent_model_id", None),
        "llm_provider_choices": [
            {key: provider[key] for key in ("id", "name", "uid", "base_url", "has_api_key")}
            for provider in metadata["providers"]
        ],
        "model_probe_result": probe_result if isinstance(probe_result, dict) else None,
        "settings_tenant_id": getattr(request.state, "tenant_id", None),
    }


async def _run_probe(request: Request, *, model_id: str, mode: str, tenant_id: int | None, consent: str):
    """Run a current owner-scoped local or explicitly consented provider test."""
    try:
        user_id = _actor_id(request)
    except PermissionError:
        return HTMLResponse("Недостаточно прав", status_code=403)
    form_token = (await request.form()).get("_csrf")
    if not ensure_csrf(request, form_token):
        return HTMLResponse("Недействительный CSRF-токен", status_code=403)
    if mode not in {"local", "real"}:
        add_flash(request, "error", "Неизвестный вид проверки")
        return RedirectResponse(BACK, status_code=303)
    if mode == "real" and consent != "yes":
        add_flash(request, "error", "Подтвердите отправку короткого запроса провайдеру")
        return RedirectResponse(BACK, status_code=303)
    from app.models import LLMModel
    from app.services.ai.personal_catalog import require_actor

    require_actor(user_id)
    try:
        if not model_id.isascii() or not model_id.isdecimal() or int(model_id) <= 0:
            raise ValueError
        model = await LLMModel.objects.select_related("provider").get(id=int(model_id), owner_user_id=user_id)
        if model is None:
            raise PermissionError
    except (ValueError, PermissionError):
        add_flash(request, "error", "Выбранная модель недоступна")
        return RedirectResponse(BACK, status_code=303)

    result = None
    if mode == "local":
        from app.services.ai.model_probe import probe_short

        result = probe_short(model)
    else:
        # The real request is bound to the authenticated request workspace; the
        # billing admission path rechecks membership, payer, quota and model.
        active_tenant_id = getattr(request.state, "tenant_id", None)
        if type(tenant_id) is not int or tenant_id <= 0 or tenant_id != active_tenant_id:
            add_flash(request, "error", "Для реального теста выберите своё активное пространство")
            return RedirectResponse(BACK, status_code=303)
        from app.models.managers.tenant_manager import tenants

        tenant = await tenants.get(id=tenant_id, is_active=True)
        if tenant is None:
            add_flash(request, "error", "Активное пространство не найдено")
            return RedirectResponse(BACK, status_code=303)
        from app.services.ai.model_probe import probe_real_capped, provider_limit_hint

        result = await probe_real_capped(model, purpose="model_test")
        result["limit_hint"] = provider_limit_hint(result, model)

    # Keep only a compact, safe status in the signed session cookie. Provider
    # response bodies, content and usage diagnostics never enter the cookie.
    text = result.get("text") or result.get("error")
    if not text:
        if result.get("ok"):
            text = "Реальный короткий запрос выполнен." if mode == "real" else "Локальная проверка пройдена."
        else:
            text = "Реальный запрос не выполнен." if mode == "real" else "Локальная проверка не пройдена."
    hint = result.get("limit_hint")
    request.session["settings_model_probe_result"] = {
        "model_id": int(model_id),
        "model_name": str(model.name)[:100],
        "owner_user_id": user_id,
        "mode": mode,
        "ok": bool(result.get("ok")),
        "text": str(text)[:300],
        "limit_hint": (
            {"code": str(hint.get("code", ""))[:80], "message": str(hint.get("message", ""))[:240]}
            if isinstance(hint, dict)
            else None
        ),
    }
    return RedirectResponse(BACK, status_code=303)


@router.post("/probe")
async def probe_model(request: Request):
    form = await request.form()
    return await _run_probe(
        request,
        model_id=str(form.get("model_id", "")),
        mode=str(form.get("mode", "")),
        tenant_id=int(form["tenant_id"]) if str(form.get("tenant_id", "")).isdecimal() else None,
        consent=str(form.get("consent", "")),
    )


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
