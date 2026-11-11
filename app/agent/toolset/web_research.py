"""Chat tools for web research (search -> fetch -> evidence-linked summary) and public page reading (P20-T12)."""

from __future__ import annotations

from typing import Any

from app.agent.tools import tool
from app.services.web_research.contracts import WebResearchRefusal
from app.services.web_research.service import web_research_service


@tool(
    name="web_research",
    description=(
        "Веб-ресёрч («найди в интернете»): поиск и чтение публичных страниц со сводкой и ссылками на источники. "
        "Если Yandex Search API не настроен, под капотом по умолчанию используется изолированный Chromium. "
        "Тестовые результаты (stub O-5) явно помечаются и никогда не выдаются за живые доказательства."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Поисковый запрос или тема исследования"},
            "preset": {
                "type": "string",
                "enum": ["general_search", "site_brief", "fact_check", "task_delegation"],
                "default": "general_search",
                "description": "Сценарий применения: поиск, разбор сайта, проверка фактов для документа или шаг задачи",
            },
            "target_urls": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Необязательный список публичных URL для прямого чтения и проверки",
            },
            "max_results": {"type": "integer", "minimum": 1, "maximum": 5, "default": 3},
        },
        "required": ["query"],
    },
    required_permission="collection.source.view",
)
async def web_research(
    query: str,
    preset: str = "general_search",
    target_urls: list[str] | None = None,
    max_results: int = 3,
) -> dict[str, Any]:
    try:
        result = await web_research_service.research(
            {
                "query": query,
                "preset": preset,
                "target_urls": list(target_urls or []),
                "max_results": max_results,
                "save_evidence": False,
            }
        )
        return {
            "query": result.query,
            "preset": result.preset,
            "provider_used": result.provider_used,
            "default_under_hood": result.default_under_hood,
            "chromium_used_under_hood": result.chromium_used_under_hood,
            "is_stub": result.is_stub,
            "stub_warning": result.stub_warning,
            "summary_text": result.summary_text,
            "citation_coverage": result.citation_coverage,
            "citations": [c.model_dump(mode="json") for c in result.citations],
            "search_hits": [h.model_dump(mode="json") for h in result.search_hits],
            "refusals": result.refusals,
            "recommended_actions": result.recommended_actions,
        }
    except WebResearchRefusal as exc:
        return {"error": exc.user_message, "code": exc.code}
    except ValueError:
        return {"error": "Некорректные параметры запроса веб-исследования.", "code": "invalid_query"}


@tool(
    name="web_fetch_page",
    description=(
        "Прочитать и разобрать публичную веб-страницу («разбери сайт») с проверкой SSRF/DNS rebinding, "
        "правил robots.txt, лимитов размера/времени, нейтрализацией prompt injection и расчётом SHA-256."
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Публичный http/https адрес страницы"},
            "preset": {
                "type": "string",
                "enum": ["site_brief", "fact_check", "general_search"],
                "default": "site_brief",
            },
        },
        "required": ["url"],
    },
    required_permission="collection.source.view",
)
async def web_fetch_page(url: str, preset: str = "site_brief") -> dict[str, Any]:
    try:
        page = await web_research_service.fetch_page({"url": url, "preset": preset, "save_evidence": False})
        return {
            "url": page.url,
            "canonical_url": page.canonical_url,
            "title": page.title,
            "excerpt": page.excerpt,
            "untrusted_body": page.untrusted_body,
            "content_hash": page.content_hash,
            "fetched_at": page.fetched_at.isoformat(),
            "fetch_mode": page.fetch_mode,
            "prompt_injection_detected": page.prompt_injection_detected,
            "injection_markers": page.injection_markers,
            "citation_anchor": page.citation_anchor,
            "is_stub": page.is_stub,
        }
    except WebResearchRefusal as exc:
        return {"error": exc.user_message, "code": exc.code}
    except ValueError:
        return {"error": "Указан некорректный URL страницы.", "code": "invalid_url"}
