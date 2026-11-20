"""Source-only regressions for honest Work-surface states and links."""

from pathlib import Path

__source_only__ = True


ROOT = Path(__file__).parents[2]


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_task_sources_link_requires_both_permissions():
    template = read("app/web/templates/web/_task_tabs.html")
    assert "key == 'sources' and perms and perms.can('plannertask', 'view') and perms.can('source', 'view')" in template


def test_planner_discloses_partial_page_when_cursor_exists():
    template = read("app/web/templates/web/planner.html")
    assert "data-planner-page-limit" in template
    assert "группы по срокам неполные" in template
    assert "{% if next_before_id %}" in template


def test_calendar_event_projection_does_not_fake_event_detail_url():
    source = read("app/services/workspace/task_surface.py")
    assert '"href": None' in source
    assert '"settings_href": "/app/settings?tab=connections"' in source


def test_calendar_keeps_metadata_only_events_visible_in_grid():
    template = read("app/web/templates/web/task_calendar.html")
    script = read("app/static/js/task-calendar.js")
    assert "data-calendar-title" in template
    assert "data-calendar-link" in template
    assert "document.createElement('span')" in script
    assert "titleNode.textContent.trim()" in script
