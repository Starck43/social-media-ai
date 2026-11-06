"""D14 outbox entrypoints: UPDATE drafts/cancellation; explicit CONFIGURE approval."""

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from app.api.deps import require_model_perm
from app.api.v1.conventions import (
    BusinessRoute,
    require_business_scope,
    require_idempotency_key,
    validate_business_routes,
)
from app.schemas.business import BusinessPage
from app.services.communications.outbox import CommunicationError
from app.services.communications.outbox_contracts import DraftIn, MessageOut, ScheduleIn
from app.services.communications.outbox_store import OutboxConflict, OutboxUnavailable, outbox_store
from app.types import ActionType

router = APIRouter(
    prefix="/communication/outbox",
    tags=["Communications"],
    route_class=BusinessRoute,
    dependencies=[Depends(require_business_scope), Depends(require_model_perm("comms.outbox", ActionType.VIEW))],
)


async def invoke(operation):
    try:
        return await operation
    except OutboxUnavailable:
        raise HTTPException(404) from None
    except (OutboxConflict, CommunicationError):
        raise HTTPException(409) from None
    except PermissionError:
        raise HTTPException(403) from None
    except ValueError:
        raise HTTPException(422) from None


@router.post(
    "/draft",
    response_model=MessageOut,
    status_code=201,
    openapi_extra={"x-creates-command": True},
    dependencies=[Depends(require_model_perm("comms.outbox", ActionType.UPDATE))],
)
async def create_outbox_draft(body: DraftIn, idempotency_key: str = Depends(require_idempotency_key)):
    return await invoke(outbox_store.draft(body, idempotency_key))


@router.post(
    "/schedule",
    response_model=MessageOut,
    dependencies=[Depends(require_model_perm("comms.outbox", ActionType.CONFIGURE))],
)
async def schedule_outbox(body: ScheduleIn):
    return await invoke(outbox_store.schedule(body))


@router.get("/scheduled", response_model=BusinessPage[MessageOut])
async def list_scheduled_outbox(limit: int = Query(50, ge=1, le=100), before_id: int | None = Query(None, gt=0)):
    return await invoke(outbox_store.scheduled(limit=limit, before_id=before_id))


@router.delete(
    "/scheduled/{message_id}",
    response_model=MessageOut,
    dependencies=[Depends(require_model_perm("comms.outbox", ActionType.UPDATE))],
)
async def cancel_scheduled_outbox(message_id: int = Path(gt=0)):
    return await invoke(outbox_store.cancel(message_id))


@router.get("/{message_id}", response_model=MessageOut)
async def get_outbox_message(message_id: int = Path(gt=0)):
    return await invoke(outbox_store.get(message_id))


validate_business_routes(router)
