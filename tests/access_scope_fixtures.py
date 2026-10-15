"""Owner/guest web fixtures and an admin-entry template stub; no app imports or DB."""

import runpy
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
home = runpy.run_path(str(ROOT / "tests/home_overview_fixtures.py"))
settings = runpy.run_path(str(ROOT / "tests/settings_scope_fixtures.py"))
env, out = home["env"], home["OUT"]
nav = runpy.run_path(str(ROOT / "app/web/nav.py"))
workspace_list = [
    dict(tenant=NS(id=7, name="Моё пространство"), active=True, is_owner=True, role="owner", role_label="Владелец"),
    dict(
        tenant=NS(id=8, name="Пространство другого владельца"),
        active=False,
        is_owner=False,
        role="VIEWER",
        role_label="Наблюдатель",
    ),
]


def save(name, template, data):
    html = env.get_template(template).render(**data)
    for src, target in [
        ("https://cdn.tailwindcss.com", "http://fixture.test/static/vendor/tailwind.js"),
        ("https://cdn.jsdelivr.net/npm/alpinejs@3.14.1/dist/cdn.min.js", "http://fixture.test/static/vendor/alpine.js"),
        ("https://unpkg.com/htmx.org@1.9.12", "http://fixture.test/static/vendor/htmx.js"),
    ]:
        html = html.replace(src, target)
    (out / f"{name}.html").write_text(html)


for owner in [True, False]:
    tid = 7 if owner else 8
    rights = {("aianalytics", "view"), ("source", "view"), ("digestrun", "view")}
    if owner:
        rights |= {("agenttask", "view"), ("source", "create"), ("agenttask", "create")}
    perms = NS(
        can=lambda model, action: (model, action) in rights,
        can_manage_workspace=lambda target: owner and target == tid,
        is_owner=owner,
        can_open_chat=owner,
        workspace_role="owner" if owner else "viewer",
    )
    workspaces = [{**w, "active": w["tenant"].id == tid} for w in workspace_list]
    common = {
        **home["base"],
        "tenant": NS(id=tid, name="Моё пространство" if owner else "Пространство другого владельца"),
        "workspaces": workspaces,
        "perms": perms,
        "member_level": NS(
            label="Владелец" if owner else "Наблюдатель", hint="Разрешённые материалы текущего пространства"
        ),
        "nav": tuple(item for item in nav["NAV_ITEMS"] if item.key != "jobs" and nav["allows"](item, perms)),
        "can_open_chat": owner,
        "is_superuser": False,
        "recent_results": [],
        "recent_tasks": [],
        "next_step": None,
        "offer": {k: (k != "tasks" or owner) for k in home["base"]["offer"]},
    }
    prefix = "access-owner" if owner else "access-guest"
    save(prefix, "web/dashboard.html", common)
    save(
        prefix + "-chat-denied",
        "web/section_denied.html",
        {**common, "section": "chat", "denied_section": "Чат с агентом"},
    )
    data = {
        **settings["context"],
        **common,
        "section": "settings",
        "tab": "team",
        "visible_tabs": ["account", "connections", "workspace", "team"],
        "settings_manage": owner,
        "settings_team_visible": owner,
        "memberships": [NS(id=21, user_id=101), NS(id=33, user_id=202)] if owner else [],
        "member_names": {101: "Владелец", 202: "Приглашённый участник"},
        "member_role_keys": {21: "owner", 33: "viewer"},
        "role_labels": {"owner": "Владелец", "viewer": "Наблюдатель"},
    }
    save(prefix + "-team", "web/settings.html", data)
print("Rendered six owner/guest access states. Synthetic only.")
