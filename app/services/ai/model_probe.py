"""Economical model probes: status (free) → mock (free, no network) → real capped.

Tiered testing for LLM models. The cheapest conclusive level wins:

* Level 0 — ``probe_status``: no provider traffic at all. Local config +
  history verdicts. Stops ~80% of wasted calls before any token is spent.
* Level 1 — ``probe_mock``: canned local answer, no network. Verifies the
  app-side pipeline (routing, parsing, cost plumbing) for 0 tokens.
* Level 2 — ``probe_real_capped``: one minimal provider call with a fixed
  one-word prompt (``max_tokens=8``, timeout 20s). Proves the key/route
  works for a fraction of a cent instead of a full chat answer.

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
        f"[МОК-ПРОБА] Модель {model.name} ({model.model_id}): маршрут, парсинг и "
        f"учёт стоимости проверены локально, провайдер не вызывался."
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


async def probe_real_capped(model, prompt: str = PROBE_PROMPT) -> dict[str, Any]:
    """Level 2 — one minimal provider call, capped cost.

    Fixed one-word prompt, ``max_tokens=8``, 20s timeout. Small model answers
    cost a fraction of a cent; the point is reachability, not quality.
    """
    import httpx

    from app.services.ai.llm_client import LLMClientFactory

    try:
        client = LLMClientFactory.create(model)
    except Exception as exc:  # noqa: BLE001 — factory errors are probe failures
        return {"ok": False, "error": f"Не удалось создать клиент: {exc}", "tokens_spent": 0}
    if not client.api_key:
        return {"ok": False, "error": "API-ключ провайдера не настроен", "tokens_spent": 0}

    from app.core.config import settings

    if settings.PERSONAL_BILLING_ENABLED:
        try:
            from app.services.ai.llm_client import journal_chat_attempt

            response = await client.chat(
                [{"role": "user", "content": prompt}],
                max_tokens=PROBE_MAX_TOKENS,
                temperature=0.0,
                timeout=PROBE_TIMEOUT,
                personal_billing_purpose="admin_probe",
            )
            dispatch_id = response.pop("_personal_dispatch_id", None)
            await journal_chat_attempt(client, "admin_probe", response, dispatch_id=dispatch_id)
            raw = response.get("raw") if isinstance(response, dict) else None
            usage = response.get("usage") if isinstance(response, dict) else None
            usage = usage if isinstance(usage, dict) else {}
            return {
                "ok": True,
                "content": response.get("content", ""),
                "usage": {
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0),
                },
                "tokens_spent": usage.get("total_tokens", 0),
                "is_real_response": True,
                "raw": raw,
            }
        except Exception as exc:
            from app.services.billing.errors import raise_personal_billing_block

            raise_personal_billing_block(exc)
            logger.warning("Personal admin probe failed model_id=%s", getattr(model, "id", "?"))
            return {"ok": False, "error": str(exc), "tokens_spent": 0}

    payload: dict[str, Any] = {
        "model": client.model_name,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": PROBE_MAX_TOKENS,
        "stream": False,
    }
    try:
        async with httpx.AsyncClient() as http:
            response = await http.post(
                f"{client.base_url}/chat/completions",
                headers=client._headers(),
                json=payload,
                timeout=PROBE_TIMEOUT,
            )
            if response.status_code != 200:
                return {
                    "ok": False,
                    "error": f"Провайдер ответил {response.status_code}: {response.text[:300]}",
                    "tokens_spent": 0,
                }
            data = response.json()
    except Exception as exc:  # noqa: BLE001 — network/timeout are probe failures
        logger.warning("Real probe failed for model %s: %s", getattr(model, "id", "?"), exc)
        return {"ok": False, "error": f"Сеть/таймаут: {exc}", "tokens_spent": 0}

    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return {"ok": False, "error": "Провайдер вернул пустой ответ", "tokens_spent": 0}
    usage = data.get("usage") or {}
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    return {
        "ok": True,
        "content": text,
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
        "tokens_spent": prompt_tokens + completion_tokens,
        "is_real_response": True,
    }

