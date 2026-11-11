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
        "По решению владельца O-5 без явно активированного живого провайдера (Yandex Search API или браузера P20-T05) "
        "поиск возвращает явно помеченную заглушку (stub O-5), которая никогда не выдаётся за живые доказательства, "
        "а чтение указанных публичных URL выполняется по прямому HTTP с привязкой проверенного IP."
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
    *,
    session: Any | None = None,
    require_durable: bool = False,
) -> dict[str, Any]:
    try:
        result = await web_research_service.research(
            {
                "query": query,
                "preset": preset,
                "target_urls": list(target_urls or []),
                "max_results": max_results,
                "save_evidence": False,
            },
            session=session,
            require_durable=require_durable,
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
        "Прочитать и разобрать публичную веб-страницу («разбери сайт») по прямому HTTP с привязкой проверенного IP, "
        "проверкой SSRF/DNS rebinding, правил robots.txt, лимитов размера/времени, эвристической фильтрацией "
        "prompt injection и расчётом SHA-256 (внешний текст всегда остаётся недоверенным)."
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
async def web_fetch_page(
    url: str,
    preset: str = "site_brief",
    *,
    session: Any | None = None,
    require_durable: bool = False,
) -> dict[str, Any]:
    try:
        page = await web_research_service.fetch_page(
            {"url": url, "preset": preset, "save_evidence": False},
            session=session,
            require_durable=require_durable,
        )
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
