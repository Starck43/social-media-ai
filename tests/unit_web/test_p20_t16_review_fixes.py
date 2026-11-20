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


def test_calendar_has_no_client_timezone_or_time_calculation():
    template = read("app/web/templates/web/task_calendar.html")
    script = read("app/static/js/task-calendar.js")
    assert "Часовой пояс" not in template
    assert "data-timezone" not in template
    assert "timezone=" not in template
    assert "data-today=" in template
    assert "data-date=" in template and "data-time=" in template
    assert "Intl.DateTimeFormat" not in script
    assert "timeZone" not in script
    assert "dataset.date" in script and "dataset.time" in script


def test_work_surface_controls_follow_the_compact_visual_contract():
    dashboard = read("app/web/templates/web/dashboard.html")
    chat = read("app/web/templates/web/chat.html")
    communications = read("app/web/templates/web/_inbox_categories.html")
    planner = read("app/web/templates/web/planner.html")
    calendar = read("app/web/templates/web/task_calendar.html")
    assert 'class="ui-button ui-button-primary" href="/app/chat"' in dashboard
    assert 'class="ui-button" href="/app/planner"' in dashboard
    assert "icon('panel-right', 'chat-panel-toggle-icon')" in chat
    assert "communications-filter-tabs" in communications
    assert '<button type="button" class="ui-button ui-button-icon ui-button-quiet" @click="editOpen = !editOpen"' in planner
    assert "planner-priority--{{ item.priority }}" in planner
    assert '<section hidden aria-hidden="true" class="task-calendar-source' in calendar
    assert "Объём выборки ограничен" not in calendar
    assert "task-calendar-note" not in calendar
    assert "task-calendar-legend" not in calendar


def test_dialog_composer_keeps_one_send_or_stop_control():
    chat = read("app/web/templates/web/chat.html")
    row = chat.split('<div class="chat-input-row">', 1)[1].split('</div>', 1)[0]
    assert chat.count('@click="sending ? stopGeneration() : sendMessage()"') == 1
    assert row.index('@click="handleAttachClick()"') < row.index('x-ref="input"') < row.index('class="chat-send"')
    assert ':disabled="(pending && !sending) || workspaceContextBlocked() || isLocalOnlyBlocked()"' in row
    assert '<input type="hidden" name="_csrf" value="{{ csrf }}">' in chat
