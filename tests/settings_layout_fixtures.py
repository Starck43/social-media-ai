"""Standalone strict settings fixtures: no app, DB, secrets or provider imports."""

from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace as NS

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

ROOT = Path(__file__).resolve().parents[1]
MODES = (
    "account",
    "no-workspace",
    "member-workspace",
    "member-team",
    "owner-workspace",
    "owner-team",
    "team-empty",
    "agent",
    "agent-error",
    "channels",
    "channels-empty",
    "connections",
    "operator",
)


def environment():
    env = Environment(
        loader=FileSystemLoader(ROOT / "app/web/templates"),
        autoescape=select_autoescape(),
        undefined=StrictUndefined,
        extensions=["jinja2.ext.do"],
    )
    env.filters["ui_label"] = str
    env.filters["human_dt"] = lambda value, **kwargs: str(value or "")
    env.globals["static_v"] = lambda path: "http://fixture.test/static/" + path
    env.globals["url_for"] = lambda name, path: "http://fixture.test/static/" + path
    return env


def context(mode):
    workspace = mode != "no-workspace"
    manage = mode in {
        "owner-workspace",
        "owner-team",
        "team-empty",
        "agent",
        "agent-error",
        "channels",
        "channels-empty",
    }
    tab = {
        "no-workspace": "account",
        "member-workspace": "workspace",
        "owner-workspace": "workspace",
        "member-team": "team",
        "owner-team": "team",
        "team-empty": "team",
        "operator": "workspace",
        "agent-error": "agent",
        "channels-empty": "channels",
    }.get(mode, mode)
    tenant = NS(
        id=7,
        name="Мониторинг рынка",
        timezone="Europe/Kirov",
        daily_cost_limit=5.0,
        slug="monitor",
        plan="free",
        max_sources=3,
        agent_model="model-a",
        agent_max_tokens=1024,
        agent_temperature=0.3,
        agent_system_prompt="Отвечай кратко и по делу.",
    )
    perms = NS(
        can=lambda *args: True,
        can_manage_workspace=lambda target: manage and target == 7,
        can_manage_member=lambda role, target=None: manage and (target is None or target != "owner" or role == "owner"),
        is_owner=manage,
        workspace_role="owner" if manage else "viewer",
    )
    return dict(
        section="settings",
        tab=tab,
        tenant=tenant if workspace else None,
        user=NS(
            id=101,
            username="sergey",
            email="sergey@fixture.invalid",
            role=None,
            has_admin_access=lambda: mode == "operator",
        ),
        workspaces=[],
        nav=[
            NS(key=key, href=href, label=label, ready=True, icon=icon)
            for key, href, label, icon in [
                ("dashboard", "/app/", "Дашборд", "home"),
                ("sources", "/app/sources", "Источники", "sources"),
                ("tasks", "/app/tasks", "Задачи", "tasks"),
                ("analytics", "/app/analytics", "Аналитика", "analytics"),
                ("digests", "/app/digests", "Дайджесты", "document"),
                ("chat", "/app/chat", "Чат с агентом", "chat"),
            ]
        ],
        csrf="fixture-csrf",
        messages=[],
        flashes=[],
        request=NS(query_params={}),
        perms=perms,
        is_superuser=False,
        settings_manage=manage,
        settings_team_visible=manage,
        member_level=(
            None if manage or not workspace else NS(label="Наблюдатель", hint="Просмотр разрешённых материалов")
        ),
        visible_tabs=(
            ["account", "connections", "workspace", "team", "agent", "channels"]
            if manage
            else (["account", "connections", "workspace", "team"] if workspace else ["account"])
        ),
        workspace_summary=dict(name=tenant.name, timezone=tenant.timezone),
        memberships=(
            []
            if mode == "team-empty"
            else [
                NS(id=1, user_id=101, ai_chat_enabled=True),
                NS(id=2, user_id=102, ai_chat_enabled=True),
                NS(id=3, user_id=103, ai_chat_enabled=False),
            ]
        ),
        member_names={101: "Сергей Шабалин", 102: "Анна Смирнова", 103: "ОченьДлинноеИмяБезПробелов" * 4},
        member_role_keys={1: "owner", 2: "editor", 3: "viewer"},
        role_labels={"owner": "Владелец", "editor": "Редактор", "viewer": "Наблюдатель"},
        plans=["free", "pro"],
        plan_labels={"free": "Базовый", "pro": "Профессиональный"},
        plan_rows=[dict(key="max_sources", label="Источники", free=3, pro=30)],
        plan_usage={"max_sources": 2},
        agent_model_options=[
            dict(id="model-a", label="Provider / Chat model ($0.0010/$0.0020/1K) ★"),
            dict(id="model-b", label="ДлинноеНазваниеМодели" * 5),
        ],
        probe_result=dict(
            ok=mode != "agent-error",
            text=(
                "Статус модели и локальный mock проверены"
                if mode != "agent-error"
                else "Модель недоступна: выберите другую модель"
            ),
        ),
        channels=(
            []
            if mode == "channels-empty"
            else [
                NS(
                    id=12,
                    title="Команда маркетинга",
                    channel_type="Telegram",
                    platform="telegram",
                    channel_id="fixture",
                    is_active=True,
                    is_digest_target=True,
                )
            ]
        ),
        connections=[
            dict(
                platform="vk",
                title="ВКонтакте",
                purpose="Сбор данных с вашим личным доступом",
                is_oauth=True,
                status=NS(
                    state="missing",
                    tone="neutral",
                    label="Не подключено",
                    detail="Подключите аккаунт",
                    is_configured=False,
                ),
                manual_kinds=[("user_token", "Личный токен")],
                rows=[],
                manual_hint="Резервный способ подключения",
            )
        ],
    )


def invite_context(state="open"):
    c = context("owner-team")
    c.update(
        issued_invite=(
            dict(
                qr_svg=None,
                code="FIXTURE-ONLY",
                link="http://fixture.test/app/invite/fake",
                role="Наблюдатель",
                expires="26.10.2026",
            )
            if state == "issued"
            else None
        ),
        invite_roles=[
            NS(key="viewer", label="Наблюдатель", hint="Просмотр материалов"),
            NS(key="editor", label="Редактор", hint="Работа с материалами"),
        ],
        invite_default="viewer",
        invite_days=[1, 7, 30],
        open_invites=(
            []
            if state == "empty"
            else [dict(id=42, role="Наблюдатель", created="19.10.2026", expires="26.10.2026 UTC", uses="0 из 1")]
        ),
    )
    return c


def render(out):
    out.mkdir(parents=True, exist_ok=True)
    env = environment()
    for mode in MODES:
        html = env.get_template("web/settings.html").render(**context(mode))
        for source, target in [
            ("https://cdn.tailwindcss.com", "tailwind.js"),
            ("https://cdn.jsdelivr.net/npm/alpinejs@3.14.1/dist/cdn.min.js", "alpine.js"),
            ("https://unpkg.com/htmx.org@1.9.12", "htmx.js"),
        ]:
            html = html.replace(source, "http://fixture.test/static/vendor/" + target)
        (out / f"settings-{mode}.html").write_text(html)
    for state in ["open", "empty", "issued"]:
        (out / f"invites-{state}.html").write_text(
            env.get_template("web/_invite_dialog.html").render(**invite_context(state))
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    render(parser.parse_args().out)
    print(f"Rendered {len(MODES)} settings states and 3 invitation states; synthetic only.")
