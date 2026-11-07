"""Pure event-template expansion; this module does not schedule or write tasks.

Generation, permissions, persistence and idempotency are provided separately.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class TemplateStep:
    key: str
    title: str
    lead_days: int
    action: str | None = None


@dataclass(frozen=True, slots=True)
class PlannedStep:
    key: str
    title: str
    due_date: date
    action: str | None


# These are local defaults, not a persisted or user-configurable template store.
DEFAULT_TEMPLATES = MappingProxyType(
    {
        "birthday": (
            TemplateStep("gift", "Купить подарок", 7),
            TemplateStep("greeting", "Написать поздравление", 1, "greeting_draft"),
        ),
        "meeting": (TemplateStep("prepare", "Подготовиться к встрече", 1),),
        "deadline": (TemplateStep("prepare", "Подготовиться к сроку", 1),),
    }
)


def expand_template(event_type: str, event_date: date) -> tuple[PlannedStep, ...]:
    """Return date-only steps for a local event date without writing or sending."""
    if not isinstance(event_date, date) or isinstance(event_date, datetime):
        raise ValueError("event_date_must_be_date")
    try:
        steps = DEFAULT_TEMPLATES[event_type]
    except (KeyError, TypeError):
        raise ValueError("unknown_event_template") from None
    try:
        return tuple(
            PlannedStep(step.key, step.title, event_date - timedelta(days=step.lead_days), step.action)
            for step in steps
        )
    except OverflowError:
        raise ValueError("event_date_out_of_range") from None
