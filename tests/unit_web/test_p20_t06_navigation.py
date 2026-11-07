"""Navigation contract, isolated from the application database."""

from pathlib import Path
from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader

from tests.source_import_isolation import load_isolated_source

ROOT = Path(__file__).resolve().parents[2]


def resource_for(key):
    return SimpleNamespace(key=key, model_name=key)


nav = load_isolated_source(
    "_p20_t06_nav", ROOT / "app/web/nav.py", {"app.core.access_catalog": SimpleNamespace(resource_for=resource_for)}
)

EXPECTED = {
    "dashboard": ("/app/", None),
    "chat": ("/app/chat", None),
    "communications": ("/app/communications", "scheduling.action.view"),
    "tasks": ("/app/tasks", "scheduling.task.view"),
    "planner": ("/app/planner", "planner.task.view"),
    "contacts": ("/app/contacts", "contacts.contact.view"),
    "followups": ("/app/followups", "scheduling.task.view"),
    "cases": ("/app/cases", "scheduling.action.view"),
    "knowledge": ("/app/knowledge", "analysis.result.view"),
    "documents": ("/app/documents", "analysis.result.view"),
    "sources": ("/app/sources", "collection.source.view"),
    "scenarios": ("/app/scenarios", "analysis.scenario.view"),
    "analytics": ("/app/analytics", "analysis.result.view"),
    "digests": ("/app/digests", "notifications.digest_run.view"),
    "actions": ("/app/actions", "scheduling.action.view"),
    "jobs": ("/app/jobs", "scheduling.job.view"),
    "settings": ("/app/settings", None),
}


class Perms:
    def __init__(self, allowed=(), chat=False):
        self.allowed = set(allowed)
        self.can_open_chat = chat

    def can(self, resource, action):
        return (resource, action) in self.allowed


def test_all_routes_are_unique_ready_and_keep_permissions():
    assert len(nav.NAV_ITEMS) == len(EXPECTED)
    assert len({item.key for item in nav.NAV_ITEMS}) == len(EXPECTED)
    assert {item.key: (item.href, item.required_permission) for item in nav.NAV_ITEMS} == EXPECTED
    assert all(item.ready for item in nav.NAV_ITEMS)
    for item in nav.NAV_ITEMS:
        if item.key == "chat":
            assert not nav.allows(item, Perms())
            assert nav.allows(item, Perms(chat=True))
        elif item.required_permission:
            resource, _, action = item.required_permission.rpartition(".")
            assert not nav.allows(item, Perms())
            assert nav.allows(item, Perms({(resource, action)}))
            assert nav.section_of(item.href) == item
        else:
            assert nav.allows(item, Perms())


def test_group_order_and_mobile_order_are_stable():
    assert list(dict.fromkeys(item.group for item in nav.NAV_ITEMS)) == [
        "work",
        "clients",
        "knowledge",
        "monitoring",
        "system",
    ]
    assert [item.key for item in nav.MOBILE_NAV_ITEMS] == ["dashboard", "chat", "communications", "tasks"]
    assert all(item.ready for item in nav.MOBILE_NAV_ITEMS)
    assert all(item.group_label == nav.NAV_GROUP_LABELS[item.group] for item in nav.NAV_ITEMS)


def test_template_orders_accessible_groups_and_exposes_drawer_button():
    env = Environment(loader=FileSystemLoader(ROOT / "app/web/templates"), autoescape=True)
    env.filters["ui_label"] = str
    template = env.get_template("web/_shell_nav.html")
    user = SimpleNamespace(username="Test", role=None, has_admin_access=lambda: False)
    html = template.render(menu_id="desktop", user=user, tenant=None, nav=nav.NAV_ITEMS, workspaces=[], perms=None)
    assert 'aria-label="Основные разделы"' in html
    # With a tenant, headings and links use the same source order.
    tenant = SimpleNamespace(id=1, name="Example")
    html = template.render(menu_id="desktop", user=user, tenant=tenant, nav=nav.NAV_ITEMS, workspaces=[], perms=None)
    offsets = [html.index(f'class="shell-nav-group">{label}</h2>') for label in nav.NAV_GROUP_LABELS.values()]
    assert offsets == sorted(offsets)
    base = (ROOT / "app/web/templates/web/base.html").read_text()
    assert 'aria-label="Быстрая навигация"' in base
    assert 'data-dialog-open="navigation-drawer" aria-controls="navigation-drawer"' in base
    assert "{% for item in mobile_nav|default([], true) %}" in base


def test_mobile_bar_renders_only_ready_permitted_items_and_skips_embedded():
    env = Environment(loader=FileSystemLoader(ROOT / "app/web/templates"), autoescape=True)
    env.filters["ui_label"] = str
    env.globals["static_v"] = lambda path: "/static/" + path
    env.globals["url_for"] = lambda name, path: "/static/" + path
    user = SimpleNamespace(id=1, username="Test", role=None, has_admin_access=lambda: False)
    tenant = SimpleNamespace(id=1, name="Example")
    perms = SimpleNamespace(is_owner=True, can_manage_workspace=lambda _: False)
    allowed = Perms({("scheduling.task", "view")}, chat=False)
    mobile = tuple(item for item in nav.MOBILE_NAV_ITEMS if nav.allows(item, allowed))
    context = dict(
        user=user,
        tenant=tenant,
        perms=perms,
        request=SimpleNamespace(query_params={}),
        nav=(),
        mobile_nav=mobile,
        workspaces=[],
        csrf="test",
        section="dashboard",
        member_level=None,
        flashes=[],
    )
    html = env.get_template("web/base.html").render(**context)
    bar = html.split('<nav class="shell-mobile-bar"', 1)[1].split("</nav>", 1)[0]
    assert 'href="/app/"' in bar and 'href="/app/tasks"' in bar
    assert 'href="/app/chat"' not in bar and 'href="/app/communications"' not in bar
    assert bar.index("Сегодня") < bar.index("Задачи") < bar.index("Ещё")
    context["request"] = SimpleNamespace(query_params={"embedded": "1"})
    assert 'class="shell-mobile-bar"' not in env.get_template("web/base.html").render(**context)
