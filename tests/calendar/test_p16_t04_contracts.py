"""Provider-neutral calendar input contracts used by the CalDAV connection flow."""

from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from app.services.calendar.contracts import EventInput, YandexConnect


def test_yandex_write_connection_requires_explicit_consent():
    with pytest.raises(ValidationError, match="calendar_consent_required"):
        YandexConnect(calendar_id="cal-1", login="user@yandex.ru", app_password="private", read_only=False)

    connection = YandexConnect(
        calendar_id="cal-1",
        login="user@yandex.ru",
        app_password="private",
        read_only=False,
        write_consent=True,
    )
    assert connection.write_consent is True
    assert "private" not in repr(connection)


def test_event_input_rejects_mixed_bounds_and_nonpositive_duration():
    with pytest.raises(ValidationError, match="invalid_event_bounds"):
        EventInput(title="event", start=date(2026, 1, 1), end=datetime(2026, 1, 2, tzinfo=timezone.utc))
    with pytest.raises(ValidationError, match="invalid_event_bounds"):
        EventInput(title="event", start=date(2026, 1, 2), end=date(2026, 1, 2))


def test_event_input_requires_known_timezone_and_safe_text():
    with pytest.raises(ValidationError):
        EventInput(title="event", start=date(2026, 1, 1), end=date(2026, 1, 2), timezone="Mars/Olympus")
    with pytest.raises(ValidationError, match="invalid_event_text"):
        EventInput(title="event\x00hidden", start=date(2026, 1, 1), end=date(2026, 1, 2))
