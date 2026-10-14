"""The `/app` navigation as data (docs/API.md).

Section labels and routes live here, not in the template, so a page cannot
appear and leave a dead link behind. `render()` puts `nav` into the context and
the sidebar and mobile drawer both iterate it, marking the active entry by
`section`.

`ready=False` means the section has a plan but no page yet: it renders as a
disabled item with a "скоро" badge instead of a `#` link that silently does
nothing. Landing the page is one flag flip — no template surgery.
"""

from __future__ import annotations

from typing import Any, NamedTuple


class NavItem(NamedTuple):
    key: str
    href: str
    label: str
    ready: bool = True
    required_permission: str | None = None  # e.g., "source.view", "agenttask.create"
    icon: str = "circle"


# The shell and mobile drawer consume the same permission-filtered daily sections.
NAV_ITEMS: tuple[NavItem, ...] = (
    NavItem("dashboard", "/app/", "Обзор", icon="home"),
    NavItem("sources", "/app/sources", "Источники", required_permission="source.view", icon="database"),
    NavItem("scenarios", "/app/scenarios", "Сценарии", required_permission="agentscenario.view", icon="layers"),
    NavItem("tasks", "/app/tasks", "Задачи", required_permission="agenttask.view", icon="calendar"),
    NavItem("analytics", "/app/analytics", "Аналитика", required_permission="aianalytics.view", icon="chart"),
    NavItem("digests", "/app/digests", "Дайджесты", required_permission="digestrun.view", icon="document"),
    NavItem("jobs", "/app/jobs", "Очередь задач", icon="list"),
    NavItem("chat", "/app/chat", "Чат с агентом", icon="message"),
    NavItem("settings", "/app/settings", "Настройки", icon="settings"),
)

# Sections that fit the fixed mobile bar. Capped deliberately: seven full labels
# do not fit a 360px screen, so the bar carries the daily loop (see a source, see
# what happened, tweak the schedule) and the rest stays in the sidebar, which
# returns above `md`.
MOBILE_NAV_KEYS: frozenset[str] = frozenset({"dashboard", "sources", "tasks", "jobs"})
MOBILE_NAV_ITEMS: tuple[NavItem, ...] = tuple(item for item in NAV_ITEMS if item.key in MOBILE_NAV_KEYS)


def allows(item: NavItem, perms: Any) -> bool:
    """True when the caller may open the section (no requirement = everyone signed in)."""
    if item.required_permission is None:
        return True
    model_name, _, action = item.required_permission.rpartition(".")
    return perms is not None and perms.can(model_name, action)


def section_of(path: str) -> NavItem | None:
    """The gated section a `/app` path belongs to, or None (dashboard, settings, auth)."""
    for item in NAV_ITEMS:
        if item.required_permission is not None and (path == item.href or path.startswith(item.href + "/")):
            return item
    return None
