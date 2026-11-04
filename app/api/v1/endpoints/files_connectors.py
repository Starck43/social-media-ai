"""Default-off owner-scoped API and mTLS outbound device WebSocket."""

import asyncio
import logging
import re
import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import Field

from app.api.deps import require_model_perm
from app.api.v1.conventions import BusinessRoute, _error, business_router
from app.core.tenant_context import tenant_scope
from app.schemas.business import BusinessPage, BusinessSchema
from app.services.local_connector.gateway import cloud_chat_references
from app.services.local_connector.runtime import get_gateway
from app.services.local_connector.wire import (
    MAX_CONTROL_BYTES,
    TIMEOUT,
    ConnectorUnavailable,
    FileReference,
    SearchArgs,
    SearchHit,
)
from app.types import ActionType

Root = Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")]
Fingerprint = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
PairingId = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]


class GatewayRoute(BusinessRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def bounded(request):
            total = bytearray()
            async for chunk in request.stream():
                if len(total) + len(chunk) > 65536:
                    return _error(request, 400)
                total.extend(chunk)
            request._body = bytes(total)
            return await handler(request)

        return bounded


def gateway():
    try:
        return get_gateway()
    except ConnectorUnavailable:
        raise HTTPException(503) from None


def group(action):
    result = business_router("files", permission=require_model_perm("localconnector", action))
    result.route_class = GatewayRoute
    result.dependencies.insert(0, Depends(gateway))
    return result


router = APIRouter()
logger = logging.getLogger(__name__)
read = group(ActionType.VIEW)
create = group(ActionType.CREATE)
configure = group(ActionType.CONFIGURE)
remove = group(ActionType.DELETE)
public = APIRouter(prefix="/files", tags=["Files"], route_class=GatewayRoute, dependencies=[Depends(gateway)])
# Bounded in-process network attempt limiter; per-code and per-owner fences are
# additionally durable. No forwarded headers or unbounded IP map.
_attempts = {}


class Begin(BusinessSchema):
    device_name: str = Field(min_length=1, max_length=100, repr=False)
    platform: str = Field(pattern=r"^(windows|macos|linux)$")
    version: str = Field(pattern=r"^[a-zA-Z0-9_.-]{1,40}$")


class Pairing(BusinessSchema):
    pairing_id: PairingId
    secret: str = Field(repr=False)
    expires_in: int


class Offer(BusinessSchema):
    pairing_id: PairingId
    secret: str = Field(min_length=32, max_length=128, repr=False)
    csr_pem: str = Field(min_length=1, max_length=8192, repr=False)


class Offered(BusinessSchema):
    fingerprint: Fingerprint
    device_id: int | None
    certificate_pem: str | None


class Confirm(BusinessSchema):
    fingerprint: Fingerprint
    compared_on_device: bool


class DeviceId(BusinessSchema):
    device_id: int


class Device(BusinessSchema):
    id: int
    device_name: str = Field(repr=False)
    fingerprint: Fingerprint
    platform: str
    version: str
    revoked: bool
    online: bool


class RootConsent(BusinessSchema):
    roots: list[Root] = Field(max_length=16)
    boundary: str = Field(pattern=r"^company$")
    cloud_processing_consent: bool


class Changed(BusinessSchema):
    changed: bool


class Search(BusinessSchema):
    device_ids: list[Annotated[int, Field(gt=0, strict=True)]] = Field(min_length=1, max_length=8)
    args: SearchArgs


class Results(BusinessSchema):
    hits: list[SearchHit]
    partial: bool


class ChatReferences(BusinessSchema):
    references: list[FileReference]
    partial: bool


class SearchReleaseResponse(JSONResponse):
    """Reauthorize and serialize revocation before emitting bounded result bytes."""

    def __init__(self, *, request, gateway, owner_id, roots, dispatch, references_only=False):
        self.request = request
        self.gateway = gateway
        self.owner_id = owner_id
        self.roots = roots
        self.dispatch = dispatch
        if references_only:
            hits = [SearchHit.model_validate(hit) for hit in dispatch.payload["hits"]]
            payload = ChatReferences(
                references=cloud_chat_references(hits), partial=dispatch.payload["partial"]
            ).model_dump(mode="json")
        else:
            payload = Results.model_validate(dispatch.payload).model_dump(mode="json")
        super().__init__(content=payload)
        if len(self.body) > MAX_CONTROL_BYTES:
            raise HTTPException(503)
        self.headers["Cache-Control"] = "no-store"

    async def __call__(self, scope, receive, send):
        # `authorize` commits before any socket send. The process fence prevents
        # this device's revoke/consent route from committing between that check
        # and completion of the bounded ASGI response send, without holding DB
        # locks across network I/O.
        async with self.gateway.channels.release_fence:
            try:
                for device_id, snapshot in self.dispatch.snapshots.items():
                    await self.gateway.store.authorize(
                        owner_id=self.owner_id,
                        device_id=device_id,
                        roots=self.roots,
                        previous=snapshot,
                    )
            except ConnectorUnavailable:
                response = _error(self.request, 404)
                await response(scope, receive, send)
                return
            except Exception as error:
                logger.error("local_connector_release_failure error_type=%s", type(error).__name__)
                response = _error(self.request, 503)
                await response(scope, receive, send)
                return

            async with asyncio.timeout(TIMEOUT):
                await super().__call__(scope, receive, send)


async def invoke(call):
    try:
        return await call
    except ConnectorUnavailable:
        raise HTTPException(404) from None


@create.post("/connectors/pairings", response_model=Pairing)
async def begin(body: Begin, request: Request, gw=Depends(gateway)):
    return await invoke(gw.store.begin(owner_id=request.state.api_user.id, **body.model_dump()))


@public.post("/connectors/register", response_model=Offered)
async def offer(body: Offer, request: Request, gw=Depends(gateway)):
    if request.url.scheme != "https":
        raise HTTPException(403)
    clock = time.monotonic()
    for key, (_, expires) in list(_attempts.items()):
        if expires <= clock:
            _attempts.pop(key, None)
    peer = request.client.host if request.client is not None else "unknown"
    key = gw.authority.verifier("network", peer)
    count, expiry = _attempts.get(key, (0, clock + 60))
    if count >= 20 or (key not in _attempts and len(_attempts) >= 4096):
        raise HTTPException(429)
    _attempts[key] = count + 1, expiry
    return await invoke(gw.store.offer(**body.model_dump()))


@create.post("/connectors/pairings/{pairing_id}/confirm", response_model=DeviceId)
async def confirm(body: Confirm, request: Request, pairing_id: PairingId, gw=Depends(gateway)):
    if body.compared_on_device is not True:
        raise HTTPException(400)
    device = await invoke(
        gw.store.confirm(owner_id=request.state.api_user.id, pairing_id=pairing_id, fingerprint=body.fingerprint)
    )
    return {"device_id": device}


@read.get("/connectors", response_model=BusinessPage[Device])
async def devices(
    request: Request,
    limit: int = Query(50, ge=1, le=100),
    before_id: int | None = Query(None, gt=0),
    gw=Depends(gateway),
):
    rows = await invoke(gw.store.list_owned(owner_id=request.state.api_user.id, limit=limit, before_id=before_id))
    items = [
        Device(
            id=row.id,
            device_name=row.device_name_encrypted,
            fingerprint=row.fingerprint,
            platform=row.platform,
            version=row.version,
            revoked=row.revoked_at is not None,
            online=row.id in gw.channels.sessions,
        )
        for row in rows
    ]
    return BusinessPage[Device](items=items, limit=limit, next_before_id=rows[-1].id if len(rows) == limit else None)


@remove.delete("/connectors/{device_id}", response_model=Changed)
async def revoke(request: Request, device_id: int = Path(gt=0), gw=Depends(gateway)):
    async with gw.channels.release_fence:
        await invoke(gw.store.revoke(owner_id=request.state.api_user.id, device_id=device_id))
        await gw.channels.revoke(device_id)
    return {"changed": True}


@configure.put("/connectors/{device_id}/roots", response_model=Changed)
async def consent(body: RootConsent, request: Request, device_id: int = Path(gt=0), gw=Depends(gateway)):
    if (body.roots and body.cloud_processing_consent is not True) or len(set(body.roots)) != len(body.roots):
        raise HTTPException(400)
    async with gw.channels.release_fence:
        await invoke(gw.store.consent(owner_id=request.state.api_user.id, device_id=device_id, roots=body.roots))
    return {"changed": True}


@read.post("/search", response_model=Results)
async def search(body: Search, request: Request, gw=Depends(gateway)):
    dispatch = await invoke(
        gw.search(owner_id=request.state.api_user.id, **body.model_dump(exclude={"args"}), args=body.args)
    )
    return SearchReleaseResponse(
        request=request,
        gateway=gw,
        owner_id=request.state.api_user.id,
        roots=body.args.roots,
        dispatch=dispatch,
    )


@router.websocket("/files/connectors/session")
async def session(socket: WebSocket):
    gw = None
    identity = None
    channel = None
    try:
        gw = get_gateway()
        fingerprint, serial = gw.authority.peer(socket.scope)
        identity = await gw.store.channel_identity(fingerprint=fingerprint, serial=serial)
        tid, owner, device, epoch = identity
        channel = gw.channels.attach(device, socket, epoch)
        await socket.accept()
        with tenant_scope(tid):
            while True:
                async with asyncio.timeout(60):
                    message = await socket.receive_bytes()
                current = await gw.store.channel_identity(fingerprint=fingerprint, serial=serial)
                if current != identity:
                    raise ConnectorUnavailable()
                gw.channels.receive(device, channel, message)
    except (ConnectorUnavailable, WebSocketDisconnect, TimeoutError, ValueError):
        pass
    finally:
        if gw is not None and identity is not None and channel is not None:
            await gw.channels.detach(identity[2], channel)
        try:
            await socket.close(code=1008)
        except RuntimeError:
            pass


for child in (read, create, configure, remove, public):
    router.include_router(child)
