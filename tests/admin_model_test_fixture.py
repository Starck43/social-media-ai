"""Render the actual SQLAdmin template with inert context; no app/DB imports."""

import json
import re
import sys
from importlib.metadata import distribution
from pathlib import Path
from types import SimpleNamespace as NS

from jinja2 import ChoiceLoader, Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = distribution("sqladmin").locate_file("sqladmin")


def offline_html(html):
    # The inherited Alpine CDN is not used by these components; no fixture network.
    return re.sub(r'<script\b[^>]*src="https?://[^"]+"[^>]*>\s*</script>', "", html)


def render(destination):
    destination.mkdir(parents=True, exist_ok=True)
    env = Environment(
        loader=ChoiceLoader([FileSystemLoader(ROOT / "app/templates"), FileSystemLoader(PACKAGE / "templates")]),
        autoescape=select_autoescape(["html", "xml"]),
    )

    def url(name, **kw):
        if name == "static":
            return (ROOT / "app/static" / kw["path"]).as_uri()
        if name == "admin:statics":
            return (PACKAGE / "statics" / kw["path"]).as_uri()
        return "/admin/" + kw.get("identity", "") + "/" + name.split(":")[-1]

    env.globals.update(url_for=url, csrf_token=lambda: "fake-csrf")
    model = NS(
        id=7,
        name="Assistant model",
        model_id="provider/text-model",
        model_type="text,image",
        max_tokens=1024,
        default_temperature=0.3,
        is_active=True,
        input_cost_per_1k=0.0000001,
        output_cost_per_1k=0.002,
    )
    provider = NS(name="Example provider", api_format="openai")
    admin = NS(title="ИИ Ассистент", logo_url=None, favicon_url=None, _menu=NS(items=[]), authentication_backend=True)
    request = NS(session={}, url_for=url)
    checks = [
        ("API-ключ настроен", True),
        ("Модель активна", True),
        ("Провайдер активен", True),
        ("Нет недавних ошибок", True),
    ]
    states = {
        "initial": None,
        "short": dict(
            ok=True,
            tier="connectivity",
            mode="short",
            text="API доступен: список моделей получен.",
            method="GET /models",
            http_status=200,
            model_available=True,
            models_count=3,
            elapsed_ms=42,
            tokens_spent=0,
        ),
        "mock": dict(
            ok=True,
            tier="mock",
            text="Локальная имитация",
            content="Тестовый ответ: <script>не выполнять</script>",
            tokens_spent=0,
            usage={"prompt_tokens": 8, "completion_tokens": 12},
        ),
        "real": dict(
            ok=True,
            tier="real",
            text="Получен ответ на ваш запрос.",
            request_prompt="Скажи привет",
            max_tokens=512,
            elapsed_ms=350,
            content="Здравствуйте!\n\nЭто настоящий текстовый ответ модели.",
            dispatch_state="response_received",
            stage="complete",
            http_status=200,
            accounting_status="confirmed",
            finish_reason="stop",
            response_json={
                "id": "fixture-reply",
                "model": "fixture-model",
                "choices": [{"message": {"content": "Привет <script>bad()</script>"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 8, "completion_tokens": 4},
            },
            tokens_spent=12,
            estimated_cost="0.000008",
        ),
        "unknown": dict(
            ok=True,
            tier="real",
            text="Ответ получен; usage не сообщён.",
            content="да",
            tokens_spent=None,
            save_warning="Оценка стоимости не сохранена. Не повторяйте автоматически.",
        ),
        "error": dict(
            ok=False,
            tier="real",
            text="Реальный ответ не подтверждён. Проверьте подключение, ключ и лимиты.",
            tokens_spent=None,
            stage="transport",
            dispatch_state="possible",
            error_code="timeout",
            http_status=None,
        ),
        "blocked": dict(
            ok=False,
            tier="real",
            text="API-ключ не настроен. Запрос не отправлен.",
            stage="preflight",
            dispatch_state="not_dispatched",
            error_code="missing_key",
            tokens_spent=0,
        ),
        "empty": dict(
            ok=False,
            tier="real",
            text="Ответ API получен без текста сообщения.",
            stage="complete",
            dispatch_state="response_received",
            http_status=200,
            accounting_status="confirmed",
            finish_reason="length",
            response_json={
                "choices": [
                    {
                        "message": {"content": None, "reasoning_content": "Служебное рассуждение"},
                        "finish_reason": "length",
                    }
                ]
            },
        ),
    }
    states["empty"].update(provider_cost_usd="0", provider_cost_state="resolved", tokens_spent=478)
    states["http-error"] = {
        **states["real"],
        "ok": False,
        "http_status": 429,
        "error_code": "provider_http",
        "stage": "response",
        "content": "",
        "finish_reason": None,
    }
    states["filtered"] = {**states["empty"], "finish_reason": "content_filter", "error_code": "empty_content"}
    states["owner"] = {**states["real"], "accounting_status": "standard"}
    for state, result in states.items():
        if result and "response_json" in result:
            result["response_json_text"] = json.dumps(result["response_json"], ensure_ascii=False, indent=2)
        html = env.get_template("llm_model/test_results.html").render(
            model=model,
            provider=provider,
            admin=admin,
            request=request,
            prompt="",
            result=result,
            status_checks=checks,
            status_hint="Локальные настройки доступны.",
            probe_prompt="Ответь одним словом: да",
            billing_ready=state != "owner",
            billing_tenants=[NS(id=3, name="Test workspace")],
            billing_tenant_id=3,
        )
        (destination / (state + ".html")).write_text(offline_html(html))
    model.name = "VeryLongModelName" * 16
    provider.name = "LongProviderName" * 14
    model.model_id = "model-id-without-breaks" * 18
    model.is_active = False
    (destination / "long.html").write_text(
        offline_html(
            env.get_template("llm_model/test_results.html").render(
                model=model,
                provider=provider,
                admin=admin,
                request=request,
                prompt="<script>escape me</script>",
                result=None,
                status_checks=[(label, False) for label, _ in checks],
                status_hint="Проверьте настройки.",
                probe_prompt="да",
                billing_ready=True,
                billing_tenants=[NS(id=3, name="Test workspace")],
                billing_tenant_id=3,
            )
        )
    )
    print(json.dumps({"states": [*states, "long"], "directory": str(destination)}))


if __name__ == "__main__":
    render(Path(sys.argv[1]).resolve())
