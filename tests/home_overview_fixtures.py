"""Synthetic home fixtures, not an authentication/ASGI integration test.

Use the project Jinja dependency; no providers, application imports or DB.
"""

import argparse
from pathlib import Path
from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

parser = argparse.ArgumentParser(description="Render home fixtures without app imports or DB access.")
parser.add_argument("--out", type=Path, required=True)
args = parser.parse_args()
ROOT = Path(__file__).resolve().parents[1]
OUT = args.out.resolve()
env = Environment(
    loader=FileSystemLoader(ROOT / "app/web/templates"), autoescape=select_autoescape(), undefined=StrictUndefined
)
env.filters["ui_label"] = str
env.filters["human_dt"] = lambda value, **kwargs: str(value or "")
env.globals["url_for"] = lambda name, path: "http://fixture.test/static/" + path
all_rights = {
    ("source", "view"),
    ("source", "create"),
    ("agenttask", "view"),
    ("agenttask", "create"),
    ("aianalytics", "view"),
    ("digestrun", "view"),
}
nav_data = [
    ("dashboard", "/app/", "Дашборд"),
    ("sources", "/app/sources", "Источники"),
    ("tasks", "/app/tasks", "Задачи"),
    ("analytics", "/app/analytics", "Аналитика"),
    ("digests", "/app/digests", "Дайджесты"),
    ("chat", "/app/chat", "Чат с агентом"),
    ("settings", "/app/settings", "Настройки"),
]
base = {
    "tenant": {"id": 7, "name": "Мониторинг рынка"},
    "user": {"username": "test-member", "is_superuser": False},
    "workspaces": [{"tenant": {"id": 7, "name": "Мониторинг рынка"}, "active": True, "role": "owner"}],
    "nav": [{"key": key, "href": href, "label": label, "ready": True} for key, href, label in nav_data],
    "mobile_nav": [{"key": key, "href": href, "label": label, "ready": True} for key, href, label in nav_data[:3]],
    "section": "dashboard",
    "csrf": "local-fixture-only",
    "connection_alerts": [],
    "unread_notifications": 0,
    "flashes": [],
    "can_open_chat": True,
    "is_superuser": False,
    "tenants": [],
    "filter_tenant_id": None,
    "next_step": None,
    "offer": {k: True for k in ["sources", "add_source", "add_scenario", "tasks", "add_task", "digests", "analytics"]},
    "recent_results": [
        {
            "id": i,
            "source_name": "VK · Сообщество бренда",
            "created_at": "сегодня, 10:30",
            "title": title,
            "summary": "Обсуждение новых предложений и обратная связь покупателей. В результате выделены основные темы и вопросы, требующие внимания.",
            "analysis_incomplete": i == 1,
        }
        for i, title in enumerate(
            [
                "Обзор обсуждений: запуск новой линейки",
                "Изменения в предложениях конкурентов",
                "Обратная связь по доставке",
            ]
        )
    ],
    "recent_tasks": [
        {
            "id": 1,
            "name": "Ежедневный обзор конкурентов",
            "next_run_at": "завтра, 09:00",
            "last_status": None,
            "tenant": {"name": "Мониторинг рынка"},
        },
        {
            "id": 2,
            "name": "Сбор упоминаний бренда",
            "next_run_at": "сегодня, 18:00",
            "last_status": "failed",
            "tenant": {"name": "Мониторинг рынка"},
        },
        {
            "id": 3,
            "name": "Разовый анализ кампании",
            "next_run_at": None,
            "last_status": None,
            "tenant": {"name": "Мониторинг рынка"},
        },
    ],
    "effective_active": {1, 2},
    "_tz": "Europe/Moscow",
    "request": SimpleNamespace(url=SimpleNamespace(path="/app/", query="")),
}
states = {
    "owner": {},
    "empty": {
        "recent_results": [],
        "recent_tasks": [],
        "next_step": {"label": "Подключите первый источник", "href": "/app/sources"},
    },
    "denied": {
        "offer": {k: False for k in base["offer"]},
        "recent_results": [],
        "recent_tasks": [],
        "nav": [base["nav"][0], *base["nav"][-2:]],
        "mobile_nav": [base["mobile_nav"][0]],
    },
    "long": {
        "tenant": {"id": 7, "name": "ОченьДлинноеНазваниеРабочегоПространстваБезПробелов" * 5},
        "is_superuser": True,
        "user": {"username": "operator", "is_superuser": True},
        "tenants": [{"id": 7, "name": "НазваниеПространстваБезПробелов" * 9}],
        "filter_tenant_id": 7,
        "connection_alerts": [{"detail": "Тестовое подключение требует обновления", "label": "VK"}],
        "flashes": [{"kind": "error", "text": "Проверочный текст ошибки: соединение требует обновления."}],
        "recent_results": [
            {
                "id": 1,
                "source_name": "ИсточникБезПробелов" * 12,
                "created_at": "сегодня, 10:30",
                "title": "ЗаголовокБезПробелов" * 14,
                "summary": "ДлинноеСодержимоеБезПробелов" * 14,
                "analysis_incomplete": True,
            }
        ],
        "recent_tasks": [
            {
                "id": 1,
                "name": "НазваниеЗадачиБезПробелов" * 12,
                "next_run_at": None,
                "last_status": "failed",
                "tenant": {"name": "ПространствоБезПробелов" * 9},
            }
        ],
    },
}
for state, extra in states.items():
    c = {**base, **extra}
    html = env.get_template("web/dashboard.html").render(**c)
    for external, local in [
        ("https://cdn.tailwindcss.com", "tailwind.js"),
        ("https://unpkg.com/htmx.org@1.9.12", "htmx.js"),
        ("https://cdn.jsdelivr.net/npm/alpinejs@3.14.1/dist/cdn.min.js", "alpine.js"),
    ]:
        html = html.replace(external, "http://fixture.test/static/vendor/" + local)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{state}.html").write_text(html)
print("Rendered four fixture states with the real base/macros/partials; no app imports or DB.")
