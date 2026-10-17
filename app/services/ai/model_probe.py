"""Shared offline checks, admin metadata connectivity and bounded billed requests.

Web settings retain the offline ``probe_short`` contract. The admin's explicit
short POST uses ``probe_connectivity`` (metadata only, never generation).
``probe_real_capped`` defaults to an eight-token probe for existing callers;
admin-supplied questions get a bounded, usable answer via the same billed client.
"""

from __future__ import annotations

import logging
from datetime import datetime
from time import monotonic
from typing import Any

import httpx

logger = logging.getLogger(__name__)

#: Fixed minimal probe prompt — the model must answer with a single word.
#: Keeps the real probe at a handful of tokens in both directions.
PROBE_PROMPT = "Ответь одним словом: да"
PROBE_MAX_TOKENS = 8
PROBE_TIMEOUT = 20.0
REQUEST_MAX_TOKENS = 512


def probe_status(model) -> dict[str, Any]:
    """Level 0 — free local verdict, no provider traffic.

    Returns ``{"ok": bool, "checks": [...], "hint": str}``. Each check is a
    ``(label, passed)`` pair for the UI. Never touches the network.
    """
    provider = getattr(model, "provider", None)
    checks: list[tuple[str, bool]] = []
    try:
        has_key = bool(provider.get_api_key()) if provider else False
    except Exception:  # noqa: BLE001 — a broken key accessor is itself a failure
        has_key = False
    checks.append(("API-ключ настроен", has_key))
    checks.append(("Модель активна", bool(getattr(model, "is_active", False))))
    checks.append(("Провайдер активен", bool(provider and getattr(provider, "is_active", False))))

    last_error = getattr(model, "last_error_at", None)
    last_success = getattr(model, "last_success_at", None)
    if isinstance(last_error, datetime) and isinstance(last_success, datetime):
        healthy = last_success >= last_error
    elif last_error is not None:
        healthy = False
    else:
        healthy = True
    checks.append(("Нет недавних ошибок", healthy))

    failed = [label for label, passed in checks if not passed]
    ok = not failed
    hint = (
        "Всё хорошо — можно запускать mock или реальный запрос."
        if ok
        else "Сначала исправьте: " + "; ".join(failed) + "."
    )
    return {"ok": ok, "checks": checks, "hint": hint, "tokens_spent": 0}


def probe_mock(model, prompt: str) -> dict[str, Any]:
    """Level 1 — canned local answer, zero tokens, zero network.

    Mirrors the real probe's response shape (content/usage/cost) so the UI
    renders both identically; only ``is_real_response`` differs.
    """
    from app.services.ai.llm_client import price_usage_usd

    prompt_tokens, completion_tokens = 8, 12
    content = (
        f"[МОК-ПРОБА] Модель {model.name} ({model.model_id}): локальная имитация ответа. " "Провайдер не вызывался."
    )
    cost = price_usage_usd(model, prompt_tokens, completion_tokens)
    return {
        "content": content,
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
        "cost": cost,
        "tokens_spent": 0,
        "is_real_response": False,
        "prompt_echo": prompt,
    }


def probe_short(model) -> dict[str, Any]:
    """Prompt-free local status/mock check shared by web and operator UI.

    Never dispatch a provider call or silently escalate to a paid tier. Success
    means local configuration/mock checks passed, not remote reachability.
    """
    verdict = probe_status(model) if model is not None else {"ok": False, "checks": [], "hint": "Модель не выбрана"}
    result = {
        "ok": False,
        "tier": "status",
        "text": verdict["hint"],
        "checks": verdict["checks"],
        "tokens_spent": 0,
    }
    if not verdict["ok"]:
        return result
    try:
        mock = probe_mock(model, "Локальная проверка конфигурации")
    except Exception:
        result["text"] = "Локальная мок-проверка не пройдена. Проверьте настройки модели."
        result["tier"] = "mock"
        return result
    result.update(
        ok=bool(mock.get("ok", True)),
        tier="mock",
        text=(
            "Статус и локальный mock: OK. Провайдер не вызывался."
            if mock.get("ok", True)
            else "Локальная мок-проверка не пройдена."
        ),
        content=mock.get("content", ""),
    )
    return result


def request_parameters(model, prompt: str = "") -> tuple[str, int]:
    """Preserve the tiny default; cap custom answers by both UI and model limits."""
    if len(prompt) > 8000:
        raise ValueError("Prompt exceeds 8000 characters")
    question = prompt.strip()
    limit = REQUEST_MAX_TOKENS if question else PROBE_MAX_TOKENS
    return question or PROBE_PROMPT, min(limit, max(1, int(model.max_tokens)))


def _dispatch_ready(model) -> bool:
    # Past failures are diagnostic, not a permanent ban on testing recovery.
    return all(passed for _, passed in probe_status(model)["checks"][:3])


