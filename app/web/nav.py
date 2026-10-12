"""The `/app` navigation as data (docs/API.md).

Section labels and routes live here, not in the template, so a page cannot
appear and leave a dead link behind. `render()` puts `nav` into the context and
the sidebar and the mobile bar both iterate it, marking the active entry by
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


# The desktop sidebar shows everything (including the roadmap); the mobile bar
# shows only `ready` items, so it never dead-ends on a placeholder.
NAV_ITEMS: tuple[NavItem, ...] = (
    NavItem("dashboard", "/app/", "Дашборд"),
    NavItem("sources", "/app/sources", "Источники", required_permission="source.view"),
    NavItem("scenarios", "/app/scenarios", "Сценарии", required_permission="agentscenario.view"),
    NavItem("tasks", "/app/tasks", "Задачи", required_permission="agenttask.view"),
    NavItem("analytics", "/app/analytics", "Аналитика", required_permission="aianalytics.view"),
    NavItem("digests", "/app/digests", "Дайджесты", required_permission="digestrun.view"),
    NavItem("jobs", "/app/jobs", "Очередь задач"),
    NavItem("chat", "/app/chat", "Чат с агентом"),
    NavItem("settings", "/app/settings", "Настройки"),
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
