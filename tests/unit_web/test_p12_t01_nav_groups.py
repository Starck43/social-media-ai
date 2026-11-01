"""Domain-grouped `/app` navigation and `ready=False` section scaffolding (P12-T01)."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from types import SimpleNamespace

from tests.source_import_isolation import load_isolated_source

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
        "overview",
        "monitoring",
        "knowledge",
        "communications",
        "cases_system",
    }
    for item in NAV_ITEMS:
        assert item.group in NAV_GROUP_LABELS, f"{item.key} has unknown group {item.group!r}"
        assert item.group_label == NAV_GROUP_LABELS[item.group]


def test_upcoming_p12_sections_are_registered_as_unready_placeholders() -> None:
    unready = {item.key: item for item in NAV_ITEMS if not item.ready}
    assert set(unready) == {
        "knowledge",
        "documents",
        "communications",
        "followups",
        "cases",
    }
    assert all(item.href == "#" for item in unready.values())
    assert section_of("#") is None


def test_ready_routes_and_mobile_bar_remain_unchanged() -> None:
    ready = {item.key: item.href for item in NAV_ITEMS if item.ready}
    assert ready == {
        "dashboard": "/app/",
        "chat": "/app/chat",
        "sources": "/app/sources",
        "scenarios": "/app/scenarios",
        "tasks": "/app/tasks",
        "analytics": "/app/analytics",
        "digests": "/app/digests",
        "actions": "/app/actions",
        "jobs": "/app/jobs",
        "settings": "/app/settings",
    }
    assert [item.key for item in MOBILE_NAV_ITEMS] == ["dashboard", "sources", "tasks", "jobs"]
    assert all(item.ready and item.href.startswith("/app") for item in MOBILE_NAV_ITEMS)


def test_permission_filtering_applies_to_unready_p12_sections() -> None:
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