async def probe_connectivity(model) -> dict[str, Any]:
    """Authenticated OpenAI metadata GET, no prompt/completion or paid fallback.

    A valid model list proves this endpoint/key responded, not that generation
    works. Unsupported metadata routes must remain failures, never chat retries.
    """
    from app.services.ai.llm_client import LLMClientFactory

    result = {"ok": False, "tier": "connectivity", "tokens_spent": 0, "method": "GET /models"}
    if not _dispatch_ready(model):
        return {**result, "text": "Проверьте API-ключ и активность модели/провайдера."}
    if model.provider.api_format != "openai":
        return {**result, "text": "Короткая API-проверка поддерживает только OpenAI-совместимый GET /models."}
    started = monotonic()
    try:
        client = LLMClientFactory.create(model)
        async with httpx.AsyncClient(follow_redirects=False) as http:
            response = await http.get(f"{client.base_url}/models", headers=client._headers(), timeout=PROBE_TIMEOUT)
        result["http_status"] = response.status_code
        if response.status_code != 200:
            hints = {
                401: "API отклонил ключ (HTTP 401).",
                403: "API отказал в доступе (HTTP 403).",
                404: "GET /models не найден (HTTP 404). Генерация не проверялась.",
                405: "GET /models не поддерживается (HTTP 405). Генерация не проверялась.",
                429: "API ограничил частоту запросов или квоту (HTTP 429).",
            }
            result["text"] = hints.get(response.status_code, "API вернул ошибку. Проверьте подключение провайдера.")
        else:
            data = response.json()
            models = data.get("data") if isinstance(data, dict) else None
            if not isinstance(models, list) or any(
                not isinstance(row, dict) or not isinstance(row.get("id"), str) for row in models
            ):
                result["text"] = "API ответил, но формат списка моделей не подтверждён. Генерация не проверялась."
            else:
                available = any(row["id"] == model.model_id for row in models)
                result.update(
                    ok=True,
                    model_available=available,
                    models_count=len(models),
                    text="API доступен: авторизованный список моделей получен. Генерация ответа не запускалась.",
                )
    except httpx.TimeoutException:
        result["text"] = "API не ответил за 20 секунд. Генерация не запускалась."
    except Exception:
        # Never expose provider bodies, URLs, request headers or exception text.
        result["text"] = "Не удалось проверить API. Проверьте адрес, ключ и формат ответа провайдера."
    result["elapsed_ms"] = round((monotonic() - started) * 1000)
    return result


async def probe_real_capped(model, prompt: str = "") -> dict[str, Any]:
    """One bounded billed request; never fall back to an unaccounted HTTP path.

    The selected client owns admission, durable dispatch/outcome accounting and
    transport. A failure after dispatch is ambiguous and must not trigger retry.
    """
    from app.core.config import settings
    from app.services.ai.llm_client import LLMClientFactory, journal_chat_attempt

    question, max_tokens = request_parameters(model, prompt)
    if not _dispatch_ready(model):
        return {"ok": False, "error": "Проверьте API-ключ и активность модели/провайдера.", "tokens_spent": 0}
    if not settings.PERSONAL_BILLING_ENABLED:
        return {"ok": False, "error": "Личный учёт расходов ещё не включён", "tokens_spent": 0}
    if getattr(getattr(model, "provider", None), "api_format", None) != "openai":
        return {"ok": False, "error": "Нужен OpenAI-совместимый API", "tokens_spent": 0}
    try:
        client = LLMClientFactory.create(model)
    except Exception:
        return {"ok": False, "error": "Не удалось создать клиент. Проверьте конфигурацию.", "tokens_spent": 0}
    if not client.api_key:
        return {"ok": False, "error": "API-ключ провайдера не настроен", "tokens_spent": 0}

    # This is a newly created client, not a shared setting. Transport controls
    # stay out of provider JSON; chat() reads its timeout from the client.
    client.timeout = PROBE_TIMEOUT
    try:
        response = await client.chat(
            [{"role": "user", "content": question}],
            max_tokens=max_tokens,
            temperature=0.0,
            personal_billing_purpose="admin_probe",
        )
    except Exception:
        logger.warning("Billed probe response/accounting unconfirmed for model %s", getattr(model, "id", "?"))
        return {
            "ok": False,
            "error": "Ответ или учёт расходов не подтверждены. Не повторяйте вызов автоматически.",
            "tokens_spent": None,
        }
    if not isinstance(response, dict):
        return {"ok": False, "error": "Ответ не подтверждён. Не повторяйте автоматически.", "tokens_spent": None}

    dispatch_id = response.pop("_personal_dispatch_id", None)
    journal_warning = False
    try:
        await journal_chat_attempt(client, "admin_probe", response, dispatch_id=dispatch_id)
    except Exception:
        # A secondary journal failure must not re-dispatch or erase an answer.
        journal_warning = True
    raw = response.get("raw")
    # The chat compatibility shape fills missing usage with zeros. Read only
    # provider evidence so unknown usage is never mislabeled as free.
    usage = raw.get("usage") if isinstance(raw, dict) else None
    usage = usage if isinstance(usage, dict) else {}

    def count(key):
        value = usage.get(key)
        return value if type(value) is int and value >= 0 else None

    incoming, outgoing = count("prompt_tokens"), count("completion_tokens")
    total = incoming + outgoing if incoming is not None and outgoing is not None else None
    text = response.get("content")
    if not isinstance(text, str) or not text.strip():
        return {
            "ok": False,
            "error": "Провайдер вернул пустой ответ. Возможен расход токенов; автоматического повтора нет.",
            "tokens_spent": total,
            "usage": {"prompt_tokens": incoming, "completion_tokens": outgoing, "total_tokens": total},
        }
    result = {
        "ok": True,
        "content": text,
        "usage": {"prompt_tokens": incoming, "completion_tokens": outgoing, "total_tokens": total},
        "tokens_spent": total,
        "is_real_response": True,
        "request_prompt": question,
        "max_tokens": max_tokens,
    }
    if journal_warning:
        result["save_warning"] = "Ответ получен, но журнал не подтверждён. Не повторяйте вызов автоматически."
    return result
