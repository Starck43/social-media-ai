"""Synthetic content-read doubles, without settings, DB or provider imports."""

from contextlib import asynccontextmanager
from dataclasses import replace

from app.integrations.file_workspace.content_read import (
    ContentReadContext,
    ContentReadRequest,
    ContentReadStream,
    ReadPlainText,
)
from app.integrations.file_workspace.plain_text import extract_plain_text


class SyntheticContentAuthority:
    def __init__(self, events=None):
        self.events = events if events is not None else []
        self.requests = []
        self.initial_updates = {}
        self.fresh_updates = {}
        self.deny_at = None
        self.hook = None

    async def authorize(self, request):
        self.requests.append(request)
        self.events.append("authorize")
        if self.hook:
            await self.hook()
        if len(self.requests) == self.deny_at:
            raise PermissionError("private-account-token")
        context = ContentReadContext(
            request,
            "disk:/private/file.txt",
            "file.txt",
            "root-key",
            "policy-key",
            "credential-key",
            1,
            "cloud",
            "processing-policy-key",
        )
        context = replace(context, **self.initial_updates)
        return replace(context, **self.fresh_updates) if len(self.requests) > 1 else context


class SyntheticContentReader:
    def __init__(self, events=None, chunks=(b"hello\r\n",)):
        self.events = events if events is not None else []
        self.chunks = chunks
        self.stream_updates = {}
        self.contexts = []
        self.limits = []
        self.consumed = 0
        self.closed = False
        self.before_yield = None
        self.on_chunk = None
        self.on_close = None

    async def _chunks(self):
        for chunk in self.chunks:
            if self.on_chunk:
                await self.on_chunk()
            self.events.append("chunk")
            self.consumed += 1
            yield chunk

    @asynccontextmanager
    async def open(self, context, *, max_bytes):
        self.contexts.append(context)
        self.limits.append(max_bytes)
        self.events.append("open")
        chunks = self._chunks()
        try:
            if self.before_yield:
                await self.before_yield()
            expected = sum(len(chunk) for chunk in self.chunks if isinstance(chunk, (bytes, bytearray)))
            stream = ContentReadStream(context, chunks, expected, True)
            yield replace(stream, **self.stream_updates)
        finally:
            await chunks.aclose()
            self.closed = True
            self.events.append("close")
            if self.on_close:
                await self.on_close()


def synthetic_plain_text_read(payload=b"first\nsecond\nthird\n", *, provider_version=None):
    """Completed synthetic read matching SyntheticContentAuthority assertions."""
    context = ContentReadContext(
        ContentReadRequest(1, 2, 3, 4, "a" * 32),
        "disk:/private/file.txt",
        "file.txt",
        "root-key",
        "policy-key",
        "credential-key",
        1,
        "cloud",
        "processing-policy-key",
    )
    return ReadPlainText(context, extract_plain_text(payload, name=context.name), provider_version)
