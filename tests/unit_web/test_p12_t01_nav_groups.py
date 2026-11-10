"""Domain-grouped `/app` navigation and `ready=False` section scaffolding (P12-T01)."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from types import SimpleNamespace

from tests.source_import_isolation import load_isolated_source

__source_only__ = True

ROOT = Path(__file__).resolve().parents[2]


class _ActionType(str, Enum):
    VIEW = "view"
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    ANALYZE = "analyze"
    EXPORT = "export"
    CONFIGURE = "configure"

    @property
    def db_value(self) -> str:
        return self.value

    @classmethod
    def get_by_value(cls, val: str):
        return cls._value2member_map_.get(val)


class _WorkspaceRoleType(str, Enum):
    OWNER = "owner"
    ADMIN = "admin"
    MANAGER = "manager"
    ANALYST = "analyst"
    VIEWER = "viewer"


_types_stub = SimpleNamespace(
    ActionType=_ActionType,
    PlatformRoleType=SimpleNamespace,
    WorkspaceRoleType=_WorkspaceRoleType,
)
catalog = load_isolated_source("_p12_t01_catalog", ROOT / "app/core/access_catalog.py", {"app.types": _types_stub})
nav_mod = load_isolated_source("_p12_t01_nav", ROOT / "app/web/nav.py", {"app.core.access_catalog": catalog})

NAV_ITEMS = nav_mod.NAV_ITEMS
NAV_GROUP_LABELS = nav_mod.NAV_GROUP_LABELS
MOBILE_NAV_ITEMS = nav_mod.MOBILE_NAV_ITEMS
allows = nav_mod.allows
section_of = nav_mod.section_of


class _StubPerms:
    def __init__(self, rights: set[tuple[str, str]], *, can_open_chat: bool = False):
        self._rights = rights
        self.can_open_chat = can_open_chat

    def can(self, model_or_key: str, action: str) -> bool:
        return (model_or_key, action) in self._rights


def test_every_nav_item_belongs_to_a_declared_domain_group() -> None:
    assert set(NAV_GROUP_LABELS) == {
        "work",
        "clients",
        "knowledge",
        "monitoring",
        "system",
    }
    for item in NAV_ITEMS:
        assert item.group in NAV_GROUP_LABELS, f"{item.key} has unknown group {item.group!r}"
        assert item.group_label == NAV_GROUP_LABELS[item.group]


def test_p12_sections_are_live_and_gated() -> None:
    """Wave 2 used to be placeholders; P12-T09 activated them.

    A section is live only when its nav entry points at a real path and the
    path belongs to a gated section, so `section_of` can enforce the
    permission. Routes themselves are checked in `tests/web/test_p12_routes.py`.
    """
    p12 = {
        "knowledge": "/app/knowledge",
        "documents": "/app/documents",
        "communications": "/app/communications",
        "followups": "/app/followups",
        "cases": "/app/cases",
    }
    for key, href in p12.items():
        item = next(item for item in NAV_ITEMS if item.key == key)
        assert item.ready, f"{key} is still a placeholder"
        assert item.href == href
        assert section_of(href) == item
        assert item.required_permission is not None


def test_ready_routes_remain_and_mobile_bar_is_daily_loop() -> None:
    ready = {item.key: item.href for item in NAV_ITEMS if item.ready}
    assert ready == {
        "dashboard": "/app/",
        "chat": "/app/chat",
        "sources": "/app/sources",
        "scenarios": "/app/scenarios",
        "tasks": "/app/task-hub",
        "analytics": "/app/analytics",
        "digests": "/app/digests",
        "actions": "/app/actions",
        "jobs": "/app/jobs",
        "settings": "/app/settings",
        "knowledge": "/app/knowledge",
        "documents": "/app/documents",
        "communications": "/app/communications",
        "followups": "/app/followups",
        "cases": "/app/cases",
        "contacts": "/app/contacts",
    }
    assert [item.key for item in MOBILE_NAV_ITEMS] == ["dashboard", "chat", "communications", "tasks"]
    tasks = next(item for item in NAV_ITEMS if item.key == "tasks")
    assert allows(tasks, _StubPerms({("planner.task", "view")}))
    assert allows(tasks, _StubPerms({("scheduling.task", "view")}))
    assignments = section_of("/app/tasks")
    assert assignments is not None and assignments.key == "agent_tasks"
    assert assignments.required_permission == "scheduling.task.view"
    planner = section_of("/app/planner")
    assert planner is not None and planner.key == "planner"
    assert planner.required_permission == "planner.task.view"
    assert all(item.ready and item.href.startswith("/app") for item in MOBILE_NAV_ITEMS)


def test_permission_filtering_applies_to_p12_sections() -> None:
    knowledge = next(item for item in NAV_ITEMS if item.key == "knowledge")
    cases = next(item for item in NAV_ITEMS if item.key == "cases")
    assert not allows(knowledge, None)
    assert allows(knowledge, _StubPerms({("analysis.result", "view")}))
    assert not allows(cases, _StubPerms({("collection.source", "view")}))
    assert allows(cases, _StubPerms({("scheduling.action", "view")}))


def test_shell_nav_template_renders_domain_group_headings_with_default_guard() -> None:
    template_source = (ROOT / "app/web/templates/web/_shell_nav.html").read_text()
    assert "item.group_label|default('')" in template_source
    assert 'class="shell-nav-group"' in template_source
