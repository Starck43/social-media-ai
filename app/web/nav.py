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
    required_permission: str | None = None  # catalog key, e.g. "collection.source.view"
    icon: str = "circle"


# The shell and mobile drawer consume the same permission-filtered daily sections.
NAV_ITEMS: tuple[NavItem, ...] = (
    NavItem("dashboard", "/app/", "Обзор", icon="home"),
    NavItem("sources", "/app/sources", "Источники", required_permission="collection.source.view", icon="database"),
    NavItem("scenarios", "/app/scenarios", "Сценарии", required_permission="analysis.scenario.view", icon="layers"),
    NavItem("tasks", "/app/tasks", "Задачи", required_permission="scheduling.task.view", icon="calendar"),
    NavItem("actions", "/app/actions", "Действия", required_permission="scheduling.action.view", icon="document"),
    NavItem("analytics", "/app/analytics", "Аналитика", required_permission="analysis.result.view", icon="chart"),
    NavItem(
        "digests", "/app/digests", "Дайджесты", required_permission="notifications.digest_run.view", icon="document"
    ),
    NavItem("jobs", "/app/jobs", "Очередь задач", required_permission="scheduling.job.view", icon="list"),
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
    if item.key == "chat":
        return bool(perms is not None and getattr(perms, "can_open_chat", False))
    if item.required_permission is None:
        return True
    # The right is a catalog key, not `model.action`: resource keys carry their
    # own dots (`scheduling.task.view`), so splitting on the last one would ask
    # about a resource called `scheduling` and hide every such section.
    from app.core.access_catalog import resource_for

    resource_key, _, action = item.required_permission.rpartition(".")
    resource = resource_for(resource_key)
    if resource is None:
        return False
    # A `WebPerms` answers in model names; tests pass a stub keyed by catalog
    # resource keys, as `NavItem.required_permission` is written.
    return perms is not None and (perms.can(resource.model_name, action) or perms.can(resource.key, action))


def section_of(path: str) -> NavItem | None:
    """The gated section a `/app` path belongs to, or None (dashboard, settings, auth)."""
    for item in NAV_ITEMS:
        if (item.required_permission is not None or item.key == "chat") and (
            path == item.href or path.startswith(item.href + "/")
        ):
            return item
    return None
