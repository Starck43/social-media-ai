"""Strict synthetic settings scopes; stdlib/Jinja only, never application/ORM imports."""

import runpy
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
home = runpy.run_path(str(ROOT / "tests/shell_page_fixtures.py"))
env, out, common = home["env"], home["out"], home["common"]
tenant = NS(
    id=7,
    name="Мониторинг рынка",
    timezone="Europe/Kirov",
    daily_cost_limit=5.0,
    slug="monitor",
    plan="free",
    max_sources=3,
    agent_model=None,
    agent_max_tokens=None,
    agent_temperature=None,
    agent_system_prompt="Пример настройки агента",
)
status = NS(
    platform="vk",
    tone="neutral",
    state="missing",
    label="Не подключено",
    detail="Только ваше подключение",
    is_configured=False,
)
card = dict(
    platform="vk",
    title="ВКонтакте",
    purpose="Личное подключение",
    is_oauth=True,
    manual_hint="Сохраните личный ключ",
    status=status,
    manual_kinds=[("user_token", "VK")],
    rows=[],
)
for name, tab, manage, workspace, operator in [
    ("account", "account", False, True, False),
    ("no-workspace", "account", False, False, False),
    ("member-workspace", "workspace", False, True, False),
    ("member-team", "team", False, True, False),
    ("owner-workspace", "workspace", True, True, False),
    ("owner-team", "team", True, True, False),
    ("agent", "agent", True, True, False),
    ("channels", "channels", True, True, False),
    ("connections", "connections", False, True, False),
    ("operator", "workspace", True, True, True),
]:
    context = {
        **common,
        "section": "settings",
        "tab": tab,
        "tenant": tenant if workspace else None,
        "user": dict(id=101, username="test-member", email="example@fixture.invalid", is_superuser=operator),
        "workspaces": common["workspaces"] if workspace else [],
        "is_superuser": operator,
        "settings_manage": manage,
        "settings_team_visible": manage,
        "perms": NS(
            can=lambda *args: True,
            can_manage_workspace=lambda target, allowed=manage: allowed and target == 7,
            is_owner=manage,
            workspace_role="VIEWER",
        ),
        "member_level": (
            NS(label="Наблюдатель", hint="Просмотр разрешённых материалов") if not manage and workspace else None
        ),
        "visible_tabs": (
            ["account", "connections", "workspace", "team", "agent", "channels"]
            if manage
            else (["account", "connections", "workspace", "team"] if workspace else ["account"])
        ),
        "workspace_summary": dict(name=tenant.name, timezone=tenant.timezone),
        "memberships": [],
        "member_names": {},
        "member_role_keys": {},
        "role_labels": {},
        "plans": ["free"],
        "plan_labels": {"free": "Базовый"},
        "plan_rows": [],
        "plan_usage": {},
        "agent_model_options": [],
        "connections": [card],
        "channels": [],
        "action_types": [],
    }
    rendered = env.get_template("web/settings.html").render(**context)
    (out / f"settings-{name}.html").write_text(
        rendered.replace("https://cdn.tailwindcss.com", "http://fixture.test/static/vendor/tailwind.js")
        .replace(
            "https://cdn.jsdelivr.net/npm/alpinejs@3.14.1/dist/cdn.min.js",
            "http://fixture.test/static/vendor/alpine.js",
        )
        .replace("https://unpkg.com/htmx.org@1.9.12", "http://fixture.test/static/vendor/htmx.js")
    )
print("Rendered ten selected-scope settings states; synthetic only.")
