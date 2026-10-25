"""Synthetic full menu in the actual shell; no application startup or DB."""

import asyncio
import re
import sys
from importlib.metadata import distribution
from pathlib import Path
from types import SimpleNamespace as NS

from sqladmin._menu import ViewMenu
from starlette.requests import Request
from test_admin_model_probe import model
from test_admin_shell_catalog import ROOT, console, source

PACKAGE = distribution("sqladmin").locate_file("sqladmin")


async def render(destination):
    destination.mkdir(parents=True, exist_ok=True)
    loaded = source.__wrapped__()
    ctx = console(loaded)
    names = [
        "Пользователи",
        "Платформы",
        "Источники",
        "Собранные данные",
        "Сценарии",
        "Задачи",
        "Действия",
        "Аналитика",
        "Очередь",
        "Уведомления",
        "Провайдеры ИИ",
        "Модели ИИ",
        "Пространства",
        "Учётные данные",
    ]
    for index, name in enumerate(names):
        view = NS(
            is_model=True,
            identity=f"synthetic-{index}",
            name_plural=name,
            is_visible=lambda _: True,
            is_accessible=lambda _: True,
        )
        ctx.admin._menu.add(ViewMenu(view, name, "fa fa-table"))
    request = Request(
        {
            "type": "http",
            "app": ctx.app,
            "router": ctx.app.router,
            "scheme": "https",
            "server": ("fixture.invalid", 443),
            "headers": [],
            "path": "/admin/llm-model/test",
            "root_path": "",
            "query_string": b"",
            "path_params": {},
            "session": {},
        }
    )
    request.state.admin_user = ctx.user
    await ctx.backend.load(request)
    for state in ["initial", "result", "long"]:
        if state == "long":
            ctx.user.username = "long-admin-name-" * 7
        response = await ctx.admin.templates.TemplateResponse(
            request,
            "llm_model/test_results.html",
            {
                "model": model(name="GLM 5.3 Flash (free)" if state != "long" else "VeryLongModelName" * 20),
                "provider": NS(name="Example provider", api_format="openai"),
                "prompt": "",
                "result": (
                    None
                    if state == "initial"
                    else {
                        "ok": True,
                        "tier": "connectivity",
                        "mode": "short",
                        "text": "API доступен",
                        "models_count": 3,
                        "tokens_spent": 0,
                    }
                ),
                "status_checks": [("Ключ настроен", True)],
                "status_hint": "Локальная конфигурация доступна.",
                "probe_prompt": "Да",
                "billing_ready": False,
                "billing_tenants": [],
            },
        )
        html = response.body.decode()
        html = html.replace("https://fixture.invalid/static/", (ROOT / "app/static").as_uri() + "/")
        html = html.replace("https://fixture.invalid/admin/statics/", (PACKAGE / "statics").as_uri() + "/")
        html = re.sub(r'<script\b[^>]*src="https?://[^"]+"[^>]*>\s*</script>', "", html)
        (destination / (state + ".html")).write_text(html)


if __name__ == "__main__":
    asyncio.run(render(Path(sys.argv[1]).resolve()))
