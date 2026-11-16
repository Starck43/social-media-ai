"""Calendar connections, source reads and separately admitted event commands."""

from fastapi import Depends, HTTPException, Path, Query

from app.api.deps import require_model_perm
from app.api.v1.conventions import business_router, require_idempotency_key, validate_business_routes
from app.integrations.oauth.service import OAuthFlowError
from app.services.calendar.contracts import (
    CalendarError,
    CallbackInput,
    ConnectionID,
    GoogleConnect,
    WritePreview,
    YandexConnect,
    YandexDiscovery,
)
from app.services.calendar.service import calendar_service
from app.services.commands.ledger import CommandError
from app.services.planner.tasks import PlannerConflict, PlannerUnavailable
from app.types import ActionType

router = business_router("calendar", permission=require_model_perm("plannertask", ActionType.VIEW))


async def invoke(operation):
    try:
        return await operation
    except PlannerUnavailable:
        raise HTTPException(404) from None
    except PlannerConflict:
        raise HTTPException(409) from None
    except (PermissionError, CommandError):
        raise HTTPException(403) from None
    except (CalendarError, OAuthFlowError):
        raise HTTPException(503) from None
    except ValueError:
        raise HTTPException(422) from None


@router.post(
    "/connect/google", response_model=dict, dependencies=[Depends(require_model_perm("plannertask", ActionType.CREATE))]
)
async def connect_google(request: GoogleConnect):
    return await invoke(calendar_service.connect_google(request))


@router.post(
    "/connect/google/callback",
    response_model=dict,
    dependencies=[Depends(require_model_perm("plannertask", ActionType.CREATE))],
)
async def google_callback(request: CallbackInput):
    return await invoke(calendar_service.callback_google(request))


@router.get(
    "/connect/google/callback",
    response_model=dict,
    dependencies=[Depends(require_model_perm("plannertask", ActionType.CREATE))],
)
async def google_redirect(
    state: str = Query(min_length=1, max_length=500),
    code: str = Query(min_length=1, max_length=4000),
    connection_id: int | None = Query(None, gt=0),
):
    return await invoke(
        calendar_service.callback_google(CallbackInput(connection_id=connection_id, state=state, code=code))
    )


@router.post(
    "/discover/yandex",
    response_model=dict,
    dependencies=[Depends(require_model_perm("plannertask", ActionType.CREATE))],
)
async def discover_yandex(request: YandexDiscovery):
    return await invoke(calendar_service.discover_yandex(request))


@router.post(
    "/connect/yandex", response_model=dict, dependencies=[Depends(require_model_perm("plannertask", ActionType.CREATE))]
)
async def connect_yandex(request: YandexConnect):
    return await invoke(calendar_service.connect_yandex(request))


@router.get("/connections", response_model=dict)
async def connections():
    return await invoke(calendar_service.connections())


@router.post(
    "/connections/choose",
    response_model=dict,
    dependencies=[Depends(require_model_perm("plannertask", ActionType.UPDATE))],
)
async def choose(request: ConnectionID):
    return await invoke(calendar_service.choose(request.connection_id))


@router.delete(
    "/connections/{connection_id}",
    response_model=dict,
    dependencies=[Depends(require_model_perm("plannertask", ActionType.DELETE))],
)
async def disconnect(connection_id: int = Path(gt=0)):
    return await invoke(calendar_service.disconnect(connection_id))


@router.post("/sync", response_model=dict, dependencies=[Depends(require_model_perm("plannertask", ActionType.UPDATE))])
async def sync(request: ConnectionID):
    return await invoke(calendar_service.sync(request.connection_id))


@router.get("/events", response_model=dict)
async def events(
    connection_id: int | None = Query(None, gt=0),
    limit: int = Query(50, ge=1, le=100),
    before_id: int | None = Query(None, gt=0),
):
    return await invoke(calendar_service.events(connection_id=connection_id, limit=limit, before_id=before_id))


@router.post(
    "/events/preview",
    response_model=dict,
    openapi_extra={"x-creates-command": True},
    dependencies=[Depends(require_model_perm("plannertask", ActionType.UPDATE))],
)
async def preview(request: WritePreview, idempotency_key: str = Depends(require_idempotency_key)):
    return await invoke(calendar_service.preview(request, idempotency_key))


@router.post(
    "/commands/{command_id}/execute",
    response_model=dict,
    dependencies=[Depends(require_model_perm("plannertask", ActionType.UPDATE))],
)
async def execute(command_id: int = Path(gt=0)):
    return await invoke(calendar_service.execute(command_id))


validate_business_routes(router)
