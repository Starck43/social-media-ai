"""Chat tool for natural-language read-only 1C queries (P17-T03).

The model proposes a typed plan; the plan is validated against the connection's own
metadata and allowlist before any request, so text in the question can never widen
the entity or field surface.
"""

from __future__ import annotations

from typing import Any

from app.agent.tools import tool
from app.core.permissions import get_current_user
from app.core.tenant_context import current_tenant_id
from app.models.managers.workspace_connection_manager import ConnectionDenied
from app.services.onec.history import OneCHistoryError, OneCQueryHistoryRepository
from app.services.onec.nl_query import OneCNaturalLanguageError, OneCQueryAnswerService
from app.services.onec.query import OneCQueryError

CHAT_ROW_LIMIT = 50


@tool(
    name="onec_query",
    description=(
        "Вопрос к 1С в режиме только чтения («покажи остатки товара X», «какие счета контрагента Y»): "
        "вопрос переводится в типизированный план, план проверяется по метаданным подключения и allowlist, "
        "затем выполняется один ограниченный OData GET. Произвольный текст запроса не исполняется, "
        "изменение данных и обход allowlist невозможны. Если вопрос неоднозначен, инструмент возвращает "
        "уточняющий вопрос вместо догадки."
    ),
    parameters={
        "type": "object",
        "properties": {
            "connection_id": {"type": "integer", "minimum": 1, "description": "Идентификатор подключения 1С"},
            "question": {"type": "string", "description": "Вопрос пользователя о данных 1С"},
            "cache_ttl_seconds": {"type": "integer", "minimum": 0, "maximum": 300, "default": 0},
        },
        "required": ["connection_id", "question"],
    },
    required_permission="collection.source.view",
)
async def onec_query(connection_id: int, question: str, cache_ttl_seconds: int = 0) -> dict[str, Any]:
    user = get_current_user()
    tenant_id = current_tenant_id()
    if user is None or tenant_id is None:
        return {"error": "Нужна авторизация в рабочем пространстве.", "code": "unauthorized"}
    try:
        answer = await OneCQueryAnswerService().answer(
            tenant_id=tenant_id,
            actor_id=user.id,
            connection_id=connection_id,
            question=question,
            history=OneCQueryHistoryRepository(),
            cache_ttl_seconds=cache_ttl_seconds,
        )
    except ConnectionDenied:
        return {"error": "Подключение недоступно или не проверено.", "code": "connection_unavailable"}
    except OneCNaturalLanguageError as exc:
        return {"error": "Не удалось построить допустимый план запроса.", "code": exc.code}
    except OneCQueryError as exc:
        return {"error": "1С не отдала данные по этому запросу.", "code": exc.code}
    except OneCHistoryError as exc:
        return {"error": "Не удалось записать историю запроса.", "code": exc.code}

    if answer.clarification is not None:
        return {"needs_clarification": True, "clarification": answer.clarification}

    table = answer.table
    assert table is not None
    return {
        "needs_clarification": False,
        "interpretation": answer.interpretation.description if answer.interpretation else "",
        "plan_fingerprint": answer.interpretation.validated.plan_fingerprint if answer.interpretation else "",
        "entity_set": table.title,
        "summary": table.summary,
        "columns": list(table.columns),
        "rows": [dict(zip(table.columns, row)) for row in table.rows[:CHAT_ROW_LIMIT]],
        "row_count": len(table.rows),
        "truncated": len(table.rows) > CHAT_ROW_LIMIT or table.truncated,
        "next_page_token": answer.result.next_page_token if answer.result else None,
        "history_id": answer.history_id,
        "chart": [{"label": label, "value": value} for label, value in table.chart],
    }


__all__ = ["onec_query"]
