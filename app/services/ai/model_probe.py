"""Local model checks and explicitly requested, capped provider probes.

Tiered testing for LLM models. Local checks and explicitly requested provider probes are distinct:

* Level 0 — ``probe_status``: no provider traffic at all. Local config +
  history verdicts; not proof of remote reachability.
* Level 1 — ``probe_mock``: canned local answer, no network. Verifies the
  response/usage display shape for 0 tokens; not an end-to-end routing check.
* Level 2 — ``probe_real_capped``: one minimal provider call with a fixed
  one-word prompt (``max_tokens=8``, timeout 20s). Proves the key/route
  returned a response; actual provider charges depend on its pricing.

The admin ``test_model`` action, ``/app/settings`` chat-model check and the
scenario model check all share this module, so the economy rule lives in one
place.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

#: Fixed minimal probe prompt — the model must answer with a single word.
#: Keeps the real probe at a handful of tokens in both directions.
PROBE_PROMPT = "Ответь одним словом: да"
PROBE_MAX_TOKENS = 8
PROBE_TIMEOUT = 20.0


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
        "Всё хорошо — можно запускать мок или реальную пробу."
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


async def probe_real_capped(model) -> dict[str, Any]:
    """One billed fixed probe; never fall back to an unaccounted HTTP path.

    The selected client owns admission, durable dispatch/outcome accounting and
    transport. A failure after dispatch is ambiguous and must not trigger retry.
    """
    from app.core.config import settings
    from app.services.ai.llm_client import LLMClientFactory, journal_chat_attempt

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
            [{"role": "user", "content": PROBE_PROMPT}],
            max_tokens=PROBE_MAX_TOKENS,
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
        return {"ok": False, "error": "Провайдер вернул пустой ответ", "tokens_spent": total}
    result = {
        "ok": True,
        "content": text,
        "usage": {"prompt_tokens": incoming, "completion_tokens": outgoing, "total_tokens": total},
        "tokens_spent": total,
        "is_real_response": True,
    }
    if journal_warning:
        result["save_warning"] = "Ответ получен, но журнал не подтверждён. Не повторяйте вызов автоматически."
    return result
