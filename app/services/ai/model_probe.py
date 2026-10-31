"""Shared offline checks, metadata connectivity and bounded billed requests.

``probe_short`` is local-only; ``probe_connectivity`` checks metadata without
generation. Explicit ``probe_real_capped`` calls use the same billed client and
may spend tokens. All real probes retain bounded output and admission controls.
"""

from __future__ import annotations

import json
import logging
import re
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


def safe_probe_value(value, api_key: str = "", *, depth: int = 0, budget=None):
    """Bounded JSON display copy; never mutate evidence or render provider HTML.

    Hide credential fields and the configured key even if echoed in other fields.
    Keep token-usage fields intact. Mark truncated diagnostics, never invent data.
    """
    budget = [200_000] if budget is None else budget
    if depth > 20 or budget[0] <= 0:
        return "[truncated]"
    budget[0] -= 1

    def text(raw):
        raw = str(raw)
        raw = raw.replace(api_key, "[redacted]") if api_key else raw
        raw = re.sub(r"(?i)Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [redacted]", raw)
        raw = re.sub(
            r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret|session(?:id)?|cookie)\s*([=:])\s*([^\s&,;\"'<>]+)",
            r"\1\2[redacted]",
            raw,
        )
        limit = min(20_000, max(0, budget[0]))
        budget[0] -= min(len(raw), limit)
        return raw if len(raw) <= limit else raw[:limit] + "[truncated]"

    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if budget[0] <= 0:
                result["_diagnostic_truncated"] = True
                break
            label = text(key)
            sensitive = re.search(
                r"(?i)(api.?key|authorization|access.?token|refresh.?token|password|secret|cookie|credential|session|^token$)",
                str(key),
            )
            result[label] = (
                "[redacted]" if sensitive else safe_probe_value(item, api_key, depth=depth + 1, budget=budget)
            )
        return result
    if isinstance(value, list):
        result = []
        for item in value:
            if budget[0] <= 0:
                result.append("[truncated]")
                break
            result.append(safe_probe_value(item, api_key, depth=depth + 1, budget=budget))
        return result
    if isinstance(value, str):
        return text(value)
    return value if value is None or isinstance(value, (bool, int, float)) else text(value)


def _real_result(observation, response=None, *, api_key="", error=None):
    """Separate provider reply, usable text and accounting; absence is not zero."""
    from app.services.ai.attempt_journal import provider_cost_evidence

    response = response if isinstance(response, dict) else {}
    raw = observation.get("raw_response", response.get("raw"))
    raw_usage = raw.get("usage", {}) if isinstance(raw, dict) else {}
    raw_usage = raw_usage if isinstance(raw_usage, dict) else {}

    def count(key):
        value = raw_usage.get(key)
        return value if type(value) is int and value >= 0 else None

    incoming, outgoing = count("prompt_tokens"), count("completion_tokens")
    total = incoming + outgoing if incoming is not None and outgoing is not None else None
    choices = raw.get("choices") if isinstance(raw, dict) else None
    choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
    message = choice.get("message")
    message = message if isinstance(message, dict) else {}
    content = response.get("content", message.get("content"))
    content = content if isinstance(content, str) else ""
    provider_error = isinstance(raw, dict) and bool(raw.get("error"))
    cost_claimed, provider_cost = provider_cost_evidence(raw)
    result = {
        "ok": bool(content.strip()) and not error and not provider_error,
        "tier": "real",
        "content": safe_probe_value(content, api_key),
        "stage": observation.get("stage", "unknown"),
        "dispatch_state": observation.get("dispatch_state", "unknown"),
        "http_status": observation.get("http_status"),
        "accounting_status": observation.get("accounting_status", "not_confirmed"),
        "finish_reason": safe_probe_value(response.get("finish_reason", choice.get("finish_reason")), api_key),
        "usage": {"prompt_tokens": incoming, "completion_tokens": outgoing, "total_tokens": total},
        "tokens_spent": total,
        # Structured provider evidence only; never overwrite the configured tariff
        # estimate or infer a price from generated text/upstream supplier costs.
        "provider_cost_usd": str(provider_cost) if provider_cost is not None else None,
        "provider_cost_state": "resolved" if provider_cost is not None else "unresolved" if cost_claimed else "absent",
        "response_json": safe_probe_value(raw, api_key),
        "response_body": safe_probe_value(observation.get("response_body"), api_key),
        "effective_max_tokens": observation.get("effective_max_tokens"),
    }
    if result["response_json"] is not None:
        result["response_json_text"] = json.dumps(result["response_json"], ensure_ascii=False, indent=2)
    if error:
        result.update(error=error[1], error_code=error[0])
    elif provider_error:
        result.update(error="API вернул JSON с ошибкой. См. ответ провайдера ниже.", error_code="provider_error")
    elif not content.strip():
        result.update(
            error="Ответ API получен, но текст сообщения пуст. Проверьте JSON и finish_reason; автоматического повтора нет.",
            error_code="empty_content",
        )
    return result


async def probe_real_capped(model, prompt: str = "", *, purpose: str = "admin_probe") -> dict[str, Any]:
    """One bounded provider request using the client's active accounting mode.

    Never bypass admission or retry. Preserve received provider evidence even
    when parsing or subsequent accounting fails; diagnostics are display copies.
    """
    from app.services.ai.llm_client import LLMClientFactory, journal_chat_attempt

    if purpose not in {"admin_probe", "model_test"}:
        raise ValueError("Unsupported model probe purpose")
    question, max_tokens = request_parameters(model, prompt)

    def blocked(code, message):
        return {
            "ok": False,
            "error_code": code,
            "error": message,
            "stage": "preflight",
            "dispatch_state": "not_dispatched",
            "tokens_spent": 0,
        }

    if not _dispatch_ready(model):
        return blocked("configuration", "Проверьте API-ключ и активность модели/провайдера. Запрос не отправлен.")

    if getattr(getattr(model, "provider", None), "api_format", None) != "openai":
        return blocked("unsupported_format", "Нужен OpenAI-совместимый API. Запрос не отправлен.")
    try:
        client = LLMClientFactory.create(model)
    except Exception:
        return blocked(
            "client_configuration", "Не удалось создать клиент. Проверьте конфигурацию; запрос не отправлен."
        )
    if not client.api_key:
        return blocked("missing_key", "API-ключ провайдера не настроен. Запрос не отправлен.")
    observation = {"stage": "client", "dispatch_state": "unknown"}
    client._probe_diagnostics = observation
    client.timeout = PROBE_TIMEOUT
    try:
        response = await client.chat(
            [{"role": "user", "content": question}],
            max_tokens=max_tokens,
            temperature=0.0,
            personal_billing_purpose=purpose,
        )
    except Exception as exc:
        stage = observation.get("stage")
        if stage == "admission":
            # Match only fixed repository-owned messages. Never display arbitrary
            # exception text, SQL, account identities or credentials.
            reasons = {
                "Payer has no configured personal daily limit": (
                    "personal_account_required",
                    "У администратора нет настроенного личного счёта с дневным лимитом.",
                ),
                "Reviewed clean-day billing boundary is not active": (
                    "billing_cutover_required",
                    "Не активирована проверенная граница начала личного учёта расходов (cutover).",
                ),
                "Capped payer requires a reviewed provider/model request bound": (
                    "model_bound_required",
                    "Для модели не настроена проверенная политика ограничения стоимости запроса.",
                ),
                "Personal daily limit would be exceeded": (
                    "daily_limit_exceeded",
                    "Запрос превысит личный дневной лимит расходов.",
                ),
                "Current payer/day cap would be exceeded": (
                    "daily_limit_exceeded",
                    "Запрос превысит доступный бюджет дня.",
                ),
                "Personal account is frozen by an unresolved billing discrepancy": (
                    "account_frozen",
                    "Личный счёт заблокирован до проверки расхождения учёта.",
                ),
                "Selected model or provider changed; reload configuration before retry": (
                    "configuration_changed",
                    "Конфигурация модели или провайдера изменилась. Обновите страницу.",
                ),
                "Active workspace member is required": (
                    "initiator_unavailable",
                    "Не подтверждены актуальные права инициатора запроса.",
                ),
            }
            code, hint = reasons.get(
                str(exc),
                (
                    "admission_denied",
                    "Учёт расходов не разрешил запуск. Проверьте личный счёт, дневной бюджет и политику модели.",
                ),
            )
            error = (code, "Запрос не отправлен: " + hint)
        elif isinstance(exc, httpx.TimeoutException):
            error = (
                "timeout",
                "Истёк таймаут 20 секунд. Доставка запроса может быть не подтверждена; не повторяйте автоматически.",
            )
        elif observation.get("http_status") and observation["http_status"] != 200:
            error = ("provider_http", "API вернул HTTP-ошибку. См. статус и JSON ответа; автоматического повтора нет.")
        elif stage == "accounting":
            error = (
                "accounting_failed",
                "Ответ API получен, но учёт расходов не подтверждён. Ответ сохранён ниже; не повторяйте автоматически.",
            )
        elif isinstance(exc, httpx.TransportError):
            error = (
                "transport_error",
                "Ошибка соединения с API. Доставка может быть не подтверждена; не повторяйте автоматически.",
            )
        elif stage == "response":
            error = (
                "invalid_response",
                "Ответ API получен, но JSON не удалось разобрать. См. тело ответа; не повторяйте автоматически.",
            )
        else:
            error = ("unconfirmed", "Ответ или учёт расходов не подтверждены. Не повторяйте вызов автоматически.")
        result = _real_result(observation, api_key=client.api_key, error=error)
        if observation.get("dispatch_state") == "not_dispatched":
            result["tokens_spent"] = 0
        hint = provider_limit_hint(result, model)
        if hint is not None:
            result["limit_hint"] = hint
        return result
    if not isinstance(response, dict):
        result = _real_result(
            observation,
            api_key=client.api_key,
            error=("invalid_response", "Ответ не подтверждён. Не повторяйте автоматически."),
        )
        hint = provider_limit_hint(result, model)
        if hint is not None:
            result["limit_hint"] = hint
        return result
    dispatch_id = response.pop("_personal_dispatch_id", None)
    journal_warning = False
    try:
        await journal_chat_attempt(client, purpose, response, dispatch_id=dispatch_id)
    except Exception:
        journal_warning = True
    result = _real_result(observation, response, api_key=client.api_key)
    result.update(request_prompt=question, max_tokens=max_tokens, is_real_response=True)
    if journal_warning:
        result["save_warning"] = "Ответ получен, но журнал не подтверждён. Не повторяйте вызов автоматически."
        result["accounting_status"] = "journal_unconfirmed"
    hint = provider_limit_hint(result, model)
    if hint is not None:
        result["limit_hint"] = hint
    return result


_PROVIDER_LIMIT_MESSAGES = {
    "rate_limit": "Провайдер сообщил об ограничении частоты запросов или временной квоте.",
    "quota": "Провайдер сообщил, что доступная квота исчерпана.",
    "budget": "Провайдер сообщил об ограничении бюджета или баланса.",
    "billing": "Провайдер сообщил о проблеме оплаты или баланса аккаунта.",
    "pricing_mismatch": "Провайдер сообщил стоимость вызова при нулевом тарифе модели. Проверьте тариф в настройках.",
}


def _finite_limit_number(value: Any):
    """Parse bounded JSON numeric evidence without accepting bools or infinities."""
    from decimal import Decimal, InvalidOperation

    if isinstance(value, bool) or type(value) not in (int, float, str):
        return None
    if isinstance(value, str) and len(value) > 64:
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not number.is_finite() or (number != 0 and abs(number.adjusted()) > 30):
        return None
    return number


def provider_limit_hint(result: dict[str, Any], model=None) -> dict[str, str] | None:
    """Map structured provider status/error/price evidence to fixed safe UI copy.

    Never return provider text, credentials, or arbitrary response fields. A
    provider-reported price is evidence for a warning only; it never edits the
    configured catalog tariff. Zero token rates are not interpreted as quota.
    """
    if not isinstance(result, dict) or result.get("tier") != "real":
        return None

    status = result.get("http_status")
    payload = result.get("response_json")
    code = None

    # Only inspect recognized limit containers, never model-generated choices,
    # arbitrary metadata or free-text response content.
    limit_containers = {
        "limits": "budget",
        "quota": "quota",
        "quotas": "quota",
        "budget": "budget",
        "budgets": "budget",
        "rate_limit": "rate_limit",
        "rate_limits": "rate_limit",
        "usage_limit": "budget",
        "usage_limits": "budget",
        "daily_limit": "budget",
        "billing": "billing",
        "credits": "quota",
    }
    exhausted_flags = {"exhausted", "exceeded", "limit_exceeded", "quota_exceeded", "rate_limited", "limit_reached"}
    remaining_fields = {
        "remaining",
        "remaining_quota",
        "quota_remaining",
        "remaining_budget",
        "budget_remaining",
        "credits_remaining",
    }
    if isinstance(payload, dict):
        for container_name, category in limit_containers.items():
            container = payload.get(container_name)
            if not isinstance(container, dict):
                continue
            nodes = [(container, category)]
            for key in ("daily", "monthly", "requests", "tokens", "current"):
                nested = container.get(key)
                if isinstance(nested, dict):
                    nested_category = "rate_limit" if key == "requests" else "quota" if key == "tokens" else category
                    nodes.append((nested, nested_category))
            for node, node_category in nodes:
                if any(node.get(flag) is True for flag in exhausted_flags):
                    code = node_category
                    break
                for field in remaining_fields:
                    amount = _finite_limit_number(node.get(field))
                    if amount is not None and amount <= 0:
                        code = node_category
                        break
                if code is None and "limit" in node and "used" in node:
                    limit = _finite_limit_number(node["limit"])
                    used = _finite_limit_number(node["used"])
                    if limit is not None and used is not None and limit > 0 and used >= limit:
                        code = node_category
                if code is not None:
                    break
            if code is not None:
                break

    fragments: list[str] = []
    if isinstance(payload, dict):
        provider_error = payload.get("error")
        if isinstance(provider_error, dict):
            for field in ("code", "type", "message", "status"):
                value = provider_error.get(field)
                if isinstance(value, (str, int)) and not isinstance(value, bool):
                    fragments.append(str(value).lower()[:180])
        elif isinstance(provider_error, str):
            fragments.append(provider_error.lower()[:180])
        for field in ("error_code", "error_type", "error_message"):
            value = payload.get(field)
            if isinstance(value, str):
                fragments.append(value.lower()[:180])
    error_code = result.get("error_code")
    if isinstance(error_code, str):
        fragments.append(error_code.lower()[:160])
    response_body = result.get("response_body")
    if isinstance(response_body, str):
        fragments.append(response_body.lower()[:2000])
    text = " ".join(fragments)

    # A structured exhaustion signal wins over generic HTTP 429/402. Otherwise
    # classify the provider's explicit error fields, then generic status.
    if code is None:
        if any(
            term in text
            for term in (
                "insufficient_quota",
                "quota_exceeded",
                "quota exceeded",
                "out of quota",
                "quota exhausted",
                "credits exhausted",
            )
        ):
            code = "quota"
        elif any(
            term in text
            for term in (
                "rate_limit",
                "rate limit",
                "too many requests",
                "requests per minute",
                "rpm_limit",
                "tpm_limit",
            )
        ):
            code = "rate_limit"
        elif any(
            term in text
            for term in (
                "budget_exceeded",
                "budget exceeded",
                "spending limit",
                "monthly limit",
                "daily limit",
                "usage limit",
            )
        ):
            code = "budget"
        elif any(
            term in text
            for term in ("billing", "payment required", "payment method", "insufficient balance", "no credits")
        ):
            code = "billing"

    if code is None and status == 429:
        code = "rate_limit"
    elif code is None and status == 402:
        code = "billing"

    if code is None and model is not None:
        provider_cost = result.get("provider_cost_usd")
        try:
            from decimal import Decimal, InvalidOperation

            reported = Decimal(str(provider_cost))
            configured = (
                Decimal(str(getattr(model, "input_cost_per_1k", 0))) == 0
                and Decimal(str(getattr(model, "output_cost_per_1k", 0))) == 0
            )
            if configured and reported.is_finite() and reported > 0:
                code = "pricing_mismatch"
        except (InvalidOperation, ValueError, TypeError):
            pass

    return {"code": code, "message": _PROVIDER_LIMIT_MESSAGES[code]} if code else None
