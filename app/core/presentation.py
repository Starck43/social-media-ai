"""Stable message IDs and the current Russian presentation catalog.

Russian is the only selected product locale today. Messages cross presenter and
error boundaries as IDs plus parameters; introducing another catalog remains a
product decision. Returned strings are plain text and must stay subject to the
destination's normal escaping rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Mapping


class MessageId(str, Enum):
    """Stable identifiers for user-facing messages at selected boundaries."""

    TASK_SOURCE_ERROR = "task.source.error"
    TASK_SOURCE_NEW_ITEMS = "task.source.new_items"
    TASK_SOURCE_ITEMS = "task.source.items"
    TASK_SOURCE_ANALYSIS_RESULTS = "task.source.analysis_results"
    LLM_REQUEST_FAILED = "llm.request_failed"
    LLM_AUTH_REJECTED = "llm.auth_rejected"
    LLM_RATE_LIMITED = "llm.rate_limited"
    LLM_REQUEST_REJECTED = "llm.request_rejected"
    LLM_UNAVAILABLE = "llm.unavailable"


@dataclass(frozen=True)
class _PluralForms:
    one: str
    few: str
    many: str


@dataclass(frozen=True)
class PresentationMessage:
    """A safe message key and values, never a caller-supplied format string."""

    message_id: MessageId
    parameters: Mapping[str, object] = field(default_factory=dict)
    plural_count: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "parameters", MappingProxyType(dict(self.parameters)))


DEFAULT_LOCALE = "ru"

_RU_CATALOG: Mapping[MessageId, str | _PluralForms] = MappingProxyType(
    {
        MessageId.TASK_SOURCE_ERROR: "ошибка{details}",
        MessageId.TASK_SOURCE_NEW_ITEMS: _PluralForms(
            one="{count} новая",
            few="{count} новых",
            many="{count} новых",
        ),
        MessageId.TASK_SOURCE_ITEMS: _PluralForms(
            one="{count} запись",
            few="{count} записи",
            many="{count} записей",
        ),
        MessageId.TASK_SOURCE_ANALYSIS_RESULTS: _PluralForms(
            one="{count} результат анализа",
            few="{count} результата анализа",
            many="{count} результатов анализа",
        ),
        MessageId.LLM_REQUEST_FAILED: "Не удалось обратиться к языковой модели. Подробности в логах.",
        MessageId.LLM_AUTH_REJECTED: (
            "Языковая модель отклонила запрос: неверный или истёкший API-ключ. Подробности в логах."
        ),
        MessageId.LLM_RATE_LIMITED: ("Языковая модель временно недоступна из-за лимита запросов. Подробности в логах."),
        MessageId.LLM_REQUEST_REJECTED: ("Языковая модель отклонила мой запрос ({status}). Подробности в логах."),
        MessageId.LLM_UNAVAILABLE: ("Языковая модель сейчас недоступна ({status}). Подробности в логах."),
    }
)

_CATALOGS: Mapping[str, Mapping[MessageId, str | _PluralForms]] = MappingProxyType({DEFAULT_LOCALE: _RU_CATALOG})


def plural_category(count: int) -> str:
    """Return the Russian plural category used by the selected catalog."""
    value = abs(int(count))
    if value % 10 == 1 and value % 100 != 11:
        return "one"
    if 2 <= value % 10 <= 4 and not 12 <= value % 100 <= 14:
        return "few"
    return "many"


def _resolve_locale(locale: str | None) -> str:
    if isinstance(locale, str):
        language = locale.strip().replace("_", "-").split("-", maxsplit=1)[0].lower()
        if language in _CATALOGS:
            return language
    return DEFAULT_LOCALE


def render_message(message: PresentationMessage, *, locale: str | None = None) -> str:
    """Render a known message; unsupported locales predictably fall back to RU."""
    catalog = _CATALOGS[_resolve_locale(locale)]
    template = catalog.get(message.message_id)
    if template is None:
        raise ValueError("Unknown presentation message ID")
    if isinstance(template, _PluralForms):
        if message.plural_count is None:
            raise ValueError("Plural presentation message requires a count")
        template = getattr(template, plural_category(message.plural_count))
    elif message.plural_count is not None:
        raise ValueError("Non-plural presentation message cannot have a count")
    try:
        values = {name: str(value) for name, value in message.parameters.items()}
        return template.format_map(values)
    except (KeyError, ValueError) as error:
        raise ValueError("Invalid presentation message parameters") from error
