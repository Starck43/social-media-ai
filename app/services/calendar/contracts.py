"""Provider-neutral calendar data. Provider payloads remain source-owned."""

from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, Field, StrictInt, field_validator, model_validator

from app.schemas.business import BusinessSchema


class EventInput(BusinessSchema):
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(default="", max_length=20000)
    location: str = Field(default="", max_length=1000)
    start: AwareDatetime | date
    end: AwareDatetime | date
    timezone: str = "UTC"
    recurrence: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("recurrence")
    @classmethod
    def recurrence_lines(cls, value):
        if any(
            len(line) > 4096
            or "\x00" in line
            or "\r" in line
            or "\n" in line
            or not line.startswith(("RRULE:", "RDATE:", "EXDATE:"))
            for line in value
        ):
            raise ValueError("invalid_calendar_recurrence")
        return value

    @field_validator("timezone")
    @classmethod
    def zone(cls, value):
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError):
            raise ValueError("invalid_timezone") from None
        return value

    @model_validator(mode="after")
    def bounds(self):
        if type(self.start) is not type(self.end) or self.end <= self.start:
            raise ValueError("invalid_event_bounds")
        if not self.title.strip() or any("\x00" in value for value in (self.title, self.description, self.location)):
            raise ValueError("invalid_event_text")
        return self


class GoogleConnect(BusinessSchema):
    connection_id: StrictInt | None = Field(default=None, gt=0)
    calendar_id: str = Field(min_length=1, max_length=500)
    read_only: bool = True
    write_consent: bool = False

    @model_validator(mode="after")
    def consent(self):
        if not self.read_only and not self.write_consent:
            raise ValueError("calendar_consent_required")
        return self


class YandexDiscovery(BusinessSchema):
    login: str = Field(min_length=1, max_length=320)
    app_password: str = Field(min_length=1, max_length=2048, repr=False)


class YandexConnect(GoogleConnect, YandexDiscovery):
    pass


class CallbackInput(BusinessSchema):
    connection_id: StrictInt | None = Field(default=None, gt=0)
    state: str = Field(min_length=1, max_length=500)
    code: str = Field(min_length=1, max_length=4000, repr=False)


class ConnectionID(BusinessSchema):
    connection_id: StrictInt = Field(gt=0)


class WritePreview(ConnectionID):
    task_id: StrictInt | None = Field(default=None, gt=0)
    event_id: StrictInt | None = Field(default=None, gt=0)
    operation: Literal["upsert", "delete", "resolve_local", "resolve_remote"] = "upsert"
    event: EventInput | None = None

    @model_validator(mode="after")
    def target(self):
        if self.event_id is None and (self.task_id is None or self.operation != "upsert"):
            raise ValueError("calendar_target_required")
        if self.operation in {"upsert", "resolve_local"} and self.event is None:
            raise ValueError("calendar_event_required")
        return self


class CalendarError(RuntimeError):
    def __init__(self, code="source_unavailable"):
        self.code = code
        super().__init__(code)


class PreconditionFailed(CalendarError):
    def __init__(self):
        super().__init__("calendar_conflict")


class ReauthRequired(CalendarError):
    def __init__(self):
        super().__init__("reauth")
