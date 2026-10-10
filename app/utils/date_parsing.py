"""Compatible date parsing without application or database dependencies."""

import logging
from datetime import date, datetime, time, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

_DAY_FIRST_FORMATS = ("%d-%m-%Y", "%d.%m.%Y")


def _with_utc_if_naive(value: datetime) -> datetime:
    """Attach UTC to naive values; preserve aware values and their offsets."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def parse_datetime(
    date_input: Any, target_timezone: str = "UTC+3"
) -> Optional[datetime]:
    """Parse a supported input, returning an aware datetime or None.

    Accepts datetime/date objects, Unix timestamps, DD-MM-YYYY, DD.MM.YYYY
    and ISO strings. Naive datetimes and dates use UTC; aware inputs retain
    their timezone. Invalid or unsupported inputs return None with a warning.

    ``target_timezone`` is retained for call compatibility, not conversion:
    historically this parameter did not change the resulting timezone. Date
    strings therefore remain midnight UTC even when its value is ``UTC+3``.
    The legacy falsey-input guard is also preserved: numeric zero returns None.
    Strings are not stripped or otherwise normalized beyond ISO Z handling.
    """
    if not date_input:
        return None

    try:
        if isinstance(date_input, datetime):
            return _with_utc_if_naive(date_input)
        if isinstance(date_input, date):
            return _with_utc_if_naive(datetime.combine(date_input, time(0, 0, 0)))
        if isinstance(date_input, (int, float)):
            return datetime.fromtimestamp(date_input, tz=timezone.utc)
        if isinstance(date_input, str):
            for day_first_format in _DAY_FIRST_FORMATS:
                try:
                    parsed = datetime.strptime(date_input, day_first_format)
                    return _with_utc_if_naive(parsed)
                except ValueError:
                    pass
            try:
                return _with_utc_if_naive(
                    datetime.fromisoformat(date_input.replace("Z", "+00:00"))
                )
            except ValueError:
                pass
            logger.warning(f"Unsupported date string format: {date_input}")
            return None

        logger.warning(f"Unsupported date input type: {type(date_input)}")
        return None
    except Exception as exc:
        logger.warning(f"Date parsing failed for {date_input}: {exc}")
        return None


# Keep the established import path and signature without a second implementation.
universal_date_parser = parse_datetime


def to_unix_timestamp(date_input: Any, target_timezone: str = "UTC+3") -> Optional[int]:
    """Parse an input into integer Unix seconds, preserving legacy truncation."""
    # Resolve the legacy name at call time to preserve existing monkeypatch hooks.
    parsed = universal_date_parser(date_input, target_timezone)
    if not parsed:
        return None
    try:
        return int(parsed.timestamp())
    except Exception as exc:
        logger.warning(f"Timestamp conversion failed: {exc}")
        return None
