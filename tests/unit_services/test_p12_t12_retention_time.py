"""Pure synthetic clock/deadline tests; no DB, fixtures, sleep or provider."""

import ast
import inspect
import traceback
from dataclasses import FrozenInstanceError, fields, replace
from datetime import datetime, timedelta, timezone, tzinfo

import pytest

from app.integrations.file_workspace import retention_time
from app.integrations.file_workspace.retention_time import (
    MAX_CAPTURE_AGE,
    RETENTION_TTL,
    RetentionDeadline,
    RetentionTimeUnavailable,
    make_retention_deadline,
    retention_time_eligible,
)

__source_only__ = True
UTC = timezone.utc
CAPTURE = datetime(2026, 11, 3, 12, 0, 0, 123456, tzinfo=UTC)
TICK = timedelta(microseconds=1)


def make(*, captured_at=CAPTURE, now=CAPTURE, last_observed_at=CAPTURE):
    return make_retention_deadline(captured_at=captured_at, now=now, last_observed_at=last_observed_at)


def eligible(deadline=None, *, now=CAPTURE, last_observed_at=CAPTURE):
    return retention_time_eligible(make() if deadline is None else deadline, now=now, last_observed_at=last_observed_at)


def reject(**kwargs):
    with pytest.raises(RetentionTimeUnavailable) as caught:
        make(**kwargs)
    error = caught.value
    assert str(error) == "retention_time_unavailable"
    assert error.args == ("retention_time_unavailable",)
    assert error.__cause__ is None and error.__context__ is None
    rendered = "".join(traceback.format_exception(error))
    assert "2026-11-03" not in rendered
    assert "ValueError" not in rendered and "OverflowError" not in rendered


def test_proposal_constants_and_minimal_immutable_deadline():
    assert MAX_CAPTURE_AGE == timedelta(minutes=5)
    assert RETENTION_TTL == timedelta(hours=24)
    deadline = make()
    assert [field.name for field in fields(deadline)] == ["captured_at", "expires_at"]
    assert deadline.captured_at == CAPTURE
    assert deadline.expires_at == CAPTURE + timedelta(hours=24)
    assert deadline.captured_at.tzinfo is UTC and deadline.expires_at.tzinfo is UTC
    assert not hasattr(deadline, "__dict__")
    assert "2026" not in repr(deadline) and "captured_at" not in repr(deadline)
    with pytest.raises(FrozenInstanceError):
        deadline.expires_at = CAPTURE


@pytest.mark.parametrize("age", [timedelta(0), TICK, timedelta(minutes=5) - TICK, timedelta(minutes=5)])
def test_capture_age_is_inclusive_and_deadline_uses_capture_not_save(age):
    deadline = make(now=CAPTURE + age)
    assert deadline == make()
    assert deadline.expires_at != CAPTURE + age + RETENTION_TTL or age == timedelta(0)


@pytest.mark.parametrize("age", [-TICK, -timedelta(days=1), timedelta(minutes=5) + TICK, timedelta(days=1)])
def test_future_or_stale_capture_is_rejected(age):
    reject(now=CAPTURE + age, last_observed_at=min(CAPTURE, CAPTURE + age))


def test_capture_can_follow_earlier_trusted_clock_mark():
    assert make(last_observed_at=CAPTURE - timedelta(days=1)) == make()
    assert eligible(last_observed_at=CAPTURE - timedelta(days=1)) is True


@pytest.mark.parametrize("age", [timedelta(0), TICK, timedelta(hours=1), timedelta(hours=24) - TICK])
def test_existing_record_remains_time_eligible_after_capture_admission_window(age):
    assert eligible(now=CAPTURE + age) is True


@pytest.mark.parametrize("age", [-TICK, timedelta(hours=24), timedelta(hours=24) + TICK, timedelta(days=7)])
def test_future_capture_or_exact_expiry_and_later_are_not_eligible(age):
    assert eligible(now=CAPTURE + age, last_observed_at=min(CAPTURE, CAPTURE + age)) is False


@pytest.mark.parametrize("delta", [TICK, timedelta(seconds=1), timedelta(days=1)])
def test_clock_rollback_denies_creation_and_use(delta):
    reject(last_observed_at=CAPTURE + delta)
    assert eligible(last_observed_at=CAPTURE + delta) is False


def test_equal_clock_mark_is_allowed_and_checks_never_mutate_deadline():
    deadline = make()
    for age in [timedelta(0), timedelta(hours=1), timedelta(hours=12), RETENTION_TTL - TICK]:
        now = CAPTURE + age
        assert eligible(deadline, now=now, last_observed_at=now) is True
        assert deadline == make()


