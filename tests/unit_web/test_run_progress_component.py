"""Shared progress distinguishes waiting, confirmed activity and missing evidence."""

from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape


@pytest.fixture
def progress():
    env = Environment(
        loader=FileSystemLoader(Path(__file__).resolve().parents[2] / "app/web/templates"),
        autoescape=select_autoescape(("html",)),
    )
    return env.get_template("web/_run_progress.html").module.progress_bar


def test_waiting_has_animation_without_invented_percentage(progress):
    html = str(progress("Ожидает запуска", state="pending"))
    assert "Ожидает запуска" in html
    assert "animate-pulse" in html
    assert "animate-spin" not in html
    assert "aria-valuenow" not in html
    assert "motion-reduce:animate-none" in html


def test_unconfirmed_activity_is_not_animated_as_running(progress):
    html = str(progress("Статус не подтверждён", state="unconfirmed", percent=25, processed=1, total=4))
    assert "animate-spin" not in html
    assert "animate-pulse" not in html
    assert 'aria-valuenow="25"' in html
    assert "Обработано 1 из 4 записей" in html


@pytest.mark.parametrize("value", [None, True, -1, 101, "50"])
def test_unknown_or_invalid_percentage_never_looks_complete(progress, value):
    html = str(progress("Обработка", percent=value))
    assert "aria-valuenow" not in html
    assert "width:" not in html


def test_confirmed_zero_and_content_are_preserved_safely(progress):
    html = str(progress('<img src=x onerror="bad">', state="running", percent=0, processed=0, total=8))
    assert 'aria-valuenow="0"' in html
    assert "Обработано 0 из 8 записей" in html
    assert "animate-spin" in html
    assert "<img" not in html
