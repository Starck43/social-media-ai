"""Explicit bounded original attachment / selected-line preview; no model or storage."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import uuid4

from app.integrations.file_workspace.content_read import ContentReadContext, ContentReadRequest, ReadPlainText
from app.integrations.file_workspace.line_evidence import LineRange, build_line_evidence
from app.integrations.file_workspace.plain_text import MAX_INPUT_BYTES, extract_plain_text
from app.integrations.file_workspace.yandex_content import (
    CONTENT_TIMEOUT,
    DownloadedText,
    FileDownloadUnavailable,
    YandexTextClient,
)


@dataclass(frozen=True, slots=True, repr=False)
class OriginalAttachment:
    payload: bytes
    # Static disposition avoids provider-controlled headers/names.
    filename: str = "original.txt"


class FileContentService:
    def __init__(self, manager, *, client=None):
        self.manager = manager
        self.client = client if client is not None else YandexTextClient()

    async def _fetch(self, args, processing):
        access = await self.manager.authorize(**args, processing=processing)
        # One outer budget also bounds a trusted injected client's cleanup.
        async with asyncio.timeout(CONTENT_TIMEOUT):
            downloaded = await self.client.download(path=access.context.path, name=access.name, token=access.token)
        if (
            type(downloaded) is not DownloadedText
            or type(downloaded.payload) is not bytes
            or len(downloaded.payload) > MAX_INPUT_BYTES
        ):
            raise ValueError
        fresh = await self.manager.authorize(**args, processing=processing, previous=access)
        if fresh != access:
            raise ValueError
        return access, downloaded.payload

    async def original(self, **args):
        """POST-only consumer must return isolated attachment, never a redirect URL."""
        try:
            _, payload = await self._fetch(args, False)
            return OriginalAttachment(payload)
        except Exception:
            pass
        raise FileDownloadUnavailable()

    async def preview(self, *, start=1, end=32, **args):
        """Fresh explicit cloud-processing consent; selected quotes, not AI conclusions."""
        try:
            if type(start) is not int or type(end) is not int or not 1 <= start <= end <= 1000 or end - start + 1 > 32:
                raise ValueError
            access, payload = await self._fetch(args, True)
            extraction = extract_plain_text(payload, name=access.name)
            if extraction.line_count == 0 or start > extraction.line_count:
                raise ValueError
            ctx = access.context
            source = ReadPlainText(
                ContentReadContext(
                    ContentReadRequest(ctx.tenant_id, ctx.actor_id, ctx.connection_id, ctx.object_id, uuid4().hex),
                    ctx.path,
                    access.name,
                    ctx.root_key,
                    ctx.policy_key,
                    ctx.credential_key,
                    ctx.observation_number,
                    "cloud",
                    access.processing_revision,
                ),
                extraction,
                None,
            )
            return build_line_evidence(source, ranges=(LineRange(start, min(end, extraction.line_count)),))
        except Exception:
            pass
        raise FileDownloadUnavailable()
