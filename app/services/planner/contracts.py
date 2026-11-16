"""Strict shared API/tool/service contracts; no database or application settings."""

from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, Field, StrictInt, field_validator, model_validator

from app.schemas.business import BusinessSchema
from app.services.planner.recurrence import parse_rule

Priority = Literal["low", "medium", "high", "critical"]
Status = Literal["todo", "in_progress", "done", "cancelled"]
EditScope = Literal["occurrence", "series"]
PositiveID = StrictInt


class TaskCreate(BusinessSchema):
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(default="", max_length=20000)
    assignee_id: PositiveID | None = Field(default=None, gt=0)
    due_at: AwareDatetime | None = None
    due_date: date | None = None
    timezone: str = "UTC"
    priority: Priority = "medium"
    category: str = Field(default="", max_length=100)
    tags: list[str] = Field(default_factory=list, max_length=20)
    contact_id: PositiveID | None = Field(default=None, gt=0)
    parent_id: PositiveID | None = Field(default=None, gt=0)
    recurrence: str | None = None

    @field_validator("title", "description", "category")
    @classmethod
    def text_safe(cls, value):
        if "\x00" in value:
            raise ValueError("invalid_text")
        return value

    @field_validator("tags")
    @classmethod
    def tags_safe(cls, value):
        if any(not tag.strip() or len(tag) > 60 or "\x00" in tag for tag in value) or len(set(value)) != len(value):
            raise ValueError("invalid_tags")
        return value

    @field_validator("timezone")
    @classmethod
    def valid_zone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("invalid_timezone") from None
        return value

    @model_validator(mode="after")
    def schedule(self):
        if not self.title.strip() or (self.due_at is not None and self.due_date is not None):
            raise ValueError("invalid_task")
        if self.recurrence is not None:
            anchor = self.due_at or self.due_date
            if anchor is None:
                raise ValueError("recurrence_requires_deadline")
            rule = parse_rule(self.recurrence, timed=self.due_at is not None)
            if rule.until is not None and rule.until < anchor:
                raise ValueError("recurrence_ends_before_start")
            self.recurrence = rule.text
        return self


class TaskUpdate(BusinessSchema):
    expected_version: StrictInt = Field(gt=0)
    scope: EditScope
    title: str | None = None
    description: str | None = None
    assignee_id: PositiveID | None = Field(default=None, gt=0)
    priority: Priority | None = None
    category: str | None = None
    tags: list[str] | None = None
    contact_id: PositiveID | None = Field(default=None, gt=0)
    parent_id: PositiveID | None = Field(default=None, gt=0)
    status: Literal["todo", "in_progress", "cancelled"] | None = None


class TaskReschedule(BusinessSchema):
    expected_version: StrictInt = Field(gt=0)
    scope: EditScope
    due_at: AwareDatetime | None = None
    due_date: date | None = None
    timezone: str
    recurrence: str | None = None


class TaskVersion(BusinessSchema):
    expected_version: StrictInt = Field(gt=0)


class TaskOut(TaskCreate):
    id: int
    owner_user_id: int
    status: Status
    version: int
    series_id: int
    occurrence_key: str
    recurrence_source: str
    completed_at: datetime | None = None


class CompleteOut(BusinessSchema):
    task: TaskOut
    next_task: TaskOut | None = None


class DeletedOut(BusinessSchema):
    deleted: bool
