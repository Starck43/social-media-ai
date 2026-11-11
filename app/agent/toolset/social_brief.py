"""Read-only social morning brief tool; recommendations link to owning evidence."""

from datetime import date
from urllib.parse import urlencode

from app.agent.tools import tool


@tool(
    name="social_morning_brief",
    description=(
        "Локальная сводка социальных результатов, только ссылка без свидетельств. "
        "Проверьте рекомендации в веб-интерфейсе."
    ),
    required_permission="analysis.result.view",
    parameters={
        "type": "object",
        "properties": {
            "days": {"type": "integer", "minimum": 1, "maximum": 31, "default": 1},
            "timezone": {"type": "string", "maxLength": 64},
            "start": {"type": "string", "format": "date"},
            "end": {"type": "string", "format": "date"},
        },
        "required": [],
        "additionalProperties": False,
    },
)
async def social_morning_brief(
    days: int = 1,
    timezone: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> dict:
    from app.core.permissions import get_current_user
    from app.core.tenant_context import current_tenant_id
    from app.services.social.morning_brief import preview

    actor_id = getattr(get_current_user(), "id", None)
    tenant_id = current_tenant_id()
    if type(actor_id) is not int or type(tenant_id) is not int or tenant_id <= 0:
        return {
            "error": "Активный участник workspace не найден.",
            "code": "social_brief_unavailable",
        }
    try:
        report_start = date.fromisoformat(start) if start is not None else None
        report_end = date.fromisoformat(end) if end is not None else None
        await preview(
            tenant_id,
            actor_id,
            days=days,
            **({"timezone_name": timezone} if timezone else {}),
            **(
                {"report_start": report_start, "report_end": report_end} if start is not None or end is not None else {}
            ),
        )
    except PermissionError:
        return {
            "error": "Нет доступа к источникам или результатам анализа.",
            "code": "social_brief_forbidden",
        }
    except ValueError:
        return {
            "error": "Некорректное окно отчета или часовой пояс.",
            "code": "social_brief_invalid_window",
        }
    query = {"days": days}
    if timezone:
        query["tz"] = timezone
    if start and end:
        query.update(start=start, end=end)
    return {
        "web_href": f"/app/digests/morning?{urlencode(query)}",
        "writes": False,
        "data_boundary": "local_only",
        "model_status": "disabled_deterministic_only",
        "notice": ("Откройте веб-сводку; локальные свидетельства " "не передаются в чат."),
    }
