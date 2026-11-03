"""Private bounded history-only adapter for the default-OFF queued collector.

No login, send, file sessions, exported senders, application RPC retries,
reconnect, flood sleep or raw-client escape. Telethon transport acknowledgements
and protocol retransmission are NOT application-level exactly-once guarantees.
This adapter relies on Telethon's pinned private API and needs final fake-provider
and cancellation/race validation before activation.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timezone

from app.services.social.connector_authorizations import AuthorizationUnavailable
from app.services.social.telegram_grant_access import source_grant_selection
from app.services.social.telegram_runtime_fence import _locked_runtime

_HARD_ITEM_LIMIT = 1000
_DISCONNECT_SECONDS = 5


def _build_client(material, fence):
    """All application submissions, including connect's config RPC, use send gate.

    Keep library imports lazy. Do not use the legacy vault/session builder. Only
    an already-present in-memory auth key is accepted: this is not onboarding.
    """
    from telethon import TelegramClient, utils
    from telethon.sessions import StringSession
    from telethon.tl import functions

    allowed = (
        functions.users.GetUsersRequest,
        functions.contacts.ResolveUsernameRequest,
        functions.channels.GetChannelsRequest,
        functions.messages.GetChatsRequest,
        functions.messages.GetHistoryRequest,
    )
    silent = logging.Logger("telegram.runtime.private", level=logging.CRITICAL + 1)
    silent.addHandler(logging.NullHandler())
    silent.propagate = False

    class _GatedSender:
        __slots__ = ("_delegate",)

        def __init__(self, delegate):
            self._delegate = delegate

        def __getattr__(self, name):
            return getattr(self._delegate, name)

        def send(self, request, ordered=False):
            # Return a coroutine, NOT create_task: authorization is task-bound.
            return self._send(request, ordered)

        async def _send(self, request, ordered):
            base = request
            # Connect emits layer/init wrappers; ordinary _call can emit only
            # InvokeWithoutUpdates. Never accept batched or arbitrary wrappers.
            if isinstance(base, functions.InvokeWithoutUpdatesRequest):
                base = base.query
            if isinstance(base, functions.InvokeWithLayerRequest):
                if not client._runtime_connecting:
                    raise AuthorizationUnavailable()
                base = base.query
            if isinstance(base, functions.InitConnectionRequest):
                if not client._runtime_connecting:
                    raise AuthorizationUnavailable()
                base = base.query
            if isinstance(base, functions.help.GetConfigRequest):
                if not client._runtime_connecting:
                    raise AuthorizationUnavailable()
            elif not isinstance(base, allowed):
                raise AuthorizationUnavailable()
            async with fence._request():
                return await self._delegate.send(request, ordered=ordered)

    class _ReadOnlyClient(TelegramClient):
        async def _call(self, sender, request, ordered=False, flood_sleep_threshold=None):
            # Deliberately don't call Telethon._call: even request_retries=0 can
            # sleep on server errors, and its migration path switches DC. Resolve
            # may issue nested RPCs; those go through this same allowlist/gate.
            if sender is not self._sender or not isinstance(request, allowed):
                raise AuthorizationUnavailable()
            await request.resolve(self, utils)
            result = await sender.send(functions.InvokeWithoutUpdatesRequest(request), ordered=ordered)
            await utils.maybe_async(self.session.process_entities(result))
            return result

        async def _on_login(self, user):
            # connect() normally initializes updates with an unrelated GetState
            # RPC. No update receiver/catch-up/keepalive work is allowed here.
            self._authorized = True
            self._mb_entity_cache.set_self_user(user.id, user.bot, user.access_hash)
            return user

        async def _update_loop(self):
            return

        async def _keepalive_loop(self):
            return

        async def _switch_dc(self, new_dc):
            raise AuthorizationUnavailable()

        async def _borrow_exported_sender(self, dc_id):
            raise AuthorizationUnavailable()

    session = StringSession(material.session)
    if session.auth_key is None or not session.auth_key.key:
        raise AuthorizationUnavailable()
    client = _ReadOnlyClient(
        session,
        material.api_id,
        material.api_hash,
        request_retries=0,
        connection_retries=0,
        retry_delay=0,
        auto_reconnect=False,
        timeout=10,
        flood_sleep_threshold=0,
        receive_updates=False,
        catch_up=False,
        base_logger=silent,
    )
    client._runtime_connecting = False
    client._sender = _GatedSender(client._sender)
    return client


async def _disconnect(client):
    """No shielded orphan task: close this private client in the owning task."""
    try:
        async with asyncio.timeout(_DISCONNECT_SECONDS):
            # Public disconnect() spawns a shielded task. The private coroutine
            # keeps cleanup awaited/bounded; it sends no application RPC.
            await client._disconnect_coro()
    except asyncio.CancelledError:
        raise
    except Exception:
        raise AuthorizationUnavailable() from None


def _date_bound(value):
    """Bounded date parser without logging invalid values or widening a window."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time())
    elif type(value) is str and len(value) <= 64:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise AuthorizationUnavailable() from None
    else:
        raise AuthorizationUnavailable()
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


