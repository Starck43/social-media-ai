"""The `/app` navigation as data (docs/design/ui.md §5).

Section labels and routes live here, not in the template, so a page cannot
appear and leave a dead link behind. `render()` puts `nav` into the context and
the sidebar and the mobile bar both iterate it, marking the active entry by
`section`.

`ready=False` means the section has a plan but no page yet: it renders as a
disabled item with a "скоро" badge instead of a `#` link that silently does
nothing. Landing the page is one flag flip — no template surgery.
"""

from __future__ import annotations

from typing import NamedTuple


class NavItem(NamedTuple):
    key: str
    href: str
    label: str
    ready: bool = True


# The desktop sidebar shows everything (including the roadmap); the mobile bar
# shows only `ready` items, so it never dead-ends on a placeholder.
NAV_ITEMS: tuple[NavItem, ...] = (
    NavItem("dashboard", "/app/", "Дашборд"),
    NavItem("sources", "/app/sources", "Источники"),
    NavItem("tasks", "/app/tasks", "Задачи"),
    NavItem("scenarios", "/app/scenarios", "Сценарии"),
    NavItem("analytics", "/app/analytics", "Аналитика"),
    NavItem("digests", "/app/digests", "Дайджесты"),
    NavItem("jobs", "/app/jobs", "Задания"),
    NavItem("chat", "/app/chat", "Чат с агентом"),
    NavItem("settings", "#", "Настройки", ready=False),
)

# Sections that fit the fixed mobile bar. Capped deliberately: seven full labels
# do not fit a 360px screen, so the bar carries the daily loop (see a source, see
# what happened, tweak the schedule) and the rest stays in the sidebar, which
# returns above `md`.
MOBILE_NAV_KEYS: frozenset[str] = frozenset({"dashboard", "sources", "tasks", "jobs"})
MOBILE_NAV_ITEMS: tuple[NavItem, ...] = tuple(item for item in NAV_ITEMS if item.key in MOBILE_NAV_KEYS)
