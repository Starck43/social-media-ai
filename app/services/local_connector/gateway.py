"""Bounded fan-out with fresh ownership/consent checks before dispatch and release."""

import asyncio
from dataclasses import dataclass

from .channel import ConnectorChannels
from .wire import ConnectorUnavailable, SearchArgs, canonical, new_request, sign


@dataclass(frozen=True)
class SearchDispatch:
    """Bounded result payload plus snapshots to reauthorize at HTTP release."""

    payload: dict
    snapshots: dict[int, tuple]


class ConnectorGateway:
    def __init__(self, store, authority, channels=None):
        self.store = store
        self.authority = authority
        self.channels = channels if channels is not None else ConnectorChannels()

    async def _search_device(self, owner_id, device_id, args):
        snapshot = await self.store.authorize(owner_id=owner_id, device_id=device_id, roots=args.roots)
        tid, owner, device, epoch, consent, policy = snapshot
        request = new_request(
            key_id=self.authority.key_id,
            tenant_id=tid,
            owner_id=owner,
            device_id=device,
            device_epoch=epoch,
            consent_epoch=consent,
            policy_epoch=policy,
            args=args,
        )
        payload, digest = sign(request, self.authority.signer)
        await self.store.reserve(request, digest)
        response = await self.channels.search(device_id, request, payload, digest)
        if response.outcome != "succeeded":
            raise ConnectorUnavailable()
        await self.store.finish(request, digest)
        return response, snapshot

    async def search(self, *, owner_id, device_ids, args: SearchArgs):
        args.checked()
        if not 1 <= len(device_ids) <= 8 or len(set(device_ids)) != len(device_ids):
            raise ConnectorUnavailable()
        # Check EVERY requested device before any query leaves the API process.
        for device in device_ids:
            await self.store.authorize(owner_id=owner_id, device_id=device, roots=args.roots)
        responses = await asyncio.gather(
            *(self._search_device(owner_id, device, args) for device in device_ids), return_exceptions=True
        )
        hits = []
        snapshots = {}
        partial = False
        for device, result in zip(device_ids, responses):
            response = result
            if isinstance(response, BaseException):
                partial = True
            else:
                response, snapshot = result
                snapshots[device] = snapshot
                partial |= response.partial
                hits.extend(response.hits)
        hits.sort(key=lambda hit: (-hit.rank, hit.reference.root_id, hit.reference.object_id))
        partial |= len(hits) > args.limit
        hits = hits[: args.limit]
        if sum(hit.snippet is not None for hit in hits) > 8:
            raise ConnectorUnavailable()
        # Recheck every selected registration after fan-out, not only the first
        # response (revocation/policy may have committed while others were slow).
        for device in device_ids:
            await self.store.authorize(
                owner_id=owner_id, device_id=device, roots=args.roots, previous=snapshots.get(device)
            )
        result = {"hits": [hit.model_dump() for hit in hits], "partial": partial}
        canonical(result)  # aggregate response has the same 64-KiB ceiling
        return SearchDispatch(payload=result, snapshots=snapshots)


def cloud_chat_references(hits):
    """Company observations only; never auto-project snippets into a cloud model.

    Local-only references are also data and are not a permissible fallback.
    """
    if any(hit.data_boundary != "company" for hit in hits):
        raise ConnectorUnavailable()
    return [hit.reference.model_dump() for hit in hits]
