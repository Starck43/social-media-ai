"""Calendar-month retention cutoffs shared by the prune task and its tests."""

from __future__ import annotations

import calendar
from datetime import datetime


def months_before(moment: datetime, months: int) -> datetime:
    """Return `moment` shifted back by whole calendar months.

    The day is clamped to the target month's length (31 March minus one month
    is 28/29 February), so the window is never shorter than the calendar months
    requested. `moment` must be timezone-aware; `months` must be at least 1.
    """
    if moment.tzinfo is None:
        raise ValueError("Retention reference time must be timezone-aware")
    if type(months) is not int or months < 1:
        raise ValueError("Retention months must be an integer of at least 1")
    total = moment.year * 12 + (moment.month - 1) - months
    year, month = divmod(total, 12)
    month += 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)