def test_synthetic_caller_keeps_high_water_even_on_expiry_denial():
    deadline = make()
    mark = CAPTURE
    for now, expected in [
        (CAPTURE + timedelta(hours=1), True),
        (deadline.expires_at, False),
        (deadline.expires_at - TICK, False),
        (CAPTURE + timedelta(hours=2), False),
        (deadline.expires_at + TICK, False),
    ]:
        assert eligible(deadline, now=now, last_observed_at=mark) is expected
        mark = max(mark, now)  # Synthetic stand-in, NOT durable/atomic DB evidence.
    assert mark == deadline.expires_at + TICK


def test_stale_external_mark_cannot_be_detected_by_a_pure_function():
    # Explicit trust boundary: these inputs omit a prior expiry observation.
    # The helper cannot certify persistence, current authority or clock history.
    assert eligible(now=CAPTURE + timedelta(hours=1), last_observed_at=CAPTURE) is True


class DateTimeSubclass(datetime):
    pass


class HostileTimezone(tzinfo):
    def utcoffset(self, value):
        raise AssertionError("timezone callback must not run")

    def dst(self, value):
        raise AssertionError("timezone callback must not run")


BAD_TIMES = [
    None,
    True,
    0,
    1.0,
    "2026-11-03T12:00:00Z",
    CAPTURE.date(),
    CAPTURE.replace(tzinfo=None),
    CAPTURE.astimezone(timezone(timedelta(hours=3))),
    CAPTURE.replace(tzinfo=HostileTimezone()),
    DateTimeSubclass(2026, 11, 3, 12, tzinfo=UTC),
]


@pytest.mark.parametrize("field", ["captured_at", "now", "last_observed_at"])
@pytest.mark.parametrize("value", BAD_TIMES, ids=[f"invalid-{i}" for i in range(len(BAD_TIMES))])
def test_creation_refuses_nonexact_or_non_utc_time_without_conversion(field, value):
    reject(**{field: value})


@pytest.mark.parametrize("field", ["now", "last_observed_at"])
@pytest.mark.parametrize("value", BAD_TIMES, ids=[f"invalid-{i}" for i in range(len(BAD_TIMES))])
def test_eligibility_refuses_nonexact_or_non_utc_clock(field, value):
    assert eligible(**{field: value}) is False


@pytest.mark.parametrize("field", ["captured_at", "expires_at"])
@pytest.mark.parametrize("value", BAD_TIMES, ids=[f"invalid-{i}" for i in range(len(BAD_TIMES))])
def test_eligibility_refuses_malformed_window_fields(field, value):
    assert eligible(replace(make(), **{field: value})) is False


class DeadlineSubclass(RetentionDeadline):
    pass


@pytest.mark.parametrize(
    "value", [None, False, {}, (CAPTURE, CAPTURE + RETENTION_TTL), DeadlineSubclass(CAPTURE, CAPTURE + RETENTION_TTL)]
)
def test_only_exact_deadline_type_is_accepted(value):
    assert retention_time_eligible(value, now=CAPTURE, last_observed_at=CAPTURE) is False


@pytest.mark.parametrize("delta", [-RETENTION_TTL - TICK, -RETENTION_TTL, -TICK, TICK, timedelta(days=365)])
def test_changed_or_extended_expiry_is_not_a_policy_change_mechanism(delta):
    assert eligible(replace(make(), expires_at=CAPTURE + RETENTION_TTL + delta)) is False


def test_dataclass_construction_is_not_a_provenance_signature():
    # Trusted application data only: recomputing BOTH fields produces a valid
    # time window. This says nothing about a real file, owner, consent or version.
    later = CAPTURE + timedelta(days=1)
    assert eligible(RetentionDeadline(later, later + RETENTION_TTL), now=later, last_observed_at=later) is True


@pytest.mark.parametrize(
    "capture", [datetime.min.replace(tzinfo=UTC), datetime(2028, 2, 29, 23, 59, 59, 999999, tzinfo=UTC)]
)
def test_representable_minimum_and_leap_day_preserve_absolute_microsecond_timing(capture):
    deadline = make(captured_at=capture, now=capture, last_observed_at=capture)
    assert deadline.expires_at - deadline.captured_at == RETENTION_TTL
    assert eligible(deadline, now=deadline.expires_at - TICK, last_observed_at=capture) is True
    assert eligible(deadline, now=deadline.expires_at, last_observed_at=capture) is False


def test_datetime_overflow_is_sanitized_or_ineligible():
    maximum = datetime.max.replace(tzinfo=UTC)
    reject(captured_at=maximum, now=maximum, last_observed_at=maximum)
    assert eligible(RetentionDeadline(maximum, maximum), now=maximum, last_observed_at=maximum) is False


def test_no_ambient_clock_io_or_application_dependencies():
    tree = ast.parse(inspect.getsource(retention_time))
    modules = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert modules <= {"__future__", "dataclasses", "datetime"}
    assert not any(isinstance(node, ast.Import) for node in ast.walk(tree))
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"now", "utcnow", "today", "timestamp", "fromtimestamp"}
        for node in ast.walk(tree)
    )
