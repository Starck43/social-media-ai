"""P20-T13: explicit label associations in the task add modal and its schedule controls.

Source-only, no application import: the page body of
``app/web/templates/web/tasks.html`` is rendered by a bare Jinja environment
 whose loader reads the real shared macros, so the contract is observed on
real rendered HTML instead of text matching the template source. Only
repo-existing libraries are used (pytest + Jinja from ``requirements.txt``): no
DB, no engine, no network, no ``app`` package.

Contract, on the rendered add-task form: every ``<label for=...>`` names a
control that exists exactly once; the fields this task touched carry an
association (their own explicit ``for``/``id`` pair, or a wrapping label as
elsewhere on the page) and keep their names, Alpine bindings and submit contract.

Run via the documented source-only route in the task card (no root conftest).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader

__source_only__ = True

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "app" / "web" / "templates" / "web" / "tasks.html"

# The page body between its own ``{% block content %}`` markers; the head and
# script blocks are outside this contract.
PAGE_BODY = re.compile(r"{% block content %}\n(.*?)\n{% endblock %}", re.S)

# The add-task modal is the form that posts to the task collection endpoint.
ADD_FORM = re.compile(r'<form method="post" action="/app/tasks".*?</form>', re.S)


@pytest.fixture(autouse=True)
def no_application_import():
    """This template-only suite must not bootstrap application/DB fixtures."""
    assert not any(name == "app" or name.startswith("app.") for name in sys.modules)
    yield
    assert not any(name == "app" or name.startswith("app.") for name in sys.modules)


def _render(page_body: str) -> str:
    # Only the content block is rendered: the page script (Alpine state, modal
    # toggles) is asserted separately from the parsed source.
    env = Environment(
        loader=ChoiceLoader([DictLoader({"web/_task_tabs.html": ""}), FileSystemLoader(ROOT / "app/web/templates")]),
        autoescape=True,
    )
    env.filters["ui_label"] = str
    imports = TEMPLATE.read_text(encoding="utf-8").splitlines()[0]
    return env.from_string(imports + "\n" + page_body).render(**PAGE_VALUES)


PAGE_VALUES = dict(
    csrf="synthetic-csrf",
    tenants=[],
    is_superuser=False,
    filter_tenant_id=None,
    tasks=[],
    sources=[],
    source_groups=[],
    scenarios=[],
    target_fields=[],
    trigger_types=[],
    action_types=[],
    job_types=[],
    collection_defaults={"start_date": "", "end_date": ""},
    planner_reference=None,
    prefill_retention=False,
    prefill_scenario_id=None,
    prefill_source_ids=[],
    prefill_job_type="analyze",
    open_add_modal=False,
    job_id=None,
    running_jobs={},
    scenario_analysis={},
    cron_to_human=lambda value: value,
    human_dt=lambda value, **kwargs: value,
    static_v=lambda path: path,
    _tz="Europe/Moscow",
)


@pytest.fixture(scope="module")
def page() -> str:
    text = TEMPLATE.read_text(encoding="utf-8")
    body = PAGE_BODY.search(text)
    assert body, "tasks.html no longer exposes a page body between the content block markers"
    return _render(body.group(1))


@pytest.fixture(scope="module")
def form(page: str) -> str:
    found = ADD_FORM.search(page)
    assert found, "the add-task modal no longer posts to /app/tasks"
    return found.group(0)


def _ids(html: str) -> dict[str, int]:
    seen: dict[str, int] = {}
    for value in re.findall(r'\sid="([^"]*)"', html):
        seen[value] = seen.get(value, 0) + 1
    return seen


def _label_targets(html: str) -> list[str]:
    return re.findall(r'<label\b[^>]*\sfor="([^"]*)"', html)


def _control_with_name(html: str, name: str) -> str:
    """The control element opening tag for a form field name."""
    found = re.search(rf'<(?:input|select|textarea)\b[^>]*\sname="{name}"', html)
    assert found, f"field {name} is not rendered"
    return found.group(0)


def _control_with_id(html: str, control_id: str) -> str:
    # Attributes may sit on their own source lines: match to the closing bracket.
    found = re.search(rf'<(?:input|select|textarea)\b[^>]*\sid="{control_id}"[^>]*>', html, re.S)
    assert found, f"no control carries id={control_id}"
    return found.group(0)


class TestAddModalLabels:
    def test_modal_is_rendered_with_its_controls(self, page: str, form: str) -> None:
        # The heading sits in the modal frame, above the form element itself.
        assert "Создать задачу" in page
        for field in ("task_goal", "rolling_days", "sched_time", "digest_period"):
            _control_with_name(form, field)

    def test_the_page_has_no_missing_or_ambiguous_label_target(self, page: str) -> None:
        # Whole-page check: the ids are unique among all templates' controls the
        # page renders, so a duplicate prefix that collides with another control
        # would break the association. Macro-owned ids are counted as rendered.
        ids = _ids(page)
        targets = _label_targets(page)
        assert targets, "the page lost its explicit label associations"
        for target in targets:
            assert target in ids, f'<label for="{target}"> has no control with that id'
            assert ids[target] == 1, f'id="{target}" is rendered {ids[target]} times'

    def test_explicit_pairs_inside_the_modal(self, form: str) -> None:
        # A field rendered twice (the add modal and the analyze-only digest
        # block both post `digest_group_by`) gets two ids naming the same field:
        # they must differ, because a label can only anchor one control.
        ids = _ids(form)
        targets = _label_targets(form)
        assert len(targets) == len(set(targets)), f"duplicate label targets: {sorted(targets)}"
        assert all(count == 1 for count in ids.values()), "an id inside the modal is repeated"
        for target in targets:
            assert ids.get(target) == 1, f'<label for="{target}"> anchors {ids.get(target, 0)} controls'

    def test_ids_stay_distinct_across_the_two_digest_blocks(self, form: str) -> None:
        # Two blocks post `digest_group_by`: the analyze-only compose block and
        # the dedicated digest job block. Each select needs its own id, because
        # a label can anchor only one control — this is the collision an
        # id-per-field-name rule would have introduced.
        select_ids = re.findall(r'<select\b[^>]*\sname="digest_group_by"[^>]*>', form, re.S)
        assert len(select_ids) == 2, f"expected both digest_group_by selects, got {len(select_ids)}"
        found = {re.search(r'\sid="([^"]+)"', tag).group(1) for tag in select_ids}
        assert len(found) == 2, f"the two digest_group_by selects share one id: {found}"
        assert {"add-digest_report_group_by", "add-digest_group_by"} == found
        # One label stands apart (`for=`), the other wraps its select; both are
        # associations, and the ids above must not be the same one.
        assert "add-digest_group_by" in _label_targets(form)
        assert (
            'id="add-digest_report_group_by"'
            in re.findall(
                r'<label\b[^>]*>(?:(?!</label>).)*?<select\b[^>]*\sid="add-digest_report_group_by"',
                form,
                re.S,
            )[0]
        )

    def test_task_goal_is_labelled(self, form: str) -> None:
        # The goal input used to sit under a bare <label> that wrapped nothing.
        control = _control_with_name(form, "task_goal")
        control_id = re.search(r'\sid="([^"]+)"', control)
        assert control_id, "task_goal carries no id"
        assert control_id.group(1) in _label_targets(form)

    def test_named_selects_carry_an_associated_label(self, form: str) -> None:
        # Wrapping labels count as association (the page's existing style); the
        # explicit ids added here are asserted where a label stands apart.
        wrapping = re.findall(
            r"<label\b[^>]*>(?:(?!</label>).)*?" r'<(?:input|select|textarea)\b[^>]*\sid="([^"]+)"',
            form,
            re.S,
        )
        targets = set(_label_targets(form)) | set(wrapping)
        for field in (
            "source_scope",
            "window_type",
            "rolling_days",
            "vk_access",
            "digest_window_type",
            "digest_rolling_days",
            "digest_start_date",
            "digest_end_date",
            "digest_period",
            "digest_group_by",
        ):
            control = _control_with_name(form, field)
            control_id = re.search(r'\sid="([^"]+)"', control)
            assert control_id, f"field {field} carries no id"
            assert control_id.group(1) in targets, f"field {field} has no label pointing at its id"

    def test_schedule_controls_are_labelled(self, form: str) -> None:
        targets = set(_label_targets(form))
        for expected in (
            "add-sched_time",
            "add-sched-freq",
            "add-sched-monthday",
            "add-sched-every-hours",
        ):
            assert expected in targets, f"schedule control {expected} lost its label"
        assert 'name="sched_time"' in _control_with_id(form, "add-sched_time")
        assert 'x-model="freq"' in _control_with_id(form, "add-sched-freq")
        assert 'x-model.number="monthday"' in _control_with_id(form, "add-sched-monthday")
        assert 'x-model.number="everyHours"' in _control_with_id(form, "add-sched-every-hours")


class TestAddModalKeepsItsContracts:
    def test_post_contract_unchanged(self, form: str) -> None:
        assert 'method="post"' in form
        assert 'action="/app/tasks"' in form
        assert 'name="_csrf"' in form
        for field in (
            "source_scope",
            "window_type",
            "rolling_days",
            "vk_access",
            "digest_window_type",
            "digest_group_by",
            "digest_time_breakdown",
        ):
            _control_with_name(form, field)

    def test_alpine_state_and_toggles_unchanged(self, form: str) -> None:
        assert 'x-data="addTaskForm()"' in form
        assert 'x-data="scheduleBuilder()"' in form
        assert ':value="cron"' in form
        assert '@click="applyPreset(' in form
        # Modal toggles and the Alpine state shape that backs these inputs live in
        # the page markup around and after the form, so they are read as source.
        page = TEMPLATE.read_text(encoding="utf-8")
        assert '@click="openAdd = false"' in page
        assert '@keydown.escape.window="openAdd = false"' in page
        assert "function addTaskForm()" in page
        for binding in (
            'digest_period: "day"',
            'digest_group_by: "themes"',
            'digest_window_type: "rolling"',
            "source_scope:",
            "keywords_match:",
        ):
            assert binding in page, f"Alpine state lost {binding}"
