"""API-process liveness and bounded DB readiness; no worker health claim."""

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.models import Permission

router = APIRouter(tags=["Health"])
DB_PROBE_TIMEOUT_SECONDS = 2.0


def _response(status_code: int, **fields) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={**fields, "timestamp": datetime.now(timezone.utc).isoformat()},
        headers={"Cache-Control": "no-store"},
    )


@router.get("/livez")
async def liveness() -> JSONResponse:
    """The HTTP process can respond; never query a dependency here."""
    return _response(200, status="ok")


@router.get("/health")
@router.get("/readyz")
async def readiness() -> JSONResponse:
    """Ready only if a real global-model read succeeds before the timeout.

    /health is the compatible path, with corrected failure status semantics.
    No raw exception, DSN, schema, customer content or counts are exposed.
    Cancellation propagates; asyncio timeout assumes cooperative async drivers.
    """
    try:
        await asyncio.wait_for(Permission.objects.count(), timeout=DB_PROBE_TIMEOUT_SECONDS)
    except Exception:
        return _response(503, status="error", database="disconnected")
    return _response(200, status="ok", database="connected")
