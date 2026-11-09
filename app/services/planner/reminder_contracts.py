"""Typed inputs and outputs for personal planner reminders."""

from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, Field, StrictInt, model_validator

from app.schemas.business import BusinessSchema

ReminderChannel = Literal["inbox", "telegram", "vk", "email", "max"]
ReminderPreset = Literal["day", "week", "month", "custom"]
SnoozePreset = Literal["hour", "day", "week"]


class ReminderCreate(BusinessSchema):
    preset: ReminderPreset
    remind_at: AwareDatetime | None = None
    channel: ReminderChannel = "inbox"

    @model_validator(mode="after")
    def custom_time(self):
        if (self.preset == "custom") != (self.remind_at is not None):
            raise ValueError("custom_reminder_time_required")
        return self


class ReminderSnooze(BusinessSchema):
    expected_version: StrictInt = Field(gt=0)
    preset: SnoozePreset


class ReminderReschedule(BusinessSchema):
    expected_version: StrictInt = Field(gt=0)
    remind_at: AwareDatetime


class ReminderVersion(BusinessSchema):
    expected_version: StrictInt = Field(gt=0)


class ReminderOut(BusinessSchema):
    id: int
    tenant_id: int
    task_id: int
    recipient_user_id: int
    channel: ReminderChannel
    preset: ReminderPreset
    remind_at: datetime
    snoozed_until: datetime | None
    next_attempt_at: datetime
    state: Literal["pending", "deferred", "delivered", "cancelled"]
    sent_at: datetime | None
    version: int
    generation: int
    notification_id: int | None
    last_error: str | None
    delivery_state: Literal["pending", "sending", "sent", "blocked", "unknown"] | None = None
    delivery_error_code: str | None = None
    late: bool
