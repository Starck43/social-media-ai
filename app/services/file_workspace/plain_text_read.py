"""Mock-only, in-memory authorize/read/extract/recheck orchestration.

This module deliberately imports no database, concrete authority or provider.
Real activation requires separate P8-T34/P12-T13-reviewed implementations.
"""

from __future__ import annotations

import asyncio
import unicodedata
from uuid import uuid4

from app.integrations.file_workspace.content_read import (
    MAX_READ_CHUNKS,
    READ_TIMEOUT,
    ContentReadAuthority,
    ContentReadContext,
    ContentReader,
    ContentReadRequest,
    ContentReadStream,
    PlainTextReadUnavailable,
    ReadPlainText,
)
from app.integrations.file_workspace.plain_text import MAX_INPUT_BYTES, extract_plain_text


def _opaque(value: object, limit: int) -> bool:
    return (
        type(value) is str
        and 0 < len(value) <= limit
        and not any(unicodedata.category(char).startswith("C") for char in value)
    )


def _context_valid(context: object, request: ContentReadRequest) -> bool:
    return (
        type(context) is ContentReadContext
        and type(context.request) is ContentReadRequest
        and context.request == request
        and _opaque(context.path, 2048)
        and len(context.path.encode("utf-8")) <= 2048
        and all(_opaque(key, 512) for key in (context.root_key, context.policy_key, context.credential_key))
        and type(context.observation_number) is int
        and context.observation_number > 0
        and type(context.processing_boundary) is str
        and context.processing_boundary == "cloud"
        and _opaque(context.processing_policy_key, 512)
    )


def _live(deadline: float) -> None:
    # Explicit checks also reject adapters that swallow cancellation or perform
    # synchronous work across a cooperative timeout. This is not preemption.
    task = asyncio.current_task()
    if task is not None and task.cancelling():
        raise asyncio.CancelledError()
    if asyncio.get_running_loop().time() >= deadline:
        raise PlainTextReadUnavailable()


async def _read(request, authority, reader, deadline) -> ReadPlainText:
    context = await authority.authorize(request)
    _live(deadline)
    if not _context_valid(context, request):
        raise PlainTextReadUnavailable()
    # Reject unsupported/unsafe display names BEFORE asking for any file bytes.
    extract_plain_text(b"", name=context.name)
    async with reader.open(context, max_bytes=MAX_INPUT_BYTES) as stream:
        _live(deadline)
        if (
            type(stream) is not ContentReadStream
            or not _context_valid(stream.context, request)
            or stream.context != context
            or stream.complete is not True
            or type(stream.expected_bytes) is not int
            or not 0 <= stream.expected_bytes <= MAX_INPUT_BYTES
            or (stream.provider_version is not None and not _opaque(stream.provider_version, 512))
        ):
            raise PlainTextReadUnavailable()
        payload = bytearray()
        chunks = 0
        async for chunk in stream.chunks:
            _live(deadline)
            chunks += 1
            if type(chunk) is not bytes or chunks > MAX_READ_CHUNKS:
                raise PlainTextReadUnavailable()
            if len(chunk) > stream.expected_bytes - len(payload):
                raise PlainTextReadUnavailable()
            payload.extend(chunk)
        if len(payload) != stream.expected_bytes:
            raise PlainTextReadUnavailable()
        version = stream.provider_version
    # Release/close transport BEFORE parsing and fresh authority checks.
    _live(deadline)
    extraction = extract_plain_text(bytes(payload), name=context.name)
    _live(deadline)
    fresh = await authority.authorize(request)
    _live(deadline)
    if not _context_valid(fresh, request) or fresh != context:
        raise PlainTextReadUnavailable()
    return ReadPlainText(context, extraction, version)


async def read_plain_text(
    *,
    tenant_id: int,
    actor_id: int,
    connection_id: int,
    object_id: int,
    authority: ContentReadAuthority | None = None,
    reader: ContentReader | None = None,
) -> ReadPlainText:
    """No default authority/reader, caller URL/path, writes, retries or cache.

    Both injected boundaries are trusted application code, never request inputs.
    One 5s cooperative budget includes both checks, streaming, close and parsing.
    No result is released on failure; cancellation propagates unchanged. No
    process-memory erasure or malicious-adapter/network safety is promised.
    """
    try:
        if authority is None or reader is None:
            raise PlainTextReadUnavailable()
        if any(type(value) is not int or value <= 0 for value in (tenant_id, actor_id, connection_id, object_id)):
            raise PlainTextReadUnavailable()
        request = ContentReadRequest(tenant_id, actor_id, connection_id, object_id, uuid4().hex)
        deadline = asyncio.get_running_loop().time() + READ_TIMEOUT
        async with asyncio.timeout(READ_TIMEOUT):
            return await _read(request, authority, reader, deadline)
    except Exception:
        pass
    # Outside the except: no chained exception retains provider/decoder details
    # in ordinary formatted tracebacks. Cancellation (BaseException) is not caught.
    raise PlainTextReadUnavailable()