async def _history(client, fence, source, limit):
    """Only the pinned target, oldest-unread first; no search or extra lanes.

    A max-ID cursor MUST NOT acknowledge a newest-first capped slice: that
    would permanently skip unread messages between the old cursor and the cap.
    """
    from app.services.monitoring.staging import cursor_value
    from app.services.social.tg_client import TelegramClient as Normalizer

    if source_grant_selection(source) != fence._pin.selection:
        raise AuthorizationUnavailable()
    await fence._inspect()
    me = await client.get_me()
    if (
        me is None
        or type(me.id) is not int
        or me.id <= 0
        or getattr(me, "bot", False)
        or fence._subject_key != str(me.id)
    ):
        raise AuthorizationUnavailable()
    # _resolve_entity logs provider exception details, so do NOT use it here.
    target = await client.get_entity(Normalizer._entity_ref(fence._pin.selection.external_id))
    if target is None:
        raise AuthorizationUnavailable()
    normalizer = Normalizer(None)
    params = source.params or {}
    watermark = cursor_value(params.get("telegram_l2_last_item_id", getattr(source, "last_item_id", None)))
    if params.get("force_refresh") is True:
        watermark = 0
    dates = params.get("cli_dates") or {}
    if type(dates) is not dict:
        raise AuthorizationUnavailable()
    start = _date_bound(dates.get("start_date") or getattr(source, "date_from", None)) if not watermark else None
    end = _date_bound(dates.get("end_date"))
    if start is not None and end is not None and start > end:
        raise AuthorizationUnavailable()
    items = []
    async for message in client.iter_messages(
        target, limit=limit, min_id=watermark, offset_date=start, reverse=True, wait_time=0
    ):
        # Cached pages also get a fresh check before retaining each item; actual
        # paginated RPCs are independently gated at sender.send.
        await fence._inspect()
        published = _date_bound(getattr(message, "date", None))
        if published is None:
            raise AuthorizationUnavailable()
        if end is not None and published > end:
            break
        if start is not None and published < start:
            continue
        item = normalizer._normalize_message(message, source)
        if item is not None:
            items.append(item)
    await fence._inspect()
    return items


class _TelegramReadBatch:
    __slots__ = ("_fence", "_items", "_active", "_admitted")

    def __init__(self, fence, items):
        self._fence = fence
        self._items = items
        self._active = True
        self._admitted = False

    def __repr__(self):
        return "<TelegramReadBatch>"

    def __reduce_ex__(self, protocol):
        raise TypeError("telegram_batch_not_serializable")

    def _close(self):
        self._active = False
        self._items.clear()

    @asynccontextmanager
    async def _admission(self):
        """Trusted staging code must write ONLY in the yielded transaction.

        No model/provider/send work inside this scope. A retained Python copy is
        not revocable and must never be treated as a permit by callers.
        """
        self._fence._require_owner()
        if not self._active or self._admitted:
            raise AuthorizationUnavailable()
        # One attempt only, including commit-unknown failures.
        self._admitted = True
        async with self._fence._output_transaction() as session:
            yield session, tuple(self._items)


@asynccontextmanager
async def _telegram_history_batch(selection, claim, source, *, limit):
    """Private entry point; the collector requires its default-OFF rollout gate."""
    batch = None
    items = []
    try:
        if type(limit) is not int or not 0 < limit <= _HARD_ITEM_LIMIT:
            raise AuthorizationUnavailable()
        async with _locked_runtime(selection, claim) as fence:
            async with fence._guard._setup_material() as material:
                client = _build_client(material, fence)
                try:
                    await fence._inspect()
                    client._runtime_connecting = True
                    async with asyncio.timeout(20):
                        await client.connect()
                    client._runtime_connecting = False
                    await fence._inspect()
                    items = await _history(client, fence, source, limit)
                finally:
                    client._runtime_connecting = False
                    await _disconnect(client)
            await fence._inspect()
            batch = _TelegramReadBatch(fence, items)
            try:
                yield batch
            finally:
                batch._close()
    except asyncio.CancelledError:
        raise
    except Exception:
        raise AuthorizationUnavailable() from None
    finally:
        items.clear()
