"""Read-only actor-scoped social morning brief API."""

from datetime import date
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Depends, HTTPException, Query, Request

from app.api.deps import require_model_perm
from app.api.v1.conventions import business_router, validate_business_routes
from app.services.social.morning_brief import MorningBrief
from app.types import ActionType

router = business_router("social", permission=require_model_perm("source", ActionType.VIEW))


def _validate_window(days: int, start: date | None, end: date | None) -> dict:
    if days < 1 or days > 31:
        raise HTTPException(400, "Invalid report window")
    if start is None and end is None:
        return {}
    if start is None or end is None or start > end or (end - start).days != days - 1:
        raise HTTPException(400, "Invalid report window")
    return {"report_start": start, "report_end": end}


@router.get(
    "/brief",
    response_model=MorningBrief,
    operation_id="get_social_morning_brief",
    dependencies=[Depends(require_model_perm("aianalytics", ActionType.VIEW))],
)
async def get_social_morning_brief(
    request: Request,
    days: Annotated[int, Query(ge=1, le=31)] = 1,
    timezone_name: Annotated[str | None, Query(alias="tz", max_length=64)] = None,
    start: Annotated[date | None, Query()] = None,
    end: Annotated[date | None, Query()] = None,
):
    """Return a deterministic, read-only attention brief for the active actor."""
    actor = getattr(request.state, "api_user", None)
    tenant_id = getattr(request.state, "tenant_id", None)
    if actor is None or type(tenant_id) is not int or tenant_id <= 0:
        raise HTTPException(403, "Workspace and actor required")
    if timezone_name:
        try:
            ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError):
            raise HTTPException(400, "Invalid timezone") from None
    window = _validate_window(days, start, end)
    from app.services.social.morning_brief import preview

    try:
        return await preview(
            tenant_id,
            actor.id,
            days=days,
            **({"timezone_name": timezone_name} if timezone_name else {}),
            **window,
        )
    except PermissionError:
        raise HTTPException(403, "Social brief unavailable") from None
    except ValueError:
        raise HTTPException(400, "Invalid report window or timezone") from None
    except Exception:
        raise HTTPException(503, "Social brief temporarily unavailable") from None


validate_business_routes(router)
