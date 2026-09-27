"""Tests for the manual collection CLI (cli/commands/collect.py)."""

import pytest

from cli.commands.collect import _parse_date


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("01-05-2025", "2025-05-01"),
        ("01.05.2025", "2025-05-01"),
        ("2025-05-01", "2025-05-01"),
    ],
)
def test_parse_date_valid(value, expected):
    assert str(_parse_date(value)) == expected


def test_parse_date_none():
    assert _parse_date(None) is None


def test_parse_date_invalid():
    with pytest.raises(Exception):
        _parse_date("not-a-date")
