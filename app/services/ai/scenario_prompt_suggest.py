"""
Turning a plain description into a scenario's `base_prompt`.

The web wizard (step 2) and the chat tool `scenario_suggest_prompt` both need
"the owner writes what they want, the model writes the instruction", and both
must fall back to a manual field when no model answers. One implementation, so
the two surfaces cannot drift in wording or in failure behaviour.

Never raises: a broken provider or an unconfigured fleet is `None`, and both
callers treat that as "ask the person to write it themselves" rather than as an
error the owner cannot act on.
"""

from __future__ import annotations

# The instruction the model answers. It names the variables the registry
# actually resolves (see `prompt_variables.AVAILABLE_VARIABLES`), so a generated
# prompt validates clean instead of filling the warning slot on every save.
SYSTEM_INSTRUCTION = (
    "Ты — помощник по созданию промптов для ИИ-аналитика соцсетей. "
    "По описанию задачи напиши ОДИН промпт для модели, которая анализирует "
    "собранный контент и возвращает JSON. Промпт — на русском, конкретный, "
    "2-5 предложений, без markdown-обёртки и кавычек. Можно использовать "
    "переменные {platform}, {text}, {date_range}, {source_name}, {scenario_name}, "
    "{max_keywords}, {max_topics}, {sentiment_categories}."
)


async def suggest_base_prompt(description: str) -> str | None:
    """A `base_prompt` for `description`, or None when no model answers.

    Uses `chat_with_fallback`, the same provider chain the agent runs on, so a
    broken default provider is not a reason to refuse — the next one is tried.
    """
    from app.services.ai.llm_client import chat_with_fallback

    description = (description or "").strip()
    if not description:
        return None

    messages = [
        {"role": "system", "content": SYSTEM_INSTRUCTION},
        {"role": "user", "content": f"Задача: {description}"},
    ]
    try:
        response = await chat_with_fallback(messages, max_tokens=400)
    except Exception:  # noqa: BLE001 — the caller falls back, it does not fail
        return None
    return ((response or {}).get("content") or "").strip() or None
