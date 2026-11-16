"""DB-exempt template expansion tests; no application or provider I/O."""

from datetime import date, datetime

import pytest

from app.services.planner.events import DEFAULT_TEMPLATES, expand_template

__source_only__ = True


def test_birthday_steps_and_explicit_draft_action():
    gift, greeting = expand_template("birthday", date(2027, 1, 3))
    assert (gift.key, gift.due_date, gift.action) == ("gift", date(2026, 12, 27), None)
    assert (greeting.key, greeting.due_date, greeting.action) == ("greeting", date(2027, 1, 2), "greeting_draft")


@pytest.mark.parametrize("kind", ["meeting", "deadline"])
def test_other_templates_have_date_only_preparation(kind):
    (step,) = expand_template(kind, date(2028, 3, 1))
    assert step.key == "prepare"
    assert step.due_date == date(2028, 2, 29)
    assert step.action is None


@pytest.mark.parametrize("value", [datetime(2028, 1, 1), "2028-01-01", None])
def test_reject_non_date_values(value):
    with pytest.raises(ValueError, match="event_date_must_be_date"):
        expand_template("birthday", value)


def test_unknown_and_out_of_range_rejected():
    with pytest.raises(ValueError, match="unknown_event_template"):
        expand_template("unknown", date(2028, 1, 1))
    with pytest.raises(ValueError, match="event_date_out_of_range"):
        expand_template("birthday", date.min)


def test_templates_are_immutable():
    with pytest.raises(TypeError):
        DEFAULT_TEMPLATES["birthday"] = ()
    assert expand_template("birthday", date(2028, 1, 1)) == expand_template("birthday", date(2028, 1, 1))
