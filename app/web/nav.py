"""The `/app` navigation as data (docs/platform/API.md).

Section labels and routes live here, not in the template, so a page cannot
appear and leave a dead link behind. `render()` puts `nav` into the context and
the sidebar and mobile drawer both iterate it, marking the active entry by
`section`.

`ready=False` means the section has a plan but no page yet: it renders as a
disabled item with a "скоро" badge instead of a `#` link that silently does
nothing. Landing the page is one flag flip — no template surgery.

Add sidebar items only for a module’s own objects opened daily. Integrations
are connections in Settings and appear as context or sources, not new entries.
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
    group: str = "overview"
    group_label: str = ""


NAV_GROUP_LABELS: dict[str, str] = {
    "assistant": "Ассистент",
    "dashboard": "Дашборд",
    "tasks": "Задачи",
    "messages": "Сообщения",
    "docs": "Документация",
    "crm": "CRM",
    "erp": "ERP",
    "data": "Данные",
    "system": "Система",
}


# Proposal v1: six business sections own the sidebar (dashboard, tasks, messages,
# documentation, CRM, ERP); the assistant sits above them, the collection and
# queue machinery below. Existing routes and permissions are unchanged; group
# order is reading order.
NAV_ITEMS: tuple[NavItem, ...] = (
    # Ассистент
    NavItem("chat", "/app/chat", "Чат с агентом", icon="chat", group="assistant", group_label="Ассистент"),
    # Дашборд
    NavItem("dashboard", "/app/", "Сегодня", icon="home", group="dashboard", group_label="Дашборд"),
    NavItem(
        "analytics",
        "/app/analytics",
        "Аналитика",
        required_permission="analysis.result.view",
        icon="chart",
        group="dashboard",
        group_label="Дашборд",
    ),
    NavItem(
        "digests",
        "/app/digests",
        "Дайджесты",
        required_permission="notifications.digest_run.view",
        icon="document",
        group="dashboard",
        group_label="Дашборд",
    ),
    # Задачи
    NavItem(
        "tasks",
        "/app/task-hub",
        "Задачи",
        required_permission="scheduling.task.view",
        icon="calendar",
        group="tasks",
        group_label="Задачи",
    ),
    NavItem(
        "followups",
        "/app/followups",
        "Договорённости",
        required_permission="scheduling.task.view",
        icon="clock",
        group="tasks",
        group_label="Задачи",
    ),
    # Сообщения
    NavItem(
        "communications",
        "/app/communications",
        "Входящие",
        required_permission="scheduling.action.view",
        icon="inbox",
        group="messages",
        group_label="Сообщения",
    ),
    # Документация
    NavItem(
        "documents",
        "/app/documents",
        "Документы и версии",
        required_permission="analysis.result.view",
        icon="document",
        group="docs",
        group_label="Документация",
    ),
    NavItem(
        "knowledge",
        "/app/knowledge",
        "База знаний",
        required_permission="analysis.result.view",
        icon="database",
        group="docs",
        group_label="Документация",
    ),
    NavItem(
        "files",
        "/app/knowledge/files",
        "Файлы",
        required_permission="analysis.result.view",
        icon="clip",
        group="docs",
        group_label="Документация",
    ),
    # CRM
    NavItem(
        "contacts",
        "/app/contacts",
        "Контакты",
        required_permission="contacts.contact.view",
        icon="users",
        group="crm",
        group_label="CRM",
    ),
    NavItem(
        "cases",
        "/app/cases",
        "Предложения",
        required_permission="scheduling.action.view",
        icon="briefcase",
        group="crm",
        group_label="CRM",
    ),
    # ERP
    NavItem(
        "onec",
        "/app/settings/onec",
        "1С: обмен данными",
        required_permission="onec.data.view",
        icon="chain",
        group="erp",
        group_label="ERP",
    ),
    NavItem(
        "onec_audit",
        "/app/settings/onec/audit",
        "Аудит 1С",
        required_permission="onec.data.view",
        icon="check",
        group="erp",
        group_label="ERP",
    ),
    # Данные
    NavItem(
        "sources",
        "/app/sources",
        "Источники",
        required_permission="collection.source.view",
        icon="database",
        group="data",
        group_label="Данные",
    ),
    NavItem(
        "scenarios",
        "/app/scenarios",
        "Сценарии",
        required_permission="analysis.scenario.view",
        icon="layers",
        group="data",
        group_label="Данные",
    ),
    # Система
    NavItem(
        "actions",
        "/app/actions",
        "Действия",
        required_permission="scheduling.action.view",
        icon="check",
        group="system",
        group_label="Система",
    ),
    NavItem(
        "jobs",
        "/app/jobs",
        "Очередь задач",
        required_permission="scheduling.job.view",
        icon="list",
        group="system",
        group_label="Система",
    ),
    NavItem(
        "settings",
        "/app/settings",
        "Настройки",
        icon="settings",
        group="system",
        group_label="Система",
    ),
)

# A direct planner start-page preference remains valid after consolidating the
# sidebar. Start-page choices and navigation entries have different purposes.
PLANNER_SECTION = NavItem("planner", "/app/planner", "Мои дела", required_permission="planner.task.view")
START_PAGE_ITEMS = (*NAV_ITEMS, PLANNER_SECTION)

# Proposed O-3 composition; More is a drawer button in the template.
MOBILE_NAV_KEYS: tuple[str, ...] = ("dashboard", "chat", "communications", "tasks")
MOBILE_NAV_ITEMS: tuple[NavItem, ...] = tuple(
    item for key in MOBILE_NAV_KEYS for item in NAV_ITEMS if item.key == key and item.ready
)


def allows(item: NavItem, perms: Any) -> bool:
    """True when the caller may open the section (no requirement = everyone signed in)."""
    if item.key == "chat":
        return bool(perms is not None and getattr(perms, "can_open_chat", False))
    if item.key == "tasks":
        return bool(
            perms is not None
            and (
                perms.can("agenttask", "view")
                or perms.can("scheduling.task", "view")
                or perms.can("plannertask", "view")
                or perms.can("planner.task", "view")
            )
        )
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
    if path == "/app/planner" or path.startswith("/app/planner/"):
        return PLANNER_SECTION
    if path == "/app/tasks" or path.startswith("/app/tasks/"):
        return NavItem("agent_tasks", "/app/tasks", "Поручения агенту", required_permission="scheduling.task.view")
    # `/app/knowledge/files` nests under `/app/knowledge`, so the deepest match
    # owns the section; a shorter prefix never shadows a nested page.
    matches = [
        item
        for item in NAV_ITEMS
        if item.ready
        and (item.required_permission is not None or item.key == "chat")
        and (path == item.href or path.startswith(item.href + "/"))
    ]
    if not matches:
        return None
    return max(matches, key=lambda item: len(item.href))
